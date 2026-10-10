"""Everything the bot does gets written down: one JSON line per cycle plus a CSV of fills."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

FILL_FIELDS = ["timestamp", "symbol", "side", "amount", "price", "cost", "fee", "reason"]


class Journal:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cycles_path = self.dir / "cycles.jsonl"
        self.fills_path = self.dir / "trades.csv"
        self.state_path = self.dir / "bot_state.json"

    def log_cycle(self, record: dict):
        with self.cycles_path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def log_fill(self, fill: dict):
        new = not self.fills_path.exists()
        with self.fills_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FILL_FIELDS, extrasaction="ignore")
            if new:
                writer.writeheader()
            writer.writerow(fill)

    def cycles(self, n: int) -> list[dict]:
        if not self.cycles_path.exists():
            return []
        lines = self.cycles_path.read_text().splitlines()[-n:]
        return [json.loads(line) for line in lines if line.strip()]

    def recent_history(self, n: int = 12) -> list[dict]:
        """Compact summary of recent cycles to remind the AI what it did before."""
        out = []
        for c in self.cycles(n):
            out.append({
                "time": c.get("time"),
                "equity": c.get("equity_after"),
                "market_view": c.get("market_view", "")[:400],
                "decisions": [f"{d['symbol']} {d['action']} {d['size_pct']}% conf {d['confidence']}"
                              for d in c.get("decisions", []) if d["action"] != "hold"],
                "fills": [f"{f['side']} {f['symbol']} {f['cost']:.2f} @ {f['price']}" for f in c.get("fills", [])],
            })
        return out

    def day_start_equity(self, equity: float) -> float:
        """Equity at the first cycle of today (UTC); used for the daily loss limit."""
        today = datetime.now(timezone.utc).date().isoformat()
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if state.get("day") != today:
            state = {"day": today, "start_equity": equity}
            self.state_path.write_text(json.dumps(state))
        return state["start_equity"]
