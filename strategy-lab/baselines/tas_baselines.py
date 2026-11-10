"""TradingAgentStack baselines for the Strategy Lab: the plain strategies every other result is read against.

Spot only, long only, no leverage. Indicators are written out in pandas so the file needs nothing but Freqtrade.
Signals only look at closed candles; Freqtrade fills them on the next candle.

FROZEN: once this file is deployed it is never edited. The lab scores a strategy from the day it was added, so the
code behind a record must not change. A new or changed baseline goes in a new file (tas_baselines_2.py) under a new
class name.
"""
import hashlib

from pandas import DataFrame, Series

from freqtrade.strategy import IStrategy


def _sma(s: Series, n: int) -> Series:
    return s.rolling(n).mean()


def _ema(s: Series, n: int) -> Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: Series, n: int = 14) -> Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


class _Common:
    """Shared settings. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target: exits come from each strategy's own rule
    stoploss = -0.99            # no stop, for the same reason
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 60

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe


class TASHoldBTC(_Common, IStrategy):
    """Buy Bitcoin with the whole wallet on day one and never sell."""
    timeframe = "1h"
    use_exit_signal = False

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 1 if metadata["pair"] == "BTC/USDT" else 0
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, **kwargs):
        return max_stake


class TASHoldTop5(_Common, IStrategy):
    """Split the wallet across five big coins on day one and never sell."""
    timeframe = "1h"
    use_exit_signal = False
    COINS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 1 if metadata["pair"] in self.COINS else 0
        return dataframe


class TASSmaCross4h(_Common, IStrategy):
    """In the market while the 20-period average is above the 50-period average, on 4-hour candles."""
    timeframe = "4h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast"] = _sma(dataframe["close"], 20)
        dataframe["slow"] = _sma(dataframe["close"], 50)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["fast"] > dataframe["slow"]) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["fast"] < dataframe["slow"]).astype(int)
        return dataframe


class TASEmaCross1h(_Common, IStrategy):
    """In the market while the 12-hour exponential average is above the 26-hour one."""
    timeframe = "1h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast"] = _ema(dataframe["close"], 12)
        dataframe["slow"] = _ema(dataframe["close"], 26)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["fast"] > dataframe["slow"]) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["fast"] < dataframe["slow"]).astype(int)
        return dataframe


class TASMacd1h(_Common, IStrategy):
    """Buy when the MACD line crosses above its signal line, sell on the cross back down. Hourly candles."""
    timeframe = "1h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["macd"] = _ema(dataframe["close"], 12) - _ema(dataframe["close"], 26)
        dataframe["signal"] = _ema(dataframe["macd"], 9)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        up = (dataframe["macd"] > dataframe["signal"]) & (dataframe["macd"].shift(1) <= dataframe["signal"].shift(1))
        dataframe["enter_long"] = (up & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        down = (dataframe["macd"] < dataframe["signal"]) & (dataframe["macd"].shift(1) >= dataframe["signal"].shift(1))
        dataframe["exit_long"] = down.astype(int)
        return dataframe


class TASTrendFilter1d(_Common, IStrategy):
    """Hold a coin while its price is above its 20-day average; step out when it is below."""
    timeframe = "1d"
    startup_candle_count = 25

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["avg"] = _sma(dataframe["close"], 20)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["close"] > dataframe["avg"]) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["close"] < dataframe["avg"]).astype(int)
        return dataframe


class TASRsiDip1h(_Common, IStrategy):
    """Buy when the hourly RSI falls below 30, sell when it recovers above 55."""
    timeframe = "1h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = _rsi(dataframe["close"], 14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["rsi"] < 30) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["rsi"] > 55).astype(int)
        return dataframe


class TASBollinger1h(_Common, IStrategy):
    """Buy a close below the lower Bollinger band (20 hours, 2 deviations), sell at the middle band."""
    timeframe = "1h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["mid"] = _sma(dataframe["close"], 20)
        dataframe["lower"] = dataframe["mid"] - 2 * dataframe["close"].rolling(20).std()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["close"] < dataframe["lower"]) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["close"] > dataframe["mid"]).astype(int)
        return dataframe


class TASDipBuyer1h(_Common, IStrategy):
    """Buy after a 10% fall in 24 hours. Sell at a 5% gain or a 15% loss, whichever comes first."""
    timeframe = "1h"
    minimal_roi = {"0": 0.05}
    stoploss = -0.15
    use_exit_signal = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["chg24"] = dataframe["close"] / dataframe["close"].shift(24) - 1
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["chg24"] < -0.10) & (dataframe["volume"] > 0)).astype(int)
        return dataframe


class TASBreakout4h(_Common, IStrategy):
    """Buy a close above the highest high of the previous 20 four-hour candles; sell below the lowest low of the previous 10."""
    timeframe = "4h"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["hh"] = dataframe["high"].rolling(20).max().shift(1)
        dataframe["ll"] = dataframe["low"].rolling(10).min().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["close"] > dataframe["hh"]) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["close"] < dataframe["ll"]).astype(int)
        return dataframe


class TASCoinFlip1h(_Common, IStrategy):
    """The control: buys and sells at fixed pseudo-random hours that depend only on the coin and the time.
    It knows nothing about the market. A strategy worth running should beat it."""
    timeframe = "1h"

    @staticmethod
    def _roll(pair: str, when) -> int:
        return int(hashlib.md5(f"{pair}|{int(when.timestamp())}".encode()).hexdigest()[:8], 16)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["roll"] = dataframe["date"].map(lambda d: self._roll(metadata["pair"], d))
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = ((dataframe["roll"] % 48 == 0) & (dataframe["volume"] > 0)).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = ((dataframe["roll"] // 48) % 24 == 0).astype(int)
        return dataframe
