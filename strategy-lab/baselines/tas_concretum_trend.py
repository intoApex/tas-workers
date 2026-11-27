"""Crypto trend ensemble, restated from Zarattini, Pagani and Barbon, "Catching Crypto Trends".

Source: Carlo Zarattini, Alberto Pagani and Andrea Barbon, "Catching Crypto Trends; A Tactical Approach for Bitcoin and
Altcoins", Swiss Finance Institute Research Paper 25-80 (SSRN 5209907, 2025). Concretum Group publishes it at
https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/
On survivorship-free data from 2015 to March 2025, their altcoin version made 18% a year after 0.10% costs.

Their rules, as restated here, for each coin:
  * Nine trend models on daily closes, with lookbacks of 5, 10, 20, 30, 60, 90, 150, 250 and 360 days.
  * A model goes long when the close breaks above the highest close of its lookback (the days before today).
  * It exits on a trailing stop that only rises: each day the higher of yesterday's stop and the midpoint between the
    highest and lowest close of its lookback. A close under the stop ends the trade.
  * The coin's position is the share of the nine models that are long, scaled to 25% yearly volatility (25% divided by
    the yearly standard deviation of the last 90 daily returns).

What is different here, and why:
  * Five coins, not the whole market: the lab's wallet has five slots, so this runs on the five largest of its 20 coins
    (BTC, ETH, SOL, BNB, XRP), one slot each.
  * No leverage: the paper lets a position reach 2x through the volatility scaling. On spot, a coin's position stops at
    its full slot.
  * Resizing: the paper re-forms the portfolio every day. Here a position is resized only when it is more than a fifth
    away from its target, to keep fees down. The paper notes costs matter and proposes its own way to cut them; this is
    ours.
Our own code, written from the paper's description. Not affiliated with or endorsed by the authors.

Spot only. Indicators are written out in pandas and numpy so the file needs nothing but Freqtrade. Signals use closed
daily candles and fill at the next day's open.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
import numpy as np
from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)


def _donchian(close: Series, n: int) -> np.ndarray:
    """1.0 on the days the n-day model is long, else 0.0. The stop only ratchets up, so this walks the days in order."""
    c = close.to_numpy("float64")
    high_before = close.shift(1).rolling(n).max().to_numpy("float64")  # highest close of the n days before today
    mid = ((close.rolling(n).max() + close.rolling(n).min()) / 2).to_numpy("float64")
    out = np.zeros(len(c))
    long, stop = False, -np.inf
    for i in range(len(c)):
        if long:
            if c[i] < stop:
                long = False
            elif mid[i] == mid[i]:
                stop = max(stop, mid[i])
        if not long and high_before[i] == high_before[i] and c[i] > high_before[i]:
            long, stop = True, mid[i]
        out[i] = 1.0 if long else 0.0
    return out


class TASTrendEnsemble1d(IStrategy):
    """Nine breakout models (5 to 360 days) per coin, each with a rising midpoint stop; the coin's position is the share
    of models long, scaled to 25% yearly volatility. BTC, ETH, SOL, BNB and XRP, spot, one slot each."""
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no fixed stop: each model has its own trailing stop, and the trade ends when all are out
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    startup_candle_count = 400

    VOL_TARGET = 0.25
    VOL_DAYS = 90
    BAND = 0.2  # resize only when the position is more than this share away from its target

    def bot_start(self, **kwargs) -> None:
        self._resized = {}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        if metadata["pair"] not in TOP5:
            df["target"] = 0.0
            return df
        models = np.vstack([_donchian(df["close"], n) for n in LOOKBACKS])
        df["models_long"] = models.sum(axis=0)
        vol = df["close"].pct_change().rolling(self.VOL_DAYS).std(ddof=0) * np.sqrt(365)
        scale = (self.VOL_TARGET / vol).clip(upper=1.0)
        df["target"] = (df["models_long"] / len(LOOKBACKS) * scale).fillna(0.0)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["target"] > 0) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["target"] <= 0).astype(int)  # every model has stopped out
        return dataframe

    def _target_stake(self, pair: str) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if not len(df):
            return 0.0
        slot = self.wallets.get_total_stake_amount() / len(TOP5)  # one slot per coin
        return slot * float(df["target"].iloc[-1])

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self._target_stake(pair), max_stake)

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake,
                              current_entry_rate, current_exit_rate, current_entry_profit, current_exit_profit, **kwargs):
        day = current_time.date()
        if self._resized.get(trade.id) == day:  # at most once per daily candle
            return None
        self._resized[trade.id] = day
        target = self._target_stake(trade.pair)
        value = trade.amount * current_rate
        if target <= 0 or abs(target - value) <= self.BAND * max(target, value):
            return None
        floor = min_stake or 0.0
        if target > value:
            add = min(target - value, max_stake)
            return add if add >= floor else None
        cut = value - target
        if cut < floor or value - cut < floor:
            return None
        return -cut
