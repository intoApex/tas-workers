from freqtrade.strategy import IStrategy
import talib.abstract as ta


class TASLeagueTestCrash(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    minimal_roi = {"0": 0.04}
    stoploss = -0.05
    startup_candle_count = 60

    def populate_indicators(self, df, metadata):
        raise ValueError("the model divided by zero on purpose")
        df["slow"] = ta.EMA(df, timeperiod=48)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["fast"] > df["slow"]) & (df["fast"].shift(1) <= df["slow"].shift(1)), "enter_long"] = 1
        df.loc[(df["fast"] < df["slow"]) & (df["fast"].shift(1) >= df["slow"].shift(1)), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        return df

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 2.0
