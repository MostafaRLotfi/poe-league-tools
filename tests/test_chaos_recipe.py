"""Headless tests for market/stashapi.py + tools/chaos_recipe.py.

Offline only: the stash endpoint is exercised through an injected fake
fetcher (no network, no real POESESSID); recipe math runs on the
hand-authored fixture tests/fixtures_stash/dump_tab.json.
"""
import contextlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]

from market import stashapi                     # noqa: E402
from craft.pool import CraftData                # noqa: E402
import chaos_recipe                             # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_stash", "dump_tab.json")
with open(FIX, encoding="utf-8") as f:
    FIXTURE = json.load(f)

CRAFT = CraftData.load()

_saved_sessid = os.environ.pop("POESESSID", None)  # tests run env-stripped


def mk(base, ilvl=70, identified=False, frame=2):
    return {"name": "", "typeLine": base, "baseType": base, "ilvl": ilvl,
            "identified": identified, "frameType": frame, "w": 1, "h": 1}


# ------------------------------------------------- keyword fallback (no data)
for base, want in [
    ("Full Ringmail", "body"),          # 'Ringmail' must NOT match ring
    ("Glorious Plate", "body"),
    ("Ruby Ring", "ring"),
    ("Agate Amulet", "amulet"),
    ("Heavy Belt", "belt"),
    ("Stygian Vise", "belt"),
    ("Nightmare Bascinet", "helmet"),
    ("Slink Gloves", "gloves"),
    ("Shagreen Boots", "boots"),
    ("Corrugated Buckler", "shield"),
    ("Broadhead Arrow Quiver", "quiver"),
    ("Long Bow", "weapon2h"),
    ("Coiled Staff", "weapon2h"),
    ("Karui Maul", "weapon2h"),
    ("Driftwood Club", "weapon1h"),
    ("Prophecy Wand", "weapon1h"),
    ("Ambusher", "unknown"),            # no keyword -> unknown, no crash
    ("Frayed Mystery Box", "unknown"),
]:
    got = stashapi.classify_slot(mk(base), craft_data=None)
    assert got == want, f"fallback {base}: {got} != {want}"

# dataset classification (craft.pool.CraftData base -> item class)
assert stashapi.classify_slot(mk("Vaal Regalia"), CRAFT) == "body"
assert stashapi.classify_slot(mk("Thicket Bow"), CRAFT) == "weapon2h"
assert stashapi.classify_slot(mk("Jewelled Foil"), CRAFT) == "weapon1h"
assert stashapi.classify_slot(mk("Ambusher"), CRAFT) == "weapon1h", \
    "dataset resolves keyword-less bases (Ambusher is a dagger)"
# magic-style typeLine still resolves via the substring base lookup
magic = {"typeLine": "Sparkling Sapphire Ring of the Whelpling",
         "baseType": "", "frameType": 2, "ilvl": 70, "identified": True}
assert stashapi.classify_slot(magic, CRAFT) == "ring"
# a bogus craft_data object degrades to the keyword fallback, not a crash
assert stashapi.classify_slot(mk("Ruby Ring"), craft_data=object()) == "ring"

# --------------------------------------------------- fixture analysis (data)
report = stashapi.analyze(FIXTURE["items"], craft_data=CRAFT)

c = report["counts"]
assert c["body"] == {"unid": 1, "id": 1, "total": 2}
assert c["helmet"] == {"unid": 1, "id": 0, "total": 1}
assert c["gloves"] == {"unid": 1, "id": 0, "total": 1}
assert c["boots"] == {"unid": 1, "id": 0, "total": 1}, \
    "ilvl 74 boundary item is eligible"
assert c["belt"] == {"unid": 1, "id": 1, "total": 2}, \
    "ilvl 60 boundary item is eligible"
assert c["amulet"] == {"unid": 1, "id": 0, "total": 1}
assert c["ring"] == {"unid": 1, "id": 1, "total": 2}, \
    "ilvl 59/75 rings and the magic ring must not be counted"
assert c["weapon1h"] == {"unid": 2, "id": 0, "total": 2}
assert c["weapon2h"] == {"unid": 1, "id": 0, "total": 1}
assert c["shield"] == {"unid": 1, "id": 0, "total": 1}
assert c["quiver"] == {"unid": 1, "id": 0, "total": 1}
assert c["unknown"] == {"unid": 1, "id": 0, "total": 1}

