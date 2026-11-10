"""TAS Strategy Lab: forward tests of public open-source strategies on public market data. No keys.

Each strategy is scored only on candles from after the day it was added here (out of sample), with
Binance spot fees, the same 20 pairs, $1,000 paper wallet and 5 open trades for every strategy.
Strategies are checked once with Freqtrade's lookahead analysis; any that peek at future candles are flagged.
A strategy's file is frozen on the day it is added: it is never replaced afterwards, and its SHA-256 is kept in the
registry and published with its result, so a record always belongs to the code that produced it.
Community collections are pinned to one commit each and every file is read (not run) before it is admitted: it may only
import the libraries a strategy needs, and may not open files, run commands or reach the network.
A strategy that times out or is killed three runs in a row is retired, so one heavy file cannot stall the lab.
Our own baselines (hold, moving-average cross, dip buying, breakout and a coin-flip control) live in baselines/.
Raw data stays on the server; /public/strategy-lab/results.json holds the per-strategy results.
"""
import ast, csv, datetime as dt, glob, hashlib, io, json, os, re, shutil, subprocess, time, urllib.request, zipfile

UD = "/freqtrade/user_data"; CFG = "/app/config.json"; PUB = "/public/strategy-lab"
SOURCES = [  # (label, zip url, path prefix inside the zip that holds strategies)
    ("freqtrade/freqtrade-strategies", "https://codeload.github.com/freqtrade/freqtrade-strategies/zip/refs/heads/main", "freqtrade-strategies-main/user_data/strategies/"),
]
SKIP_DIRS = ("futures/", "hyperopts/", "lookahead_bias/")
# Community collections: (owner/repo, commit, path prefixes inside the repo to read, extra imports or calls allowed for this source).
# Each is pinned to one commit, so what runs here is exactly what was read when the line was added, and it is fetched once.
# To take a newer version of a collection, add a new line with the new commit: files already here stay frozen.
PINNED = [
    # NostalgiaForInfinity: the three current generations. It keeps a small cache file next to the strategy and sets its
    # parameters by name, so this one source may use pathlib, rapidjson, getattr and setattr.
    ("iterativv/NostalgiaForInfinity", "75790918caac247449993f37fca94b9eeea07235",
     ("NostalgiaForInfinityX6.py", "NostalgiaForInfinityX7.py", "NostalgiaForInfinityX8.py"), ("pathlib", "rapidjson", "getattr", "setattr", "__getattribute__")),
    ("ssssi/freqtrade_strs", "3ed699f6773bd3db4526eb31bd6d459b95fd2bc2", ("binance/", "ClucHAnix/"), ()),
    ("jilv220/freqtrade-stuff", "786f1e7cdea930dc7bfc4fe926e8a9629514e27d", ("",), ()),
    ("paulcpk/freqtrade-strategies-that-work", "1e154a2f6b9aeecbaacb7db5ed6b866603daee2c", ("",), ()),
    ("brookmiles/freqtrade-stuff", "1f52e1feb037b0f1851ce85c8471e7b3e6969bd4", ("strategies/",), ()),
    ("i1ya/freqtrade-strategies", "4bf648ce0251225ba2237e343ba3f692b761f964", ("",), ()),
    ("hansen1015/freqtrade_strategy", "4b7d342c2a5c6664c00b6a1855e7354a2ffd8225", ("",), ()),
    ("werkkrew/freqtrade-strategies", "582e9649a88623b6f85f03a63bfa68410cd8ad8a", ("strategies/",), ()),
    ("phuchust/freqtrade_strategy", "4a0176ed284379a060262d5943394f2ca056d3d7", ("",), ()),
]
PINNED_SKIP = ("test", "hyperopt", "futures", "lookahead", "/old/", "deprecated")
ALLOW_IMPORTS = {"freqtrade", "pandas", "numpy", "talib", "technical", "pandas_ta", "ta", "scipy", "numba", "datetime", "typing", "functools", "logging",
                 "math", "abc", "warnings", "decimal", "copy", "enum", "collections", "operator", "itertools", "re", "statistics", "dataclasses",
                 "__future__", "time", "random", "json", "hashlib", "cachetools", "dateutil", "arrow", "pytz"}
