#!/usr/bin/env python3
"""Vaults on more venues: a daily snapshot from each venue's own public API, plus our own share-price record.

Public data only, no keys, one request per list per day. For every venue below, once a day:
  1. Save the venue's whole public vault list to /srv/tas/data/vaults/<venue>/<day>.json.gz (raw, kept on the server).
  2. Publish /srv/tas/public/vaults/<venue>.json for vaults with at least the venue's minimum in them: name, manager,
     size, fees, the venue's own reported figures (labelled as the venue's) and our record computed from the daily
     share prices we saved ourselves (return since we started tracking and the worst drop).

Venues (all public endpoints, no login):
  paradex  https://api.prod.paradex.trade/v1/vaults and /v1/vaults/summary   (perp DEX vaults, Starknet)
  orderly  https://api-sv.orderly.org/v1/public/strategy_vault/vault/info    (Orderly strategy vaults)
  kamino   https://api.kamino.finance/strategies/metrics?env=mainnet-beta    (automated liquidity strategies, Solana)
A venue that fails keeps yesterday's public file; the others still run.
"""
import datetime as dt, gzip, json, os, sys, time, urllib.request

DATA = os.environ.get("VAULTS_DATA", "/srv/tas/data/vaults")
PUB = os.environ.get("VAULTS_PUBLIC", "/srv/tas/public/vaults")
UA = "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

# ---------- venue adapters: each returns (raw, rows); a row needs id, name, tvl_usd and share_price ----------

def paradex():
    meta = get("https://api.prod.paradex.trade/v1/vaults").get("results") or []
    time.sleep(1)
    summ = get("https://api.prod.paradex.trade/v1/vaults/summary").get("results") or []
    if len(meta) < 50:
        raise RuntimeError(f"only {len(meta)} vaults listed")
    by = {m["address"].lower(): m for m in meta if m.get("address")}
    rows = []
    for s in summ:
        a = (s.get("address") or "").lower()
        m = by.get(a, {})
        if m.get("status") != "ACTIVE" or (m.get("visibility") or {}).get("positions") is False:
            continue
        rows.append({"id": a, "name": m.get("name"), "manager": m.get("curator_name") or None, "kind": m.get("kind"),
                     "url": f"https://app.paradex.trade/vaults/{a}", "tvl_usd": num(s.get("tvl")), "share_price": num(s.get("vtoken_price")),
                     "depositors": s.get("num_depositors"), "created": (m.get("created_at") or 0) // 1000 or None,
                     "fees_pct": {"profit_share": num(m.get("profit_share"))},
                     "lockup_days": m.get("lockup_period"),
                     "venue_reported": {"return_30d": num(s.get("roi_30d")), "return_total": num(s.get("total_roi")),
                                        "max_drawdown": (-num(s.get("max_drawdown"))) if num(s.get("max_drawdown")) is not None else None},
                     "description": (m.get("strategy_description") or m.get("description") or "")[:300]})
    return {"vaults": meta, "summary": summ}, rows

def orderly():
    d = get("https://api-sv.orderly.org/v1/public/strategy_vault/vault/info")
    vs = ((d.get("data") or {}).get("rows")) or []
    if not vs:
        raise RuntimeError("no vaults listed")
    rows = []
    for v in vs:
        if str(v.get("status") or "").lower() not in ("live", "active", ""):
            continue
        rows.append({"id": v.get("vault_id"), "name": v.get("vault_name"), "manager": v.get("sp_name"), "kind": v.get("vault_type"),
                     "address": (v.get("vault_address") or "").lower() or None,
                     "url": "https://app.orderly.network/vaults", "tvl_usd": num(v.get("tvl")), "share_price": num(v.get("est_main_share_price")),
                     "depositors": v.get("lp_counts"), "created": (num(v.get("vault_start_time")) or 0) // 1000 or None,
                     "fees_pct": {"performance": (num(v.get("performance_fee_rate")) or 0) * 100 if v.get("performance_fee_rate") is not None else None},
                     "venue_reported": {"apy_30d": num(v.get("30d_apy")), "apy_lifetime": num(v.get("lifetime_apy"))},
                     "description": (v.get("description") or "")[:300]})
    return d, rows

