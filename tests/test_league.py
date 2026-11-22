#!/usr/bin/env python3
"""The AI league's retirement rule and wrapper, without Freqtrade. Run: python3 tests/test_league.py
league_outcome() decides what $1,000 became and whether a bot was retired (first liquidation, or more than 30% below its
peak with open positions at each hour's price); the wrapper the lab writes must load the model's file unchanged and cut
leverage to the cap. The full run (Freqtrade, made-up candles, a test season) is in strategy-lab/harness/run_lab.py."""
import importlib.util, os, sys, tempfile, types
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["LAB_MODE"] = "futures"
spec = importlib.util.spec_from_file_location("lab", os.path.join(ROOT, "strategy-lab", "lab.py"))
lab = importlib.util.module_from_spec(spec); spec.loader.exec_module(lab)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))

H = 3600000; T0 = 1795000000000 // H * H
PRICE = {}
def prices(pair):  # (time each close became known, close), one an hour
    p = PRICE[pair]; return (np.arange(len(p), dtype="int64") * H + T0 + H, np.array(p, dtype="float64"))
lab.hourly_prices = prices

def trade(pair, open_h, close_h, entry, exit_, amount, profit, reason="exit_signal", short=False, stake=None):
    return {"pair": pair, "open_timestamp": T0 + open_h * H, "close_timestamp": T0 + close_h * H, "is_short": short,
            "profit_abs": profit, "exit_reason": reason, "stake_amount": stake or entry * amount, "max_stake_amount": stake or entry * amount,
            "orders": [{"order_filled_timestamp": T0 + open_h * H, "ft_is_entry": True, "amount": amount, "safe_price": entry},
                       {"order_filled_timestamp": T0 + close_h * H, "ft_is_entry": False, "amount": amount, "safe_price": exit_}]}

def result(trades, hours):
    return {"backtest_start_ts": T0, "backtest_end_ts": T0 + hours * H, "starting_balance": 1000.0, "trades": trades}

# 1. a quiet winner: +$50 over 10 hours, never retired
PRICE["BTC/USDT:USDT"] = [100, 101, 102, 103, 104, 105, 104, 105, 106, 107, 110, 110]
o = lab.league_outcome(result([trade("BTC/USDT:USDT", 0, 10, 100, 110, 5, 50.0)], 11), 0.30)
check("a winner ends at $1,050 and is not retired", o["value"] == 1050.0 and "retired" not in o, o)

# 2. a position liquidated at hour 4: retired there, and a later win does not count
PRICE["ETH/USDT:USDT"] = [100, 95, 90, 80, 70, 120, 130, 130, 130, 130, 130, 130]
liq = trade("ETH/USDT:USDT", 0, 4, 100, 70, 3, -100.0, reason="liquidation", stake=100.0)
win = trade("BTC/USDT:USDT", 5, 10, 105, 110, 5, 25.0)
o = lab.league_outcome(result([liq, win], 11), 0.30)
check("a liquidation retires the bot", o.get("retired", "").startswith("liquidated: ETH/USDT:USDT long"), o)
check("its value is the wallet at that hour ($900), not the later win", o["value"] == 900.0, o)

# 3. a fall of more than 30% from the peak, with the position still open: retired at that hour
PRICE["SOL/USDT:USDT"] = [100, 120, 140, 100, 80, 60, 50, 50, 50, 50, 50, 50]
open_loser = trade("SOL/USDT:USDT", 0, 10, 100, 50, 5, -250.0, stake=500.0)  # 5 SOL at $100: wallet 1000 -> 1200 at the peak
o = lab.league_outcome(result([open_loser], 11), 0.30)
check("a 30% fall with the position still open retires the bot", o.get("retired") == "fell more than 30% from its peak", o)
check("it is retired at the first hour below 70% of the peak", o["value"] == 800.0 and 0.30 < o["worst_drop"] < 0.34, o)

# 4. a short that wins, and the 29% line: not retired
PRICE["XRP/USDT:USDT"] = [1.0, 1.1, 1.2, 1.0, 0.9, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8]
o = lab.league_outcome(result([trade("XRP/USDT:USDT", 0, 10, 1.0, 0.8, 1000, 200.0, short=True)], 11), 0.30)
check("a winning short is valued the right way round", o["value"] == 1200.0 and "retired" not in o and o["worst_drop"] < 0.3, o)

