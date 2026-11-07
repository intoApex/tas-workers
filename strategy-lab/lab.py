"""TAS Strategy Lab: forward tests of public open-source strategies on public market data. No keys.

Each strategy is scored only on candles from after the day it was added here (out of sample), with
Binance spot fees, the same 20 pairs, $1,000 paper wallet and 5 open trades for every strategy.
Strategies are checked once with Freqtrade's lookahead analysis; any that peek at future candles are flagged.
Raw data stays on the server; /public/strategy-lab/results.json holds the per-strategy results.
"""
import csv, datetime as dt, glob, io, json, os, re, shutil, subprocess, time, urllib.request, zipfile

UD = "/freqtrade/user_data"; CFG = "/app/config.json"; PUB = "/public/strategy-lab"
SOURCES = [  # (label, zip url, path prefix inside the zip that holds strategies)
    ("freqtrade/freqtrade-strategies", "https://codeload.github.com/freqtrade/freqtrade-strategies/zip/refs/heads/main", "freqtrade-strategies-main/user_data/strategies/"),
]
SKIP_DIRS = ("futures/", "hyperopts/", "lookahead_bias/")
TFS = ["5m", "15m", "1h", "4h", "1d"]
RUN_HOUR = int(os.environ.get("LAB_RUN_HOUR", "2"))
LOOKAHEAD_PER_DAY = int(os.environ.get("LAB_LOOKAHEAD_PER_DAY", "6"))
REG = os.path.join(UD, "registry.json")

def log(*a): print(dt.datetime.utcnow().strftime("%H:%M:%S"), *a, flush=True)

def sh(args, timeout=1800):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def load_reg():
    try: return json.load(open(REG))
    except Exception: return {}

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1); os.replace(tmp, path)

def fetch_sources(reg_files):
    os.makedirs(os.path.join(UD, "strategies"), exist_ok=True)
    stamp = os.path.join(UD, ".sources_at")
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 7 * 86400: return
    for label, url, prefix in SOURCES:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingAgentStack-strategy-lab/1.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist():
            if not n.startswith(prefix) or not n.endswith(".py"): continue
            rel = n[len(prefix):]
            if any(rel.startswith(s) for s in SKIP_DIRS) or "/" in rel and rel.split("/")[0].startswith("__"): continue
            dest = os.path.join(UD, "strategies", os.path.basename(rel))
            if os.path.exists(dest) and reg_files.get(os.path.basename(rel)) not in (None, label + ":" + rel): continue
            open(dest, "wb").write(z.read(n)); reg_files[os.path.basename(rel)] = label + ":" + rel
    open(stamp, "w").write(dt.datetime.utcnow().isoformat())

