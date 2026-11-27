"""Upside-down RSI, long and short: the leverage-board twin of the spot lab's TASRsiUpside4h.

Source: "I tried coding an AI Crypto Trading Bot (here's how it did)", an X article by @GoshawkTrades, March 2026
(https://x.com/GoshawkTrades/status/2033710133008580648). Its rule buys when the 4-hour RSI rises above 70 and sells when
it falls below 30, and it names an optional short as the mirror image: short below 30, cover above 70. The article's
reported results (Bitcoin and Ether, 2017 to 2026, about 60% a year, worst drop 57%) are for the long side.

The rule, as restated here:
  * 4-hour candles of BTC and ETH perpetuals, RSI over 14 candles with Wilder's smoothing.
  * Long when RSI crosses above 70, out when it crosses below 30.
  * Short when RSI crosses below 30, out when it crosses above 70.
  * Half the wallet per coin, no leverage (1x), no stop and no profit target, as in the article.
Our own code, written from the article's description. Not affiliated with or endorsed by the author.

Indicators are written out in pandas so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy

COINS = ("BTC/USDT:USDT", "ETH/USDT:USDT")


def _rsi(s: Series, n: int = 14) -> Series:
    """RSI with Wilder's smoothing."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


class TASRsiUpsideLS4h(IStrategy):
    """Long BTC or ETH when the 4-hour RSI(14) crosses above 70, short when it crosses below 30, each closed by the
    opposite cross. Half the wallet per coin, 1x, no stop."""
    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = True
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop, as in the article; at 1x a position cannot be liquidated before it doubles against us
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 120

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = _rsi(dataframe["close"], 14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        r = dataframe["rsi"]
        ok = (dataframe["volume"] > 0) & (metadata["pair"] in COINS)
        dataframe["enter_long"] = ((r > 70) & (r.shift(1) <= 70) & ok).astype(int)
        dataframe["enter_short"] = ((r < 30) & (r.shift(1) >= 30) & ok).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        r = dataframe["rsi"]
        dataframe["exit_long"] = ((r < 30) & (r.shift(1) >= 30)).astype(int)
        dataframe["exit_short"] = ((r > 70) & (r.shift(1) <= 70)).astype(int)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / len(COINS), max_stake)
