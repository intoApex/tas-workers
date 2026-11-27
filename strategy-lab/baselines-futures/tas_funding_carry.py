"""Funding carry across coins: long the perpetuals with the lowest funding, short those with the highest.

Source: Bryan Vine, "Crypto Carry: The Funding-Rate Cross-Section, Net of Costs and the Liquidation Tail", Alpha
Research paper 2, June 2026 (https://bryanvine.github.io/alpha-research/paper2.html). On 30 Binance USD-M perpetuals from
January 2023 to May 2026, with 0.06% a side for fees and slippage, a weekly dollar-neutral book long the third with the
lowest funding and short the third with the highest earned a net Sharpe of 0.71 in-sample and 0.39 out-of-sample, with a
worst drop of about 35%. The same study finds the plain carry trade (long spot, short perpetual) has faded to below
cash. The idea: coins whose longs pay the most to stay long are crowded, and crowded coins tend to lag.

The rule, as restated here:
  * The lab's 20 perpetuals. Each one's funding is summed over 7 days and lagged by a day, so nothing from the
    rebalance day itself is used. Vine averaged it; some coins pay every 4 hours and some every 8, and the sum is what
    a holder actually pays over the week.
  * Every Monday at 00:00 UTC: long the 2 coins with the lowest sum, short the 2 with the highest. The study took
    thirds of 30 coins; with 20 coins and 5 slots, 2 and 2 keeps the book dollar-neutral.
  * A position is held until a Monday when it is no longer in its pair of 2. A fifth of the wallet each, no leverage
    (1x), no stop and no profit target, as in the study. Funding is paid and received as the exchange charges it.
Our own code, written from the study's description. Not affiliated with or endorsed by the author.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, concat

from freqtrade.strategy import IStrategy

COINS = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
    "DOT/USDT:USDT",
    "TRX/USDT:USDT",
    "BCH/USDT:USDT",
    "NEAR/USDT:USDT",
    "UNI/USDT:USDT",
    "ATOM/USDT:USDT",
    "ETC/USDT:USDT",
    "FIL/USDT:USDT",
    "APT/USDT:USDT",
    "ARB/USDT:USDT",
)  # the lab's 20 perpetuals, fixed here so the ranking never depends on the run's pair list
PICK = 2
SLOTS = 5  # the lab's five slots: a fifth of the wallet per position


class TASFundingCarry1h(IStrategy):
    """Every Monday 00:00 UTC: long the 2 perpetuals with the lowest funding over the past week, short the 2 with the
    highest (funding summed over 7 days, lagged a day). 20 coins, a fifth of the wallet each, 1x, no stop."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop, as in the study; at 1x a position cannot be liquidated before it doubles against us
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 200  # 8 days of hourly candles: 7 summed, 1 of lag

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

    def informative_pairs(self):
        return [(p, "1h", "funding_rate") for p in COINS]

    def _rank_table(self) -> DataFrame:
        """Hour-indexed table, one column per coin: the 7-day funding sum known by that hour (lagged a day)."""
        cols = []
        for p in COINS:
            fr = self.dp.get_pair_dataframe(p, "1h", "funding_rate")
            if not len(fr):
                continue
            col = "funding_rate" if "funding_rate" in fr.columns else "open"
            s = fr.set_index("date")[col].astype(float).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            week = s.rolling("7D").sum().shift(freq="24h")  # what a holder paid over the 7 days ending a day earlier
            cols.append(week.rename(p))
        if not cols:
            return DataFrame()
        t = concat(cols, axis=1).sort_index().ffill()
        return t.rank(axis=1, method="first")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        pair = metadata["pair"]
        # the Sunday 23:00 candle closes at Monday 00:00: its signals fill at the Monday 00:00 open
        df["rebalance"] = (df["date"].dt.weekday == 6) & (df["date"].dt.hour == 23)
        ranks = self._rank_table()
        if ranks.empty or pair not in ranks.columns:
            df["fund_low"], df["fund_high"] = False, False
            return df
        n = ranks.notna().sum(axis=1)
        r = ranks[pair]
        enough = n >= 2 * PICK  # with fewer coins than that, the long and short picks would overlap
        low = (r <= PICK) & enough
        high = (r > n - PICK) & r.notna() & enough
        # funding events are hours apart: carry each hour's standing forward to the candles in between
        df["fund_low"] = df["date"].map(low.reindex(low.index.union(df["date"])).ffill()).fillna(False).astype(bool)
        df["fund_high"] = df["date"].map(high.reindex(high.index.union(df["date"])).ffill()).fillna(False).astype(bool)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        ok = (df["volume"] > 0) & df["rebalance"]
        df["enter_long"] = (ok & df["fund_low"]).astype(int)
        df["enter_short"] = (ok & df["fund_high"]).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["exit_long"] = (df["rebalance"] & ~df["fund_low"]).astype(int)
        df["exit_short"] = (df["rebalance"] & ~df["fund_high"]).astype(int)
        return df

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / SLOTS, max_stake)
