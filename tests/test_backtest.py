"""Backtest tests with a fake exchange (synthetic candles) and a fake Claude."""

import math
import time
from datetime import datetime, timezone

import pytest

from ai_trader.backtest import (DAY_MS, HOUR_MS, BacktestConfig, BacktestRun, HistoricalMarket, _day_ms,
                                fetch_candles, list_runs, max_drawdown_pct)
from ai_trader.config import Settings
from ai_trader.usage import Budget, UsageLedger

START, END = "2026-03-01", "2026-03-03"


def candles(start_ms, end_ms, step, base=100.0):
    out, t, i = [], start_ms, 0
    while t < end_ms:
        p = base * (1 + 0.05 * math.sin(i / 10)) * (1 + i * 0.0005)
        out.append([t, p, p * 1.01, p * 0.99, p, 10.0])
        t += step
        i += 1
    return out


class FakeExchange:
    """Serves synthetic history; like Kraken, returns at most 720 candles per call."""

    def __init__(self, first_ms=None):
        self.first_ms = first_ms or _day_ms("2025-11-01")
        self.end_ms = _day_ms(END) + DAY_MS
        self.calls = 0

    def parse_timeframe(self, tf):
        return 3600 if tf == "1h" else 86400

    def fetch_ohlcv(self, symbol, tf, since=None, limit=720):
        self.calls += 1
        step = self.parse_timeframe(tf) * 1000
        data = candles(self.first_ms, self.end_ms, step, 100 if symbol.startswith("BTC") else 10)
        return [c for c in data if c[0] >= since][:limit]


class FakeBrain:
    def __init__(self, ledger, source, cost=0.01):
        self.ledger, self.source, self.cost, self.calls, self.latest_seen = ledger, source, cost, 0, []

    def decide(self, snapshots, portfolio, limits, history):
        self.calls += 1
        self.ledger.record("claude-opus-5-5", int(self.cost * 1_000_000 / 4), 0, self.source)  # $cost at $4/M input
        self.latest_seen.append(snapshots["BTC/USD"]["price"])
        action = "buy" if self.calls % 4 == 1 else "sell" if self.calls % 4 == 3 else "hold"
        return {"market_view": "fake", "decisions": [
            {"symbol": "BTC/USD", "action": action, "size_pct": 10 if action == "buy" else 100,
             "confidence": 0.9, "reasoning": "test"}]}


def config(**kw):
    base = dict(exchange="fake", symbols=["BTC/USD", "ETH/USD"], start=START, end=END, step_hours=4,
                model="claude-opus-5-5", effort="low", starting_cash=1000, max_spend_usd=5)
    base.update(kw)
    return BacktestConfig(**base)


def run_backtest(tmp_path, cfg, exchange=None, budget=None):
    settings = Settings.from_env()
    if budget:
        settings.budget = budget
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    run = BacktestRun(cfg, settings, ledger, tmp_path / "bt" / "run1", exchange=exchange or FakeExchange())
    run._brain = FakeBrain(ledger, run.source)
    run._run_safely()
    return run, ledger


def test_fetch_candles_pages_through_history():
    ex = FakeExchange()
    since = _day_ms("2025-12-01")
    got = fetch_candles(ex, "BTC/USD", "1h", since, ex.end_ms)
    assert got[0][0] == since and got[-1][0] == ex.end_ms - HOUR_MS
    assert len(got) == (ex.end_ms - since) // HOUR_MS and ex.calls > 1


def test_market_never_shows_future_candles():
    hourly = {"BTC/USD": candles(0, 300 * HOUR_MS, HOUR_MS)}
    daily = {"BTC/USD": candles(0, 20 * DAY_MS, DAY_MS)}
    m = HistoricalMarket(hourly, daily)
    m.now_ms = 250 * HOUR_MS + HOUR_MS // 2  # half way through candle 250
    snap = m.snapshot("BTC/USD")
    assert snap["price"] == hourly["BTC/USD"][249][4]  # last *closed* candle
    assert snap["hourly"]["candles"] == 200
    assert snap["daily"]["candles"] == 10  # days 0-9 have closed by hour 250.5; day 10 hasn't


def test_backtest_runs_and_summarises(tmp_path):
    run, ledger = run_backtest(tmp_path, config())
    s = run.summary
    assert run.state == "done", run.message
    assert s["steps_done"] == 12  # 2 days / 4h
    assert s["trades"] > 0 and s["fees"] > 0
    assert s["return_pct"] is not None and s["hold_return_pct"] is not None
    assert s["claude_cost"] == pytest.approx(ledger.spent_by(run.source))
    assert s["from"].startswith("2026-03-01")
    assert list_runs(tmp_path / "bt")[0]["id"] == "run1"


def test_backtest_stops_at_spend_limit(tmp_path):
    run, ledger = run_backtest(tmp_path, config(max_spend_usd=0.05))
    assert run.state == "stopped" and "spending limit" in run.message
    assert ledger.spent_by(run.source) <= 0.05 + 1e-9


def test_backtest_moves_start_when_exchange_history_is_short(tmp_path):
    ex = FakeExchange(first_ms=_day_ms("2026-03-01") + 12 * HOUR_MS)
    run, _ = run_backtest(tmp_path, config(), exchange=ex)
    assert run.summary["note"] and "only has hourly history" in run.summary["note"]
    assert run.state == "done", run.message
    assert run.summary["from"] >= "2026-03-02T12"


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130]) == pytest.approx(-25)
