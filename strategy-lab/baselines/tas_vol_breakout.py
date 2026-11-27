"""Volatility breakout, restated from Larry Williams' rule as crypto bot traders run it.

Source: Larry Williams' volatility breakout, from his short-term trading work: buy when the price rises past today's
open plus a share of yesterday's range, and sell at the next day's open. It became one of the most-copied crypto bot
rules, above all in Korea, where bots on Upbit buy on the breakout and sell at the 09:00 Korean day change (00:00 UTC),
usually with k = 0.5. A practitioner rule: no crypto study backs it.

The rule, as restated here:
  * Each UTC day: target = today's open + 0.5 x (yesterday's high - yesterday's low).
  * Buy when price passes the target. Sell at the next 00:00 UTC open.
What is different, and why: the original buys the moment price touches the target. The lab reads closed 1-hour
candles, so here an hourly close has to be above the target, and the order fills at the next hour's open. No entries on
the last hour of the day (it would fill at the sell time). The five largest of the lab's 20 coins (BTC, ETH, SOL, BNB,
XRP), one slot each.
Our own code, written from the published rule. Not affiliated with or endorsed by Larry Williams.

Spot only, long only, no leverage, no stop. Indicators are written out in pandas so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from datetime import timedelta

from pandas import DataFrame

from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")


class TASVolBreakout1h(IStrategy):
    """Buy when an hourly close passes today's open + 0.5 x yesterday's range; sell at the next 00:00 UTC open. BTC,
    ETH, SOL, BNB and XRP, spot, one slot each."""
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # no stop: the day change ends every trade
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 48

    K = 0.5

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        day = df["date"].dt.floor("D")
        df["day_open"] = df.groupby(day)["open"].transform("first")
        daily = df.groupby(day).agg(hi=("high", "max"), lo=("low", "min"))
        prev_range = (daily["hi"] - daily["lo"])
        prev_range.index = prev_range.index + timedelta(days=1)  # yesterday's range, used today
        df["prev_range"] = day.map(prev_range)
        df["target"] = df["day_open"] + self.K * df["prev_range"]
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        ok = (df["volume"] > 0) & (df["date"].dt.hour < 23) & (metadata["pair"] in TOP5)
        df["enter_long"] = ((df["close"] > df["target"]) & ok).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # the 23:00 candle closes at 00:00: its signal fills at the 00:00 open
        dataframe["exit_long"] = (dataframe["date"].dt.hour == 23).astype(int)
        return dataframe

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self.wallets.get_total_stake_amount() / len(TOP5), max_stake)
