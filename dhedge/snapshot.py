#!/usr/bin/env python3
"""dHEDGE vaults: a daily snapshot from dHEDGE's public GraphQL API, plus our own share-price record.

Public data only, no keys. Once a day:
  1. Save every vault dHEDGE lists (all chains) to /srv/tas/data/dhedge/<day>.json.gz (raw, kept on the server).
  2. Publish /srv/tas/public/dhedge/vaults.json for vaults with at least $50k in them: TVL, fees, manager,
     dHEDGE's own performance figures (labelled as dHEDGE-reported), and our record computed from the
     daily share prices we saved ourselves (return since we started tracking and the worst drop).
The token price is the vault's onchain share price as dHEDGE reports it (USD, 18 decimals).
The daily share prices are also kept as one small file per day in <data>/prices/, and the whole history is published as
/srv/tas/public/dhedge/prices.json so the site's nightly run can copy it off this server: they are the one thing here that
cannot be fetched again. Days missing from the folder are written back from that file, or from restore/dhedge/prices.json
in this repo, before each run. See common/pricebook.py.
"""
import datetime as dt, gzip, json, os, sys, urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
from common import pricebook

API = "https://api-v2.dhedge.org/graphql"
DATA = os.environ.get("DHEDGE_DATA", "/srv/tas/data/dhedge")
PUB = os.environ.get("DHEDGE_PUBLIC", "/srv/tas/public/dhedge")
PRICES = os.path.join(DATA, "prices")
RESTORE = os.environ.get("DHEDGE_RESTORE", os.path.join(REPO, "restore", "dhedge"))
MIN_TVL = float(os.environ.get("DHEDGE_MIN_TVL", "50000"))
UA = "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)"
FIELDS = ("address name managerName managerAddress blockchainCode totalValue totalSupply tokenPrice isPrivate category poolType "
          "performanceFeeNumerator managerFeeNumerator streamingFeeNumerator entryFeeNumerator exitFeeNumerator blockTime symbol "
          "sortinoRatio riskFactor score leaderboardRank performanceMetrics { day week month quarter halfyear year }")

def gql(query):
    req = urllib.request.Request(API, data=json.dumps({"query": query}).encode(), headers={"Content-Type": "application/json", "User-Agent": UA})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if d.get("errors"):
        raise RuntimeError(str(d["errors"])[:300])
    return d["data"]

def e18(x):
    try: return int(x) / 1e18
    except Exception: return None

def factor(x):
    v = e18(x)
    return None if v is None or v <= 0 else round(v - 1, 6)

def pct(num):
    try: return round(int(num) / 100, 2)   # numerators are out of 10,000
    except Exception: return None

def save_day(day, funds):
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, f"{day}.json.gz")
    with gzip.open(p + ".tmp", "wt") as f:
        json.dump({"at": dt.datetime.utcnow().isoformat() + "Z", "funds": funds}, f)
    os.replace(p + ".tmp", p)

