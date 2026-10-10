# AI Crypto Trader

Claude reads live crypto market data and decides to buy, sell, or hold each coin. A separate layer of hard-coded risk rules decides what actually gets traded. It starts in **paper mode**: fake money at real prices, so you can see how it performs before risking anything.

## How it works

Every cycle (once an hour by default):

1. **Market data.** It pulls price, 24h stats and hourly and daily candles for each coin from the exchange, and computes RSI, MACD, moving averages, momentum, volatility and volume.
2. **Stop-losses.** Any coin that has dropped `RISK_STOP_LOSS_PCT` below its entry price is sold automatically, before the AI is asked anything.
3. **AI decision.** Claude (`claude-opus-5-5`) gets the market data, your portfolio, the risk limits and its own last 12 decisions. It returns buy/sell/hold for each coin, with a size, a confidence score and a reason.
4. **Risk rules.** The AI can't get around these:
   - Buys are capped at 10% of equity per trade and 25% per coin, and 20% of equity always stays in cash.
   - Calls with confidence below 0.6 are ignored.
   - After a 5% loss in a day, no new buys are made until tomorrow. Sells still go through.
   - It trades spot only, with no leverage and no shorting.
5. **Execute and log.** Orders go to the paper broker or the real exchange. Every cycle is written to `cycles.jsonl` and every fill to `trades.csv`.

If anything goes wrong (the exchange is down, the API errors, or the reply can't be parsed), it holds everything for that cycle.

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
- **Settings:** change the exchange, coins, how hard Claude thinks, or reset the paper account.

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
| `RISK_*` | see above | The hard limits |

**API cost:** I estimate about $0.05–0.10 per cycle at `high` effort, which comes to roughly $1–2 a day if it runs hourly. I haven't measured this on a real run yet. Run it every 4 hours (`loop --minutes 240`) or set `TRADER_EFFORT=medium` to cut the cost.

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
