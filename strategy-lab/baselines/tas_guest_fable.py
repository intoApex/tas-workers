"""A guest in the Strategy Lab: one strategy written by an AI model, to see what a model's first attempt is worth.

Written by Claude Fable 5.2 on 2026-11-11 in one pass. It was not backtested, tuned or revised before it was added:
the model wrote the rules below from general knowledge and the lab took the file as it came. It gets the same $1,000 of
paper money, the same 20 coins and the same fees as every other bot, and its record starts the day it was added.

Spot only, long only, no leverage. Indicators are written out in pandas so the file needs nothing but Freqtrade.
Signals only look at closed candles; Freqtrade fills them on the next candle.

FROZEN: once this file is deployed it is never edited (see tas_baselines.py). A second attempt is a new class in a new file.
"""
from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy


def _ema(s: Series, n: int) -> Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: Series, n: int = 14) -> Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


class TASFableOnePass1h(IStrategy):
    """Buy a pullback inside an uptrend, sell when the bounce is spent or the trend breaks.

    Uptrend: the close is above the 200-hour average and the 50-hour average is above the 200-hour one.
    Pullback: the hourly RSI has been at or below 40 in the last six hours and has just climbed back above it,
    with the price no more than 3% above the 50-hour average. Sell when the RSI passes 70 or the close falls
    below the 200-hour average. A fixed stop closes any trade that is 5% under its entry.
    """
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "1h"
    startup_candle_count = 220
    minimal_roi = {"0": 100.0}  # no profit target: the exit rule decides
    stoploss = -0.05
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = _ema(dataframe["close"], 50)
        dataframe["ema200"] = _ema(dataframe["close"], 200)
        dataframe["rsi"] = _rsi(dataframe["close"], 14)
        dataframe["rsi_low6"] = dataframe["rsi"].shift(1).rolling(6).min()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        uptrend = (dataframe["close"] > dataframe["ema200"]) & (dataframe["ema50"] > dataframe["ema200"])
        bounce = (dataframe["rsi_low6"] <= 40) & (dataframe["rsi"] > 40) & (dataframe["rsi"].shift(1) <= 40)
        near = dataframe["close"] < dataframe["ema50"] * 1.03
        dataframe["enter_long"] = (uptrend & bounce & near & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = ((dataframe["rsi"] > 70) | (dataframe["close"] < dataframe["ema200"])).astype(int)
        return dataframe
