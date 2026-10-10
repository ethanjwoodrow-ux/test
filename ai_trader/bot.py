"""One trading cycle: fetch data -> stop-losses -> strategy signals (+ Claude review of new entries)
-> risk rules -> execute -> log. The old "claude" mode asks Claude for every decision instead."""

from datetime import datetime, timezone

import ccxt

from ai_trader import risk
from ai_trader.broker import LiveBroker, PaperBroker
from ai_trader.brain import ClaudeBrain, ClaudeReviewer
from ai_trader.config import Settings
from ai_trader.engine import ReviewStore, StrategyEngine
from ai_trader.journal import Journal
from ai_trader.market import MarketData, make_exchange
from ai_trader.strategies import DAILY_HISTORY, latest, signal_series
from ai_trader.usage import UsageLedger


class TradingBot:
    def __init__(self, settings: Settings, market, broker, brain, journal: Journal, clock=None, engine=None):
        self.settings = settings
        self.engine = engine  # StrategyEngine in strategy modes; None means Claude decides everything
        self.clock = clock or (lambda: datetime.now(timezone.utc))  # backtests pass a simulated clock
        self.market = market
        self.broker = broker
        self.brain = brain
        self.journal = journal

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def portfolio(self, prices: dict[str, float]) -> dict:
        cash = self.broker.cash()
        positions = {}
        for symbol, pos in self.broker.positions().items():
            price = prices.get(symbol) or pos.get("avg_entry") or 0
            entry = pos.get("avg_entry") or 0
            positions[symbol] = {**pos, "price": price, "value": pos["amount"] * price,
                                 "pnl_pct": (price / entry - 1) * 100 if entry else 0.0}
        equity = cash + sum(p["value"] for p in positions.values())
        return {"cash": cash, "equity": equity, "positions": positions, "prices": prices}

    def _execute(self, order: risk.Order, snapshots: dict, fills: list, errors: list):
        snap = snapshots[order.symbol]
        try:
            if order.side == "buy":
                fill = self.broker.buy(order.symbol, order.spend, snap["ask"], order.reason)
            else:
                fill = self.broker.sell(order.symbol, order.amount, snap["bid"], order.reason)
        except Exception as e:  # exchange rejections, min-size errors, network blips
            errors.append(f"{order.side} {order.symbol} failed: {e}")
            return
        fill.timestamp = self._now()
        fills.append(fill.to_dict())
        self.journal.log_fill(fill.to_dict())

    def _data_error(self, symbol: str, e: Exception) -> str:
        if isinstance(e, ccxt.BadSymbol):
            return f"{symbol} isn't traded on {self.settings.exchange}; check the coin list in settings"
        if isinstance(e, ccxt.NetworkError):
            return f"couldn't reach {self.settings.exchange} for {symbol} (internet down or exchange unavailable)"
        return f"market data for {symbol} failed: {e}"

    def run_cycle(self) -> dict:
        return self._strategy_cycle() if self.engine else self._claude_cycle()

    def _strategy_cycle(self) -> dict:
        errors: list[str] = []
        daily, quotes = {}, {}
        for symbol in self.settings.symbols:
            try:  # a coin with missing data is left untouched this cycle rather than read as "sell"
                candles = self.market.daily(symbol, DAILY_HISTORY)
                quotes[symbol] = self.market.ticker(symbol)
                daily[symbol] = candles
            except Exception as e:
                errors.append(self._data_error(symbol, e))
        if not quotes:
            record = {"time": self._now(), "mode": self.settings.mode, "errors": errors, "decisions": [], "fills": []}
            self.journal.log_cycle(record)
            return record

        prices = {s: q["price"] for s, q in quotes.items()}
        before = self.portfolio(prices)
        limits = self.settings.limits
        day_start = self.journal.day_start_equity(before["equity"], self.clock())
        halted = before["equity"] < day_start * (1 - limits.max_daily_loss_pct / 100)

        fills: list[dict] = []
        stopped = set()
        for order in risk.stop_loss_orders(before, limits):
            if order.symbol in quotes:
                self._execute(order, quotes, fills, errors)
                stopped.add(order.symbol)
        portfolio = self.portfolio(prices) if fills else before

        signals = latest(signal_series(self.engine.mode, daily))
        plan = self.engine.plan(self.clock(), signals, daily, portfolio, halted, stopped,
                                slots=len(self.settings.symbols))
        for order in plan["orders"]:
            self._execute(order, quotes, fills, errors)

        after = self.portfolio(prices)
        record = {
            "time": self._now(),
            "mode": self.settings.mode,
            "strategy": self.engine.mode,
            "prices": prices,
            "equity_before": round(before["equity"], 2),
            "equity_after": round(after["equity"], 2),
            "cash_after": round(after["cash"], 2),
            "day_start_equity": round(day_start, 2),
            "halted": halted,
            "market_view": plan["summary"],
            "decisions": plan["decisions"],
            "reviews": plan["reviews"],
            "targets": {s: round(w, 4) for s, w in plan["targets"].items()},
            "signals": {s: {k: {"weight": round(sig.weight, 3), "since": sig.since, "reason": sig.reason}
                            for k, sig in by.items()} for s, by in signals.items()},
            "rejected": plan["rejected"],
            "fills": fills,
            "errors": errors + plan["errors"],
            "usage": plan["usage"],
        }
        self.journal.log_cycle(record)
        return record

    def _claude_cycle(self) -> dict:
        errors: list[str] = []
        snapshots = {}
        for symbol in self.settings.symbols:
            try:
                snapshots[symbol] = self.market.snapshot(symbol)
            except Exception as e:
                errors.append(self._data_error(symbol, e))
        if not snapshots:
            record = {"time": self._now(), "mode": self.settings.mode, "errors": errors, "decisions": [], "fills": []}
            self.journal.log_cycle(record)
            return record

        prices = {s: snap["price"] for s, snap in snapshots.items()}
        before = self.portfolio(prices)
        limits = self.settings.limits
        day_start = self.journal.day_start_equity(before["equity"], self.clock())
        halted = before["equity"] < day_start * (1 - limits.max_daily_loss_pct / 100)

        fills: list[dict] = []
        for order in risk.stop_loss_orders(before, limits):
            if order.symbol in snapshots:
                self._execute(order, snapshots, fills, errors)

        portfolio = self.portfolio(prices) if fills else before
        decision = self.brain.decide(snapshots, portfolio, limits, self.journal.recent_history(self.clock()))
        if decision.get("error"):
            errors.append(f"AI: {decision['error']} (holding everything this cycle)")

        orders, rejected = risk.vet(decision["decisions"], portfolio, limits, halted)
        for order in orders:
            self._execute(order, snapshots, fills, errors)

        after = self.portfolio(prices)
        record = {
            "time": self._now(),
            "mode": self.settings.mode,
            "prices": prices,
            "equity_before": round(before["equity"], 2),
            "equity_after": round(after["equity"], 2),
            "cash_after": round(after["cash"], 2),
            "day_start_equity": round(day_start, 2),
            "halted": halted,
            "market_view": decision.get("market_view", ""),
            "decisions": decision["decisions"],
            "rejected": rejected,
            "fills": fills,
            "errors": errors,
            "usage": decision.get("usage"),
        }
        self.journal.log_cycle(record)
        return record




