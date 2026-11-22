from freqtrade.persistence import Trade
from freqtrade.strategy import (
    IStrategy,
    informative,
    stoploss_from_absolute,
    stoploss_from_open,
)
from pandas import DataFrame
import pandas as pd
import talib.abstract as ta


class TASLeaguePreGrok(IStrategy):
    """Trend-aligned pullbacks and breakouts, with crash shorts and small mean-reversion bets."""

    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = True
    startup_candle_count = 200

    process_only_new_candles = True
    position_adjustment_enable = False

    stoploss = -0.12
    use_custom_stoploss = True
    trailing_stop = False

    minimal_roi = {
        "0": 0.16,
        "8": 0.08,
        "20": 0.045,
        "40": 0.02,
        "72": 0.005,
    }

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 3},
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 24,
            "trade_limit": 3,
            "stop_duration_candles": 8,
            "only_per_pair": False,
        },
        {
            "method": "MaxDrawdown",
            "lookback_period_candles": 72,
            "trade_limit": 4,
            "stop_duration_candles": 24,
            "max_allowed_drawdown": 0.12,
        },
    ]

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema8"] = ta.EMA(dataframe, timeperiod=8)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        return dataframe

    @staticmethod
    def _col(dataframe: DataFrame, name: str, default: float = 0.0) -> pd.Series:
        if name in dataframe.columns:
            return dataframe[name]
        return pd.Series(default, index=dataframe.index)

    def _btc_features(self, dataframe: DataFrame) -> DataFrame:
        keys = (
            "btc_risk_on",
            "btc_risk_off",
            "btc_crash",
            "btc_pump",
            "btc_falling",
            "btc_rising",
            "btc_ret24",
        )
        if self.dp is None:
            for key in keys:
                dataframe[key] = 0
            return dataframe
        try:
            raw = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.timeframe)
        except Exception:
            raw = None
        if raw is None or len(raw) == 0 or "close" not in raw.columns or "date" not in raw.columns:
            for key in keys:
                dataframe[key] = 0
            return dataframe

        btc = raw[["date", "close"]].copy()
        btc["btc_ema21"] = ta.EMA(btc, timeperiod=21)
        btc["btc_ema55"] = ta.EMA(btc, timeperiod=55)
        btc["btc_slope"] = btc["btc_ema21"] - btc["btc_ema21"].shift(3)
        btc["btc_ret4"] = btc["close"].pct_change(4)
        btc["btc_ret24"] = btc["close"].pct_change(24)
        btc["btc_risk_on"] = (
            (btc["close"] > btc["btc_ema21"])
            & (btc["btc_slope"] > 0)
            & (btc["close"] > btc["btc_ema55"])
        ).astype(int)
        btc["btc_risk_off"] = (
            (btc["close"] < btc["btc_ema21"])
            & (btc["btc_slope"] < 0)
            & (btc["close"] < btc["btc_ema55"])
        ).astype(int)
        btc["btc_crash"] = (btc["btc_ret4"] <= -0.03).astype(int)
        btc["btc_pump"] = (btc["btc_ret4"] >= 0.03).astype(int)
        btc["btc_falling"] = (btc["btc_ret4"] <= -0.012).astype(int)
        btc["btc_rising"] = (btc["btc_ret4"] >= 0.012).astype(int)
        btc = btc[
            [
                "date",
                "btc_risk_on",
                "btc_risk_off",
                "btc_crash",
                "btc_pump",
                "btc_falling",
                "btc_rising",
                "btc_ret24",
            ]
        ]
        dataframe = dataframe.merge(btc, on="date", how="left")
        for key in keys:
            dataframe[key] = dataframe[key].fillna(0)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema55"] = ta.EMA(dataframe, timeperiod=55)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["vol_sma"] = dataframe["volume"].rolling(20).mean()
        dataframe["ret4"] = dataframe["close"].pct_change(4)
        dataframe["ret24"] = dataframe["close"].pct_change(24)
        dataframe["pullback_low"] = dataframe["low"].rolling(4).min()
        dataframe["pullback_high"] = dataframe["high"].rolling(4).max()
        dataframe["donch_high"] = dataframe["high"].rolling(36).max().shift(1)
        dataframe["donch_low"] = dataframe["low"].rolling(36).min().shift(1)

        boll = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        dataframe["bb_upper"] = boll["upperband"]
        dataframe["bb_mid"] = boll["middleband"]
        dataframe["bb_lower"] = boll["lowerband"]

        dataframe = self._btc_features(dataframe)
        dataframe["rs24"] = dataframe["ret24"] - dataframe["btc_ret24"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        risk_on = self._col(dataframe, "btc_risk_on") == 1
        risk_off = self._col(dataframe, "btc_risk_off") == 1
        crash = self._col(dataframe, "btc_crash") == 1
        pump = self._col(dataframe, "btc_pump") == 1
        falling = self._col(dataframe, "btc_falling") == 1
        rising = self._col(dataframe, "btc_rising") == 1
        h4_up = self._col(dataframe, "ema8_4h") > self._col(dataframe, "ema21_4h")
        h4_down = self._col(dataframe, "ema8_4h") < self._col(dataframe, "ema21_4h")
        rsi_4h = self._col(dataframe, "rsi_4h")

        liquid = dataframe["volume"] > 0
        vol_ok = dataframe["volume"] > dataframe["vol_sma"] * 0.65
        vol_strong = dataframe["volume"] > dataframe["vol_sma"] * 1.15
        body = (dataframe["close"] - dataframe["open"]).abs() / dataframe["open"]
        mild = body < 0.035
        bo_body = body < 0.06
        green = dataframe["close"] > dataframe["open"]
        red = dataframe["close"] < dataframe["open"]
        rsi_up = dataframe["rsi"] > dataframe["rsi"].shift(1)
        rsi_down = dataframe["rsi"] < dataframe["rsi"].shift(1)

        pb_long = (
            risk_on
            & ~crash
            & ~falling
            & h4_up
            & (rsi_4h > 40)
            & (rsi_4h < 76)
            & (dataframe["ema21"] > dataframe["ema55"])
            & (dataframe["ema9"] > dataframe["ema21"])
            & (dataframe["close"] > dataframe["ema55"])
            & (dataframe["adx"] > 18)
            & (dataframe["plus_di"] > dataframe["minus_di"])
            & (dataframe["rsi"] > 40)
            & (dataframe["rsi"] < 65)
            & rsi_up
            & (dataframe["close"] > dataframe["ema9"])
            & (dataframe["close"].shift(1) <= dataframe["ema9"].shift(1))
            & (dataframe["pullback_low"] <= dataframe["ema21"] * 1.012)
            & (dataframe["rs24"] > -0.02)
            & vol_ok
            & mild
            & green
            & liquid
        )
        bo_long = (
            risk_on
            & ~crash
            & ~falling
            & h4_up
            & (rsi_4h < 78)
            & (dataframe["close"] > dataframe["donch_high"])
            & (dataframe["close"].shift(1) <= dataframe["donch_high"].shift(1))
            & (dataframe["close"] > dataframe["ema21"])
            & (dataframe["plus_di"] > dataframe["minus_di"])
            & (dataframe["adx"] > 12)
            & (dataframe["rsi"] > 52)
            & (dataframe["rsi"] < 78)
            & (dataframe["rs24"] > -0.005)
            & vol_strong
            & bo_body
            & green
            & liquid
        )
        mr_long = (
            ~risk_off
            & ~crash
            & ~falling
            & (dataframe["rsi"] < 28)
            & rsi_up
            & (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["adx"] < 32)
            & (dataframe["volume"] > dataframe["vol_sma"] * 1.1)
            & green
            & mild
            & liquid
            & (dataframe["bb_mid"] > 0)
        )

        pb_short = (
            risk_off
            & ~pump
            & ~rising
            & h4_down
            & (rsi_4h < 60)
            & (rsi_4h > 24)
            & (dataframe["ema21"] < dataframe["ema55"])
            & (dataframe["ema9"] < dataframe["ema21"])
            & (dataframe["close"] < dataframe["ema55"])
            & (dataframe["adx"] > 18)
            & (dataframe["minus_di"] > dataframe["plus_di"])
            & (dataframe["rsi"] < 60)
            & (dataframe["rsi"] > 35)
            & rsi_down
            & (dataframe["close"] < dataframe["ema9"])
            & (dataframe["close"].shift(1) >= dataframe["ema9"].shift(1))
            & (dataframe["pullback_high"] >= dataframe["ema21"] * 0.988)
            & (dataframe["rs24"] < 0.02)
            & vol_ok
            & mild
            & red
            & liquid
        )
        bo_short = (
            risk_off
            & ~pump
            & ~rising
            & h4_down
            & (rsi_4h > 22)
            & (dataframe["close"] < dataframe["donch_low"])
            & (dataframe["close"].shift(1) >= dataframe["donch_low"].shift(1))
            & (dataframe["close"] < dataframe["ema21"])
            & (dataframe["minus_di"] > dataframe["plus_di"])
            & (dataframe["adx"] > 12)
            & (dataframe["rsi"] < 48)
            & (dataframe["rsi"] > 22)
            & (dataframe["rs24"] < 0.005)
            & vol_strong
            & bo_body
            & red
            & liquid
        )
        cr_short = (
            crash
            & ~pump
            & (dataframe["ret4"] < -0.015)
            & (dataframe["close"] < dataframe["ema21"])
            & (dataframe["close"] > dataframe["ema21"] - 4.0 * dataframe["atr"])
            & (dataframe["rsi"] > 18)
            & (dataframe["rsi"] < 48)
            & red
            & vol_ok
            & liquid
        )
        mr_short = (
            ~risk_on
            & ~pump
            & ~rising
            & (dataframe["rsi"] > 72)
            & rsi_down
            & (dataframe["close"] > dataframe["bb_upper"])
            & (dataframe["adx"] < 32)
            & (dataframe["volume"] > dataframe["vol_sma"] * 1.1)
            & red
            & mild
            & liquid
            & (dataframe["bb_mid"] > 0)
        )

        dataframe.loc[mr_long.fillna(False), "enter_long"] = 1
        dataframe.loc[mr_long.fillna(False), "enter_tag"] = "mr_long"
        dataframe.loc[pb_long.fillna(False), "enter_long"] = 1
        dataframe.loc[pb_long.fillna(False), "enter_tag"] = "pb_long"
        dataframe.loc[bo_long.fillna(False), "enter_long"] = 1
        dataframe.loc[bo_long.fillna(False), "enter_tag"] = "bo_long"

        dataframe.loc[mr_short.fillna(False), "enter_short"] = 1
        dataframe.loc[mr_short.fillna(False), "enter_tag"] = "mr_short"
        dataframe.loc[pb_short.fillna(False), "enter_short"] = 1
        dataframe.loc[pb_short.fillna(False), "enter_tag"] = "pb_short"
        dataframe.loc[bo_short.fillna(False), "enter_short"] = 1
        dataframe.loc[bo_short.fillna(False), "enter_tag"] = "bo_short"
        dataframe.loc[cr_short.fillna(False), "enter_short"] = 1
        dataframe.loc[cr_short.fillna(False), "enter_tag"] = "cr_short"

        both = (dataframe["enter_long"] == 1) & (dataframe["enter_short"] == 1)
        dataframe.loc[both, "enter_long"] = 0
        dataframe.loc[both, "enter_short"] = 0
        dataframe.loc[both, "enter_tag"] = ""

        dataframe["enter_long"] = dataframe["enter_long"].astype("int64")
        dataframe["enter_short"] = dataframe["enter_short"].astype("int64")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        crash = self._col(dataframe, "btc_crash") == 1
        pump = self._col(dataframe, "btc_pump") == 1
        bear_cross = (dataframe["ema9"] < dataframe["ema21"]) & (
            dataframe["ema9"].shift(1) >= dataframe["ema21"].shift(1)
        )
        bull_cross = (dataframe["ema9"] > dataframe["ema21"]) & (
            dataframe["ema9"].shift(1) <= dataframe["ema21"].shift(1)
        )

        dataframe.loc[(crash | bear_cross).fillna(False), "exit_long"] = 1
        dataframe.loc[(pump | bull_cross).fillna(False), "exit_short"] = 1
        dataframe["exit_long"] = dataframe["exit_long"].astype("int64")
        dataframe["exit_short"] = dataframe["exit_short"].astype("int64")
        return dataframe

    def _hours_open(self, trade: Trade, current_time) -> float:
        opened = trade.open_date_utc
        try:
            return (current_time - opened).total_seconds() / 3600.0
        except TypeError:
            return (
                current_time.replace(tzinfo=None) - opened.replace(tzinfo=None)
            ).total_seconds() / 3600.0

    def custom_stoploss(
        self,
        pair,
        trade,
        current_time,
        current_rate,
        current_profit,
        after_fill=False,
        **kwargs,
    ):
        hard = float(self.stoploss)
        if current_rate is None or current_rate <= 0:
            return hard
        lev = float(trade.leverage or 1.0)
        if lev <= 0:
            lev = 1.0
        tag = trade.enter_tag or ""

        atr_mult = 2.6
        min_price = current_rate * 0.008
        max_price = current_rate * 0.06
        if tag.startswith("bo_"):
            atr_mult = 3.2
        elif tag.startswith("mr_"):
            atr_mult = 1.7
            max_price = current_rate * 0.025
        elif tag.startswith("cr_"):
            atr_mult = 2.1
            max_price = current_rate * 0.04

        if current_profit > 0.08:
            atr_mult = min(atr_mult, 1.3)
        elif current_profit > 0.04:
            atr_mult = min(atr_mult, 1.8)

        atr = None
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is not None and len(dataframe) > 0:
                atr = float(dataframe["atr"].iloc[-1])
        except Exception:
            atr = None

        candidates = [hard]
        if atr is not None and atr > 0 and atr == atr:
            dist = min(max(atr_mult * atr, min_price), max_price)
            if trade.is_short:
                stop_rate = current_rate + dist
            else:
                stop_rate = current_rate - dist
            if stop_rate > 0:
                try:
                    candidates.append(
                        float(
                            stoploss_from_absolute(
                                stop_rate,
                                current_rate,
                                is_short=trade.is_short,
                                leverage=lev,
                            )
                        )
                    )
                except Exception:
                    pass

        lock = None
        if tag.startswith("mr_"):
            if current_profit > 0.02:
                lock = 0.006
        elif tag.startswith("bo_"):
            if current_profit > 0.12:
                lock = 0.06
            elif current_profit > 0.07:
                lock = 0.03
            elif current_profit > 0.045:
                lock = 0.005
        else:
            if current_profit > 0.08:
                lock = 0.04
            elif current_profit > 0.045:
                lock = 0.015
            elif current_profit > 0.028:
                lock = 0.002

        if lock is not None:
            try:
                candidates.append(
                    float(
                        stoploss_from_open(
                            lock,
                            current_profit,
                            is_short=trade.is_short,
                            leverage=lev,
                        )
                    )
                )
            except Exception:
                pass

        sl = max(candidates)
        if sl >= -0.001:
            sl = -0.001
        if sl < hard:
            sl = hard
        return float(sl)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        tag = trade.enter_tag or ""
        hours = self._hours_open(trade, current_time)
        lev = float(trade.leverage or 1.0)
        if lev <= 0:
            lev = 1.0

        if tag.startswith("mr_"):
            if current_profit >= 0.02:
                return "mr_tp"
            if hours >= 8 and current_profit < 0.008:
                return "mr_timeout"
        elif tag.startswith("cr_"):
            if hours >= 10 and current_profit < 0.008:
                return "cr_timeout"
        else:
            if hours >= 30 and current_profit < 0.005:
                return "time_flat"

        if hours >= 12 and current_profit < -0.045:
            return "early_cut"

        try:
            open_rate = float(trade.open_rate)
            if open_rate > 0:
                if trade.is_short:
                    favorable = float(trade.min_rate or current_rate)
                    mfe = ((open_rate - favorable) / open_rate) * lev
                else:
                    favorable = float(trade.max_rate or current_rate)
                    mfe = ((favorable - open_rate) / open_rate) * lev
                if mfe >= 0.07 and current_profit < 0.015:
                    return "giveback"
        except Exception:
            pass

        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return None
        if dataframe is None or len(dataframe) < 2:
            return None
        last = dataframe.iloc[-1]

        if "btc_crash" in dataframe.columns and not trade.is_short:
            if float(last["btc_crash"]) == 1.0:
                return "btc_crash"
        if "btc_pump" in dataframe.columns and trade.is_short:
            if float(last["btc_pump"]) == 1.0:
                return "btc_pump"

        rsi = last["rsi"]
        if rsi == rsi:
            if tag.startswith("mr_"):
                if not trade.is_short and current_profit > 0.008 and float(last["close"]) >= float(last["bb_mid"]):
                    return "mr_mid"
                if trade.is_short and current_profit > 0.008 and float(last["close"]) <= float(last["bb_mid"]):
                    return "mr_mid"
            else:
                if (
                    not trade.is_short
                    and current_profit > 0.03
                    and float(rsi) > 77
                    and float(last["close"]) < float(last["open"])
                ):
                    return "exhaustion"
                if (
                    trade.is_short
                    and current_profit > 0.03
                    and float(rsi) < 23
                    and float(last["close"]) > float(last["open"])
                ):
                    return "exhaustion"
        return None

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        try:
            stake = float(proposed_stake)
        except Exception:
            return proposed_stake
        tag = entry_tag or ""
        if tag.startswith("mr_"):
            stake *= 0.50
        elif tag.startswith("cr_"):
            stake *= 0.70
        if min_stake is not None:
            try:
                stake = max(stake, float(min_stake))
            except Exception:
                pass
        if max_stake is not None:
            try:
                stake = min(stake, float(max_stake))
            except Exception:
                pass
        return stake

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        tag = entry_tag or ""
        if tag.startswith("mr_"):
            lev = 1.0
        else:
            lev = 1.5
            try:
                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                atr = float(dataframe["atr"].iloc[-1])
                atr_pct = atr / float(current_rate)
                if atr_pct > 0.02:
                    lev = 1.0
                elif atr_pct > 0.012:
                    lev = 1.5
                else:
                    lev = 2.0
                if (
                    pair in ("BTC/USDT:USDT", "ETH/USDT:USDT")
                    and atr_pct < 0.01
                    and not tag.startswith("cr_")
                ):
                    lev = 2.5
            except Exception:
                lev = 1.5
        try:
            lev = min(lev, float(max_leverage))
        except Exception:
            pass
        if lev < 1.0:
            lev = 1.0
        if lev > 3.0:
            lev = 3.0
        return float(lev)

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        try:
            try:
                open_trades = list(Trade.get_open_trades())
            except Exception:
                open_trades = list(Trade.get_trades_proxy(is_open=True))
        except Exception:
            return True
        same = 0
        for open_trade in open_trades:
            if side == "short" and open_trade.is_short:
                same += 1
            elif side == "long" and not open_trade.is_short:
                same += 1
        if same >= 3:
            return False
        return True