BAD_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "locals", "vars", "getattr", "setattr", "delattr"}
BAD_ATTRS = {"__subclasses__", "__globals__", "__builtins__", "__import__", "__code__", "__getattribute__", "__reduce__", "__dict__", "system", "popen"}
STRIKES = 3  # runs in a row a strategy may time out or be killed before it is retired
BT_TIMEOUT = int(os.environ.get("LAB_BT_TIMEOUT", "900"))
LOOKAHEAD_TRIES = 2
CODE_REV = os.environ.get("LAB_CODE_REV", "")

def due(last_day, last_rev, now):
    """Run once a day from RUN_HOUR, and at once when new code has been deployed since the last finished run."""
    if last_day != str(now.date()): return now.hour >= RUN_HOUR or not last_day
    return bool(CODE_REV) and last_rev != CODE_REV
TFS = ["1m", "5m", "15m", "1h", "4h", "1d"]  # 1m: several official scalping strategies run on it
RUN_HOUR = int(os.environ.get("LAB_RUN_HOUR", "2"))
BACKTEST_DAYS = int(os.environ.get("LAB_BACKTEST_DAYS", "45"))
LOOKAHEAD_PER_DAY = int(os.environ.get("LAB_LOOKAHEAD_PER_DAY", "6"))
REG = os.path.join(UD, "registry.json")
BASELINES = os.environ.get("LAB_BASELINES", "/app/baselines")

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

def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def install_baselines(reg_files):
    """Copy our own baseline strategies in once. Like every other strategy, a file that is already there is never replaced."""
    os.makedirs(os.path.join(UD, "strategies"), exist_ok=True)
    for f in sorted(glob.glob(os.path.join(BASELINES, "*.py"))):
        dest = os.path.join(UD, "strategies", os.path.basename(f))
        if os.path.exists(dest): continue
        shutil.copyfile(f, dest); reg_files[os.path.basename(f)] = "tas-workers:strategy-lab/baselines/" + os.path.basename(f)

def record_hashes(reg):
    """Keep the SHA-256 of every strategy file as first seen. A later mismatch means the file changed, which must not happen."""
    hashes = reg.setdefault("sha256", {})
    for f in glob.glob(os.path.join(UD, "strategies", "*.py")):
        b = os.path.basename(f); h = sha256(f)
        if b not in hashes: hashes[b] = h
        elif hashes[b] != h: log("WARNING: strategy file changed since it was added:", b)
    return hashes

def admit(src, extra=()):
    """Read one strategy file without running it. Returns (reason it is not admitted or None, the strategy classes it defines)."""
    try: tree = ast.parse(src)
    except (SyntaxError, ValueError): return "does not parse", []
    allow = ALLOW_IMPORTS | set(extra); bad_calls = BAD_CALLS - set(extra); bad_attrs = BAD_ATTRS - set(extra)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] not in allow: return "imports " + a.name.split(".")[0], []
        elif isinstance(n, ast.ImportFrom):
            if n.level: return "imports a file next to it", []
            if (n.module or "").split(".")[0] not in allow: return "imports " + (n.module or "").split(".")[0], []
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in bad_calls: return "calls " + n.func.id, []
        elif isinstance(n, ast.Attribute) and n.attr in bad_attrs: return "uses " + n.attr, []
    strat = {"IStrategy"}; found = []  # strategy classes: IStrategy's children in this file, and their children
    for _ in range(8):
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name not in strat and any(getattr(b, "id", getattr(b, "attr", "")) in strat for b in n.bases):
                strat.add(n.name); found.append(n.name)
    return (None, found) if found else ("no strategy class", [])

def fetch_pinned(reg):
    """Admit each pinned community collection once. A file gets in only if it passes admit() and none of its classes is here already."""
    done = reg.setdefault("pinned_done", []); rejected = reg.setdefault("rejected", {})
    have = set(class_files())
    for repo, sha, folders, extra in PINNED:
        key = repo + "@" + sha
        if key in done: continue
        try:
            req = urllib.request.Request(f"https://codeload.github.com/{repo}/zip/{sha}", headers={"User-Agent": "TradingAgentStack-strategy-lab/1.0"})
            z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=120).read()))
        except Exception as e:
            log("pinned source not fetched, will retry next run:", key, repr(e)[:200]); continue
        prefix = repo.split("/")[1] + "-" + sha + "/"; added = 0
        for n in sorted(z.namelist()):
            if not n.startswith(prefix) or not n.endswith(".py"): continue
            rel = n[len(prefix):]
            if not any(rel.startswith(f) for f in folders) or any(w in rel.lower() for w in PINNED_SKIP): continue
            raw = z.read(n)
            if b"IStrategy" not in raw: continue
            label = key + ":" + rel
            why, classes = admit(raw.decode("utf-8", "replace"), extra)
            if not why and have & set(classes): why = "a strategy with the same name is already in the lab (" + sorted(have & set(classes))[0] + ")"
            if why: rejected[label] = why; continue
            name = re.sub(r"[^A-Za-z0-9_]", "_", repo) + "__" + re.sub(r"[^A-Za-z0-9_.]", "_", os.path.basename(rel))
            dest = os.path.join(UD, "strategies", name)
            if os.path.exists(dest): continue
            open(dest, "wb").write(raw); reg["files"][name] = label; have |= set(classes); added += 1
        done.append(key); log("pinned source", key, "admitted", added)