def build_bot(settings: Settings) -> TradingBot:
    data_dir = settings.data_dir / settings.mode
    if settings.is_live:
        if not settings.live_confirmed:
            raise SystemExit("Live mode needs LIVE_TRADING_CONFIRMED=I_UNDERSTAND_THE_RISKS in .env")
        if not settings.exchange_api_key:
            raise SystemExit("Live mode needs EXCHANGE_API_KEY and EXCHANGE_API_SECRET in .env")
        exchange = make_exchange(settings.exchange, settings.exchange_api_key,
                                 settings.exchange_api_secret, settings.exchange_password)
        broker = LiveBroker(exchange, settings.symbols, settings.quote_currency, data_dir / "entries.json")
    else:
        exchange = make_exchange(settings.exchange)
        broker = PaperBroker(data_dir / "paper_portfolio.json", settings.starting_cash,
                             settings.paper_fee_pct, settings.paper_slippage_pct)
    brain = ClaudeBrain(settings.model, settings.effort, ledger=usage_ledger(settings),
                        budget=settings.budget, source=settings.mode)
    return TradingBot(settings, MarketData(exchange), broker, brain, Journal(data_dir),
                      engine=build_engine(settings, data_dir / "reviews.json", source=settings.mode))


def build_engine(settings: Settings, review_path, source: str) -> StrategyEngine | None:
    if settings.strategy == "claude":
        return None
    reviewer = (ClaudeReviewer(settings.review_model, settings.review_effort, ledger=usage_ledger(settings),
                               budget=settings.budget, source=source) if settings.review_enabled else None)
    return StrategyEngine(settings.strategy, settings.limits, reviewer, ReviewStore(review_path),
                          follow_unreviewed=settings.follow_unreviewed)


def usage_ledger(settings: Settings) -> UsageLedger:
    return UsageLedger(settings.data_dir / "claude_usage.jsonl")
