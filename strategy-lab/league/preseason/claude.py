from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, merge_informative_pair, stoploss_from_absolute


class TASLeaguePreClaude(IStrategy):
    """
    Trend-following futures strategy (long and short) on 1h candles.

    * Entries: 48h breakout in the direction of the pair trend, or a pullback
      that reclaims the fast EMA inside a strong trend. BTC is used as a
      market regime filter and as a relative-strength benchmark.
    * Exits: ATR trailing stop (ratchet only), break-even lock, trend-flip
      signal and a stale-trade timer.
    * Risk: volatility-based position sizing, hard cap per position, and a
      drawdown brake that shrinks and finally halts new risk.
    """

    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = True

    # ROI is effectively disabled, winners are managed by the trailing stop.
    minimal_roi = {"0": 10.0}

    # Fail-safe stop (ratio includes leverage: 0.20 / 3x = ~6.7% price move).
    stoploss = -0.20
    use_custom_stoploss = True
    trailing_stop = False

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    process_only_new_candles = True
    startup_candle_count = 400
    position_adjustment_enable = False

    # ---- strategy constants -------------------------------------------------
    btc_pair = "BTC/USDT:USDT"
    lev = 3.0

    risk_per_trade = 0.015      # fraction of equity lost if the first stop is hit
    max_stake_frac = 0.19       # max margin per position as fraction of equity
    stop_atr = 3.0              # initial / base trailing distance in ATRs
    min_stop_pct = 0.012        # floor of the stop distance (fraction of price)
    max_stop_pct = 0.06         # ceiling of the stop distance (fraction of price)

    stale_hours = 36.0          # close losing trades older than this
    cooldown_win_h = 3.0        # pause on a pair after a winning trade
    cooldown_loss_h = 12.0      # pause on a pair after a losing trade

    # ------------------------------------------------------------------------
    def informative_pairs(self):
        return [(self.btc_pair, self.timeframe)]

    # ---- helpers -------------------------------------------------------------
    @staticmethod
    def _btc_frame(src: DataFrame) -> DataFrame:
        out = pd.DataFrame({"date": src["date"]})
        e50 = ta.EMA(src, timeperiod=50)
        e200 = ta.EMA(src, timeperiod=200)
        c = src["close"]
        up = (c > e200) & (e50 > e200)
        dn = (c < e200) & (e50 < e200)
        out["btc_trend"] = up.astype(int) - dn.astype(int)
        out["btc_roc"] = c / c.shift(72) - 1.0
        return out

    def _stop_dist(self, price: float, atr: float, mult: float) -> float:
        floor = self.min_stop_pct * price * (mult / self.stop_atr)
        dist = max(mult * atr, floor)
        return min(dist, self.max_stop_pct * price)

    def _equity(self) -> float:
        try:
            if self.wallets is not None:
                cur = self.config.get("stake_currency", "USDT")
                total = float(self.wallets.get_total(cur))
                if total > 0 and math.isfinite(total):
                    return total
        except Exception:
            pass
        return 1000.0

    def _drawdown(self, equity: float) -> float:
        """Realised drawdown from the realised equity peak (0.0 .. 1.0)."""
        try:
            closed = Trade.get_trades_proxy(is_open=False)
        except Exception:
            return 0.0
        items = []
        for t in closed:
            d = t.close_date_utc
            if d is None:
                continue
            items.append((d, float(t.close_profit_abs or 0.0)))
        if not items:
            return 0.0
        items.sort(key=lambda x: x[0])
        eq = equity
        peak = equity
        for _, p in reversed(items):
            eq -= p
            if eq > peak:
                peak = eq
        if peak <= 0:
            return 0.0
        return max(0.0, 1.0 - equity / peak)

    def _risk_scale(self) -> float:
        dd = self._drawdown(self._equity())
        if dd < 0.06:
            return 1.0
        if dd < 0.12:
            return 0.7
        if dd < 0.18:
            return 0.45
        if dd < 0.24:
            return 0.25
        return 0.0

    def _cooldown_ok(self, pair: str, current_time: datetime) -> bool:
        try:
            closed = Trade.get_trades_proxy(pair=pair, is_open=False)
            last_t = None
            last_p = 0.0
            for t in closed:
                d = t.close_date_utc
                if d is None:
                    continue
                if last_t is None or d > last_t:
                    last_t = d
                    last_p = float(t.close_profit or 0.0)
            if last_t is None:
                return True
            hours = (current_time - last_t).total_seconds() / 3600.0
            need = self.cooldown_loss_h if last_p < 0 else self.cooldown_win_h
            return hours >= need
        except Exception:
            return True

    def _last_candle(self, pair: str):
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return None
        if df is None or len(df) == 0:
            return None
        return df.iloc[-1]

    # ---- indicators ----------------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]

        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=24)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=96)
        dataframe["ema_long"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # Channel of the previous 48 closed candles (current candle excluded).
        dataframe["hh"] = dataframe["high"].rolling(48).max().shift(1)
        dataframe["ll"] = dataframe["low"].rolling(48).min().shift(1)
        dataframe["vol_mean"] = dataframe["volume"].rolling(24).mean().shift(1)
        dataframe["roc"] = dataframe["close"] / dataframe["close"].shift(72) - 1.0
        dataframe["rsi_min"] = dataframe["rsi"].rolling(6).min().shift(1)
        dataframe["rsi_max"] = dataframe["rsi"].rolling(6).max().shift(1)

        # BTC regime and benchmark momentum.
        merged = False
        if pair == self.btc_pair:
            own = self._btc_frame(dataframe)
            dataframe["btc_trend"] = own["btc_trend"].values
            dataframe["btc_roc"] = own["btc_roc"].values
            merged = True
        else:
            btc = None
            try:
                if self.dp is not None:
                    btc = self.dp.get_pair_dataframe(pair=self.btc_pair, timeframe=self.timeframe)
            except Exception:
                btc = None
            if btc is not None and len(btc) > 0:
                info = self._btc_frame(btc)
                dataframe = merge_informative_pair(
                    dataframe, info, self.timeframe, self.timeframe, ffill=True
                )
                suffix = "_" + self.timeframe
                dataframe = dataframe.rename(
                    columns={
                        "btc_trend" + suffix: "btc_trend",
                        "btc_roc" + suffix: "btc_roc",
                    }
                )
                drop_col = "date" + suffix
                if drop_col in dataframe.columns:
                    dataframe = dataframe.drop(columns=[drop_col])
                merged = True

        if not merged:
            dataframe["btc_trend"] = 0
            dataframe["btc_roc"] = dataframe["roc"]

        dataframe["btc_trend"] = dataframe["btc_trend"].fillna(0)
        dataframe["btc_roc"] = dataframe["btc_roc"].fillna(dataframe["roc"])

        return dataframe

    # ---- entries -------------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        close = df["close"]
        atr = df["atr"]

        live = (df["volume"] > 0) & (df["atr_pct"] > 0.0015) & (df["atr_pct"] < 0.03)
        rng_ok = (df["high"] - df["low"]) < 3.5 * atr
        vol_surge = df["volume"] > 1.2 * df["vol_mean"]

        up_trend = (
            (df["ema_fast"] > df["ema_slow"])
            & (close > df["ema_long"])
            & (df["ema_slow"] > df["ema_slow"].shift(12))
        )
        dn_trend = (
            (df["ema_fast"] < df["ema_slow"])
            & (close < df["ema_long"])
            & (df["ema_slow"] < df["ema_slow"].shift(12))
        )

        long_break = (
            live
            & rng_ok
            & vol_surge
            & up_trend
            & (close > df["hh"])
            & (df["adx"] > 20)
            & (df["rsi"] < 82)
            & ((close - df["ema_fast"]) < 5.0 * atr)
            & (df["btc_trend"] >= 0)
            & (df["roc"] > 0)
            & (df["roc"] >= df["btc_roc"])
        )

        short_break = (
            live
            & rng_ok
            & vol_surge
            & dn_trend
            & (close < df["ll"])
            & (df["adx"] > 20)
            & (df["rsi"] > 18)
            & ((df["ema_fast"] - close) < 5.0 * atr)
            & (df["btc_trend"] <= 0)
            & (df["roc"] < 0)
            & (df["roc"] <= df["btc_roc"])
        )

        long_pull = (
            live
            & rng_ok
            & (df["ema_fast"] > df["ema_slow"])
            & (df["ema_slow"] > df["ema_long"])
            & (close > df["ema_slow"])
            & (df["adx"] > 18)
            & (df["rsi_min"] < 42)
            & (close > df["ema_fast"])
            & (close.shift(1) <= df["ema_fast"].shift(1))
            & (df["btc_trend"] == 1)
            & (df["roc"] >= df["btc_roc"])
        )

        short_pull = (
            live
            & rng_ok
            & (df["ema_fast"] < df["ema_slow"])
            & (df["ema_slow"] < df["ema_long"])
            & (close < df["ema_slow"])
            & (df["adx"] > 18)
            & (df["rsi_max"] > 58)
            & (close < df["ema_fast"])
            & (close.shift(1) >= df["ema_fast"].shift(1))
            & (df["btc_trend"] == -1)
            & (df["roc"] <= df["btc_roc"])
        )

        df.loc[long_break, ["enter_long", "enter_tag"]] = (1, "breakout_long")
        df.loc[long_pull & ~long_break, ["enter_long", "enter_tag"]] = (1, "pullback_long")
        df.loc[short_break, ["enter_short", "enter_tag"]] = (1, "breakout_short")
        df.loc[short_pull & ~short_break, ["enter_short", "enter_tag"]] = (1, "pullback_short")

        return df

    # ---- exits ---------------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        has_vol = df["volume"] > 0

        exit_long = has_vol & (
            (df["ema_fast"] < df["ema_slow"])
            | ((df["btc_trend"] == -1) & (df["close"] < df["ema_slow"]))
        )
        exit_short = has_vol & (
            (df["ema_fast"] > df["ema_slow"])
            | ((df["btc_trend"] == 1) & (df["close"] > df["ema_slow"]))
        )

        df.loc[exit_long, "exit_long"] = 1
        df.loc[exit_short, "exit_short"] = 1
        return df

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        try:
            age_h = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        except Exception:
            return None
        if age_h >= self.stale_hours and current_profit < 0.0:
            return "stale"
        return None

    def custom_exit_price(self, pair: str, trade: Trade, current_time: datetime,
                          proposed_rate: float, current_profit: float,
                          exit_tag: Optional[str], **kwargs) -> float:
        # Protective exits are priced slightly through the market so they fill.
        tag = str(exit_tag or "")
        if ("stop" in tag) or ("liquid" in tag) or ("emergency" in tag):
            if trade.is_short:
                return proposed_rate * 1.001
            return proposed_rate * 0.999
        return proposed_rate

    # ---- stop management -----------------------------------------------------
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> Optional[float]:
        last = self._last_candle(pair)
        if last is None:
            return None
        try:
            atr = float(last["atr"])
            close = float(last["close"])
        except Exception:
            return None
        if not (math.isfinite(atr) and math.isfinite(close)):
            return None
        if atr <= 0 or close <= 0 or current_rate is None or current_rate <= 0:
            return None

        entry = float(trade.open_rate)
        is_short = bool(trade.is_short)
        lev = float(trade.leverage or 1.0)

        # Progress is measured on the last closed candle only.
        gain = (entry - close) if is_short else (close - entry)
        gain_atr = gain / atr

        mult = self.stop_atr
        if gain_atr >= 7.0:
            mult = 1.6
        elif gain_atr >= 4.0:
            mult = 2.2

        init_dist = self._stop_dist(entry, atr, self.stop_atr)
        trail_dist = self._stop_dist(close, atr, mult)

        if is_short:
            stop = min(entry + init_dist, close + trail_dist)
            if gain_atr >= 2.0:
                stop = min(stop, entry * (1.0 - 0.0015))
        else:
            stop = max(entry - init_dist, close - trail_dist)
            if gain_atr >= 2.0:
                stop = max(stop, entry * (1.0 + 0.0015))

        sl = stoploss_from_absolute(stop, current_rate, is_short=is_short, leverage=lev)
        if sl is None or not math.isfinite(sl):
            return None
        if sl < 0.001:
            # Price is already beyond the stop level: leave right away.
            return 0.001
        return sl

    # ---- sizing and leverage -------------------------------------------------
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: Optional[str],
                 side: str, **kwargs) -> float:
        return float(max(1.0, min(self.lev, max_leverage)))

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        scale = self._risk_scale()
        if scale <= 0:
            return 0.0

        equity = self._equity()

        stop_frac = 0.03
        last = self._last_candle(pair)
        if last is not None:
            try:
                atr = float(last["atr"])
                close = float(last["close"])
                if math.isfinite(atr) and math.isfinite(close) and atr > 0 and close > 0:
                    stop_frac = self._stop_dist(close, atr, self.stop_atr) / close
            except Exception:
                stop_frac = 0.03
        stop_frac = min(max(stop_frac, self.min_stop_pct), self.max_stop_pct)

        lev = float(leverage) if leverage and leverage > 0 else 1.0
        notional = equity * self.risk_per_trade * scale / stop_frac
        stake = notional / lev

        cap = equity * self.max_stake_frac
        stake = min(stake, cap)
        if max_stake is not None and max_stake > 0:
            stake = min(stake, float(max_stake))

        if min_stake is not None and stake < min_stake:
            limit = cap
            if max_stake is not None and max_stake > 0:
                limit = min(limit, float(max_stake))
            if min_stake <= limit:
                stake = float(min_stake)
            else:
                return 0.0

        if not math.isfinite(stake) or stake <= 0:
            return 0.0
        return float(stake)

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime,
                            entry_tag: Optional[str], side: str, **kwargs) -> bool:
        if self._risk_scale() <= 0:
            return False
        return self._cooldown_ok(pair, current_time)
