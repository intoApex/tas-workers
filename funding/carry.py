#!/usr/bin/env python3
"""Funding rates and the plain delta-neutral carry, from each venue's public funding history (2026-11-17).

Owner, Nov 17: TradingAgentStack should be one place for all automated trading, DeFi strategies included (funding, delta neutral,
liquidity). The first piece is the simplest delta-neutral strategy there is: hold a coin and short the same amount of its perpetual,
so price moves cancel and what is left is the funding the short side is paid (or pays). Every venue publishes its funding history,
so what that carry really earned can be worked out exactly, coin by coin and venue by venue, before anyone puts money in a vault
that claims to do it.

Once a day:
  1. Pick the coins: the TOP_N biggest Hyperliquid perps by open interest (from metaAndAssetCtxs), plus BTC, ETH and SOL always.
  2. For each venue and coin, read the funding history we don't have yet (the first run reads a year) and keep it in
     DATA/<venue>/<coin>.json as [time_ms, rate] pairs, rate as a fraction per funding period. Older than KEEP_DAYS is dropped.
       hyperliquid  POST api.hyperliquid.xyz/info {"type":"fundingHistory"}            hourly
       binance      fapi.binance.com/fapi/v1/fundingRate (USD-M, {COIN}USDT)            8-hourly (some 4-hourly)
       bybit        api.bybit.com/v5/market/funding/history (linear, {COIN}USDT)       8-hourly (some 4- or 1-hourly)
       dydx         indexer.dydx.trade/v4/historicalFunding/{COIN}-USD                  hourly, CORE coins only (the API pages 100 rows)
     Binance and Bybit refuse US servers; this server is in Frankfurt.
  3. Work out, per venue and coin:
       funding_apr_<w>  the average funding over the window, as a yearly rate (the sum of the rates per year of time covered)
       carry            the short-perp, long-spot position: equity compounds the day's funding every day (positive funding is
                        paid to shorts), minus ROUND_TRIP_COST once for opening and closing both legs. Return over 90 and 365
                        days, the worst drop of that equity, and a weekly curve.
     It leaves out: price differences between the spot and the perp (basis), which move the mark-to-market but net out when
     both legs close; the margin the short needs and its liquidation risk in a sharp rally; borrowing; and taxes. The page says so.
  4. Publish /srv/tas/public/funding/carry.json and /srv/tas/public/funding/status.json.

Public endpoints only, no keys, a polite pause between requests. Standard library only. A failed venue keeps the history it
already has and is named in status.json; the others still publish.
"""
import datetime as dt, json, os, sys, time, urllib.parse, urllib.request

DATA = os.environ.get("FUNDING_DATA", "/srv/tas/data/funding")
PUB = os.environ.get("FUNDING_PUBLIC", "/srv/tas/public/funding")
TOP_N = int(os.environ.get("FUNDING_TOP_N", "15"))
CORE = ["BTC", "ETH", "SOL"]
KEEP_DAYS, FIRST_DAYS = 400, 365
ROUND_TRIP_COST = 0.003     # 0.30%: spot buy and sell plus perp sell and buy back at typical taker fees, once per position
PAUSE = float(os.environ.get("FUNDING_PAUSE", "0.35"))
UA = {"User-Agent": "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)", "Accept": "application/json"}
HOUR, DAY = 3_600_000, 86_400_000
WINDOWS = (("7d", 7), ("30d", 30), ("90d", 90), ("365d", 365))


def get(url, body=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    h = {**UA, **({"Content-Type": "application/json"} if body is not None else {})}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
        return json.loads(r.read())


def now_ms():
    return int(time.time() * 1000)


# ---------- venue readers: each returns new [time_ms, rate] pairs after `since` (exclusive), oldest first ----------

def hyperliquid(coin, since):
    out, start = [], since + 1
    for _ in range(40):
        rows = get("https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": coin, "startTime": start})
        if not rows:
            break
        out += [[int(r["time"]), float(r["fundingRate"])] for r in rows]
        if len(rows) < 500:
            break
        start = int(rows[-1]["time"]) + 1
        time.sleep(PAUSE)
    return out


