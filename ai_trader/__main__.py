"""Command line: python -m ai_trader [run|loop|status|reset]."""

import argparse
import shutil
import time

from rich.console import Console
from rich.table import Table

from ai_trader.bot import TradingBot, build_bot
from ai_trader.config import Settings

console = Console()


def show_cycle(record: dict):
    console.rule(f"[bold]{record['time']}  ({record['mode'].upper()})")
    if record.get("market_view"):
        console.print(f"[italic]{record['market_view']}[/italic]\n")
    if record.get("decisions"):
        t = Table("Coin", "Action", "Size %", "Conf", "Why")
        for d in record["decisions"]:
            colour = {"buy": "green", "sell": "red"}.get(d["action"], "white")
            t.add_row(d["symbol"], f"[{colour}]{d['action']}[/{colour}]", f"{d['size_pct']:.1f}",
                      f"{d['confidence']:.2f}", d["reasoning"])
        console.print(t)
    for r in record.get("rejected", []):
        console.print(f"[yellow]Blocked by risk rules:[/yellow] {r['action']} {r['symbol']} - {r['why']}")
    for f in record.get("fills", []):
        console.print(f"[bold]FILLED[/bold] {f['side'].upper()} {f['amount']:.6f} {f['symbol']} @ {f['price']:.2f} "
                      f"({f['cost']:.2f} + fee {f['fee']:.2f}) - {f['reason']}")
    for e in record.get("errors", []):
        console.print(f"[red]Error:[/red] {e}")
    if "equity_after" in record:
        console.print(f"\nEquity: [bold]{record['equity_after']:,.2f}[/bold]   Cash: {record['cash_after']:,.2f}"
                      + ("   [red]DAILY LOSS LIMIT HIT - buys paused[/red]" if record.get("halted") else ""))


def show_status(bot: TradingBot):
    prices = {}
    for symbol in bot.settings.symbols:
        try:
            prices[symbol] = bot.market.ticker(symbol)["price"]
        except Exception as e:
            console.print(f"[red]Could not price {symbol}: {e}[/red]")
    p = bot.portfolio(prices)
    t = Table("Coin", "Amount", "Avg entry", "Price", "Value", "P&L %", title=f"Portfolio ({bot.settings.mode})")
    for s, pos in p["positions"].items():
        colour = "green" if pos["pnl_pct"] >= 0 else "red"
        t.add_row(s, f"{pos['amount']:.6f}", f"{pos['avg_entry']:.2f}", f"{pos['price']:.2f}",
                  f"{pos['value']:,.2f}", f"[{colour}]{pos['pnl_pct']:+.2f}[/{colour}]")
    console.print(t)
    console.print(f"Cash: {p['cash']:,.2f}   Equity: [bold]{p['equity']:,.2f}[/bold]")
    if not bot.broker.is_live:
        start = bot.broker.state.data["starting_cash"]
        console.print(f"Return since start: {(p['equity'] / start - 1) * 100:+.2f}% (started with {start:,.2f})")
    console.print(f"Logs: {bot.journal.dir}")


def main():
    parser = argparse.ArgumentParser(prog="ai_trader", description="AI crypto trading bot powered by Claude")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("dashboard", help="open the web dashboard (the default)")
    sub.add_parser("run", help="run one trading cycle")
    loop = sub.add_parser("loop", help="run a cycle every N minutes until stopped (Ctrl+C)")
    loop.add_argument("--minutes", type=float, default=60)
    sub.add_parser("status", help="show the current portfolio")
    sub.add_parser("reset", help="wipe the paper portfolio and logs and start over")
    args = parser.parse_args()

    if args.cmd in (None, "dashboard"):
        from ai_trader.dashboard import run
        run()
        return

    settings = Settings.from_env()

    if args.cmd == "reset":
        if settings.is_live:
            raise SystemExit("reset only works in paper mode")
        target = settings.data_dir / "paper"
        if input(f"Delete {target}? Type yes: ").strip().lower() == "yes":
            shutil.rmtree(target, ignore_errors=True)
            console.print("Paper account reset.")
        return

    bot = build_bot(settings)
    if settings.is_live:
        console.print("[bold red]LIVE MODE - real money, real orders.[/bold red]")

    if args.cmd == "status":
        show_status(bot)
    elif args.cmd == "run":
        show_cycle(bot.run_cycle())
    elif args.cmd == "loop":
        console.print(f"Trading {', '.join(settings.symbols)} every {args.minutes:g} min. Ctrl+C to stop.")
        try:
            while True:
                started = time.monotonic()
                try:
                    show_cycle(bot.run_cycle())
                except Exception as e:  # keep the loop alive through one bad cycle
                    console.print(f"[red]Cycle crashed: {e}[/red]")
                time.sleep(max(0, args.minutes * 60 - (time.monotonic() - started)))
        except KeyboardInterrupt:
            console.print("Stopped.")


if __name__ == "__main__":
    main()
