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

## Setup (Windows)

1. Get a Claude API key at https://platform.claude.com. You have to add credit there.
2. Double-click **`TRADER_SETUP.bat`**, paste your key and press Enter to use Kraken.
3. Double-click **`TRADE_ONCE.bat`** to run one cycle and see what it thinks.
4. Double-click **`TRADE_LOOP.bat`** to keep it trading every hour while the window is open.
5. Double-click **`TRADER_STATUS.bat`** to see the portfolio and the return since you started.

From a terminal it's `python -m ai_trader run | loop --minutes 60 | status | reset`.

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
