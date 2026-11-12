#!/usr/bin/env python3
"""Hyperliquid's top traders: a daily record from Hyperliquid's public data (no keys, public endpoints only).

Hyperliquid has no copy-trading button of its own. People copy its top wallets by hand or through outside apps, and they
pick those wallets from Hyperliquid's leaderboard. This asks the question the leaderboard doesn't answer: what does a
wallet's whole record look like, and what happened to the wallets that were on top after we started watching them?

Once a day:
  1. Read Hyperliquid's full leaderboard (one public file, every ranked wallet).
  2. Take the TOP_N wallets with the biggest 30-day profit among accounts holding at least MIN_ACCOUNT dollars.
     That choice is ours, not Hyperliquid's ranking; it is written into the output under "rules".
  3. For each, read the wallet's own account history from Hyperliquid and work out the record ourselves:
     return, worst drop and Bitcoin over the same dates. Deposits and withdrawals are not profit (Modified Dietz,
     the same sums as the site's Hyperliquid vault ratings, scripts/rate_hl_vaults.py in intoApex/tradingagentstack).
  4. Remember the day and place each wallet first made the list (watch.json, kept on the server). A wallet that later
     drops off the list is still followed for WATCH_DAYS days, so "the top wallets, N days later" can be shown with the
     ones that fell away still in it.
  5. Publish /srv/tas/public/hl/traders.json (the records) and /srv/tas/public/hl/status.json (did the run work).
     A small copy of the day's leaderboard cut is kept in /srv/tas/data/hl-traders/<day>.json.gz (not served).

A failed run leaves yesterday's traders.json in place and says why in status.json.
Wallet addresses and display names are Hyperliquid's public leaderboard data. Standard library only.
"""
import datetime as dt, gzip, json, math, os, sys, time, urllib.request

API = "https://api.hyperliquid.xyz/info"
BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DATA = os.environ.get("HL_TRADERS_DATA", "/srv/tas/data/hl-traders")
PUB = os.environ.get("HL_TRADERS_PUBLIC", "/srv/tas/public/hl")
TOP_N = int(os.environ.get("HL_TRADERS_TOP_N", "50"))
MIN_ACCOUNT = float(os.environ.get("HL_TRADERS_MIN_ACCOUNT", "100000"))
WATCH_DAYS = int(os.environ.get("HL_TRADERS_WATCH_DAYS", "90"))
WATCH_MAX = int(os.environ.get("HL_TRADERS_WATCH_MAX", "100"))   # wallets off the list that are still followed
PAUSE = float(os.environ.get("HL_TRADERS_PAUSE", "1.0"))        # seconds between wallet requests
MIN_CAPITAL, DD_GATE, WINDOW_DAYS, MIN_HISTORY_DAYS = 10_000, 0.30, 365, 30
UA = {"User-Agent": "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)"}
DAY = 86_400_000


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return json.loads(r.read())


def post(body, tries=4):
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=data, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {e}", file=sys.stderr, flush=True)
            time.sleep(8 * (i + 1))


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()


def ms_of(day):
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# ---------- the leaderboard ----------
def board_rows(raw):
    """Hyperliquid's leaderboard file as plain rows. Raises with what it saw if the shape is not the one expected."""
    rows = raw.get("leaderboardRows") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        raise ValueError("leaderboard: no rows; top-level " + (str(sorted(raw.keys())[:8]) if isinstance(raw, dict) else type(raw).__name__))
    out, bad = [], 0
    for r in rows:
        try:
            wp = r.get("windowPerformances")
            wins = dict(wp) if isinstance(wp, list) else (wp or {})
            month, alltime = wins.get("month") or {}, wins.get("allTime") or {}
            addr = str(r["ethAddress"]).lower()
            if not (addr.startswith("0x") and len(addr) == 42):
                raise ValueError("address")
            out.append({"address": addr, "name": (r.get("displayName") or None), "account": num(r.get("accountValue")),
                        "pnl_30d": num(month.get("pnl")), "roi_30d": num(month.get("roi")), "vlm_30d": num(month.get("vlm")),
                        "pnl_all": num(alltime.get("pnl")), "roi_all": num(alltime.get("roi"))})
        except Exception:
            bad += 1
    if not out or bad > len(rows) * 0.2:
        raise ValueError(f"leaderboard: {bad} of {len(rows)} rows unreadable; first row keys {sorted(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]).__name__}")
    return out


def pick(rows):
    ok = [r for r in rows if (r["account"] or 0) >= MIN_ACCOUNT and r["pnl_30d"] is not None and (r["vlm_30d"] or 0) > 0]
    ok.sort(key=lambda r: -r["pnl_30d"])
    return ok


