"""Tests for the dashboard server pieces that don't need a browser or network."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ai_trader import dashboard


def test_update_env_file_keeps_other_lines(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("UC_USERNAME=me\nANTHROPIC_API_KEY=old\n# comment\n")
    monkeypatch.setattr(dashboard.os, "environ", {})
    dashboard.update_env_file(env, {"ANTHROPIC_API_KEY": "sk-ant-new", "TRADER_EXCHANGE": "binance"})
    assert env.read_text() == "UC_USERNAME=me\nANTHROPIC_API_KEY=sk-ant-new\n# comment\n\nTRADER_EXCHANGE=binance\n"


def test_equity_history_benchmark():
    cycles = [
        {"time": "t1", "equity_before": 1000, "equity_after": 1000, "prices": {"BTC/USD": 100}},
        {"time": "t2", "equity_before": 1000, "equity_after": 1050, "prices": {"BTC/USD": 120}},
    ]
    h = dashboard.equity_history(cycles, "BTC/USD")
    assert [p["equity"] for p in h] == [1000, 1000, 1050]
    assert [p["hold"] for p in h] == [1000, 1000, 1200]
    assert dashboard.equity_history([], "BTC/USD") == []


class FakeController:
    def __init__(self):
        self.ran = 0

    def state(self):
        return {"ok": True}

    def run_now(self):
        self.ran += 1


@pytest.fixture
def server(tmp_path, monkeypatch):
    page = tmp_path / "page.html"
    page.write_text("token=__TOKEN__")
    monkeypatch.setattr(dashboard, "PAGE", page)
    ctl = FakeController()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = dashboard.make_handler(ctl, "secret", port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", ctl
    srv.shutdown()


def _req(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_page_embeds_token_and_state_served(server):
    base, _ = server
    assert _req(base + "/") == (200, "token=secret")
    assert json.loads(_req(base + "/api/state")[1]) == {"ok": True}


def test_post_requires_token(server):
    base, ctl = server
    assert _req(base + "/api/run", "POST")[0] == 403
    assert _req(base + "/api/run", "POST", {"X-Trader-Token": "wrong"})[0] == 403
    assert _req(base + "/api/run", "POST", {"X-Trader-Token": "secret"})[0] == 200
    assert ctl.ran == 1


def test_foreign_host_rejected(server):
    base, _ = server
    assert _req(base + "/api/state", headers={"Host": "evil.example"})[0] == 403


@pytest.mark.parametrize("raw", ['  sk-ant-api03-abc  ', '"sk-ant-api03-abc"', "ANTHROPIC_API_KEY=sk-ant-api03-abc", "sk-ant-api03-\nabc"])
def test_clean_api_key(raw):
    assert dashboard.clean_api_key(raw) == "sk-ant-api03-abc"


def test_check_api_key_spots_openai_key():
    assert "OpenAI" in dashboard.check_api_key("sk-proj-123")
    assert "OpenAI" in dashboard.check_api_key("sk-123")


def test_check_api_key_rejected_and_offline(monkeypatch):
    import anthropic

    class Models:
        def __init__(self, exc):
            self.exc = exc

        def list(self, **kw):
            if self.exc:
                raise self.exc

    def fake_client(exc):
        return lambda **kw: type("C", (), {"models": Models(exc)})()

    resp = type("R", (), {"status_code": 401, "headers": {}, "request": None})()
    monkeypatch.setattr(anthropic, "Anthropic", fake_client(anthropic.AuthenticationError("bad", response=resp, body=None)))
    assert "rejected" in dashboard.check_api_key("sk-ant-api03-x")
    monkeypatch.setattr(anthropic, "Anthropic", fake_client(anthropic.APIConnectionError(request=None)))
    assert dashboard.check_api_key("sk-ant-api03-x") is None
    monkeypatch.setattr(anthropic, "Anthropic", fake_client(None))
    assert dashboard.check_api_key("sk-ant-api03-x") is None
