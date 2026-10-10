"""Local web dashboard for the trader. Start with: python -m ai_trader dashboard (or START_TRADER.py)."""

import csv
import json
import os
import re
import secrets
import shutil
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic
import ccxt

from ai_trader import backtest
from ai_trader.bot import build_bot, usage_ledger
from ai_trader.config import ENV_PATH, Settings
from ai_trader.usage import EFFORT_OUTPUT_FACTOR, guess_call_cost

HOST = "127.0.0.1"
PORT = 8765
PAGE = Path(__file__).with_name("dashboard.html")
PRICE_REFRESH_SECONDS = 30
EFFORTS = ("low", "medium", "high", "xhigh", "max")
BACKTEST_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
SYMBOL_RE = re.compile(r"[A-Z0-9]+/[A-Z0-9]+")


def parse_symbols(raw: str) -> list[str] | None:
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return symbols if symbols and all(SYMBOL_RE.fullmatch(s) for s in symbols) else None


def _positive(raw, allow_zero=False) -> float | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 or (allow_zero and v == 0) else None


def update_env_file(path: Path, values: dict[str, str]):
    """Set KEY=value lines in .env, keeping every other line (UC Learn settings etc.) as-is."""
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(lines) + "\n")
    os.environ.update(values)


def clean_api_key(raw: str) -> str:
    """Strip the spaces, quotes and 'ANTHROPIC_API_KEY=' people often copy along with a key."""
    key = raw.strip().strip('"').strip("'").strip()
    if "=" in key and key.split("=", 1)[0].strip().upper().endswith("KEY"):
        key = key.split("=", 1)[1].strip().strip('"').strip("'")
    return "".join(key.split())


def check_api_key(key: str) -> str | None:
    """Return a plain-English problem with the key, or None if Claude accepts it."""
    if key.startswith(("sk-proj-", "sk-svcacct-")) or (key.startswith("sk-") and not key.startswith("sk-ant-")):
        return ("That's an OpenAI (ChatGPT) key, not a Claude key. Get a Claude key at "
                "platform.claude.com > Settings > API keys. It starts with sk-ant-")
    if key.startswith("sk-ant-admin"):
        return "That's an Admin key. Create a normal API key instead (Settings > API keys > Create key)."
    try:
        anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.list(limit=1)
    except anthropic.AuthenticationError:
        return ("Claude rejected that key. Copy it again from platform.claude.com > Settings > API keys, "
                "or create a new one (a key is only shown once, when you create it).")
    except (anthropic.APIConnectionError, anthropic.APIStatusError):
        pass  # can't verify right now (offline, outage); save it and let the first run report problems
    return None


