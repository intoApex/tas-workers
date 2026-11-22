from freqtrade.strategy import IStrategy
from pandas import DataFrame
from talib import EMA, RSI, ATR, MACD
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union


class TASLeaguePreDeepSeek(IStrategy):
    """
    Trend-following strategy with ATR-based stoploss.
    Uses EMA50/200, RSI, MACD and ATR on 1h timeframe.
    """

    INTERFACE_VERSION = 3

    timeframe = '1h'

    can_short = True
    leverage = 2.0
    max_open_trades = 5

    stoploss = -0.10
    minimal_roi = {
        "0": 0.05,
        "30": 0.03,
        "60": 0.01,
        "120": 0
    }

    trailing_stop = False
    use_custom_stoploss = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count = 200

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Trend indicators
        dataframe['ema50'] = EMA(dataframe['close'], timeperiod=50)
        dataframe['ema200'] = EMA(dataframe['close'], timeperiod=200)

        # Momentum
        dataframe['rsi'] = RSI(dataframe['close'], timeperiod=14)

        # Volatility
        dataframe['atr'] = ATR(dataframe['high'], dataframe['low'], dataframe['close'], timeperiod=14)

        # MACD
        macd, macdsignal, macdhist = MACD(dataframe['close'], fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe['macd'] = macd
        dataframe['macdsignal'] = macdsignal
        dataframe['macdhist'] = macdhist

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['ema50'] > dataframe['ema200']) &
                (dataframe['close'] > dataframe['ema50']) &
                (dataframe['rsi'] < 70) &
                (dataframe['macdhist'] > 0) &
                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        dataframe.loc[
            (
                (dataframe['ema50'] < dataframe['ema200']) &
                (dataframe['close'] < dataframe['ema50']) &
                (dataframe['rsi'] > 30) &
                (dataframe['macdhist'] < 0) &
                (dataframe['volume'] > 0)
            ),
            'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['rsi'] > 80) |
                (dataframe['macdhist'] < 0) |
                (dataframe['close'] < dataframe['ema50'])
            ),
            'exit_long'] = 1

        dataframe.loc[
            (
                (dataframe['rsi'] < 20) |
                (dataframe['macdhist'] > 0) |
                (dataframe['close'] > dataframe['ema50'])
            ),
            'exit_short'] = 1

        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        ATR-based stoploss: 2 * ATR below current price, capped at -10%.
        """
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is not None and len(dataframe) > 0:
                last_atr = dataframe['atr'].iloc[-1]
                if last_atr > 0:
                    sl = -2.0 * last_atr / current_rate
                    if sl < -0.10:
                        sl = -0.10
                    return sl
        except Exception:
            pass
        return -0.10
