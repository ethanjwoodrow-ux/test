"""Backtesting: replay past prices hour by hour and let Claude trade them with fake money.

At each step Claude sees only candles that had closed by then, never later ones. One caveat no code
can fix: Claude may remember roughly what prices did from its training data, so a good backtest is
"not ruled out", not proof. Paper trading forward is the real test.
"""

import bisect
import json
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_trader import indicators
from ai_trader.bot import TradingBot
from ai_trader.brain import ClaudeBrain
from ai_trader.broker import PaperBroker
from ai_trader.config import Settings
from ai_trader.journal import Journal
from ai_trader.market import make_exchange
from ai_trader.usage import UsageLedger

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
WARMUP_HOURS = 200
WARMUP_DAYS = 90


@dataclass
class BacktestConfig:
    exchange: str
    symbols: list[str]
    start: str            # YYYY-MM-DD (UTC)
    end: str              # YYYY-MM-DD (UTC), exclusive
    step_hours: int       # Claude decides every N hours
    model: str
    effort: str
    starting_cash: float
    max_spend_usd: float  # stop the backtest before Claude spend passes this

    def steps(self) -> int:
        start, end = _day_ms(self.start), _day_ms(self.end)
        return max(0, (end - start) // (self.step_hours * HOUR_MS))


def _day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def fetch_candles(exchange, symbol: str, timeframe: str, since: int, until: int) -> list[list[float]]:
    """Page through an exchange's candle history between two timestamps (ms)."""
    step = exchange.parse_timeframe(timeframe) * 1000
    out: list[list[float]] = []
    while since < until:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=720)
        if not batch:
            break
        for c in batch:
            if c[0] < until and (not out or c[0] > out[-1][0]):
                out.append(c)
        nxt = batch[-1][0] + step
        if nxt <= since:
            break
        since = nxt
    return out


class HistoricalMarket:
    """Stands in for MarketData during a backtest. `now_ms` is the simulated time."""

    def __init__(self, hourly: dict[str, list], daily: dict[str, list]):
        self.hourly, self.daily = hourly, daily
        self._hourly_ts = {s: [c[0] for c in cs] for s, cs in hourly.items()}
        self._daily_ts = {s: [c[0] for c in cs] for s, cs in daily.items()}
        self.now_ms = 0

    def _closed(self, candles, stamps, length_ms, limit):
        # A candle is only known once it has closed: open time + length <= now.
        i = bisect.bisect_right(stamps, self.now_ms - length_ms)
        return candles[max(0, i - limit):i]

    def ticker(self, symbol: str) -> dict:
        hourly = self._closed(self.hourly[symbol], self._hourly_ts[symbol], HOUR_MS, 25)
        if not hourly:
            raise RuntimeError(f"no price data for {symbol} at {_iso(self.now_ms)}")
        price = hourly[-1][4]
        day_ago = hourly[0][4] if len(hourly) > 24 else None
        return {"price": price, "bid": price, "ask": price,
                "change_24h_pct": round((price / day_ago - 1) * 100, 2) if day_ago else None,
                "volume_24h_quote": round(sum(c[5] for c in hourly[-24:]) * price, 2)}

    def snapshot(self, symbol: str) -> dict:
        hourly = self._closed(self.hourly[symbol], self._hourly_ts[symbol], HOUR_MS, WARMUP_HOURS)
        daily = self._closed(self.daily[symbol], self._daily_ts[symbol], DAY_MS, WARMUP_DAYS)
        return {
            "symbol": symbol,
            **self.ticker(symbol),
            "hourly": indicators.summarize(hourly),
            "daily": indicators.summarize(daily) if daily else None,
            "last_24_hourly_closes": [c[4] for c in hourly[-24:]],
        }


def max_drawdown_pct(equities: list[float]) -> float:
    peak, worst = 0.0, 0.0
    for e in equities:
        peak = max(peak, e)
        if peak:
            worst = min(worst, (e / peak - 1) * 100)
    return worst


