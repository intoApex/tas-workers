#!/usr/bin/env python3
"""What is inside a strategy, read without running it (strategy-lab/inside.py).
Run: python3 tests/test_inside.py   No Freqtrade and no network.

The rule this protects: the description is built from the file's own code and nothing else, the file is never executed,
and a value that isn't spelled out in the code is left out instead of guessed."""
import glob, importlib.util, os, re, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("inside", os.path.join(REPO, "strategy-lab", "inside.py"))
inside = importlib.util.module_from_spec(spec); spec.loader.exec_module(inside)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    ok = ok and bool(cond)

FULL = '''
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative

def EWO(df, a=5, b=35):
    return (ta.EMA(df, timeperiod=a) - ta.EMA(df, timeperiod=b)) / df["close"] * 100

class Base(IStrategy):
    timeframe = "15m"
    stoploss = -0.25
    trailing_stop = True

class Full(Base):
    timeframe = "5m"
    inf_1h = "1h"
    minimal_roi = {"0": 0.04, "30": 0.02, "120": 0}
    stoploss = -0.10
    use_custom_stoploss = True
    position_adjustment_enable = True
    max_entry_position_adjustment = 3
    can_short = True
    buy_rsi = IntParameter(10, 40, default=30, space="buy")
    sell_x = DecimalParameter(0.1, 0.9, default=0.5, space="sell")

    @informative("4h")
    def populate_indicators_4h(self, dataframe, metadata):
        dataframe["rsi"] = ta.RSI(dataframe)
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        bb = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["ewo"] = EWO(dataframe)
        dataframe["mfi"] = ta.MFI(dataframe); dataframe["adx"] = ta.ADX(dataframe)
        dataframe["cci"] = ta.CCI(dataframe); dataframe["sar"] = ta.SAR(dataframe)
        dataframe["hammer"] = ta.CDLHAMMER(dataframe)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe.loc[dataframe["rsi"] > 70, "exit_long"] = 1
        return dataframe

    def custom_stoploss(self, *a, **k): return -0.05
    def custom_exit(self, *a, **k): return None
    def adjust_trade_position(self, *a, **k): return None
'''
d = inside.describe(FULL, "Full")
check("the child's timeframe wins over the base class's", d.get("timeframe") == "5m", d)
check("other candle sizes come from the decorator and from attributes", sorted(d.get("other_timeframes", [])) == ["1h", "4h"], d.get("other_timeframes"))
check("indicators are named once each, in the order the code calls them",
      d["indicators"][:3] == ["RSI", "Bollinger Bands", "Elliott Wave Oscillator"], d["indicators"])
check("an indicator inside a helper function the class calls is found", "EMA" in d["indicators"], d["indicators"])
check("candlestick pattern functions are grouped", "Candlestick patterns" in d["indicators"], d["indicators"])
check("stop-loss is the child's number", d.get("stoploss") == -0.10, d.get("stoploss"))
check("trailing stop is inherited from the base class", d["trailing_stop"] is True)
check("custom stop-loss needs the switch and the method", d["custom_stoploss"] is True)
check("profit target: first, last, steps", d.get("roi") == {"first": 0.04, "last": 0.0, "steps": 3}, d.get("roi"))
check("exit signal, custom exit, shorting, position changes", d["exit_signal"] and d["custom_exit"] and d["can_short"] and d["adjusts_position"] and d.get("max_extra_buys") == 3, d)
check("tunable parameters are counted", d["tunable_parameters"] == 2, d["tunable_parameters"])
s = d["sentence"]
check("sentence: candles and other sizes", s.startswith("Reads 5-minute candles (and "), s)
check("sentence: names five indicators and counts the rest", " and 4 more." in s and "RSI, Bollinger Bands" in s, s)
check("sentence: how it sells", "Sells on its own sell signal, a custom exit rule and a profit target of 4% that falls to 0% the longer it holds." in s, s)
check("sentence: the stop", "Stop-loss at a 10% loss, moved by its own rule and trailing behind the price." in s, s)
check("sentence: extras", "(up to 3 extra buys)" in s and "Can bet on prices falling." in s, s)

