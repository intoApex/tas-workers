"""A venue's share-price history as one public file, and the way back from it.

The "our own record" figures (return since we started tracking, worst drop) are computed from one small file per day,
<data>/<venue>/prices/<day>.json = {vault id: share price}. Those files exist only on this server's disk: lose the disk and
every record restarts from zero. So each scan also publishes the whole history as one file, which the site's nightly
"Data tracks" run copies to the perf-data branch of the site repository, off this server.

Published file (public share prices of public vaults, nothing private):
  {"as_of": "2026-11-11", "venue": "paradex", "days": ["2026-11-09", "2026-11-10", "2026-11-11"],
   "prices": {"<vault id>": [0, [1.01, 1.02, null]]}}
"days" are the days we have a price file for. Each vault has [index of its first day in "days", its price on each day from
there on]; null is a day the venue did not list it. Prices are written exactly as saved, so a restore is exact.

Which vaults are in it: every vault the caller asks to keep today (the scanners pass those with at least a tenth of the
venue's publishing minimum in them), plus every vault already in the published file or in a backup placed in restore/,
so a vault that shrinks or is delisted keeps its history, also across a restore. Dust vaults are left out: dHEDGE alone
lists 3,777 and all of them every day would be a 25 MB file within a year.

The way back, restore(): any day the backup has and the prices folder does not is written back from the backup. Days that
are on disk are never touched. It reads the first source that parses:
  1. the published file itself (covers a lost data folder while the public folder survived), then
  2. restore/<...>-prices.json in this repository (covers a lost disk: copy the file from the site repository's perf-data
     branch, folder vault-prices/, into restore/ with a pull request; the server pulls it and the next scan fills the days in).
"""
import datetime as dt, json, os


def _is_day(name):
    try:
        dt.date.fromisoformat(name[:10]); return name.endswith(".json") and len(name) == 15
    except ValueError:
        return False


def days_on_disk(prices_dir):
    return sorted(n[:10] for n in os.listdir(prices_dir) if _is_day(n)) if os.path.isdir(prices_dir) else []


def series(prices_dir):
    """{vault id: [(day, price), ...]} from the day files, oldest first."""
    out = {}
    for day in days_on_disk(prices_dir):
        try:
            prices = json.load(open(os.path.join(prices_dir, day + ".json")))
        except Exception:
            continue
        for k, p in prices.items():
            if p and p > 0:
                out.setdefault(k, []).append((day, p))
    return out


def _read(path):
    """A published history file as {day: {id: price}}, or None when it is missing or not what we wrote."""
    try:
        d = json.load(open(path))
        days, out = d["days"], {}
        if not isinstance(days, list) or not isinstance(d["prices"], dict):
            return None
        for vid, (start, vals) in d["prices"].items():
            for i, p in enumerate(vals):
                if p and p > 0:
                    out.setdefault(days[start + i], {})[vid] = p
        return out
    except Exception:
        return None


def published_ids(path):
    try:
        return set(json.load(open(path))["prices"])
    except Exception:
        return set()


def publish(prices_dir, out_path, venue, as_of, keep, also=()):
    """Write the history of the vaults in `keep`, of those already published and of those in the backups listed in `also`
    (so a vault that has since shrunk or been delisted is still there after a restore). Returns (days, vaults)."""
    hist, days = series(prices_dir), days_on_disk(prices_dir)
    if not days:
        return 0, 0
    idx = {d: i for i, d in enumerate(days)}
    ids = set(keep) | published_ids(out_path)
    for path in also:
        ids |= published_ids(path)
    ids &= set(hist)
    prices = {}
    for vid in sorted(ids):
        pts = {idx[d]: p for d, p in hist[vid]}
        first, last = min(pts), max(pts)
        prices[vid] = [first, [pts.get(i) for i in range(first, last + 1)]]
    doc = {"as_of": as_of, "venue": venue, "days": days, "prices": prices,
           "about": "Daily share prices saved by TradingAgentStack, the source of its own vault records. Each vault: [index of its "
                    "first day in days, its price on each day from there on]; null is a day the venue did not list it."}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path + ".tmp", "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(out_path + ".tmp", out_path)
    return len(days), len(prices)


def restore(prices_dir, sources):
    """Write back every day a backup has and the folder lacks. Returns (days written, source used) or (0, None)."""
    have = set(days_on_disk(prices_dir))
    for src in sources:
        book = _read(src)
        if not book:
            continue
        missing = sorted(d for d in book if d not in have)
        if not missing:
            continue
        os.makedirs(prices_dir, exist_ok=True)
        for day in missing:
            p = os.path.join(prices_dir, day + ".json")
            with open(p + ".tmp", "w") as fh:
                json.dump(book[day], fh, separators=(",", ":"))
            os.replace(p + ".tmp", p)
        return len(missing), src
    return 0, None