assert report["rejected"] == {"not_rare": 2, "ilvl_low": 1, "ilvl_high": 1}
assert report["unknown_bases"] == ["Voidforged Relic"]

# set math: rings gate everything (1 unid + 1 id = 1 pair -> 1 mixed set)
assert report["sets"] == {"total": 1, "all_unid": 0, "value_chaos": 1}
assert report["missing_for_next_set"] == {
    "helmet": 1, "gloves": 1, "boots": 1, "amulet": 1, "ring": 2}
assert report["summary"] == (
    "1 complete set ready (0 all-unid, 1c). "
    "Missing for next set: 1 helmet, 1 gloves, 1 boots, 1 amulet, 2 rings.")

# same fixture through the keyword fallback -> identical classification
report_kw = stashapi.analyze(FIXTURE["items"], craft_data=None)
assert report_kw["counts"] == report["counts"]
assert report_kw["summary"] == report["summary"]

# ------------------------------------------------------------------ renderer
text = stashapi.render(report)
assert report["summary"] in text, "renderer must contain the one-liner"
assert "ring" in text and "amulet" in text and "bottleneck" in text, \
    "jewellery callout is mandatory"
assert "note:" in text, "mixed id/unid sets warn by default"
assert "Voidforged Relic" in text, "unknown bases are reported"
assert "skipped: 2 non-rare, 1 below ilvl 60, 1 above ilvl 74" in text
assert "note:" not in stashapi.render(report, allow_identified=True)

# ------------------------------------------------------- pure set math cases
BODYSET = (["Vaal Regalia", "Hubris Circlet", "Sorcerer Gloves",
            "Titan Greaves", "Leather Belt", "Onyx Amulet",
            "Gold Ring", "Gold Ring"])

# 2H vs dual-1H accounting
one_set_2h = [mk(b) for b in BODYSET + ["Thicket Bow"]]
assert stashapi.analyze(one_set_2h, CRAFT)["sets"]["total"] == 1
one_set_dual = [mk(b) for b in BODYSET + ["Imbued Wand", "Imbued Wand"]]
assert stashapi.analyze(one_set_dual, CRAFT)["sets"]["total"] == 1
one_set_shield = [mk(b) for b in BODYSET + ["Imbued Wand",
                                            "Pinnacle Tower Shield"]]
assert stashapi.analyze(one_set_shield, CRAFT)["sets"]["total"] == 1, \
    "1H + shield completes the weapon requirement"
half_weapon = stashapi.analyze([mk(b) for b in BODYSET + ["Imbued Wand"]],
                               CRAFT)
assert half_weapon["sets"]["total"] == 0
assert half_weapon["missing_for_next_set"] == {"weapon": 1}
assert "Missing for next set: 1 one-handed weapon." in half_weapon["summary"]
quiver_no_weapon = stashapi.analyze(
    [mk(b) for b in BODYSET + ["Penetrating Arrow Quiver"]], CRAFT)
assert quiver_no_weapon["sets"]["total"] == 0, "quivers are NOT required/counted"
assert quiver_no_weapon["missing_for_next_set"] == {"weapon": 2}
# bow leftover cannot split across sets: 1 bow + 1 wand = 1 set, not 1.5
mixed_weapons = stashapi.analyze(
    [mk(b) for b in BODYSET * 2 + ["Thicket Bow", "Imbued Wand"]], CRAFT)
assert mixed_weapons["sets"]["total"] == 1
assert mixed_weapons["missing_for_next_set"]["weapon"] == 1

# the headline example shape: all-unid sets at 2c each
two_sets = [mk(b) for b in
            (["Vaal Regalia", "Hubris Circlet", "Sorcerer Gloves",
              "Titan Greaves", "Onyx Amulet", "Thicket Bow"] * 3
             + ["Leather Belt"] * 2 + ["Gold Ring"] * 4)]
rep2 = stashapi.analyze(two_sets, CRAFT)
assert rep2["sets"] == {"total": 2, "all_unid": 2, "value_chaos": 4}
assert rep2["summary"] == ("2 complete unid sets ready (4c). "
                           "Missing for next set: 1 belt, 2 rings.")