# 5. the wrapper: loads the model's file as it is, keeps every method, cuts leverage to 3x and leaves lower leverage alone
tmp = tempfile.mkdtemp()
fs = types.ModuleType("freqtrade"); st = types.ModuleType("freqtrade.strategy")
class IStrategy:
    def leverage(self, *a, **k): return 1.0
st.IStrategy = IStrategy; sys.modules["freqtrade"] = fs; sys.modules["freqtrade.strategy"] = st
model = os.path.join(tmp, "model.py")
open(model, "w").write("from freqtrade.strategy import IStrategy\nclass MyBot(IStrategy):\n    lev = 10.0\n"
                       "    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):\n"
                       "        return self.lev\n    def populate_indicators(self, df, metadata):\n        return 'mine'\n")
src = lab.SHIM.format(season="pre", rel="league/pre/model.py", sha="abc", model_class="MyBot", cls="TASLeaguePreX", cap=3.0,
                      modname="tas_league_pre_x", path=model)
ns = {}; exec(compile(src, "shim.py", "exec"), ns)
bot = ns["TASLeaguePreX"]()
kw = dict(pair="BTC/USDT:USDT", current_time=None, current_rate=1.0, proposed_leverage=1.0, max_leverage=20.0, entry_tag=None, side="long")
check("the wrapper cuts 10x to 3x", bot.leverage(**kw) == 3.0)
bot.lev = 2.0
check("and leaves 2x as it is", bot.leverage(**kw) == 2.0)
check("everything else is the model's own code", bot.populate_indicators(None, None) == "mine" and type(bot).__mro__[1].__name__ == "MyBot")
check("the model's file is not touched", "lev = 10.0" in open(model).read() and "min(" not in open(model).read())

# 6. the prompt's import list is stricter than the board's: random, ta, numba and arrow are refused for league bots
check("random is refused for a league bot", lab.league_imports_outside("import random\nimport numpy as np\n") == ["random"])
check("ta and arrow too", lab.league_imports_outside("import ta\nfrom arrow import now\n") == ["arrow", "ta"])
check("the allowed ones pass", lab.league_imports_outside("import talib.abstract as ta\nimport pandas_ta\nfrom datetime import datetime\n"
                                                          "from freqtrade.strategy import IStrategy, informative\nfrom technical import qtpylib\n") == [])


# what the lookahead check found is kept for the page, as Freqtrade wrote it (2026-11-22)
_csv = "/tmp/lookahead.csv"
def _fake_sh(cmd, timeout):
    open(_csv, "w").write("filename,strategy,has_bias,total_signals,biased_entry_signals,biased_exit_signals,biased_indicators\n"
                          "x.py,TASLeagueX,True,20,0,0,\"btc_rsi, btc_ret6\"\n")
    return 0, ""
_sh = lab.sh; lab.sh = _fake_sh
got = lab.lookahead("TASLeagueX", lab.dt.date(2026, 11, 1))
check("a flagged bot keeps what was flagged", got is True and lab.LA_DETAIL.get("biased_indicators") == "btc_rsi, btc_ret6"
      and lab.LA_DETAIL.get("biased_entry_signals") == "0", lab.LA_DETAIL)
def _fake_sh2(cmd, timeout):  # what Freqtrade 2026.10 does when it can't decide: a header-only file and a log line
    open(_csv, "w").write("filename,strategy,has_bias,total_signals,biased_entry_signals,biased_exit_signals,biased_indicators\n")
    return 0, "INFO - found 9 trades which is less than minimum_trade_amount 10. Cancelling this backtest lookahead bias test."
lab.sh = _fake_sh2
got = lab.lookahead("TASLeagueX", lab.dt.date(2026, 11, 1))
check("an undecided check keeps Freqtrade's reason", got is None
      and lab.LA_DETAIL.get("has_bias") == "not decided: 9 trades in the test window, the check needs 10", lab.LA_DETAIL)
lab.sh = _sh

print("FAILURES:", 0 if ok else "see above"); sys.exit(0 if ok else 1)
