"""Bitcoin evening hours: hold Bitcoin from 21:00 to 23:00 UTC, restated from Quantpedia's seasonality study.

Source: Quantpedia, "The Seasonality of Bitcoin" (https://quantpedia.com/the-seasonality-of-bitcoin/), extending
Padysak and Vojtko's "Seasonality, Trend-following, and Mean reversion in Bitcoin". On hourly Gemini data from October
2015 to June 2023, buying Bitcoin at 21:00 UTC and selling at 23:00 UTC returned 40.64% a year with a worst drop of
-22.7%, against more than -70% for holding Bitcoin. Costs are not stated. The pattern was stronger on high-volatility
days: trading only when 30-day volatility is above its one-year median returned 37.26% a year, worst drop -18.87%.

Why it is on the leverage board: 40% a year over about 365 trades is roughly 0.09% per trade before costs. A spot round
trip costs about 0.2%, so the fee would decide the result before the clock did. Here a round trip costs about 0.1%,
which is the honest test: does the evening edge still pay for itself?

The rule, as restated here:
  TASBtcEvening1h         every day: long Bitcoin at the 21:00 UTC open, out at the 23:00 UTC open.
  TASBtcEveningHighVol1h  the same, only on days when Bitcoin's 30-day volatility is above its one-year median.
                          As in the study, volatility is measured on daily returns and the day's state is set at
                          00:00 UTC from the day before.
Whole wallet, no leverage (1x), long only, no stop and no profit target. Our own code, written from the study's
description. Not affiliated with or endorsed by Quantpedia.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from datetime import timedelta

import numpy as np
from pandas import DataFrame

from freqtrade.strategy import IStrategy

PAIR = "BTC/USDT:USDT"


class _Evening:
    """Shared rules. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop: the clock ends every trade
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 30
    HIGH_VOL_ONLY = False

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

    def informative_pairs(self):
        return [(PAIR, "1d")] if self.HIGH_VOL_ONLY else []

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        if not self.HIGH_VOL_ONLY:
            df["high_vol"] = True
            return df
        d = self.dp.get_pair_dataframe(PAIR, "1d")
        if not len(d):
            df["high_vol"] = False
            return df
        r = np.log(d["close"]).diff()
        vol30 = r.rolling(30).std(ddof=0)
        state = (vol30 > vol30.rolling(365, min_periods=180).median()).to_numpy()
        # a daily candle dated D closes at 00:00 on D+1: its state applies to the hours of D+1
        known = dict(zip(d["date"] + timedelta(days=1), state))
        df["high_vol"] = df["date"].dt.floor("D").map(known).fillna(False).astype(bool)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        hour = dataframe["date"].dt.hour
        # the 20:00 candle closes at 21:00; its signal fills at the 21:00 open
        ok = (hour == 20) & (dataframe["volume"] > 0) & dataframe["high_vol"].astype(bool) & (metadata["pair"] == PAIR)
        dataframe["enter_long"] = ok.astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # the 22:00 candle closes at 23:00; its signal fills at the 23:00 open
        dataframe["exit_long"] = (dataframe["date"].dt.hour == 22).astype(int)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return max_stake


class TASBtcEvening1h(_Evening, IStrategy):
    """Long Bitcoin every day from 21:00 to 23:00 UTC with the whole wallet, 1x."""


class TASBtcEveningHighVol1h(_Evening, IStrategy):
    """Long Bitcoin from 21:00 to 23:00 UTC, only when its 30-day volatility is above its one-year median. Whole
    wallet, 1x."""
    HIGH_VOL_ONLY = True
    startup_candle_count = 400  # Freqtrade loads this many candles of each timeframe: 400 days of daily ones for the median
