#!/usr/bin/env python3
"""The Strategy Lab's handling of a strategy Freqtrade will not run, and of new lab code (strategy-lab/lab.py).
Run: python3 tests/test_lab_refusals.py   No Freqtrade and no network: the command runner and the result reader are stand-ins.

Why this exists: Freqtrade 2026.10 logs "Configuration error: ..." and exits with code 0. The lab took code 0 to mean a
backtest ran, so 18 strategies sat on the site as running with every number empty from 2026-11-10 to 2026-11-12."""
import importlib.util, json, os, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("lab", os.path.join(REPO, "strategy-lab", "lab.py"))
lab = importlib.util.module_from_spec(spec); spec.loader.exec_module(lab)
import datetime as dt

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    ok = ok and bool(cond)

MARKET = ('2026-11-12 17:26:53,982 - freqtrade - ERROR - Configuration error: Market entry orders require entry_pricing.price_side = "other".\n'
          "Please make sure to review the documentation at https://www.freqtrade.io/en/stable.\n")
ZERO = ("2026-11-12 17:26:56,307 - freqtrade - ERROR - Configuration error: The config stoploss needs to be different from 0 to avoid problems with sell orders.\n"
        "Please make sure to review the documentation at https://www.freqtrade.io/en/stable.\n")
GOOD = {"timeframe": "1h", "total_trades": 7, "profit_total": 0.012, "profit_total_abs": 12.0, "max_drawdown_account": 0.03, "winrate": 0.57,
        "wins": 4, "losses": 3, "market_change": -0.01, "backtest_start": "2026-10-01 00:00:00", "backtest_end": "2026-11-12 00:00:00", "trades": []}
START = dt.date(2026, 10, 1)

def run(outputs, results, **kw):
    """backtest() with Freqtrade replaced: `outputs` are the (exit code, text) of each call in turn."""
    calls, reads = [], []
    def sh(args, timeout=0):
        calls.append(args); return outputs[len(calls) - 1]
    def parse():
        reads.append(1); return {"strategy": results}
    lab.sh, lab.parse_last_backtest, lab.marked_drawdown = sh, parse, (lambda s: None)
    return lab.backtest("Bot", START, **kw), calls, reads

check("the refusal is read from Freqtrade's own log line", lab.refusal(MARKET) == 'Market entry orders require entry_pricing.price_side = "other".', lab.refusal(MARKET))
check("clean output is not a refusal", lab.refusal("Dataload complete. Calculating indicators\n") is None and lab.refusal("") is None and lab.refusal(None) is None)
check("only the market-orders refusal asks for the market config", lab.wants_market(lab.refusal(MARKET)) and not lab.wants_market(lab.refusal(ZERO)) and not lab.wants_market(None))

r, calls, reads = run([(0, "ran fine")], {"Bot": GOOD})
check("a normal run: ok, numbers read, no note, no extra config", r["status"] == "ok" and r["trades"] == 7 and "orders_note" not in r and lab.MARKET_CFG not in calls[0], r)

r, calls, reads = run([(0, MARKET), (0, "ran fine")], {"Bot": GOOD})
check("market orders: run a second time with the market-orders config", len(calls) == 2 and lab.MARKET_CFG not in calls[0] and lab.MARKET_CFG in calls[1], calls)
check("market orders: the extra config comes after the lab's own, so it wins", calls[1].index(lab.MARKET_CFG) > calls[1].index(lab.CFG))
check("market orders: ok, with the note the site needs", r["status"] == "ok" and r.get("orders_note") == lab.MARKET_NOTE and r["profit_total"] == 0.012, r)

r, calls, reads = run([(0, "ran fine")], {"Bot": GOOD}, market=True)
check("a strategy already known to need it starts with the market config and keeps the note", len(calls) == 1 and lab.MARKET_CFG in calls[0] and r.get("orders_note") == lab.MARKET_NOTE)

r, calls, reads = run([(0, ZERO)], {"Other": GOOD})
check("any other refusal is an error that says why, in one call", r["status"] == "error" and "stoploss needs to be different from 0" in r["error"] and len(calls) == 1, r)
check("a refused run never reads the result file (it would be another strategy's)", reads == [])

r, calls, reads = run([(0, MARKET), (0, MARKET)], {"Bot": GOOD})
check("still refused with the market config: an error, not a loop", r["status"] == "error" and len(calls) == 2, (r, len(calls)))

r, calls, reads = run([(0, "ran fine")], {"Other": GOOD})
check("code 0 but no entry for this strategy: an error, not empty numbers", r["status"] == "error" and "without a result" in r["error"], r)
r, calls, reads = run([(0, "ran fine")], {"Bot": {}})
check("code 0 and an empty entry: an error too", r["status"] == "error", r)

r, calls, reads = run([(2, "Timeframe needs to be set in either configuration or as cli argument"), (0, MARKET), (0, "ran fine")], {"Bot": GOOD})
check("timeframe retry and market retry work together", r["status"] == "ok" and r.get("timeframe_note") and r.get("orders_note") and "--timeframe" in calls[2] and lab.MARKET_CFG in calls[2], (r, calls))

r, calls, reads = run([(124, "")], {"Bot": GOOD})
check("a timeout is still a timeout", r["status"] == "timeout")

cfg = json.load(open(os.path.join(REPO, "strategy-lab", "market-orders.json")))
check("market-orders.json sets both price sides to \"other\" and nothing else", cfg == {"entry_pricing": {"price_side": "other"}, "exit_pricing": {"price_side": "other"}}, cfg)
check("the market config sits next to lab.py, where both labs mount it", os.path.dirname(lab.MARKET_CFG) == os.path.join(REPO, "strategy-lab") and os.path.exists(lab.MARKET_CFG))
for name in ("config.json", "config-futures.json"):
    c = json.load(open(os.path.join(REPO, "strategy-lab", name)))
    check(f"{name} is still priced for limit orders (the reason the extra file exists)", c["entry_pricing"]["price_side"] == "same" and c["exit_pricing"]["price_side"] == "same")

d = tempfile.mkdtemp(); f = os.path.join(d, "lab.py")
open(f, "w").write("x = 1\n"); sig = lab.code_sig(f)
check("same code on disk: no restart", lab.code_changed(f, sig) is False)
open(f, "w").write("x = 2\n")
check("new code on disk: restart", lab.code_changed(f, sig) is True)
open(f, "w").write("def half_written(:\n")
check("a half-written file is left alone until it is whole", lab.code_changed(f, sig) is False)
check("a missing file or an unknown starting point: no restart", lab.code_changed(os.path.join(d, "gone.py"), sig) is False and lab.code_changed(f, None) is False)

src = open(os.path.join(REPO, "strategy-lab", "lab.py")).read()
check("the lab checks for new code between runs, before deciding whether a run is due", src.index("if code_changed(ME, LOADED)") < src.index("if due(last, rev, dt.datetime.utcnow())"))
print("\nALL OK" if ok else "\nFAILED"); sys.exit(0 if ok else 1)
