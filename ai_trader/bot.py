"""One trading cycle: fetch data -> enforce stop-losses -> ask Claude -> apply risk rules -> execute -> log."""

from datetime import datetime, timezone

import ccxt

from ai_trader import risk
from ai_trader.broker import LiveBroker, PaperBroker
from ai_trader.brain import ClaudeBrain
from ai_trader.config import Settings
from ai_trader.journal import Journal
from ai_trader.market import MarketData, make_exchange


class TradingBot:
    def __init__(self, settings: Settings, market, broker, brain, journal: Journal):
        self.settings = settings
        self.market = market
        self.broker = broker
        self.brain = brain
        self.journal = journal

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
        fills.append(fill.to_dict())
        self.journal.log_fill(fill.to_dict())

    def run_cycle(self) -> dict:
        errors: list[str] = []
        snapshots = {}
        for symbol in self.settings.symbols:
            try:
                snapshots[symbol] = self.market.snapshot(symbol)
            except ccxt.BadSymbol:
                errors.append(f"{symbol} isn't traded on {self.settings.exchange}; check the coin list in settings")
            except ccxt.NetworkError:
                errors.append(f"couldn't reach {self.settings.exchange} for {symbol} (internet down or exchange unavailable)")
            except Exception as e:
                errors.append(f"market data for {symbol} failed: {e}")
        if not snapshots:
            record = {"time": _now(), "mode": self.settings.mode, "errors": errors, "decisions": [], "fills": []}
            self.journal.log_cycle(record)
            return record

        prices = {s: snap["price"] for s, snap in snapshots.items()}
        before = self.portfolio(prices)
        limits = self.settings.limits
        day_start = self.journal.day_start_equity(before["equity"])
        halted = before["equity"] < day_start * (1 - limits.max_daily_loss_pct / 100)

        fills: list[dict] = []
        for order in risk.stop_loss_orders(before, limits):
            if order.symbol in snapshots:
                self._execute(order, snapshots, fills, errors)

        portfolio = self.portfolio(prices) if fills else before
        decision = self.brain.decide(snapshots, portfolio, limits, self.journal.recent_history())
        if decision.get("error"):
            errors.append(f"AI: {decision['error']} (holding everything this cycle)")

        orders, rejected = risk.vet(decision["decisions"], portfolio, limits, halted)
        for order in orders:
            self._execute(order, snapshots, fills, errors)

        after = self.portfolio(prices)
        record = {
            "time": _now(),
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    return TradingBot(settings, MarketData(exchange), broker, ClaudeBrain(settings.model, settings.effort), Journal(data_dir))
