"""Tracks what each Claude call costs and enforces the daily/monthly spending caps.

Costs are estimates from token counts and list prices. The exact bill is on platform.claude.com.
"""

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# US$ per million tokens (input, output), standard Claude API list prices.
PRICES_PER_MTOK = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_PRICE = PRICES_PER_MTOK["claude-opus-5-5"]

# Rough per-call cost before we have any history: ~8k tokens in, ~2.5k out at high effort.
TYPICAL_TOKENS = (8_000, 2_500)
EFFORT_OUTPUT_FACTOR = {"low": 0.4, "medium": 0.7, "high": 1.0, "xhigh": 1.5, "max": 2.0}


def call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def guess_call_cost(model: str, effort: str) -> float:
    tokens_in, tokens_out = TYPICAL_TOKENS
    return call_cost(model, tokens_in, int(tokens_out * EFFORT_OUTPUT_FACTOR.get(effort, 1.0)))


@dataclass
class Budget:
    daily_usd: float = 5.0     # 0 = no cap. Covers trading and backtests.
    monthly_usd: float = 40.0  # 0 = no cap. Covers trading and backtests.


class UsageLedger:
    """Append-only log of Claude calls (one JSON line each), shared by paper, live and backtests."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def record(self, model: str, input_tokens: int, output_tokens: int, source: str) -> float:
        cost = call_cost(model, input_tokens, output_tokens)
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": model,
                 "input_tokens": input_tokens, "output_tokens": output_tokens,
                 "cost": round(cost, 6), "source": source}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return cost

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def summary(self, now: datetime | None = None) -> dict:
        """Spend today / this month (your local time) / all time."""
        now = (now or datetime.now(timezone.utc)).astimezone()
        today, month = now.date(), (now.year, now.month)
        out = {"today": 0.0, "month": 0.0, "total": 0.0, "calls": 0, "by_source": {}}
        for e in self.entries():
            local = datetime.fromisoformat(e["time"]).astimezone()
            out["total"] += e["cost"]
            out["calls"] += 1
            kind = e["source"].split(":")[0]  # "backtest:<id>" counts as "backtest"
            out["by_source"][kind] = out["by_source"].get(kind, 0.0) + e["cost"]
            if (local.year, local.month) == month:
                out["month"] += e["cost"]
                if local.date() == today:
                    out["today"] += e["cost"]
        return out

    def spent_by(self, source: str) -> float:
        return sum(e["cost"] for e in self.entries() if e["source"] == source)

    def typical_call(self, model: str, effort: str) -> float:
        """Average of the last 10 calls on this model, or a rough guess if there are none yet."""
        recent = [e["cost"] for e in self.entries() if e["model"] == model][-10:]
        return sum(recent) / len(recent) if recent else guess_call_cost(model, effort)

    def budget_problem(self, budget: Budget, next_call: float) -> str | None:
        """Why the next call would break a cap, or None if it's fine."""
        s = self.summary()
        if budget.daily_usd > 0 and s["today"] + next_call > budget.daily_usd:
            return f"daily Claude budget reached (${s['today']:.2f} of ${budget.daily_usd:.2f} used today)"
        if budget.monthly_usd > 0 and s["month"] + next_call > budget.monthly_usd:
            return f"monthly Claude budget reached (${s['month']:.2f} of ${budget.monthly_usd:.2f} used this month)"
        return None