def binance(coin, since):
    out, start = [], since + 1
    for _ in range(20):
        q = urllib.parse.urlencode({"symbol": f"{coin}USDT", "startTime": start, "limit": 1000})
        rows = get(f"https://fapi.binance.com/fapi/v1/fundingRate?{q}")
        if not rows:
            break
        out += [[int(r["fundingTime"]), float(r["fundingRate"])] for r in rows]
        if len(rows) < 1000:
            break
        start = int(rows[-1]["fundingTime"]) + 1
        time.sleep(PAUSE)
    return out


def bybit(coin, since):
    # Bybit pages backwards from endTime, 200 rows at a time
    out, end = [], now_ms()
    for _ in range(40):
        q = urllib.parse.urlencode({"category": "linear", "symbol": f"{coin}USDT", "endTime": end, "limit": 200})
        d = get(f"https://api.bybit.com/v5/market/funding/history?{q}")
        rows = ((d.get("result") or {}).get("list")) or []
        if d.get("retCode") not in (0, None) or not rows:
            break
        page = [[int(r["fundingRateTimestamp"]), float(r["fundingRate"])] for r in rows]
        out += [p for p in page if p[0] > since]
        oldest = min(p[0] for p in page)
        if oldest <= since or len(rows) < 200:
            break
        end = oldest - 1
        time.sleep(PAUSE)
    return sorted(out)


