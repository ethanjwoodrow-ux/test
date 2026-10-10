"""Order execution: a paper broker (fake money, real prices) and a live ccxt broker."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Fill:
    symbol: str
    side: str          # "buy" or "sell"
    amount: float      # coins
    price: float       # quote currency per coin, after slippage
    cost: float        # quote currency spent (buy) or received (sell), before fee
    fee: float
    reason: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class _StateFile:
    """JSON state on disk, or kept only in memory when path is None (fast backtests)."""

    def __init__(self, path: Path | None, default: dict):
        self.path = path
        if path and path.exists():
            self.data = json.loads(path.read_text())
        else:
            self.data = default
            self.save()

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)


class PaperBroker:
    """Simulates fills at the live bid/ask with fees and slippage. State persists between runs."""

    is_live = False

    def __init__(self, state_path: Path | None, starting_cash: float, fee_pct: float, slippage_pct: float):
        self.fee = fee_pct / 100
        self.slippage = slippage_pct / 100
        self.state = _StateFile(state_path, {"cash": starting_cash, "starting_cash": starting_cash,
                                             "positions": {}, "created": _now()})

    def cash(self) -> float:
        return self.state.data["cash"]

    def positions(self) -> dict[str, dict]:
        return {s: dict(p) for s, p in self.state.data["positions"].items() if p["amount"] > 0}

    def buy(self, symbol: str, spend: float, price: float, reason: str = "") -> Fill:
        fill_price = price * (1 + self.slippage)
        fee = spend * self.fee
        if spend + fee > self.cash() + 1e-9:
            spend = self.cash() / (1 + self.fee)
            fee = spend * self.fee
        amount = spend / fill_price
        pos = self.state.data["positions"].setdefault(symbol, {"amount": 0.0, "avg_entry": 0.0})
        new_amount = pos["amount"] + amount
        pos["avg_entry"] = (pos["amount"] * pos["avg_entry"] + amount * fill_price) / new_amount
        pos["amount"] = new_amount
        self.state.data["cash"] -= spend + fee
        self.state.save()
        return Fill(symbol, "buy", amount, fill_price, spend, fee, reason, _now())

    def sell(self, symbol: str, amount: float, price: float, reason: str = "") -> Fill:
        pos = self.state.data["positions"].get(symbol)
        if not pos or pos["amount"] <= 0:
            raise ValueError(f"No {symbol} position to sell")
        amount = min(amount, pos["amount"])
        fill_price = price * (1 - self.slippage)
        proceeds = amount * fill_price
        fee = proceeds * self.fee
        pos["amount"] -= amount
        if pos["amount"] <= 1e-12:
            del self.state.data["positions"][symbol]
        self.state.data["cash"] += proceeds - fee
        self.state.save()
        return Fill(symbol, "sell", amount, fill_price, proceeds, fee, reason, _now())


class LiveBroker:
    """Places real market orders on the exchange. Entry prices are tracked locally for stop-losses."""

    is_live = True

    def __init__(self, exchange, symbols: list[str], quote_currency: str, state_path: Path):
        self.exchange = exchange
        self.symbols = symbols
        self.quote = quote_currency
        self.entries = _StateFile(state_path, {"avg_entry": {}})
        self.exchange.load_markets()

    def cash(self) -> float:
        return float(self.exchange.fetch_balance()["free"].get(self.quote, 0) or 0)

    def positions(self) -> dict[str, dict]:
        totals = self.exchange.fetch_balance()["total"]
        out = {}
        for symbol in self.symbols:
            amount = float(totals.get(symbol.split("/")[0], 0) or 0)
            if amount > 0:
                out[symbol] = {"amount": amount, "avg_entry": self.entries.data["avg_entry"].get(symbol, 0.0)}
        return out

    def _fill(self, order: dict, symbol: str, side: str, fallback_price: float, reason: str) -> Fill:
        amount = float(order.get("filled") or order.get("amount") or 0)
        price = float(order.get("average") or order.get("price") or fallback_price)
        fee = float((order.get("fee") or {}).get("cost") or 0)
        return Fill(symbol, side, amount, price, amount * price, fee, reason, _now())

    def buy(self, symbol: str, spend: float, price: float, reason: str = "") -> Fill:
        amount = float(self.exchange.amount_to_precision(symbol, spend / price))
        held = self.positions().get(symbol, {"amount": 0.0, "avg_entry": 0.0})
        order = self.exchange.create_market_buy_order(symbol, amount)
        fill = self._fill(order, symbol, "buy", price, reason)
        total = held["amount"] + fill.amount
        if total > 0:
            self.entries.data["avg_entry"][symbol] = (held["amount"] * held["avg_entry"] + fill.amount * fill.price) / total
            self.entries.save()
        return fill

    def sell(self, symbol: str, amount: float, price: float, reason: str = "") -> Fill:
        amount = float(self.exchange.amount_to_precision(symbol, amount))
        order = self.exchange.create_market_sell_order(symbol, amount)
        return self._fill(order, symbol, "sell", price, reason)
