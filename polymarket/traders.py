#!/usr/bin/env python3
"""Polymarket's top traders: a daily record from Polymarket's public Data API (no keys, documented routes only).

Polymarket has no copy button. People copy its top wallets by hand or with outside bots, and they pick them from
Polymarket's leaderboard, which shows one number: profit. This adds what that number hides, from Polymarket's own data:
how much of a wallet's lifetime profit one bet made, how much it has given back from its best day, and what happened
after we first saw it.

Once a day:
  1. Read the leaderboard: the TOP_N wallets with the biggest 30-day profit (GET /v2/leaderboard, month, by PnL).
  2. For each wallet read three things: its lifetime profit, day by day (GET /v2/user-pnl, all, 1d), its profile numbers
     (GET /v2/user-stats: markets traded, biggest single win, first seen) and what its open positions are worth (GET /v2/value).
  3. Work out, in dollars: lifetime profit, the highest it has been, the most it has given back from a high, and the
     share of lifetime profit that came from the single biggest win.
     There is no percentage return here and no 30% gate: Polymarket does not publish deposits and withdrawals (the fields
     are there and empty), so nobody outside can know how much money a wallet was working with. We say so and stay in dollars.
  4. Remember the day and place each wallet first made the list (watch.json, kept on the server). A wallet that later drops
     off the list is still followed for WATCH_DAYS days.
  5. Publish /srv/tas/public/polymarket/traders.json and /srv/tas/public/polymarket/status.json.

A failed run leaves yesterday's traders.json in place and says why in status.json.
Wallet addresses, names and X handles are Polymarket's public leaderboard data. Standard library only.
Shapes checked against the live API on 2026-11-13 (intoApex/tradingagentstack scripts/probe_feeds.py).
"""
import datetime as dt, json, os, sys, time, urllib.parse, urllib.request

API = "https://data-api.polymarket.com"
DATA = os.environ.get("PM_TRADERS_DATA", "/srv/tas/data/pm-traders")
PUB = os.environ.get("PM_TRADERS_PUBLIC", "/srv/tas/public/polymarket")
TOP_N = int(os.environ.get("PM_TRADERS_TOP_N", "50"))
WATCH_DAYS = int(os.environ.get("PM_TRADERS_WATCH_DAYS", "90"))
WATCH_MAX = int(os.environ.get("PM_TRADERS_WATCH_MAX", "100"))
PAUSE = float(os.environ.get("PM_TRADERS_PAUSE", "0.4"))
MIN_DAYS = 30          # a wallet younger than this is listed, with a note that its record is short
UA = {"User-Agent": "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)", "Accept": "application/json"}


def get(path, tries=4, **q):
    url = API + path + ("?" + urllib.parse.urlencode(q) if q else "")
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {path}: {e}", file=sys.stderr, flush=True)
            time.sleep(6 * (i + 1))


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def day_of(ts):
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date().isoformat()


def board():
    """The leaderboard page as plain rows. Raises with what it saw if the shape is not the one expected."""
    d = get("/v2/leaderboard", time_period="month", sort_by="PNL", limit=TOP_N)
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("leaderboard: no rows; top-level " + (str(sorted(d.keys())[:8]) if isinstance(d, dict) else type(d).__name__))
    out = []
    for r in rows:
        a = str(r.get("user_id") or "").lower()
        if a.startswith("0x") and len(a) == 42 and num(r.get("pnl")) is not None:
            out.append({"address": a, "name": (r.get("user_name") or "").strip() or None, "x": (r.get("x_username") or "").strip() or None,
                        "verified": bool(r.get("verified")), "pnl_30d": num(r.get("pnl")), "shares_30d": num(r.get("volume"))})
    if len(out) < len(rows) * 0.8:
        raise ValueError(f"leaderboard: {len(rows) - len(out)} of {len(rows)} rows unreadable; first row keys {sorted(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]).__name__}")
    return out


def series(points):
    """[(day, lifetime profit in dollars)] from Polymarket's cumulative daily points, one per day, oldest first.
    economic_pnl is Polymarket's all-in figure (positions marked to market plus rebates and rewards); where a day has
    no value for it, the position-only figure stands in, then the chart figure."""
    out = {}
    for p in points or []:
        v = next((num(p.get(k)) for k in ("economic_pnl", "position_pnl", "trade_pnl") if num(p.get(k)) is not None), None)
        if v is not None and p.get("timestamp"):
            out[day_of(p["timestamp"])] = v
    return sorted(out.items())


