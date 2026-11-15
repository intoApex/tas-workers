"""TAS runner, part 1: the signal hub. Paper money, no keys.

The Strategy Lab replays each bot once a day. The runner needs each offered bot's trades as they happen, so the hub runs
a live dry-run copy of every strategy in runner/strategies.json, each in its own Freqtrade process, on the lab's own
frozen file (checked against the SHA-256 the lab recorded when it first saw the file) and the lab's own config. Every
fill is posted by Freqtrade's webhook to a small collector in this same process, kept on disk, and published read-only at
https://workers.tradingagentstack.com/runner/signals.json (status in runner/status.json).

Part 2, the executor that copies these signals into users' own exchange accounts, is NOT here and never runs on this
server: it needs keys. runner/DESIGN.md says how it will work and the rules it must follow.

Run: python3 hub.py (inside the freqtrade image; compose.yaml service runner-signals, profile "runner").
Tests, no Freqtrade and no network: python3 tests/test_runner_hub.py
"""
import datetime as dt, hashlib, http.server, json, os, re, shutil, subprocess, threading, time

APP = os.path.dirname(os.path.abspath(__file__))
LAB = os.environ.get("RUNNER_LAB_DIR", "/lab")                   # the lab's user_data, read-only: strategies/*.py, registry.json
LAB_PUB = os.environ.get("RUNNER_LAB_PUBLIC", "/labpub")         # the lab's published results.json, read-only
LAB_CFG = os.environ.get("RUNNER_LAB_CONFIG", "/labcfg")         # tas-workers strategy-lab/: config.json and market-orders.json
UD = os.environ.get("RUNNER_DATA_DIR", "/freqtrade/user_data")   # the hub's own folder: one sub-folder per strategy
PUB = os.environ.get("RUNNER_PUBLIC_DIR", "/public/runner")
PORT = int(os.environ.get("RUNNER_PORT", "8090"))
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")              # a Python class name; nothing else reaches a path or a URL
RECENT = 50                                                       # events per strategy in the public file
KEYS = ("pair", "trade_id", "direction", "open_rate", "close_rate", "amount", "stake_amount", "open_date", "close_date",
        "profit_ratio", "exit_reason", "enter_tag", "order_type")


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def log(*a):
    print(dt.datetime.utcnow().strftime("%H:%M:%S"), *a, flush=True)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


# ---------- which strategies, and how each one runs ----------

def offered():
    """Names from strategies.json, valid class names only, in file order, no repeats."""
    cfg = load_json(os.path.join(APP, "strategies.json"), {}) or {}
    out = []
    for s in cfg.get("strategies", []):
        n = s.get("name") if isinstance(s, dict) else s
        if isinstance(n, str) and NAME.match(n) and n not in out:
            out.append(n)
    return out


def plan(name, registry, results):
    """How to run one strategy, or (None, why) if it can't be run as the lab ran it. Pure: reads only what it is given."""
    meta = (registry.get("strategies") or {}).get(name)
    if not meta or not meta.get("file"):
        return None, "not in the lab's registry"
    fname = meta["file"]
    if os.path.basename(fname) != fname or not fname.endswith(".py"):
        return None, "unexpected file name in the registry"
    sha = (registry.get("sha256") or {}).get(fname)
    if not sha:
        return None, "the lab recorded no SHA-256 for its file"
    r = next((x for x in (results or {}).get("results", []) if x.get("strategy") == name), {})
    if r.get("status") != "ok":
        return None, "the lab doesn't run it today"
    both = (r.get("backtest") or {}, r.get("live") or {})
    tf = next((b.get("timeframe") for b in both if b.get("timeframe_note") and b.get("timeframe")), None)
    market = bool(meta.get("market_orders")) or any(b.get("orders_note") for b in both)
    return {"name": name, "file": fname, "sha256": sha, "timeframe": tf, "market": market}, None


