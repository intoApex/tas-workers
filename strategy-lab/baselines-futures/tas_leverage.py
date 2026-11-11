"""TradingAgentStack leverage ladder: the same plain rules at 1x, 2x, 3x, 5x and 10x.

The point is to show what leverage alone does to a result. Each rule is identical across its rungs; only the
leverage changes. Binance USDT perpetual futures, isolated margin. A liquidated position loses its whole margin.

Three rules:
  Hold       buy Bitcoin once with the whole wallet as margin and never sell. If it is liquidated, it stays out.
  Trend      on five large coins: long while the 20-period average is above the 50-period one, short while below.
  RSI dip    buy when the hourly RSI falls below 30, sell when it recovers above 55 (the spot lab's TASRsiDip1h).

Indicators are written out in pandas so the file needs nothing but Freqtrade. Signals only look at closed candles.

FROZEN: once this file is deployed it is never edited. A new or changed rung goes in a new file under a new class name.
"""
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT")


def _sma(s: Series, n: int) -> Series:
    return s.rolling(n).mean()


def _rsi(s: Series, n: int = 14) -> Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


class _Lev:
    """Shared settings. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    LEV = 1
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target: exits come from each rule
    stoploss = -0.99            # no stop: the only forced exit is liquidation
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 60

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return float(min(self.LEV, max_leverage))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe


class _LevHold(_Lev):
    """Buy Bitcoin once with the whole wallet as margin and never sell. One entry ever: after a liquidation it stays out."""
    timeframe = "1h"
    use_exit_signal = False

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 1 if metadata["pair"] == "BTC/USDT:USDT" else 0
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, **kwargs):
        return max_stake

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return not Trade.get_trades_proxy(pair=pair, is_open=False)


class TASLevHoldBTC1x(_LevHold, IStrategy):
    """Hold Bitcoin on futures with no leverage. The control for the rungs below."""
    LEV = 1


class TASLevHoldBTC2x(_LevHold, IStrategy):
    """Hold Bitcoin at 2x."""
    LEV = 2


class TASLevHoldBTC3x(_LevHold, IStrategy):
    """Hold Bitcoin at 3x."""
    LEV = 3


class TASLevHoldBTC5x(_LevHold, IStrategy):
    """Hold Bitcoin at 5x."""
    LEV = 5


class TASLevHoldBTC10x(_LevHold, IStrategy):
    """Hold Bitcoin at 10x."""
    LEV = 10


class _LevTrend(_Lev):
    """Always in the market on five large coins: long while the 20-period average is above the 50-period one, short while below."""
    timeframe = "4h"
    can_short = True
    COINS = TOP5

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast"] = _sma(dataframe["close"], 20)
        dataframe["slow"] = _sma(dataframe["close"], 50)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ok = (dataframe["volume"] > 0) & (metadata["pair"] in self.COINS)
        dataframe["enter_long"] = ((dataframe["fast"] > dataframe["slow"]) & ok).astype(int)
        dataframe["enter_short"] = ((dataframe["fast"] < dataframe["slow"]) & ok).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["fast"] < dataframe["slow"]).astype(int)
        dataframe["exit_short"] = (dataframe["fast"] > dataframe["slow"]).astype(int)
        return dataframe


class TASLevTrend4h1x(_LevTrend, IStrategy):
    """Trend, long and short, no leverage."""
    LEV = 1


class TASLevTrend4h3x(_LevTrend, IStrategy):
    """Trend, long and short, at 3x."""
    LEV = 3


class TASLevTrend4h5x(_LevTrend, IStrategy):
    """Trend, long and short, at 5x."""
    LEV = 5


class _LevRsiDip(_Lev):
    """Buy when the hourly RSI falls below 30, sell when it recovers above 55. Long only, any of the 20 coins."""
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


class TASLevRsiDip1h1x(_LevRsiDip, IStrategy):
    """RSI dip buying, no leverage."""
    LEV = 1


class TASLevRsiDip1h3x(_LevRsiDip, IStrategy):
    """RSI dip buying at 3x."""
    LEV = 3


class TASLevRsiDip1h5x(_LevRsiDip, IStrategy):
    """RSI dip buying at 5x."""
    LEV = 5
