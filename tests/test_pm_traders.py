#!/usr/bin/env python3
"""Polymarket top traders (polymarket/traders.py).
Run: python3 tests/test_pm_traders.py   No network: Polymarket is a stand-in built from the shapes its live API returned
on 2026-11-13, and everything is written to a temp folder."""
import datetime as dt, importlib.util, json, os, shutil, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.environ.update({"PM_TRADERS_DATA": tmp + "/data", "PM_TRADERS_PUBLIC": tmp + "/public", "PM_TRADERS_TOP_N": "3", "PM_TRADERS_PAUSE": "0", "PM_TRADERS_WATCH_MAX": "5"})
spec = importlib.util.spec_from_file_location("pmt", os.path.join(REPO, "polymarket", "traders.py"))
pmt = importlib.util.module_from_spec(spec); spec.loader.exec_module(pmt)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

NOW = dt.datetime(2026, 11, 13, 2, 0, tzinfo=dt.timezone.utc)
A = {k: "0x" + c * 40 for k, c in (("steady", "a"), ("onebet", "b"), ("gaveback", "c"), ("young", "d"), ("late", "e"))}


def points(daily_totals, end):
    """Polymarket's cumulative daily points for a list of lifetime totals, the last one on `end`."""
    out = []
    n = len(daily_totals)
    for i, v in enumerate(daily_totals):
        ts = int((end - dt.timedelta(days=n - 1 - i)).replace(hour=0, minute=0).timestamp())
        out.append({"timestamp": ts, "source_block": 1, "realized_market_pnl": v, "realized_lp_pnl": 0.0, "realized_combo_pnl": 0.0, "unrealized_pnl": 0.0,
                    "maker_rebate": 0.0, "taker_rebate": 0.0, "reward_income": 0.0, "yield_income": 0.0, "referral_income": 0.0, "deposits": None, "withdrawals": None,
                    "realized_pnl": v, "wallet_income": 100.0, "position_pnl": v - 100, "settled_pnl": v, "economic_pnl": v, "trade_pnl": v - 50,
                    "fees": -20.0, "fees_paid": -20.0, "cashflow_net": None, "volume": 10.0 * (i + 1), "volume_usdc": 5.0 * (i + 1), "trade_count": i + 1})
    return out


def world(end):
    return {A["steady"]: (points([1000.0 * i for i in range(101)], end), {"trades": 400, "biggest_win": 5000.0, "join_date": int((end - dt.timedelta(days=100)).timestamp())}, 20000.0),
            # one bet of 90,000 made nearly all of a 100,000 total
            A["onebet"]: (points([0.0] * 50 + [90000.0] + [90000.0 + 200 * i for i in range(1, 51)], end), {"trades": 12, "biggest_win": 90000.0, "join_date": int((end - dt.timedelta(days=100)).timestamp())}, 0.0),
            # up to 200,000, then gave 150,000 of it back
            A["gaveback"]: (points([4000.0 * i for i in range(51)] + [200000.0 - 3000 * i for i in range(1, 51)], end), {"trades": 900, "biggest_win": 20000.0, "join_date": int((end - dt.timedelta(days=100)).timestamp())}, 5000.0),
            A["young"]: (points([500.0 * i for i in range(11)], end), {"trades": 5, "biggest_win": 3000.0, "join_date": int((end - dt.timedelta(days=10)).timestamp())}, 100.0),
            A["late"]: (points([-2000.0 * i for i in range(61)], end), {"trades": 50, "biggest_win": 0.0, "join_date": int((end - dt.timedelta(days=60)).timestamp())}, 0.0)}


state = {"end": NOW, "board": 1, "fail": set(), "bad": None}
BOARD = {1: [(A["gaveback"], 90000.0, "GaveBack", "gb_x"), (A["onebet"], 80000.0, "OneBet", ""), (A["steady"], 30000.0, "", ""), (A["young"], 5000.0, "Young", "")],
         2: [(A["steady"], 50000.0, "", ""), (A["late"], 40000.0, "Late", ""), (A["young"], 20000.0, "Young", ""), (A["onebet"], -5.0, "OneBet", "")]}