def dydx(coin, since):
    out, before = [], None
    for _ in range(100):
        q = {"limit": 100}
        if before:
            q["effectiveBeforeOrAt"] = before
        d = get(f"https://indexer.dydx.trade/v4/historicalFunding/{coin}-USD?" + urllib.parse.urlencode(q))
        rows = d.get("historicalFunding") or []
        if not rows:
            break
        page = [[int(dt.datetime.fromisoformat(r["effectiveAt"].replace("Z", "+00:00")).timestamp() * 1000), float(r["rate"])] for r in rows]
        out += [p for p in page if p[0] > since]
        oldest = min(p[0] for p in page)
        if oldest <= since or len(rows) < 100:
            break
        before = dt.datetime.fromtimestamp((oldest - 1) / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        time.sleep(PAUSE)
    return sorted(out)


VENUES = {"hyperliquid": (hyperliquid, "Hyperliquid", "{c}"), "binance": (binance, "Binance", "{c}USDT"),
          "bybit": (bybit, "Bybit", "{c}USDT"), "dydx": (dydx, "dYdX", "{c}-USD")}


def pick_coins():
    """CORE plus the TOP_N Hyperliquid perps by open interest in dollars. Coins whose name starts with 'k' (Hyperliquid's
    thousand-unit contracts) are left out, because the other venues list them under different names."""
    try:
        meta, ctxs = get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
        oi = []
        for a, c in zip(meta["universe"], ctxs):
            if a.get("isDelisted") or a["name"].startswith("k"):
                continue
            oi.append((float(c.get("openInterest") or 0) * float(c.get("markPx") or 0), a["name"]))
        top = [n for _, n in sorted(oi, reverse=True)[:TOP_N]]
    except Exception as e:
        print(f"coin list from Hyperliquid failed, using the core coins only: {e}")
        top = []
    return list(dict.fromkeys(CORE + top))


# ---------- the record ----------

def series_path(venue, coin):
    return os.path.join(DATA, venue, f"{coin}.json")


def load(venue, coin):
    try:
        return json.load(open(series_path(venue, coin)))
    except Exception:
        return []


def save(venue, coin, rows):
    os.makedirs(os.path.join(DATA, venue), exist_ok=True)
    cut = now_ms() - KEEP_DAYS * DAY
    rows = sorted({t: r for t, r in rows if t >= cut}.items())
    tmp = series_path(venue, coin) + ".tmp"
    json.dump([[t, r] for t, r in rows], open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, series_path(venue, coin))
    return [[t, r] for t, r in rows]


def apr(rows, days, end):
    """Average funding over the last `days` as a yearly rate: the rates summed, scaled to a year of the time they cover.
    None when the window is less than 90% covered."""
    lo = end - days * DAY
    w = [(t, r) for t, r in rows if t > lo]
    if len(w) < 2:
        return None
    span = end - max(lo, w[0][0])
    if span < 0.9 * days * DAY:
        return None
    return sum(r for _, r in w) * (365 * DAY / span)


def daily(rows):
    """{day: summed funding rate that day}"""
    out = {}
    for t, r in rows:
        d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat()
        out[d] = out.get(d, 0.0) + r
    return out


def carry(rows, days, end):
    """The short-perp, long-spot position over the last `days`: (return, worst drop, weekly curve), or None when the
    window is less than 90% covered. Equity starts at 1, compounds each day's funding, and pays ROUND_TRIP_COST once."""
    lo = end - days * DAY
    w = [(t, r) for t, r in rows if t > lo]
    if not w or (end - w[0][0]) < 0.9 * days * DAY:
        return None
    eq, peak, worst, curve = 1.0 - ROUND_TRIP_COST, 1.0, 0.0, []
    worst = min(worst, eq / peak - 1)
    for i, (d, r) in enumerate(sorted(daily(w).items())):
        eq *= 1 + r
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
        if i % 7 == 0:
            curve.append([d, round(eq - 1, 5)])
    curve.append([sorted(daily(w))[-1], round(eq - 1, 5)])
    return {"return": round(eq - 1, 6), "max_drawdown": round(worst, 6), "curve": curve}


def summarize(venue, coin, rows, end):
    if not rows:
        return None
    gaps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]))
    interval_h = round(gaps[len(gaps) // 2] / HOUR, 2) if gaps else None
    last30 = [r for t, r in rows if t > end - 30 * DAY]
    row = {"venue": venue, "venue_name": VENUES[venue][1], "coin": coin, "symbol": VENUES[venue][2].format(c=coin),
           "interval_hours": interval_h, "points": len(rows),
           "from": dt.datetime.fromtimestamp(rows[0][0] / 1000, dt.timezone.utc).date().isoformat(),
           "to": dt.datetime.fromtimestamp(rows[-1][0] / 1000, dt.timezone.utc).date().isoformat(),
           "latest_rate": rows[-1][1],
           "share_positive_30d": round(sum(1 for r in last30 if r > 0) / len(last30), 4) if last30 else None}
    for name, days in WINDOWS:
        a = apr(rows, days, end)
        row[f"funding_apr_{name}"] = round(a, 6) if a is not None else None
    for name, days in (("90d", 90), ("365d", 365)):
        row[f"carry_{name}"] = carry(rows, days, end)
    return row


def main():
    os.makedirs(PUB, exist_ok=True)
    end, coins = now_ms(), pick_coins()
    rows, trouble, fetched = [], {}, 0
    for venue, (fn, _, _) in VENUES.items():
        for coin in (CORE if venue == "dydx" else coins):
            have = load(venue, coin)
            since = have[-1][0] if have else end - FIRST_DAYS * DAY
            try:
                new = fn(coin, since)
                fetched += len(new)
                have = save(venue, coin, have + new) if new else have
            except Exception as e:
                trouble[f"{venue}:{coin}"] = str(e)[:160]
            s = summarize(venue, coin, have, end)
            if s:
                rows.append(s)
            time.sleep(PAUSE)
    as_of = dt.datetime.now(dt.timezone.utc).date().isoformat()
    best = sorted((r for r in rows if r.get("carry_365d")), key=lambda r: -r["carry_365d"]["return"])[:10]
    out = {"as_of": as_of, "coins": coins, "venues": {k: v[1] for k, v in VENUES.items()},
           "round_trip_cost": ROUND_TRIP_COST,
           "method": ("Funding history from each venue's public API. funding_apr_* is the average funding over the window as a yearly rate. "
                      "carry_* is a position that holds the coin and shorts the same amount of its perpetual: its equity compounds each day's "
                      f"funding (positive funding is paid to the short) and pays {ROUND_TRIP_COST:.2%} once for opening and closing both legs. "
                      "It leaves out the spot-perp price gap (basis), the short's margin and liquidation risk in a sharp rally, borrowing and taxes."),
           "best_365d": [{"venue": r["venue"], "coin": r["coin"], "return": r["carry_365d"]["return"], "max_drawdown": r["carry_365d"]["max_drawdown"]} for r in best],
           "rows": rows}
    tmp = os.path.join(PUB, "carry.json.tmp")
    json.dump(out, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, os.path.join(PUB, "carry.json"))
    json.dump({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "rows": len(rows), "new_points": fetched,
               "failed": trouble}, open(os.path.join(PUB, "status.json"), "w"), indent=1)
    print(f"funding: {len(rows)} venue-coin rows, {fetched} new points, {len(trouble)} failed")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
