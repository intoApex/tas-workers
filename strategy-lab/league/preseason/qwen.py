from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, informative

logger = logging.getLogger(__name__)


class TASLeaguePreQwen(IStrategy):
    """
    Multi-timeframe trend continuation plus small range-reversion module.

    - 1h signals, 4h regime filter, BTC 1h market filter (timestamp aligned, past only).
    - Long and short, 2x isolated leverage, ATR sized stakes.
    - ATR chandelier trailing stop, break-even lock, signal based exits.
    - Realised-drawdown circuit breakers throttle new risk after losing clusters.
    """

    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = True
    startup_candle_count = 120
    process_only_new_candles = True

    # wide disaster backstop only; the ATR stop below is always tighter
    stoploss = -0.20
    use_custom_stoploss = True
    trailing_stop = False

    minimal_roi = {"0": 0.40, "120": 0.20, "300": 0.11, "600": 0.06}

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    position_adjustment_enable = False

    # ---- tunables -------------------------------------------------------
    btc_pair = "BTC/USDT:USDT"
    target_leverage = 2.0
    risk_init_mult = 2.2          # ATR multiples for the initial stop
    risk_mr_mult = 1.7
    trail_mult = 2.6              # ATR multiples for the chandelier trail
    trail_mr_mult = 1.5
    risk_floor = 0.012            # min stop distance in price fraction
    risk_cap = 0.060              # max stop distance in price fraction
    be_buffer = 0.0018            # break-even cushion (covers fees)
    max_same_side = 4

    # ---- small helpers --------------------------------------------------
    @staticmethod
    def _num(value, default: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(v):
            return default
        return v

    def _closed_slice(self, pair: str, current_time: datetime) -> Optional[DataFrame]:
        """Rows strictly older than current_time (only finished candles)."""
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return None
        if df is None or len(df) == 0:
            return None
        dates = df["date"]
        ct = current_time
        try:
            if dates.dt.tz is None:
                if ct.tzinfo is not None:
                    ct = ct.replace(tzinfo=None)
            else:
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
            idx = int(dates.searchsorted(ct, side="left"))
        except Exception:
            return None
        if idx <= 0:
            return None
        return df.iloc[:idx]

    def _risk_frac(self, atr_pct: float, mr: bool) -> float:
        mult = self.risk_mr_mult if mr else self.risk_init_mult
        return min(max(mult * atr_pct, self.risk_floor), self.risk_cap)

    @staticmethod
    def _held_hours(trade: Trade, current_time: datetime) -> float:
        try:
            return max(0.0, (current_time - trade.open_date_utc).total_seconds() / 3600.0)
        except Exception:
            return 0.0

    def _drawdown_state(self):
        start = 1000.0
        try:
            w = self.config.get("dry_run_wallet", 1000)
            if w is not None and float(w) > 0:
                start = float(w)
        except Exception:
            start = 1000.0
        try:
            closed = Trade.get_trades_proxy(is_open=False)
        except Exception:
            return 0.0, start, []
        rows = [t for t in closed if t.close_date is not None]
        rows.sort(key=lambda t: t.close_date)
        cum = 0.0
        peak = 0.0
        for t in rows:
            cum += self._num(t.close_profit_abs, 0.0)
            if cum > peak:
                peak = cum
        return peak - cum, start, rows

    # ---- market state ---------------------------------------------------
    def _add_market_state(self, df: DataFrame) -> DataFrame:
        defaults = {"btc_rsi": 50.0, "btc_ret6": 0.0, "btc_ret24": 0.0, "btc_trend": 0.0}
        b = None
        try:
            raw = self.dp.get_pair_dataframe(pair=self.btc_pair, timeframe=self.timeframe)
            if raw is not None and len(raw) > 60:
                b = raw[["date", "close"]].copy()
        except Exception:
            b = None
        if b is not None and len(b) > 60:
            b = b.iloc[:-1]
            b["btc_ema50"] = ta.EMA(b, timeperiod=50)
            b["btc_rsi"] = ta.RSI(b, timeperiod=14)
            b["btc_ret6"] = b["close"] / b["close"].shift(6) - 1.0
            b["btc_ret24"] = b["close"] / b["close"].shift(24) - 1.0
            b["btc_trend"] = np.where(b["close"] > b["btc_ema50"], 1.0, -1.0)
            b = b[["date", "btc_rsi", "btc_ret6", "btc_ret24", "btc_trend"]]
            b = b.drop_duplicates(subset=["date"], keep="last")
            df = df.merge(b, on="date", how="left")
        for key, val in defaults.items():
            if key not in df.columns:
                df[key] = val
            else:
                df[key] = df[key].fillna(val)
        return df

    def informative_pairs(self):
        return [(self.btc_pair, self.timeframe)]

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema20_prev"] = dataframe["ema20"].shift(3)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        return dataframe

    # ---- indicators -----------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe

        df["atr"] = ta.ATR(df, timeperiod=14)
        df["atr_pct"] = df["atr"] / df["close"]
        df["atr_pct_med"] = df["atr_pct"].rolling(96, min_periods=24).median()

        df["ema20"] = ta.EMA(df, timeperiod=20)
        df["ema50"] = ta.EMA(df, timeperiod=50)
        df["ema100"] = ta.EMA(df, timeperiod=100)

        df["rsi"] = ta.RSI(df, timeperiod=14)
        macd = ta.MACD(df, fastperiod=12, slowperiod=26, signalperiod=9)
        df["macdhist"] = macd["macdhist"]
        df["adx"] = ta.ADX(df, timeperiod=14)
        df["plus_di"] = ta.PLUS_DI(df, timeperiod=14)
        df["minus_di"] = ta.MINUS_DI(df, timeperiod=14)

        # prior channel extremes (shifted so the current bar is not inside them)
        df["hh24"] = df["high"].rolling(24, min_periods=24).max().shift(1)
        df["ll24"] = df["low"].rolling(24, min_periods=24).min().shift(1)
        df["hh48"] = df["high"].rolling(48, min_periods=48).max().shift(1)
        df["ll48"] = df["low"].rolling(48, min_periods=48).min().shift(1)

        bb = ta.BBANDS(df, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        df["bb_up"] = bb["upperband"]
        df["bb_mid"] = bb["middleband"]
        df["bb_low"] = bb["lowerband"]

        df["vol_ma"] = df["volume"].rolling(24, min_periods=12).mean()
        df["vol_rel"] = df["volume"] / df["vol_ma"].replace(0.0, np.nan)

        rng = (df["high"] - df["low"]).replace(0.0, np.nan)
        df["body_pos"] = (df["close"] - df["low"]) / rng
        df["ret6"] = df["close"] / df["close"].shift(6) - 1.0
        df["dist_ema20"] = (df["close"] - df["ema20"]) / df["atr"].replace(0.0, np.nan)

        df = self._add_market_state(df)
        return df

    # ---- entries --------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["enter_tag"] = ""

        valid = (
            df["ema100"].notna()
            & df["atr_pct"].notna()
            & df["adx"].notna()
            & df["ema20_4h"].notna()
            & df["adx_4h"].notna()
            & df["hh24"].notna()
            & df["bb_mid"].notna()
        )
        atr_ok = (
            (df["atr_pct"] > 0.0025)
            & (df["atr_pct"] < 0.055)
            & (df["atr_pct"] < 3.5 * df["atr_pct_med"])
        )
        base = valid & atr_ok & (df["volume"] > 0)

        up4 = (df["close_4h"] > df["ema20_4h"]) & (df["ema20_4h"] > df["ema20_prev_4h"])
        dn4 = (df["close_4h"] < df["ema20_4h"]) & (df["ema20_4h"] < df["ema20_prev_4h"])
        up1 = (df["ema20"] > df["ema50"]) & (df["close"] > df["ema100"])
        dn1 = (df["ema20"] < df["ema50"]) & (df["close"] < df["ema100"])

        mkt_l = (df["btc_ret6"] > -0.025) & (df["btc_rsi"] > 33) & (df["btc_ret24"] > -0.07)
        mkt_s = (df["btc_ret6"] < 0.025) & (df["btc_rsi"] < 67) & (df["btc_ret24"] < 0.07)

        # --- breakout continuation ---
        bo_l = (
            base & up4 & up1 & mkt_l
            & (df["close"] > df["hh24"])
            & (df["close"] > df["open"])
            & (df["rsi"] > 51) & (df["rsi"] < 80)
            & (df["adx"] > 17)
            & (df["plus_di"] > df["minus_di"])
            & (df["macdhist"] > 0)
            & (df["vol_rel"] > 1.1)
            & (df["dist_ema20"] < 3.2)
        )
        bo_s = (
            base & dn4 & dn1 & mkt_s
            & (df["close"] < df["ll24"])
            & (df["close"] < df["open"])
            & (df["rsi"] < 49) & (df["rsi"] > 20)
            & (df["adx"] > 19)
            & (df["minus_di"] > df["plus_di"])
            & (df["macdhist"] < 0)
            & (df["vol_rel"] > 1.1)
            & (df["dist_ema20"] > -3.2)
        )

        # --- pullback resume ---
        pb_l = (
            base & up4 & mkt_l
            & (df["ema50"] > df["ema100"])
            & (df["close"] > df["ema50"])
            & (df["low"].shift(1) <= df["ema20"].shift(1) * 1.002)
            & (df["close"].shift(1) > df["ema100"].shift(1))
            & (df["close"] > df["open"])
            & (df["close"] > df["high"].shift(1))
            & (df["rsi"] > df["rsi"].shift(1))
            & (df["rsi"] < 72) & (df["rsi"].shift(1) < 55)
            & (df["adx"] > 14)
            & (df["vol_rel"] > 0.8)
            & (df["ret6"] > -0.05)
        )
        pb_s = (
            base & dn4 & mkt_s
            & (df["ema50"] < df["ema100"])
            & (df["close"] < df["ema50"])
            & (df["high"].shift(1) >= df["ema20"].shift(1) * 0.998)
            & (df["close"].shift(1) < df["ema100"].shift(1))
            & (df["close"] < df["open"])
            & (df["close"] < df["low"].shift(1))
            & (df["rsi"] < df["rsi"].shift(1))
            & (df["rsi"] > 28) & (df["rsi"].shift(1) > 45)
            & (df["adx"] > 16)
            & (df["vol_rel"] > 0.8)
            & (df["ret6"] < 0.05)
        )

        # --- quiet range reversion ---
        mr_l = (
            base & (~dn4) & mkt_l
            & (df["adx"] < 18) & (df["adx_4h"] < 22)
            & (df["close"] < df["bb_low"])
            & (df["rsi"] < 33)
            & (df["close"] > df["open"])
            & (df["body_pos"] > 0.5)
            & (df["close"] > df["ll48"])
            & (df["ret6"] > -0.045)
            & (df["vol_rel"] > 0.9)
        )
        mr_s = (
            base & (~up4) & mkt_s
            & (df["adx"] < 18) & (df["adx_4h"] < 22)
            & (df["close"] > df["bb_up"])
            & (df["rsi"] > 67)
            & (df["close"] < df["open"])
            & (df["body_pos"] < 0.5)
            & (df["close"] < df["hh48"])
            & (df["ret6"] < 0.045)
            & (df["vol_rel"] > 0.9)
        )

        df.loc[mr_l, ["enter_long", "enter_tag"]] = (1, "mr_l")
        df.loc[pb_l, ["enter_long", "enter_tag"]] = (1, "pb_l")
        df.loc[bo_l, ["enter_long", "enter_tag"]] = (1, "bo_l")
        df.loc[mr_s, ["enter_short", "enter_tag"]] = (1, "mr_s")
        df.loc[pb_s, ["enter_short", "enter_tag"]] = (1, "pb_s")
        df.loc[bo_s, ["enter_short", "enter_tag"]] = (1, "bo_s")
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # all signal exits are tag aware and handled in custom_exit
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ---- sizing ---------------------------------------------------------
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        stake = self._num(proposed_stake, 0.0)
        if stake <= 0:
            return 0.0
        factor = 0.95
        hist = self._closed_slice(pair, current_time)
        if hist is not None and len(hist) > 0:
            atr_pct = self._num(hist.iloc[-1]["atr_pct"], 0.0)
            if atr_pct > 0.0:
                factor *= min(1.0, max(0.5, 0.011 / atr_pct))
        dd, start, _rows = self._drawdown_state()
        if start > 0 and dd > 0.07 * start:
            factor *= 0.65
        stake = stake * factor
        if min_stake is not None and stake < min_stake:
            stake = float(min_stake)
        if max_stake is not None and max_stake > 0:
            stake = min(stake, float(max_stake))
        return float(max(stake, 0.0))

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(min(self.target_leverage, max(1.0, self._num(max_leverage, 1.0))))

    # ---- protective stop ------------------------------------------------
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                        current_profit: float, after_fill: bool, **kwargs) -> Optional[float]:
        hist = self._closed_slice(pair, current_time)
        if hist is None or len(hist) == 0:
            return None
        last = hist.iloc[-1]
        atr_pct = min(max(self._num(last["atr_pct"], 0.010), 0.003), 0.050)
        mr = (trade.enter_tag or "").startswith("mr")
        risk = self._risk_frac(atr_pct, mr)
        trail = (self.trail_mr_mult if mr else self.trail_mult) * atr_pct

        lev = self._num(trade.leverage, 1.0)
        if lev <= 0:
            lev = 1.0
        open_rate = self._num(trade.open_rate, 0.0)
        rate = self._num(current_rate, 0.0)
        if open_rate <= 0 or rate <= 0:
            return None

        n = max(1, int(self._held_hours(trade, current_time)))
        window = hist.tail(n)

        if trade.is_short:
            trough = self._num(window["low"].min(), rate)
            trough = min(trough, rate)
            p = (open_rate - rate) / open_rate
            stop = open_rate * (1.0 + risk)
            if p >= risk:
                stop = min(stop, open_rate * (1.0 - self.be_buffer))
            if p >= 1.5 * risk:
                stop = min(stop, trough * (1.0 + trail))
            rel = -lev * (stop / rate - 1.0)
        else:
            peak = self._num(window["high"].max(), rate)
            peak = max(peak, rate)
            p = (rate - open_rate) / open_rate
            stop = open_rate * (1.0 - risk)
            if p >= risk:
                stop = max(stop, open_rate * (1.0 + self.be_buffer))
            if p >= 1.5 * risk:
                stop = max(stop, peak * (1.0 - trail))
            rel = lev * (stop / rate - 1.0)

        if rel >= -0.0005:
            return None
        return float(max(rel, -0.9))

    # ---- discretionary exits -------------------------------------------
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        hist = self._closed_slice(pair, current_time)
        if hist is None or len(hist) == 0:
            return None
        last = hist.iloc[-1]
        atr_pct = min(max(self._num(last["atr_pct"], 0.010), 0.003), 0.050)
        tag = trade.enter_tag or ""
        mr = tag.startswith("mr")
        risk = self._risk_frac(atr_pct, mr)
        held = self._held_hours(trade, current_time)
        open_rate = self._num(trade.open_rate, 0.0)
        rate = self._num(current_rate, 0.0)
        if open_rate <= 0 or rate <= 0:
            return None
        short = bool(trade.is_short)
        p = (open_rate - rate) / open_rate if short else (rate - open_rate) / open_rate

        rsi = self._num(last["rsi"], 50.0)
        mid = self._num(last["bb_mid"], rate)
        c = self._num(last["close"], rate)
        o = self._num(last["open"], rate)
        e20 = self._num(last["ema20"], rate)
        e50 = self._num(last["ema50"], rate)
        btc6 = self._num(last["btc_ret6"], 0.0)

        if mr:
            if short:
                if rsi < 45 or rate <= mid:
                    return "mr_target"
            else:
                if rsi > 55 or rate >= mid:
                    return "mr_target"
            if held >= 16:
                return "mr_timeout"
            return None

        if short:
            if c > e50 and e20 > e50:
                return "trend_flip"
            if held >= 4 and p < -0.55 * risk and c > e20:
                return "failed_entry"
            if rsi < 20 and p > 2.5 * risk and c > o:
                return "blowoff"
            if btc6 > 0.035 and p < 0:
                return "mkt_risk_off"
        else:
            if c < e50 and e20 < e50:
                return "trend_flip"
            if held >= 4 and p < -0.55 * risk and c < e20:
                return "failed_entry"
            if rsi > 80 and p > 2.5 * risk and c < o:
                return "blowoff"
            if btc6 < -0.035 and p < 0:
                return "mkt_risk_off"

        if held >= 60 and p <= 0:
            return "stall"
        if held >= 150 and p < risk:
            return "stale"
        if held >= 336:
            return "max_hold"
        return None

    # ---- gatekeeper -----------------------------------------------------
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        try:
            open_trades = Trade.get_trades_proxy(is_open=True)
        except Exception:
            open_trades = []
        want_short = side == "short"
        same = 0
        for t in open_trades:
            if bool(t.is_short) == want_short:
                same += 1
        if same >= self.max_same_side:
            return False

        dd, start, rows = self._drawdown_state()
        if rows:
            try:
                gap = current_time - rows[-1].close_date
            except Exception:
                return True
            if dd > 0.18 * start and gap < timedelta(hours=72):
                return False
            if dd > 0.10 * start and gap < timedelta(hours=30):
                return False
            if len(rows) >= 3 and gap < timedelta(hours=8):
                if all(self._num(t.close_profit, 0.0) < 0 for t in rows[-3:]):
                    return False
        return True