def strike(meta, why):
    meta["strikes"] = meta.get("strikes", 0) + 1
    if meta["strikes"] >= STRIKES:
        meta["retired"] = f"retired after {STRIKES} runs in a row that could not finish under the lab's limits ({why})"

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
            if os.path.exists(dest): continue  # frozen: a strategy's code never changes after the day it was added (new files still arrive)
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

def backtest(name, start, tf=None):
    code, out = sh(["freqtrade", "backtesting", "-c", CFG, "--userdir", UD, "--strategy", name,
                    "--timerange", start.strftime("%Y%m%d") + "-", "--export", "trades", "--cache", "none"]
                   + (["--timeframe", tf] if tf else []), BT_TIMEOUT)
    if code == 124: return {"status": "timeout", "error": f"did not finish in {BT_TIMEOUT} seconds"}
    if code < 0: return {"status": "killed", "error": "stopped by the system, most likely for using too much memory"}
    if code != 0:
        if not tf and "Timeframe needs to be set" in out:  # example strategies that leave the timeframe to the user
            return dict(backtest(name, start, "5m"), timeframe_note="not set by the strategy; run on 5m")
        if "can_short" in out:  # the lab trades spot, so futures-only strategies are listed but not run
            return {"status": "not_spot", "error": "futures-only strategy (it needs shorting); the lab runs spot"}
        return {"status": "error", "error": out[-600:]}
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
                    "--lookahead-analysis-exportfilename", out_csv], BT_TIMEOUT)
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
    if reg.get("running") in reg["strategies"]:  # the lab stopped part-way through this strategy last time: count it against the strategy
        strike(reg["strategies"][reg["running"]], "the lab stopped while it was running")
    reg["running"] = None
    install_baselines(reg["files"])
    fetch_sources(reg["files"])
    fetch_pinned(reg)
    hashes = record_hashes(reg)
    names, list_err = list_strategies()
    if not names: raise RuntimeError("no strategies loaded: " + (list_err or "")[-400:])
    cf = class_files()
    for n in names:
        if n not in reg["strategies"]:
            f = cf.get(n, ""); reg["strategies"][n] = {"added": str(today), "file": f, "source": reg["files"].get(f, ""), "lookahead_bias": None}
    save_json(REG, reg)
    code, out = sh(["freqtrade", "download-data", "-c", CFG, "--userdir", UD, "--timeframes", *TFS, "--days", "60"], 3600)
    dl_err = None if code == 0 else out[-600:]
    deep = os.path.join(UD, ".deep_history_v1")
    if not os.path.exists(deep):  # once: long history on the slow timeframes, so strategies with long indicators get real values
        code, out = sh(["freqtrade", "download-data", "-c", CFG, "--userdir", UD, "--timeframes", "1h", "4h", "1d", "--days", "900", "--prepend"], 3600)
        if code == 0: open(deep, "w").write(str(today))
        else: log("deep history not downloaded, will retry:", out[-300:])
    checked = 0
    for n, meta in sorted(reg["strategies"].items()):  # a check that cannot finish is tried twice, then left, so it never holds up the others
        if (meta.get("lookahead_bias") is None and meta.get("lookahead_tries", 0) < LOOKAHEAD_TRIES and checked < LOOKAHEAD_PER_DAY
                and n in names and not meta.get("retired")):
            reg["running"] = n; save_json(REG, reg)
            meta["lookahead_tries"] = meta.get("lookahead_tries", 0) + 1
            meta["lookahead_bias"] = lookahead(n, today - dt.timedelta(days=20)); meta["lookahead_checked"] = str(today); checked += 1
            reg["running"] = None; save_json(REG, reg)
            log("lookahead", n, meta["lookahead_bias"])
    results = []
    for n, meta in sorted(reg["strategies"].items()):
        added = dt.date.fromisoformat(meta["added"]); days = (today - added).days
        r = {"strategy": n, "source": meta.get("source"), "added": meta["added"], "days_live": days, "lookahead_bias": meta.get("lookahead_bias"),
             "code_sha256": hashes.get(meta.get("file") or "")}
        if n not in names: r.update(status="not_loadable")
        elif meta.get("retired"): r.update(status="retired", error=meta["retired"])
        else:
            reg["running"] = n; save_json(REG, reg); t1 = time.time()
            # Labelled backtest on the last 45 days (published at once, never called a track record),
            # plus the live, out-of-sample record that grows every day from the day the strategy was added.
            bt = backtest(n, today - dt.timedelta(days=BACKTEST_DAYS))
            r["backtest"] = dict(bt, days=BACKTEST_DAYS, label="backtest")
            r["status"] = bt.get("status")
            if days >= 1 and bt.get("status") == "ok":
                r["live"] = dict(backtest(n, added), label="live since " + meta["added"])
            bad = [x.get("status") for x in (bt, r.get("live") or {}) if x.get("status") in ("timeout", "killed")]
            if bad: strike(meta, bad[0])
            else: meta["strikes"] = 0
            r["seconds"] = round(time.time() - t1); reg["running"] = None
        results.append(r); log("result", n, r.get("status"), r.get("profit_total"))
    save_json(REG, reg)
    summary = {"at": dt.datetime.utcnow().isoformat() + "Z", "strategies": len(results),
               "ok": sum(r.get("status") == "ok" for r in results), "live_days": max([r.get("days_live", 0) for r in results] or [0]),
               "errors": sum(r.get("status") == "error" for r in results), "retired": sum(r.get("status") == "retired" for r in results),
               "timeouts": sum(r.get("status") in ("timeout", "killed") for r in results),
               "pinned_sources": len(reg.get("pinned_done", [])), "not_admitted": len(reg.get("rejected", {})),
               "lookahead_flagged": sum(r.get("lookahead_bias") is True for r in results),
               "lookahead_checked": sum(r.get("lookahead_bias") is not None for r in results),
               "seconds": round(time.time() - t0), "list_error": list_err, "download_error": dl_err,
               "method": "Two numbers per strategy, never mixed: a labelled backtest on the last 45 days, and the live out-of-sample record from the day it was added here. Binance spot candles, 20 pairs, $1,000 paper wallet, 5 open trades, exchange fees included. Spot only: no leverage, no shorting. Each strategy's code is frozen on the day it is added (its SHA-256 is published with its result). Community collections are pinned to one commit, and a file is admitted only if a read of its code shows it imports nothing but the libraries a strategy needs. A strategy that cannot finish three runs in a row is retired. Strategies flagged by Freqtrade's lookahead analysis are marked."}
    save_json(os.path.join(PUB, "results.json"), {"as_of": str(today), "summary": summary, "results": results})
    save_json(os.path.join(PUB, "not-admitted.json"), {"as_of": str(today), "about": "Files from the pinned community collections that were read and not admitted, with the reason.", "files": reg.get("rejected", {})})
    save_json(os.path.join(PUB, "status.json"), summary)
    open(os.path.join(UD, ".last_run_v4"), "w").write(str(today))
    open(os.path.join(UD, ".last_run_rev"), "w").write(CODE_REV)
    log("done", json.dumps(summary)[:400])

if __name__ == "__main__":
    os.makedirs(PUB, exist_ok=True)
    sh(["freqtrade", "create-userdir", "--userdir", UD], 120)
    while True:
        last = open(os.path.join(UD, ".last_run_v4")).read().strip() if os.path.exists(os.path.join(UD, ".last_run_v4")) else ""
        rev = open(os.path.join(UD, ".last_run_rev")).read().strip() if os.path.exists(os.path.join(UD, ".last_run_rev")) else ""
        if due(last, rev, dt.datetime.utcnow()):
            try: run_once()
            except Exception as e:
                log("run failed:", repr(e))
                save_json(os.path.join(PUB, "status.json"), {"at": dt.datetime.utcnow().isoformat() + "Z", "run_failed": repr(e)[:600]})
                time.sleep(3600)
        time.sleep(600)
