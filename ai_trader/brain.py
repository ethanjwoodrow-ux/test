"""The AI part. In strategy modes Claude only reviews new entries and writes a weekly strategy review;
in the old "Claude decides" mode it makes every buy/sell/hold call."""

import json
from dataclasses import asdict

import anthropic

from ai_trader.config import RiskLimits
from ai_trader.usage import Budget, UsageLedger

# Models that accept server-side refusal fallbacks (Haiku has none).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

SYSTEM_PROMPT = """You are the decision engine of an automated crypto spot-trading bot. Each run you get \
fresh market data for a fixed list of coins, the current portfolio, the risk limits, and a log of your \
own recent decisions and fills. You decide, per coin, whether to buy, sell, or hold.

How your output is used:
- size_pct for a buy is the percent of TOTAL portfolio equity to spend. For a sell it is the percent of \
the current position in that coin to sell (100 = close it).
- A separate risk layer enforces the limits you are shown. Oversized trades get trimmed and \
low-confidence calls are dropped, so be honest with confidence rather than inflating it.
- The account is spot only: no leverage, no shorting. Fees are roughly 0.25% each way, so churning \
small moves loses money. Holding is a perfectly good decision and usually the right one.
- Stop-losses are applied automatically before you run; you do not need to manage them.

Think like a disciplined swing trader: favour clear trend + momentum alignment across the hourly and \
daily timeframes, respect volatility, avoid chasing big candles, and take profit or cut positions when \
the thesis breaks. Use your recent decision log to stay consistent and avoid flip-flopping.

Return one decision for every coin listed. Keep each reasoning to one or two plain-English sentences."""


def decision_schema(symbols: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "market_view": {"type": "string", "description": "One-paragraph read of the overall market right now."},
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "enum": symbols},
                        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                        "size_pct": {"type": "number", "description": "0-100. See system prompt."},
                        "confidence": {"type": "number", "description": "0-1."},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["symbol", "action", "size_pct", "confidence", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["market_view", "decisions"],
        "additionalProperties": False,
    }


def build_prompt(snapshots: dict, portfolio: dict, limits: RiskLimits, history: list[dict]) -> str:
    sections = {
        "portfolio": {
            "cash": round(portfolio["cash"], 2),
            "equity": round(portfolio["equity"], 2),
            "positions": {
                s: {"amount": p["amount"], "avg_entry": round(p["avg_entry"], 4), "price": p["price"],
                    "value": round(p["value"], 2), "pnl_pct": round(p["pnl_pct"], 2),
                    "pct_of_equity": round(p["value"] / portfolio["equity"] * 100, 2)}
                for s, p in portfolio["positions"].items()
            },
        },
        "risk_limits": asdict(limits),
        "market": snapshots,
        "your_recent_history": history,
    }
    return (
        f"Coins to decide on: {', '.join(snapshots)}\n\n"
        + "\n\n".join(f"<{name}>\n{json.dumps(body, indent=1, default=str)}\n</{name}>" for name, body in sections.items())
    )


def _clean(raw: dict, symbols: list[str]) -> list[dict]:
    out = []
    for d in raw.get("decisions", []):
        if d.get("symbol") not in symbols or d.get("action") not in ("buy", "sell", "hold"):
            continue
        out.append({
            "symbol": d["symbol"],
            "action": d["action"],
            "size_pct": max(0.0, min(100.0, float(d.get("size_pct", 0)))),
            "confidence": max(0.0, min(1.0, float(d.get("confidence", 0)))),
            "reasoning": str(d.get("reasoning", "")),
        })
    return out


class _ClaudeCaller:
    """One structured-output Claude request, with the budget check before it and cost logging after it."""

    def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None,
                 ledger: UsageLedger | None = None, budget: Budget | None = None, source: str = "paper"):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()
        self.ledger = ledger
        self.budget = budget
        self.source = source

    def _ask(self, system: str, user: str, schema: dict, max_tokens: int = 16000) -> tuple[dict | None, str | None, dict | None]:
        """Returns (parsed JSON, error, usage). On any error the caller should fall back safely."""
        if self.ledger and self.budget:
            problem = self.ledger.budget_problem(self.budget, self.ledger.typical_call(self.model, self.effort))
            if problem:
                return None, problem, None
        fallback = ({"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
                    if self.model in FALLBACK_MODELS else {})
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                **fallback,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError:
            return None, "Anthropic API key missing or invalid (set ANTHROPIC_API_KEY in .env)", None
        except anthropic.RateLimitError:
            return None, "rate limited by Anthropic API", None
        except anthropic.APIStatusError as e:
            return None, f"Anthropic API error {e.status_code}: {e.message}", None
        except anthropic.APIConnectionError:
            return None, "could not reach Anthropic API", None

        usage = response.usage
        cost = None
        if self.ledger:  # billed even when the reply turns out unusable
            cost = self.ledger.record(getattr(response, "model", None) or self.model,
                                      usage.input_tokens, usage.output_tokens, self.source)
        info = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost": cost}
        if response.stop_reason in ("refusal", "max_tokens"):
            return None, f"model stopped with {response.stop_reason}", info
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text), None, info
        except json.JSONDecodeError:
            return None, "model returned invalid JSON", info


