#!/usr/bin/env python3
"""Funding rates and the delta-neutral carry (funding/carry.py).
Run: python3 tests/test_funding_carry.py   No network: every venue is a stand-in and everything is written to a temp folder."""
import importlib.util, json, os, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.environ.update({"FUNDING_DATA": tmp + "/data", "FUNDING_PUBLIC": tmp + "/public", "FUNDING_PAUSE": "0", "FUNDING_TOP_N": "2"})
spec = importlib.util.spec_from_file_location("fc", os.path.join(REPO, "funding", "carry.py"))
fc = importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

H, D = fc.HOUR, fc.DAY
END = 1_794_912_000_000                       # 2026-11-17 00:00 UTC
hourly = lambda days, rate: [[END - i * H, rate] for i in range(days * 24, 0, -1)]

# 1. a steady 0.001% an hour is about 8.76% a year
rows = hourly(400, 0.00001)
a = fc.apr(rows, 365, END)
check("apr: 0.001% an hour is 8.76% a year", abs(a - 0.0876) < 1e-6, a)
check("apr: a window less than 90% covered is None", fc.apr(hourly(20, 0.00001), 30, END) is None)

# 2. the carry: costs once, compounds daily, no drop when funding never turns negative (beyond the entry cost)
c = fc.carry(rows, 365, END)
want = (1 - fc.ROUND_TRIP_COST) * (1 + 24 * 0.00001) ** 365 - 1
check("carry: 365 days of steady funding compounds daily, minus the round trip once", abs(c["return"] - want) < 2e-4, (c["return"], want))
check("carry: the only drop is the entry cost", abs(c["max_drawdown"] + fc.ROUND_TRIP_COST) < 1e-9, c["max_drawdown"])
check("carry: a weekly curve that ends on the last day", len(c["curve"]) >= 52 and c["curve"][-1][1] == round(c["return"], 5), c["curve"][-3:])

# 3. negative funding shows up as a drop
neg = hourly(400, 0.00001)
for r in neg[-60 * 24:-30 * 24]:
    r[1] = -0.00005                            # a month of shorts paying longs
c2 = fc.carry(neg, 365, END)
check("carry: a month of negative funding is a drop of about 3.6%", -0.045 < c2["max_drawdown"] < -0.03, c2["max_drawdown"])
check("carry: and a lower return than steady funding", c2["return"] < c["return"])

# 4. the stored history keeps KEEP_DAYS and drops duplicates
fc.now_ms = lambda: END
kept = fc.save("hyperliquid", "BTC", hourly(500, 0.00001) + hourly(10, 0.00001))
check("save: older than KEEP_DAYS is dropped and duplicates are merged", len(kept) == fc.KEEP_DAYS * 24, len(kept))
check("save: and it reads back the same", fc.load("hyperliquid", "BTC") == kept)

# 5. a full run with stand-in venues: one fails, the rest still publish
calls = []
def fake(rate):
    def fn(coin, since):
        calls.append(coin)
        return [p for p in hourly(400, rate) if p[0] > since]
    return fn
def broken(coin, since):
    raise RuntimeError("HTTP 451")
fc.VENUES = {"hyperliquid": (fake(0.00001), "Hyperliquid", "{c}"), "binance": (broken, "Binance", "{c}USDT"), "dydx": (fake(0.000005), "dYdX", "{c}-USD")}
fc.pick_coins = lambda: ["BTC", "ETH", "SOL", "HYPE"]
rc = fc.main()
out = json.load(open(tmp + "/public/carry.json")); st = json.load(open(tmp + "/public/status.json"))
check("run: publishes and exits 0 when a venue fails", rc == 0 and out["rows"], rc)
check("run: the failed venue is named in status.json, per coin", set(st["failed"]) == {f"binance:{c}" for c in ["BTC", "ETH", "SOL", "HYPE"]}, st["failed"])
check("run: dYdX only reads the core coins", sorted(r["coin"] for r in out["rows"] if r["venue"] == "dydx") == ["BTC", "ETH", "SOL"])
hl = [r for r in out["rows"] if r["venue"] == "hyperliquid" and r["coin"] == "BTC"][0]
check("run: a row has the funding rates, the carry and the interval", hl["interval_hours"] == 1 and hl["funding_apr_30d"] and hl["carry_365d"]["return"] > 0, hl)
check("run: best_365d is sorted by the year's carry", out["best_365d"][0]["venue"] == "hyperliquid", out["best_365d"][:2])
calls.clear(); fc.main()
check("run: a second run asks only for what is new", calls and all(c in ("BTC", "ETH", "SOL", "HYPE") for c in calls))

print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
