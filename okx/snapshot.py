"""Daily snapshot of OKX's official public copy-trading data (no keys, public endpoints only).

Raw data stays on the server in /data/okx/<date>/ (not served). The public file /public/okx-status.json
holds counts only, never trader names or codes. Polite rate: about 2 requests a second.
"""
import gzip, json, os, time, urllib.request, urllib.parse, datetime as dt

BASE = "https://www.okx.com/api/v5/copytrading/"
UA = "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/)"
DATA, PUBLIC = "/data/okx", "/public"
TOP_N = int(os.environ.get("OKX_TOP_N", "200"))
MAX_PAGES = int(os.environ.get("OKX_MAX_PAGES", "60"))
RUN_AT_UTC_HOUR = int(os.environ.get("OKX_RUN_HOUR", "1"))

def get(path, **q):
    url = BASE + path + "?" + urllib.parse.urlencode(q)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read().decode())
            time.sleep(1.0)
            if j.get("code") == "0":
                return j.get("data", [])
            if j.get("code") in ("50011", "50061"):  # rate limited
                time.sleep(5 * (attempt + 1)); continue
            return {"error": j.get("code"), "msg": j.get("msg")}
        except Exception as e:  # network hiccup
            time.sleep(3 * (attempt + 1)); err = str(e)
    return {"error": "fetch_failed", "msg": err[:200]}

def write_gz(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt") as f: json.dump(obj, f)
    os.replace(tmp, path)

def run_once():
    day = dt.datetime.utcnow().strftime("%Y-%m-%d")
    out = os.path.join(DATA, day)
    started = time.time()
    ranks, page, total = [], 1, None
    while page <= MAX_PAGES:
        d = get("public-lead-traders", instType="SWAP", sortType="overview", page=str(page), limit="20")
        if not isinstance(d, list) or not d: break
        total = int(d[0].get("totalPage", "1") or 1)
        ranks += d[0].get("ranks", [])
        if page >= total: break
        page += 1
    write_gz(os.path.join(out, "ranks.json.gz"), {"at": dt.datetime.utcnow().isoformat() + "Z", "pages": page, "totalPage": total, "ranks": ranks})
    detail, errors = {}, 0
    for r in ranks[:TOP_N]:
        c = r.get("uniqueCode")
        if not c: continue
        rec = {
            "stats": get("public-stats", instType="SWAP", uniqueCode=c, lastDays="4"),
            "daily_pnl": get("public-pnl", instType="SWAP", uniqueCode=c, lastDays="4"),
            "weekly_pnl": get("public-weekly-pnl", instType="SWAP", uniqueCode=c),
            "positions_closed": get("public-subpositions-history", instType="SWAP", uniqueCode=c, limit="100"),
        }
        errors += sum(1 for v in rec.values() if isinstance(v, dict) and v.get("error"))
        detail[c] = rec
    write_gz(os.path.join(out, "detail.json.gz"), {"at": dt.datetime.utcnow().isoformat() + "Z", "traders": detail})
    status = {"at": dt.datetime.utcnow().isoformat() + "Z", "day": day, "lead_traders_listed": len(ranks), "pages": page,
              "detailed": len(detail), "errors": errors, "seconds": round(time.time() - started),
              "days_collected": len([d for d in os.listdir(DATA) if d[:2] == "20"]),
              "note": "counts only; raw data stays on the server"}
    tmp = os.path.join(PUBLIC, "okx-status.json.tmp")
    with open(tmp, "w") as f: json.dump(status, f, indent=1)
    os.replace(tmp, os.path.join(PUBLIC, "okx-status.json"))
    print(json.dumps(status), flush=True)

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    while True:
        day = dt.datetime.utcnow().strftime("%Y-%m-%d")
        done = os.path.exists(os.path.join(DATA, day, "detail.json.gz"))
        if not done and dt.datetime.utcnow().hour >= RUN_AT_UTC_HOUR:
            try: run_once()
            except Exception as e: print("run failed:", e, flush=True)
        time.sleep(600)