class ClaudeBrain(_ClaudeCaller):
    """Old mode: Claude makes every buy/sell/hold call itself. Costs far more than the strategy modes."""

    def decide(self, snapshots: dict, portfolio: dict, limits: RiskLimits, history: list[dict]) -> dict:
        """Returns {"market_view", "decisions", "error"?}. Any failure means hold everything."""
        symbols = list(snapshots)
        raw, error, usage = self._ask(SYSTEM_PROMPT, build_prompt(snapshots, portfolio, limits, history),
                                      decision_schema(symbols))
        if error:
            return {"market_view": "", "decisions": [], "error": error, "usage": usage}
        return {"market_view": str(raw.get("market_view", "")), "decisions": _clean(raw, symbols), "usage": usage}


# ---------- strategy mode: Claude only reviews ----------

REVIEW_SYSTEM = """You review trade entries proposed by a rule-based crypto strategy (spot only, no leverage). \
The rules are tested and usually right, so approve by default. Veto or shrink only for a concrete reason \
the rules can't see, such as a parabolic run far above the averages, extreme volatility, or a signal that \
contradicts the bigger trend. size: 1 = full position, 0.5 = half, 0 = skip. One short sentence per reason."""


def review_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reviews": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "approve": {"type": "boolean"},
                    "size": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "approve", "size", "reason"],
                "additionalProperties": False,
            }},
        },
        "required": ["reviews"],
        "additionalProperties": False,
    }


class ClaudeReviewer(_ClaudeCaller):
    """Reviews new entries from the strategies. One small request per batch of new signals."""

    def review(self, entries: list[dict], context: dict) -> dict:
        """entries: [{"id", "symbol", "strategy", "rule", "reason", "facts"}]. Returns {"reviews": {id: {...}}, "error"?, "usage"}."""
        payload = {"proposed_entries": entries, "account": context}
        raw, error, usage = self._ask(REVIEW_SYSTEM, json.dumps(payload, separators=(",", ":"), default=str),
                                      review_schema(), max_tokens=4000)
        if error:
            return {"reviews": {}, "error": error, "usage": usage}
        ids = {e["id"] for e in entries}
        out = {}
        for r in raw.get("reviews", []):
            if r.get("id") in ids:
                size = max(0.0, min(1.0, float(r.get("size", 1))))
                approve = bool(r.get("approve")) and size > 0
                out[r["id"]] = {"approve": approve, "size": size if approve else 0.0, "reason": str(r.get("reason", ""))}
        return {"reviews": out, "usage": usage}


COACH_SYSTEM = """You are reviewing three rule-based crypto strategies that a small spot account can switch \
between (or run all together). You get each strategy's recent results on the same coins, plain buy-and-hold \
for comparison, and current market conditions. Say plainly how each is doing and why (trend vs choppy market, \
fees from churn, etc.), and recommend which mode to run now. Be honest when results are too short or close to \
call, and don't recommend switching on noise. Keep it under 150 words, plain English, no hype."""


def coach_schema(modes: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "recommended_mode": {"type": "string", "enum": modes},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["summary", "recommended_mode", "confidence"],
        "additionalProperties": False,
    }


class StrategyCoach(_ClaudeCaller):
    """Weekly (or on-demand) review of how the strategies are doing. One request."""

    def assess(self, stats: dict, modes: list[str]) -> dict:
        raw, error, usage = self._ask(COACH_SYSTEM, json.dumps(stats, separators=(",", ":"), default=str),
                                      coach_schema(modes), max_tokens=4000)
        if error:
            return {"error": error, "usage": usage}
        return {**raw, "usage": usage}
