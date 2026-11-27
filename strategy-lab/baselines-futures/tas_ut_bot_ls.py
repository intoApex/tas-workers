"""UT Bot, long and short: the leverage-board twin of the spot lab's TASUtBot1h.

Source: "UT Bot Alerts" by QuantNomad, an open-source Pine Script indicator on TradingView and one of its most-used
community scripts. It keeps an ATR trailing stop and prints Buy when the close crosses above it and Sell when the close
crosses below. Traded as published, each signal flips the position: Buy closes a short and opens a long, Sell closes a
long and opens a short. There is no published track record behind it: it is here because so many people trade it.

The rule, as restated here with the script's defaults (key value 1, ATR period 10, chart candles, not Heikin Ashi):
  * nLoss = key value x ATR(10). The stop rises under an up-move (the higher of the last stop and close - nLoss), falls
    over a down-move (the lower of the last stop and close + nLoss), and flips when the close crosses it.
  * Buy: close a short, open a long. Sell: close a long, open a short.
Closed 1-hour candles, no leverage (1x), no stop beyond the script's own. Because a flip bot is always in a position,
it would keep whichever 5 coins signalled first for good; so it trades the five largest perpetuals (BTC, ETH, SOL, BNB,
XRP), one slot each, like the board's other always-in bots.
Our own code, written from the script's logic. Not affiliated with or endorsed by QuantNomad or TradingView.

Indicators are written out in pandas and numpy so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
import numpy as np
from pandas import DataFrame, Series, concat

from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT")


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


class TASUtBotLS1h(IStrategy):
    """UT Bot Alerts with its defaults (key 1, ATR 10) on 1-hour candles, flipping long on Buy and short on Sell.
    BTC, ETH, SOL, BNB and XRP perpetuals, 1x."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop beyond the script's own trailing line; at 1x a position cannot be liquidated before it doubles against us
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 100

    KEY = 1.0
    ATR_PERIOD = 10

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        stop = Series(_ut_stop(df["close"], self.KEY * _atr(df, self.ATR_PERIOD)), index=df.index)
        c = df["close"]
        above = (c > stop) & (c.shift(1) <= stop.shift(1))
        below = (stop > c) & (stop.shift(1) <= c.shift(1))
        df["ut_stop"] = stop
        df["ut_buy"] = (c > stop) & above
        df["ut_sell"] = (c < stop) & below
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ok = (dataframe["volume"] > 0) & (metadata["pair"] in TOP5)
        dataframe["enter_long"] = (dataframe["ut_buy"] & ok).astype(int)
        dataframe["enter_short"] = (dataframe["ut_sell"] & ok).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = dataframe["ut_sell"].astype(int)
        dataframe["exit_short"] = dataframe["ut_buy"].astype(int)
        return dataframe
