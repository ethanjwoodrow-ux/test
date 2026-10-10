"""Three rule-based strategies on daily candles. No AI involved: the same candles always give the same signal.

Each strategy turns a coin's daily history into a signal per day: how much of that coin's slot to hold
(0 = none, 1 = full slot) and when the current position started. Positions are sized with volatility
targeting, so a wild coin gets a smaller position than a calm one.

Background (see AI_TRADER.md for sources):
- Trend following / time-series momentum has the strongest evidence in crypto of the simple rules,
  though much of the edge goes once trading costs are counted.
- The breakout ensemble follows Zarattini, Pagani & Barbon, "Catching Crypto Trends" (2025): Donchian
  channels over several lookbacks, a trailing mid-channel stop and volatility-based sizing.
- Dip buying in an uptrend (Connors-style RSI-2) has weaker evidence on daily crypto data; it is here
  because it trades when the trend strategies are waiting, which smooths the combined result.
"""

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

TARGET_VOL = 0.40          # aim each coin's position at ~40% annualised volatility
VOL_WINDOW = 30            # days of returns used to measure volatility
DAILY_HISTORY = 400        # daily candles the strategies like to see (longest breakout lookback + buffer)


@dataclass
class Signal:
    weight: float                     # 0..1 share of the coin's slot
    since: str | None                 # date (YYYY-MM-DD) the current position started; None when flat
    reason: str
    detail: dict = field(default_factory=dict)


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).date().isoformat()


# ---------- indicator series (one value per candle, None until there's enough data) ----------

def sma_series(values: list[float], n: int) -> list[float | None]:
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def rolling_extreme(values: list[float], n: int, highest: bool) -> list[float | None]:
    """Highest (or lowest) of the last n values including today, via a monotonic queue."""
    out, q = [], deque()
    for i, v in enumerate(values):
        while q and ((values[q[-1]] <= v) if highest else (values[q[-1]] >= v)):
            q.pop()
        q.append(i)
        if q[0] <= i - n:
            q.popleft()
        out.append(values[q[0]] if i >= n - 1 else None)
    return out


