# Test file written for the lab's own harness: never finishes, to exercise the time limit and retirement.
import time
from pandas import DataFrame
from freqtrade.strategy import IStrategy
class HarnessNeverFinishes(IStrategy):
    timeframe = "1h"; minimal_roi = {"0": 0.03}; stoploss = -0.08
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        time.sleep(3600); return dataframe
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe
