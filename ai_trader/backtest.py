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

from ai_trader import indicators, risk
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
    strategy: str = "claude"  # "claude" (Claude decides, hourly steps), a strategy mode, or "compare"
    review: bool = False      # strategy modes: Claude reviews new entries

    def steps(self) -> int:
        start, end = _day_ms(self.start), _day_ms(self.end)
        if self.strategy != "claude":
            return max(0, (end - start) // DAY_MS)
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
                "max_spend": self.config.max_spend_usd,
                "uses_claude": self.config.strategy == "claude" or self.config.review}

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
        if self.config.strategy != "claude":
            return self._run_strategy()
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

    def _run_strategy(self):
        from ai_trader.brain import ClaudeReviewer
        from ai_trader.strategies import DAILY_HISTORY, MODES

        cfg = self.config
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2))
        self.state = "loading"
        self._write_summary()
        exchange = self._exchange or make_exchange(cfg.exchange)
        start, end = _day_ms(cfg.start), _day_ms(cfg.end)
        daily = {}
        for i, symbol in enumerate(cfg.symbols, 1):
            self.message = f"Downloading daily price history for {symbol} ({i}/{len(cfg.symbols)})…"
            daily[symbol] = fetch_candles(exchange, symbol, "1d", start - DAILY_HISTORY * DAY_MS, end)
            if not daily[symbol]:
                raise RuntimeError(f"{cfg.exchange} returned no daily history for {symbol}")
        first = max(cs[0][0] for cs in daily.values())
        self.note = None
        if first > start - 60 * DAY_MS:
            self.note = (f"{cfg.exchange} only has daily history back to {_iso(first)[:10]}, so the first weeks are "
                         "warm-up and the longest breakout channels have less history. Binance keeps more history.")
        self.start_ms = max(start, first + 60 * DAY_MS)  # strategies need ~2 months of history to say anything

        modes = ["trend", "breakout", "dip", "all"] if cfg.strategy == "compare" else [cfg.strategy]
        reviewer = None
        if cfg.review and cfg.strategy != "compare":
            reviewer = _LimitedReviewer(ClaudeReviewer(cfg.model, cfg.effort, ledger=self.ledger,
                                                       budget=self.settings.budget, source=self.source),
                                        self.ledger, self.source, cfg.max_spend_usd)
        days = len({c[0] for cs in daily.values() for c in cs if self.start_ms <= c[0] < end})
        self.total = days * len(modes)
        self.state = "running"
        results = {}
        for n, mode in enumerate(modes):
            self.message = f"Simulating {MODES[mode][0]}…"
            results[mode] = simulate(mode, daily, self.start_ms, end, cfg.starting_cash, self.settings.paper_fee_pct,
                                     self.settings.paper_slippage_pct, self.settings.limits, reviewer=reviewer,
                                     stop_event=self._stop,
                                     progress=lambda done, _total, n=n: setattr(self, "step", n * days + done))
        stopped = self._stop.is_set()
        self.state, self.message = ("stopped", "Stopped by you.") if stopped else ("done", "Finished.")
        if reviewer and reviewer.limit_hit:
            self.message += f" Claude review hit your ${cfg.max_spend_usd:.2f} limit partway through; after that it ran on the rules alone."

        bench = cfg.symbols[0]
        primary = "all" if cfg.strategy == "compare" else cfg.strategy
        main = results[primary]
        hold = _hold_curve(daily[bench], [p["time"] for p in main["points"]], cfg.starting_cash)
        curves = {m: r["points"] for m, r in results.items()} | {"hold": hold}
        (self.dir / "curves.json").write_text(json.dumps(curves))
        with (self.dir / "cycles.jsonl").open("w") as f:
            for c in main["cycles"]:
                f.write(json.dumps(c, default=str) + "\n")
        journal = Journal(self.dir)
        for fill in main["fills"]:
            journal.log_fill(fill)
        stats = {m: curve_stats(r["points"], r["fills"], cfg.starting_cash) for m, r in results.items()}
        self._write_summary(strategy_stats=stats, primary=primary, points=main["points"], hold=hold)

    def _write_summary(self, bot: TradingBot | None = None, strategy_stats: dict | None = None,
                       primary: str | None = None, points: list | None = None, hold: list | None = None):
        if strategy_stats is not None:
            cfg, main = self.config, strategy_stats[primary]
            summary = {"id": self.id, "config": asdict(cfg), "state": self.state, "message": self.message,
                       "note": getattr(self, "note", None), "claude_cost": round(self.ledger.spent_by(self.source), 4),
                       "updated": _iso(int(time.time() * 1000)), "steps_done": len(points), "strategies": strategy_stats,
                       "primary": primary, "start_equity": cfg.starting_cash, "end_equity": main["end_equity"],
                       "return_pct": main["return_pct"], "max_drawdown_pct": main["max_drawdown_pct"],
                       "trades": main["trades"], "fees": main["fees"], "hold_symbol": cfg.symbols[0],
                       "hold_return_pct": round((hold[-1]["equity"] / cfg.starting_cash - 1) * 100, 2) if hold else None,
                       "from": points[0]["time"] if points else None, "to": points[-1]["time"] if points else None}
            self.summary = summary
            (self.dir / "summary.json").write_text(json.dumps(summary, indent=2))
            return
        self._write_summary_claude(bot)

    def _write_summary_claude(self, bot: TradingBot | None = None):
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


# ---------- strategy backtests: fast, day by day, free unless Claude review is on ----------

