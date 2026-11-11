"""TradingAgentStack baselines, part 2: the grid and DCA bots that bot platforms sell, written out in plain rules.

A grid bot buys a slice each time the price falls one step and sells a slice each time it rises one step.
A DCA bot buys, buys more as the price falls ("safety orders"), and sells everything at a small profit over its
average price. Neither has a stop: if the price keeps falling they sit on the coins, which is the risk of the type.
Also here: plain weekly buying, which is what most people mean by "DCA".

Spot only, long only, no leverage. All five trade only the five largest coins, or Bitcoin alone.
Decisions use the price at the start of each 15-minute candle; nothing looks ahead.

FROZEN: once this file is deployed it is never edited. A new or changed baseline goes in a new file
(tas_baselines_3.py) under a new class name.
"""
from pandas import DataFrame

from freqtrade.strategy import IStrategy

TOP5 = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")


class _Base2:
    """Shared settings. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target unless a bot sets one
    stoploss = -0.99            # no stop: these bots hold through a fall
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = False
    position_adjustment_enable = True
    startup_candle_count = 1
    COINS = TOP5

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Start a new round whenever there is none open for this coin. The rules below decide everything after that.
        dataframe["enter_long"] = 1 if metadata["pair"] in self.COINS else 0
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    @staticmethod
    def _entries(trade):
        return trade.select_filled_orders(trade.entry_side)


class _Grid(_Base2):
    """A spot grid. LEVELS slices per coin, one STEP apart, measured down from the price of the first buy.

    With n slices held: buy one more when the price is n steps below the first buy (until all slices are in);
    sell one when the price is back above the level where the newest slice was bought, plus one step.
    The last slice is sold one step above the first buy, which ends the round; the next round starts at the new price.
    """
    LEVELS = 8
    STEP = 0.015

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, **kwargs):
        return proposed_stake / self.LEVELS

    def _state(self, trade):
        buys = self._entries(trade)
        if not buys:
            return None, 0, 0.0
        n = trade.nr_of_successful_entries - trade.nr_of_successful_exits  # slices held now
        return buys[0].safe_price, n, buys[0].stake_amount

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs):
        first, n, slice_stake = self._state(trade)
        if not first or n < 1:
            return None
        if n < self.LEVELS and current_rate <= first * (1 - self.STEP) ** n:
            return slice_stake if slice_stake <= max_stake else None, f"grid_buy_{n + 1}"
        if n > 1 and current_rate >= first * (1 - self.STEP) ** (n - 2):
            return -(trade.stake_amount / n), f"grid_sell_{n}"
        if n == 1 and current_rate >= first * (1 + self.STEP):
            return -trade.stake_amount, "grid_top"  # the last slice: this closes the round
        return None


class TASGrid15m(_Grid, IStrategy):
    """Grid bot, tight: 8 slices per coin, 1.5% apart, on the five largest coins."""
    timeframe = "15m"
    max_entry_position_adjustment = -1
    LEVELS = 8
    STEP = 0.015


class TASGridWide15m(_Grid, IStrategy):
    """Grid bot, wide: 6 slices per coin, 3% apart, on the five largest coins."""
    timeframe = "15m"
    max_entry_position_adjustment = -1
    LEVELS = 6
    STEP = 0.03


class _Dca(_Base2):
    """A DCA bot. One starting buy, then up to SAFETY more buys, each when the price is STEP below the last buy and each
    SCALE times the size of the one before. Everything is sold when the position is TAKE above its average price."""
    SAFETY = 5
    STEP = 0.02
    SCALE = 1.3
    TAKE = 0.015

    def _total(self):
        return sum(self.SCALE ** i for i in range(self.SAFETY + 1))

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, **kwargs):
        return proposed_stake / self._total()  # keep room for every safety order

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs):
        buys = self._entries(trade)
        done = len(buys) - 1  # safety orders used so far
        if not buys or done >= self.SAFETY or current_rate > buys[-1].safe_price * (1 - self.STEP):
            return None
        stake = buys[0].stake_amount * self.SCALE ** (done + 1)
        return stake if stake <= max_stake else None, f"safety_{done + 1}"


class TASDca15m(_Dca, IStrategy):
    """DCA bot, the usual setup: 5 safety orders 2% apart, each 1.3 times the last, sell at 1.5% over the average price."""
    timeframe = "15m"
    minimal_roi = {"0": 0.015}
    max_entry_position_adjustment = 5
    SAFETY, STEP, SCALE, TAKE = 5, 0.02, 1.3, 0.015


class TASDcaFlat15m(_Dca, IStrategy):
    """DCA bot, cautious: 4 safety orders 3% apart, all the same size, sell at 2% over the average price."""
    timeframe = "15m"
    minimal_roi = {"0": 0.02}
    max_entry_position_adjustment = 4
    SAFETY, STEP, SCALE, TAKE = 4, 0.03, 1.0, 0.02


class TASWeeklyBuyBTC(_Base2, IStrategy):
    """Plain weekly buying: put one 26th of the wallet into Bitcoin, add the same amount every 7 days, never sell."""
    timeframe = "1h"
    max_entry_position_adjustment = 25
    COINS = ("BTC/USDT",)
    WEEKS = 26

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, **kwargs):
        return max_stake / self.WEEKS

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs):
        buys = self._entries(trade)
        if not buys or len(buys) >= self.WEEKS:
            return None
        last = buys[-1].order_filled_utc or buys[-1].order_date_utc
        if (current_time - last).total_seconds() < 7 * 86400:
            return None
        stake = buys[0].stake_amount
        return stake if stake <= max_stake else None, f"week_{len(buys) + 1}"
