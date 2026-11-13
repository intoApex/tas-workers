#!/usr/bin/env python3
"""Bybit copy trading: the lead traders Bybit recommends, kept as a daily record (no key, read from this server only).

Bybit's own AI connector (github.com/bybit-exchange/trading-mcp, getCopyTradingClassicLeaderboard) reads
GET /v5/copy-trade/recommend-leader-list without signing it. From US servers Bybit answers HTTP 403, so this runs here.
The list is short (15 traders on 2026-11-13) and it is Bybit's recommendation, not a full leaderboard. What it gives for
each trader is 30 days: ROI, max drawdown, Sharpe, win rate, followers, and a 30-point ROI curve (yieldTrend).

Once a day:
  1. Read the list.
  2. Work out what the 30 curve points are. The route has no public description of them, so nothing is assumed: the points
     are tried as a running ROI and as daily yields, at the three scales Bybit uses (fraction, percent, E4), and a reading
     is accepted only when it lands on Bybit's own 30-day ROI for most of the list. If none does, no figure of ours is
     published and Bybit's own figures are shown alone.
  3. With an accepted reading: the worst drop over the 30 daily points (to hold against Bybit's own max drawdown), and
     each day's return, which is saved so a trader's record can grow past Bybit's 30 days.
  4. Remember the day and place each trader was first recommended (watch.json). The daily returns we have saved, from
     that day on, give the record since we first saw them. A trader Bybit stops recommending gets no new days.
  5. Publish /srv/tas/public/bybit/traders.json and /srv/tas/public/bybit/status.json.

A failed run leaves yesterday's traders.json in place and says why in status.json. status.json also carries the first
row's number formats (no names), so a change in Bybit's formats shows up there first.
Nicknames and leader marks are Bybit's public list data. Standard library only.
Field names checked from this server on 2026-11-13 (probes/bybit_copytrading.sh); values were not seen when this was written.
"""
import datetime as dt, json, os, re, sys, time, urllib.request

API = "https://api.bybit.com"
LIST = "/v5/copy-trade/recommend-leader-list"
DATA = os.environ.get("BYBIT_TRADERS_DATA", "/srv/tas/data/bybit-traders")
PUB = os.environ.get("BYBIT_TRADERS_PUBLIC", "/srv/tas/public/bybit")
WATCH_DAYS = int(os.environ.get("BYBIT_TRADERS_WATCH_DAYS", "90"))
DD_GATE = 0.30
ACCEPT = 0.6           # a reading of the curve is accepted when it lands on Bybit's ROI for this share of the list
UA = {"User-Agent": "TradingAgentStack-workers/1.0 (+https://tradingagentstack.com/about/; public data, once a day)", "Accept": "application/json"}


