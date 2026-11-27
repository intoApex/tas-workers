"""Cross-sectional momentum: hold the coins that rose most, restated from Liu, Tsyvinski and Wu and its realistic retest.

Sources: Yukun Liu, Aleh Tsyvinski and Xi Wu, "Common Risk Factors in Cryptocurrency" (Journal of Finance, 2022), found
that buying the fifth of coins with the highest one- to four-week returns and selling the lowest fifth earned about 3%
a week. Chulwoo Han, Byeongguk Kang and Jehyeon Ryu retested it with costs and daily mark-to-market and found the
evidence weak: much of the profit came from the long side, and shorts suffered from large jumps. Their best setting
was a 14-day look-back and a 7-day hold. This is the long side of that setting; the leverage board runs the long-short
book as TASCrossMomLS1d.

The rule, as restated here:
  * Every Monday at 00:00 UTC, rank the lab's 20 coins by their 14-day return.
  * Hold the top fifth (4 coins) for the week, a quarter of the wallet each. A coin still in the top 4 at the next
    rebalance is kept; one that drops out is sold.
What is different, and why: the papers ranked hundreds or thousands of coins; the lab has 20, so a fifth is 4 coins.
Long only, on the spot wallet. Ranked over a fixed list of the 20 coins in this file, so the ranking never depends on a
run's pair list.
Our own code, written from the papers' descriptions. Not affiliated with or endorsed by the authors.

Spot only, no leverage, no stop. Indicators are written out in pandas so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, concat

from freqtrade.strategy import IStrategy

COINS = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT",
    "LTC/USDT", "DOT/USDT", "TRX/USDT", "BCH/USDT", "NEAR/USDT", "UNI/USDT", "ATOM/USDT", "ETC/USDT", "FIL/USDT",
    "APT/USDT", "ARB/USDT",
)  # the lab's 20 coins, fixed here


class TASCrossMom1d(IStrategy):
    """Every Monday 00:00 UTC, hold the 4 of the 20 coins with the highest 14-day return, a quarter of the wallet
    each. Spot, long only."""
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop: the weekly ranking decides
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 30

    LOOKBACK = 14
    HOLD = 4

    def informative_pairs(self):
        return [(p, self.timeframe) for p in COINS]

    def _ranks(self) -> DataFrame:
        cols = []
        for p in COINS:
            d = self.dp.get_pair_dataframe(p, self.timeframe)
            if len(d):
                s = d.set_index("date")["close"]
                cols.append((s / s.shift(self.LOOKBACK) - 1).rename(p))
        if not cols:
            return DataFrame()
        r = concat(cols, axis=1).sort_index()
        return r.rank(axis=1, ascending=False, method="first")  # 1 = strongest

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        # the Sunday candle closes at Monday 00:00: its signals fill at the Monday 00:00 open
        df["rebalance"] = df["date"].dt.weekday == 6
        ranks = self._ranks()
        pair = metadata["pair"]
        if ranks.empty or pair not in ranks.columns:
            df["top"] = False
            return df
        n = ranks.notna().sum(axis=1)
        top = (ranks[pair] <= self.HOLD) & (n >= 2 * self.HOLD)
        df["top"] = df["date"].map(top).fillna(False).astype(bool)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["enter_long"] = (df["rebalance"] & df["top"] & (df["volume"] > 0)).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["exit_long"] = (df["rebalance"] & ~df["top"]).astype(int)
        return df

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / self.HOLD, max_stake)
