# AI Crypto Trader

Claude reads live crypto market data and decides to buy, sell, or hold each coin. A separate layer of hard-coded risk rules decides what actually gets traded. It starts in **paper mode**: fake money at real prices, so you can see how it performs before risking anything.

## How it works

Three rule-based strategies do the trading. Claude reviews them instead of making every call, which cuts Claude costs by roughly 50–100×.

Every cycle (you pick how often; once an hour is plenty):

1. **Prices.** It reads ~400 days of daily candles plus the live price for each coin.
2. **Stop-losses.** Any coin `RISK_STOP_LOSS_PCT` (default 15%) below its entry is sold first. It isn't bought back until its strategy gives a fresh signal.
3. **Strategy signals.** Each strategy says whether to hold each coin and how much (details below). Each coin gets an equal slot of the account.
4. **Claude review, new entries only.** A brand-new entry goes to Claude once, in one small request covering every new signal that cycle. Claude can approve it, halve it, or veto it, and its verdict is remembered, so the same signal is never paid for twice. Exits and trims never wait for Claude.
5. **Rebalance with safety rules.** At most 40% of the account per coin and at least 10% kept in cash. Small differences are ignored so fees don't eat the account. After a 5% losing day, no new buys until tomorrow.
6. **Log.** Every cycle goes to `cycles.jsonl` and every fill to `trades.csv`.

Once a week while auto-trading, or when you click **Review now**, Claude also looks at how all three strategies did over the last 90 days (worked out for free) and suggests which to run. That's one request, a few cents.

If Claude is unreachable or a spending cap is reached, the strategy keeps trading by default. You can switch that to "skip the trade" in Settings.

### The three strategies