def get(path, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {path}: {e}", file=sys.stderr, flush=True)
            time.sleep(5 * (i + 1))


def num(x):
    """A number from Bybit's number, numeric string or percent string; None for anything else."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x) if x == x else None
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*(%?)\s*", str(x))
    return float(m.group(1)) / (100 if m.group(2) else 1) if m else None


def whole(x):
    v = num(x)
    return int(v) if v is not None else None


def e4(row, key):
    """A fraction from an ...E4 field (1234 means 12.34%), falling back to the plain field ("12.34%" or 0.1234)."""
    v = num(row.get(key + "E4"))
    if v is not None:
        return v / 10000
    return num(row.get(key))


def day(x):
    """Bybit's statisticDate as YYYY-MM-DD: epoch seconds or milliseconds, YYYYMMDD or an ISO date."""
    s = str(x).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
        return s[:10]
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{10}(\d{3})?", s):
        return dt.datetime.fromtimestamp(int(s[:10]), dt.timezone.utc).date().isoformat()
    return None


def points(row):
    """[(day, raw value)] oldest first, or [] when the curve can't be read."""
    out = []
    for p in row.get("yieldTrend") or []:
        d, v = day(p.get("statisticDate")), num(p.get("trendValue"))
        if d is None or v is None:
            return []
        out.append((d, v))
    return sorted(out)


def near(a, b):
    return abs(a - b) <= max(0.005, 0.05 * abs(b))


READINGS = [(k, s) for k in ("running", "daily") for s in (1, 100, 10000)]


def curve(pts, kind, scale):
    """The running ROI (fractions) under one reading of the points, or None when the reading can't hold (a day at -100% or worse)."""
    vals = [v / scale for _, v in pts]
    if kind == "running":
        return vals if all(v > -1 for v in vals) else None
    g, out = 1.0, []
    for v in vals:
        if v <= -1:
            return None
        g *= 1 + v
        out.append(g - 1)
    return out


def pick_reading(rows):
    """The reading that lands on Bybit's own 30-day ROI for the most traders, and how many: ((kind, scale) | None, matched, of).
    Decided for the whole list at once, so one trader with a flat curve can't pick the wrong scale for himself."""
    usable = [(points(r), e4(r, "thirtyDayRoi")) for r in rows]
    usable = [(p, roi) for p, roi in usable if p and roi is not None]
    best, hits = None, 0
    for kind, scale in READINGS:
        n = 0
        for p, roi in usable:
            c = curve(p, kind, scale)
            n += bool(c) and near(c[-1], roi)
        if n > hits:
            best, hits = (kind, scale), n
    if not usable or hits < ACCEPT * len(usable):
        return None, hits, len(usable)
    return best, hits, len(usable)


def chain(rs):
    g, peak, mdd = 1.0, 1.0, 0.0
    for r in rs:
        g *= 1 + r
        peak = max(peak, g)
        mdd = min(mdd, g / peak - 1)
    return g - 1, mdd


def daily_returns(pts, c):
    """{day: that day's return} from a running ROI curve. The first point's own return needs the day before it, which
    we don't have, so it starts at the second point."""
    return {pts[i][0]: (1 + c[i]) / (1 + c[i - 1]) - 1 for i in range(1, len(c))}


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


def formats(row):
    """The first row's number formats for status.json: every field but the ones that identify the trader."""
    keep = {k: v for k, v in row.items() if k not in ("nickname", "leaderMark", "avatar", "yieldTrend")}
    yt = row.get("yieldTrend") or []
    return {"fields": keep, "yieldTrend_first": yt[:2], "yieldTrend_last": yt[-2:], "yieldTrend_n": len(yt)}


def run(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    d = get(LIST)
    if not isinstance(d, dict) or d.get("retCode") not in (0, "0"):
        raise ValueError(f"list: retCode {d.get('retCode') if isinstance(d, dict) else type(d).__name__}, {str(d.get('retMsg') if isinstance(d, dict) else d)[:120]}")
    res = d.get("result") or {}
    rows = [r for r in (res.get("list") or []) if isinstance(r, dict) and r.get("leaderMark")]
    if not rows:
        raise ValueError("list: no rows with a leaderMark; result keys " + str(sorted(res.keys()))[:200])
    reading, matched, of = pick_reading(rows)

    watch_path, hist_path = os.path.join(DATA, "watch.json"), os.path.join(DATA, "returns.json")
    watch, hist = jload(watch_path, {}), jload(hist_path, {})
    cutoff = (now - dt.timedelta(days=WATCH_DAYS)).date().isoformat()
    traders = []
    for i, r in enumerate(rows, 1):
        mark = str(r["leaderMark"])
        w = watch.setdefault(mark, {"first_seen": today, "first_rank": i, "name": r.get("nickname")})
        w["name"] = r.get("nickname") or w.get("name")
        roi, dd = e4(r, "thirtyDayRoi"), e4(r, "thirtyDayMaxDrawdown")
        own, pts = None, points(r)
        if reading and pts and roi is not None:
            c = curve(pts, *reading)
            if c and near(c[-1], roi):
                rets = daily_returns(pts, c)
                own = {"from": pts[0][0], "to": pts[-1][0], "points": len(pts), "roi_at_end": round(c[-1], 4),
                       "worst_drop_30d": round(chain([c[0]] + [rets[p[0]] for p in pts[1:]])[1], 4)}
                hist.setdefault(mark, {}).update({k: round(v, 6) for k, v in rets.items()})
        mine = sorted((k, v) for k, v in (hist.get(mark) or {}).items() if k >= w["first_seen"])
        since = None
        if mine:
            ret, mdd = chain([v for _, v in mine])
            since = {"from": w["first_seen"], "to": mine[-1][0], "days": len(mine), "return": round(ret, 4), "max_drawdown": round(mdd, 4)}
        gate = own["worst_drop_30d"] if own else (-abs(dd) if dd is not None else None)
        traders.append({
            "mark": mark, "name": r.get("nickname"), "rank": i, "level": r.get("leaderLevel"),
            "followers": whole(r.get("currentFollowerCount")), "max_followers": whole(r.get("maxFollowerCount")),
            "min_investment_usd": (num(r.get("minInvestmentE8")) or 0) / 1e8 or None,
            "bybit": {"roi_30d": roi, "max_drawdown_30d": -abs(dd) if dd is not None else None, "sharpe_30d": e4(r, "thirtyDaySharpeRatio"),
                      "win_rate_30d": e4(r, "thirtyDayWinRate"), "volatility_30d": e4(r, "thirtyDayReturnVolatility"),
                      "trades_per_week": (num(r.get("thirtyDayWeekTradeCountE2")) or 0) / 100 if r.get("thirtyDayWeekTradeCountE2") is not None else None},
            "own": own, "on_list": True, "first_seen": w["first_seen"], "first_rank": w["first_rank"],
            "days_watched": (now.date() - dt.date.fromisoformat(w["first_seen"])).days, "since_seen": since,
            "status": "no_figures" if gate is None else "high_drawdown" if gate < -DD_GATE else "held"})
    on = {t["mark"] for t in traders}
    watched = []
    for mark, w in sorted(watch.items(), key=lambda kv: kv[1]["first_seen"], reverse=True):
        if mark in on or w["first_seen"] < cutoff:
            continue
        mine = sorted((k, v) for k, v in (hist.get(mark) or {}).items() if k >= w["first_seen"])
        ret, mdd = chain([v for _, v in mine]) if mine else (None, None)
        watched.append({"mark": mark, "name": w.get("name"), "rank": None, "on_list": False, "first_seen": w["first_seen"], "first_rank": w["first_rank"],
                        "days_watched": (now.date() - dt.date.fromisoformat(w["first_seen"])).days,
                        "since_seen": {"from": w["first_seen"], "to": mine[-1][0], "days": len(mine), "return": round(ret, 4), "max_drawdown": round(mdd, 4)} if mine else None})
    gaps = [t for t in traders if t["own"] and t["bybit"]["max_drawdown_30d"] is not None]
    out = {"name": "TradingAgentStack: Bybit's recommended copy traders, checked", "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": today,
           "rules": {"what": "the lead traders Bybit recommends for Classic copy trading. A short list chosen by Bybit, not a full leaderboard.",
                     "window_days": 30, "drawdown_gate": DD_GATE, "watch_days": WATCH_DAYS,
                     "own_figures": "worked out from Bybit's 30-point ROI curve, and only when our reading of that curve lands on Bybit's own 30-day ROI",
                     "after_first_seen": "a trader's record since we first saw them is chained from the daily returns we saved; a trader Bybit stops recommending has no new days"},
           "sources": {"list": API + LIST, "site": "https://www.bybit.com/copyTrade/"},
           "list": {"title": res.get("title"), "n": len(rows)},
           "curve": {"reading": {"kind": reading[0], "scale": reading[1]} if reading else None, "matched": matched, "of": of},
           "summary": {"traders": len(traders), "with_own_figures": sum(1 for t in traders if t["own"]),
                       "held": sum(1 for t in traders if t["status"] == "held"), "high_drawdown": sum(1 for t in traders if t["status"] == "high_drawdown"),
                       "own_drop_deeper_than_bybit": sum(1 for t in gaps if t["own"]["worst_drop_30d"] < t["bybit"]["max_drawdown_30d"] - 0.01),
                       "watching_since": min((w["first_seen"] for w in watch.values()), default=today), "off_the_list": len(watched)},
           "traders": traders, "watched": watched}
    jsave(os.path.join(PUB, "traders.json"), out, separators=(",", ":"))
    jsave(watch_path, {m: w for m, w in watch.items() if w["first_seen"] >= cutoff or m in on}, indent=0)
    keep_from = (now - dt.timedelta(days=WATCH_DAYS + 40)).date().isoformat()
    jsave(hist_path, {m: {k: v for k, v in h.items() if k >= keep_from} for m, h in hist.items() if m in watch}, separators=(",", ":"))
    return {"at": out["as_of"], "ok": True, "traders": len(traders), "with_own_figures": out["summary"]["with_own_figures"],
            "curve": out["curve"], "watched": len(watched), "formats": formats(rows[0])}


def main():
    try:
        status = run()
    except Exception as e:
        status = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    jsave(os.path.join(PUB, "status.json"), status)
    print(json.dumps(status)[:1500], flush=True)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
