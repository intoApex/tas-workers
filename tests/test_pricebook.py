#!/usr/bin/env python3
"""The share-price backup and the way back from it (common/pricebook.py, vaults/scan.py, dhedge/snapshot.py).
Run: python3 tests/test_pricebook.py   No network: the venues are stand-ins and everything is written to a temp folder."""
import gzip, importlib.util, json, os, shutil, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
from common import pricebook

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

def load(name, path, env):
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def jl(p): return json.load(open(p))
def wipe(p): shutil.rmtree(p, ignore_errors=True)

tmp = tempfile.mkdtemp()

# ---------- the module on its own ----------
pd = os.path.join(tmp, "unit", "prices"); os.makedirs(pd)
days = {"2026-11-09": {"a": 1.0, "b": 2.0, "dust": 5.0}, "2026-11-10": {"a": 1.0234567891234567, "dust": 5.0, "late": 7.0},
        "2026-11-12": {"a": 1.1, "b": 2.2, "late": 7.7, "zero": 0}}
for d, v in days.items(): json.dump(v, open(os.path.join(pd, d + ".json"), "w"))
for junk in ("notes.txt", "2026-11-11.json.tmp", "latest.json", "2026-13-40.json"): open(os.path.join(pd, junk), "w").write("{}")
check("only real day files count", pricebook.days_on_disk(pd) == ["2026-11-09", "2026-11-10", "2026-11-12"], pricebook.days_on_disk(pd))
s = pricebook.series(pd)
check("series: oldest first, zero prices dropped", s["a"] == [("2026-11-09", 1.0), ("2026-11-10", 1.0234567891234567), ("2026-11-12", 1.1)] and "zero" not in s, s)
out = os.path.join(tmp, "unit", "pub", "x-prices.json")
n = pricebook.publish(pd, out, "x", "2026-11-12", {"a", "b", "late", "not-there"})
d = jl(out)
check("published: days, counts, venue, date", n == (3, 3) and d["days"] == ["2026-11-09", "2026-11-10", "2026-11-12"] and d["venue"] == "x" and d["as_of"] == "2026-11-12", (n, d["days"]))
check("published: first-day index, a gap is null, prices exactly as saved",
      d["prices"] == {"a": [0, [1.0, 1.0234567891234567, 1.1]], "b": [0, [2.0, None, 2.2]], "late": [1, [7.0, 7.7]]}, d["prices"])
check("published: dust left out, unknown id ignored", "dust" not in d["prices"] and "not-there" not in d["prices"])
pricebook.publish(pd, out, "x", "2026-11-12", {"a"})
check("a vault already published stays in after it drops below the bar", set(jl(out)["prices"]) == {"a", "b", "late"})
check("an empty folder publishes nothing", pricebook.publish(os.path.join(tmp, "nowhere"), os.path.join(tmp, "unit", "pub", "none.json"), "x", "2026-11-12", {"a"}) == (0, 0) and not os.path.exists(os.path.join(tmp, "unit", "pub", "none.json")))

rd = os.path.join(tmp, "unit", "restored")
bad = os.path.join(tmp, "unit", "bad.json"); open(bad, "w").write("{not json")
got = pricebook.restore(rd, [os.path.join(tmp, "missing.json"), bad, out])
check("restore: skips a missing and a broken source, writes every day from the first good one", got == (3, out) and pricebook.days_on_disk(rd) == d["days"], got)
check("restore is exact for the vaults in the backup", pricebook.series(rd) == {k: v for k, v in s.items() if k != "dust"}, pricebook.series(rd))
check("restore again: nothing to do", pricebook.restore(rd, [out]) == (0, None))
os.remove(os.path.join(rd, "2026-11-10.json")); json.dump({"a": 99.0, "only-here": 3.0}, open(os.path.join(rd, "2026-11-12.json"), "w"))
got = pricebook.restore(rd, [out])
check("restore fills only the missing day and never touches a day on disk", got == (1, out) and jl(os.path.join(rd, "2026-11-12.json")) == {"a": 99.0, "only-here": 3.0} and jl(os.path.join(rd, "2026-11-10.json")) == {"a": 1.0234567891234567, "late": 7.0}, got)
json.dump({"days": "x", "prices": []}, open(bad, "w"))
check("a file of the wrong shape is not a backup", pricebook.restore(os.path.join(tmp, "unit", "r2"), [bad]) == (0, None))

# ---------- vaults/scan.py with stand-in venues ----------
V = os.path.join(tmp, "vaults")
scan = load("scan", "vaults/scan.py", {"VAULTS_DATA": V + "/data", "VAULTS_PUBLIC": V + "/pub", "VAULTS_RESTORE": V + "/restore"})
def venue(day_i):
    # big: always listed. grower: small at first, crosses the minimum on day 3. dust: never matters. gone: delisted after day 1.
    px = [(1.00, 1.00, 4.0, 9.0), (1.10, 1.02, 4.0, None), (0.99, 1.05, 4.0, None), (1.21, 1.08, 4.0, None)][day_i]
    rows = [{"id": "big", "name": "Big", "tvl_usd": 500_000, "share_price": px[0]},
            {"id": "grower", "name": "Grower", "tvl_usd": [2_000, 3_000, 12_000, 15_000][day_i], "share_price": px[1]},
            {"id": "dust", "name": "Dust", "tvl_usd": 50, "share_price": px[2]}]
    if px[3]: rows.append({"id": "gone", "name": "Gone", "tvl_usd": 40_000, "share_price": px[3]})
    return {"raw": day_i}, rows
D = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
def go(i): return scan.run("demo", lambda: venue(i), 10_000, D[i])
def pub(): return {v["id"]: v for v in jl(V + "/pub/demo.json")["vaults"]}
m = [go(0), go(1), go(2)]
check("scan says what it published", m[2] == "demo: 3 listed, 2 with $10,000+; price history published: 3 days, 3 vaults", m[2])
book = jl(V + "/pub/demo-prices.json")
check("history file: every vault at a tenth of the minimum or more, dust left out, a delisted vault kept",
      book["days"] == D[:3] and book["prices"] == {"big": [0, [1.0, 1.1, 0.99]], "grower": [0, [1.0, 1.02, 1.05]], "gone": [0, [9.0]]}, book)
before = pub()
check("records before any loss", before["big"]["tracking_since"] == D[0] and before["big"]["return_tracked"] == -0.01 and before["big"]["max_drawdown_tracked"] == -0.1
      and before["grower"]["tracking_since"] == D[0] and before["grower"]["return_tracked"] == 0.05 and before["big"]["computed_from"] == "our daily snapshots", before)

# 1. the data folder is lost, the public folder survives: the next scan puts the days back first
keepbook = json.dumps(book)
wipe(V + "/data")
msg = go(3); after = pub()
check("lost data folder: three days restored from the published file", "3 days of prices restored from " + V + "/pub/demo-prices.json" in msg, msg)
check("lost data folder: records carry on from the first day", after["big"]["tracking_since"] == D[0] and after["big"]["tracking_days"] == 3 and after["big"]["return_tracked"] == 0.21
      and after["big"]["max_drawdown_tracked"] == -0.1 and after["grower"]["return_tracked"] == 0.08, after)
check("lost data folder: the history file now has four days", jl(V + "/pub/demo-prices.json")["days"] == D and jl(V + "/pub/demo-prices.json")["prices"]["big"] == [0, [1.0, 1.1, 0.99, 1.21]])

# 2. the whole disk is lost and a scan runs before anyone has put the backup back: records restart, then recover
full = jl(V + "/pub/demo-prices.json")
wipe(V + "/data"); wipe(V + "/pub")
msg = go(3); lost = pub()
check("lost disk, no backup yet: records restart from today", lost["big"]["tracking_days"] == 0 and "return_tracked" not in lost["big"] and "restored" not in msg and jl(V + "/pub/demo-prices.json")["days"] == [D[3]], (msg, lost["big"]))
os.makedirs(V + "/restore"); json.dump({**full, "days": D[:3], "prices": json.loads(keepbook)["prices"]}, open(V + "/restore/demo-prices.json", "w"))  # the copy from perf-data, a day old
msg = go(3); back = pub()
check("lost disk: three days restored from the copy in restore/", "3 days of prices restored from" in msg and "restore" in msg, msg)
check("lost disk: records are whole again", back["big"]["tracking_since"] == D[0] and back["big"]["return_tracked"] == 0.21 and back["big"]["max_drawdown_tracked"] == -0.1 and back["grower"]["return_tracked"] == 0.08, back)
check("lost disk: the published history is whole again, delisted vault included", jl(V + "/pub/demo-prices.json")["days"] == D and jl(V + "/pub/demo-prices.json")["prices"]["gone"] == [0, [9.0]], jl(V + "/pub/demo-prices.json"))
check("a second run restores nothing", "restored" not in go(3))

# 3. venues whose records do not rest on our prices publish no history file
r = scan.run("withhist", lambda: ({}, [{"id": "v", "name": "V", "tvl_usd": 1e6, "share_price": 1.2, "venue_history": [("2026-01-01", 1.0), ("2026-11-11", 1.2)]}]), 10_000, D[2])
check("a venue with its own published history: no history file of ours", not os.path.exists(V + "/pub/withhist-prices.json") and "price history" not in r, r)
r = scan.run("noprice", lambda: ({}, [{"id": "p", "name": "P", "tvl_usd": 1e6, "share_price": None, "no_share_price": True}]), 10_000, D[2])
check("a venue with no share price: no history file", not os.path.exists(V + "/pub/noprice-prices.json") and "price history" not in r, r)
check("the real venue list is whole", set(scan.VENUES) == {"paradex", "orderly", "kamino", "grvt", "hibachi", "lagoon", "metric", "onchain"})
check("a paused venue is not scanned, keeps its adapter and says why", "d2" not in scan.VENUES and callable(scan.d2) and len(scan.PAUSED["d2"]) > 20)
# 4. a fault in the backup code never stops a scan
real_publish, real_restore = pricebook.publish, pricebook.restore
def boom(*a, **k): raise RuntimeError("disk full")
pricebook.publish = boom; pricebook.restore = boom
msg = go(3)
check("backup code that fails: the scan still publishes the vaults and says what went wrong", msg.startswith("demo: 3 listed, 2 with $10,000+") and "price restore failed: disk full" in msg and "price history not published: disk full" in msg
      and pub()["big"]["return_tracked"] == 0.21, msg)