def fake_get(path, tries=4, **q):
    if path == "/v2/leaderboard":
        if state["bad"] is not None:
            return state["bad"]
        rows = [{"rank": i, "user_id": a.upper().replace("0X", "0x"), "pnl": p, "volume": 1e6, "user_name": n, "profile_image": "", "x_username": x, "verified": False}
                for i, (a, p, n, x) in enumerate(BOARD[state["board"]][:int(q["limit"])], 1)]
        return {"data": rows, "pagination": {"limit": int(q["limit"]), "offset": 0, "has_more": True, "next_cursor": "x"}}
    u = q["user"]
    if u in state["fail"]:
        raise RuntimeError("stand-in failure")
    pts, stats, val = world(state["end"]).get(u, ([], None, None))
    if path == "/v2/user-pnl":
        return {"data": {"proxy_wallet": u, "interval": q["interval"], "fidelity": q["fidelity"], "source_fidelity": "1d", "points": pts}}
    if path == "/v2/user-stats":
        return {"data": ({"proxy_wallet": u, "views": 1, **stats} if stats else None)}
    return {"data": {"proxy_wallet": u, "value": val or 0}}


pmt.get = fake_get
jl = lambda p: json.load(open(p))

# ---------- day one ----------
st = pmt.run(NOW)
d = jl(tmp + "/public/traders.json")
t = {x["address"]: x for x in d["traders"]}
check("the run reports what it did", st["ok"] and st["traders"] == 3 and st["rated"] == 3 and st["failed_wallets"] == 0, st)
check("the top 3 by 30-day profit, in the leaderboard's order", [x["address"] for x in d["traders"]] == [A["gaveback"], A["onebet"], A["steady"]])
check("addresses are lower case and each has a Polymarket profile link", all(x["address"] == x["address"].lower() and x["url"] == "https://polymarket.com/profile/" + x["address"] for x in d["traders"]))
check("name and X handle are kept, an empty one is null", t[A["gaveback"]]["name"] == "GaveBack" and t[A["gaveback"]]["x_username"] == "gb_x" and t[A["steady"]]["name"] is None and t[A["onebet"]]["x_username"] is None)
r = t[A["steady"]]["record"]
check("lifetime profit is the last day's total", r["pnl_all_usd"] == 100000 and r["peak_pnl_usd"] == 100000 and r["worst_giveback_usd"] == 0 and r["giveback_share"] == 0, r)
check("a steady wallet's biggest win is a small share of its profit", r["biggest_win_share"] == 0.05 and r["markets_traded"] == 400 and r["open_positions_usd"] == 20000, r)
r = t[A["onebet"]]["record"]
check("one bet that made 90,000 of 100,000 shows as 0.9 of lifetime profit", r["pnl_all_usd"] == 100000 and r["biggest_win_usd"] == 90000 and r["biggest_win_share"] == 0.9, r)
r = t[A["gaveback"]]["record"]
check("a wallet that fell from 200,000 to 50,000 gave back 150,000, three quarters of its best", r["peak_pnl_usd"] == 200000 and r["pnl_all_usd"] == 50000 and r["worst_giveback_usd"] == 150000 and r["giveback_share"] == 0.75, r)
check("the day of the high and of the low are recorded", r["peak_day"] == (NOW - dt.timedelta(days=50)).date().isoformat() and r["worst_giveback_day"] == NOW.date().isoformat(), r)
check("the last 30 days by the daily series is kept next to the leaderboard's figure", t[A["steady"]]["record"]["pnl_30d_series_usd"] == 30000 and t[A["steady"]]["pnl_30d_usd"] == 30000
      and t[A["gaveback"]]["record"]["pnl_30d_series_usd"] == -90000 and t[A["gaveback"]]["pnl_30d_usd"] == 90000 and d["summary"]["leaderboard_differs"] == 2, (t[A["gaveback"]]["record"], d["summary"]))
check("days counts from the wallet's first day", t[A["steady"]]["record"]["days"] == 100 and t[A["steady"]]["status"] == "ok")
check("the summary counts one-bet wallets and give-backs", d["summary"]["one_bet_half"] == 1 and d["summary"]["gave_back_half"] == 1 and d["summary"]["down_overall"] == 0 and d["summary"]["with_history"] == 3, d["summary"])
check("it says why there is no percentage return", "deposits and withdrawals" in d["rules"]["no_percent_return"] and d["rules"]["top_n"] == 3)
check("nothing is watched off the list on day one, and no since-seen yet", d["watched"] == [] and all(x["since_seen"] is None for x in d["traders"]))
check("the list as read is kept on the server", len(jl(tmp + "/data/2026-11-13.json")["rows"]) == 3)