def record(points, stats, value, seen_day=None):
    """(record, since_seen) for one wallet, either may be None."""
    s = series(points)
    if len(s) < 2:
        return None, None
    last = (points or [{}])[-1]
    pnl = s[-1][1]
    peak, peak_day, giveback, giveback_day, low = s[0][1], s[0][0], 0.0, None, s[0][1]
    for d, v in s:
        if v > peak:
            peak, peak_day = v, d
        if peak - v > giveback:
            giveback, giveback_day = peak - v, d
        low = min(low, v)
    # the last 30 days by the daily series itself. Polymarket's leaderboard figure for the same 30 days can differ from this
    # (2026-11-13, the top wallet: $4.7M on the leaderboard, $2.7M here), so both are kept and the page says which is which.
    d30 = (dt.date.fromisoformat(s[-1][0]) - dt.timedelta(days=30)).isoformat()
    before30 = [v for d, v in s if d <= d30]
    pnl_30d = pnl - before30[-1] if before30 else None
    st = stats or {}
    big = num(st.get("biggest_win"))
    joined = day_of(st["join_date"]) if st.get("join_date") else s[0][0]
    days = (dt.date.fromisoformat(s[-1][0]) - dt.date.fromisoformat(min(joined, s[0][0]))).days
    rec = {"first_day": min(joined, s[0][0]), "last_day": s[-1][0], "days": days,
           "pnl_all_usd": round(pnl), "pnl_30d_series_usd": round(pnl_30d) if pnl_30d is not None else None,
           "peak_pnl_usd": round(peak), "peak_day": peak_day,
           "worst_giveback_usd": round(giveback), "worst_giveback_day": giveback_day,
           # how much of its best total the wallet has handed back at the worst point; only meaningful once it has been well in profit
           "giveback_share": round(giveback / peak, 4) if peak > 1000 else None,
           "lowest_pnl_usd": round(low),
           "markets_traded": st.get("trades") if isinstance(st.get("trades"), int) else None,
           "biggest_win_usd": round(big) if big is not None else None,
           # the single best resolved bet as a share of everything the wallet has made; over 1 means the rest lost money
           "biggest_win_share": round(big / pnl, 4) if big is not None and pnl > 1000 else None,
           "income_usd": round(num(last.get("wallet_income")) or 0), "fees_usd": round(num(last.get("fees")) or 0),
           "unrealized_usd": round(num(last.get("unrealized_pnl")) or 0),
           "volume_usd": round(num(last.get("volume_usdc")) or 0), "fills": last.get("trade_count") if isinstance(last.get("trade_count"), int) else None,
           "open_positions_usd": round(value) if value is not None else None,
           "points": len(s)}
    since = None
    if seen_day:
        before = [v for d, v in s if d <= seen_day]
        after = [(d, v) for d, v in s if d >= seen_day]
        if before and len(after) >= 1 and s[-1][0] > seen_day:
            base = before[-1]
            pk, gb = base, 0.0
            for _, v in after:
                pk = max(pk, v); gb = max(gb, pk - v)
            since = {"from": seen_day, "to": s[-1][0], "pnl_change_usd": round(pnl - base), "worst_giveback_usd": round(gb)}
    return rec, since


