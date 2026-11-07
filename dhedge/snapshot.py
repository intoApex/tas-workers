#!/usr/bin/env python3
"""dHEDGE vaults: a daily snapshot from dHEDGE's public GraphQL API, plus our own share-price record.

Public data only, no keys. Once a day:
  1. Save every vault dHEDGE lists (all chains) to /srv/tas/data/dhedge/<day>.json.gz (raw, kept on the server).
  2. Publish /srv/tas/public/dhedge/vaults.json for vaults with at least $50k in them: TVL, fees, manager,
     dHEDGE's own performance figures (labelled as dHEDGE-reported), and our record computed from the
     daily share prices we saved ourselves (return since we started tracking and the worst drop).
The token price is the vault's onchain share price as dHEDGE reports it (USD, 18 decimals).
"""
import datetime as dt, gzip, json, os, sys, urllib.request

API = "https://api-v2.dhedge.org/graphql"
DATA = os.environ.get("DHEDGE_DATA", "/srv/tas/data/dhedge")
PUB = os.environ.get("DHEDGE_PUBLIC", "/srv/tas/public/dhedge")
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

def history():
    """Our own daily share prices: {address: [(day, price), ...]} from the saved snapshots."""
    out = {}
    for name in sorted(os.listdir(DATA)):
        if not name.endswith(".json.gz"): continue
        day = name[:10]
        try: funds = json.load(gzip.open(os.path.join(DATA, name), "rt"))["funds"]
        except Exception: continue
        for f in funds:
            p = e18(f.get("tokenPrice"))
            if p and p > 0:
                out.setdefault(f["address"].lower(), []).append((day, p))
    return out

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
    print(f"{today}: {len(funds)} listed, {len(rows)} with ${MIN_TVL:,.0f}+")
    return 0

if __name__ == "__main__":
    sys.exit(main())
