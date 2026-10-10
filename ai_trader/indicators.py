"""Technical indicators computed from plain lists of prices (no pandas needed)."""

import statistics


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. 70+ is usually read as overbought, 30- as oversold."""
    if len(values) <= period:
        return None
    gains, losses = [], []
    for prev, cur in zip(values, values[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(values) < slow + signal:
        return None
    line = [f - s for f, s in zip(ema_series(values, fast), ema_series(values, slow))]
    sig = ema_series(line, signal)
    return {"macd": line[-1], "signal": sig[-1], "histogram": line[-1] - sig[-1]}


def pct_change(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return (values[-1] / values[-periods - 1] - 1) * 100


def volatility_pct(values: list[float], period: int = 24) -> float | None:
    """Standard deviation of per-candle % returns over the last `period` candles."""
    if len(values) <= period:
        return None
    window = values[-period - 1:]
    returns = [(b / a - 1) * 100 for a, b in zip(window, window[1:]) if a]
    return statistics.stdev(returns) if len(returns) > 1 else None


def _round(x, places=4):
    return None if x is None else round(x, places)


def summarize(ohlcv: list[list[float]]) -> dict:
    """Condense [timestamp, open, high, low, close, volume] candles into indicator readings."""
    closes = [c[4] for c in ohlcv]
    volumes = [c[5] for c in ohlcv]
    m = macd(closes)
    avg_vol = sma(volumes[:-1], 20) if len(volumes) > 20 else None
    return {
        "candles": len(closes),
        "close": closes[-1] if closes else None,
        "sma_20": _round(sma(closes, 20)),
        "sma_50": _round(sma(closes, 50)),
        "ema_12": _round(ema_series(closes, 12)[-1]) if closes else None,
        "rsi_14": _round(rsi(closes), 2),
        "macd": {k: _round(v) for k, v in m.items()} if m else None,
        "change_1_pct": _round(pct_change(closes, 1), 2),
        "change_6_pct": _round(pct_change(closes, 6), 2),
        "change_24_pct": _round(pct_change(closes, 24), 2),
        "volatility_pct": _round(volatility_pct(closes), 3),
        "volume_vs_20avg": _round(volumes[-1] / avg_vol, 2) if avg_vol else None,
        "high_of_period": max(c[2] for c in ohlcv) if ohlcv else None,
        "low_of_period": min(c[3] for c in ohlcv) if ohlcv else None,
    }
