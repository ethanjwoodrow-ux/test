"""Offline tests for the AI trader: fake market, fake Claude, real risk rules and paper broker."""

import json
import math
from types import SimpleNamespace

import pytest

from ai_trader import indicators, risk
from ai_trader.bot import TradingBot
from ai_trader.brain import ClaudeBrain, decision_schema
from ai_trader.broker import PaperBroker
from ai_trader.config import RiskLimits, Settings
from ai_trader.journal import Journal


# ---------- indicators ----------

def test_rsi_extremes():
    assert indicators.rsi(list(range(1, 40))) == 100.0
    assert indicators.rsi(list(range(40, 1, -1))) < 1


def test_summarize_shapes():
    candles = [[i, 100 + i, 101 + i, 99 + i, 100 + i + math.sin(i), 10 + i % 3] for i in range(100)]
    s = indicators.summarize(candles)
    assert s["sma_20"] and s["sma_50"] and s["macd"] and s["rsi_14"] is not None
    assert s["candles"] == 100


# ---------- paper broker ----------

def test_paper_buy_sell_round_trip(tmp_path):
    b = PaperBroker(tmp_path / "p.json", 1000, fee_pct=0.1, slippage_pct=0)
    fill = b.buy("BTC/USD", 500, 100)
    assert fill.amount == pytest.approx(5)
    assert b.cash() == pytest.approx(1000 - 500.5)
    b.sell("BTC/USD", 5, 110)
    assert b.positions() == {}
    assert b.cash() == pytest.approx(499.5 + 550 - 0.55)
    # state survives a restart
    assert PaperBroker(tmp_path / "p.json", 999, 0.1, 0).cash() == pytest.approx(b.cash())


def test_paper_avg_entry(tmp_path):
    b = PaperBroker(tmp_path / "p.json", 1000, 0, 0)
    b.buy("ETH/USD", 100, 10)
    b.buy("ETH/USD", 100, 20)
    assert b.positions()["ETH/USD"]["avg_entry"] == pytest.approx(200 / 15)


# ---------- risk ----------

LIMITS = RiskLimits(max_position_pct=25, max_trade_pct=10, min_cash_pct=20, min_confidence=0.6,
                    max_daily_loss_pct=5, stop_loss_pct=8, min_order_usd=10)


def portfolio(cash=10000, positions=None, prices=None):
    prices = prices or {"BTC/USD": 100.0, "ETH/USD": 10.0}
    pos = {}
    for s, (amount, entry) in (positions or {}).items():
        pos[s] = {"amount": amount, "avg_entry": entry, "price": prices[s], "value": amount * prices[s],
                  "pnl_pct": (prices[s] / entry - 1) * 100}
    return {"cash": cash, "equity": cash + sum(p["value"] for p in pos.values()), "positions": pos, "prices": prices}


def d(symbol, action, size, conf=0.9):
    return {"symbol": symbol, "action": action, "size_pct": size, "confidence": conf, "reasoning": "test"}


def test_buy_trimmed_to_max_trade():
    orders, rej = risk.vet([d("BTC/USD", "buy", 50)], portfolio(), LIMITS, halted=False)
    assert orders[0].spend == pytest.approx(1000)  # 10% of 10k
    assert "max trade size" in orders[0].reason


def test_buy_respects_position_cap():
    p = portfolio(cash=8000, positions={"BTC/USD": (20, 100)})  # 2000 of 10000 already in BTC
    orders, _ = risk.vet([d("BTC/USD", "buy", 10)], p, LIMITS, halted=False)
    assert orders[0].spend == pytest.approx(500)


def test_buy_respects_cash_reserve():
    p = portfolio(cash=2500, positions={"ETH/USD": (750, 10)})  # equity 10k, reserve 2k
    orders, _ = risk.vet([d("BTC/USD", "buy", 10)], p, LIMITS, halted=False)
    assert orders[0].spend == pytest.approx(500)


def test_low_confidence_and_halt_rejected():
    _, rej = risk.vet([d("BTC/USD", "buy", 5, conf=0.3)], portfolio(), LIMITS, halted=False)
    assert "confidence" in rej[0]["why"]
    orders, rej = risk.vet([d("BTC/USD", "buy", 5)], portfolio(), LIMITS, halted=True)
    assert not orders and "daily loss" in rej[0]["why"]


def test_sell_allowed_when_halted_and_processed_first():
    p = portfolio(cash=2000, positions={"ETH/USD": (800, 10)})
    orders, _ = risk.vet([d("BTC/USD", "buy", 10), d("ETH/USD", "sell", 50)], p, LIMITS, halted=False)
    assert [o.side for o in orders] == ["sell", "buy"]
    assert orders[0].amount == pytest.approx(400)
    assert orders[1].spend == pytest.approx(1000)  # freed cash lets the buy go through in full


def test_sell_without_position_rejected():
    _, rej = risk.vet([d("BTC/USD", "sell", 100)], portfolio(), LIMITS, halted=False)
    assert rej[0]["why"] == "no position to sell"


def test_stop_loss():
    p = portfolio(cash=0, positions={"BTC/USD": (1, 120)})  # price 100 is -16.7%
    orders = risk.stop_loss_orders(p, LIMITS)
    assert orders[0].side == "sell" and orders[0].amount == 1