class BacktestRun:
    """Runs one backtest in a background thread. Poll `status()` for progress."""

    def __init__(self, config: BacktestConfig, settings: Settings, ledger: UsageLedger, out_dir: Path,
                 exchange=None, brain=None):
        self.config = config
        self.settings = settings
        self.ledger = ledger
        self.dir = out_dir
        self.id = out_dir.name
        self.source = f"backtest:{self.id}"
        self._exchange = exchange
        self._brain = brain
        self._stop = threading.Event()
        self.state = "starting"
        self.message = "Starting…"
        self.step = 0
        self.total = config.steps()
        self.summary: dict | None = None

    def start(self):
        threading.Thread(target=self._run_safely, daemon=True).start()

    def stop(self):
        self._stop.set()

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "message": self.message, "step": self.step,
                "total": self.total, "spent": round(self.ledger.spent_by(self.source), 4),
                "max_spend": self.config.max_spend_usd}

    def _run_safely(self):
        try:
            self._run()
        except Exception as e:
            self.state, self.message = "error", f"Backtest failed: {e}"
            self._write_summary()

    def _load(self) -> HistoricalMarket:
        cfg = self.config
        exchange = self._exchange or make_exchange(cfg.exchange)
        start, end = _day_ms(cfg.start), _day_ms(cfg.end)
        hourly, daily = {}, {}
        for i, symbol in enumerate(cfg.symbols, 1):
            self.message = f"Downloading price history for {symbol} ({i}/{len(cfg.symbols)})…"
            hourly[symbol] = fetch_candles(exchange, symbol, "1h", start - WARMUP_HOURS * HOUR_MS, end)
            daily[symbol] = fetch_candles(exchange, symbol, "1d", start - WARMUP_DAYS * DAY_MS, end)
            if not hourly[symbol]:
                raise RuntimeError(f"{cfg.exchange} returned no hourly history for {symbol}")
        # Some exchanges (Kraken) only keep ~30 days of hourly candles. Start where every coin has data.
        earliest = max(cs[0][0] for cs in hourly.values()) + 24 * HOUR_MS
        if earliest > start:
            self.note = (f"{cfg.exchange} only has hourly history back to {_iso(earliest)[:10]}, so the backtest "
                         f"starts there. For longer backtests pick Binance (coins like BTC/USDT).")
        else:
            self.note = None
        self.start_ms = max(start, earliest)
        self.end_ms = min(end, max(cs[-1][0] for cs in hourly.values()) + HOUR_MS)
        self.total = max(0, (self.end_ms - self.start_ms) // (cfg.step_hours * HOUR_MS))
        return HistoricalMarket(hourly, daily)

    def _run(self):
        cfg = self.config
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2))
        self.state = "loading"
        self._write_summary()
        market = self._load()
        if self.total == 0:
            raise RuntimeError("no price data in that date range")

        settings = replace(self.settings, symbols=cfg.symbols, exchange=cfg.exchange, mode="backtest")
        broker = PaperBroker(self.dir / "paper_portfolio.json", cfg.starting_cash,
                             self.settings.paper_fee_pct, self.settings.paper_slippage_pct)
        brain = self._brain or ClaudeBrain(cfg.model, cfg.effort, ledger=self.ledger,
                                           budget=self.settings.budget, source=self.source)
        clock = lambda: datetime.fromtimestamp(market.now_ms / 1000, timezone.utc)  # noqa: E731
        bot = TradingBot(settings, market, broker, brain, Journal(self.dir), clock=clock)

        self.state = "running"
        for self.step in range(self.total):
            if self._stop.is_set():
                self.state, self.message = "stopped", "Stopped by you."
                break
            spent = self.ledger.spent_by(self.source)
            if spent + self.ledger.typical_call(cfg.model, cfg.effort) > cfg.max_spend_usd:
                self.state = "stopped"
                self.message = f"Stopped at the ${cfg.max_spend_usd:.2f} spending limit for this backtest."
                break
            market.now_ms = self.start_ms + self.step * cfg.step_hours * HOUR_MS
            self.message = f"Simulating {_iso(market.now_ms)[:16].replace('T', ' ')} UTC"
            record = bot.run_cycle()
            if self.step == 0:
                self._write_summary()  # lists the run (state "running") as soon as it has data
            budget_hit = next((e for e in record.get("errors", []) if "budget reached" in e), None)
            if budget_hit:
                self.state, self.message = "stopped", f"Stopped: {budget_hit.removeprefix('AI: ').split(' (')[0]}."
                break
        else:
            self.step = self.total
            self.state, self.message = "done", "Finished."
        self._write_summary(bot)

    def _write_summary(self, bot: TradingBot | None = None):
        cycles = bot.journal.cycles(100_000) if bot else []
        summary = {"id": self.id, "config": asdict(self.config), "state": self.state, "message": self.message,
                   "note": getattr(self, "note", None), "claude_cost": round(self.ledger.spent_by(self.source), 4),
                   "updated": _iso(int(time.time() * 1000)), **results(cycles, self.config.symbols[0])}
        self.summary = summary
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2))


def results(cycles: list[dict], bench: str) -> dict:
    """Return, drawdown, trades and fees from a backtest's cycle log, next to just holding `bench`."""
    cycles = [c for c in cycles if "equity_after" in c]
    if not cycles:
        return {"steps_done": 0}
    first, last = cycles[0], cycles[-1]
    base = first["equity_before"]
    p0, p1 = first["prices"].get(bench), last["prices"].get(bench)
    fills = [f for c in cycles for f in c.get("fills", [])]
    return {
        "steps_done": len(cycles),
        "from": first["time"], "to": last["time"],
        "start_equity": base, "end_equity": last["equity_after"],
        "return_pct": round((last["equity_after"] / base - 1) * 100, 2),
        "hold_symbol": bench,
        "hold_return_pct": round((p1 / p0 - 1) * 100, 2) if p0 and p1 else None,
        "max_drawdown_pct": round(max_drawdown_pct([base] + [c["equity_after"] for c in cycles]), 2),
        "trades": len(fills),
        "fees": round(sum(f["fee"] for f in fills), 2),
    }


def new_run_dir(root: Path) -> Path:
    return root / datetime.now().strftime("%Y%m%d-%H%M%S")


def list_runs(root: Path) -> list[dict]:
    out = []
    for path in sorted(root.glob("*/summary.json"), reverse=True):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            pass
    return out


def default_dates(days: int = 14) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    return (end - timedelta(days=days)).isoformat(), end.isoformat()