class Controller:
    """Owns the bot, runs cycles in the background and keeps a cached view for the page to poll."""

    def __init__(self):
        self.cycle_lock = threading.Lock()
        self.busy = False
        self.auto_minutes: float | None = None
        self.next_run: float | None = None
        self.last_error: str | None = None
        self.portfolio: dict | None = None
        self.portfolio_at = 0.0
        self.backtest: backtest.BacktestRun | None = None
        self._load()
        prefs = self._prefs()
        if prefs.get("auto_minutes") and not self.settings.is_live and self.bot:
            self.set_auto(prefs["auto_minutes"])
        threading.Thread(target=self._scheduler, daemon=True).start()
        threading.Thread(target=self._price_loop, daemon=True).start()

    # ---- setup ----

    def _load(self):
        self.settings = Settings.from_env()
        self.bot, self.setup_error = None, None
        try:
            self.bot = build_bot(self.settings)
        except SystemExit as e:
            self.setup_error = str(e)
        except Exception as e:
            self.setup_error = f"Could not start the bot: {e}"
        self.ledger = usage_ledger(self.settings)
        self.portfolio, self.portfolio_at = None, 0.0

    def _prefs_path(self) -> Path:
        return self.settings.data_dir / "dashboard.json"

    def _prefs(self) -> dict:
        try:
            return json.loads(self._prefs_path().read_text())
        except (OSError, ValueError):
            return {}

    def _save_prefs(self):
        self._prefs_path().parent.mkdir(parents=True, exist_ok=True)
        self._prefs_path().write_text(json.dumps({"auto_minutes": self.auto_minutes}))

    def save_settings(self, body: dict) -> str | None:
        values = {}
        key = clean_api_key(body.get("api_key", ""))
        if key:
            problem = check_api_key(key)
            if problem:
                return problem
            values["ANTHROPIC_API_KEY"] = key
        if body.get("exchange"):
            if body["exchange"] not in ccxt.exchanges:
                return f"Unknown exchange {body['exchange']!r}."
            values["TRADER_EXCHANGE"] = body["exchange"]
        if body.get("symbols"):
            symbols = parse_symbols(body["symbols"])
            if not symbols:
                return "Coins must look like BTC/USD, separated by commas."
            values["TRADER_SYMBOLS"] = ",".join(symbols)
        for field, env, label in (("daily_budget", "CLAUDE_DAILY_BUDGET_USD", "Daily cap"),
                                  ("monthly_budget", "CLAUDE_MONTHLY_BUDGET_USD", "Monthly cap")):
            if body.get(field) not in (None, ""):
                v = _positive(body[field], allow_zero=True)
                if v is None:
                    return f"{label} must be a number (0 means no cap)."
                values[env] = f"{v:g}"
        if body.get("starting_cash"):
            try:
                if float(body["starting_cash"]) <= 0:
                    raise ValueError
            except ValueError:
                return "Starting cash must be a positive number."
            values["PAPER_STARTING_CASH"] = str(body["starting_cash"])
        if body.get("effort"):
            if body["effort"] not in EFFORTS:
                return "Unknown effort level."
            values["TRADER_EFFORT"] = body["effort"]
        if not values:
            return None
        if self.busy:
            return "Wait for the current cycle to finish, then save again."
        with self.cycle_lock:
            update_env_file(ENV_PATH, values)
            self._load()
        return None

    def reset_paper(self) -> str | None:
        if self.settings.is_live:
            return "Reset only works in paper mode."
        if self.busy:
            return "Wait for the current cycle to finish."
        with self.cycle_lock:
            self.set_auto(None)
            shutil.rmtree(self.settings.data_dir / "paper", ignore_errors=True)
            self._load()
        return None

    # ---- running ----

    def set_auto(self, minutes: float | None):
        self.auto_minutes = minutes if minutes and minutes > 0 else None
        self.next_run = time.time() if self.auto_minutes else None
        self._save_prefs()

    def run_now(self) -> str | None:
        if not self.bot:
            return self.setup_error or "Finish setup first."
        if self.busy:
            return "A cycle is already running."
        threading.Thread(target=self._cycle, daemon=True).start()
        return None

    def _cycle(self):
        if not self.cycle_lock.acquire(blocking=False):
            return
        self.busy = True
        try:
            record = self.bot.run_cycle()
            self.last_error = "; ".join(record.get("errors", [])) or None
        except Exception as e:
            self.last_error = f"Cycle crashed: {e}"
        finally:
            self.busy = False
            self.cycle_lock.release()
            self.portfolio_at = 0  # refresh now

    def _scheduler(self):
        while True:
            time.sleep(1)
            if self.auto_minutes and self.next_run and time.time() >= self.next_run and self.bot and not self.busy:
                self.next_run = time.time() + self.auto_minutes * 60
                self._cycle()

    def _price_loop(self):
        while True:
            time.sleep(1)
            bot = self.bot
            if not bot or self.busy or time.time() - self.portfolio_at < PRICE_REFRESH_SECONDS:
                continue
            self.portfolio_at = time.time()
            prices, stale = {}, []
            for symbol in bot.settings.symbols:
                try:
                    prices[symbol] = bot.market.ticker(symbol)["price"]
                except Exception:
                    stale.append(symbol)
            if stale:  # fall back to the last prices the bot traded on
                last = bot.journal.cycles(1)
                for s in stale:
                    if last and s in last[0].get("prices", {}):
                        prices[s] = last[0]["prices"][s]
            try:
                p = bot.portfolio(prices)
                p["stale_prices"] = stale
                self.portfolio = p
            except Exception as e:
                self.last_error = f"Could not read portfolio: {e}"

    # ---- backtests ----

    def start_backtest(self, body: dict) -> str | None:
        if self.backtest and self.backtest.state in ("starting", "loading", "running"):
            return "A backtest is already running. Stop it first."
        if not os.getenv("ANTHROPIC_API_KEY"):
            return "Add your Claude API key first."
        symbols = parse_symbols(body.get("symbols", ""))
        if not symbols:
            return "Coins must look like BTC/USD, separated by commas."
        if body.get("exchange") not in ccxt.exchanges:
            return "Pick an exchange."
        try:
            start = datetime.strptime(body.get("start", ""), "%Y-%m-%d").date()
            end = datetime.strptime(body.get("end", ""), "%Y-%m-%d").date()
        except ValueError:
            return "Pick a start and end date."
        if start >= end:
            return "The end date has to be after the start date."
        if end > datetime.now(timezone.utc).date() + timedelta(days=1):
            return "The end date can't be in the future."
        step = int(body.get("step_hours") or 0)
        if step not in (1, 2, 4, 6, 12, 24):
            return "Pick how often Claude decides."
        if body.get("model") not in BACKTEST_MODELS or body.get("effort") not in EFFORTS:
            return "Pick a model and effort."
        cash, limit = _positive(body.get("starting_cash")), _positive(body.get("max_spend_usd"))
        if cash is None or limit is None:
            return "Starting money and spending limit must be positive numbers."
        problem = self.ledger.budget_problem(self.settings.budget, self.ledger.typical_call(body["model"], body["effort"]))
        if problem:
            return f"Can't start: {problem}. You can raise the caps in Settings."
        cfg = backtest.BacktestConfig(exchange=body["exchange"], symbols=symbols, start=start.isoformat(),
                                      end=end.isoformat(), step_hours=step, model=body["model"],
                                      effort=body["effort"], starting_cash=cash, max_spend_usd=limit)
        self.backtest = backtest.BacktestRun(cfg, self.settings, self.ledger,
                                             backtest.new_run_dir(self._backtest_root()))
        self.backtest.start()
        return None

    def stop_backtest(self) -> str | None:
        if self.backtest:
            self.backtest.stop()
        return None

    def _backtest_root(self) -> Path:
        return self.settings.data_dir / "backtests"

    def backtest_detail(self, run_id: str) -> dict | None:
        if not re.fullmatch(r"\d{8}-\d{6}", run_id):
            return None
        run_dir = self._backtest_root() / run_id
        if not (run_dir / "summary.json").exists():
            return None
        journal_cycles = [json.loads(line) for line in (run_dir / "cycles.jsonl").read_text().splitlines()
                          if line.strip()] if (run_dir / "cycles.jsonl").exists() else []
        summary = json.loads((run_dir / "summary.json").read_text())
        live = self.backtest if self.backtest and self.backtest.id == run_id else None
        if live and live.state in ("starting", "loading", "running"):  # fresh numbers mid-run
            summary.update(backtest.results(journal_cycles, summary["config"]["symbols"][0]),
                           state=live.state, message=live.message, claude_cost=live.status()["spent"])
        return {
            "summary": summary,
            "history": equity_history(journal_cycles, summary["config"]["symbols"][0]),
            "cycles": [{k: c.get(k) for k in ("time", "market_view", "decisions", "rejected", "fills", "errors")}
                       for c in reversed(journal_cycles[-200:])],
            "trades": read_trades(run_dir / "trades.csv", 500),
        }

    def _cost_estimates(self) -> dict:
        """Rough cost of one Claude call per backtest model and effort. Uses your real average where known."""
        s = self.settings
        recent = [e["cost"] for e in self.ledger.entries() if e["model"] == s.model][-10:]
        avg = sum(recent) / len(recent) if recent else None
        out = {}
        for model in BACKTEST_MODELS:
            out[model] = {}
            for effort in EFFORTS:
                if model == s.model and avg is not None:
                    out[model][effort] = avg * EFFORT_OUTPUT_FACTOR[effort] / EFFORT_OUTPUT_FACTOR.get(s.effort, 1.0)
                else:
                    out[model][effort] = guess_call_cost(model, effort)
        return out

    # ---- view ----

    def state(self) -> dict:
        s = self.settings
        out = {
            "mode": s.mode, "exchange": s.exchange, "symbols": s.symbols, "model": s.model, "effort": s.effort,
            "quote": s.quote_currency, "starting_cash": s.starting_cash,
            "has_api_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "setup_error": self.setup_error, "busy": self.busy, "auto_minutes": self.auto_minutes,
            "next_run_in": max(0, round(self.next_run - time.time())) if self.next_run else None,
            "last_error": self.last_error, "limits": asdict(s.limits), "data_dir": str(s.data_dir / s.mode),
            "portfolio": None, "history": [], "cycles": [], "trades": [],
            "usage": {**self.ledger.summary(), "daily_cap": s.budget.daily_usd, "monthly_cap": s.budget.monthly_usd,
                      "per_call": self.ledger.typical_call(s.model, s.effort)},
            "backtest": {
                "current": self.backtest.status() if self.backtest else None,
                "runs": backtest.list_runs(self._backtest_root())[:20],
                "default_dates": backtest.default_dates(7),
                "estimates": self._cost_estimates(),
                "models": BACKTEST_MODELS,
            },
        }
        if not self.bot:
            return out
        if self.portfolio:
            p = self.portfolio
            out["portfolio"] = {
                "cash": p["cash"], "equity": p["equity"], "stale_prices": p.get("stale_prices", []),
                "positions": [{"symbol": sym, **{k: v for k, v in pos.items()}} for sym, pos in p["positions"].items()],
            }
        cycles = self.bot.journal.cycles(1000)
        out["history"] = equity_history(cycles, s.symbols[0] if s.symbols else None)
        out["cycles"] = [{k: c.get(k) for k in ("time", "market_view", "decisions", "rejected", "fills", "errors", "halted")}
                         for c in reversed(cycles[-30:])]
        out["trades"] = read_trades(self.bot.journal.fills_path, 100)
        return out