| Strategy | Rules | Why it's here |
|---|---|---|
| **Trend following** | Buy when the daily close is above the 50-day average and the coin is up over 30 days. Sell when it closes below the 50-day average. | Time-series momentum has the most consistent evidence among simple crypto rules ([Han, Kang & Ryu](https://www.journals.vu.lt/BATP/article/view/44540); [QuantPedia summary](https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/)). Much of the edge disappears once fees are counted. |
| **Breakout ensemble** | Seven Donchian channels (20–360 days). Each one buys a new N-day closing high and sells below its ratcheting mid-line. The position is the share of channels that are in. | Based on Zarattini, Pagani & Barbon, ["Catching Crypto Trends"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907) (2025), which reports a net-of-fee Sharpe above 1.5 on a top-20 coin portfolio. This is a simpler, lower-turnover version: the 5- and 10-day channels are dropped. |
| **Dip buyer** | Only in an uptrend (above the 200-day average). Buy when the 2-day RSI drops below 10. Sell on a bounce above the 5-day average, after 10 days, or if the uptrend breaks. | Connors-style RSI-2. The evidence on daily crypto data is weak and mixed ([Corbet & Katsiampa](https://shura.shu.ac.uk/23470/)). It's included because it trades when the trend strategies are waiting, which smooths the combined result. |
| **All three combined** (default) | Each strategy runs on a third of the account. | Spreading across different styles usually gives a smoother ride than any one of them. |

All three size positions by volatility, aiming each coin at about 40% annual volatility, so a wild coin gets a smaller position than a calm one. The old mode, where Claude makes every decision, is still available in Settings as "Claude decides everything". It costs about 50× more.

**Honest expectations:** trend-following rules tend to lag buy-and-hold in strong bull runs and earn their keep by stepping aside in crashes. Backtest several periods (a bull run, a crash, a sideways year) before trusting any of them.

## Start it (Windows)

You need Python 3.10 or newer from https://www.python.org/downloads/. Tick **"Add Python to PATH"** during install.

1. **Double-click `START_TRADER.py`.** A black window opens. The first time, it installs what it needs, which takes about a minute. Then the dashboard opens in your browser at http://127.0.0.1:8765.
2. **Paste your Claude API key** into the setup screen and click **Save and start**. Get a key at https://platform.claude.com and add a few dollars of credit there first.
3. Click **Run now** to watch one round of decisions, or set **Auto-trade** to run every hour (or 15 min to once a day).

Keep the black window open while it trades. Closing it stops the bot. Next time, double-click `START_TRADER.py` again; auto-trade picks up where you left it.

If double-clicking opens the file in an editor or Notepad instead, right-click it, choose **Open with**, then **Python**.

The `.py` launcher is used instead of `.bat` files because Windows Smart App Control blocks downloaded `.bat` scripts.

### What the dashboard shows

- **Total value, return since start, and vs just holding BTC.** That last one is the honest test: is the bot beating doing nothing?
- **Value over time:** the bot's portfolio next to what holding BTC from day one would be worth. Hover for exact numbers.
- **Latest AI decision:** Claude's read of the market and its call on each coin, with its reasoning, plus anything the safety rules blocked.
- **Holdings, trades and earlier decisions.**
- **Strategy:** each coin's status in every strategy (in or out, since when, and why), and the target holding.
- **Claude's strategy review:** the weekly 90-day comparison and Claude's suggestion, with a one-click switch.
- **Settings:** strategy, Claude review on or off, review model and effort, spending caps, exchange, coins, or reset the paper account.

### Backtesting

The **Backtest** tab replays real past prices with fake money.

- **Compare all three (free, the default):** runs Trend, Breakout, Dip and the combination side by side against just holding, over any period you pick. It takes seconds and costs nothing. Run it on several periods.
- **One strategy with Claude review:** the same, but Claude reviews each new entry. Costs about a cent per review; a year on three coins is usually a few dollars. Set a spending limit; past it, the backtest continues on the rules alone.
- **Claude decides everything (old mode):** one Claude call per step. Expensive; keep it to short periods.

At each step the strategy (and Claude) only sees prices up to that moment. The strategies need about a year of history to warm up their longest channels. **Kraken keeps about 2 years of daily prices**, so for longer backtests pick **Binance** with coins like `BTC/USDT`.

### Claude credit tracking and caps

Every Claude call is logged with its cost in `ai_trader_data\claude_usage.jsonl`. The **Claude spent today** tile shows today's spend, this month's spend and the average cost per run.

Set caps in **Settings**:
- **Daily cap** (default $5) and **monthly cap** (default $40) cover trading and backtests together. 0 means no cap.
- When a cap is reached, the bot holds everything instead of calling Claude. It starts again the next day (or month).

The costs are estimates worked out from token counts and Anthropic's list prices. Your real bill and balance are at platform.claude.com under **Settings → Billing**. You can also set a spending limit there as a second safety net.

### Command line (optional)

`python -m ai_trader` opens the dashboard. `python -m ai_trader run | loop --minutes 60 | status | reset` do the same jobs without it.

Logs are saved to `%USERPROFILE%\ai_trader_data\paper\`. `trades.csv` opens in Excel.

## Settings

All settings go in `.env`. `.env.example` lists every option. The main ones:

| Setting | Default | What it does |
|---|---|---|
| `TRADER_EXCHANGE` | `kraken` | Any [ccxt exchange](https://github.com/ccxt/ccxt#supported-cryptocurrency-exchanges), e.g. `binance`, `coinbase` |
| `TRADER_SYMBOLS` | `BTC/USD,ETH/USD,SOL/USD` | Coins to trade. Use the exchange's pair names (Binance uses `BTC/USDT`) |
| `TRADER_EFFORT` | `high` | How hard Claude thinks: `low` / `medium` / `high` / `xhigh` / `max` |
| `PAPER_STARTING_CASH` | `10000` | Fake money for paper mode |
| `TRADER_STRATEGY` | `all` | `all`, `trend`, `breakout`, `dip`, or `claude` (Claude decides everything) |
| `TRADER_REVIEW` | `on` | Claude reviews new entries (`on`/`off`) |
| `TRADER_REVIEW_MODEL` | `claude-opus-5-5` | Model for reviews and the weekly report (`claude-sonnet-5-5`, `claude-haiku-5-5` are cheaper) |
| `TRADER_REVIEW_EFFORT` | `low` | Effort for reviews |
| `TRADER_IF_NO_REVIEW` | `follow` | If Claude can't review: `follow` the strategy or `skip` the trade |
| `CLAUDE_DAILY_BUDGET_USD` | `5` | Max Claude spend per day, trading + backtests (0 = no cap) |
| `CLAUDE_MONTHLY_BUDGET_USD` | `40` | Max Claude spend per month (0 = no cap) |
| `RISK_*` | see above | The hard limits |

**API cost:** in the strategy modes Claude only runs on new entries (about a cent each with Opus at low effort; a few per coin per month) plus one weekly review, so usually well under a dollar a month. The old "Claude decides everything" mode is about $0.05–0.10 per run, roughly $1–2 a day hourly. The **Claude spent today** tile shows what it actually costs you.

## Going live (real money)

Paper trade for at least a few weeks first and compare the result against just holding BTC. Most trading bots, AI or not, lose to buy-and-hold once fees are counted.

When you're ready:

1. Create an exchange API key with **trade** permission only. **Never give it withdrawal permission.**
2. Add these to `.env`:
   ```
   TRADER_MODE=live
   EXCHANGE_API_KEY=...
   EXCHANGE_API_SECRET=...
   LIVE_TRADING_CONFIRMED=I_UNDERSTAND_THE_RISKS
   ```
3. Start with a small balance on the exchange. The bot trades whatever quote-currency cash is in the account.

Live and paper keep separate logs (`ai_trader_data/live/` and `ai_trader_data/paper/`).

This is an experiment, not financial advice. Only trade money you can afford to lose.

## Tests

```
pytest tests/
```

The tests run offline against a fake exchange and a fake Claude. They cover the indicators, the paper broker, every risk rule, the parsing of Claude's replies and full trading cycles.
