"""Strategy mode: turn strategy signals into trades, with Claude reviewing each new entry once.

Token use is kept low on purpose:
- Exits, trims and stop-losses never wait for Claude.
- Claude sees a new entry once (per strategy, coin and signal). Its verdict is remembered, so the same
  signal is never re-reviewed on later cycles.
- All new entries in a cycle go to Claude in one small request (a few hundred tokens of facts).
"""

import json
from datetime import datetime
from pathlib import Path

from ai_trader import risk
from ai_trader.config import RiskLimits
from ai_trader.strategies import MODES, STRATEGIES, Signal, rsi_series, sma_series, vol_series


class ReviewStore:
    """Remembers Claude's verdict for each (coin, strategy, signal start date). Kept in memory if path is None."""

    MAX_ENTRIES = 300

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text())
            except ValueError:
                self.data = {}

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, value: dict):
        self.data[key] = value
        if len(self.data) > self.MAX_ENTRIES:
            for old in sorted(self.data, key=lambda k: self.data[k].get("time", ""))[: len(self.data) - self.MAX_ENTRIES]:
                del self.data[old]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))

    def recent(self, n: int = 5) -> list[dict]:
        items = sorted(self.data.items(), key=lambda kv: kv[1].get("time", ""), reverse=True)[:n]
        return [{"entry": k, **v} for k, v in items]


def market_facts(candles: list[list[float]]) -> dict:
    """A handful of numbers about a coin for Claude's review. Small on purpose: tokens cost money."""
    closes = [c[4] for c in candles]
    if len(closes) < 2:
        return {}
    last = closes[-1]

    def pct(a, b):
        return round((a / b - 1) * 100, 1) if a and b else None

    sma50, sma200 = sma_series(closes, 50)[-1], sma_series(closes, 200)[-1]
    rsi14, vol = rsi_series(closes, 14)[-1], vol_series(closes)[-1]
    return {
        "price": last,
        "ret_7d_pct": pct(last, closes[-8]) if len(closes) > 7 else None,
        "ret_30d_pct": pct(last, closes[-31]) if len(closes) > 30 else None,
        "vs_sma50_pct": pct(last, sma50),
        "vs_sma200_pct": pct(last, sma200),
        "rsi14": round(rsi14) if rsi14 is not None else None,
        "vol_ann_pct": round(vol * 100) if vol else None,
        "off_90d_high_pct": pct(last, max(closes[-90:])),
    }


class StrategyEngine:
    def __init__(self, mode: str, limits: RiskLimits, reviewer=None, store: ReviewStore | None = None,
                 follow_unreviewed: bool = True, band_pct: float = 5.0):
        if mode not in MODES:
            raise ValueError(f"unknown strategy mode {mode!r}")
        self.mode = mode
        self.keys = MODES[mode][1]
        self.limits = limits
        self.reviewer = reviewer
        self.store = store or ReviewStore(None)
        self.follow_unreviewed = follow_unreviewed
        self.band_pct = band_pct

    def plan(self, now: datetime, signals: dict[str, dict[str, Signal]], candles: dict[str, list],
             portfolio: dict, halted: bool, stopped: set[str] = frozenset(), slots: int | None = None) -> dict:
        """Decide this cycle's orders. `signals` holds each strategy's latest signal per coin."""
        stamp = now.isoformat(timespec="seconds")
        errors: list[str] = []
        multipliers: dict[str, float] = {}
        pending: list[dict] = []
        reviews_shown: list[dict] = []

        for symbol, by_strategy in signals.items():
            for key in self.keys:
                sig = by_strategy.get(key)
                if not sig or sig.weight <= 0 or not sig.since:
                    continue
                part = f"{symbol}|{key}|{sig.since}"
                if symbol in stopped:
                    self.store.put(part, {"approve": False, "size": 0.0, "time": stamp,
                                          "reason": "stop-loss hit; waiting for a fresh signal", "by": "rules"})
                stored = self.store.get(part)
                if stored:
                    multipliers[part] = stored["size"]
                else:
                    pending.append({"id": part, "symbol": symbol, "strategy": STRATEGIES[key].name,
                                    "rule": STRATEGIES[key].rules, "signal": sig.reason,
                                    "facts": market_facts(candles.get(symbol, []))})

        usage = None
        if pending:
            result = self.reviewer.review(pending, self._context(portfolio)) if self.reviewer else {"reviews": {}, "error": "Claude review is off"}
            usage = result.get("usage")
            if result.get("error") and self.reviewer:
                errors.append(f"Claude review unavailable ({result['error']}); "
                              + ("following the strategy without review" if self.follow_unreviewed else "skipping new entries"))
            for entry in pending:
                verdict = result["reviews"].get(entry["id"])
                if verdict:
                    self.store.put(entry["id"], {**verdict, "time": stamp, "by": "claude"})
                    multipliers[entry["id"]] = verdict["size"]
                    reviews_shown.append({"symbol": entry["symbol"], "strategy": entry["strategy"], **verdict})
                else:  # not stored, so it's reviewed when Claude is reachable again
                    multipliers[entry["id"]] = 1.0 if (self.follow_unreviewed or not self.reviewer) else 0.0

        n = max(1, slots or len(signals))  # each coin gets an equal slot of the account
        targets, reasons = {}, {}
        for symbol, by_strategy in signals.items():
            weight, parts = 0.0, []
            for key in self.keys:
                sig = by_strategy.get(key)
                if not sig:
                    continue
                mult = multipliers.get(f"{symbol}|{key}|{sig.since}", 1.0) if sig.weight > 0 else 1.0
                weight += sig.weight * mult
                note = sig.reason
                if sig.weight > 0 and mult < 1:
                    note += " [Claude vetoed]" if mult == 0 else f" [Claude sized to {mult:.0%}]"
                parts.append(f"{STRATEGIES[key].name}: {note}")
            targets[symbol] = weight / len(self.keys) / n
            reasons[symbol] = parts

        orders, notes = risk.rebalance(targets, portfolio, self.limits, halted, self.band_pct)
        sides = {o.symbol: o.side for o in orders}
        decisions = [{"symbol": s, "action": sides.get(s, "hold"), "size_pct": round(targets[s] * 100, 1),
                      "confidence": None, "reasoning": " · ".join(reasons[s])} for s in signals]
        held = {s: round(targets[s] * 100) for s in signals if targets[s] > 0}
        summary = (f"{MODES[self.mode][0]}: "
                   + (", ".join(f"{s.split('/')[0]} {pct}%" for s, pct in held.items()) + " target" if held else "all cash")
                   + (f". Claude reviewed {len(reviews_shown)} new entr{'y' if len(reviews_shown) == 1 else 'ies'}." if reviews_shown else "."))
        return {"orders": orders, "rejected": notes, "decisions": decisions, "reviews": reviews_shown,
                "targets": targets, "summary": summary, "errors": errors, "usage": usage}

    def _context(self, portfolio: dict) -> dict:
        equity = portfolio["equity"] or 1
        return {
            "mode": MODES[self.mode][0],
            "cash_pct": round(portfolio["cash"] / equity * 100),
            "holdings_pct": {s: round(p["value"] / equity * 100) for s, p in portfolio["positions"].items()},
            "recent_reviews": [{"entry": r["entry"], "approve": r.get("approve"), "reason": r.get("reason", "")[:80]}
                               for r in self.store.recent(4)],
        }
