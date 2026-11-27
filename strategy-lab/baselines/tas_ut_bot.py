"""UT Bot, restated from QuantNomad's "UT Bot Alerts", one of TradingView's most-used community scripts.

Source: "UT Bot Alerts" by QuantNomad, an open-source Pine Script indicator on TradingView, copied and adapted in
hundreds of community scripts. It keeps an ATR trailing stop under or over the price and prints Buy when the close
crosses above the stop and Sell when it crosses below. The board already runs TradingView's own built-in strategies;
this is the first of the community favourites. There is no published track record behind it: it is here because so
many people trade it.

The rule, as restated here with the script's defaults (key value 1, ATR period 10, chart candles, not Heikin Ashi):
  * nLoss = key value x ATR(10). The stop rises under an up-move (the higher of the last stop and close - nLoss), falls
    over a down-move (the lower of the last stop and close + nLoss), and flips to the other side when the close
    crosses it.
  * Buy when the close crosses above the stop. Sell when it crosses below.
What is different, and why: the script draws signals in both directions; the spot wallet cannot short, so a Sell
closes the long and the bot waits in cash (the leverage board runs the flip version, TASUtBotLS1h). Signals are read on
closed 1-hour candles, the same candles as the TradingView built-ins on the board. Any of the 20 coins, 5 slots.
Our own code, written from the script's logic. Not affiliated with or endorsed by QuantNomad or TradingView.

Spot only, long only, no leverage, no stop beyond the script's own. Indicators are written out in pandas and numpy so
the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
import numpy as np
from pandas import DataFrame, Series, concat

from freqtrade.strategy import IStrategy


def _atr(df: DataFrame, n: int) -> Series:
    """Average true range, Wilder's smoothing (Pine's atr())."""
    pc = df["close"].shift(1)
    tr = concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _ut_stop(close: Series, n_loss: Series) -> np.ndarray:
    """The UT Bot trailing stop, bar by bar, as the script computes it (the previous stop starts at 0)."""
    c = close.to_numpy("float64")
    loss = n_loss.to_numpy("float64")
    out = np.zeros(len(c))
    prev = 0.0
    for i in range(len(c)):
        pc = c[i - 1] if i else c[i]
        if c[i] > prev and pc > prev:
            s = max(prev, c[i] - loss[i])
        elif c[i] < prev and pc < prev:
            s = min(prev, c[i] + loss[i])
        elif c[i] > prev:
            s = c[i] - loss[i]
        else:
            s = c[i] + loss[i]
        out[i] = s
        prev = s
    return out


def _ut_signals(df: DataFrame, key: float, atr_period: int) -> DataFrame:
    stop = Series(_ut_stop(df["close"], key * _atr(df, atr_period)), index=df.index)
    c = df["close"]
    above = (c > stop) & (c.shift(1) <= stop.shift(1))   # EMA(1) of the close crosses over the stop
    below = (stop > c) & (stop.shift(1) <= c.shift(1))   # the stop crosses over EMA(1)
    df["ut_stop"] = stop
    df["ut_buy"] = (c > stop) & above
    df["ut_sell"] = (c < stop) & below
    return df


class TASUtBot1h(IStrategy):
    """UT Bot Alerts with its defaults (key 1, ATR 10) on 1-hour candles: buy on its Buy signal, sell on its Sell
    signal. Any of the 20 coins, spot, long only."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop beyond the script's own trailing line
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 100

    KEY = 1.0
    ATR_PERIOD = 10

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return _ut_signals(dataframe, self.KEY, self.ATR_PERIOD)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = (dataframe["ut_buy"] & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = dataframe["ut_sell"].astype(int)
        return dataframe
