"""Headless tests: area-event parsing, layout index, UI state,
fetch-script path safety, route cross-check against Exile-UI data."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay"), os.path.join(ROOT, "tools"),
                ROOT]

from client_watcher import last_area, parse_line       # noqa: E402
from layout_index import LayoutIndex                   # noqa: E402
from ui_state import (UiState, clamp_scale, clamp_size,    # noqa: E402
                      valid_pos)
from main import dispatch_events                       # noqa: E402
from route_engine import RouteEngine                   # noqa: E402
from party_state import PartyState                     # noqa: E402
import crosscheck_routes as cc                         # noqa: E402
import fetch_layouts                                   # noqa: E402

# ------------------------------------------------ area ('Generating') lines
DBG = "2026/07/24 20:11:02 1234 ac9 [DEBUG Client 5]"
INF = "2026/07/24 20:11:03 1234 ac9 [INFO Client 5]"

assert parse_line(
    f'{DBG} Generating level 33 area "1_4_2" with seed 1234567890') == \
    ("area", ("1_4_2", 33, 1234567890))
assert parse_line(
    f'{DBG} Generating level 2 area "1_1_town" with seed 1') == \
    ("area", ("1_1_town", 2, 1))

# chat can never spoof it: player text always sits behind '<Name>:' in an
# INFO line, and the pattern is anchored to the raw line start
spoof = 'Generating level 33 area "1_4_2" with seed 666'
assert parse_line(f"{INF} #Troll: {spoof}") is None
assert parse_line(f"{INF} : {spoof}") is None            # system chat echo
assert parse_line(f"{INF} @From Scam: {spoof}") is None
assert parse_line(spoof) is None                          # no log prefix

# malformed variants stay unparsed
assert parse_line(f'{DBG} Generating level x area "1_4_2" with seed 1') is None
assert parse_line(f'{DBG} Generating level 33 area 1_4_2 with seed 1') is None

# ---------------------------------------------------------------- last_area
tmp = tempfile.mkdtemp()
try:
    log = os.path.join(tmp, "Client.txt")
    with open(log, "w", encoding="utf-8") as f:
        f.write(f'{DBG} Generating level 2 area "1_1_2" with seed 11\n')
        f.write(f"{INF} : You have entered The Coast.\n")
        f.write(f'{DBG} Generating level 4 area "1_1_3" with seed 22\n')
        f.write(f"{INF} : You have entered The Mud Flats.\n")
    assert last_area(log) == ("1_1_3", 4, 22)
    assert last_area(os.path.join(tmp, "missing.txt")) is None
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------- dispatch_events wiring
engine = RouteEngine(os.path.join(ROOT, "routes"))
party = PartyState(me="Me", members=[])
ops = dispatch_events([("area", ("1_1_2", 2, 99))], engine, party)
assert ops == [("area", ("1_1_2", 2, 99))]

# ------------------------------------------------------------- LayoutIndex
tmp = tempfile.mkdtemp()
try:
    zones = os.path.join(tmp, "zones")
    os.makedirs(zones)
    for name in ["1_1_2 1.jpg", "1_1_2 1_1.jpg", "1_1_2 2.jpg",
                 "1_1_2 10.png", "1_1_2 x.jpg", "1_1_2 y.jpg",
                 "1_1_2 y_1.jpg", "2_6_town 1.jpg", "junk.txt",
                 "no-space.jpg"]:
        open(os.path.join(zones, name), "w").close()

    idx = LayoutIndex(tmp)
    assert idx.count == 8                       # junk.txt / no-space skipped
    assert idx.has("1_1_2") and idx.has("2_6_town")
    assert not idx.has("9_9_9")
    assert idx.variants("9_9_9") == []

    var = idx.variants("1_1_2")
    heads = [h for h, _ in var]
    assert heads == ["1", "2", "10", "x", "y"]  # numeric order, then x, y
    paths = dict(var)
    assert [os.path.basename(p) for p in paths["1"]] == \
        ["1_1_2 1.jpg", "1_1_2 1_1.jpg"]        # head first, then chain
    assert [os.path.basename(p) for p in paths["y"]] == \
        ["1_1_2 y.jpg", "1_1_2 y_1.jpg"]

    # missing pack dir -> empty index, no crash
    empty = LayoutIndex(os.path.join(tmp, "nope"))
    assert empty.count == 0 and not empty.has("1_1_2")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# the real fetched pack, when present, must index cleanly
real = LayoutIndex(os.path.join(ROOT, "overlay", "assets", "layouts"))
if real.count:
    assert real.count > 400 and real.has("1_1_2")
    assert all(os.path.exists(p)
               for _, ps in real.variants("1_1_2") for p in ps)

# ---------------------------------------------------------------- UiState
assert clamp_scale(1.0) == 1.0
assert clamp_scale(99) == 2.5
assert clamp_scale(0.1) == 0.5
assert clamp_scale("junk") == 1.0
assert clamp_scale(None) == 1.0
assert valid_pos([40, 140]) == (40, 140)
assert valid_pos([40.5, 140]) is None
assert valid_pos([True, False]) is None
assert valid_pos("40,140") is None
assert valid_pos(None) is None

# clamp_size keeps the overlay (and its resize grip) within screen bounds
assert clamp_size(500, 350, 200, 1200, 1, 2000) == (500, 350)   # already inside
assert clamp_size(1200, 2000, 200, 800, 1, 800) == (800, 800), \
    "1200x2000 restored on an 800x800 display is capped on-screen"
assert clamp_size(50, 5, 200, 1200, 107, 2000) == (200, 107), "floors applied"
assert clamp_size(360.0, 220.9, 200, 1200, 1, 2000) == (360, 220), "coerced to int"

tmp = tempfile.mkdtemp()
try:
    path = os.path.join(tmp, "ui_state.json")
    st = UiState(path)
    assert st.get("card", "scale") == 1.0       # defaults
    st.set("card", "scale", 1.3)
    st.set("layouts", "pos", [700, 200])
    st2 = UiState(path)                          # round-trips
    assert st2.get("card", "scale") == 1.3
    assert valid_pos(st2.get("layouts", "pos")) == (700, 200)
    assert st2.get("card", "compact") is False   # untouched default intact

    with open(path, "w", encoding="utf-8") as f:
        f.write("{corrupted")
    st3 = UiState(path)                          # corrupt file -> defaults
    assert st3.get("card", "scale") == 1.0

    with open(path, "w", encoding="utf-8") as f:
        json.dump(["not", "a", "dict"], f)
    assert UiState(path).get("card", "scale") == 1.0
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# -------------------------------------------- fetch_layouts path sanitizing
sm = fetch_layouts.safe_member
assert sm("Exile-UI-layouts/zones/1_1_2 1.jpg") == "zones/1_1_2 1.jpg"
assert sm("Exile-UI-layouts/version.json") == "version.json"
assert sm("Exile-UI-layouts/file-list.json") == "file-list.json"
assert sm("Exile-UI-layouts/zones/evil.exe") is None
assert sm("Exile-UI-layouts/.gitignore") is None
assert sm("Exile-UI-layouts/zones/../../../etc/passwd") is None
assert sm("Exile-UI-layouts/zones/sub/dir.jpg") is None    # one level only
assert sm("top-only") is None
assert sm("Exile-UI-layouts/zones/UPPER.PNG") == "zones/UPPER.PNG"

# ------------------------------------- routes vs Exile-UI data (hard checks)
assert cc.norm("The Chamber of Sins Level 1") == "chamber of sins (1)"
assert cc.norm("Twilight Strand") == cc.norm("The Twilight Strand")
assert cc.norm("The Coast") == "coast"

per_act, by_id = cc.load_areas()
routes = cc.load_routes()
assert cc.check_names(routes, per_act) == [], \
    "route zone names must resolve against data/exileui/areas.json"
assert cc.check_levels(routes, per_act) == [], \
    "route arealvl values must match data/exileui/areas.json"
assert by_id["1_1_2"][1]["name"] == "The Coast"

# guide markup parses into per-act area-id sequences
gids = cc.guide_area_ids()
assert set(gids) == set(range(1, 11))
assert "1_1_town" in gids[1] and "1_1_2" in gids[1]

print("ALL TESTS PASSED")