def simulate(mode: str, daily: dict[str, list], start_ms: int, end_ms: int, starting_cash: float,
             fee_pct: float, slippage_pct: float, limits, reviewer=None, slots: int | None = None,
             stop_event: threading.Event | None = None, progress=None) -> dict:
    """Trade one strategy mode over daily candles, deciding at each daily close.

    Signals are computed once over the whole history; each signal only uses candles up to its own day,
    so nothing from the future leaks in. Fills happen at that day's close with fees and slippage."""
    from ai_trader.engine import ReviewStore, StrategyEngine
    from ai_trader.strategies import latest, signal_series

    series = signal_series(mode, daily)
    index = {sym: {c[0]: i for i, c in enumerate(cs)} for sym, cs in daily.items()}
    days = sorted({c[0] for cs in daily.values() for c in cs if start_ms <= c[0] < end_ms})
    broker = PaperBroker(None, starting_cash, fee_pct, slippage_pct)
    engine = StrategyEngine(mode, limits, reviewer, ReviewStore(None), follow_unreviewed=True)
    points, fills, cycles = [], [], []
    last_prices: dict[str, float] = {}
    prev_equity = starting_cash
    for step, ts in enumerate(days):
        if stop_event is not None and stop_event.is_set():
            break
        now = datetime.fromtimestamp((ts + DAY_MS) / 1000, timezone.utc)  # decision at the daily close
        idx = {sym: index[sym][ts] for sym in daily if ts in index[sym]}
        for sym, i in idx.items():
            last_prices[sym] = daily[sym][i][4]
        portfolio = _portfolio(broker, last_prices)
        halted = portfolio["equity"] < prev_equity * (1 - limits.max_daily_loss_pct / 100)

        day_fills, stopped = [], set()
        for order in risk.stop_loss_orders(portfolio, limits):
            if order.symbol in idx:
                day_fills.append(broker.sell(order.symbol, order.amount, last_prices[order.symbol], order.reason))
                stopped.add(order.symbol)
        if day_fills:
            portfolio = _portfolio(broker, last_prices)

        signals = {sym: {k: s[i] for k, s in series[sym].items()} for sym, i in idx.items()}
        candles = {sym: daily[sym][max(0, i - 220):i + 1] for sym, i in idx.items()} if reviewer else {}
        plan = engine.plan(now, signals, candles, portfolio, halted, stopped, slots=slots or len(daily))
        for order in plan["orders"]:
            price = last_prices[order.symbol]
            try:
                fill = (broker.buy(order.symbol, order.spend, price, order.reason) if order.side == "buy"
                        else broker.sell(order.symbol, order.amount, price, order.reason))
            except ValueError:
                continue
            day_fills.append(fill)
        stamp = now.isoformat(timespec="seconds")
        for f in day_fills:
            f.timestamp = stamp
            fills.append(f.to_dict())
        equity = _portfolio(broker, last_prices)["equity"]
        points.append({"time": stamp, "equity": round(equity, 2)})
        if reviewer or day_fills:
            cycles.append({"time": stamp, "market_view": plan["summary"], "decisions": plan["decisions"],
                           "reviews": plan["reviews"], "rejected": plan["rejected"],
                           "fills": [f.to_dict() for f in day_fills], "errors": plan["errors"]})
        prev_equity = equity
        if progress:
            progress(step + 1, len(days))
    return {"mode": mode, "points": points, "fills": fills, "cycles": cycles}


def _portfolio(broker: PaperBroker, prices: dict[str, float]) -> dict:
    positions = {}
    for sym, pos in broker.positions().items():
        price = prices.get(sym, pos["avg_entry"])
        positions[sym] = {**pos, "price": price, "value": pos["amount"] * price,
                          "pnl_pct": (price / pos["avg_entry"] - 1) * 100 if pos["avg_entry"] else 0.0}
    equity = broker.cash() + sum(p["value"] for p in positions.values())
    return {"cash": broker.cash(), "equity": equity, "positions": positions, "prices": dict(prices)}


def curve_stats(points: list[dict], fills: list[dict], starting_cash: float) -> dict:
    equities = [starting_cash] + [p["equity"] for p in points]
    return {
        "return_pct": round((equities[-1] / starting_cash - 1) * 100, 2),
        "max_drawdown_pct": round(max_drawdown_pct(equities), 2),
        "trades": len(fills),
        "fees": round(sum(f["fee"] for f in fills), 2),
        "end_equity": round(equities[-1], 2),
    }


def _hold_curve(candles: list, times: list[str], starting_cash: float) -> list[dict]:
    """What putting everything into one coin on day one would be worth on each day."""
    by_day = {_iso(c[0] + DAY_MS): c[4] for c in candles}
    first = next((by_day[t] for t in times if t in by_day), None)
    return [{"time": t, "equity": round(starting_cash * by_day[t] / first, 2)} for t in times if first and t in by_day]


class _LimitedReviewer:
    """Wraps the reviewer so a backtest stops calling Claude once its own spending limit is reached."""

    def __init__(self, reviewer, ledger: UsageLedger, source: str, max_spend: float):
        self.reviewer, self.ledger, self.source, self.max_spend = reviewer, ledger, source, max_spend
        self.limit_hit = False

    def review(self, entries, context):
        next_call = self.ledger.typical_call(self.reviewer.model, self.reviewer.effort)
        if self.ledger.spent_by(self.source) + next_call > self.max_spend:
            self.limit_hit = True
            return {"reviews": {}, "error": "backtest spending limit reached"}
        return self.reviewer.review(entries, context)
