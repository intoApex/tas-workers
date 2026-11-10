from freqtrade.strategy import IStrategy
class HarnessOpens(IStrategy):
    def populate_indicators(self, dataframe, metadata):
        open("/tmp/x", "w").write("x"); return dataframe
