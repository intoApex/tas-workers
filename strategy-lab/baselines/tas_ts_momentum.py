"""Market momentum switch: time-series momentum on the crypto market, restated from Han, Kang and Ryu.

Sources: Yukun Liu and Aleh Tsyvinski, "Risks and Returns of Cryptocurrency" (Review of Financial Studies, 2021), found
strong time-series momentum in crypto. Chulwoo Han, Byeongguk Kang and Jehyeon Ryu, "Time-Series and Cross-Sectional
Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions", retested it with 0.15%
costs and daily mark-to-market from 2014 to 2023. Their best setting: a 28-day look-back and a 5-day hold, long when
the market's look-back return is in the top third, cash otherwise. Net Sharpe 1.51 against 0.85 for holding the
market. Long-only versions beat the market almost everywhere; short and long-short versions generally lost.

The rule, as restated here:
  * "The market" is an equal-weighted index of the five largest of the lab's 20 coins (BTC, ETH, SOL, BNB, XRP). The
    paper used a value-weighted index of every coin; the lab's wallet has five slots.
  * Each day, the signal is on when the index's 28-day return is in the top third of its 28-day returns over the past
    year. The paper splits into thirds too; the past-year window for the split is ours, since the paper's exact window
    for the thirds is not restated here.
  * The paper holds each day's decision for 5 days, with starting days staggered. So the position is the share of the
    last 5 daily signals that were on: one fifth per signal, in all five coins at once. All five off: out.
Our own code, written from the paper's description. Not affiliated with or endorsed by the authors.

Spot only, long only, no leverage. Indicators are written out in pandas so the file needs nothing but Freqtrade.
Signals use closed daily candles and fill at the next day's open.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, concat

from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")


class TASMarketMomentum1d(IStrategy):
    """In the five largest coins while the 5-coin index's 28-day return is in the top third of its past year, sized by
    how many of the last 5 daily signals were on. Spot, long only."""
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop: the paper's rule has none
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    position_adjustment_enable = True
    max_entry_position_adjustment = -1
    startup_candle_count = 400

    LOOKBACK = 28
    HOLD = 5
    RANK_DAYS = 365
    BAND = 0.1  # resize when the position is more than this share of its slot away from target

    def bot_start(self, **kwargs) -> None:
        self._resized = {}

    def informative_pairs(self):
        return [(p, self.timeframe) for p in TOP5]

    def _signal(self) -> DataFrame:
        """Date-indexed: the index, its 28-day return, the top-third line and the held share. Same for every coin."""
        rets = []
        for p in TOP5:
            d = self.dp.get_pair_dataframe(p, self.timeframe)
            if len(d):
                rets.append(d.set_index("date")["close"].pct_change().rename(p))
        r = concat(rets, axis=1).sort_index()
        index = (1 + r.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
        out = DataFrame({"index": index})
        out["mom"] = index / index.shift(self.LOOKBACK) - 1
        out["top_third"] = out["mom"].rolling(self.RANK_DAYS, min_periods=180).quantile(2 / 3)
        out["on"] = (out["mom"] >= out["top_third"]).astype(float)
        out["held"] = out["on"].rolling(self.HOLD, min_periods=1).sum() / self.HOLD
        return out

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        if metadata["pair"] not in TOP5:
            df["target"] = 0.0
            return df
        sig = self._signal()
        df["target"] = df["date"].map(sig["held"]).fillna(0.0)
        df["index_mom"] = df["date"].map(sig["mom"])
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["target"] > 0) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["target"] <= 0).astype(int)
        return dataframe

    def _slot(self) -> float:
        return self.wallets.get_total_stake_amount() / len(TOP5)  # one slot per coin

    def _target_stake(self, pair: str) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        return self._slot() * float(df["target"].iloc[-1]) if len(df) else 0.0

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
        if target <= 0 or abs(target - value) <= self.BAND * self._slot():
            return None
        floor = min_stake or 0.0
        if target > value:
            add = min(target - value, max_stake)
            return add if add >= floor else None
        cut = value - target
        if cut < floor or value - cut < floor:
            return None
        return -cut