def equity_history(cycles: list[dict], benchmark: str | None) -> list[dict]:
    """Bot equity after each cycle, next to what simply holding `benchmark` from day one would be worth."""
    cycles = [c for c in cycles if "equity_after" in c]
    if not cycles:
        return []
    base = cycles[0]["equity_before"]
    p0 = cycles[0].get("prices", {}).get(benchmark)
    points = [{"time": cycles[0]["time"], "equity": base, "hold": base if p0 else None}]
    for c in cycles:
        pt = c.get("prices", {}).get(benchmark)
        points.append({"time": c["time"], "equity": c["equity_after"],
                       "hold": round(base * pt / p0, 2) if p0 and pt else None})
    return points


def read_trades(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return list(reversed(rows[-n:]))


def make_handler(controller: Controller, token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: only answer requests addressed to this machine by name.
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"})
            if self.path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, controller.state())
            if self.path.startswith("/api/backtest/"):
                detail = controller.backtest_detail(self.path.rsplit("/", 1)[1])
                return self._send(200 if detail else 404, detail or {"error": "not found"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            # The token lives only in our own page, so other websites can't drive the bot.
            if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Trader-Token", ""), token):
                return self._send(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            if self.path == "/api/run":
                err = controller.run_now()
            elif self.path == "/api/auto":
                minutes = body.get("minutes")
                controller.set_auto(float(minutes) if minutes else None)
                err = None
            elif self.path == "/api/settings":
                err = controller.save_settings(body)
            elif self.path == "/api/reset":
                err = controller.reset_paper()
            elif self.path == "/api/backtest/start":
                err = controller.start_backtest(body)
            elif self.path == "/api/backtest/stop":
                err = controller.stop_backtest()
            else:
                return self._send(404, {"error": "not found"})
            self._send(400 if err else 200, {"error": err} if err else {"ok": True})

    return Handler


def run(port: int = PORT, open_browser: bool = True):
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer((HOST, port), make_handler(Controller(), secrets.token_urlsafe(24), port))
    except OSError:
        print(f"The dashboard already seems to be running. Opening {url}")
        if open_browser:
            webbrowser.open(url)
        return
    print("=" * 56)
    print(f"  AI Trader dashboard:  {url}")
    print("  Keep this window open while trading. Close it to stop.")
    print("=" * 56)
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