# identified pieces dilute: swap one ring to identified -> mixed set value
one_id = [mk(b) for b in BODYSET[:-1] + ["Thicket Bow"]]
one_id.append(mk("Gold Ring", identified=True))
rep_id = stashapi.analyze(one_id, CRAFT)
assert rep_id["sets"] == {"total": 1, "all_unid": 0, "value_chaos": 1}

# empty tab: everything missing, no crash
empty = stashapi.analyze([], CRAFT)
assert empty["sets"] == {"total": 0, "all_unid": 0, "value_chaos": 0}
assert empty["missing_for_next_set"] == {
    "body": 1, "helmet": 1, "gloves": 1, "boots": 1, "belt": 1,
    "amulet": 1, "ring": 2, "weapon": 2}
assert empty["summary"].startswith("0 complete sets ready.")

# ------------------------------------------------------ fetch: degrade path
assert "POESESSID" not in os.environ
try:
    stashapi.fetch_stash_tab("acct", "Mirage", 0)
    raise AssertionError("expected StashUnavailable without POESESSID")
except stashapi.StashUnavailable as exc:
    msg = str(exc)
    assert "POESESSID" in msg and "never stored" in msg, \
        "degrade message must carry the how-to instructions"

# ------------------------------------------- fetch: injected fetcher, no net
class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, extra_headers=None):
        self.calls.append((url, extra_headers))
        return self.payload


ff = FakeFetcher(FIXTURE)
payload = stashapi.fetch_stash_tab("my account", "Mirage", 2,
                                   poesessid="sess-abc", fetcher=ff)
assert payload is FIXTURE
url, headers = ff.calls[0]
assert url == ("https://www.pathofexile.com/character-window/get-stash-items"
               "?accountName=my%20account&realm=pc&league=Mirage"
               "&tabs=1&tabIndex=2")
assert headers == {"Cookie": "POESESSID=sess-abc"}

# poesessid falls back to the environment variable
os.environ["POESESSID"] = "env-sess"
ff2 = FakeFetcher(FIXTURE)
stashapi.fetch_stash_tab("a", "Mirage", 0, fetcher=ff2)
assert ff2.calls[0][1] == {"Cookie": "POESESSID=env-sess"}
del os.environ["POESESSID"]

# list_tabs: tabIndex=0 catalog request, names come back
ff3 = FakeFetcher(FIXTURE)
tabs = stashapi.list_tabs("a", "Mirage", poesessid="s", fetcher=ff3)
assert "tabs=1&tabIndex=0" in ff3.calls[0][0]
assert [t["n"] for t in tabs] == ["currency", "dump", "CHAOS", "maps"]

# malformed response -> SourceError, not a KeyError
from market.sources import SourceError                  # noqa: E402
try:
    stashapi.fetch_stash_tab("a", "M", 0, poesessid="s",
                             fetcher=FakeFetcher({"error": "nope"}))
    raise AssertionError("expected SourceError on shape mismatch")
except SourceError:
    pass

# ------------------------------------------------------------------ CLI
def run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = chaos_recipe.main(argv, out=out)
    return code, out.getvalue()


code, out = run_cli(["--json-file", FIX])
assert code == 0
assert report["summary"] in out
assert "bottleneck" in out

code, out = run_cli(["--json-file", FIX, "--json"])
assert code == 0
parsed = json.loads(out)
assert parsed["sets"] == {"total": 1, "all_unid": 0, "value_chaos": 1}
assert parsed["missing_for_next_set"]["ring"] == 2

code, out = run_cli(["--json-file", FIX, "--allow-identified"])
assert code == 0 and "note:" not in out

# no POESESSID + no --json-file: instructions and exit 1, no traceback
assert "POESESSID" not in os.environ
code, out = run_cli(["--account", "someone", "--tab", "2"])
assert code == 1
assert "POESESSID" in out and "pathofexile.com" in out

code, out = run_cli(["--json-file", os.path.join(FIX, "nope.json")])
assert code == 1 and "could not read" in out

assert chaos_recipe.default_league() == "Mirage", \
    "league default comes from market/config.json"

if _saved_sessid is not None:                      # restore the caller's env
    os.environ["POESESSID"] = _saved_sessid

print("ALL TESTS PASSED")
print(f"  fixture: {report['sets']['total']} set / "
      f"{report['sets']['value_chaos']}c; "
      f"missing {report['missing_for_next_set']}")