def jload(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def jsave(path, obj, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, **kw)
    os.replace(path + ".tmp", path)


def run(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    day = now.date().isoformat()
    t_start = time.time()
    top = board()
    os.makedirs(DATA, exist_ok=True)
    jsave(os.path.join(DATA, day + ".json"), {"day": day, "rows": top})   # the list as we read it, for later checks
    watch_path = os.path.join(DATA, "watch.json")
    watch = jload(watch_path, {})
    for i, r in enumerate(top, 1):
        watch.setdefault(r["address"], {"first_seen": day, "first_rank": i, "first_pnl_30d": r["pnl_30d"], "name": r["name"]})
    by_addr = {r["address"]: r for r in top}
    cutoff = (now - dt.timedelta(days=WATCH_DAYS)).date().isoformat()
    off = sorted((a for a, w in watch.items() if a not in by_addr and w["first_seen"] >= cutoff), key=lambda a: watch[a]["first_seen"], reverse=True)[:WATCH_MAX]
    failed = 0

    def one(addr, rank):
        nonlocal failed
        r, w = by_addr.get(addr) or {}, watch[addr]
        row = {"address": addr, "name": r.get("name") or w.get("name"), "x_username": r.get("x"), "verified": r.get("verified", False),
               "url": f"https://polymarket.com/profile/{addr}", "rank": rank,
               "pnl_30d_usd": round(r["pnl_30d"]) if r.get("pnl_30d") is not None else None,
               "on_board": bool(r), "first_seen": w["first_seen"], "first_rank": w["first_rank"],
               "days_watched": (now.date() - dt.date.fromisoformat(w["first_seen"])).days, "record": None, "since_seen": None}
        try:
            pts = ((get("/v2/user-pnl", user=addr, interval="all", fidelity="1d").get("data") or {}).get("points")) or []
            time.sleep(PAUSE)
            stats = get("/v2/user-stats", user=addr).get("data") or {}
            time.sleep(PAUSE)
            val = num((get("/v2/value", user=addr).get("data") or {}).get("value"))
            row["record"], row["since_seen"] = record(pts, stats, val, w["first_seen"])
        except Exception as e:
            failed += 1
            print(f"{addr}: {e}", file=sys.stderr, flush=True)
        time.sleep(PAUSE)
        return row

    traders = [one(r["address"], i) for i, r in enumerate(top, 1)]
    watched = [one(a, None) for a in off]
    rated = [t for t in traders if t["record"]]
    if not rated:
        raise ValueError(f"no wallet history could be read ({failed} of {len(traders)} wallets failed)")
    for t in traders + watched:
        rec = t["record"]
        t["status"] = "no_history" if not rec else "short_history" if rec["days"] < MIN_DAYS else "ok"
    full = [t for t in rated if t["status"] == "ok"]
    shares = sorted(t["record"]["biggest_win_share"] for t in full if t["record"]["biggest_win_share"] is not None)
    out = {"name": "TradingAgentStack: Polymarket top traders, checked", "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": day,
           "rules": {"top_n": TOP_N, "ranked_by": "30-day profit in dollars, from Polymarket's leaderboard", "min_history_days": MIN_DAYS, "watch_days": WATCH_DAYS,
                     "no_percent_return": "Polymarket does not publish deposits and withdrawals, so the money a wallet worked with is unknown. Everything here is in dollars."},
           "sources": {"api": API, "leaderboard": "/v2/leaderboard", "profit_series": "/v2/user-pnl", "profile": "/v2/user-stats", "open_positions": "/v2/value"},
           "summary": {"rated": len(rated), "with_history": len(full),
                       "one_bet_half": sum(1 for x in shares if x >= 0.5),           # wallets whose single biggest win is half or more of their lifetime profit
                       "median_biggest_win_share": shares[len(shares) // 2] if shares else None,
                       "gave_back_half": sum(1 for t in full if (t["record"]["giveback_share"] or 0) >= 0.5),
                       "down_overall": sum(1 for t in full if t["record"]["pnl_all_usd"] < 0),
                       # wallets whose 30-day figure on the leaderboard is more than a fifth away from the daily series over the same days
                       "leaderboard_differs": sum(1 for t in full if t["pnl_30d_usd"] and t["record"]["pnl_30d_series_usd"] is not None
                                                  and abs(t["pnl_30d_usd"] - t["record"]["pnl_30d_series_usd"]) > 0.2 * abs(t["pnl_30d_usd"])),
                       "watching_since": min((w["first_seen"] for w in watch.values()), default=day), "off_the_list": len(watched)},
           "traders": traders, "watched": watched}
    jsave(os.path.join(PUB, "traders.json"), out, separators=(",", ":"))
    jsave(watch_path, {a: w for a, w in watch.items() if w["first_seen"] >= cutoff or a in by_addr}, indent=0)
    return {"at": out["as_of"], "ok": True, "traders": len(traders), "rated": len(rated), "watched": len(watched), "failed_wallets": failed,
            "seconds": round(time.time() - t_start)}


def main():
    try:
        status = run()
    except Exception as e:
        status = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    jsave(os.path.join(PUB, "status.json"), status)
    print(json.dumps(status), flush=True)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