def list_strategies():
    code, out = sh(["freqtrade", "list-strategies", "-c", CFG, "--userdir", UD, "-1"], 300)
    names = [l.strip() for l in out.splitlines() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", l.strip())]
    return names, (None if code == 0 else out[-800:])

def class_files():
    m = {}
    for f in glob.glob(os.path.join(UD, "strategies", "*.py")):
        for c in re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", open(f, errors="ignore").read(), re.M): m[c] = os.path.basename(f)
    return m

def parse_last_backtest():
    last = json.load(open(os.path.join(UD, "backtest_results", ".last_result.json")))["latest_backtest"]
    path = os.path.join(UD, "backtest_results", last)
    if path.endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = [n for n in z.namelist() if n.endswith(".json") and "_config" not in n and "market_change" not in n][0]
        return json.loads(z.read(name))
    return json.load(open(path))

def backtest(name, start):
    code, out = sh(["freqtrade", "backtesting", "-c", CFG, "--userdir", UD, "--strategy", name,
                    "--timerange", start.strftime("%Y%m%d") + "-", "--export", "trades", "--cache", "none"], 1200)
    if code != 0: return {"status": "error", "error": out[-600:]}
    s = parse_last_backtest()["strategy"].get(name, {})
    trades = s.get("trades", [])
    return {"status": "ok", "timeframe": s.get("timeframe"), "trades": s.get("total_trades", len(trades)),
            "profit_total": s.get("profit_total"), "profit_abs": s.get("profit_total_abs"),
            "max_drawdown": s.get("max_drawdown_account", s.get("max_relative_drawdown")),
            "winrate": s.get("winrate"), "wins": s.get("wins"), "losses": s.get("losses"),
            "market_change": s.get("market_change"), "start": s.get("backtest_start"), "end": s.get("backtest_end")}

def lookahead(name, start):
    out_csv = "/tmp/lookahead.csv"
    if os.path.exists(out_csv): os.remove(out_csv)
    code, out = sh(["freqtrade", "lookahead-analysis", "-c", CFG, "--userdir", UD, "--strategy", name,
                    "--timerange", start.strftime("%Y%m%d") + "-", "-p", "BTC/USDT", "ETH/USDT", "SOL/USDT",
                    "--lookahead-analysis-exportfilename", out_csv], 1800)
    if os.path.exists(out_csv):
        for row in csv.DictReader(open(out_csv)):
            if row.get("strategy") == name or len(row) and name in json.dumps(row):
                v = (row.get("has_bias") or "").strip().lower()
                if v in ("true", "false"): return v == "true"
    m = re.search(name + r".*?\|\s*(True|False)\s*\|", out)
    return (m.group(1) == "True") if m else None

def run_once():
    t0 = time.time(); today = dt.datetime.utcnow().date()
    reg = load_reg(); reg.setdefault("files", {}); reg.setdefault("strategies", {})
    fetch_sources(reg["files"])
    names, list_err = list_strategies()
    cf = class_files()
    for n in names:
        if n not in reg["strategies"]:
            f = cf.get(n, ""); reg["strategies"][n] = {"added": str(today), "file": f, "source": reg["files"].get(f, ""), "lookahead_bias": None}
    save_json(REG, reg)
    code, out = sh(["freqtrade", "download-data", "-c", CFG, "--userdir", UD, "--timeframes", *TFS, "--days", "60"], 3600)
    dl_err = None if code == 0 else out[-600:]
    checked = 0
    for n, meta in sorted(reg["strategies"].items()):
        if meta.get("lookahead_bias") is None and checked < LOOKAHEAD_PER_DAY:
            meta["lookahead_bias"] = lookahead(n, today - dt.timedelta(days=20)); meta["lookahead_checked"] = str(today); checked += 1
            log("lookahead", n, meta["lookahead_bias"])
    results = []
    for n, meta in sorted(reg["strategies"].items()):
        added = dt.date.fromisoformat(meta["added"]); days = (today - added).days
        r = {"strategy": n, "source": meta.get("source"), "added": meta["added"], "days_live": days, "lookahead_bias": meta.get("lookahead_bias")}
        if n not in names: r.update(status="not_loadable")
        elif days < 1: r.update(status="waiting")
        else: r.update(backtest(n, added))
        results.append(r); log("result", n, r.get("status"), r.get("profit_total"))
    save_json(REG, reg)
    summary = {"at": dt.datetime.utcnow().isoformat() + "Z", "strategies": len(results),
               "ok": sum(r.get("status") == "ok" for r in results), "waiting": sum(r.get("status") == "waiting" for r in results),
               "errors": sum(r.get("status") == "error" for r in results), "lookahead_flagged": sum(r.get("lookahead_bias") is True for r in results),
               "lookahead_checked": sum(r.get("lookahead_bias") is not None for r in results),
               "seconds": round(time.time() - t0), "list_error": list_err, "download_error": dl_err,
               "method": "Out of sample only: each strategy is scored on Binance spot candles after the day it was added, 20 pairs, $1,000 paper wallet, 5 open trades, exchange fees included."}
    save_json(os.path.join(PUB, "results.json"), {"summary": summary, "results": results})
    save_json(os.path.join(PUB, "status.json"), summary)
    open(os.path.join(UD, ".last_run"), "w").write(str(today))
    log("done", json.dumps(summary)[:400])

if __name__ == "__main__":
    os.makedirs(PUB, exist_ok=True)
    sh(["freqtrade", "create-userdir", "--userdir", UD], 120)
    while True:
        last = open(os.path.join(UD, ".last_run")).read().strip() if os.path.exists(os.path.join(UD, ".last_run")) else ""
        if last != str(dt.datetime.utcnow().date()) and (dt.datetime.utcnow().hour >= RUN_HOUR or not last):
            try: run_once()
            except Exception as e:
                log("run failed:", repr(e))
                save_json(os.path.join(PUB, "status.json"), {"at": dt.datetime.utcnow().isoformat() + "Z", "run_failed": repr(e)[:600]})
                time.sleep(3600)
        time.sleep(600)