def config_for(p, base, market_cfg):
    """The lab's config, plus this strategy's own settings, plus the webhook to our collector. Always dry run, never a key."""
    c = json.loads(json.dumps(base))
    if p["market"]:
        for k, v in (market_cfg or {}).items():
            c[k] = {**c.get(k, {}), **v}
    if p["timeframe"]:
        c["timeframe"] = p["timeframe"]
    c["dry_run"] = True
    c["exchange"]["key"] = ""
    c["exchange"]["secret"] = ""
    c["bot_name"] = f"tas-runner-{p['name']}"
    c["webhook"] = {"enabled": True, "url": f"http://127.0.0.1:{PORT}/event/{p['name']}", "format": "json",
                    "retries": 3, "retry_delay": 1, "timeout": 5,
                    "entry_fill": {"event": "entry_fill", **{k: "{" + k + "}" for k in ("pair", "trade_id", "direction", "open_rate", "amount",
                                                                                        "stake_amount", "open_date", "enter_tag", "order_type")}},
                    "exit_fill": {"event": "exit_fill", **{k: "{" + k + "}" for k in ("pair", "trade_id", "direction", "open_rate", "close_rate",
                                                                                      "amount", "stake_amount", "open_date", "close_date",
                                                                                      "profit_ratio", "exit_reason", "order_type")}}}
    return c


def prepare(p, base, market_cfg, lab=LAB, ud=UD):
    """Copy the lab's frozen file in (refusing it if its hash has changed) and write the config. Returns the folder or raises."""
    src = os.path.join(lab, "strategies", p["file"])
    with open(src, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if got != p["sha256"]:
        raise RuntimeError(f"{p['file']} has changed since the lab first saw it ({got[:12]} != {p['sha256'][:12]})")
    d = os.path.join(ud, p["name"])
    os.makedirs(os.path.join(d, "strategies"), exist_ok=True)
    shutil.copyfile(src, os.path.join(d, "strategies", p["file"]))
    save_json(os.path.join(d, "config.json"), config_for(p, base, market_cfg))
    return d


# ---------- the collector: fills in, state out ----------

class Book:
    """Every fill per strategy, kept as one JSON line each, and the state rebuilt from them."""

    def __init__(self, ud, names):
        self.ud, self.names, self.lock = ud, set(names), threading.Lock()
        self.open, self.recent, self.total, self.last = {}, {}, {}, {}
        for n in self.names:
            self.open[n], self.recent[n], self.total[n] = {}, [], 0
            for line in self._lines(n):
                try:
                    self._apply(n, json.loads(line))
                except Exception:
                    continue

    def _lines(self, n):
        try:
            with open(os.path.join(self.ud, n, "events.jsonl")) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def _apply(self, n, ev):
        tid = str(ev.get("trade_id"))
        if ev["event"] == "entry_fill":
            self.open[n][tid] = {k: ev.get(k) for k in ("pair", "trade_id", "direction", "open_rate", "amount", "stake_amount", "open_date")}
        elif ev["event"] == "exit_fill":
            self.open[n].pop(tid, None)
        self.recent[n] = (self.recent[n] + [ev])[-RECENT:]
        self.total[n] += 1
        self.last[n] = ev.get("received")

    def add(self, n, payload):
        """Validate one webhook payload and record it. Returns (ok, why)."""
        if n not in self.names:
            return False, "unknown strategy"
        if not isinstance(payload, dict) or payload.get("event") not in ("entry_fill", "exit_fill"):
            return False, "not a fill"
        ev = {"event": payload["event"], "received": now()}
        for k in KEYS:
            v = payload.get(k)
            if v is None or v == "" or v == "None":
                continue
            v = str(v)[:64]
            if k in ("open_rate", "close_rate", "amount", "stake_amount", "profit_ratio"):
                try:
                    v = float(v)
                except ValueError:
                    continue
            ev[k] = v
        if "pair" not in ev or "trade_id" not in ev:
            return False, "missing pair or trade_id"
        with self.lock:
            os.makedirs(os.path.join(self.ud, n), exist_ok=True)
            with open(os.path.join(self.ud, n, "events.jsonl"), "a") as f:
                f.write(json.dumps(ev) + "\n")
            self._apply(n, ev)
        return True, "ok"

    def public(self, status=None):
        with self.lock:
            return {"as_of": now(),
                    "about": "TradingAgentStack runner: live paper copies of Strategy Lab bots, every fill as it happens. Paper money: no real "
                             "order is placed. Same 20 coins, rules and $1,000 paper wallet as the lab (Binance spot prices). Not financial advice.",
                    "strategies": {n: {"open": sorted(self.open[n].values(), key=lambda x: str(x.get("open_date"))),
                                       "recent": list(reversed(self.recent[n])), "fills": self.total[n], "last_fill": self.last.get(n),
                                       **({"process": status[n]} if status and n in status else {})}
                                   for n in sorted(self.names)}}


def make_handler(book, on_change):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            m = re.fullmatch(r"/event/([A-Za-z_][A-Za-z0-9_]{0,79})", self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 20000)) if n else b""
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                payload = None
            ok, why = book.add(m.group(1), payload) if m else (False, "bad path")
            self.send_response(200 if ok else 400)
            self.end_headers()
            self.wfile.write(why.encode())
            if ok:
                on_change()

        def log_message(self, *a):  # quiet: one line per fill is logged by the hub instead
            pass
    return H