def kamino():
    d = get("https://api.kamino.finance/strategies/metrics?env=mainnet-beta&status=LIVE")
    if not isinstance(d, list) or len(d) < 50:
        raise RuntimeError(f"only {len(d) if isinstance(d, list) else 0} strategies listed")
    rows = []
    for s in d:
        pair = f"{s.get('tokenA') or '?'}-{s.get('tokenB') or '?'}"
        sid = s.get("strategy")
        k = (s.get("kaminoApy") or {}).get("vault") or {}
        # sharePrice is the USD value of one share, so our record includes the price moves of both tokens, not only fees
        rows.append({"id": sid, "name": pair, "manager": "Kamino", "kind": "automated liquidity",
                     "url": f"https://app.kamino.finance/liquidity/{sid}", "tvl_usd": num(s.get("totalValueLocked")), "share_price": num(s.get("sharePrice")),
                     "venue_reported": {"apy_7d": num(k.get("apy7d")), "apy_30d": num(k.get("apy30d"))}})
    return d, rows

VENUES = {"paradex": (paradex, 10_000), "orderly": (orderly, 10_000), "kamino": (kamino, 250_000)}

# ---------- shared: save, history, record, publish ----------

def save_day(venue, day, raw):
    d = os.path.join(DATA, venue)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{day}.json.gz")
    with gzip.open(p + ".tmp", "wt") as f:
        json.dump({"at": dt.datetime.utcnow().isoformat() + "Z", "raw": raw}, f)
    os.replace(p + ".tmp", p)

def save_prices(venue, day, rows):
    # our own daily share prices, one small file per day: {id: price}
    d = os.path.join(DATA, venue, "prices")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{day}.json"), "w") as f:
        json.dump({r["id"]: r["share_price"] for r in rows if r.get("id") and r.get("share_price")}, f, separators=(",", ":"))

def history(venue):
    out, d = {}, os.path.join(DATA, venue, "prices")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            prices = json.load(open(os.path.join(d, name)))
        except Exception:
            continue
        for k, p in prices.items():
            if p and p > 0:
                out.setdefault(k, []).append((name[:10], p))
    return out

def record(series):
    if len(series) < 2:
        return {"tracking_days": 0, "tracking_since": series[0][0] if series else None}
    first, last = series[0][1], series[-1][1]
    peak, worst = 0, 0.0
    for _, p in series:
        peak = max(peak, p); worst = min(worst, p / peak - 1)
    d0 = dt.date.fromisoformat(series[0][0]); d1 = dt.date.fromisoformat(series[-1][0])
    return {"tracking_since": series[0][0], "tracking_days": (d1 - d0).days, "return_tracked": round(last / first - 1, 6), "max_drawdown_tracked": round(worst, 6)}

def run(venue, fn, min_tvl, today):
    raw, rows = fn()
    save_day(venue, today, raw)
    save_prices(venue, today, rows)
    hist = history(venue)
    keep = [{**r, **record(hist.get(r["id"], []))} for r in rows if (r.get("tvl_usd") or 0) >= min_tvl and r.get("share_price")]
    keep.sort(key=lambda r: -(r["tvl_usd"] or 0))
    os.makedirs(PUB, exist_ok=True)
    doc = {"as_of": today, "venue": venue, "listed": len(rows), "min_tvl_usd": min_tvl, "vaults": keep,
           "note": "venue_reported figures are the venue's own; *_tracked figures are computed by TradingAgentStack from daily share prices it saved itself."}
    tmp = os.path.join(PUB, f"{venue}.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(PUB, f"{venue}.json"))
    return f"{venue}: {len(rows)} listed, {len(keep)} with ${min_tvl:,.0f}+"

def main():
    today = dt.datetime.utcnow().date().isoformat()
    only = sys.argv[1:] or list(VENUES)
    status, bad = {}, 0
    for v in only:
        fn, min_tvl = VENUES[v]
        try:
            msg = run(v, fn, min_tvl, today); status[v] = {"ok": True, "msg": msg}; print(msg)
        except Exception as e:
            bad += 1; status[v] = {"ok": False, "error": str(e)[:200]}; print(f"{v}: FAILED {e}", file=sys.stderr)
        time.sleep(2)
    os.makedirs(PUB, exist_ok=True)
    with open(os.path.join(PUB, "status.json"), "w") as fh:
        json.dump({"at": dt.datetime.utcnow().isoformat() + "Z", "venues": status}, fh)
    return 1 if bad == len(only) else 0

if __name__ == "__main__":
    sys.exit(main())
