#!/usr/bin/env python3
"""Starting the site's "Data tracks" workflow when the day's lab results are in (dispatch/data_tracks.py).
Run: python3 tests/test_dispatch_data_tracks.py   No network: GitHub is a stand-in and everything is written to a temp folder."""
import datetime as dt, importlib.util, io, json, os, sys, tempfile, urllib.error
from contextlib import redirect_stdout

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.environ.update({"TAS_PUBLIC": tmp + "/public", "TAS_DATA": tmp + "/data", "TAS_DISPATCH_TOKEN_FILE": tmp + "/token"})
spec = importlib.util.spec_from_file_location("dt_", os.path.join(REPO, "dispatch", "data_tracks.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name, "" if cond else extra); ok = ok and bool(cond)

TOKEN = "github_pat_TESTONLY_0123456789"
U = dt.timezone.utc
NOW = dt.datetime(2026, 11, 24, 4, 20, tzinfo=U)
FUT_TODAY, FUT_OLD = "2026-11-24T04:15:21.6Z", "2026-11-23T04:15:21.6Z"
SPOT_TODAY, SPOT_OLD = "2026-11-24T03:21:04Z", "2026-11-23T06:21:04Z"

def lab(which, at):
    p = m.FUTURES if which == "fut" else m.SPOT
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"as_of": at[:10], "summary": {"at": at}, "results": [{"x": 1}]}, open(p, "w"))

def token(text=TOKEN, mode=0o600):
    open(m.TOKEN_FILE, "w").write(text + "\n"); os.chmod(m.TOKEN_FILE, mode)

calls = []
class Resp:
    def __init__(self, code): self.status = code
    def getcode(self): return self.status
def github(code=204):
    def opener(req, timeout=None):
        calls.append({"url": req.full_url, "method": req.get_method(), "auth": req.headers.get("Authorization"), "body": json.loads(req.data)})
        if code != 204:
            raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(json.dumps({"message": "Bad credentials"}).encode()))
        return Resp(204)
    return opener

def run(now=NOW, code=204):
    calls.clear(); out = io.StringIO()
    with redirect_stdout(out): m.main(now=now, opener=github(code))
    return out.getvalue()

# 1. the rule itself
d = m.decide
check("decide: no run from today yet -> wait", d(NOW, FUT_OLD, SPOT_TODAY, {})[0] == "wait")
check("decide: both labs done today -> send", d(NOW, FUT_TODAY, SPOT_TODAY, {})[0] == "send")
check("decide: spot lab not done, before 07:00 -> wait", d(NOW, FUT_TODAY, SPOT_OLD, {})[0] == "wait")
check("decide: spot lab not done, from 07:00 -> send anyway", d(NOW.replace(hour=7), FUT_TODAY, SPOT_OLD, {})[0] == "send")
check("decide: already sent for this run -> wait", d(NOW, FUT_TODAY, SPOT_TODAY, {"lab_at": FUT_TODAY, "status": "sent"})[0] == "wait")
check("decide: a new lab run on the same day -> send again", d(NOW, FUT_TODAY, SPOT_TODAY, {"lab_at": "2026-11-24T01:00:00Z", "status": "sent"})[0] == "send")
check("decide: failed under the cap -> try again", d(NOW, FUT_TODAY, SPOT_TODAY, {"lab_at": FUT_TODAY, "status": "failed", "tries": 2})[0] == "send")
check("decide: failed MAX_TRIES times -> stop", d(NOW, FUT_TODAY, SPOT_TODAY, {"lab_at": FUT_TODAY, "status": "failed", "tries": m.MAX_TRIES})[0] == "wait")

# 2. no token: nothing is asked, said once
lab("fut", FUT_TODAY); lab("spot", SPOT_TODAY)
o = run()
check("no token: no request, one line", not calls and "no token" in o, o)
o2 = run()
check("no token: not repeated every 10 minutes", not calls and o2 == "", o2)

# 3. a token readable by others is refused
token(mode=0o644)
o = run()
check("token readable by others: refused, no request", not calls and "chmod 600" in o, o)
o2 = run()
check("token readable by others: said once, not every 10 minutes", not calls and o2 == "", o2)

# 4. the request: main, the lab timestamp, the token only in the header, nothing printed of it
token()
o = run()
c = calls[0] if calls else {}
check("sends one request", len(calls) == 1, calls)
check("request: POST to the data-tracks.yml dispatch endpoint", c.get("method") == "POST"
      and c.get("url") == "https://api.github.com/repos/intoApex/tradingagentstack/actions/workflows/data-tracks.yml/dispatches", c.get("url"))
check("request: ref main and the lab timestamp as input", c.get("body") == {"ref": "main", "inputs": {"lab_at": FUT_TODAY}}, c.get("body"))
check("request: token sent as a bearer header", c.get("auth") == f"Bearer {TOKEN}")
st = json.load(open(m.STATE)); pub = json.load(open(m.STATUS))
check("state: sent, one try", st.get("status") == "sent" and st.get("tries") == 1 and st.get("lab_at") == FUT_TODAY, st)
check("the token appears in no output or file", TOKEN not in o and TOKEN not in json.dumps(st) and TOKEN not in json.dumps(pub), o)
o = run()
check("after a success: not asked again for the same run", not calls and o == "", o)

# 5. failures are retried on the next deploy run, up to the cap, and never echo the token
os.remove(m.STATE)
for i in range(m.MAX_TRIES):
    o = run(code=401)
    check(f"failure {i + 1}: asked, recorded as failed", len(calls) == 1 and json.load(open(m.STATE)).get("tries") == i + 1, o)
check("failure note keeps GitHub's message, not the token", "Bad credentials" in o and TOKEN not in o, o)
o = run(code=204)
check(f"after {m.MAX_TRIES} failures: stops asking (the schedule covers the day)", not calls, o)

# 6. the next day's run is asked for again
lab("fut", "2026-11-25T04:14:00Z"); lab("spot", "2026-11-25T03:20:00Z")
run(now=dt.datetime(2026, 11, 25, 4, 20, tzinfo=U))
check("next day: asked again", len(calls) == 1 and calls[0]["body"]["inputs"]["lab_at"] == "2026-11-25T04:14:00Z")

# 7. deploy.sh runs it on the host, and no container can see the token file
dep = open(os.path.join(REPO, "deploy.sh")).read()
check("deploy.sh runs dispatch/data_tracks.py", "python3 dispatch/data_tracks.py" in dep)
comp = open(os.path.join(REPO, "compose.yaml")).read()
check("no service mounts /etc/tas or the token", "/etc/tas" not in comp and "dispatch" not in comp)

print("FAILURES:", 0 if ok else "see above")
sys.exit(0 if ok else 1)
