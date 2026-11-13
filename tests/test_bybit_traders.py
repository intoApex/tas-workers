#!/usr/bin/env python3
"""bybit/traders.py against a stand-in for Bybit's list. Run: python3 tests/test_bybit_traders.py
The route's values had not been seen when the collector was written, so the main thing checked here is that it never
publishes a figure of ours unless its reading of the curve lands on Bybit's own 30-day ROI."""
import datetime as dt, importlib.util, json, os, shutil, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
os.environ.update({"BYBIT_TRADERS_DATA": tmp + "/data", "BYBIT_TRADERS_PUBLIC": tmp + "/public"})
spec = importlib.util.spec_from_file_location("bt", os.path.join(ROOT, "bybit", "traders.py"))
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
bt.time.sleep = lambda s: None
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
jl = lambda p: json.load(open(p))
NOW = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.timezone.utc)
D0 = dt.date(2026, 11, 13)

def running(daily):
    g, out = 1.0, []
    for r in daily:
        g *= 1 + r
        out.append(g - 1)
    return out

def row(mark, daily, shape="pct_ms", end=D0, roi=None, dd=0.05, name=None):
    """One trader. shape says how the 30 curve points are written."""
    c = running(daily)
    days = [end - dt.timedelta(days=len(daily) - 1 - i) for i in range(len(daily))]
    if shape == "pct_ms":        # running ROI in percent, as strings; dates as epoch milliseconds
        yt = [{"statisticDate": str(int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)), "trendValue": f"{v * 100:.4f}"} for d, v in zip(days, c)]
    elif shape == "e4_iso":      # running ROI as E4 integers; ISO dates
        yt = [{"statisticDate": d.isoformat(), "trendValue": round(v * 10000)} for d, v in zip(days, c)]
    elif shape == "daily_e4":    # each day's yield as E4 integers; YYYYMMDD
        yt = [{"statisticDate": d.strftime("%Y%m%d"), "trendValue": round(r * 10000)} for d, r in zip(days, daily)]
    elif shape == "usdt":        # running profit in dollars: not an ROI at any scale
        yt = [{"statisticDate": d.isoformat(), "trendValue": f"{52_000 + 900 * i:.2f}"} for i, d in enumerate(days)]
    r = c[-1] if roi is None else roi
    return {"leaderMark": mark, "nickname": name or f"trader-{mark}", "avatar": "x", "leaderLevel": "GOLD", "currentFollowerCount": "120", "maxFollowerCount": 500,
            "minInvestmentE8": "5000000000", "thirtyDayRoi": f"{r * 100:.2f}%", "thirtyDayRoiE4": str(round(r * 10000)), "thirtyDayMaxDrawdown": f"{dd * 100:.2f}%",
            "thirtyDayMaxDrawdownE4": str(round(dd * 10000)), "thirtyDayReturnVolatilityE4": "321", "thirtyDaySharpeRatio": "2.10", "thirtyDaySharpeRatioE4": "21000",
            "thirtyDayWeekTradeCountE2": "1250", "thirtyDayWinRateE4": "6400", "yieldTrend": yt}

steady = [0.004] * 30
crash = [0.01] * 12 + [-0.40] + [0.02] * 17
ANS = {}
bt.get = lambda path, tries=3: ANS["v"]
wrap = lambda rows: {"retCode": 0, "retMsg": "success", "result": {"title": "Recommended", "list": rows}}

# ---------- day one: running ROI in percent ----------
ANS["v"] = wrap([row("A", steady), row("B", crash, dd=0.20), row("C", [0.002] * 30)])
st = bt.run(NOW)
d = jl(tmp + "/public/traders.json"); t = {x["mark"]: x for x in d["traders"]}
check("the run reports what it did", st["ok"] and st["traders"] == 3 and st["with_own_figures"] == 3, st)
check("the curve is read as a running ROI in percent, and it lands on Bybit's ROI for all three", d["curve"] == {"reading": {"kind": "running", "scale": 100}, "matched": 3, "of": 3}, d["curve"])
check("Bybit's own figures are kept as fractions", abs(t["A"]["bybit"]["roi_30d"] - running(steady)[-1]) < 1e-3 and t["B"]["bybit"]["max_drawdown_30d"] == -0.20
      and t["A"]["bybit"]["sharpe_30d"] == 2.1 and t["A"]["bybit"]["win_rate_30d"] == 0.64 and t["A"]["bybit"]["trades_per_week"] == 12.5, t["A"]["bybit"])
check("followers and the minimum investment are read", t["A"]["followers"] == 120 and t["A"]["max_followers"] == 500 and t["A"]["min_investment_usd"] == 50.0, t["A"])
check("a steady curve has no drop", t["A"]["own"]["worst_drop_30d"] == 0 and t["A"]["status"] == "held", t["A"]["own"])
check("one day at -40% is the worst drop from the curve, deeper than Bybit's own 20%, and the gate uses ours",
      abs(t["B"]["own"]["worst_drop_30d"] + 0.40) < 0.005 and t["B"]["status"] == "high_drawdown" and d["summary"]["own_drop_deeper_than_bybit"] == 1, (t["B"]["own"], d["summary"]))
check("the curve's dates are read from epoch milliseconds", t["A"]["own"]["to"] == "2026-11-13" and t["A"]["own"]["from"] == "2026-10-15" and t["A"]["own"]["points"] == 30, t["A"]["own"])
check("rank, first seen and first rank are set, and nothing is known yet about the days after", t["B"]["rank"] == 2 and t["B"]["first_seen"] == "2026-11-14" and t["B"]["first_rank"] == 2 and t["B"]["since_seen"] is None)
check("the file says it is Bybit's recommendation and not a leaderboard", "not a full leaderboard" in d["rules"]["what"] and d["list"] == {"title": "Recommended", "n": 3})
check("the formats in the status carry no name, mark or avatar", "nickname" not in json.dumps(st["formats"]) and "leaderMark" not in json.dumps(st["formats"]) and st["formats"]["yieldTrend_n"] == 30)

