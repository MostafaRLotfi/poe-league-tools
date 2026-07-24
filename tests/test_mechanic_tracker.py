"""Headless tests: Allflame mechanic tracker, calibration, loot logging.

The patterns config is ALWAYS injected from tests/fixtures_mechanic/ --
never the shipped data/3.29/mechanic_lines.json, whose mechanic entries
are VERIFY-gated placeholders until launch-night calibration.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay"), os.path.join(ROOT, "tools")]

from mechanic_tracker import (MechanicTracker, candidates,  # noqa: E402
                              parse_chaos, parse_raw)
import allflame                                             # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_mechanic")
LOG = os.path.join(FIX, "session.log")
CFG_PATH = os.path.join(FIX, "config.json")
with open(CFG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)
with open(LOG, encoding="utf-8") as f:
    LINES = f.read().splitlines()

# ------------------------------------------------------------- raw parse
PRE = "2026/07/24 20:11:03 1234 ac9 [INFO Client 5]"
ts, msg = parse_raw(f"{PRE} : You have entered The Coast.")
assert (ts.hour, ts.minute, ts.second) == (20, 11, 3)
assert msg == "You have entered The Coast."
assert parse_raw("random noise") is None
assert parse_raw(f"{PRE} #Troll: You have entered The Coast.") is None, \
    "chat (speaker between ] and :) must not parse as a system line"
assert parse_raw(
    f"{PRE} @From Scam: lol ] : You have entered X.") is None, \
    "a '] : ' planted inside a chat payload must not be the match point"

# ------------------------------------------------- tracker: full session
tr = MechanicTracker(CFG)
events = [ev for ev in map(tr.feed, LINES) if ev]

assert events == [
    ("town", "Lioneye's Watch"),
    ("zone", "The Mud Flats"),
    ("voyage", "The Sunken Reef"),
    ("encounter", ("maro", "The Sunken Reef")),   # 3 barks -> 1 encounter
    ("voyage", "The Sunken Trench"),
    ("encounter", ("maro", "The Sunken Trench")),
    ("town", "Faustus' Hideout"),                 # substring town match
    ("zone", "The Riverways"),
    ("town", "Overseer's Tower"),
], f"event stream mismatch: {events}"

# chat spoofs: neither the fake voyage-zone entries nor the fake NPC bark
# produced events (checked via the stream above), and per_zone is clean
assert "The Sunken Fortress" not in tr.per_zone
assert "The Sunken Vault" not in tr.per_zone

s = tr.summary()
assert s["voyages"] == 2, s
assert s["encounters"] == 2, s
assert s["segments"] == 2, s

# segmentation: seg1 = 20:05 Mud Flats -> 21:00 hideout (55 min of field
# time), seg2 = 21:30 Riverways -> 21:45 town (15 min); town/hideout gaps
# (20:00-20:05, 21:00-21:30) contribute nothing
assert len(tr.segments) == 2
assert tr.segments[0]["active_s"] == 3300.0, tr.segments[0]
assert tr.segments[0]["start"].endswith("20:05:00")
assert tr.segments[0]["end"].endswith("21:00:00")
assert tr.segments[1]["active_s"] == 900.0, tr.segments[1]
assert tr.active_s == 4200.0

# active_hours excludes town time; rates come from active time only
assert s["active_hours"] == 1.17, s          # 70 min, not 105 min wall
assert s["voyages_per_hour"] == 1.71, s      # round(2 / (4200/3600), 2)
assert s["encounters_per_hour"] == 1.71, s

assert s["per_zone"] == {
    "The Mud Flats": {"entries": 1, "encounters": 0},
    "The Sunken Reef": {"entries": 1, "encounters": 1},
    "The Sunken Trench": {"entries": 1, "encounters": 1},
    "The Riverways": {"entries": 1, "encounters": 0},
}, s["per_zone"]

# re-entering a voyage zone is a NEW visit: the same NPC pattern counts again
tr2 = MechanicTracker(CFG)
base = "2026/07/24 22:0{m}:00 1234 ac9 [INFO Client 5] : {msg}"
for m, msg in [(0, "You have entered The Sunken Reef."),
               (1, "Allbinder Maro: The flame hungers for your cargo."),
               (2, "You have entered The Sunken Reef."),
               (3, "Allbinder Maro: The flame hungers for your cargo.")]:
    tr2.feed(base.format(m=m, msg=msg))
assert tr2.encounters == 2 and tr2.voyages == 2

# unknown location (before any zone line): NPC barks don't count
tr3 = MechanicTracker(CFG)
assert tr3.feed(
    f"{PRE} : Allbinder Maro: The flame hungers for your cargo.") is None
assert tr3.encounters == 0

# ------------------------------------------------------------ candidates
rows = candidates(LINES, CFG)
assert rows[0] == (4, "Allbinder Maro", "The flame hungers for your cargo."), \
    f"field NPC line must rank first: {rows}"
assert not any(sp == "Nessa" for _, sp, _ in rows), \
    f"town-heavy speaker (2 town barks vs 1 field) must be suppressed: {rows}"
assert candidates(LINES, CFG, top=0) == []

# ------------------------------------------------------------ parse_chaos
assert parse_chaos("15c") == 15.0
assert parse_chaos("15") == 15.0
assert parse_chaos("2.5 chaos") == 2.5
assert parse_chaos("1.5div", div_rate=200) == 300.0
assert parse_chaos("2divine", div_rate=100) == 200.0
try:
    parse_chaos("1.5div")
    raise AssertionError("div without a rate must raise")
except ValueError as e:
    assert "div-rate" in str(e)
try:
    parse_chaos("a stack of essences")
    raise AssertionError("garbage must raise")
except ValueError:
    pass

# ------------------------------------------------------------- loot jsonl
tmp = tempfile.mkdtemp(prefix="poe_allflame_test_")
try:
    runs = os.path.join(tmp, "runs")                 # created on demand
    rec = allflame.append_loot("15c", "ring", runs_dir=runs)
    assert rec["chaos"] == 15.0 and rec["raw"] == "15c"
    allflame.append_loot("1.5div", "", div_rate=200, runs_dir=runs)
    with open(os.path.join(runs, "allflame_loot.jsonl"),
              encoding="utf-8") as f:
        recs = [json.loads(line) for line in f]
    assert [r["chaos"] for r in recs] == [15.0, 300.0]
    assert recs[0]["note"] == "ring" and "ts" in recs[0]
    assert allflame.loot_total(runs) == (315.0, 2)
    assert allflame.loot_total(os.path.join(tmp, "nope")) == (0.0, 0)

    # CLI end-to-end: loot subcommand appends; report reads log + loot,
    # never the shipped placeholder config (we pass the fixture config)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    tool = os.path.join(ROOT, "tools", "allflame.py")
    p = subprocess.run(
        [sys.executable, tool, "loot", "10c", "test", "drop",
         "--runs-dir", runs],
        capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0 and "10.0c" in p.stdout, (p.stdout, p.stderr)
    p = subprocess.run(
        [sys.executable, tool, "report", "--log", LOG, "--config", CFG_PATH,
         "--runs-dir", runs],
        capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, (p.stdout, p.stderr)
    out = p.stdout
    assert "voyages:           2" in out and "1.71/h" in out, out
    assert "active time:       1.17 h" in out, out
    assert "325.0c" in out, out                      # 15 + 300 + 10
    assert "162.5c" in out, out                      # per encounter (÷2)
    p = subprocess.run(
        [sys.executable, tool, "candidates", "--log", LOG,
         "--config", CFG_PATH],
        capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "4 | Allbinder Maro | The flame hungers" in p.stdout, p.stdout
    assert "Nessa" not in p.stdout
    assert "mechanic_lines.json" in p.stdout, "calibration hint missing"
    p = subprocess.run(
        [sys.executable, tool, "loot", "3div", "--runs-dir", runs],
        capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode != 0 and "div-rate" in p.stderr, (p.stdout, p.stderr)
finally:
    shutil.rmtree(tmp)

# the shipped config must load and be structurally sound (contents are
# VERIFY placeholders -- nothing above depends on them)
with open(os.path.join(ROOT, "data", "3.29", "mechanic_lines.json"),
          encoding="utf-8") as f:
    shipped = json.load(f)
MechanicTracker(shipped)                 # all patterns must compile
assert "Lioneye's Watch" in shipped["town_zones"]
assert "Hideout" in shipped["town_substrings"]
assert "VERIFY" in json.dumps(shipped), \
    "un-calibrated guesses must stay flagged until launch-night data"

print("ALL TESTS PASSED")
