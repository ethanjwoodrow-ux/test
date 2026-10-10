"""Live market data from any ccxt-supported exchange (public endpoints, no API key needed)."""

import ccxt

from ai_trader import indicators


def make_exchange(exchange_id: str, api_key: str = "", secret: str = "", password: str = ""):
    if not hasattr(ccxt, exchange_id):
        raise ValueError(f"Unknown exchange {exchange_id!r}. See https://github.com/ccxt/ccxt#supported-cryptocurrency-exchanges")
    params = {"enableRateLimit": True}
    if api_key:
        params.update(apiKey=api_key, secret=secret)
    if password:
        params["password"] = password
    return getattr(ccxt, exchange_id)(params)


class MarketData:
    def __init__(self, exchange):
        self.exchange = exchange

    def ticker(self, symbol: str) -> dict:
        t = self.exchange.fetch_ticker(symbol)
        last = t.get("last") or t.get("close")
        return {"price": last, "bid": t.get("bid") or last, "ask": t.get("ask") or last,
                "change_24h_pct": t.get("percentage"), "volume_24h_quote": t.get("quoteVolume")}

    def snapshot(self, symbol: str) -> dict:
        """Everything the AI sees about one coin: price, 24h stats and hourly/daily indicators."""
        hourly = self.exchange.fetch_ohlcv(symbol, "1h", limit=200)
        daily = self.exchange.fetch_ohlcv(symbol, "1d", limit=90)
        return {
            "symbol": symbol,
            **self.ticker(symbol),
            "hourly": indicators.summarize(hourly),
            "daily": indicators.summarize(daily),
            "last_24_hourly_closes": [c[4] for c in hourly[-24:]],
        }
