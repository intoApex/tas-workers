#!/usr/bin/env python3
"""Every AI league season folder: each bot file is there and is exactly the model's answer. Run: python3 tests/test_league_files.py

strategy-lab/league/<season>/league.json carries, for every bot, the SHA-256 of the first code block of its answer as it was
saved on the site (data/league/<season>/answers/<slug>.json, "code_sha256"). The files here are never edited, so each one's
SHA-256 must match. A bot whose answer had no code block has file null and a note."""
import glob, hashlib, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))

for f in sorted(glob.glob(os.path.join(ROOT, "strategy-lab", "league", "*", "league.json"))):
    d = os.path.dirname(f); m = json.load(open(f)); season = m.get("season")
    check(f"{season}: the folder is named after the season", os.path.basename(d) == season, d)
    check(f"{season}: days, leverage cap and retirement drop are set", m.get("days") and m.get("leverage_cap") and m.get("retire_drawdown"))
    classes = [b["class"] for b in m["bots"]]
    check(f"{season}: every class name is unique", len(classes) == len(set(classes)), classes)
    for b in m["bots"]:
        if not b.get("file"):
            check(f"{season} {b['class']}: no file, with a note", bool(b.get("note"))); continue
        p = os.path.join(d, b["file"])
        check(f"{season} {b['class']}: {b['file']} is there", os.path.isfile(p))
        if os.path.isfile(p):
            check(f"{season} {b['class']}: {b['file']} is the answer byte for byte (SHA-256)",
                  hashlib.sha256(open(p, "rb").read()).hexdigest() == b.get("code_sha256"), b.get("code_sha256"))
    extra = {os.path.basename(x) for x in glob.glob(os.path.join(d, "*.py"))} - {b.get("file") for b in m["bots"]}
    check(f"{season}: no other Python file in the folder", not extra, extra)
print("FAILURES:", 0 if ok else "some"); sys.exit(0 if ok else 1)
