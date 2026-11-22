import numpy as np
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class TASLeaguePreGPT(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 400
    max_open_trades = 5
    position_adjustment_enable = False

    # Let profitable trends run rather than imposing a fixed ROI target.
    minimal_roi = {}
    use_exit_signal = True
    exit_profit_only = False

    # Freqtrade expresses these thresholds as leveraged trade returns.
    stoploss = -0.072
    trailing_stop = True
    trailing_stop_positive = 0.034
    trailing_stop_positive_offset = 0.055
    trailing_only_offset_is_reached = True

    def leverage(
        self, pair, current_time, current_rate, proposed_leverage,
        max_leverage, entry_tag, side, **kwargs
    ) -> float:
        return min(1.5, max_leverage)

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake,
        min_stake, max_stake, leverage, entry_tag, side, **kwargs
    ) -> float:
        stake = min(0.8 * proposed_stake, max_stake)
        return stake if min_stake is None or stake >= min_stake else 0.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        d = dataframe
        d["fast"] = ta.EMA(d, timeperiod=16)
        d["mid"] = ta.EMA(d, timeperiod=40)
        d["slow"] = ta.EMA(d, timeperiod=96)
        d["atr"] = ta.ATR(d, timeperiod=14)
        d["adx"] = ta.ADX(d, timeperiod=14)
        d["rsi"] = ta.RSI(d, timeperiod=14)

        d["prior_high"] = d["high"].shift(1).rolling(20).max()
        d["prior_low"] = d["low"].shift(1).rolling(20).min()
        d["volume_ref"] = d["volume"].shift(1).rolling(24).median()
        span = (d["high"] - d["low"]).replace(0.0, np.nan)
        d["close_location"] = (d["close"] - d["low"]) / span
        return d

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        d = dataframe
        c = d["close"]
        fast, mid, slow = d["fast"], d["mid"], d["slow"]
        atr = d["atr"]

        liquid = (
            (d["volume"] > 0)
            & (d["volume_ref"] > 0)
            & (d["volume"] >= 0.7 * d["volume_ref"])
            & (atr / c).between(0.002, 0.045)
            & ((d["high"] - d["low"]) < 3.2 * atr.shift(1))
        )
        up = liquid & (fast > mid) & (mid > slow) & (slow > slow.shift(8))
        down = liquid & (fast < mid) & (mid < slow) & (slow < slow.shift(8))

        impulse = (d["volume"] > 1.15 * d["volume_ref"]) & (d["adx"] > 17)
        long_break = (
            up & impulse & (c > d["prior_high"])
            & (d["close_location"] >= 0.70) & (c < fast + 2.8 * atr)
        )
        short_break = (
            down & impulse & (c < d["prior_low"])
            & (d["close_location"] <= 0.30) & (c > fast - 2.8 * atr)
        )

        long_resume = (
            up & (d["adx"] > 19)
            & (c.shift(1) <= fast.shift(1)) & (c.shift(1) >= mid.shift(1))
            & (c > fast) & (c > d["high"].shift(1))
            & d["rsi"].between(50, 68) & (d["close_location"] >= 0.60)
        )
        short_resume = (
            down & (d["adx"] > 19)
            & (c.shift(1) >= fast.shift(1)) & (c.shift(1) <= mid.shift(1))
            & (c < fast) & (c < d["low"].shift(1))
            & d["rsi"].between(32, 50) & (d["close_location"] <= 0.40)
        )

        d["enter_long"] = 0
        d["enter_short"] = 0
        d["enter_tag"] = ""
        d.loc[long_break, ["enter_long", "enter_tag"]] = (1, "range_up")
        d.loc[short_break, ["enter_short", "enter_tag"]] = (1, "range_down")
        d.loc[long_resume, ["enter_long", "enter_tag"]] = (1, "resume_up")
        d.loc[short_resume, ["enter_short", "enter_tag"]] = (1, "resume_down")
        return d

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        d = dataframe
        c, mid = d["close"], d["mid"]
        d["exit_long"] = (
            (d["volume"] > 0)
            & ((d["fast"] < mid) | ((c < mid - 0.35 * d["atr"]) & (c.shift(1) < mid.shift(1))))
        ).astype(int)
        d["exit_short"] = (
            (d["volume"] > 0)
            & ((d["fast"] > mid) | ((c > mid + 0.35 * d["atr"]) & (c.shift(1) > mid.shift(1))))
        ).astype(int)
        return d
