"""Headless tests: gear upgrade checker (PoB item parsing, slot maps,
candidate comparison, offline party-bundle loading, CLI smoke)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay"), os.path.join(ROOT, "buildgen")]

import gear                                    # noqa: E402
import itemtext                                # noqa: E402
import pob                                     # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_gear")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


pob_root = ET.parse(os.path.join(FIX, "pob_build.xml")).getroot()

# ---------------------------------------------------- parse_pob_item
helmet_text = "".join(next(
    it for it in pob_root.find("Items").findall("Item")
    if it.get("id") == "1").itertext())
h = gear.parse_pob_item(helmet_text)
assert h["rarity"] == "Rare" and h["name"] == "Doom Halo"
assert h["base"] == "Iron Hat"
assert h["ilvl"] == 70 and h["req_level"] == 60 and h["quality"] == 20
assert h["sockets"] == 4 and h["links"] == 4
# "Implicits: 1" -> exactly one implicit, kept OUT of mods
assert h["implicit_mods"] == ["+1 to Level of Socketed Minion Gems"]
# "(30-50)" with no {range:} tag -> midpoint 40, flagged estimated
assert h["mods"] == ["+40 to maximum Life", "+25% to Fire Resistance",
                     "+30% to Cold Resistance"]
assert h["estimated"] is True
assert h["mod_tags"] == ["", "", "crafted"], "crafted tag survives"
assert h["props"] == {"life": 40, "movespeed": 0,
                      "res": {"fire": 25, "cold": 30, "lightning": 0,
                              "chaos": 0}}

# {range:0.5} resolves (40-60) -> 50; implicit (8-12) -> midpoint 10
w = gear.parse_pob_item(
    "Rarity: RARE\nStorm Song\nCarved Wand\nItem Level: 50\nLevelReq: 35\n"
    "Implicits: 1\n(8-12)% increased Spell Damage\n"
    "{tags:caster_damage,damage,caster}{range:0.5}"
    "(40-60)% increased Spell Damage\n+35 to maximum Life\n")
assert w["implicit_mods"] == ["10% increased Spell Damage"]
assert w["mods"] == ["50% increased Spell Damage", "+35 to maximum Life"]
assert w["estimated"] is True

# {range:X} at other fractions + negative range bounds (both observed in
# a live pobb.in export: "(-17-17)" and "+(-1-1)")
neg = gear.parse_pob_item(
    "Rarity: UNIQUE\nTest Rod\nImperial Staff\nImplicits: 0\n"
    "{range:1}+(-1-1) to Level of all Spell Skill Gems\n"
    "{range:0.195}(-17-17)% increased maximum Life\n")
assert neg["mods"][0] == "+1 to Level of all Spell Skill Gems"
assert neg["mods"][1] == "-10% increased maximum Life"   # -17 + 0.195*34

# an item with no ranges is NOT estimated; metadata lines (Prefix:,
# Crafted:, Energy Shield:) before the Implicits line are skipped
plain = gear.parse_pob_item(
    "Rarity: RARE\nGloom Band\nIron Ring\nEnergy Shield: 12\n"
    "Crafted: true\nPrefix: {range:0.3}SomeModId\nItem Level: 60\n"
    "Implicits: 1\nAdds 1 to 4 Physical Damage to Attacks\n"
    "+60 to maximum Life\n+35% to Fire Resistance\n")
assert plain["estimated"] is False
assert plain["implicit_mods"] == ["Adds 1 to 4 Physical Damage to Attacks"]
assert plain["mods"] == ["+60 to maximum Life", "+35% to Fire Resistance"]

# implicits count into props (parity with itemtext, whose mods include
# implicit lines): ring1 = +25 implicit life (midpoint) + 45 explicit
r1 = gear.parse_pob_item(
    "Rarity: RARE\nLoop of Storms\nCoral Ring\nImplicits: 1\n"
    "+(20-30) to maximum Life\n+45 to maximum Life\n"
    "+18% to Lightning Resistance\n")
assert r1["props"]["life"] == 70
assert r1["props"]["res"]["lightning"] == 18

# influence/corruption marker lines (live-verified: they sit BEFORE the
# Implicits line in real exports) are neither metadata nor mods, and
# must not consume an implicit slot
inf = gear.parse_pob_item(
    "Rarity: RARE\nEndgame\nLich's Circlet\nEnergy Shield: 372\n"
    "Unique ID: c9f6c60b\nHunter Item\nItem Level: 86\nQuality: 27\n"
    "Implicits: 1\n+12% to Fire Resistance\nCorrupted\n"
    "+60 to Intelligence\n+26 to maximum Life\n")
assert inf["implicit_mods"] == ["+12% to Fire Resistance"]
assert inf["mods"] == ["+60 to Intelligence", "+26 to maximum Life"]
assert "Hunter Item" not in inf["mods"] and "Corrupted" not in inf["mods"]

# garbage in -> None, never a raise
assert gear.parse_pob_item("") is None
assert gear.parse_pob_item(None) is None
assert gear.parse_pob_item("not an item\nat all") is None
assert gear.parse_pob_item("Rarity: WEIRD\nX\n") is None

# ---------------------------------------------------- slot_map
slots = gear.slot_map(pob_root)
assert set(slots) == {"Helmet", "Body Armour", "Ring 1", "Ring 2",
                      "Weapon 1"}, f"got {set(slots)}"
# activeItemSet="1" matches ItemSet id="1" even though the decoy set
# (id="2", Decoy Cap) comes first in document order
assert slots["Helmet"]["name"] == "Doom Halo", "must pick the ACTIVE set"
assert slots["Helmet"]["slot"] == "Helmet"
# itemId="0" slots (Weapon 2, Gloves) and Abyssal Socket sub-slots absent
assert "Weapon 2" not in slots and "Gloves" not in slots

# fallback: no ItemSet elements -> unslotted items, slot=None
loose = ET.Element("PathOfBuilding")
li = ET.SubElement(loose, "Items")
el = ET.SubElement(li, "Item", {"id": "7"})
el.text = "Rarity: RARE\nLone Hat\nIron Hat\nImplicits: 0\n+10 to maximum Life\n"
lmap = gear.slot_map(loose)
assert list(lmap) == ["(unslotted 7)"]
assert lmap["(unslotted 7)"]["slot"] is None
assert gear.slot_map(ET.Element("PathOfBuilding")) == {}

# ---------------------------------------------------- infer_slots
cand_helmet = itemtext.parse(read("candidate_helmet.txt"))
cand_ring = itemtext.parse(read("candidate_ring.txt"))
cand_wand = itemtext.parse(read("candidate_wand.txt"))
assert cand_helmet and cand_ring and cand_wand
assert gear.infer_slots(cand_helmet) == ["Helmet"]
assert gear.infer_slots(cand_ring) == ["Ring 1", "Ring 2"]
assert gear.infer_slots(cand_wand) == ["Weapon 1", "Weapon 2"]
assert gear.infer_slots({"item_class": "Two Hand Axes"}) == ["Weapon 1"]
assert gear.infer_slots({"item_class": "Shields"}) == ["Weapon 2"]
assert gear.infer_slots({"item_class": "Life Flasks"}) == []
assert gear.infer_slots({"item_class": "Jewels"}) == []
assert gear.infer_slots({}) == []

# ---------------------------------------------------- compare
# helmet: cand life 54/fire 13/cold 42 vs worn life 40/fire 25/cold 30
d = gear.compare(cand_helmet, slots["Helmet"])
assert d["life"] == 14 and d["movespeed"] == 0 and d["links"] == 0
assert d["res"] == {"fire": -12, "cold": 12, "lightning": 0, "chaos": 0}
assert d["total_ele_res"] == 0
assert d["score"] == 14.0 and d["verdict"] == "sidegrade"
assert d["estimated"] is True, "worn helmet had a resolved range"
assert set(d["mods_lost"]) == {"+40 to maximum Life",
                               "+25% to Fire Resistance",
                               "+30% to Cold Resistance"}
assert "+54 to maximum Life" in d["mods_gained"]
assert "+1 to Level of Socketed Minion Gems" not in d["mods_gained"], \
    "implicits must not pollute the explicit-mod diff"
assert gear.delta_text(d) == "+14 life, -12% fire res, +12% cold res"

# ring candidate is compared against BOTH ring slots
ring_diffs = {s: gear.compare(cand_ring, slots[s])
              for s in gear.infer_slots(cand_ring)}
d1, d2 = ring_diffs["Ring 1"], ring_diffs["Ring 2"]
# vs Ring 1 (life 70 incl. estimated implicit, light 18):
assert d1["life"] == -2
assert d1["res"] == {"fire": 42, "cold": 12, "lightning": -3, "chaos": 0}
assert d1["score"] == 23.5 and d1["verdict"] == "upgrade"
assert d1["estimated"] is True
# vs Ring 2 (life 60, fire 35, chaos 10):
assert d2["life"] == 8
assert d2["res"] == {"fire": 7, "cold": 12, "lightning": 15, "chaos": -10}
assert d2["total_ele_res"] == 34
assert d2["score"] == 20.0 and d2["verdict"] == "upgrade"
assert d2["estimated"] is False, "neither side had ranges"

# wand vs Weapon 1: +13 life, +22 cold -> 24 -> upgrade
dw = gear.compare(cand_wand, slots["Weapon 1"])
assert dw["life"] == 13 and dw["res"]["cold"] == 22
assert dw["score"] == 24.0 and dw["verdict"] == "upgrade"

# downgrade + link weighting: strictly worse candidate vs 6L body
worse = {"props": {"life": 0, "movespeed": 0,
                   "res": {"fire": 0, "cold": 0, "lightning": 0, "chaos": 0}},
         "links": 3, "mods": [], "mod_tags": []}
db = gear.compare(worse, slots["Body Armour"])
assert db["links"] == -3
assert db["score"] == -80 - 15 - 45.0 and db["verdict"] == "downgrade"

# ---------------------------------------------------- member_builds (offline)
tmp = tempfile.mkdtemp(prefix="poe_gear_test_")
try:
    code = pob.encode(pob_root)
    bundle_path = os.path.join(tmp, "party_bundle.json")
    bundle = {"league": "3.29", "members": [
        {"player": "Carry", "role": "Spark carry", "class": "Templar",
         "pob": code},
        {"player": "Aurabot", "role": "Aura support", "pob": code,
         "me": True},
    ]}
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)

    boom = lambda url: (_ for _ in ()).throw(  # noqa: E731
        AssertionError(f"network fetch attempted: {url}"))
    members = gear.member_builds(bundle_path, fetch=boom)
    assert [m["player"] for m in members] == ["Carry", "Aurabot"]
    assert members[0]["class"] == "Templar", "bundle class wins"
    assert members[1]["class"] == "Witch", "falls back to PoB build_info"
    assert members[1]["me"] is True
    for m in members:
        assert m["slots"]["Helmet"]["name"] == "Doom Halo"
    # decoded XML cached next to the bundle for offline reruns
    assert os.path.exists(os.path.join(tmp, "Carry.pobxml"))
    assert os.path.exists(os.path.join(tmp, "Aurabot.pobxml"))

    # cache really is used: break the bundle's pob values, rerun fine
    bundle["members"][0]["pob"] = "!!definitely-not-a-code!!"
    bundle["members"][1]["pob"] = "!!definitely-not-a-code!!"
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    members2 = gear.member_builds(bundle_path, fetch=boom)
    assert members2[0]["slots"]["Ring 1"]["name"] == "Loop of Storms"

    # refresh=True bypasses the cache -> the broken code now surfaces
    # as a per-member error, not a crash
    members3 = gear.member_builds(bundle_path, fetch=boom, refresh=True)
    assert all("error" in m for m in members3), members3

    # ------------------------------------------------ CLI smoke (offline)
    # restore good codes + cache for the CLI run
    bundle["members"][0]["pob"] = code
    bundle["members"][1]["pob"] = code
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    env = dict(os.environ, POE_TOOLS_LLM="off", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "upgrade_check.py"),
         "--bundle", bundle_path, "--no-llm", "--file",
         os.path.join(FIX, "candidate_wand.txt")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Gale Spire" in out and "Storm Song" in out
    assert "Weapon 2: empty slot — anything is an upgrade" in out
    assert "UPGRADE" in out and "not a PoB DPS calc" in out

    # ring candidate: both slots shown, --member filter works
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "upgrade_check.py"),
         "--bundle", bundle_path, "--no-llm", "--member", "carry",
         "--json", "--file", os.path.join(FIX, "candidate_ring.txt")],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert [m["player"] for m in payload["members"]] == ["Carry"]
    rows = payload["members"][0]["rows"]
    assert [r["slot"] for r in rows] == ["Ring 1", "Ring 2"]
    assert rows[0]["equipped"] == "Loop of Storms"
    assert rows[1]["diff"]["verdict"] == "upgrade"

    # unsupported class degrades politely
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "upgrade_check.py"),
         "--bundle", bundle_path, "--no-llm", "--file", "-"],
        input="Item Class: Life Flasks\nRarity: Magic\n"
              "Bubbling Divine Life Flask of Staunching\n--------\n"
              "Item Level: 60\n--------\n",
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "isn't supported" in proc.stdout
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  helmet delta: {gear.delta_text(d)} -> {d['verdict']}")
print(f"  ring vs both slots: {d1['score']:+g} / {d2['score']:+g}")