def rsi_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(b - a, 0.0) for a, b in zip(values, values[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(values, values[1:])]
    avg_g, avg_l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(values)):
        if i > n:
            avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
            avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def vol_series(values: list[float], n: int = VOL_WINDOW) -> list[float | None]:
    """Annualised volatility of daily log returns over the last n days."""
    rets = [math.log(b / a) for a, b in zip(values, values[1:]) if a > 0 and b > 0]
    out: list[float | None] = [None]
    for i in range(1, len(values)):
        window = rets[max(0, i - n):i]
        out.append(statistics.stdev(window) * math.sqrt(365) if len(window) >= max(10, n // 2) else None)
    return out


def vol_scale(vol: float | None) -> float:
    """Position size from volatility: full size at or below TARGET_VOL, smaller above it."""
    if not vol:
        return 0.5
    return max(0.1, min(1.0, TARGET_VOL / vol))


# ---------- strategies ----------

class Strategy:
    key = ""
    name = ""
    rules = ""

    def series(self, candles: list[list[float]]) -> list[Signal]:
        raise NotImplementedError


class TrendFollowing(Strategy):
    key = "trend"
    name = "Trend following"
    rules = ("Buy when the daily close is above its 50-day average AND the coin is up over the last 30 days. "
             "Sell when it closes below the 50-day average.")

    def series(self, candles):
        closes = [c[4] for c in candles]
        sma50, vols = sma_series(closes, 50), vol_series(closes)
        out, since = [], None
        for i, close in enumerate(closes):
            mom = (close / closes[i - 30] - 1) * 100 if i >= 30 else None
            if sma50[i] is None or mom is None:
                out.append(Signal(0.0, None, "not enough history yet"))
                continue
            gap = (close / sma50[i] - 1) * 100
            if since is None and close > sma50[i] and mom > 0:
                since = _day(candles[i][0])
            elif since is not None and close < sma50[i]:
                since = None
            detail = {"vs_sma50_pct": round(gap, 1), "ret_30d_pct": round(mom, 1),
                      "vol_ann_pct": round(vols[i] * 100) if vols[i] else None}
            if since:
                out.append(Signal(vol_scale(vols[i]), since, f"uptrend: {gap:+.1f}% vs 50-day avg, {mom:+.1f}% over 30 days", detail))
            else:
                out.append(Signal(0.0, None, f"no trend: {gap:+.1f}% vs 50-day avg, {mom:+.1f}% over 30 days", detail))
        return out


class BreakoutEnsemble(Strategy):
    key = "breakout"
    name = "Breakout ensemble"
    rules = ("Seven Donchian channels (20 to 360 days). Each one buys when the close makes a new N-day high and "
             "sells when it falls below the channel's ratcheting mid-line. Position = share of channels that are in.")
    LOOKBACKS = (20, 30, 60, 90, 150, 250, 360)  # the paper also uses 5 and 10; dropped here to cut trading fees

    def series(self, candles):
        closes = [c[4] for c in candles]
        vols = vol_series(closes)
        active = [n for n in self.LOOKBACKS if len(closes) > n]
        highs = {n: rolling_extreme(closes, n, True) for n in active}
        lows = {n: rolling_extreme(closes, n, False) for n in active}
        stops: dict[int, float | None] = {n: None for n in active}
        out, since = [], None
        for i, close in enumerate(closes):
            usable = [n for n in active if highs[n][i] is not None]
            if not usable:
                out.append(Signal(0.0, None, "not enough history yet"))
                continue
            for n in usable:
                mid = (highs[n][i] + lows[n][i]) / 2
                if stops[n] is None and close >= highs[n][i]:
                    stops[n] = mid
                elif stops[n] is not None:
                    stops[n] = max(stops[n], mid)       # ratchet up only
                    if close < stops[n]:
                        stops[n] = None
            in_count = sum(1 for n in usable if stops[n] is not None)
            share = in_count / len(usable)
            if share > 0 and since is None:
                since = _day(candles[i][0])
            elif share == 0:
                since = None
            detail = {"channels_in": f"{in_count}/{len(usable)}", "vol_ann_pct": round(vols[i] * 100) if vols[i] else None}
            reason = (f"{in_count} of {len(usable)} breakout channels long" if in_count
                      else f"no breakouts (0 of {len(usable)} channels long)")
            out.append(Signal(share * vol_scale(vols[i]) if share else 0.0, since, reason, detail))
        return out


class DipBuyer(Strategy):
    key = "dip"
    name = "Dip buyer"
    rules = ("Only in an uptrend (close above the 200-day average, or 100-day if history is short). Buy when 2-day "
             "RSI drops below 10. Sell when the close rises above the 5-day average, after 10 days, or if the uptrend breaks.")
    MAX_HOLD_DAYS = 10

    def series(self, candles):
        closes = [c[4] for c in candles]
        sma200, sma100 = sma_series(closes, 200), sma_series(closes, 100)
        sma5, rsi2, vols = sma_series(closes, 5), rsi_series(closes, 2), vol_series(closes)
        out, entry_i = [], None
        for i, close in enumerate(closes):
            # Decided per day from that day's data only, so live and backtest signals always match.
            long_n, trend = (200, sma200[i]) if sma200[i] is not None else (100, sma100[i])
            if trend is None or rsi2[i] is None or sma5[i] is None:
                out.append(Signal(0.0, None, "not enough history yet"))
                continue
            uptrend = close > trend
            if entry_i is None and uptrend and rsi2[i] < 10:
                entry_i = i
            elif entry_i is not None and i > entry_i and (close > sma5[i] or not uptrend or i - entry_i >= self.MAX_HOLD_DAYS):
                entry_i = None
            detail = {"rsi2": round(rsi2[i], 1), f"vs_sma{long_n}_pct": round((close / trend - 1) * 100, 1),
                      "vol_ann_pct": round(vols[i] * 100) if vols[i] else None}
            if entry_i is not None:
                out.append(Signal(vol_scale(vols[i]), _day(candles[entry_i][0]),
                                  f"bought the dip (RSI-2 was {rsi2[entry_i]:.0f}), day {i - entry_i + 1} of max {self.MAX_HOLD_DAYS}", detail))
            elif not uptrend:
                out.append(Signal(0.0, None, f"no uptrend: below the {long_n}-day average", detail))
            else:
                out.append(Signal(0.0, None, f"uptrend, waiting for a dip (RSI-2 {rsi2[i]:.0f}, needs < 10)", detail))
        return out


STRATEGIES: dict[str, Strategy] = {s.key: s for s in (TrendFollowing(), BreakoutEnsemble(), DipBuyer())}
MODES = {
    "trend": ("Trend following", ["trend"]),
    "breakout": ("Breakout ensemble", ["breakout"]),
    "dip": ("Dip buyer", ["dip"]),
    "all": ("All three combined", ["trend", "breakout", "dip"]),
}


def strategies_for(mode: str) -> list[Strategy]:
    return [STRATEGIES[k] for k in MODES[mode][1]]


def signal_series(mode: str, daily: dict[str, list]) -> dict[str, dict[str, list[Signal]]]:
    """Signals for every day of history: {symbol: {strategy_key: [Signal per candle]}}."""
    return {sym: {s.key: s.series(candles) for s in strategies_for(mode)} for sym, candles in daily.items() if candles}


def latest(series: dict[str, dict[str, list[Signal]]], index: int = -1) -> dict[str, dict[str, Signal]]:
    return {sym: {k: sigs[index] for k, sigs in by_strategy.items()} for sym, by_strategy in series.items()}
