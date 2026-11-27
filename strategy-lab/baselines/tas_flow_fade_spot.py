"""Order-flow Bollinger fade, long only, for the spot Strategy Lab.

The spot half of tas-workers strategy-lab/baselines-futures/tas_flow_fade.py, which restates a one-minute mean-reversion
bot described in public by the X account @gofish99983 on Nov 26, 2026: it reads order flow at the Bollinger bands to
judge the fight between buyers and sellers once price is stretched, fades that point, and gets "out when the move back
is a straight run". Their settings were not published; this is our own reading, and nothing here is their code. The
leverage board runs the full long-and-short version in three steps (as described, plus a regime filter, plus dynamic
settings). The spot wallet cannot short, so this is the long half of the last step only.

The lab only has candles, not real order flow, so "order flow" is an estimate: each candle's volume signed by where it
closed inside its range. And spot fees are about 0.2% a round trip, twice the leverage board's, so a fade is taken only
when the middle band is at least 0.5% away.

The rule:
  1. A struggle at the lower band. Over the last 3 one-minute candles the price touched or broke the lower Bollinger
     band (20 candles; 2 standard deviations in a choppy market, up to 3 as it trends harder), and the latest close is
     still near it. Their volume is at least twice the hour before's, candle for candle, yet they span no more than twice
     an average candle's range: heavy trading, little progress.
  2. Sellers were pushing (estimated flow over the first two of the 3 candles is negative) and the last candle went to
     buyers (it closed in the upper half of its range).
  3. Only in a choppy market: the last two hours' efficiency ratio is at most 0.35 and the band width is at most twice
     its one-day median.
  4. The middle band is at least 0.5% above the close. Then buy at the next candle's open.
Out, whichever comes first: the price closes back at the middle band; the move back is a straight run (3 higher closes
in a row while in profit); 45 minutes have passed; or the stop, 2.5 average candle ranges (ATR, 14) under the entry,
set from the candle that gave the signal and never more than 3%.

Spot only, no leverage. Indicators are written out in pandas so the file needs nothing but Freqtrade. Signals only look
at closed candles.

FROZEN: once this file is deployed it is never edited. A new or changed version goes in a new file under a new class name.
"""
from pandas import DataFrame, Series, concat

from freqtrade.strategy import IStrategy, stoploss_from_open


def _bbands(close: Series, n: int, k) -> tuple:
    """Bollinger Bands: (lower, middle, upper). k may be a number or a Series (a band that changes width)."""
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def _atr(df: DataFrame, n: int = 14) -> Series:
    """Average true range, Wilder's smoothing."""
    pc = df["close"].shift(1)
    tr = concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _flow(df: DataFrame) -> Series:
    """Estimated order flow of each candle: its volume, signed by where it closed in its range.
    +volume for a close at the high, -volume at the low, 0 in the middle or when the candle has no range."""
    rng = df["high"] - df["low"]
    where = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng.where(rng > 0)
    return where.fillna(0.0) * df["volume"]


def _efficiency(close: Series, n: int) -> Series:
    """Kaufman's efficiency ratio over n candles: the net move divided by the path travelled. Near 1 is a clean trend,
    near 0 is chop."""
    path = close.diff().abs().rolling(n).sum()
    return (close - close.shift(n)).abs() / path.where(path > 0)


def _streak(cond: Series) -> Series:
    """How many candles in a row the condition has held, counting the current one."""
    c = cond.fillna(False).astype(int)
    return c.groupby((c == 0).cumsum()).cumsum()


class TASFlowFadeLong1m(IStrategy):
    """Buy a struggle at the one-minute lower Bollinger band in a choppy market, when the middle band is at least 0.5%
    away. Out at the middle band, on a straight run up, after 45 minutes or at an ATR stop. Spot, long only."""
    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = False
    minimal_roi = {"0": 100.0}  # no profit target: exits come from the rules above
    stoploss = -0.03            # hard cap; the working stop is the ATR one in custom_stoploss
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 1500

    BB_N = 20
    BB_K = 2.0
    K_TREND = 1.0
    CLUSTER = 3
    VOL_X = 2.0
    SPAN_X = 2.0
    NEAR = 0.25
    RUN = 3
    MAX_HOLD_MIN = 45
    STOP_ATR = 2.5
    ER_N = 120
    ER_MAX = 0.35
    BW_MAX = 2.0
    ROOM = 0.005

    def bot_start(self, **kwargs) -> None:
        self._stops = {}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        c = self.CLUSTER
        er = _efficiency(df["close"], self.ER_N)
        lo, mid, up = _bbands(df["close"], self.BB_N, self.BB_K + self.K_TREND * er.fillna(1.0))
        df["bb_lo"], df["bb_mid"], df["bb_up"] = lo, mid, up
        df["er"] = er
        bw = (up - lo) / mid
        df["bw_ratio"] = bw / bw.rolling(1440, min_periods=720).median()
        df["atr"] = _atr(df, 14)
        df["flow"] = _flow(df)
        df["effort"] = df["volume"].rolling(c).sum() >= self.VOL_X * c * df["volume"].rolling(60).mean().shift(c)
        df["stall"] = (df["high"].rolling(c).max() - df["low"].rolling(c).min()) <= self.SPAN_X * df["atr"].shift(c)
        before = df["flow"].shift(1).rolling(c - 1).sum()
        df["sig_long"] = ((df["low"] <= lo).astype(float).rolling(c).max() == 1) & (df["close"] <= lo + self.NEAR * (mid - lo)) \
            & (before < 0) & (df["flow"] > 0)
        df["run_up"] = _streak(df["close"] > df["close"].shift(1))
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        ok = (df["volume"] > 0) & df["effort"] & df["stall"] & (df["er"] <= self.ER_MAX) & (df["bw_ratio"] <= self.BW_MAX)
        room = (df["bb_mid"] - df["close"]) / df["close"]
        df["enter_long"] = (ok & df["sig_long"] & (room >= self.ROOM)).astype(int)
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (dataframe["close"] >= dataframe["bb_mid"]).astype(int)
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.MAX_HOLD_MIN * 60:
            return "time_up"
        if current_profit > 0:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) and df["run_up"].iloc[-1] >= self.RUN:
                return "straight_run"
        return None

    def _stop_distance(self, pair, trade):
        """The stop's distance under the entry, as a share of the entry price: STOP_ATR average ranges, read from the
        candle that gave the signal (the last one before the entry). Worked out once per trade."""
        key = (pair, trade.open_date_utc)
        if key not in self._stops:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            before = df.loc[df["date"] < trade.open_date_utc, "atr"] if len(df) else df
            if not len(before) or not before.iloc[-1] > 0:
                return None
            self._stops[key] = min(self.STOP_ATR * float(before.iloc[-1]) / float(trade.open_rate), abs(self.stoploss))
        return self._stops[key]

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        d = self._stop_distance(pair, trade)
        if d is None:
            return None
        return stoploss_from_open(-d, current_profit, is_short=False, leverage=1.0)
