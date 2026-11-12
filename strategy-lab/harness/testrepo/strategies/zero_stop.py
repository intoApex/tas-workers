# Test file written for the lab's own harness: a strategy Freqtrade refuses outright (stoploss of 0).
from pandas import DataFrame
from freqtrade.strategy import IStrategy
class HarnessZeroStop(IStrategy):
    timeframe = "1h"; minimal_roi = {"0": 10}; stoploss = -0
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame: return dataframe
    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["volume"] < 0), "buy"] = 1; return dataframe
    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["volume"] > 0), "sell"] = 1; return dataframe
