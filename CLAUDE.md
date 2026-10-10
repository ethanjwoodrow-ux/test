# Project notes for Claude

This repo holds two separate things:
- `uc_automation/`, `scripts/` and the `.bat` files: an older University of Canterbury (UC Learn) automation toolkit. Not part of the trading work.
- `ai_trader/` and `START_TRADER.py`: an AI-assisted crypto trading bot. **This is the active project.** The user guide is `AI_TRADER.md`.

## About the user

- Based in New Zealand, on Windows. Not a programmer: give plain-English steps and exact commands to paste.
- Wants to trade **small capital (about NZ$100)** eventually. Currently paper trading (fake money) only.
- Prefers things to just work: one double-click to start, a web dashboard, no command-line chores.
- Windows Smart App Control blocks downloaded `.bat` files, which is why the launcher is `START_TRADER.py`. PowerShell blocks `claude.ps1`, so run Claude Code as `claude.cmd`.
- Project folder on their PC: `C:\Users\ethan\AppData\AI\ai bot`. Runtime data (paper account, logs, backtests, Claude spend log) is in `%USERPROFILE%\ai_trader_data\`.

## How the bot works now

- **Strategy mode (default `all`).** Three rule-based strategies on daily candles (`ai_trader/strategies.py`) do the trading:
  - `trend`: above the 50-day average and up over 30 days; exit below the 50-day average.
  - `breakout`: Donchian ensemble over 20–360 days with ratcheting mid-line stops, after Zarattini, Pagani & Barbon, "Catching Crypto Trends" (2025).
  - `dip`: RSI-2 < 10 in an uptrend (above the 200-day average); exit on a bounce, after 10 days, or when the trend breaks.
  - `all` runs each strategy on a third of the account. All size positions by volatility (target ~40% annualised per coin).
- **Claude is a reviewer, not the trader** (`ai_trader/engine.py`, `brain.py`). It reviews each *new entry* once (approve, shrink or veto) in one small batched request per cycle at low effort. The verdict is remembered in `reviews.json`, and exits never wait for Claude. A weekly "strategy coach" call compares 90-day results of all modes and suggests which to run.
- Old mode `claude` (Claude makes every decision) still exists. It costs about 50× more.
- **Risk rules** (`risk.py`): max 40% per coin, keep 10% cash, 15% stop-loss (no re-entry until a fresh signal), no buys after a 5% losing day, and a 5%-of-equity no-trade band when rebalancing.
- **Claude spend tracking** (`usage.py`): every call is logged with its estimated cost. Daily and monthly caps (default $5/$40) cover trading and backtests.
- **Backtests** (`backtest.py`): `simulate()` replays daily candles with no lookahead (tested). "Compare all three" is free (rules only). Optional Claude review has a per-backtest spending limit. Kraken only keeps about 2 years of daily data; Binance has more.
- **Dashboard** (`dashboard.py` + `dashboard.html`): local web UI at http://127.0.0.1:8765 with Trading and Backtest tabs and Settings. It binds to localhost only, and POSTs need a per-launch token.

## Working on it

- Tests: `python -m pytest tests -q` (all offline, with fake exchanges and fake Claude clients). Keep them passing.
- Check UI changes in a real browser at desktop and phone widths, light and dark.
- Default Claude model is `claude-opus-5-5`; reviews run at `low` effort. Don't switch models to save money without asking the user.
- Develop on branch `claude/festive-davinci-0shaij` and push there.

## Where we left off (next steps)

1. **Review the user's real backtest results** in `%USERPROFILE%\ai_trader_data\backtests\*\summary.json` (plus `curves.json` and `trades.csv`). Earlier work only ran on fake price data, because exchanges are blocked in the cloud sandbox.
2. **Cut fee churn.** On test data, "all three combined" made 125 trades, of which 92 were small resizes, not entries or exits. Plan: each strategy trades only on its own entry or exit events, sizes at entry, and doesn't re-size for volatility while holding. Measure fees and return before and after.
3. **Fix or replace the dip buyer.** It has the weakest evidence and the highest fees: on test data fees ate about half its profit. Options: stricter entry, BTC/ETH only, or swap in another strategy. Decide from the user's real backtests.
4. **Better scoring:** add risk-adjusted return (Sharpe), time in market and win rate, compare against an equal-weight hold of all coins (not just BTC), and add a one-click "test several periods" backtest (bull, crash, sideways).
5. **Check whether Claude's review helps:** run the same backtest with review on and off. If it doesn't add value, turn it off or keep it only for risk vetoes.
6. Later: small-account mode for ~NZ$100 (fewer coins, respect exchange minimum order sizes, NZD display), and picking an exchange the user can fund in NZD before any live trading.