# ---------- one wallet's record (same sums as the site's vault ratings) ----------
def series(pf, key):
    s = pf.get(key) or {}
    av = [(int(t), float(v)) for t, v in s.get("accountValueHistory", [])]
    pnl = [(int(t), float(v)) for t, v in s.get("pnlHistory", [])]
    return av, pnl


def steps(av, pnl, t_from, started=False):
    """Modified Dietz step returns between consecutive points that start at or after t_from."""
    out = []
    for i in range(1, min(len(av), len(pnl))):
        t0, t1 = av[i - 1][0], av[i][0]
        if t0 < t_from:
            continue
        a0 = av[i - 1][1]
        if not started:
            if a0 < MIN_CAPITAL:
                continue
            started = True
        dp = pnl[i][1] - pnl[i - 1][1]
        flow = (av[i][1] - a0) - dp
        den = a0 + 0.5 * flow
        if den <= 0:
            continue
        out.append((t0, t1, dp / den))
    return out


def chain(rs):
    g, peak, mdd = 1.0, 1.0, 0.0
    for r in rs:
        g *= 1 + r
        peak = max(peak, g)
        mdd = min(mdd, g / peak - 1)
    return g - 1, mdd


def record(portfolio, btc_close, now_ms, seen_ms=None):
    """(record, since_seen) for one wallet, either may be None. portfolio is Hyperliquid's own list of [window, history]."""
    pf = dict(portfolio or [])
    av_all, pnl_all = series(pf, "allTime")
    av_m, pnl_m = series(pf, "month")
    long_steps = steps(av_all, pnl_all, now_ms - WINDOW_DAYS * DAY)
    if not long_steps:
        return None, None
    m0 = av_m[0][0] if av_m else now_ms
    cut = next((t1 for _, t1, _ in long_steps if t1 >= m0), None)   # older history in wide steps, the last month daily
    combined = long_steps if cut is None else [s for s in long_steps if s[1] <= cut] + steps(av_m, pnl_m, cut - DAY // 2, started=True)
    t_from, t_to = combined[0][0], combined[-1][1]
    ret, mdd = chain([r for _, _, r in combined])
    rs = [r for _, _, r in long_steps]
    days = (long_steps[-1][1] - long_steps[0][0]) / DAY
    per_year = len(rs) / days * 365 if days > 0 else 0
    down = math.sqrt(sum(min(r, 0) ** 2 for r in rs) / len(rs))
    sortino = (sum(rs) / len(rs)) / down * math.sqrt(per_year) if down > 0 else None
    daily = steps(av_m, pnl_m, 0, started=True)
    r30 = chain([r for _, _, r in daily])[0] if daily else None
    worst = min((r for _, _, r in daily), default=None)

    def btc_at(t):
        prior = [c for k, c in btc_close if k <= day_of(t)]
        return prior[-1] if prior else None

    b0, b1 = btc_at(t_from), btc_at(t_to)
    big = max(abs(r) for _, _, r in combined)
    rec = {"window_from": day_of(t_from), "window_to": day_of(t_to), "days": round((t_to - t_from) / DAY),
           "return": round(ret, 4), "btc_return": round(b1 / b0 - 1, 4) if b0 and b1 else None, "max_drawdown": round(mdd, 4),
           "sortino": round(sortino, 2) if sortino is not None else None,
           "return_30d": round(r30, 4) if r30 is not None else None, "worst_day_30d": round(worst, 4) if worst is not None else None,
           "flags": ["large_single_step"] if big > 0.5 else [], "steps": len(combined)}
    since = None
    if seen_ms is not None:
        after = [s for s in combined if s[0] >= seen_ms - DAY // 2]
        if after:
            r, d = chain([x for _, _, x in after])
            sb0, sb1 = btc_at(after[0][0]), btc_at(after[-1][1])
            since = {"from": day_of(after[0][0]), "to": day_of(after[-1][1]), "return": round(r, 4), "max_drawdown": round(d, 4),
                     "btc_return": round(sb1 / sb0 - 1, 4) if sb0 and sb1 else None}
    return rec, since


# ---------- files ----------
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
    now_ms, day = int(now.timestamp() * 1000), now.date().isoformat()
    t_start = time.time()
    rows = board_rows(get(BOARD))
    cands = pick(rows)
    top = cands[:TOP_N]
    if not top:
        raise ValueError(f"leaderboard: {len(rows)} rows but none pass the rules")
    os.makedirs(DATA, exist_ok=True)
    with gzip.open(os.path.join(DATA, day + ".json.gz.tmp"), "wt") as f:   # the cut we chose from, for later checks
        json.dump({"day": day, "listed": len(rows), "eligible": len(cands), "rows": cands[:500]}, f)
    os.replace(os.path.join(DATA, day + ".json.gz.tmp"), os.path.join(DATA, day + ".json.gz"))

    watch_path = os.path.join(DATA, "watch.json")
    watch = jload(watch_path, {})
    for i, r in enumerate(top, 1):
        watch.setdefault(r["address"], {"first_seen": day, "first_rank": i, "first_pnl_30d": r["pnl_30d"], "name": r["name"]})
    by_addr = {r["address"]: r for r in rows}
    top_set = {r["address"] for r in top}
    cutoff = (now - dt.timedelta(days=WATCH_DAYS)).date().isoformat()
    off = sorted((a for a, w in watch.items() if a not in top_set and w["first_seen"] >= cutoff), key=lambda a: watch[a]["first_seen"], reverse=True)[:WATCH_MAX]

    candles = post({"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1d", "startTime": now_ms - 800 * DAY, "endTime": now_ms}})
    btc_close = [(day_of(c["t"]), float(c["c"])) for c in candles]

    failed = 0

    def one(addr, rank):
        nonlocal failed
        r, w = by_addr.get(addr) or {}, watch[addr]
        row = {"address": addr, "name": r.get("name") or w.get("name"), "url": f"https://app.hyperliquid.xyz/explorer/address/{addr}", "rank": rank,
               "account_value_usd": round(r["account"]) if r.get("account") is not None else None,
               "pnl_30d_usd": round(r["pnl_30d"]) if r.get("pnl_30d") is not None else None, "roi_30d": r.get("roi_30d"),
               "volume_30d_usd": round(r["vlm_30d"]) if r.get("vlm_30d") is not None else None,
               "pnl_all_usd": round(r["pnl_all"]) if r.get("pnl_all") is not None else None, "roi_all": r.get("roi_all"),
               "on_board": bool(r), "first_seen": w["first_seen"], "first_rank": w["first_rank"],
               "days_watched": (now.date() - dt.date.fromisoformat(w["first_seen"])).days, "record": None, "since_seen": None}
        try:
            pf = post({"type": "portfolio", "user": addr})
            row["record"], row["since_seen"] = record(pf, btc_close, now_ms, ms_of(w["first_seen"]))
        except Exception as e:
            failed += 1
            print(f"{addr}: {e}", file=sys.stderr, flush=True)
        time.sleep(PAUSE)
        return row

    traders = [one(r["address"], i) for i, r in enumerate(top, 1)]
    watched = [one(a, None) for a in off]
    rated = [t for t in traders if t["record"]]
    if not rated:
        raise ValueError(f"no wallet history could be read ({failed} of {len(traders)} requests failed)")
    for t in traders + watched:
        rec = t["record"]
        t["status"] = ("no_history" if not rec else "short_history" if rec["days"] < MIN_HISTORY_DAYS
                       else "high_drawdown" if rec["max_drawdown"] < -DD_GATE else "held")
    full = [t for t in rated if t["status"] in ("held", "high_drawdown")]
    rets = sorted(t["record"]["return"] for t in full)
    out = {"name": "TradingAgentStack: Hyperliquid top traders, checked", "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": day,
           "rules": {"top_n": TOP_N, "ranked_by": "30-day profit in dollars, from Hyperliquid's leaderboard", "min_account_usd": MIN_ACCOUNT,
                     "window_days": WINDOW_DAYS, "min_capital_usd": MIN_CAPITAL, "min_history_days": MIN_HISTORY_DAYS, "drawdown_gate": DD_GATE,
                     "watch_days": WATCH_DAYS},
           "sources": {"leaderboard": BOARD, "api": API, "wallet_history": "info request of type portfolio, one per wallet"},
           "board": {"listed": len(rows), "eligible": len(cands)},
           "summary": {"rated": len(rated), "with_history": len(full), "held": sum(1 for t in full if t["status"] == "held"),
                       "high_drawdown": sum(1 for t in full if t["status"] == "high_drawdown"),
                       "median_return": rets[len(rets) // 2] if rets else None,
                       "watching_since": min((w["first_seen"] for w in watch.values()), default=day), "off_the_list": len(watched)},
           "traders": traders, "watched": watched}
    jsave(os.path.join(PUB, "traders.json"), out, separators=(",", ":"))
    jsave(watch_path, {a: w for a, w in watch.items() if w["first_seen"] >= cutoff or a in top_set}, indent=0)
    return {"at": out["as_of"], "ok": True, "listed": len(rows), "eligible": len(cands), "traders": len(traders), "rated": len(rated),
            "watched": len(watched), "failed_requests": failed, "seconds": round(time.time() - t_start)}


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