def save_prices(day, funds):
    """One day's share prices, {address: price}: the small file the records are computed from."""
    os.makedirs(PRICES, exist_ok=True)
    out = {}
    for f in funds:
        p = e18(f.get("tokenPrice"))
        if p and p > 0 and f.get("address"):
            out[f["address"].lower()] = p
    p = os.path.join(PRICES, f"{day}.json")
    with open(p + ".tmp", "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(p + ".tmp", p)

def backfill():
    """Days saved before the prices folder existed: write their price file from the raw snapshot, once."""
    have, n = set(pricebook.days_on_disk(PRICES)), 0
    for name in sorted(os.listdir(DATA)):
        if not name.endswith(".json.gz") or name[:10] in have: continue
        try: funds = json.load(gzip.open(os.path.join(DATA, name), "rt"))["funds"]
        except Exception: continue
        save_prices(name[:10], funds); n += 1
    return n

def history():
    """Our own daily share prices: {address: [(day, price), ...]}."""
    return pricebook.series(PRICES)

def record(series):
    if len(series) < 2: return {"tracking_days": len(series)}
    first, last = series[0][1], series[-1][1]
    peak, worst = 0, 0.0
    for _, p in series:
        peak = max(peak, p); worst = min(worst, p / peak - 1)
    d0 = dt.date.fromisoformat(series[0][0]); d1 = dt.date.fromisoformat(series[-1][0])
    return {"tracking_since": series[0][0], "tracking_days": (d1 - d0).days, "return_tracked": round(last / first - 1, 6), "max_drawdown_tracked": round(worst, 6)}

def main():
    today = dt.datetime.utcnow().date().isoformat()
    funds = gql("{ allFunds { %s } }" % FIELDS)["allFunds"]
    if len(funds) < 100:
        raise RuntimeError(f"only {len(funds)} vaults returned")
    save_day(today, funds)
    save_prices(today, funds)
    book, spare = os.path.join(PUB, "prices.json"), os.path.join(RESTORE, "prices.json")
    # The backup must never stop the snapshot: whatever goes wrong in it is printed and the run carries on.
    filled, back, src, trouble = 0, 0, None, ""
    try:
        filled = backfill()
        back, src = pricebook.restore(PRICES, [book, spare])
    except Exception as e:
        trouble = f"; price restore failed: {str(e)[:120]}"
    hist = history()
    rows = []
    for f in funds:
        tvl = e18(f.get("totalValue")) or 0
        if tvl < MIN_TVL or f.get("isPrivate"): continue
        pm = f.get("performanceMetrics") or {}
        addr = f["address"].lower()
        rows.append({"name": f.get("name"), "manager": f.get("managerName"), "manager_address": (f.get("managerAddress") or "").lower(),
                     "chain": f.get("blockchainCode"), "address": addr, "url": f"https://dhedge.org/vault/{addr}",
                     "category": f.get("category"), "tvl_usd": round(tvl, 2), "share_price_usd": e18(f.get("tokenPrice")),
                     "fees_pct": {"performance": pct(f.get("performanceFeeNumerator")), "manager": pct(f.get("managerFeeNumerator")),
                                  "streaming_per_year": pct(f.get("streamingFeeNumerator")), "entry": pct(f.get("entryFeeNumerator")), "exit": pct(f.get("exitFeeNumerator"))},
                     "dhedge_reported": {k: factor(pm.get(k)) for k in ("week", "month", "quarter", "halfyear", "year")},
                     "created": f.get("blockTime"),
                     **record(hist.get(addr, []))})
    rows.sort(key=lambda r: -r["tvl_usd"])
    os.makedirs(PUB, exist_ok=True)
    doc = {"as_of": today, "source": API, "vaults_listed": len(funds), "min_tvl_usd": MIN_TVL, "vaults": rows,
           "note": "dhedge_reported figures are dHEDGE's own; *_tracked figures are computed by TradingAgentStack from daily share prices it saved itself."}
    with open(os.path.join(PUB, "vaults.json.tmp"), "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(os.path.join(PUB, "vaults.json.tmp"), os.path.join(PUB, "vaults.json"))
    # price history for vaults with a tenth of the publishing minimum or more (and any already in the file): see pricebook
    worth = {f["address"].lower() for f in funds if not f.get("isPrivate") and (e18(f.get("totalValue")) or 0) >= MIN_TVL / 10}
    try:
        days, n = pricebook.publish(PRICES, book, "dhedge", today, worth, also=[spare])
        told = f"; price history published: {days} days, {n} vaults"
    except Exception as e:
        told = f"; price history not published: {str(e)[:120]}"
    print(f"{today}: {len(funds)} listed, {len(rows)} with ${MIN_TVL:,.0f}+" + told
          + (f"; {filled} days of prices written from saved snapshots" if filled else "")
          + (f"; {back} days of prices restored from {src}" if back else "") + trouble)
    return 0

if __name__ == "__main__":
    sys.exit(main())
