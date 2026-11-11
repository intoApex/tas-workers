# Test file written for the lab's own harness: an old-interface strategy (populate_buy_trend / populate_sell_trend).
from pandas import DataFrame
from freqtrade.strategy import IStrategy
class HarnessOldApi(IStrategy):
    timeframe = "1h"; minimal_roi = {"0": 0.03}; stoploss = -0.08; startup_candle_count = 30
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ma"] = dataframe["close"].rolling(20).mean(); return dataframe
    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["close"] < dataframe["ma"] * 0.985) & (dataframe["volume"] > 0), "buy"] = 1; return dataframe
    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["close"] > dataframe["ma"]), "sell"] = 1; return dataframe