# ---------- day two: one more day, one trader gone, one new ----------
NOW2 = NOW + dt.timedelta(days=1)
ANS["v"] = wrap([row("A", steady[1:] + [-0.05], end=D0 + dt.timedelta(days=1)), row("D", [0.003] * 30, end=D0 + dt.timedelta(days=1)), row("C", [0.002] * 30, end=D0 + dt.timedelta(days=1))])
bt.run(NOW2)
d2 = jl(tmp + "/public/traders.json"); t2 = {x["mark"]: x for x in d2["traders"]}; w2 = {x["mark"]: x for x in d2["watched"]}
check("first seen and first rank never change; the new trader starts today", t2["A"]["first_seen"] == "2026-11-14" and t2["A"]["first_rank"] == 1 and t2["D"]["first_seen"] == "2026-11-15" and t2["D"]["first_rank"] == 2)
s2 = t2["A"]["since_seen"] or {}
check("the day we first saw a trader is the first day of its record with us: one day, -5%",
      s2.get("from") == "2026-11-14" and s2.get("to") == "2026-11-14" and s2.get("days") == 1 and abs(s2.get("return", 0) + 0.05) < 1e-4 and abs(s2.get("max_drawdown", 0) + 0.05) < 1e-4, s2)
check("a trader Bybit stopped recommending is still listed as watched", list(w2) == ["B"] and w2["B"]["on_list"] is False and w2["B"]["first_rank"] == 2 and d2["summary"]["off_the_list"] == 1, w2)
check("days watched counts from the first day", t2["A"]["days_watched"] == 1 and t2["D"]["days_watched"] == 0)

# ---------- other ways the curve may be written ----------
shutil.rmtree(tmp + "/data", ignore_errors=True)
ANS["v"] = wrap([row("A", steady, "e4_iso"), row("B", crash, "e4_iso", dd=0.45)])
bt.run(NOW); d3 = jl(tmp + "/public/traders.json")
check("a running ROI written as E4 integers with ISO dates is read the same way", d3["curve"]["reading"] == {"kind": "running", "scale": 10000} and abs({x["mark"]: x for x in d3["traders"]}["B"]["own"]["worst_drop_30d"] + 0.40) < 0.005, d3["curve"])
shutil.rmtree(tmp + "/data", ignore_errors=True)
ANS["v"] = wrap([row("A", steady, "daily_e4"), row("B", crash, "daily_e4")])
bt.run(NOW); d4 = jl(tmp + "/public/traders.json")
check("daily yields are recognised as daily, not as a running ROI", d4["curve"]["reading"] == {"kind": "daily", "scale": 10000} and abs({x["mark"]: x for x in d4["traders"]}["B"]["own"]["worst_drop_30d"] + 0.40) < 0.005, d4["curve"])
shutil.rmtree(tmp + "/data", ignore_errors=True)
ANS["v"] = wrap([row("A", steady, "usdt"), row("B", crash, "usdt", dd=0.20), row("C", steady, "usdt", dd=0.35)])
st5 = bt.run(NOW); d5 = jl(tmp + "/public/traders.json"); t5 = {x["mark"]: x for x in d5["traders"]}
check("a curve that is not an ROI at any scale gives no figure of ours", st5["ok"] and d5["curve"]["reading"] is None and all(x["own"] is None for x in d5["traders"]) and d5["summary"]["with_own_figures"] == 0, d5["curve"])
check("then the gate falls back to Bybit's own max drawdown", t5["A"]["status"] == "held" and t5["C"]["status"] == "high_drawdown", (t5["A"]["status"], t5["C"]["status"]))
shutil.rmtree(tmp + "/data", ignore_errors=True)
ANS["v"] = wrap([row("A", steady), row("B", crash), row("C", steady, roi=0.90), row("E", steady)])
bt.run(NOW); d6 = jl(tmp + "/public/traders.json"); t6 = {x["mark"]: x for x in d6["traders"]}
check("one trader whose curve does not land on its own ROI gets no figure of ours; the others do", d6["curve"]["matched"] == 3 and d6["curve"]["of"] == 4 and t6["C"]["own"] is None and t6["A"]["own"] is not None, d6["curve"])
ANS["v"] = wrap([row("A", [0.0] * 30), row("B", crash)])
bt.run(NOW); d7 = jl(tmp + "/public/traders.json")
check("a flat curve can't pick the scale for the list", d7["curve"]["reading"] == {"kind": "running", "scale": 100}, d7["curve"])

# ---------- bad days ----------
before = open(tmp + "/public/traders.json").read()
ANS["v"] = {"retCode": 10001, "retMsg": "params error", "result": {}}
rc = bt.main(); s8 = jl(tmp + "/public/status.json")
check("a refusal from Bybit fails the run and says what it said", rc == 1 and s8["ok"] is False and "10001" in s8["error"] and "params error" in s8["error"], s8)
check("and yesterday's file stays as it was", open(tmp + "/public/traders.json").read() == before)
ANS["v"] = wrap([])
rc = bt.main()
check("an empty list fails the run too", rc == 1 and "no rows" in jl(tmp + "/public/status.json")["error"])
ANS["v"] = wrap([row("A", steady)])
rc = bt.main(); s9 = jl(tmp + "/public/status.json")
check("a good run writes the status with the curve reading and the formats", rc == 0 and s9["ok"] and s9["curve"]["reading"] == {"kind": "running", "scale": 100} and "fields" in s9["formats"], s9)

shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