pricebook.publish, pricebook.restore = real_publish, real_restore

# ---------- dhedge/snapshot.py ----------
H = os.path.join(tmp, "dhedge")
dh = load("dhedge_snapshot", "dhedge/snapshot.py", {"DHEDGE_DATA": H + "/data", "DHEDGE_PUBLIC": H + "/pub", "DHEDGE_RESTORE": H + "/restore"})
E18 = 10**18
def funds(price_big, price_mid):
    f = [{"address": "0xBIG", "name": "Big", "totalValue": str(200_000 * E18), "tokenPrice": str(int(price_big * E18)), "isPrivate": False},
         {"address": "0xMid", "name": "Mid", "totalValue": str(8_000 * E18), "tokenPrice": str(int(price_mid * E18)), "isPrivate": False},
         {"address": "0xpriv", "name": "Private", "totalValue": str(900_000 * E18), "tokenPrice": str(2 * E18), "isPrivate": True}]
    return f + [{"address": f"0x{i:040x}", "name": f"d{i}", "totalValue": str(10 * E18), "tokenPrice": str(E18), "isPrivate": False} for i in range(120)]
# two days saved before the prices folder existed: raw snapshots only
os.makedirs(H + "/data")
for day, f in (("2026-11-07", funds(1.0, 1.0)), ("2026-11-08", funds(1.25, 1.5))):
    json.dump({"at": day, "funds": f}, gzip.open(f"{H}/data/{day}.json.gz", "wt"))
class Day(dh.dt.datetime):
    now_day = "2026-11-09"
    @classmethod
    def utcnow(cls): return cls.fromisoformat(cls.now_day + "T01:00:00")
dh.dt.datetime = Day
dh.gql = lambda q: {"allFunds": funds(1.5, 1.2)}
import io, contextlib
def run_dh():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): dh.main()
    return buf.getvalue().strip()
msg = run_dh()
rows = {v["address"]: v for v in jl(H + "/pub/vaults.json")["vaults"]}
check("dHEDGE: older days get their price file from the saved snapshots, once", "2 days of prices written from saved snapshots" in msg and pricebook.days_on_disk(H + "/data/prices") == ["2026-11-07", "2026-11-08", "2026-11-09"], msg)
check("dHEDGE: record as before", list(rows) == ["0xbig"] and rows["0xbig"]["tracking_since"] == "2026-11-07" and rows["0xbig"]["tracking_days"] == 2 and rows["0xbig"]["return_tracked"] == 0.5 and rows["0xbig"]["max_drawdown_tracked"] == 0.0, rows)
hb = jl(H + "/pub/prices.json")
check("dHEDGE: history file has $5,000+ public vaults only", hb["venue"] == "dhedge" and hb["days"] == ["2026-11-07", "2026-11-08", "2026-11-09"] and hb["prices"] == {"0xbig": [0, [1.0, 1.25, 1.5]], "0xmid": [0, [1.0, 1.5, 1.2]]}, hb)
check("dHEDGE: a second run the same day writes nothing new", "written from saved" not in run_dh() and "restored" not in msg)
pricebook.publish = boom; pricebook.restore = boom
msg2 = run_dh()
check("dHEDGE: backup code that fails does not stop the snapshot", msg2.startswith("2026-11-09: 123 listed, 1 with $50,000+") and "price history not published: disk full" in msg2 and "price restore failed: disk full" in msg2, msg2)
pricebook.publish, pricebook.restore = real_publish, real_restore
# the disk is lost; the backup is put in restore/; the next day's run brings the record back
wipe(H + "/data"); wipe(H + "/pub"); os.makedirs(H + "/restore"); json.dump(hb, open(H + "/restore/prices.json", "w"))
Day.now_day = "2026-11-10"; dh.gql = lambda q: {"allFunds": funds(1.2, 1.3)}
msg = run_dh(); rows = {v["address"]: v for v in jl(H + "/pub/vaults.json")["vaults"]}
check("dHEDGE lost disk: three days restored, record whole", "3 days of prices restored from" in msg and rows["0xbig"]["tracking_since"] == "2026-11-07" and rows["0xbig"]["tracking_days"] == 3
      and rows["0xbig"]["return_tracked"] == 0.2 and rows["0xbig"]["max_drawdown_tracked"] == -0.2, (msg, rows))
check("dHEDGE lost disk: history file whole again", jl(H + "/pub/prices.json")["prices"]["0xmid"] == [0, [1.0, 1.5, 1.2, 1.3]], jl(H + "/pub/prices.json")["prices"])

shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if ok else "FAILED"); sys.exit(0 if ok else 1)
