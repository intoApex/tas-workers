"""Turtle breakout, System 2, restated from the original Turtle Trading rules.

Source: the rules Richard Dennis and William Eckhardt taught the Turtles in 1983, as published by Curtis Faith, one of
the original Turtles ("The Original Turtle Trading Rules"). System 2 is the slower of their two systems and the one
taken on every signal: no skip filter. It is the best-known trend-following system there is.

The rule, as restated here:
  * N is the 20-day average true range.
  * Buy when the close breaks above the highest high of the last 55 days.
  * Add a unit each time the price closes another half N above the last unit's entry, up to 4 units.
  * Every unit shares one stop: 2N under the most recent unit's entry.
  * Sell everything when the close breaks below the lowest low of the last 20 days.
What is different, and why:
  * Closes, not touches: the Turtles bought the moment price touched the breakout level and sold the moment it touched
    the exit level. Here the daily candle has to close past the level, and the order fills at the next day's open. The
    2N stop still triggers inside the day.
  * Unit size: the Turtles sized a unit so that 1 N cost 1% of the account. With crypto's daily ranges that unit would
    exceed the lab's one-coin slot (a fifth of the wallet), leaving no room to add. So a unit here is a quarter of the
    slot, and 4 units fill it.
  * Long only: the spot wallet cannot short. Any of the 20 coins, 5 slots.
Our own code, written from the published rules. Not affiliated with or endorsed by the authors.

Spot only, no leverage. Indicators are written out in pandas so the file needs nothing but Freqtrade.

FROZEN: once this file is deployed it is never edited. A corrected or added rule is a new class in a new file.
"""
from pandas import DataFrame, Series, concat

from freqtrade.strategy import IStrategy, stoploss_from_absolute


def _atr(df: DataFrame, n: int) -> Series:
    """N: average true range, Wilder's smoothing."""
    pc = df["close"].shift(1)
    tr = concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


class TASTurtle1d(IStrategy):
    """Turtle System 2: buy a close above the 55-day high, add up to 4 units every half N, one stop 2N under the last
    unit, sell a close under the 20-day low. Any of the 20 coins, spot, long only."""
    INTERFACE_VERSION = 3
    timeframe = "1d"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target
    stoploss = -0.99            # replaced at once by the 2N stop in custom_stoploss
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    position_adjustment_enable = True
    max_entry_position_adjustment = 3  # 4 units in all
    startup_candle_count = 120

    ENTRY_DAYS = 55
    EXIT_DAYS = 20
    N_DAYS = 20
    UNITS = 4
    SLOTS = 5  # the lab's five slots; a unit is a quarter of one

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["n"] = _atr(df, self.N_DAYS)
        df["high_55"] = df["high"].rolling(self.ENTRY_DAYS).max().shift(1)  # the 55 days before today
        df["low_20"] = df["low"].rolling(self.EXIT_DAYS).min().shift(1)     # the 20 days before today
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["enter_long"] = ((df["close"] > df["high_55"]) & (df["volume"] > 0)).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["close"] < dataframe["low_20"]).astype(int)
        return dataframe

    def _first_unit(self, proposed_stake: float) -> float:
        """A quarter of a slot: the wallet's fifth, never more than a quarter of what Freqtrade proposes for one trade."""
        return min(self.wallets.get_total_stake_amount() / self.SLOTS, proposed_stake) / self.UNITS

    def _last_entry(self, pair, trade):
        """(entry price, N on the day that signalled it) for the most recent unit."""
        fills = [o for o in trade.select_filled_orders(trade.entry_side) if o.order_filled_utc]
        if not fills:
            return None, None
        last = max(fills, key=lambda o: o.order_filled_utc)
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        before = df.loc[df["date"] < last.order_filled_utc, "n"] if len(df) else df
        n = float(before.iloc[-1]) if len(before) else None
        return float(last.safe_price), n

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake,
                            leverage, entry_tag, side, **kwargs) -> float:
        return min(self._first_unit(proposed_stake), max_stake)

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake,
                              current_entry_rate, current_exit_rate, current_entry_profit, current_exit_profit, **kwargs):
        if trade.nr_of_successful_entries >= self.UNITS or trade.has_open_orders:
            return None
        price, n = self._last_entry(trade.pair, trade)
        if price is None or not n or current_rate < price + 0.5 * n:
            return None
        fills = trade.select_filled_orders(trade.entry_side)
        unit = fills[0].safe_cost if fills else 0.0  # every unit the size of the first, whatever else the wallet holds
        add = min(unit, max_stake)
        return add if add > 0 and add >= (min_stake or 0.0) else None

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        price, n = self._last_entry(pair, trade)
        if price is None or not n:
            return None
        return stoploss_from_absolute(price - 2 * n, current_rate, is_short=False, leverage=1.0)
