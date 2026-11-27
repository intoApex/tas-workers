"""Cross-sectional momentum, long and short: the leverage-board version of the spot lab's TASCrossMom1d.

Sources: Yukun Liu, Aleh Tsyvinski and Xi Wu, "Common Risk Factors in Cryptocurrency" (Journal of Finance, 2022): a
zero-investment book long the fifth of coins with the highest one- to four-week returns and short the lowest fifth
earned about 3% a week. Chulwoo Han, Byeongguk Kang and Jehyeon Ryu retested it with costs and daily mark-to-market
and found the evidence weak: 5 of their 21 long-short books were liquidated, much of the profit came from the long side,
and shorts suffered from large jumps. Their best setting was a 14-day look-back and a 7-day hold. This runs that
setting long and short, to see whether the short side still costs more than it earns.

The rule, as restated here:
  * Every Monday at 00:00 UTC, rank the lab's 20 perpetuals by their 14-day return.
  * Long the 2 strongest, short the 2 weakest, a fifth of the wallet each, held for the week. A coin still in its
    pair of 2 at the next rebalance is kept; one that drops out is closed.
What is different, and why: the papers ranked hundreds of coins and took fifths; with 20 coins and 5 slots, 2 and 2
keeps the book dollar-neutral. Ranked over a fixed list of the 20 perpetuals in this file, so the ranking never
depends on a run's pair list. No leverage (1x), no stop, as in the papers. Funding is paid and received as charged.
Our own code, written from the papers' descriptions. Not affiliated with or endorsed by the authors.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, concat

from freqtrade.strategy import IStrategy

COINS = (
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "LTC/USDT:USDT", "DOT/USDT:USDT", "TRX/USDT:USDT",
    "BCH/USDT:USDT", "NEAR/USDT:USDT", "UNI/USDT:USDT", "ATOM/USDT:USDT", "ETC/USDT:USDT", "FIL/USDT:USDT",
    "APT/USDT:USDT", "ARB/USDT:USDT",
)  # the lab's 20 perpetuals, fixed here
PICK = 2
SLOTS = 5  # the lab's five slots: a fifth of the wallet per position


class TASCrossMomLS1d(IStrategy):
    """Every Monday 00:00 UTC: long the 2 perpetuals with the highest 14-day return, short the 2 lowest. A fifth of
    the wallet each, 1x, no stop."""
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = True
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop, as in the papers; at 1x a position cannot be liquidated before it doubles against us
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 30

    LOOKBACK = 14

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

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
        return concat(cols, axis=1).sort_index().rank(axis=1, ascending=False, method="first")  # 1 = strongest

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        # the Sunday candle closes at Monday 00:00: its signals fill at the Monday 00:00 open
        df["rebalance"] = df["date"].dt.weekday == 6
        ranks = self._ranks()
        pair = metadata["pair"]
        if ranks.empty or pair not in ranks.columns:
            df["mom_top"], df["mom_bottom"] = False, False
            return df
        n = ranks.notna().sum(axis=1)
        enough = n >= 2 * PICK
        r = ranks[pair]
        df["mom_top"] = df["date"].map((r <= PICK) & enough).fillna(False).astype(bool)
        df["mom_bottom"] = df["date"].map((r > n - PICK) & r.notna() & enough).fillna(False).astype(bool)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        ok = df["rebalance"] & (df["volume"] > 0)
        df["enter_long"] = (ok & df["mom_top"]).astype(int)
        df["enter_short"] = (ok & df["mom_bottom"]).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["exit_long"] = (df["rebalance"] & ~df["mom_top"]).astype(int)
        df["exit_short"] = (df["rebalance"] & ~df["mom_bottom"]).astype(int)
        return df

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / SLOTS, max_stake)
