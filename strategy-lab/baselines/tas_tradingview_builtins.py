"""TradingView's built-in strategies, restated for the Strategy Lab.

TradingView ships a set of example strategies with its Strategy Tester: "MACD Strategy", "RSI Strategy", "Supertrend
Strategy" and so on. Anyone with a chart can click one on, so they are the first strategies many people ever test. This
file restates fifteen of those rules with TradingView's default settings, to see what they are worth on paper money.

It is our own code, written from the rules. Nothing here is copied from TradingView, and this is not affiliated with or
endorsed by TradingView. Each class says in words exactly what it does, so the record never depends on the name.

What is different from clicking the original onto a chart, and why:
  * Long only. The originals flip between long and short. The lab's spot wallet cannot short, so the signal that would
    open a short closes the long instead, and the bot waits in cash for the next long signal.
  * Signals are read on closed 1-hour candles and filled on the next candle. Several originals use stop orders that fill
    inside a candle, the moment the price touches a level. Here the candle has to close past that level first.
  * The lab's fees, 20 coins and $1,000 wallet, the same as every other bot. No stop loss and no profit target, as in
    the originals.

Left out: BarUpDn and Greedy (both need a gap between one candle's close and the next one's open, which a market that
never closes almost never has), Bollinger Bands Strategy Directed (long only, it is the Bollinger class below), and
Keltner Channels, Pivot Extension, Pivot Reversal and Technical Ratings (not restated yet).

Spot only, no leverage. Indicators are written out in pandas and numpy so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited (see tas_baselines.py). A corrected or added rule is a new class
in a new file.
"""
import numpy as np
from pandas import DataFrame, Series, concat

from freqtrade.strategy import IStrategy


def _sma(s: Series, n: int) -> Series:
    return s.rolling(n).mean()


def _ema(s: Series, n: int) -> Series:
    return s.ewm(span=n, adjust=False).mean()


def _rma(s: Series, n: int) -> Series:
    """Wilder's average, the one behind RSI and ATR."""
    return s.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(s: Series, n: int = 14) -> Series:
    d = s.diff()
    up = _rma(d.clip(lower=0), n)
    down = _rma(-d.clip(upper=0), n)
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


def _true_range(df: DataFrame) -> Series:
    pc = df["close"].shift(1)
    return concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def _as_series(x, like: Series) -> Series:
    return x if isinstance(x, Series) else Series(float(x), index=like.index)


def _cross_over(a: Series, b) -> Series:
    b = _as_series(b, a)
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_under(a: Series, b) -> Series:
    b = _as_series(b, a)
    return (a < b) & (a.shift(1) >= b.shift(1))


def _streak(cond: Series) -> Series:
    """How many candles in a row the condition has held, counting the current one."""
    c = cond.fillna(False).astype(int)
    return c.groupby((c == 0).cumsum()).cumsum()


