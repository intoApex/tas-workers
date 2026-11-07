"""Turn the latest OKX copy-trading snapshot into public per-trader records (runs on the host, stdlib only).

Input: /srv/tas/data/okx/<latest day>/{ranks,detail}.json.gz (written by snapshot.py).
Output: /srv/tas/public/okx/lead-traders.json  (OKX nickname and profile link are public, opt-in lead-trader data).
Everything is computed from OKX's official public copy-trading endpoints; nothing is self-reported.
"""
import gzip, json, os, statistics, datetime as dt

DATA, OUT = "/srv/tas/data/okx", "/srv/tas/public/okx"

def f(x):
    try: return float(x)
    except Exception: return None

def series(rows):
    out = []
    for r in rows if isinstance(rows, list) else []:
        ts, p, q = f(r.get("beginTs")), f(r.get("pnl")), f(r.get("pnlRatio"))
        if ts is not None and p is not None and q is not None: out.append((ts, p, q))
    return sorted(out)

def curve(rs, cumulative):
    """Equity multiple per day from OKX's daily ratios."""
    eq = []
    if cumulative:
        eq = [1 + q for _, _, q in rs]
    else:
        e = 1.0
        for _, _, q in rs: e *= (1 + q); eq.append(e)
    return eq

def max_dd(eq):
    peak, dd = None, 0.0
    for e in eq:
        peak = e if peak is None or e > peak else peak
        if peak and peak > 0: dd = min(dd, e / peak - 1)
    return dd

def window_return(rs, eq, days):
    if not rs: return None
    cutoff = rs[-1][0] - days * 86400000
    idx = [i for i, (ts, _, _) in enumerate(rs) if ts >= cutoff]
    if not idx or idx[0] == 0 and (rs[-1][0] - rs[0][0]) < (days - 1) * 86400000: return None
    base = eq[idx[0] - 1] if idx[0] > 0 else 1.0
    return eq[-1] / base - 1 if base > 0 else None

def main():
    days = sorted(d for d in os.listdir(DATA) if d[:2] == "20" and os.path.exists(os.path.join(DATA, d, "detail.json.gz")))
    if not days: return
    day = days[-1]
    ranks = json.load(gzip.open(os.path.join(DATA, day, "ranks.json.gz"), "rt"))
    detail = json.load(gzip.open(os.path.join(DATA, day, "detail.json.gz"), "rt"))["traders"]
    out, diag = [], {"cumulative_votes": 0, "incremental_votes": 0}
    for r in ranks["ranks"]:
        code = r.get("uniqueCode"); d = detail.get(code)
        if not d: continue
        rs = series(d.get("daily_pnl"))
        if len(rs) < 2: continue
        total = f(r.get("pnl")) or 0.0
        inc_err = abs(sum(p for _, p, _ in rs) - total); cum_err = abs(rs[-1][1] - total)
        cumulative = cum_err < inc_err
        diag["cumulative_votes" if cumulative else "incremental_votes"] += 1
        eq = curve(rs, cumulative)
        pos = d.get("positions_closed") if isinstance(d.get("positions_closed"), list) else []
        lev = [f(p.get("lever")) for p in pos if f(p.get("lever"))]
        st = d.get("stats")[0] if isinstance(d.get("stats"), list) and d.get("stats") else {}
        hist_days = round((rs[-1][0] - rs[0][0]) / 86400000) + 1
        rec = {
            "nickname": r.get("nickName"), "profile": r.get("portLink"), "code": code,
            "lead_days": int(f(r.get("leadDays")) or 0), "history_days": hist_days,
            "okx_pnl_usdt": total, "okx_pnl_ratio": f(r.get("pnlRatio")),
            "aum_usdt": f(r.get("aum")), "copiers": int(f(r.get("copyTraderNum")) or 0),
            "copiers_all_time": int(f(r.get("accCopyTraderNum")) or 0),
            "copier_pnl_usdt": f(st.get("curCopyTraderPnl")),
            "win_days": int(f(st.get("profitDays")) or 0), "loss_days": int(f(st.get("lossDays")) or 0),
            "return_30d": window_return(rs, eq, 30), "return_90d": window_return(rs, eq, 90),
            "return_history": eq[-1] / (1.0 if cumulative else 1.0) - 1 if eq else None,
            "max_drawdown": max_dd(eq),
            "leverage_median": statistics.median(lev) if lev else None, "leverage_max": max(lev) if lev else None,
            "closed_positions_sampled": len(pos),
            "instruments": r.get("traderInsts", [])[:8] if isinstance(r.get("traderInsts"), list) else [],
        }
        rec["passes_gates"] = bool(rec["history_days"] >= 30 and rec["max_drawdown"] is not None and rec["max_drawdown"] >= -0.30)
        out.append(rec)
    os.makedirs(OUT, exist_ok=True)
    doc = {"as_of": day, "generated": dt.datetime.utcnow().isoformat() + "Z", "source": "OKX official public copy-trading API (www.okx.com/api/v5/copytrading/public-*)",
           "listed_by_okx": len(ranks["ranks"]), "with_records": len(out), "series_reading": diag,
           "method": "Daily results from OKX's public data. Return and worst drop are computed from the daily series; gates: at least 30 days of history and a worst drop no deeper than 30%. Lead traders' results are before copiers' profit share.",
           "traders": out}
    tmp = os.path.join(OUT, "lead-traders.json.tmp"); json.dump(doc, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(OUT, "lead-traders.json"))
    # small sample of raw series for checking the reading of OKX's daily fields
    smp = {}
    for r in ranks["ranks"][:2]:
        d = detail.get(r.get("uniqueCode"), {})
        smp[r.get("nickName") or "?"] = {"okx_total_pnl": r.get("pnl"), "okx_pnl_ratio": r.get("pnlRatio"), "lead_days": r.get("leadDays"),
                                         "daily_first5": (d.get("daily_pnl") or [])[:5] if isinstance(d.get("daily_pnl"), list) else d.get("daily_pnl"),
                                         "daily_last5": (d.get("daily_pnl") or [])[-5:] if isinstance(d.get("daily_pnl"), list) else None,
                                         "weekly_first3": (d.get("weekly_pnl") or [])[:3] if isinstance(d.get("weekly_pnl"), list) else None}
    json.dump(smp, open(os.path.join(OUT, "sample.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