# ---------- brain ----------

class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


def fake_client(payload, stop_reason="end_turn"):
    msgs = FakeMessages(payload, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_brain_parses_and_clamps():
    client, msgs = fake_client({"market_view": "up", "decisions": [
        {"symbol": "BTC/USD", "action": "buy", "size_pct": 400, "confidence": 3, "reasoning": "x"},
        {"symbol": "DOGE/USD", "action": "buy", "size_pct": 5, "confidence": 1, "reasoning": "not ours"},
    ]})
    brain = ClaudeBrain("claude-opus-5-5", "high", client=client)
    out = brain.decide({"BTC/USD": {"price": 1}}, portfolio(), LIMITS, [])
    assert out["decisions"] == [{"symbol": "BTC/USD", "action": "buy", "size_pct": 100.0, "confidence": 1.0, "reasoning": "x"}]
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["schema"] == decision_schema(["BTC/USD"])


def test_brain_refusal_holds():
    client, _ = fake_client({}, stop_reason="refusal")
    out = ClaudeBrain("m", "high", client=client).decide({"BTC/USD": {}}, portfolio(), LIMITS, [])
    assert out["decisions"] == [] and "refusal" in out["error"]


# ---------- full cycle ----------

class FakeMarket:
    def __init__(self, prices):
        self.prices = prices

    def ticker(self, symbol):
        p = self.prices[symbol]
        return {"price": p, "bid": p, "ask": p}

    def snapshot(self, symbol):
        if symbol not in self.prices:
            raise RuntimeError("exchange down")
        return {"symbol": symbol, **self.ticker(symbol)}


class FakeBrain:
    def __init__(self, decisions):
        self.decisions, self.seen = decisions, []

    def decide(self, snapshots, portfolio, limits, history):
        self.seen.append(portfolio)
        return {"market_view": "fake", "decisions": self.decisions}


def make_bot(tmp_path, prices, decisions, symbols=("BTC/USD", "ETH/USD")):
    settings = Settings.from_env()
    settings.symbols = list(symbols)
    settings.limits = LIMITS
    broker = PaperBroker(tmp_path / "p.json", 10000, 0, 0)
    return TradingBot(settings, FakeMarket(prices), broker, FakeBrain(decisions), Journal(tmp_path)), broker


def test_cycle_executes_and_logs(tmp_path):
    bot, broker = make_bot(tmp_path, {"BTC/USD": 100.0, "ETH/USD": 10.0},
                           [d("BTC/USD", "buy", 5), d("ETH/USD", "hold", 0)])
    rec = bot.run_cycle()
    assert len(rec["fills"]) == 1 and rec["fills"][0]["cost"] == pytest.approx(500)
    assert broker.positions()["BTC/USD"]["amount"] == pytest.approx(5)
    assert (tmp_path / "trades.csv").read_text().count("\n") == 2
    assert bot.journal.recent_history(bot.clock())[0]["fills"]


def test_cycle_stop_loss_runs_before_ai(tmp_path):
    bot, broker = make_bot(tmp_path, {"BTC/USD": 100.0, "ETH/USD": 10.0}, [])
    broker.buy("BTC/USD", 1000, 100)
    bot.market.prices["BTC/USD"] = 85.0
    rec = bot.run_cycle()
    assert rec["fills"][0]["reason"].startswith("stop-loss")
    assert "BTC/USD" not in bot.brain.seen[0]["positions"]


def test_cycle_survives_market_outage(tmp_path):
    bot, _ = make_bot(tmp_path, {"BTC/USD": 100.0}, [d("BTC/USD", "buy", 5)])
    rec = bot.run_cycle()
    assert any("ETH/USD" in e for e in rec["errors"])
    assert len(rec["fills"]) == 1


def test_brain_records_cost_and_respects_budget(tmp_path):
    from ai_trader.usage import Budget, UsageLedger

    ledger = UsageLedger(tmp_path / "u.jsonl")
    client, msgs = fake_client({"market_view": "", "decisions": []})
    brain = ClaudeBrain("claude-opus-5-5", "high", client=client, ledger=ledger,
                        budget=Budget(daily_usd=0.10, monthly_usd=0), source="paper")
    out = brain.decide({"BTC/USD": {"price": 1}}, portfolio(), LIMITS, [])
    assert out["usage"]["cost"] == pytest.approx((100 * 4 + 50 * 20) / 1e6)  # fake usage: 100 in, 50 out
    assert len(msgs.calls) == 1
    ledger.record("claude-opus-5-5", 25_000, 0, "paper")  # $0.10 more: today's spend is now over the cap
    out = brain.decide({"BTC/USD": {"price": 1}}, portfolio(), LIMITS, [])
    assert "daily Claude budget reached" in out["error"] and len(msgs.calls) == 1  # no API call made


def test_haiku_skips_fallbacks():
    client, msgs = fake_client({"market_view": "", "decisions": []})
    ClaudeBrain("claude-haiku-5-5", "low", client=client).decide({"BTC/USD": {}}, portfolio(), LIMITS, [])
    assert "fallbacks" not in msgs.calls[0] and "betas" not in msgs.calls[0]
