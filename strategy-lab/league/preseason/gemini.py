from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import talib
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, merge_informative_pair, stoploss_from_open


class TASLeaguePreGemini(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short: bool = True
    startup_candle_count: int = 120
    process_only_new_candles: bool = True

    minimal_roi = {
        "0": 0.45,
        "720": 0.25,
        "1440": 0.14,
        "2880": 0.05,
    }

    stoploss = -0.11
    use_custom_stoploss: bool = True

    use_exit_signal: bool = True
    exit_profit_only: bool = False
    ignore_roi_if_entry_signal: bool = False

    @property
    def protections(self):
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": 2,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 2,
                "stop_duration_candles": 8,
                "only_per_pair": True,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 12,
                "trade_limit": 4,
                "stop_duration_candles": 6,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 168,
                "trade_limit": 5,
                "stop_duration_candles": 12,
                "max_allowed_drawdown": 0.18,
            },
        ]

    def informative_pairs(self):
        if not self.dp:
            return []
        pairs = self.dp.current_whitelist()
        inf_pairs = [(pair, "4h") for pair in pairs]
        if ("BTC/USDT:USDT", "4h") not in inf_pairs:
            inf_pairs.append(("BTC/USDT:USDT", "4h"))
        return inf_pairs

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        return min(2.5, max_leverage)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        if self.dp:
            inf_4h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="4h")
            if not inf_4h.empty:
                close_4h = inf_4h["close"].astype(float)
                high_4h = inf_4h["high"].astype(float)
                low_4h = inf_4h["low"].astype(float)

                inf_4h_calc = pd.DataFrame(
                    {
                        "date": inf_4h["date"],
                        "close": close_4h,
                        "ema20": talib.EMA(close_4h, timeperiod=20),
                        "ema50": talib.EMA(close_4h, timeperiod=50),
                        "rsi": talib.RSI(close_4h, timeperiod=14),
                        "adx": talib.ADX(high_4h, low_4h, close_4h, timeperiod=14),
                    }
                )
                dataframe = merge_informative_pair(
                    dataframe, inf_4h_calc, self.timeframe, "4h", ffill=True
                )

            btc_4h = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="4h")
            if not btc_4h.empty:
                btc_close = btc_4h["close"].astype(float)
                btc_start = btc_4h["open"].astype(float)
                btc_calc = pd.DataFrame(
                    {
                        "date": btc_4h["date"],
                        "btc_close": btc_close,
                        "btc_ema50": talib.EMA(btc_close, timeperiod=50),
                        "btc_rsi": talib.RSI(btc_close, timeperiod=14),
                        "btc_chg": (btc_close - btc_start) / (btc_start + 1e-9),
                    }
                )
                dataframe = merge_informative_pair(
                    dataframe, btc_calc, self.timeframe, "4h", ffill=True
                )

        required_inf_cols = [
            "close_4h",
            "ema20_4h",
            "ema50_4h",
            "rsi_4h",
            "adx_4h",
            "btc_close_4h",
            "btc_ema50_4h",
            "btc_rsi_4h",
            "btc_chg_4h",
        ]
        for col in required_inf_cols:
            if col not in dataframe.columns:
                dataframe[col] = np.nan

        close_1h = dataframe["close"].astype(float)
        high_1h = dataframe["high"].astype(float)
        low_1h = dataframe["low"].astype(float)
        vol_1h = dataframe["volume"].astype(float)

        dataframe["ema9"] = talib.EMA(close_1h, timeperiod=9)
        dataframe["ema21"] = talib.EMA(close_1h, timeperiod=21)
        dataframe["ema55"] = talib.EMA(close_1h, timeperiod=55)

        dataframe["rsi"] = talib.RSI(close_1h, timeperiod=14)
        dataframe["adx"] = talib.ADX(high_1h, low_1h, close_1h, timeperiod=14)
        dataframe["plus_di"] = talib.PLUS_DI(high_1h, low_1h, close_1h, timeperiod=14)
        dataframe["minus_di"] = talib.MINUS_DI(high_1h, low_1h, close_1h, timeperiod=14)
        dataframe["atr"] = talib.ATR(high_1h, low_1h, close_1h, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / (close_1h + 1e-9)

        bb_mid = close_1h.rolling(20).mean()
        bb_std = close_1h.rolling(20).std()
        dataframe["bb_upper"] = bb_mid + 2.0 * bb_std
        dataframe["bb_lower"] = bb_mid - 2.0 * bb_std

        kc_mid = dataframe["ema21"]
        kc_range = 1.5 * dataframe["atr"]
        sqz_now = (dataframe["bb_upper"] < (kc_mid + kc_range)) & (
            dataframe["bb_lower"] > (kc_mid - kc_range)
        )
        dataframe["recent_sqz"] = sqz_now.shift(1).rolling(5).max() > 0

        dataframe["donchian_high_18"] = high_1h.shift(1).rolling(18).max()
        dataframe["donchian_low_18"] = low_1h.shift(1).rolling(18).min()

        dataframe["vol_sma20"] = vol_1h.rolling(20).mean()
        dataframe["candle_pos"] = (close_1h - low_1h) / (high_1h - low_1h + 1e-9)
        dataframe["recent_low_3"] = low_1h.rolling(3).min()
        dataframe["recent_high_3"] = high_1h.rolling(3).max()

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        valid_market = (
            (dataframe["volume"] > 0)
            & (dataframe["atr_pct"] > 0.0035)
            & (dataframe["atr_pct"] < 0.070)
        )

        trend_up_4h = (
            (dataframe["close_4h"] > dataframe["ema50_4h"])
            & (dataframe["ema20_4h"] > dataframe["ema50_4h"] * 0.995)
            & (dataframe["rsi_4h"] > 48.0)
        )
        trend_dn_4h = (
            (dataframe["close_4h"] < dataframe["ema50_4h"])
            & (dataframe["ema20_4h"] < dataframe["ema50_4h"] * 1.005)
            & (dataframe["rsi_4h"] < 52.0)
        )

        btc_ok_long = (dataframe["btc_chg_4h"] > -0.030) & (
            (dataframe["btc_close_4h"] > dataframe["btc_ema50_4h"] * 0.98)
            | (dataframe["btc_rsi_4h"] > 45.0)
        )
        btc_ok_short = (dataframe["btc_chg_4h"] < 0.030) & (
            (dataframe["btc_close_4h"] < dataframe["btc_ema50_4h"] * 1.02)
            | (dataframe["btc_rsi_4h"] < 55.0)
        )

        long_breakout = (
            valid_market
            & trend_up_4h
            & btc_ok_long
            & (dataframe["close"] > dataframe["donchian_high_18"])
            & (dataframe["ema9"] > dataframe["ema21"])
            & (dataframe["rsi"] > 54.0)
            & (dataframe["rsi"] < 78.0)
            & (dataframe["adx"] > 18.0)
            & (dataframe["plus_di"] > dataframe["minus_di"])
            & (dataframe["volume"] > dataframe["vol_sma20"] * 1.15)
            & (dataframe["candle_pos"] > 0.60)
        )

        long_squeeze = (
            valid_market
            & trend_up_4h
            & btc_ok_long
            & dataframe["recent_sqz"]
            & (dataframe["close"] > dataframe["bb_upper"])
            & (dataframe["ema21"] > dataframe["ema55"])
            & (dataframe["rsi"] > 55.0)
            & (dataframe["rsi"] < 76.0)
            & (dataframe["plus_di"] > dataframe["minus_di"])
            & (dataframe["volume"] > dataframe["vol_sma20"] * 1.10)
            & (dataframe["candle_pos"] > 0.60)
        )

        long_pullback = (
            valid_market
            & trend_up_4h
            & (dataframe["adx_4h"] > 20.0)
            & (dataframe["rsi_4h"] > 52.0)
            & btc_ok_long
            & (dataframe["ema21"] > dataframe["ema55"])
            & (dataframe["close"] > dataframe["ema55"])
            & (dataframe["recent_low_3"] <= dataframe["ema21"] * 1.003)
            & (dataframe["close"] > dataframe["ema21"])
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["rsi"] > 46.0)
            & (dataframe["rsi"] < 66.0)
            & (dataframe["rsi"] > dataframe["rsi"].shift(1) + 1.5)
            & (dataframe["candle_pos"] > 0.55)
        )

        short_breakout = (
            valid_market
            & trend_dn_4h
            & btc_ok_short
            & (dataframe["close"] < dataframe["donchian_low_18"])
            & (dataframe["ema9"] < dataframe["ema21"])
            & (dataframe["rsi"] < 46.0)
            & (dataframe["rsi"] > 22.0)
            & (dataframe["adx"] > 18.0)
            & (dataframe["minus_di"] > dataframe["plus_di"])
            & (dataframe["volume"] > dataframe["vol_sma20"] * 1.15)
            & (dataframe["candle_pos"] < 0.40)
        )

        short_squeeze = (
            valid_market
            & trend_dn_4h
            & btc_ok_short
            & dataframe["recent_sqz"]
            & (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["ema21"] < dataframe["ema55"])
            & (dataframe["rsi"] < 45.0)
            & (dataframe["rsi"] > 24.0)
            & (dataframe["minus_di"] > dataframe["plus_di"])
            & (dataframe["volume"] > dataframe["vol_sma20"] * 1.10)
            & (dataframe["candle_pos"] < 0.40)
        )

        short_pullback = (
            valid_market
            & trend_dn_4h
            & (dataframe["adx_4h"] > 20.0)
            & (dataframe["rsi_4h"] < 48.0)
            & btc_ok_short
            & (dataframe["ema21"] < dataframe["ema55"])
            & (dataframe["close"] < dataframe["ema55"])
            & (dataframe["recent_high_3"] >= dataframe["ema21"] * 0.997)
            & (dataframe["close"] < dataframe["ema21"])
            & (dataframe["close"] < dataframe["open"])
            & (dataframe["rsi"] < 54.0)
            & (dataframe["rsi"] > 34.0)
            & (dataframe["rsi"] < dataframe["rsi"].shift(1) - 1.5)
            & (dataframe["candle_pos"] < 0.45)
        )

        dataframe.loc[long_pullback, ["enter_long", "enter_tag"]] = (1, "long_pullback")
        dataframe.loc[long_squeeze, ["enter_long", "enter_tag"]] = (1, "long_squeeze")
        dataframe.loc[long_breakout, ["enter_long", "enter_tag"]] = (1, "long_breakout")

        dataframe.loc[short_pullback, ["enter_short", "enter_tag"]] = (1, "short_pullback")
        dataframe.loc[short_squeeze, ["enter_short", "enter_tag"]] = (1, "short_squeeze")
        dataframe.loc[short_breakout, ["enter_short", "enter_tag"]] = (1, "short_breakout")

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = ""

        exit_long_trend = (
            (dataframe["close"] < dataframe["ema55"])
            & (dataframe["ema9"] < dataframe["ema21"])
            & (dataframe["rsi"] < 43.0)
        )
        exit_long_climax = (dataframe["rsi"] > 82.0) & (dataframe["close"] < dataframe["open"])

        exit_short_trend = (
            (dataframe["close"] > dataframe["ema55"])
            & (dataframe["ema9"] > dataframe["ema21"])
            & (dataframe["rsi"] > 57.0)
        )
        exit_short_climax = (dataframe["rsi"] < 18.0) & (dataframe["close"] > dataframe["open"])

        dataframe.loc[exit_long_trend, ["exit_long", "exit_tag"]] = (1, "long_trend_lost")
        dataframe.loc[exit_long_climax, ["exit_long", "exit_tag"]] = (1, "long_rsi_climax")

        dataframe.loc[exit_short_trend, ["exit_short", "exit_tag"]] = (1, "short_trend_lost")
        dataframe.loc[exit_short_climax, ["exit_short", "exit_tag"]] = (1, "short_rsi_climax")

        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        hours_held = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if hours_held >= 60.0 and -0.015 < current_profit < 0.025:
            return "stale_trade_timeout"
        return None

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool = False,
        **kwargs,
    ) -> Optional[float]:
        lev = trade.leverage if trade.leverage else 1.0

        if current_profit >= 0.24:
            sfo = stoploss_from_open(0.15, current_profit, is_short=trade.is_short, leverage=lev)
            return max(-abs(sfo), -0.055)

        if current_profit >= 0.14:
            sfo = stoploss_from_open(0.075, current_profit, is_short=trade.is_short, leverage=lev)
            return max(-abs(sfo), -0.065)

        if current_profit >= 0.075:
            sfo = stoploss_from_open(0.015, current_profit, is_short=trade.is_short, leverage=lev)
            return max(-abs(sfo), -0.075)

        hours_held = (current_time - trade.open_date_utc).total_seconds() / 3600.0
        if hours_held >= 30.0 and current_profit < 0.02:
            return -0.065

        return self.stoploss
