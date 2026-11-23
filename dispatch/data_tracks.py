#!/usr/bin/env python3
"""Start the site's "Data tracks" workflow as soon as the day's lab results are in (2026-11-23).

Why: the site's daily data job is a GitHub schedule at 01:17 UTC that GitHub starts six to seven hours late. On Nov 23 the
leverage board finished at 04:15 UTC and reached the site at 09:25 UTC. This script, run by
deploy.sh on the host every 10 minutes, asks GitHub to start "Data tracks" right away instead. "Data refresh" follows it
on the site's side, and the 01:17 schedule stays as the fallback (it skips itself if this run already covered the day).

When it asks: once per leverage-board run (strategy-lab-futures/results.json, summary.at), when that run is from today (UTC)
and the spot lab has also finished today, or it is past SPOT_WAIT_UNTIL_UTC and the spot lab still hasn't. A failed request
is tried again on the next deploy run, at most MAX_TRIES times per lab run; after that the site's schedule covers the day.

The token: one fine-grained GitHub token, limited to intoApex/tradingagentstack with Actions: read and write, so it can start
a workflow and nothing else (no code, no secrets). It lives in TOKEN_FILE on the host, never in this repo and never in a
folder any container mounts. Only this script reads it, and it never prints it. Without the file the script does nothing.
Test, no network: python3 tests/test_dispatch_data_tracks.py
"""
import datetime as dt
import json
import os
import stat
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.environ.get("TAS_DISPATCH_TOKEN_FILE", "/etc/tas/github-dispatch-token")
PUBLIC = os.environ.get("TAS_PUBLIC", "/srv/tas/public")
DATA = os.environ.get("TAS_DATA", "/srv/tas/data")
FUTURES = os.path.join(PUBLIC, "strategy-lab-futures", "results.json")
SPOT = os.path.join(PUBLIC, "strategy-lab", "results.json")
STATE = os.path.join(DATA, ".datatracks-dispatch.json")
STATUS = os.path.join(PUBLIC, "data-tracks-dispatch.json")  # public: what was asked and when, never the token
REPO = "intoApex/tradingagentstack"
WORKFLOW = "data-tracks.yml"
URL = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
SPOT_WAIT_UNTIL_UTC = 7   # hour: from then on, don't wait any longer for the spot lab
MAX_TRIES = 6             # per leverage-board run; deploy.sh runs every 10 minutes


def lab_at(path):
    """summary.at of a lab's results.json, or '' when the file is missing or unreadable."""
    try:
        return str((json.load(open(path)).get("summary") or {}).get("at") or "")
    except (OSError, ValueError):
        return ""


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=1)
    os.replace(tmp, path)


def decide(now, fut_at, spot_at, state):
    """('send', why) or ('wait', why). Pure: no files, no network."""
    today = now.strftime("%Y-%m-%d")
    if not fut_at.startswith(today):
        return "wait", "the leverage board has no run from today yet"
    if state.get("lab_at") == fut_at:
        if state.get("status") == "sent":
            return "wait", "already asked for this run"
        if int(state.get("tries") or 0) >= MAX_TRIES:
            return "wait", f"gave up after {MAX_TRIES} tries; the site's schedule covers today"
    if not spot_at.startswith(today) and now.hour < SPOT_WAIT_UNTIL_UTC:
        return "wait", "waiting for the spot lab to finish today"
    return "send", "the leverage board finished" + ("" if spot_at.startswith(today) else "; the spot lab has not, sent anyway")


def read_token(path):
    """(token, None) or (None, why). Refuses a file anyone else on the host could read."""
    try:
        st = os.stat(path)
    except OSError:
        return None, f"no token in {path}"
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None, f"{path} must be readable by its owner only (chmod 600), so it was not used"
    try:
        tok = open(path).read().strip()
    except OSError:
        return None, f"{path} can't be read by this user"
    return (tok, None) if tok else (None, f"{path} is empty")


def dispatch(token, fut_at, opener=urllib.request.urlopen):
    """Ask GitHub to start the workflow on main. Returns (ok, short note). The note never contains the token."""
    body = json.dumps({"ref": "main", "inputs": {"lab_at": fut_at}}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json", "User-Agent": "tas-workers-dispatch/1.0 (+https://tradingagentstack.com/about/)"})
    try:
        r = opener(req, timeout=20)
        code = getattr(r, "status", None) or r.getcode()
        return code == 204, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode() or "{}").get("message", "")
        except Exception:
            msg = ""
        return False, f"HTTP {e.code} {msg}"[:200].replace(token, "[token]")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:200].replace(token, "[token]")


def main(now=None, opener=urllib.request.urlopen):
    now = now or dt.datetime.now(dt.timezone.utc)
    fut_at, spot_at = lab_at(FUTURES), lab_at(SPOT)
    state = load(STATE)
    what, why = decide(now, fut_at, spot_at, state)
    if what != "send":
        return 0
    token, problem = read_token(TOKEN_FILE)
    if not token:
        if state.get("lab_at") != fut_at or state.get("status") != "no token" or state.get("note") != problem:
            print(f"{now:%FT%TZ} {fut_at}: {problem}; not asked")  # once per lab run and problem, not every 10 minutes
            save(STATE, {"lab_at": fut_at, "status": "no token", "tries": 0, "at": f"{now:%FT%TZ}", "note": problem})
        return 0
    tries = (int(state.get("tries") or 0) if state.get("lab_at") == fut_at else 0) + 1
    ok, note = dispatch(token, fut_at, opener)
    rec = {"lab_at": fut_at, "spot_at": spot_at, "status": "sent" if ok else "failed", "tries": tries, "at": f"{now:%FT%TZ}",
           "note": note, "why": why, "workflow": f"{REPO} {WORKFLOW} on main"}
    save(STATE, rec)
    save(STATUS, rec)
    print(f"{now:%FT%TZ} {fut_at}: {'asked GitHub to start Data tracks' if ok else 'request failed'} ({note}; try {tries}; {why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
