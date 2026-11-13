#!/usr/bin/env python3
"""Kamino Earn vaults in the daily vault scan (vaults/scan.py, kamino_earn).
Run: python3 tests/test_kamino_earn.py   No network: Kamino is a stand-in built from the shapes its live API returned on
2026-11-13 (the list, the metrics pages keyed by "kvault", one vault's daily history), written to a temp folder."""
import datetime as dt, importlib.util, json, os, shutil, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.environ.update({"VAULTS_DATA": tmp + "/data", "VAULTS_PUBLIC": tmp + "/public", "VAULTS_RESTORE": tmp + "/restore"})
spec = importlib.util.spec_from_file_location("scan", os.path.join(REPO, "vaults", "scan.py"))
scan = importlib.util.module_from_spec(spec); spec.loader.exec_module(scan)
scan.time.sleep = lambda s: None

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

USDC, SOL, ODD = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "So11111111111111111111111111111111111111112", "Odd1111111111111111111111111111111111111111"
names = {"BigUsdc1": ("Steakhouse USDC", USDC, 15_000_000.0), "SolVault": ("Elemental SOL Optimizer", SOL, 430_000.0), "OddToken": ("RockawayX EUROP", ODD, 4_000_000.0),
         "Small001": ("Tiny USDC", USDC, 900.0), "NoHist01": ("No History USDC", USDC, 600_000.0), "NoMetric": ("Missing Metrics", USDC, 5_000_000.0)}
for i in range(20):
    names[f"Filler{i:02d}"] = (f"Filler {i}", USDC, 10.0)
LIST = [{"address": a, "programId": "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd",
         "state": {"name": n, "tokenMint": mint, "tokenMintDecimals": 6, "managementFeeBps": 25, "performanceFeeBps": 1000, "prevAum": "1", "sharesIssued": "1"}}
        for a, (n, mint, tvl) in names.items()]
def metric(a):
    n, mint, tvl = names[a]
    return {"kvault": a, "apy7d": "0.0434", "apy30d": "0.0462", "apy90d": "0.0507", "tokenPrice": "1", "tokensAvailableUsd": str(tvl * 0.1), "tokensInvestedUsd": str(tvl * 0.9),
            "sharePrice": "84.5", "tokensPerShare": "1.0730" if a == "BigUsdc1" else "1.0370", "numberOfHolders": 173, "sharesIssued": "1"}
with_metrics = [a for a in names if a != "NoMetric"]
PAGE1, PAGE2 = with_metrics[:15], with_metrics[15:]
asked = []


def fake_get(url, body=None):
    asked.append(url)
    if url == scan.KAMINO_EARN:
        return LIST
    if url == scan.KAMINO_EARN + "/metrics":
        return {"result": [metric(a) for a in PAGE1], "paginationToken": "eyJsYXN0SWQiOiIxMDAifQ=="}
    if url.startswith(scan.KAMINO_EARN + "/metrics?paginationToken="):
        return {"result": [metric(a) for a in PAGE2], "paginationToken": None}
    if "/metrics/history?" in url:
        a = url.split("/")[-3]
        if a == "NoHist01":
            raise RuntimeError("stand-in failure")
        end = dt.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for i in range(101):   # 100 days: up 0.07% a day, with one bad week in the middle for the SOL vault
            p = 1.0 * 1.0007 ** i
            if a == "SolVault" and 50 <= i < 57:
                p *= 0.97
            out.append({"timestamp": (end - dt.timedelta(days=100 - i)).strftime("%Y-%m-%dT00:00:00.000Z"), "tvl": "1", "apy": "0.04", "sharePrice": str(p * 80), "tokensPerShare": str(p), "reserves": []})
        return out
    raise AssertionError("unexpected request " + url)


scan.get = fake_get
today = dt.datetime.utcnow().date().isoformat()
msg = scan.run("kamino-earn", scan.kamino_earn, scan.KAMINO_EARN_MIN, today)
d = json.load(open(tmp + "/public/kamino-earn.json"))
v = {x["id"]: x for x in d["vaults"]}

check("the run reports what it listed and published", "kamino-earn: 25 listed, 4 with $250,000+" in msg, msg)
check("only vaults with $250,000 or more are published, biggest first", [x["id"] for x in d["vaults"]] == ["BigUsdc1", "OddToken", "NoHist01", "SolVault"], [x["id"] for x in d["vaults"]])
check("both pages of metrics were read", sum(1 for u in asked if "/metrics" in u and "history" not in u) == 2 and any("paginationToken=eyJs" in u for u in asked))
check("a vault with no metrics row is left out, not published with a guess", "NoMetric" not in v)
check("history is asked only for vaults big enough to publish", sorted(u.split("/")[-3] for u in asked if "/metrics/history?" in u) == ["BigUsdc1", "NoHist01", "OddToken", "SolVault"])
check("size is what is lent out plus what is waiting, in dollars", abs(v["BigUsdc1"]["tvl_usd"] - 15_000_000) < 1)
check("the share price is the value of a share in the vault's own token", v["BigUsdc1"]["share_price"] == 1.073 and v["SolVault"]["share_price"] == 1.037)
check("a token we are sure of is named, any other is left empty", v["BigUsdc1"]["asset"] == "USDC" and v["SolVault"]["asset"] == "SOL" and v["OddToken"]["asset"] is None)
check("the link goes to the vault's own address on Solana's explorer", v["SolVault"]["url"] == "https://explorer.solana.com/address/SolVault" and v["SolVault"]["chain"] == "Solana")
check("Kamino's own figures are kept as fractions and labelled as the venue's", v["BigUsdc1"]["venue_reported"]["apy_30d"] == 0.0462 and v["BigUsdc1"]["venue_reported"]["management_fee"] == 0.0025
      and v["BigUsdc1"]["venue_reported"]["performance_fee"] == 0.1 and v["BigUsdc1"]["venue_reported"]["holders"] == 173, v["BigUsdc1"]["venue_reported"])
exp = 1.0007 ** 100 - 1
check("the record comes from Kamino's own daily history: 100 days, the chained return", v["BigUsdc1"]["computed_from"] == "venue share-price history" and v["BigUsdc1"]["tracking_days"] == 100
      and abs(v["BigUsdc1"]["return_tracked"] - exp) < 1e-5 and v["BigUsdc1"]["max_drawdown_tracked"] == 0, v["BigUsdc1"])
check("a bad week shows as the worst drop", abs(v["SolVault"]["max_drawdown_tracked"] + 0.03) < 0.002, v["SolVault"]["max_drawdown_tracked"])
check("with 30 days or more the return is also given as a yearly rate", abs(v["BigUsdc1"]["annualised_tracked"] - ((1 + exp) ** (365 / 100) - 1)) < 1e-4)
check("a vault whose history could not be read falls back to the prices we save ourselves", v["NoHist01"]["computed_from"] == scan.OURS and v["NoHist01"]["tracking_days"] == 0)
check("the day's raw list is kept on the server without the bulky histories", any(f.startswith(today) for f in os.listdir(tmp + "/data/kamino-earn")))
LIST_KEEP = list(LIST)
del LIST[5:]
try:
    scan.run("kamino-earn", scan.kamino_earn, scan.KAMINO_EARN_MIN, today); short = False
except RuntimeError as e:
    short = "vaults listed" in str(e)
check("a list that is suddenly tiny fails the venue and leaves yesterday's file", short and json.load(open(tmp + "/public/kamino-earn.json"))["vaults"][0]["id"] == "BigUsdc1")

shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
