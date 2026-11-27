"""Order-flow Bollinger fade: a one-minute mean-reversion bot restated from a public description, in three versions.

Where the idea comes from: on Nov 26, 2026 the X account @gofish99983 posted the backtest of what they called their first
proprietary alpha, with a follow-up describing it: an intraday mean reversion on the one-minute chart that reads order
flow at the Bollinger bands to judge the fight between buyers and sellers once price is stretched, fades that point, and
gets "out when the move back is a straight run". They posted results for one futures market (296 trades in about 13
months, 71% winners, 12-minute average hold) but not the settings, and said the version shown is neither dynamic nor
regime-filtered. This file is our own reading of that description. Nothing here is their code, and any difference between
this and their bot is ours.

What the lab can and cannot see: the original reads order flow (who hit the bid and who lifted the offer). The lab only
has candles: open, high, low, close and volume. So "order flow" here is an estimate: each candle's volume signed by where
it closed inside its range (a close at the high counts as all buying, at the low as all selling). It is a rough stand-in,
and it is the first reason this bot may not behave like the original. The second is cost: the original pays about $2.25
a contract a side on a futures exchange, close to nothing next to its trade size; here a round trip costs about 0.1%.

The rule, for a long (a short is the mirror image at the upper band):
  1. A struggle at the band. Over the last 3 one-minute candles the price touched or broke the lower Bollinger band
     (20 candles, 2 standard deviations), and the latest close is still near it. Their volume is at least twice the hour
     before's, candle for candle, yet they span no more than twice an average candle's range: heavy trading, little
     progress.
  2. Sellers were pushing (estimated flow over the first two of the 3 candles is negative) and the last candle went to
     buyers (it closed in the upper half of its range).
  3. Fade it: buy at the next candle's open.
Out, whichever comes first: the price closes back at the middle band; the move back is a straight run (3 closes in a row
in our favour while in profit), taken while it runs; 45 minutes have passed; or the stop, 2.5 average candle ranges
(ATR, 14) beyond the entry, set from the candle that gave the signal and never more than 3%.

Three versions. Each adds one of the two things the author said the posted one lacks:
  TASFlowFade1m          as described: no regime filter, fixed settings.
  TASFlowFadeRegime1m    plus a regime filter: trade only when the last two hours were choppy rather than trending
                         (efficiency ratio at most 0.35) and the bands are not blowing out (band width at most twice
                         its one-day median).
  TASFlowFadeDynamic1m   plus dynamic settings on top of the regime filter: the band widens as the market trends harder
                         (from 2 to 3 standard deviations), and a fade is taken only when the middle band is far enough
                         away to pay for the trip (at least 0.25% away, against about 0.1% of fees).

Long and short, no leverage (1x), any of the lab's 20 coins. Indicators are written out in pandas so the file needs
nothing but Freqtrade. Signals only look at closed candles.

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


class _FlowFade:
    """Shared rules. Not a strategy itself (it doesn't inherit IStrategy), so Freqtrade doesn't list it."""
    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    minimal_roi = {"0": 100.0}  # no profit target: exits come from the rules above
    stoploss = -0.03            # hard cap; the working stop is the ATR one in custom_stoploss
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    startup_candle_count = 1500  # one day of one-minute candles for the band-width median, plus the hour before it

    LEV = 1.0
    BB_N = 20
    BB_K = 2.0       # band width in standard deviations ...
    K_TREND = 0.0    # ... plus this times the efficiency ratio (the dynamic version widens the band in a trend)
    CLUSTER = 3      # candles in the struggle
    VOL_X = 2.0      # their volume against the hour before
    SPAN_X = 2.0     # their whole range, in average candle ranges
    NEAR = 0.25      # the last close within this share of the way from the band to the middle
    RUN = 3          # closes in a row in our favour that make a straight run
    MAX_HOLD_MIN = 45
    STOP_ATR = 2.5
    REGIME = False
    ER_N = 120
    ER_MAX = 0.35
    BW_MAX = 2.0
    ROOM = 0.0       # least distance from the close to the middle band, as a share of the price

    def bot_start(self, **kwargs) -> None:
        self._stops = {}

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return float(min(self.LEV, max_leverage))

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
        # the struggle: heavy trading, little progress, at the band
        df["effort"] = df["volume"].rolling(c).sum() >= self.VOL_X * c * df["volume"].rolling(60).mean().shift(c)
        df["stall"] = (df["high"].rolling(c).max() - df["low"].rolling(c).min()) <= self.SPAN_X * df["atr"].shift(c)
        before = df["flow"].shift(1).rolling(c - 1).sum()
        near = self.NEAR * (mid - lo)
        df["sig_long"] = ((df["low"] <= lo).astype(float).rolling(c).max() == 1) & (df["close"] <= lo + near) \
            & (before < 0) & (df["flow"] > 0)
        df["sig_short"] = ((df["high"] >= up).astype(float).rolling(c).max() == 1) & (df["close"] >= up - near) \
            & (before > 0) & (df["flow"] < 0)
        df["run_up"] = _streak(df["close"] > df["close"].shift(1))
        df["run_dn"] = _streak(df["close"] < df["close"].shift(1))
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        ok = (df["volume"] > 0) & df["effort"] & df["stall"]
        if self.REGIME:
            ok &= (df["er"] <= self.ER_MAX) & (df["bw_ratio"] <= self.BW_MAX)
        room_long = (df["bb_mid"] - df["close"]) / df["close"]
        room_short = (df["close"] - df["bb_mid"]) / df["close"]
        df["enter_long"] = (ok & df["sig_long"] & (room_long >= self.ROOM)).astype(int)
        df["enter_short"] = (ok & df["sig_short"] & (room_short >= self.ROOM)).astype(int) if self.can_short else 0
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # back at the middle band: the mean reversion is done
        dataframe["exit_long"] = (dataframe["close"] >= dataframe["bb_mid"]).astype(int)
        dataframe["exit_short"] = (dataframe["close"] <= dataframe["bb_mid"]).astype(int)
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.MAX_HOLD_MIN * 60:
            return "time_up"
        if current_profit > 0:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df) and df["run_dn" if trade.is_short else "run_up"].iloc[-1] >= self.RUN:
                return "straight_run"
        return None

    def _stop_distance(self, pair, trade):
        """The stop's distance from the entry, as a share of the entry price: STOP_ATR average ranges, read from the
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
        return stoploss_from_open(-d, current_profit, is_short=trade.is_short, leverage=trade.leverage)


class TASFlowFade1m(_FlowFade, IStrategy):
    """As described: fade a struggle at the one-minute Bollinger band, no regime filter, fixed settings. Long and short, 1x."""


class TASFlowFadeRegime1m(_FlowFade, IStrategy):
    """The same, traded only when the last two hours were choppy and the bands are not blowing out. Long and short, 1x."""
    REGIME = True


class TASFlowFadeDynamic1m(_FlowFade, IStrategy):
    """The regime version with dynamic settings: a band that widens in a trend (2 to 3 standard deviations) and a fade
    taken only when the middle band is at least 0.25% away, enough to pay the fees. Long and short, 1x."""
    REGIME = True
    K_TREND = 1.0
    ROOM = 0.0025
