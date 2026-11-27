"""Negative-funding squeeze: a recurring X setup, with thresholds that are ours.

Source: a setup traders post on X again and again: when funding stays negative (shorts paying longs) while price holds
its range, the crowded shorts get squeezed, so go long. The posts that test it (for example W3Realm, May 2026,
https://x.com/W3Realm/status/2057086781196230824) say the edge is conditional but publish no thresholds or numbers. So
every threshold below is ours, chosen once before any test and never tuned. Read the result as a test of the idea in
its plainest form, not of anyone's private version.

The rule, as set here:
  * Funding is negative: the coin's funding summed over the last 3 days is below zero (lagged an hour, so only paid
    funding counts).
  * Price holds its range: the close is above the lowest quarter of the last 7 days' range (the 7 days before this
    candle).
  * Buy when both first become true together.
  * Sell when the 3-day funding sum is back at or above zero (the shorts have left), when the close breaks under the
    7-day low (the range failed), or after 7 days, whichever comes first.
Any of the lab's 20 perpetuals, 5 slots, long only, no leverage (1x). Funding is paid and received as charged.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame

from freqtrade.strategy import IStrategy

COINS = (
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "LTC/USDT:USDT", "DOT/USDT:USDT", "TRX/USDT:USDT",
    "BCH/USDT:USDT", "NEAR/USDT:USDT", "UNI/USDT:USDT", "ATOM/USDT:USDT", "ETC/USDT:USDT", "FIL/USDT:USDT",
    "APT/USDT:USDT", "ARB/USDT:USDT",
)  # the lab's 20 perpetuals, fixed here


class TASNegFundingLong1h(IStrategy):
    """Buy when a perpetual's 3-day funding turns negative while its close holds above the bottom quarter of its 7-day
    range; sell when funding turns back up, the range breaks, or after 7 days. Any of the 20 perpetuals, 1x."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # the range break below is the stop, read on closes
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 200

    FUNDING_DAYS = 3
    RANGE_HOURS = 7 * 24
    MAX_HOLD_DAYS = 7

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

    def informative_pairs(self):
        return [(p, "1h", "funding_rate") for p in COINS]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        fr = self.dp.get_pair_dataframe(metadata["pair"], "1h", "funding_rate") if metadata["pair"] in COINS else DataFrame()
        if len(fr):
            col = "funding_rate" if "funding_rate" in fr.columns else "open"
            s = fr.set_index("date")[col].astype(float).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            paid = s.rolling(f"{self.FUNDING_DAYS}D").sum().shift(freq="1h")
            df["funding_3d"] = df["date"].map(paid.reindex(paid.index.union(df["date"])).ffill())
        else:
            df["funding_3d"] = float("nan")
        lo = df["low"].rolling(self.RANGE_HOURS).min().shift(1)
        hi = df["high"].rolling(self.RANGE_HOURS).max().shift(1)
        df["range_lo"] = lo
        df["setup"] = (df["funding_3d"] < 0) & (df["close"] > lo + 0.25 * (hi - lo))
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        s = df["setup"].fillna(False).astype(bool)
        df["enter_long"] = (s & ~s.shift(1, fill_value=False) & (df["volume"] > 0)).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["exit_long"] = ((df["funding_3d"] >= 0) | (df["close"] < df["range_lo"])).astype(int)
        return df

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.MAX_HOLD_DAYS * 86400:
            return "seven_days"
        return None
