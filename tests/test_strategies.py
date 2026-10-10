"""Strategy signals, the strategy engine (with Claude review memory), rebalancing and the fast simulator."""

import math
import random
from datetime import datetime, timezone

import pytest

from ai_trader import risk
from ai_trader.backtest import DAY_MS, simulate
from ai_trader.config import RiskLimits
from ai_trader.engine import StrategyEngine
from ai_trader.strategies import (STRATEGIES, BreakoutEnsemble, DipBuyer, Signal, TrendFollowing, rolling_extreme,
                                  rsi_series)
from ai_trader import indicators

T0 = 1_700_000_000_000 - 1_700_000_000_000 % DAY_MS


def make(closes):
    return [[T0 + i * DAY_MS, c, c * 1.01, c * 0.99, c, 100.0] for i, c in enumerate(closes)]


def wave(n, drift=0.002, amp=0.08, seed=1):
    rnd = random.Random(seed)
    out, p = [], 100.0
    for i in range(n):
        p *= math.exp(drift + 0.02 * rnd.gauss(0, 1))
        out.append(p * (1 + amp * math.sin(i / 15)))
    return out


def test_rolling_extreme_matches_brute_force():
    vals = [random.Random(3).random() for _ in range(200)]
    for n in (1, 5, 37):
        hi, lo = rolling_extreme(vals, n, True), rolling_extreme(vals, n, False)
        for i in range(len(vals)):
            exp_hi = max(vals[i - n + 1:i + 1]) if i >= n - 1 else None
            exp_lo = min(vals[i - n + 1:i + 1]) if i >= n - 1 else None
            assert hi[i] == exp_hi and lo[i] == exp_lo


def test_rsi_series_matches_indicator():
    closes = wave(120)
    assert rsi_series(closes, 14)[-1] == pytest.approx(indicators.rsi(closes, 14))


@pytest.mark.parametrize("strategy", list(STRATEGIES.values()), ids=list(STRATEGIES))
def test_no_lookahead(strategy):
    """The signal for day k must be the same whether or not later days exist."""
    candles = make(wave(500, seed=7))
    full = strategy.series(candles)
    for k in (60, 150, 210, 260, 380, 499):
        part = strategy.series(candles[:k + 1])[k]
        assert (part.weight, part.since) == (full[k].weight, full[k].since), k


def test_trend_in_uptrend_out_after_crash():
    closes = [100 * 1.01 ** i for i in range(120)]
    s = TrendFollowing().series(make(closes))
    assert s[-1].weight > 0 and s[-1].since
    crashed = closes + [closes[-1] * 0.97 ** i for i in range(1, 40)]
    assert TrendFollowing().series(make(crashed))[-1].weight == 0


def test_breakout_enters_on_new_high_and_exits_below_midline():
    flat = [100 + (i % 5) for i in range(100)]
    up = flat + [110, 115, 120]
    s = BreakoutEnsemble().series(make(up))
    assert s[-1].weight > 0 and "channels long" in s[-1].reason
    down = up + [100, 95, 90, 85]
    assert BreakoutEnsemble().series(make(down))[-1].weight == 0


def test_dip_buyer_buys_dip_in_uptrend_then_exits_on_bounce():
    closes = [100 * 1.004 ** i for i in range(230)]
    dip = closes + [closes[-1] * 0.95, closes[-1] * 0.91]
    s = DipBuyer().series(make(dip))
    assert s[-1].weight > 0 and "bought the dip" in s[-1].reason
    bounce = dip + [closes[-1] * 1.02]
    assert DipBuyer().series(make(bounce))[-1].weight == 0


# ---------- rebalance ----------

LIMITS = RiskLimits(max_position_pct=40, max_trade_pct=10, min_cash_pct=10, min_confidence=0.6,
                    max_daily_loss_pct=5, stop_loss_pct=15, min_order_usd=10)


def pf(cash=1000.0, held=None, prices=None):
    prices = prices or {"BTC/USD": 100.0, "ETH/USD": 10.0}
    pos = {s: {"amount": v / prices[s], "avg_entry": prices[s], "price": prices[s], "value": v, "pnl_pct": 0}
           for s, v in (held or {}).items()}
    return {"cash": cash, "equity": cash + sum(held.values() if held else []), "positions": pos, "prices": prices}


def test_rebalance_buys_sells_and_ignores_small_moves():
    orders, _ = risk.rebalance({"BTC/USD": 0.3, "ETH/USD": 0.0}, pf(700, {"ETH/USD": 300}), LIMITS, halted=False)
    assert [(o.symbol, o.side) for o in orders] == [("ETH/USD", "sell"), ("BTC/USD", "buy")]
    assert orders[1].spend == pytest.approx(300)
    orders, _ = risk.rebalance({"BTC/USD": 0.31}, pf(700, {"BTC/USD": 300}), LIMITS, halted=False)
    assert orders == []  # 1% off target is inside the band