# ---------- twenty days later ----------
state.update(end=NOW + dt.timedelta(days=20), board=2)
st2 = pmt.run(NOW + dt.timedelta(days=20))
d2 = jl(tmp + "/public/traders.json")
t2 = {x["address"]: x for x in d2["traders"]}
w2 = {x["address"]: x for x in d2["watched"]}
check("day two's list", st2["ok"] and [x["address"] for x in d2["traders"]] == [A["steady"], A["late"], A["young"]], [x["address"] for x in d2["traders"]])
check("the two wallets that dropped off are still followed", sorted(w2) == sorted([A["gaveback"], A["onebet"]]) and all(x["rank"] is None and not x["on_board"] for x in w2.values()), list(w2))
check("first seen and first rank never change", t2[A["steady"]]["first_seen"] == "2026-11-13" and t2[A["steady"]]["first_rank"] == 3 and w2[A["gaveback"]]["first_rank"] == 1 and t2[A["late"]]["first_seen"] == "2026-12-03")
check("days watched counts from the first day", t2[A["steady"]]["days_watched"] == 20 and w2[A["onebet"]]["days_watched"] == 20 and t2[A["late"]]["days_watched"] == 0)
s = t2[A["steady"]]["since_seen"]
check("since first seen is the change in lifetime profit from that day", s and s["from"] == "2026-11-13" and s["to"] == "2026-12-03" and s["worst_giveback_usd"] == 0, s)
check("a wallet that is down overall is counted, and has no share figures", t2[A["late"]]["record"]["pnl_all_usd"] == -120000 and t2[A["late"]]["record"]["biggest_win_share"] is None
      and t2[A["late"]]["record"]["giveback_share"] is None and d2["summary"]["down_overall"] == 1, t2[A["late"]]["record"])
check("ten days of history is marked short", t2[A["young"]]["status"] == "short_history" and t2[A["young"]]["record"]["days"] == 10)

# ---------- bad days ----------
state["fail"] = {A["steady"]}
st3 = pmt.run(NOW + dt.timedelta(days=20))
d3 = {x["address"]: x for x in jl(tmp + "/public/traders.json")["traders"]}
check("one wallet that cannot be read stays in the list without a record", st3["ok"] and st3["failed_wallets"] == 1 and d3[A["steady"]]["record"] is None and d3[A["steady"]]["status"] == "no_history")
state["fail"] = set()
before = open(tmp + "/public/traders.json").read()
state["bad"] = {"somethingElse": []}
rc = pmt.main()
status = jl(tmp + "/public/status.json")
check("a leaderboard in an unknown shape fails the run and says what it saw", rc == 1 and status["ok"] is False and "somethingElse" in status["error"], status)
check("and yesterday's file stays as it was", open(tmp + "/public/traders.json").read() == before)
state["bad"] = {"data": [{"nope": 1}] * 10}
rc = pmt.main()
check("rows with none of the expected fields fail the run too", rc == 1 and "unreadable" in jl(tmp + "/public/status.json")["error"], jl(tmp + "/public/status.json"))
state["bad"] = None
rc = pmt.main()
check("and a good day after a bad one works again", rc == 0 and jl(tmp + "/public/status.json")["ok"] is True)

# ---------- the series helper on its own ----------
pts = [{"timestamp": 86400 * 10, "economic_pnl": None, "position_pnl": 5.0, "trade_pnl": 9.0}, {"timestamp": 86400 * 10 + 3600, "economic_pnl": 7.0},
       {"timestamp": 86400 * 11, "economic_pnl": None, "position_pnl": None, "trade_pnl": None}, {"timestamp": 86400 * 12, "economic_pnl": 8.0}]
check("one value per day, the last of the day; a day with no value at all is left out; position profit stands in when the all-in figure is missing",
      pmt.series(pts) == [("1970-01-11", 7.0), ("1970-01-13", 8.0)] and pmt.series(pts[:1]) == [("1970-01-11", 5.0)], pmt.series(pts))

shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