def _supertrend_up(df: DataFrame, period: int, factor: float) -> Series:
    """True while Supertrend says uptrend (the line is under the price)."""
    hl2 = (df["high"] + df["low"]) / 2
    atr = _rma(_true_range(df), period)
    up_raw, lo_raw, close = (hl2 + factor * atr).to_numpy(), (hl2 - factor * atr).to_numpy(), df["close"].to_numpy()
    n = len(df)
    upper, lower, up = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=bool)
    started = False
    for i in range(n):
        if np.isnan(up_raw[i]) or np.isnan(lo_raw[i]):
            continue
        if not started:
            upper[i], lower[i], up[i], started = up_raw[i], lo_raw[i], close[i] >= hl2.iloc[i], True
            continue
        lower[i] = lo_raw[i] if (lo_raw[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
        upper[i] = up_raw[i] if (up_raw[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        up[i] = (close[i] >= lower[i]) if up[i - 1] else (close[i] > upper[i])
    return Series(up, index=df.index)


def _psar_up(df: DataFrame, start: float, step: float, maximum: float) -> Series:
    """True while Parabolic SAR sits under the price. The standard calculation, flipping when a candle touches the SAR."""
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    up = np.zeros(n, dtype=bool)
    if n < 3:
        return Series(up, index=df.index)
    uptrend = high[1] >= high[0]
    ep = high[1] if uptrend else low[1]
    sar = low[0] if uptrend else high[0]
    af = start
    up[1] = uptrend
    nxt = sar + af * (ep - sar)
    for i in range(2, n):
        sar = nxt
        flipped = False
        if uptrend and sar > low[i]:
            flipped, uptrend, sar, ep, af = True, False, max(ep, high[i]), low[i], start
        elif (not uptrend) and sar < high[i]:
            flipped, uptrend, sar, ep, af = True, True, min(ep, low[i]), high[i], start
        if not flipped:
            if uptrend and high[i] > ep:
                ep, af = high[i], min(af + step, maximum)
            elif (not uptrend) and low[i] < ep:
                ep, af = low[i], min(af + step, maximum)
        sar = min(sar, low[i - 1], low[i - 2]) if uptrend else max(sar, high[i - 1], high[i - 2])
        nxt = sar + af * (ep - sar)
        up[i] = uptrend
    return Series(up, index=df.index)


class _Tv:
    """Shared settings. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "1h"
    minimal_roi = {"0": 100.0}  # no profit target: the rule's own opposite signal is the exit
    stoploss = -0.99            # no stop, for the same reason
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 200

    def signals(self, df: DataFrame):
        """Return (long signal, short signal) as two true/false columns."""
        raise NotImplementedError

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long, short = self.signals(dataframe)
        dataframe["tv_long"] = long.fillna(False).astype(int)
        dataframe["tv_short"] = short.fillna(False).astype(int)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["tv_long"] == 1) & (dataframe["tv_short"] == 0) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["tv_short"] == 1).astype(int)
        return dataframe


class TvMacd1h(_Tv, IStrategy):
    """MACD Strategy (12, 26, 9). Buy when the MACD line crosses above its signal line, sell when it crosses back below."""

    def signals(self, df):
        macd = _ema(df["close"], 12) - _ema(df["close"], 26)
        delta = macd - _ema(macd, 9)
        return _cross_over(delta, 0), _cross_under(delta, 0)


class TvRsi1h(_Tv, IStrategy):
    """RSI Strategy (14, 30, 70). Buy when RSI climbs back above 30, sell when it falls back below 70."""

    def signals(self, df):
        rsi = _rsi(df["close"], 14)
        return _cross_over(rsi, 30), _cross_under(rsi, 70)


class TvBollinger1h(_Tv, IStrategy):
    """Bollinger Bands Strategy (20, 2). Buy when the close climbs back above the lower band, sell when it falls back below the upper band."""

    def signals(self, df):
        basis = _sma(df["close"], 20)
        dev = 2.0 * df["close"].rolling(20).std(ddof=0)
        return _cross_over(df["close"], basis - dev), _cross_under(df["close"], basis + dev)


class TvSupertrend1h(_Tv, IStrategy):
    """Supertrend Strategy (ATR 10, factor 3). Buy when Supertrend flips to uptrend, sell when it flips to downtrend."""

    def signals(self, df):
        up = _supertrend_up(df, 10, 3.0)
        was = up.shift(1, fill_value=False)
        return up & ~was, ~up & was


class TvMa2Cross1h(_Tv, IStrategy):
    """MovingAvg2Line Cross (9, 18). Buy when the 9-candle average crosses above the 18-candle average, sell when it crosses below."""

    def signals(self, df):
        fast, slow = _sma(df["close"], 9), _sma(df["close"], 18)
        return _cross_over(fast, slow), _cross_under(fast, slow)


class TvMaCross1h(_Tv, IStrategy):
    """MovingAvg Cross (9, confirm 1). Buy on the first close above the 9-candle average, sell on the first close below it."""

    def signals(self, df):
        ma = _sma(df["close"], 9)
        return _streak(df["close"] > ma) == 1, _streak(df["close"] < ma) == 1


class TvStochSlow1h(_Tv, IStrategy):
    """Stochastic Slow Strategy (14, 3, 3, 80/20). Buy when %K crosses above %D below 20, sell when %K crosses below %D above 80."""

    def signals(self, df):
        lo, hi = df["low"].rolling(14).min(), df["high"].rolling(14).max()
        k = _sma(100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan), 3)
        d = _sma(k, 3)
        return _cross_over(k, d) & (k < 20), _cross_under(k, d) & (k > 80)


class TvChannelBreakout1h(_Tv, IStrategy):
    """Channel BreakOut Strategy (5). Buy when the close breaks above the high of the last 5 candles, sell when it breaks below their low."""
    LENGTH = 5

    def signals(self, df):
        top = df["high"].rolling(self.LENGTH).max().shift(1)
        bottom = df["low"].rolling(self.LENGTH).min().shift(1)
        return df["close"] > top, df["close"] < bottom


class TvPriceChannel1h(TvChannelBreakout1h):
    """Price Channel Strategy (20). Buy when the close breaks above the high of the last 20 candles, sell when it breaks below their low."""
    LENGTH = 20


class TvMomentum1h(_Tv, IStrategy):
    """Momentum Strategy (12). Buy when the price is above where it was 12 candles ago, that gap is growing, and the
    next candle closes above this one's high. Sell on the mirror image."""

    def signals(self, df):
        mom0 = df["close"] - df["close"].shift(12)
        mom1 = mom0 - mom0.shift(1)
        up, down = (mom0 > 0) & (mom1 > 0), (mom0 < 0) & (mom1 < 0)
        return up.shift(1, fill_value=False) & (df["close"] > df["high"].shift(1)), down.shift(1, fill_value=False) & (df["close"] < df["low"].shift(1))


class TvConsecutive1h(_Tv, IStrategy):
    """Consecutive Up/Down Strategy (3, 3). Buy after three higher closes in a row, sell after three lower closes in a row."""

    def signals(self, df):
        return _streak(df["close"] > df["close"].shift(1)) >= 3, _streak(df["close"] < df["close"].shift(1)) >= 3


class TvParabolicSar1h(_Tv, IStrategy):
    """Parabolic SAR Strategy (0.02, 0.02, 0.2). Buy when the SAR dots flip under the price, sell when they flip above it."""

    def signals(self, df):
        up = _psar_up(df, 0.02, 0.02, 0.2)
        was = up.shift(1, fill_value=False)
        return up & ~was, ~up & was


class TvInsideBar1h(_Tv, IStrategy):
    """InSide Bar Strategy. When a candle sits entirely inside the one before it: buy if it closed up, sell if it closed down."""

    def signals(self, df):
        inside = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
        return inside & (df["close"] > df["open"]), inside & (df["close"] < df["open"])


class TvOutsideBar1h(_Tv, IStrategy):
    """OutSide Bar Strategy. When a candle reaches both above and below the one before it: buy if it closed up, sell if it closed down."""

    def signals(self, df):
        outside = (df["high"] > df["high"].shift(1)) & (df["low"] < df["low"].shift(1))
        return outside & (df["close"] > df["open"]), outside & (df["close"] < df["open"])


class TvVoltyExpan1h(_Tv, IStrategy):
    """Volty Expan Close Strategy (5, 0.75). Buy when a candle closes more than 0.75 of the recent average range above
    the previous close, sell when it closes that far below it."""

    def signals(self, df):
        reach = _sma(_true_range(df), 5) * 0.75
        prev_close, prev_reach = df["close"].shift(1), reach.shift(1)
        return df["close"] > prev_close + prev_reach, df["close"] < prev_close - prev_reach
