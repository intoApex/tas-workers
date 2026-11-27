"""Upside-down RSI: buy strength, restated from a public article by @GoshawkTrades on X.

Source: "I tried coding an AI Crypto Trading Bot (here's how it did)", an X article by @GoshawkTrades, March 2026
(https://x.com/GoshawkTrades/status/2033710133008580648). The textbook RSI trade buys below 30 and sells above 70. This
one does the opposite, on the view that crypto trends: buy when RSI rises above 70, sell when it falls below 30. On
4-hour candles of Bitcoin and Ether from August 2017 to February 2026, with RSI on Wilder's smoothing, the article
reports $1,000 growing to $54,600 at full allocation: about 60% a year, Sharpe 0.96, worst drop 57%, 157 trades. The
same rule with a simple-average RSI did much worse (about 30% a year), so the result leans on that choice.

The rule, as restated here:
  * 4-hour candles of BTC and ETH, the two coins the article tested.
  * RSI over 14 candles with Wilder's smoothing.
  * Buy when RSI crosses above 70. Sell when it crosses below 30. No stop and no profit target, as in the article.
  * Each coin gets half the wallet. The article ran "full allocation on each trade"; with two coins, half each is the
    nearest the wallet allows.
Our own code, written from the article's description. Not affiliated with or endorsed by the author.

Spot only, long only, no leverage. The leverage board runs the article's optional short as TASRsiUpsideLS4h.
Indicators are written out in pandas so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy

COINS = ("BTC/USDT", "ETH/USDT")


def _rsi(s: Series, n: int = 14) -> Series:
    """RSI with Wilder's smoothing."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


class TASRsiUpside4h(IStrategy):
    """Buy BTC or ETH when the 4-hour RSI(14) crosses above 70; sell when it crosses below 30. Half the wallet per coin,
    spot, no stop."""
    INTERFACE_VERSION = 3
    timeframe = "4h"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop, as in the article
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 120

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = _rsi(dataframe["close"], 14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        r = dataframe["rsi"]
        ok = (dataframe["volume"] > 0) & (metadata["pair"] in COINS)
        dataframe["enter_long"] = ((r > 70) & (r.shift(1) <= 70) & ok).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        r = dataframe["rsi"]
        dataframe["exit_long"] = ((r < 30) & (r.shift(1) >= 30)).astype(int)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / len(COINS), max_stake)
