"""The AI part: Claude reads the market snapshot and portfolio, then proposes buy/sell/hold per coin."""

import json
from dataclasses import asdict

import anthropic

from ai_trader.config import RiskLimits

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


class ClaudeBrain:
    def __init__(self, model: str, effort: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def decide(self, snapshots: dict, portfolio: dict, limits: RiskLimits, history: list[dict]) -> dict:
        """Returns {"market_view", "decisions", "error"?}. Any failure means hold everything."""
        symbols = list(snapshots)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": decision_schema(symbols)}},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(snapshots, portfolio, limits, history)}],
            )
        except anthropic.AuthenticationError:
            return self._fail("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY in .env)")
        except anthropic.RateLimitError:
            return self._fail("rate limited by Anthropic API")
        except anthropic.APIStatusError as e:
            return self._fail(f"Anthropic API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            return self._fail("could not reach Anthropic API")

        if response.stop_reason in ("refusal", "max_tokens"):
            return self._fail(f"model stopped with {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return self._fail("model returned invalid JSON")
        usage = response.usage
        return {
            "market_view": str(raw.get("market_view", "")),
            "decisions": _clean(raw, symbols),
            "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        }

    @staticmethod
    def _fail(why: str) -> dict:
        return {"market_view": "", "decisions": [], "error": why}
