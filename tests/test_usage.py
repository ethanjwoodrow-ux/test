"""Claude spend tracking and caps."""

import pytest

from ai_trader.usage import Budget, UsageLedger, call_cost, guess_call_cost


def test_call_cost():
    assert call_cost("claude-opus-5-5", 1_000_000, 100_000) == pytest.approx(4 + 2)
    assert call_cost("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    assert call_cost("unknown-model", 1_000_000, 0) == pytest.approx(4)  # priced like Opus to be safe
    assert guess_call_cost("claude-opus-5-5", "high") > guess_call_cost("claude-opus-5-5", "low")


def test_ledger_totals_and_caps(tmp_path):
    ledger = UsageLedger(tmp_path / "u.jsonl")
    ledger.record("claude-opus-5-5", 250_000, 0, "paper")       # $1.00
    ledger.record("claude-opus-5-5", 250_000, 0, "backtest:x")  # $1.00
    s = ledger.summary()
    assert s["today"] == pytest.approx(2) and s["month"] == pytest.approx(2) and s["calls"] == 2
    assert s["by_source"] == {"paper": pytest.approx(1), "backtest": pytest.approx(1)}
    assert ledger.spent_by("backtest:x") == pytest.approx(1)
    assert ledger.typical_call("claude-opus-5-5", "high") == pytest.approx(1)

    assert ledger.budget_problem(Budget(daily_usd=2.5, monthly_usd=10), next_call=1) and \
        "daily" in ledger.budget_problem(Budget(daily_usd=2.5, monthly_usd=10), next_call=1)
    assert "monthly" in ledger.budget_problem(Budget(daily_usd=0, monthly_usd=2.5), next_call=1)
    assert ledger.budget_problem(Budget(daily_usd=0, monthly_usd=0), next_call=100) is None