# ---------- the supervisor ----------

def main():
    names = offered()
    registry = load_json(os.path.join(LAB, "registry.json"), {}) or {}
    results = load_json(os.path.join(LAB_PUB, "results.json"), {}) or {}
    base = load_json(os.path.join(LAB_CFG, "config.json"))
    market_cfg = load_json(os.path.join(LAB_CFG, "market-orders.json"), {})
    if not base:
        raise SystemExit("the lab's config.json is missing")
    runs, refused = {}, {}
    for n in names:
        p, why = plan(n, registry, results)
        if not p:
            refused[n] = why
            continue
        try:
            runs[n] = prepare(p, base, market_cfg)
        except Exception as e:
            refused[n] = str(e)[:200]
    book = Book(UD, list(runs))
    procs, state = {}, {n: {"restarts": 0, "next_start": 0.0, "since": None} for n in runs}

    def status():
        out = {}
        for n in runs:  # called from the collector's threads too, while the loop below may replace a process: read once, with get
            p = procs.get(n)
            out[n] = {"running": p is not None and p.poll() is None, "since": state[n]["since"], "restarts": state[n]["restarts"]}
        return out

    def publish():
        save_json(os.path.join(PUB, "signals.json"), book.public(status()))

    def write_status():
        save_json(os.path.join(PUB, "status.json"), {"as_of": now(), "offered": names, "running": status(), "refused": refused,
                                                      "note": "Paper money only. The hub holds no exchange keys."})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(book, publish))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("collector on", PORT, "| running:", ", ".join(runs) or "-", "| refused:", refused or "-")
    publish()
    while True:
        t = time.time()
        for n, d in runs.items():
            p = procs.get(n)
            if p and p.poll() is None:
                continue
            if p is not None:  # it stopped: wait longer each time, up to 10 minutes
                state[n]["restarts"] += 1
                state[n]["next_start"] = t + min(600, 30 * 2 ** min(state[n]["restarts"], 5))
                log(n, "exited with", p.returncode, "| restart", state[n]["restarts"])
                procs.pop(n)
            if t >= state[n]["next_start"]:
                procs[n] = subprocess.Popen(["freqtrade", "trade", "--config", os.path.join(d, "config.json"), "--userdir", d,
                                             "--strategy-path", os.path.join(d, "strategies"), "--strategy", n,
                                             "--db-url", f"sqlite:///{d}/paper.sqlite", "--logfile", os.path.join(d, "freqtrade.log")],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                state[n]["since"] = now()
        write_status()
        time.sleep(15)


if __name__ == "__main__":
    main()
