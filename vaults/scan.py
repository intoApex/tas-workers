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
  grvt     https://edge.grvt.io/query (vault list) and https://market-data.grvt.io/full/v1/vault_detail, vault_summary_history
           (perp DEX strategy vaults; the venue publishes each vault's share price every 8 hours since launch)
  hibachi  https://data-api.hibachi.xyz/vault/info and /vault/performance   (perp DEX vaults; daily share price since launch)
  lagoon   https://app.lagoon.finance/api/vaults and /api/vault             (on-chain ERC-7540 vaults run by curators, many chains)
  metric   https://api.metric.xyz/public/v1/evm/metadata                    (DEX pools whose pricing is run by a curator or market
           maker; Ethereum, BNB Chain, Robinhood Chain). A pool has no share token, so there is no share price to track:
           these rows carry Metric's own figures only and are published without a record of ours.
Where the venue publishes a vault's share-price history (GRVT, Hibachi), the record is computed from that full history;
everywhere else it is computed from the daily share prices we save ourselves, so it starts on the day we started tracking.
Those saved prices are the one thing here that cannot be fetched again, so for every venue whose records rest on them the
whole history is also published as /srv/tas/public/vaults/<venue>-prices.json, which the site's nightly run copies off this
server, and days missing from the prices folder are written back from that file (or from restore/vaults/ in this repo)
before each scan. See common/pricebook.py.
A venue that fails keeps yesterday's public file; the others still run.
"""
import datetime as dt, gzip, json, os, sys, time, urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
from common import pricebook

DATA = os.environ.get("VAULTS_DATA", "/srv/tas/data/vaults")
PUB = os.environ.get("VAULTS_PUBLIC", "/srv/tas/public/vaults")
RESTORE = os.environ.get("VAULTS_RESTORE", os.path.join(REPO, "restore", "vaults"))
OURS = "our daily snapshots"
UA = "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)"

def get(url, body=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

def iso_ts(x):
    try:
        return int(dt.datetime.strptime(x[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())
    except Exception:
        return None

def daily(points):
    # points: [(unix_seconds, price)] -> [(YYYY-MM-DD, last price of that UTC day)]
    out = {}
    for t, p in sorted(points):
        if p and p > 0:
            out[dt.datetime.utcfromtimestamp(t).date().isoformat()] = p
    return sorted(out.items())

def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None

# ---------- venue adapters: each returns (raw, rows); a row needs id, name, tvl_usd and share_price
# (or no_share_price: True where the venue has none, which publishes the row without a record of ours) ----------

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
    # Paradex publishes each vault's share price since launch (public, no key), so big vaults get their whole record
    hist_raw = {}
    for r in rows:
        if (r.get("tvl_usd") or 0) < 10_000:
            continue
        time.sleep(0.5)
        try:
            h = (get(f"https://api.prod.paradex.trade/v1/vaults/history?address={r['id']}&type=price&resolution=alltime").get("results") or [{}])[0]
            hist_raw[r["id"]] = h
            r["venue_history"] = daily([(int(t) // 1000, num(x)) for t, x in zip(h.get("timestamps") or [], h.get("data") or [])])
        except Exception as e:
            r["history_error"] = str(e)[:120]
    return {"vaults": meta, "summary": summ, "history": hist_raw}, rows

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

GRVT_Q = """query V($first: Int, $where: VaultWhereInput) { vaults(first: $first, where: $where) { totalCount edges { node {
  id name chainVaultID type status managerName description managementFee performanceFee createTime mappedCategories { name } } } } }"""

def grvt():
    g = get("https://edge.grvt.io/query", {"query": GRVT_Q, "variables": {"first": 500, "where": {}}})
    nodes = [e["node"] for e in (((g.get("data") or {}).get("vaults") or {}).get("edges") or [])]
    if len(nodes) < 5:
        raise RuntimeError(f"only {len(nodes)} vaults listed")
    ids = [str(n["chainVaultID"]) for n in nodes if n.get("chainVaultID")]
    det = {str(r.get("vault_id")): r for r in (get("https://market-data.grvt.io/full/v1/vault_detail", {"vault_i_ds": ids}).get("result") or [])}
    rows, hist_raw = [], {}
    for n in nodes:
        if str(n.get("status") or "").lower() != "active":
            continue
        cid = str(n.get("chainVaultID")); d = det.get(cid, {})
        row = {"id": n.get("id"), "name": n.get("name"), "manager": n.get("managerName"),
               "kind": ", ".join(c["name"] for c in (n.get("mappedCategories") or []) if c.get("name")) or n.get("type"),
               "url": "https://grvt.io/exchange/strategies", "tvl_usd": num(d.get("total_equity")), "share_price": num(d.get("share_price")),
               "created": iso_ts(n.get("createTime")),
               "fees_pct": {"management": (num(n.get("managementFee")) or 0) / 1e4, "performance": (num(n.get("performanceFee")) or 0) / 1e4},
               "description": (n.get("description") or "")[:300]}
        if (row["tvl_usd"] or 0) >= 10_000:
            time.sleep(0.6)
            try:
                h = get("https://market-data.grvt.io/full/v1/vault_summary_history", {"vault_id": cid}).get("result") or []
                hist_raw[cid] = h
                row["venue_history"] = daily([(int(x["event_time"]) // 10**9, num(x.get("share_price"))) for x in h if x.get("event_time")])
            except Exception as e:
                row["history_error"] = str(e)[:120]
        rows.append(row)
    return {"vaults": nodes, "detail": list(det.values()), "history": hist_raw}, rows

def hibachi():
    vs = get("https://data-api.hibachi.xyz/vault/info")
    vs = vs if isinstance(vs, list) else (vs.get("vaults") or [])
    if not vs:
        raise RuntimeError("no vaults listed")
    rows, perf = [], {}
    for v in vs:
        vid = v.get("vaultId"); time.sleep(0.6)
        p = get(f"https://data-api.hibachi.xyz/vault/performance?vaultId={vid}&timeRange=All")
        iv = p.get("vaultPerformanceIntervals") or []
        perf[str(vid)] = iv
        last = max(iv, key=lambda x: x.get("timestamp") or 0) if iv else {}
        rows.append({"id": str(vid), "name": v.get("shortDescription") or v.get("symbol"), "manager": None, "kind": None,
                     "symbol": v.get("symbol"), "url": "https://hibachi.xyz/vaults",
                     "tvl_usd": num(last.get("totalValueLocked")) or ((num(v.get("perSharePrice")) or 0) * (num(v.get("outstandingShares")) or 0)),
                     "share_price": num(v.get("perSharePrice")),
                     # Hibachi publishes fees as fractions (0.1 = 10%)
                     "fees_pct": {k: (x * 100 if x is not None and x <= 1 else x) for k, x in (("management", num(v.get("managementFees"))), ("performance", num(v.get("performanceFees"))))},
                     # Hibachi returns 0 for both when it hasn't computed them, so only non-zero values are kept
                     "venue_reported": {k: x for k, x in (("max_drawdown", num(v.get("maxDrawdown"))), ("sharpe", num(v.get("sharpeRatio")))) if x},
                     "venue_history": daily([(int(x["timestamp"]), num(x.get("perSharePrice"))) for x in iv if x.get("timestamp")]),
                     "description": (v.get("description") or "")[:300]})
    return {"vaults": vs, "performance": perf}, rows

LAGOON = "https://app.lagoon.finance/api"

def lagoon():
    listed, page = [], 0
    while page < 20:
        d = get(f"{LAGOON}/vaults?pageIndex={page}&pageSize=100")
        listed += d.get("vaults") or []
        if not d.get("hasNextPage"):
            break
        page += 1; time.sleep(0.5)
    if len(listed) < 20:
        raise RuntimeError(f"only {len(listed)} vaults listed")
    rows, details = [], []
    for v in listed:
        st = v.get("state") or {}
        tvl = num(st.get("totalAssetsUsd"))
        if not v.get("isVisible"):
            continue
        if (tvl or 0) < 100_000:
            # counted as listed, not looked up: it can't reach the page anyway
            rows.append({"id": f"{(v.get('chain') or {}).get('id')}:{(v.get('address') or '').lower()}", "name": v.get("name"), "tvl_usd": tvl, "share_price": None})
            continue
        ch = (v.get("chain") or {}).get("id"); addr = (v.get("address") or "").lower()
        time.sleep(0.4)
        try:
            dd = get(f"{LAGOON}/vault?chainId={ch}&address={addr}")
        except Exception:
            continue
        details.append(dd); s2 = dd.get("state") or {}
        dec = int(((dd.get("asset") or {}).get("decimals")) or 18)
        pps = num(s2.get("pricePerShare"))
        cur = ", ".join(c.get("name") for c in (dd.get("curators") or v.get("curators") or []) if c.get("name"))
        # Lagoon reports APR in percent (2.6 = 2.6%); we store fractions like every other venue
        apr = lambda k: (lambda x: x / 100 if x is not None else None)(num(((s2.get(k) or {}).get("linearNetAprWithoutExtraYields"))))
        rows.append({"id": f"{ch}:{addr}", "name": v.get("name"), "manager": cur or None,
                     "kind": (v.get("strategyCategory") or {}).get("label"), "chain": (v.get("chain") or {}).get("name"),
                     "asset": (v.get("asset") or {}).get("symbol"), "address": addr,
                     "url": f"https://app.lagoon.finance/vault/{ch}/{addr}", "tvl_usd": tvl,
                     # share price in the vault's own asset (USDC, ETH...), so the record is the strategy's return in that asset
                     "share_price": pps / 10 ** dec if pps else None,
                     "venue_reported": {"apr_7d": apr("weeklyApr"), "apr_30d": apr("monthlyApr"), "apr_inception": apr("inceptionApr")},
                     "description": (dd.get("shortDescription") or dd.get("description") or "")[:300]})
    return {"vaults": listed, "details": details}, rows

METRIC = "https://api.metric.xyz/public/v1"
METRIC_CHAINS = {"ethereum": (1, "Ethereum"), "BSC": (56, "BNB Chain"), "Robinhood": (4663, "Robinhood Chain")}

def metric():
    # Metric (metric.xyz): a curator or market maker runs each pool's pricing and anyone can add liquidity. Liquidity sits in
    # price bins, not behind a share token, so there is nothing for us to price day by day: no_share_price rows are published
    # with Metric's own figures and no record of ours. aprBps7d is fees over size; tradingPnlAprBps7d is a position's value
    # against simply holding the two tokens; markout is what the pool made on its trades measured 5 minutes after each one.
    try:
        ids = {c.get("name"): c.get("chainId") for c in (get(f"{METRIC}/chains").get("chains") or [])}
    except Exception:
        ids = {}
    pools, off = [], 0
    while off is not None and len(pools) < 5000:
        d = get(f"{METRIC}/evm/metadata?include24h=true&count=500&offset={off}&sortBy=tvl&sortOrder=desc&minTvlFiat=1000")
        page = d.get("data") or []
        pools += page
        off = d.get("nextOffset") if page else None
        time.sleep(1)
    if len(pools) < 10:
        raise RuntimeError(f"only {len(pools)} pools listed")
    bps = lambda x: None if num(x) is None else round(num(x) / 10000, 6)
    rows = []
    for p in pools:
        known = METRIC_CHAINS.get(p.get("chain"), (None, p.get("chain")))
        cid, addr = ids.get(p.get("chain")) or known[0], p.get("poolAddress")
        if not cid or not addr:
            continue
        mk = lambda w: bps(((((p.get("markoutByWindow") or {}).get(w) or {}).get("byHorizon") or {}).get("5m") or {}).get("aprBps"))
        vol = p.get("volumeByWindow") or {}
        made = num(p.get("createdTimestamp"))
        rows.append({"id": f"{cid}:{addr.lower()}", "name": p.get("pair"), "manager": p.get("adminUsername") or None, "kind": "active pool",
                     "chain": known[1], "address": addr, "url": f"https://app.metric.xyz/pool/{cid}/{addr}",
                     "tvl_usd": num(p.get("tvlFiat")), "share_price": None, "no_share_price": True,
                     "verified_curator": bool(p.get("verifiedAdmin")), "tier": (p.get("classificationSummary") or {}).get("tierQualification"),
                     "deposits_by_allowlist": bool(p.get("depositWhitelistingEnabled")), "paused": bool(p.get("pauseLevel")),
                     "price_feed": p.get("priceProviderStatus"),
                     "created": dt.datetime.utcfromtimestamp(made).date().isoformat() if made else None,
                     "venue_reported": {"markout_apr_7d": mk("7d"), "markout_apr_30d": mk("30d"), "fee_apr_7d": bps(p.get("aprBps7d")),
                                        "vs_holding_apr_7d": bps(p.get("tradingPnlAprBps7d")),
                                        "volume_7d_usd": num(vol.get("7d")), "volume_30d_usd": num(vol.get("30d"))}})
    return {"pools": pools}, rows

VENUES = {"paradex": (paradex, 10_000), "orderly": (orderly, 10_000), "kamino": (kamino, 250_000),
          "grvt": (grvt, 10_000), "hibachi": (hibachi, 10_000), "lagoon": (lagoon, 100_000), "metric": (metric, 50_000)}

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

def prices_dir(venue):
    return os.path.join(DATA, venue, "prices")

def history(venue):
    return pricebook.series(prices_dir(venue))

def record(series):
    if len(series) < 2:
        return {"tracking_days": 0, "tracking_since": series[0][0] if series else None}
    first, last = series[0][1], series[-1][1]
    peak, worst = 0, 0.0
    for _, p in series:
        peak = max(peak, p); worst = min(worst, p / peak - 1)
    d0 = dt.date.fromisoformat(series[0][0]); d1 = dt.date.fromisoformat(series[-1][0])
    days = (d1 - d0).days
    out = {"tracking_since": series[0][0], "tracking_days": days, "return_tracked": round(last / first - 1, 6), "max_drawdown_tracked": round(worst, 6)}
    if days >= 30:
        # annualised, so vaults with records of different lengths can be compared; only shown from 30 days
        out["annualised_tracked"] = round((last / first) ** (365 / days) - 1, 6)
    if days >= 90:
        old = [p for d, p in series if dt.date.fromisoformat(d) <= d1 - dt.timedelta(days=90)]
        if old:
            out["return_90d_tracked"] = round(last / old[-1] - 1, 6)
    return out

def run(venue, fn, min_tvl, today):
    raw, rows = fn()
    book, spare = os.path.join(PUB, f"{venue}-prices.json"), os.path.join(RESTORE, f"{venue}-prices.json")
    # days the prices folder has lost come back from the published history, or from a copy placed in restore/vaults/
    # The backup must never stop a scan: whatever goes wrong in it is reported in the status line and the scan carries on.
    back, src, trouble = 0, None, ""
    try:
        back, src = pricebook.restore(prices_dir(venue), [book, spare])
    except Exception as e:
        trouble = f"; price restore failed: {str(e)[:120]}"
    save_day(venue, today, raw)
    save_prices(venue, today, rows)
    hist = history(venue)
    # vaults worth keeping a price history for: a tenth of the publishing minimum, so one that grows into the list has a record
    worth = {r["id"] for r in rows if r.get("id") and r.get("share_price") and (r.get("tvl_usd") or 0) >= min_tvl / 10}
    keep = []
    for r in rows:
        if (r.get("tvl_usd") or 0) < min_tvl:
            continue
        if r.pop("no_share_price", False):
            # the venue has no share price (Metric): its own figures only, and no record of ours
            keep.append({**r, "tracking_since": None, "tracking_days": 0, "computed_from": None}); continue
        if not r.get("share_price"):
            continue
        vh = r.pop("venue_history", None)
        if vh and len(vh) >= 2:
            # the venue's own published share prices since launch; we compute the record from them
            keep.append({**r, **record(vh), "computed_from": "venue share-price history"})
        else:
            keep.append({**r, **record(hist.get(r["id"], [])), "computed_from": OURS})
    keep.sort(key=lambda r: -(r["tvl_usd"] or 0))
    os.makedirs(PUB, exist_ok=True)
    doc = {"as_of": today, "venue": venue, "listed": len(rows), "min_tvl_usd": min_tvl, "rates_as": "fraction", "vaults": keep,
           "note": "venue_reported figures are the venue's own; *_tracked figures are computed by TradingAgentStack, from the venue's published share-price history where it has one (computed_from) and otherwise from daily share prices we saved ourselves."}
    tmp = os.path.join(PUB, f"{venue}.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(PUB, f"{venue}.json"))
    msg = f"{venue}: {len(rows)} listed, {len(keep)} with ${min_tvl:,.0f}+"
    if any(k["computed_from"] == OURS for k in keep) or os.path.exists(book) or os.path.exists(spare):
        try:
            days, n = pricebook.publish(prices_dir(venue), book, venue, today, worth, also=[spare])
            msg += f"; price history published: {days} days, {n} vaults"
        except Exception as e:
            trouble += f"; price history not published: {str(e)[:120]}"
    if back:
        msg += f"; {back} days of prices restored from {os.path.relpath(src, REPO) if src.startswith(REPO) else src}"
    return msg + trouble

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
