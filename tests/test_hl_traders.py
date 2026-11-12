#!/usr/bin/env python3
"""Hyperliquid top traders (hyperliquid/traders.py).
Run: python3 tests/test_hl_traders.py   No network: Hyperliquid is a stand-in and everything is written to a temp folder."""
import datetime as dt, gzip, importlib.util, json, os, shutil, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.environ.update({"HL_TRADERS_DATA": tmp + "/data", "HL_TRADERS_PUBLIC": tmp + "/public", "HL_TRADERS_TOP_N": "3",
                   "HL_TRADERS_PAUSE": "0", "HL_TRADERS_WATCH_MAX": "5"})
spec = importlib.util.spec_from_file_location("hlt", os.path.join(REPO, "hyperliquid", "traders.py"))
hlt = importlib.util.module_from_spec(spec); spec.loader.exec_module(hlt)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

DAY = hlt.DAY
NOW = dt.datetime(2026, 11, 13, 1, 30, tzinfo=dt.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
A = {k: "0x" + (c * 40) for k, c in (("steady", "a"), ("blowup", "b"), ("small", "c"), ("idle", "d"), ("new", "e"), ("late", "f"))}


def row(addr, account, pnl_m, vlm_m=5e6, name=None, as_dict=False):
    wins = [["day", {"pnl": "1", "roi": "0.001", "vlm": "10"}], ["week", {"pnl": "2", "roi": "0.002", "vlm": "20"}],
            ["month", {"pnl": str(pnl_m), "roi": "0.1", "vlm": str(vlm_m)}], ["allTime", {"pnl": str(pnl_m * 3), "roi": "0.5", "vlm": "9e9"}]]
    return {"ethAddress": addr.upper().replace("0X", "0x"), "accountValue": str(account), "windowPerformances": dict(wins) if as_dict else wins,
            "prize": 0, "displayName": name}


def history(daily_returns, start_value=200_000.0, deposit_on=None, days_all=200):
    """A wallet's history: wide steps for the older part, daily for the last 30 days. deposit_on = (day index, dollars)."""
    av, pnl, value, total = [], [], start_value, 0.0
    n = len(daily_returns)
    for i in range(n + 1):
        t = NOW_MS - (n - i) * DAY
        if i:
            gain = value * daily_returns[i - 1]
            value += gain; total += gain
            if deposit_on and deposit_on[0] == i:
                value += deposit_on[1]
        av.append([t, f"{value:.2f}"]); pnl.append([t, f"{total:.2f}"])
    month = {"accountValueHistory": av[-31:], "pnlHistory": pnl[-31:], "vlm": "1"}
    wide = list(range(0, n + 1, 5))
    if wide[-1] != n:
        wide.append(n)
    alltime = {"accountValueHistory": [av[i] for i in wide], "pnlHistory": [pnl[i] for i in wide], "vlm": "1"}
    return [["day", {"accountValueHistory": av[-2:], "pnlHistory": pnl[-2:], "vlm": "1"}], ["week", {"accountValueHistory": av[-8:], "pnlHistory": pnl[-8:], "vlm": "1"}],
            ["month", month], ["allTime", alltime], ["perpMonth", month]]


PORT = {A["steady"]: history([0.002] * 120),                                   # up a little every day
        A["blowup"]: history([0.01] * 90 + [-0.5] + [0.01] * 29),               # one day at -50%
        A["new"]: history([0.01] * 10, start_value=150_000.0),                  # ten days old
        A["late"]: history([0.001] * 120, deposit_on=(100, 500_000.0))}         # a big deposit that must not count as profit
BOARDS = {1: {"leaderboardRows": [row(A["steady"], 300_000, 40_000, name="Steady"), row(A["blowup"], 250_000, 90_000), row(A["small"], 5_000, 999_999),
                                  row(A["idle"], 900_000, 70_000, vlm_m=0), row(A["new"], 160_000, 15_000), row(A["late"], 700_000, 1_000)]},
          2: {"leaderboardRows": [row(A["steady"], 300_000, 45_000, name="Steady", as_dict=True), row(A["late"], 700_000, 60_000, as_dict=True),
                                  row(A["new"], 160_000, 20_000, as_dict=True), row(A["blowup"], 120_000, -80_000, as_dict=True)]}}
which = {"n": 1, "fail": set()}


def fake_get(url):
    return BOARDS[which["n"]]


def fake_post(body, tries=4):
    if body["type"] == "candleSnapshot":
        return [{"t": NOW_MS - (400 - i) * DAY, "c": str(50_000 + 100 * i)} for i in range(401)]
    if body["user"] in which["fail"]:
        raise RuntimeError("stand-in failure")
    return PORT.get(body["user"], [])


hlt.get, hlt.post = fake_get, fake_post
jl = lambda p: json.load(open(p))

# ---------- day one ----------
st = hlt.run(NOW)
d = jl(tmp + "/public/traders.json")
t = {x["address"]: x for x in d["traders"]}
check("the run reports what it did", st["ok"] and st["traders"] == 3 and st["listed"] == 6 and st["eligible"] == 4, st)
check("top 3 by 30-day profit; the small account and the wallet with no volume are left out",
      [x["address"] for x in d["traders"]] == [A["blowup"], A["steady"], A["new"]], [x["address"] for x in d["traders"]])
check("addresses are lower case and each has Hyperliquid's own explorer link", all(x["address"] == x["address"].lower() and x["url"].endswith(x["address"]) for x in d["traders"]))
check("rank, first seen and first rank are set on day one", t[A["blowup"]]["rank"] == 1 and t[A["steady"]]["first_rank"] == 2 and t[A["steady"]]["first_seen"] == "2026-11-13")
exp = 1.002 ** 120 - 1
check("a steady wallet's return is the chained daily return", abs(t[A["steady"]]["record"]["return"] - exp) < 0.002, (t[A["steady"]]["record"]["return"], exp))
check("its worst drop is zero and it held up", t[A["steady"]]["record"]["max_drawdown"] == 0 and t[A["steady"]]["status"] == "held")
check("one day at -50% is the worst drop, and the wallet goes to high drawdown", abs(t[A["blowup"]]["record"]["max_drawdown"] + 0.5) < 0.02 and t[A["blowup"]]["status"] == "high_drawdown",
      t[A["blowup"]]["record"])
check("ten days of history is marked short", t[A["new"]]["status"] == "short_history" and t[A["new"]]["record"]["days"] == 10, t[A["new"]])
check("Bitcoin over the same dates is there", t[A["steady"]]["record"]["btc_return"] is not None and t[A["steady"]]["record"]["btc_return"] > 0)
check("the display name is kept", t[A["steady"]]["name"] == "Steady" and t[A["blowup"]]["name"] is None)
check("the summary counts add up", d["summary"]["rated"] == 3 and d["summary"]["with_history"] == 2 and d["summary"]["held"] == 1 and d["summary"]["high_drawdown"] == 1, d["summary"])
check("nothing is watched off the list on day one", d["watched"] == [])
check("the day's cut is kept on the server", os.path.exists(tmp + "/data/2026-11-13.json.gz") and json.load(gzip.open(tmp + "/data/2026-11-13.json.gz"))["eligible"] == 4)
check("the rules are written into the file", d["rules"]["top_n"] == 3 and d["rules"]["min_account_usd"] == 100000 and "30-day profit" in d["rules"]["ranked_by"])

# ---------- twenty days later: a new board (windowPerformances as a dict this time), one wallet has dropped off ----------
which["n"] = 2
NOW2 = NOW + dt.timedelta(days=20)
NOW_MS = int(NOW2.timestamp() * 1000)   # history() builds each wallet's past back from this moment
PORT = {A["steady"]: history([0.002] * 140),
        A["blowup"]: history([0.01] * 90 + [-0.5] + [0.01] * 29 + [-0.02] * 20),   # lost 2% a day after we first saw it
        A["new"]: history([0.01] * 30, start_value=150_000.0),
        A["late"]: history([0.001] * 140, deposit_on=(100, 500_000.0))}
st2 = hlt.run(NOW2)
d2 = jl(tmp + "/public/traders.json")
t2 = {x["address"]: x for x in d2["traders"]}
w2 = {x["address"]: x for x in d2["watched"]}
check("day two reads a board whose windows are a dict", st2["ok"] and [x["address"] for x in d2["traders"]] == [A["late"], A["steady"], A["new"]], [x["address"] for x in d2["traders"]])
check("a wallet that dropped off the list is still followed", list(w2) == [A["blowup"]] and w2[A["blowup"]]["rank"] is None and w2[A["blowup"]]["first_rank"] == 1, d2["watched"])
check("its record since we first saw it is there, and it is a loss", w2[A["blowup"]]["since_seen"] and abs(w2[A["blowup"]]["since_seen"]["return"] - (0.98 ** 20 - 1)) < 0.01,
      w2[A["blowup"]]["since_seen"])
check("days watched counts from the first day", w2[A["blowup"]]["days_watched"] == 20 and t2[A["steady"]]["days_watched"] == 20 and t2[A["late"]]["days_watched"] == 0)
check("first seen and first rank never change", t2[A["steady"]]["first_seen"] == "2026-11-13" and t2[A["steady"]]["first_rank"] == 2 and t2[A["late"]]["first_seen"] == "2026-12-03")
check("the steady wallet's record since first seen is 20 good days", abs(t2[A["steady"]]["since_seen"]["return"] - (1.002 ** 20 - 1)) < 0.002, t2[A["steady"]]["since_seen"])
exp_late = 1.001 ** 140 - 1
check("a $500,000 deposit is not counted as profit", abs(t2[A["late"]]["record"]["return"] - exp_late) < 0.01, (t2[A["late"]]["record"]["return"], exp_late))
check("the new wallet now has 30 days and is no longer short", t2[A["new"]]["status"] == "held" and t2[A["new"]]["record"]["days"] == 30, t2[A["new"]]["record"])
check("off the list is counted in the summary", d2["summary"]["off_the_list"] == 1 and d2["summary"]["watching_since"] == "2026-11-13")

# ---------- bad days ----------
which["fail"] = {A["steady"]}
st3 = hlt.run(NOW2)
d3 = jl(tmp + "/public/traders.json")
check("one wallet that cannot be read is kept in the list without a record", st3["ok"] and st3["failed_requests"] == 1
      and {x["address"]: x for x in d3["traders"]}[A["steady"]]["record"] is None and {x["address"]: x for x in d3["traders"]}[A["steady"]]["status"] == "no_history")
which["fail"] = set()
before = open(tmp + "/public/traders.json").read()
BOARDS[2] = {"somethingElse": []}
rc = hlt.main()
status = jl(tmp + "/public/status.json")
check("a leaderboard in an unknown shape fails the run and says what it saw", rc == 1 and status["ok"] is False and "somethingElse" in status["error"], status)
check("and yesterday's file stays as it was", open(tmp + "/public/traders.json").read() == before)
BOARDS[2] = {"leaderboardRows": [{"nope": 1}] * 10}
rc = hlt.main()
check("rows with none of the expected fields fail the run too", rc == 1 and "unreadable" in jl(tmp + "/public/status.json")["error"], jl(tmp + "/public/status.json"))
BOARDS[2] = [row(A["steady"], 300_000, 45_000), row(A["late"], 700_000, 60_000)]
rc = hlt.main()
check("a bare list of rows is read as well", rc == 0 and jl(tmp + "/public/status.json")["ok"] is True, jl(tmp + "/public/status.json"))

shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
