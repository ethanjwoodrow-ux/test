"""Hard risk rules. The AI only proposes trades; nothing reaches the broker without passing here."""

from dataclasses import dataclass

from ai_trader.config import RiskLimits


@dataclass
class Order:
    symbol: str
    side: str            # "buy" or "sell"
    spend: float = 0.0   # quote currency to spend (buys)
    amount: float = 0.0  # coins to sell (sells)
    reason: str = ""


def stop_loss_orders(portfolio: dict, limits: RiskLimits) -> list[Order]:
    """Sell any whole position that has fallen stop_loss_pct below its average entry."""
    orders = []
    for symbol, pos in portfolio["positions"].items():
        entry, price = pos.get("avg_entry") or 0, pos["price"]
        if entry > 0 and price <= entry * (1 - limits.stop_loss_pct / 100):
            drop = (price / entry - 1) * 100
            orders.append(Order(symbol, "sell", amount=pos["amount"],
                                reason=f"stop-loss: {drop:.1f}% below entry {entry:.2f}"))
    return orders


def vet(decisions: list[dict], portfolio: dict, limits: RiskLimits, halted: bool) -> tuple[list[Order], list[dict]]:
    """Turn AI decisions into orders that respect every limit. Returns (orders, rejections)."""
    equity = portfolio["equity"]
    cash = portfolio["cash"]
    values = {s: p["value"] for s, p in portfolio["positions"].items()}
    orders, rejected = [], []

    def reject(d, why):
        rejected.append({"symbol": d.get("symbol"), "action": d.get("action"), "why": why})

    # Sells first so the cash they free up is available to buys in the same cycle.
    for d in sorted(decisions, key=lambda d: d.get("action") != "sell"):
        action, symbol = d.get("action"), d.get("symbol")
        if action == "hold":
            continue
        if symbol not in portfolio["prices"]:
            reject(d, "unknown symbol")
            continue
        if d.get("confidence", 0) < limits.min_confidence:
            reject(d, f"confidence {d.get('confidence', 0):.2f} below {limits.min_confidence}")
            continue
        price = portfolio["prices"][symbol]
        size_pct = max(0.0, min(100.0, float(d.get("size_pct", 0))))

        if action == "sell":
            held = portfolio["positions"].get(symbol, {}).get("amount", 0.0)
            if held <= 0:
                reject(d, "no position to sell")
                continue
            amount = held * size_pct / 100
            value = amount * price
            if value < limits.min_order_usd:
                reject(d, f"sell value {value:.2f} below minimum order")
                continue
            orders.append(Order(symbol, "sell", amount=amount, reason=d.get("reasoning", "")))
            cash += value
            values[symbol] = values.get(symbol, 0) - value

        elif action == "buy":
            if halted:
                reject(d, "daily loss limit hit; no new buys today")
                continue
            spend = equity * size_pct / 100
            caps = {
                "max trade size": equity * limits.max_trade_pct / 100,
                "max position size": equity * limits.max_position_pct / 100 - values.get(symbol, 0),
                "cash reserve": cash - equity * limits.min_cash_pct / 100,
            }
            limiting = [name for name, cap in caps.items() if cap < spend]
            spend = min(spend, *caps.values())
            if spend < limits.min_order_usd:
                reject(d, "too small after limits" + (f" ({', '.join(limiting)})" if limiting else ""))
                continue
            reason = d.get("reasoning", "")
            if limiting:
                reason += f" [trimmed by: {', '.join(limiting)}]"
            orders.append(Order(symbol, "buy", spend=spend, reason=reason))
            cash -= spend
            values[symbol] = values.get(symbol, 0) + spend
        else:
            reject(d, f"unknown action {action!r}")
    return orders, rejected