OFF = '''
from freqtrade.strategy import IStrategy
class Hold(IStrategy):
    timeframe = "1h"
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        return dataframe
class NoStopValue(IStrategy):
    timeframe = "1d"
    minimal_roi = {"0": 0.015}
    stoploss = compute()
    use_exit_signal = False
    def populate_exit_trend(self, dataframe, metadata):
        dataframe.loc[dataframe["x"] > 1, "exit_long"] = 1
        return dataframe
class Legacy(IStrategy):
    ticker_interval = "30m"
    minimal_roi = {"0": 0.012}
    stoploss = -0.05
    def populate_sell_trend(self, dataframe, metadata):
        dataframe.loc[(dataframe["a"] > dataframe["b"]), "sell"] = 1
        return dataframe
'''
h = inside.describe(OFF, "Hold")
check("a 10,000% profit target reads as switched off", h.get("roi") == {"off": True}, h.get("roi"))
check("an exit column that is only ever set to 0 is no sell signal", h["exit_signal"] is False)
check("no exit, no target, 99% stop: says it keeps what it buys",
      h["sentence"] == "Reads 1-hour candles with its own maths, no standard indicator. Has no sell signal, no profit target and no working stop-loss, so it keeps what it buys.", h["sentence"])
n = inside.describe(OFF, "NoStopValue")
check("a stop-loss that isn't a plain number is left out, not guessed", "stoploss" not in n and "Stop-loss" not in n["sentence"], n)
check("use_exit_signal = False switches the sell signal off", n["exit_signal"] is False)
check("sentence without a stop names only what is known", n["sentence"] == "Reads daily candles with its own maths, no standard indicator. Sells on a profit target of 1.5%.", n["sentence"])
l = inside.describe(OFF, "Legacy")
check("older names are read: ticker_interval and populate_sell_trend", l.get("timeframe") == "30m" and l["exit_signal"] is True, l)
check("a single profit target has no 'falls to'", "Sells on its own sell signal and a profit target of 1.2%." in l["sentence"], l["sentence"])

# never executed: a file that would leave a mark if it ran
mark = os.path.join(tempfile.mkdtemp(), "ran")
EVIL = f'open({mark!r}, "w").write("x")\nfrom freqtrade.strategy import IStrategy\nclass E(IStrategy):\n    timeframe = "1h"\n    stoploss = -0.1\n'
e = inside.describe(EVIL, "E")
check("the source is parsed, never run", not os.path.exists(mark) and e.get("timeframe") == "1h")
check("broken source gives an error, not an exception", "error" in inside.describe("class X(:\n", "X"))
check("a class that isn't in the file gives an error", "error" in inside.describe(OFF, "Missing"))
check("a file over the size limit is not parsed", inside.describe("#" * (inside.MAX_CHARS + 1), "X") == {"error": "file too large to read"})
check("pct: 0.015 -> 1.5%, -0.1 -> 10%, 0.25 -> 25%", (inside._pct(0.015), inside._pct(-0.1), inside._pct(0.25)) == ("1.5%", "10%", "25%"))

# every strategy shipped in this repo gets a sentence
n_ok = n_all = 0
for f in sorted(glob.glob(os.path.join(REPO, "strategy-lab", "baselines*", "*.py"))):
    src = open(f).read()
    for c in re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", src, re.M):
        if c.startswith("_"): continue
        n_all += 1; r = inside.describe(src, c)
        n_ok += bool(r.get("sentence")) and bool(r.get("timeframe"))
check(f"all {n_all} baseline strategies in this repo are described with a timeframe", n_all > 30 and n_ok == n_all, f"{n_ok}/{n_all}")
fable = inside.describe(open(os.path.join(REPO, "strategy-lab", "baselines", "tas_guest_fable.py")).read(), "TASFableOnePass1h")
check("the guest strategy reads as its code says",
      fable["sentence"] == "Reads 1-hour candles with EMA and RSI. Sells on its own sell signal. Stop-loss at a 5% loss.", fable["sentence"])

# the lab attaches it to each result and a failure never stops a run
spec2 = importlib.util.spec_from_file_location("lab", os.path.join(REPO, "strategy-lab", "lab.py"))
lab = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(lab)
ud = tempfile.mkdtemp(); os.makedirs(os.path.join(ud, "strategies")); lab.UD = ud
open(os.path.join(ud, "strategies", "x.py"), "w").write(OFF)
check("lab.inside_of reads the installed file", lab.inside_of("x.py", "Legacy").get("timeframe") == "30m")
check("lab.inside_of on a missing file returns an error dict", "error" in lab.inside_of("nope.py", "Legacy"))

print("\nALL OK" if ok else "\nFAILED"); sys.exit(0 if ok else 1)
