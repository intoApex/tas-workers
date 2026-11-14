#!/usr/bin/env python3
"""The runner's signal hub (runner/hub.py). Run: python3 tests/test_runner_hub.py   No Freqtrade and no network.

The rules this protects: the hub runs only strategies the lab runs today, on the lab's own frozen file (a changed file is
refused), always in dry run with no key; the collector accepts only fills for strategies it runs, keeps every fill on disk
and rebuilds the same state from it after a restart."""
import hashlib, importlib.util, json, os, sys, tempfile, threading, urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
spec = importlib.util.spec_from_file_location("hub", os.path.join(REPO, "runner", "hub.py"))
hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    ok = ok and bool(cond)

base = json.load(open(os.path.join(REPO, "strategy-lab", "config.json")))
market_cfg = json.load(open(os.path.join(REPO, "strategy-lab", "market-orders.json")))
code = b"class Dip(IStrategy):\n    timeframe = '5m'\n"
sha = hashlib.sha256(code).hexdigest()
registry = {"strategies": {"Dip": {"file": "dip.py"}, "Mkt": {"file": "mkt.py", "market_orders": True}, "NoTf": {"file": "notf.py"},
                           "Gone": {"file": "gone.py"}, "Bad": {"file": "../etc/passwd"}},
            "sha256": {"dip.py": sha, "mkt.py": sha, "notf.py": sha, "gone.py": sha, "../etc/passwd": sha}}
results = {"results": [{"strategy": "Dip", "status": "ok", "live": {"timeframe": "5m"}},
                       {"strategy": "Mkt", "status": "ok", "backtest": {"orders_note": "market orders"}},
                       {"strategy": "NoTf", "status": "ok", "backtest": {"timeframe": "5m", "timeframe_note": "not set by the strategy; run on 5m"}},
                       {"strategy": "Gone", "status": "error"}]}

# plan(): only what the lab runs, as the lab ran it
p, why = hub.plan("Dip", registry, results)
check("a bot the lab runs gets a plan with its file and hash", p == {"name": "Dip", "file": "dip.py", "sha256": sha, "timeframe": None, "market": False}, p)
check("a bot the lab doesn't run today is refused", hub.plan("Gone", registry, results)[0] is None and "today" in hub.plan("Gone", registry, results)[1])
check("a bot the lab never saw is refused", hub.plan("Nope", registry, results)[0] is None)
check("a registry file name with a path in it is refused", hub.plan("Bad", registry, results)[0] is None)
check("market orders and the lab's candle size carry over", hub.plan("Mkt", registry, results)[0]["market"] and hub.plan("NoTf", registry, results)[0]["timeframe"] == "5m")

# config_for(): the lab's config, dry run, no key, our webhook
c = hub.config_for(hub.plan("Mkt", registry, results)[0], dict(base, dry_run=False, exchange=dict(base["exchange"], key="k", secret="s")), market_cfg)
check("always dry run with an empty key and secret, even if the base config said otherwise", c["dry_run"] is True and c["exchange"]["key"] == "" and c["exchange"]["secret"] == "")
check("same coins and wallet as the lab", c["exchange"]["pair_whitelist"] == base["exchange"]["pair_whitelist"] and c["dry_run_wallet"] == base["dry_run_wallet"])
check("market orders: the lab's price-side settings are merged in", c["entry_pricing"]["price_side"] == "other" and c["exit_pricing"]["price_side"] == "other" and c["entry_pricing"]["use_order_book"] is True)
check("the webhook posts both kinds of fill to the collector for this bot", c["webhook"]["url"].endswith("/event/Mkt") and c["webhook"]["entry_fill"]["pair"] == "{pair}" and c["webhook"]["exit_fill"]["profit_ratio"] == "{profit_ratio}")
check("the base config is not changed", base["dry_run"] is True and "webhook" not in base)

# prepare(): the frozen file or nothing
with tempfile.TemporaryDirectory() as t:
    lab, ud = os.path.join(t, "lab"), os.path.join(t, "ud")
    os.makedirs(os.path.join(lab, "strategies"))
    open(os.path.join(lab, "strategies", "dip.py"), "wb").write(code)
    d = hub.prepare(hub.plan("Dip", registry, results)[0], base, market_cfg, lab=lab, ud=ud)
    check("prepare copies the file and writes the config", open(os.path.join(d, "strategies", "dip.py"), "rb").read() == code and json.load(open(os.path.join(d, "config.json")))["bot_name"] == "tas-runner-Dip")
    open(os.path.join(lab, "strategies", "dip.py"), "ab").write(b"# changed\n")
    try:
        hub.prepare(hub.plan("Dip", registry, results)[0], base, market_cfg, lab=lab, ud=ud); refused = False
    except RuntimeError as e:
        refused = "changed" in str(e)
    check("a file that changed since the lab first saw it is refused", refused)

    # Book: fills in, state out, and the same state after a restart
    book = hub.Book(ud, ["Dip"])
    entry = {"event": "entry_fill", "pair": "BTC/USDT", "trade_id": "7", "open_rate": "65000.5", "amount": "0.003", "stake_amount": "199.8", "open_date": "2026-11-14 10:00:00", "junk": "x"}
    check("an entry fill is accepted", book.add("Dip", entry) == (True, "ok"))
    check("numbers are numbers, unknown fields are dropped", book.recent["Dip"][-1]["open_rate"] == 65000.5 and "junk" not in book.recent["Dip"][-1])
    check("an open trade appears", list(book.open["Dip"]) == ["7"])
    check("a fill for a bot we don't run is refused", book.add("Other", entry)[0] is False)
    check("anything that isn't a fill is refused", book.add("Dip", {"event": "status"})[0] is False and book.add("Dip", "x")[0] is False and book.add("Dip", {"event": "entry_fill"})[0] is False)
    book.add("Dip", {"event": "exit_fill", "pair": "BTC/USDT", "trade_id": "7", "close_rate": "65500", "profit_ratio": "0.0057", "exit_reason": "roi"})
    check("an exit fill closes it", book.open["Dip"] == {} and book.total["Dip"] == 2)
    book.add("Dip", dict(entry, trade_id="8"))
    again = hub.Book(ud, ["Dip"])
    check("after a restart the state is rebuilt from the file", list(again.open["Dip"]) == ["8"] and again.total["Dip"] == 3 and again.recent["Dip"] == book.recent["Dip"])
    pub = again.public({"Dip": {"running": True}})
    check("the public file says paper money and lists the newest fill first", "Paper money" in pub["about"] and pub["strategies"]["Dip"]["recent"][0]["trade_id"] == "8" and pub["strategies"]["Dip"]["process"]["running"] is True)

    # the collector over HTTP, as Freqtrade's webhook calls it
    hits = []
    srv = hub.http.server.ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(again, lambda: hits.append(1)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    def post(path, body):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
    check("POST /event/<bot> with a fill: 200, published", post("/event/Dip", dict(entry, trade_id="9")) == 200 and hits == [1])
    check("unknown bot or path: 400, not published", post("/event/Other", entry) == 400 and post("/event/../x", entry) == 400 and hits == [1])
    srv.shutdown()

# the shipped list: valid names, and none of them the lab's test strategies
names = hub.offered()
check(f"runner/strategies.json names 1 to 5 bots ({len(names)})", 1 <= len(names) <= 5, names)

print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