def test_rebalance_caps_and_halts():
    orders, _ = risk.rebalance({"BTC/USD": 0.9}, pf(), LIMITS, halted=False)
    assert orders[0].spend == pytest.approx(400)  # max_position_pct 40
    orders, notes = risk.rebalance({"BTC/USD": 0.3}, pf(), LIMITS, halted=True)
    assert orders == [] and "daily loss" in notes[0]["why"]


# ---------- engine + Claude review memory ----------

class FakeReviewer:
    def __init__(self, verdict=None, error=None):
        self.calls, self.verdict, self.error = [], verdict or {"approve": True, "size": 1.0, "reason": "ok"}, error

    def review(self, entries, context):
        self.calls.append(entries)
        if self.error:
            return {"reviews": {}, "error": self.error}
        return {"reviews": {e["id"]: dict(self.verdict) for e in entries}, "usage": None}


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SIG_IN = {"BTC/USD": {"trend": Signal(1.0, "2025-12-30", "uptrend")}, "ETH/USD": {"trend": Signal(0.0, None, "no trend")}}


def test_engine_reviews_new_entry_once():
    rev = FakeReviewer()
    eng = StrategyEngine("trend", LIMITS, rev)
    plan = eng.plan(NOW, SIG_IN, {}, pf(), halted=False)
    assert len(rev.calls) == 1 and len(rev.calls[0]) == 1
    assert [o.side for o in plan["orders"]] == ["buy"] and plan["targets"]["BTC/USD"] == pytest.approx(0.5)
    eng.plan(NOW, SIG_IN, {}, pf(), halted=False)
    assert len(rev.calls) == 1  # remembered: no second Claude call for the same signal


def test_engine_veto_and_half_size():
    eng = StrategyEngine("trend", LIMITS, FakeReviewer({"approve": False, "size": 0.0, "reason": "too extended"}))
    plan = eng.plan(NOW, SIG_IN, {}, pf(), halted=False)
    assert plan["orders"] == [] and "Claude vetoed" in plan["decisions"][0]["reasoning"]
    eng = StrategyEngine("trend", LIMITS, FakeReviewer({"approve": True, "size": 0.5, "reason": "volatile"}))
    assert eng.plan(NOW, SIG_IN, {}, pf(), halted=False)["targets"]["BTC/USD"] == pytest.approx(0.25)


def test_engine_exits_without_review_and_blocks_reentry_after_stop():
    rev = FakeReviewer()
    eng = StrategyEngine("trend", LIMITS, rev)
    out = {"BTC/USD": {"trend": Signal(0.0, None, "no trend")}}
    plan = eng.plan(NOW, out, {}, pf(600, {"BTC/USD": 400}), halted=False)
    assert [o.side for o in plan["orders"]] == ["sell"] and rev.calls == []
    plan = eng.plan(NOW, SIG_IN, {}, pf(), halted=False, stopped={"BTC/USD"})
    assert plan["orders"] == [] and rev.calls == []


def test_engine_follows_strategy_when_claude_unavailable():
    eng = StrategyEngine("trend", LIMITS, FakeReviewer(error="daily Claude budget reached"), follow_unreviewed=True)
    plan = eng.plan(NOW, SIG_IN, {}, pf(), halted=False)
    assert [o.side for o in plan["orders"]] == ["buy"] and "budget" in plan["errors"][0]
    eng = StrategyEngine("trend", LIMITS, FakeReviewer(error="down"), follow_unreviewed=False)
    assert eng.plan(NOW, SIG_IN, {}, pf(), halted=False)["orders"] == []


def test_review_request_is_small():
    import json
    from types import SimpleNamespace
    from ai_trader.brain import ClaudeReviewer

    sent = {}

    class Msgs:
        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(stop_reason="end_turn", model=kw["model"], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                   content=[SimpleNamespace(type="text", text=json.dumps({"reviews": []}))])

    from ai_trader.engine import market_facts
    candles = make(wave(300))
    entries = [{"id": f"{s}|trend|2026-01-01", "symbol": s, "strategy": "Trend following", "rule": TrendFollowing.rules,
                "signal": "uptrend: +5% vs 50-day avg", "facts": market_facts(candles)} for s in ("BTC/USD", "ETH/USD", "SOL/USD")]
    ClaudeReviewer("claude-opus-5-5", "low", client=SimpleNamespace(beta=SimpleNamespace(messages=Msgs()))).review(entries, {"cash_pct": 50})
    chars = len(sent["system"]) + len(sent["messages"][0]["content"])
    assert chars < 3500  # roughly < 1k tokens for three entries
    assert sent["output_config"]["effort"] == "low" and sent["max_tokens"] <= 4000


# ---------- simulator ----------

def test_simulate_all_modes_run_and_charge_fees():
    daily = {"BTC/USD": make(wave(700, seed=2)), "ETH/USD": make(wave(700, drift=0.001, seed=5))}
    start, end = T0 + 400 * DAY_MS, T0 + 700 * DAY_MS
    for mode in ("trend", "breakout", "dip", "all"):
        r = simulate(mode, daily, start, end, 1000, 0.26, 0.05, LIMITS)
        assert len(r["points"]) == 300
        if r["fills"]:
            assert all(f["fee"] > 0 for f in r["fills"])
