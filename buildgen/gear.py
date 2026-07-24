#!/usr/bin/env python3
"""Gear upgrade checker: PoB equipped items vs a Ctrl+C candidate.

Pure stdlib logic, import-safe, no network at import or call time —
member_builds only touches the network through pob.read_code, and only
when a member's "pob" is a link that is not already cached on disk
(tests inject `fetch`; raw codes never fetch at all).

Three jobs:

  parse_pob_item(text)   PoB <Item> text block -> dict shaped like
                         overlay/itemtext.py's parse() output (the
                         Ctrl+C parser), so both sides of a comparison
                         speak the same dialect.
  slot_map(root)         decoded PoB XML -> {slot name: parsed item}
                         for the ACTIVE item set.
  compare(cand, worn)    defensive-stat deltas + honest verdict.

PoB item-text format (live-verified against a pobb.in export of the
party's Carry build, 2026-07-24 — see tests/fixtures_gear/):
  - first line "Rarity: RARE" (uppercase; Ctrl+C uses "Rare")
  - name line, then base line for RARE/UNIQUE/RELIC
  - metadata "Key: value" lines: "Item Level: 84", "Quality: 20",
    "Sockets: B-B-B", "LevelReq: 66", "CatalystQuality: 20",
    "Crafted: true", "Prefix:/Suffix: ...", "Energy Shield: 58", ...
  - "Implicits: N" — the next N mod lines are implicits
  - mod lines may be prefixed by {crafted}, {fractured}, {tags:...},
    {range:0.5} groups (chained), and carry unresolved "(lo-hi)" ranges,
    including negative bounds like "(-17-17)" and "+(-1-1)".
"""
from __future__ import annotations

import glob
import json
import os
import re
import xml.etree.ElementTree as ET

import pob
import sources

# ------------------------------------------------------------ PoB item text

_RARITY_MAP = {"NORMAL": "Normal", "MAGIC": "Magic", "RARE": "Rare",
               "UNIQUE": "Unique", "RELIC": "Relic"}

# {crafted}, {fractured}, {tags:...}, {range:0.5}, {custom}, ... prefixes
_BRACE_RE = re.compile(r"\{[^}]*\}")
_RANGE_TAG_RE = re.compile(r"\{range:([\d.]+)\}")
# "(lo-hi)" with optional negative bounds: "(30-40)", "(-17-17)", "(-1-1)"
_PAREN_RANGE_RE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
# metadata "Key: value" lines (PoB keys are word-ish, may contain spaces)
_META_RE = re.compile(r"^[A-Za-z][A-Za-z0-9' ]*: \S")

# colon-less marker lines that are neither metadata nor mods; they may
# appear BEFORE the "Implicits: N" line and must never consume an
# implicit slot. Influence set live-verified against the party's four
# pobb.in exports 2026-07-24; Warlord/Synthesised/Unidentified added
# from overlay/itemtext.py's equivalent list.
_MARKER_LINES = {
    "Corrupted", "Mirrored", "Split", "Unidentified", "Fractured Item",
    "Shaper Item", "Elder Item", "Crusader Item", "Hunter Item",
    "Redeemer Item", "Warlord Item", "Searing Exarch Item",
    "Eater of Worlds Item", "Synthesised Item",
}

# mod-line prefixes that mark a tag we want to keep (parallel to
# itemtext's mod_tags convention: "" = plain explicit)
_KNOWN_TAGS = ("crafted", "fractured", "custom")


def _resolve_ranges(line: str) -> tuple[str, bool]:
    """Strip {...} groups and resolve '(lo-hi)' ranges in a PoB mod line.

    The {range:X} fraction (0..1) picks the roll; without it, midpoint.
    Returns (resolved line, had_any_range).
    """
    m = _RANGE_TAG_RE.search(line)
    frac = float(m.group(1)) if m else 0.5
    frac = min(max(frac, 0.0), 1.0)
    stripped = _BRACE_RE.sub("", line).strip()

    had_range = False

    def _sub(rm: re.Match) -> str:
        nonlocal had_range
        had_range = True
        lo, hi = float(rm.group(1)), float(rm.group(2))
        val = lo + frac * (hi - lo)
        if float(rm.group(1)).is_integer() and float(rm.group(2)).is_integer():
            return str(int(round(val)))
        return f"{val:g}"

    return _PAREN_RANGE_RE.sub(_sub, stripped), had_range


# --- minimal re-derivation of itemtext's props semantics.  We cannot go
# through itemtext.parse() here: its mod-line filter would drop legitimate
# PoB mods ("Blood Magic", stat-stick lines with no digits), and importing
# its private helpers is against the module contract.  Regexes mirror
# overlay/itemtext.py exactly — if that file's semantics change, change
# these too (tests pin the behavior).
_LIFE_RE = re.compile(r"^\+(\d+) to maximum Life$")
_MS_RE = re.compile(r"^(\d+)% increased Movement Speed$")
_RES_RE = re.compile(r"^\+(\d+)% to (Fire|Cold|Lightning|Chaos) Resistance$")
_RES_DUAL_RE = re.compile(
    r"^\+(\d+)% to (Fire|Cold|Lightning|Chaos) and "
    r"(Fire|Cold|Lightning|Chaos) Resistances$")
_ALL_RES_RE = re.compile(r"^\+(\d+)% to all Elemental Resistances$")


def _derive_props(mods: list[str]) -> dict:
    """Same life/movespeed/res semantics as overlay/itemtext.py."""
    life = 0
    movespeed = 0
    res = {"fire": 0, "cold": 0, "lightning": 0, "chaos": 0}
    for mod in mods:
        m = _LIFE_RE.match(mod)
        if m:
            life += int(m.group(1))
            continue
        m = _MS_RE.match(mod)
        if m:
            movespeed += int(m.group(1))
            continue
        m = _RES_RE.match(mod)
        if m:
            res[m.group(2).lower()] += int(m.group(1))
            continue
        m = _RES_DUAL_RE.match(mod)
        if m:
            res[m.group(2).lower()] += int(m.group(1))
            res[m.group(3).lower()] += int(m.group(1))
            continue
        m = _ALL_RES_RE.match(mod)
        if m:
            for k in ("fire", "cold", "lightning"):
                res[k] += int(m.group(1))
    return {"life": life, "movespeed": movespeed, "res": res}


def _parse_sockets(value: str) -> tuple[int, int]:
    """'B-B-R G' -> (total sockets, largest linked group). Mirrors
    overlay/itemtext.py."""
    total, links = 0, 0
    for group in value.split():
        socks = [s for s in group.split("-") if s]
        total += len(socks)
        links = max(links, len(socks))
    return total, links


def parse_pob_item(text) -> dict | None:
    """Parse one PoB <Item> text block -> itemtext-shaped dict, or None.

    Output keys match overlay/itemtext.parse() where both exist:
    item_class ("" — PoB does not record it), rarity ("Rare" not "RARE"),
    name, base, ilvl, req_level, quality, sockets, links, mods, mod_tags,
    props. Extra keys: implicit_mods (kept OUT of `mods` so explicit-mod
    comparison stays clean; still counted into `props`, matching how the
    Ctrl+C parser folds implicits into props), and `estimated`: True when
    any "(lo-hi)" range was resolved (via {range:X} else midpoint).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].lower().startswith("rarity:"):
        return None
    rarity_raw = lines[0].split(":", 1)[1].strip().upper()
    rarity = _RARITY_MAP.get(rarity_raw)
    if rarity is None:
        return None
    lines = lines[1:]
    if not lines:
        return None

    name = lines[0]
    base = name
    body = lines[1:]
    if rarity in ("Rare", "Unique", "Relic") and body \
            and ":" not in body[0] and not body[0].startswith("{"):
        base = body[0]
        body = body[1:]

    parsed = {
        "item_class": "",       # PoB item text carries no Item Class line
        "rarity": rarity,
        "name": name,
        "base": base,
        "ilvl": 0,
        "req_level": 0,
        "quality": 0,
        "sockets": 0,
        "links": 0,
        "mods": [],
        "mod_tags": [],
        "implicit_mods": [],
        "estimated": False,
        "source": "pob",
    }

    n_implicits = 0
    mod_lines: list[str] = []
    seen_implicits_line = False
    for line in body:
        if line in _MARKER_LINES:
            continue
        if line.startswith("Item Level: "):
            m = re.match(r"(\d+)", line[len("Item Level: "):])
            if m:
                parsed["ilvl"] = int(m.group(1))
        elif line.startswith("LevelReq: "):
            m = re.match(r"(\d+)", line[len("LevelReq: "):])
            if m:
                parsed["req_level"] = int(m.group(1))
        elif line.startswith("Quality: "):
            m = re.match(r"\+?(\d+)", line[len("Quality: "):])
            if m:
                parsed["quality"] = int(m.group(1))
        elif line.startswith("Sockets: "):
            total, links = _parse_sockets(line[len("Sockets: "):])
            parsed["sockets"], parsed["links"] = total, links
        elif line.startswith("Implicits: "):
            m = re.match(r"(\d+)", line[len("Implicits: "):])
            if m:
                n_implicits = int(m.group(1))
            seen_implicits_line = True
        elif not seen_implicits_line and _META_RE.match(line):
            # metadata before the Implicits line (Prefix:, Crafted:,
            # Energy Shield:, CatalystQuality:, Unique ID:, ...) — skip.
            # After "Implicits: N" everything is a mod line.
            continue
        else:
            mod_lines.append(line)

    for i, raw in enumerate(mod_lines):
        tag = ""
        for t in _KNOWN_TAGS:
            if "{%s}" % t in raw:
                tag = t
                break
        resolved, had_range = _resolve_ranges(raw)
        if had_range:
            parsed["estimated"] = True
        if not resolved:
            continue
        if i < n_implicits:
            parsed["implicit_mods"].append(resolved)
        else:
            parsed["mods"].append(resolved)
            parsed["mod_tags"].append(tag)

    # props over implicits + explicits, matching the Ctrl+C parser (whose
    # `mods` list includes implicit lines and derives props from all)
    parsed["props"] = _derive_props(parsed["implicit_mods"] + parsed["mods"])
    return parsed


# ------------------------------------------------------------ PoB XML slots

def slot_map(root: ET.Element) -> dict[str, dict]:
    """Decoded PoB XML -> {slot name: parsed item} for the ACTIVE item set.

    Live-verified shape (pobb.in export, 2026-07-24; extract_items in
    buildgen/pob.py assumes the same <Item> text form):
      <Items activeItemSet="1" ...>
        <Item id="26"> ...text... </Item>
        <ItemSet id="1" title="Endgame ...">
          <Slot name="Helmet" itemId="56"/> ... itemId="0" = empty
    activeItemSet matches an ItemSet's `id` attribute (NOT its position:
    the verified export lists set ids 2,3,4,8,6,1,7,5). Sub-slots named
    "... Abyssal Socket N" (jewels socketed inside an item) are skipped.
    Fallbacks: unknown/missing activeItemSet -> first ItemSet; no ItemSet
    elements at all -> every item under a synthetic "(unslotted N)" key
    with parsed["slot"] = None.
    """
    out: dict[str, dict] = {}
    items = root.find("Items")
    if items is None:
        return out
    by_id: dict[str, ET.Element] = {}
    for item in items.findall("Item"):
        iid = item.get("id")
        if iid:
            # itertext(): older PoB versions nest <ModRange> children,
            # splitting the text (same note as pob.extract_items)
            by_id[iid] = item

    sets = items.findall("ItemSet")
    if not sets:
        for iid, item in by_id.items():
            parsed = parse_pob_item("".join(item.itertext()))
            if parsed:
                parsed["slot"] = None
                out[f"(unslotted {iid})"] = parsed
        return out

    active_id = items.get("activeItemSet")
    active = next((s for s in sets if s.get("id") == active_id), sets[0])
    for slot in active.findall("Slot"):
        name = slot.get("name") or ""
        item_id = slot.get("itemId") or "0"
        if not name or "Abyssal Socket" in name or item_id == "0":
            continue
        item = by_id.get(item_id)
        if item is None:
            continue
        parsed = parse_pob_item("".join(item.itertext()))
        if parsed:
            parsed["slot"] = name
            out[name] = parsed
    return out


# ------------------------------------------------------------ slot inference

_ONE_HANDED = {"Wands", "Daggers", "Claws", "One Hand Swords",
               "One Hand Axes", "One Hand Maces", "Sceptres",
               "Rune Daggers", "Thrusting One Hand Swords"}
_TWO_HANDED = {"Two Hand Swords", "Two Hand Axes", "Two Hand Maces",
               "Staves", "Warstaves", "Bows"}
_SIMPLE_SLOTS = {"Helmets": ["Helmet"], "Body Armours": ["Body Armour"],
                 "Gloves": ["Gloves"], "Boots": ["Boots"],
                 "Belts": ["Belt"], "Amulets": ["Amulet"],
                 "Rings": ["Ring 1", "Ring 2"],
                 "Shields": ["Weapon 2"], "Quivers": ["Weapon 2"]}


def infer_slots(parsed_candidate: dict) -> list[str]:
    """Ctrl+C item_class -> PoB slot names to compare against.

    Rings and one-handers map to BOTH candidate slots. Flasks and jewels
    return [] — unsupported in v1 (flask/jewel value isn't a defensive
    stat-line diff; callers should say so politely).
    """
    cls = (parsed_candidate.get("item_class") or "").strip()
    if cls in _SIMPLE_SLOTS:
        return list(_SIMPLE_SLOTS[cls])
    if cls in _ONE_HANDED:
        return ["Weapon 1", "Weapon 2"]
    if cls in _TWO_HANDED:
        return ["Weapon 1"]
    return []


# ------------------------------------------------------------ comparison

# score weights: an honest DEFENSIVE-STAT heuristic, nothing more
_W_LIFE = 1.0
_W_RES = 0.5
_W_MS = 2.0
_W_LINK = 15.0
_SIDEGRADE_BAND = 15.0


def _explicit_mods(parsed: dict) -> list[str]:
    """Explicit (incl. crafted/fractured) mod lines of either dialect.

    PoB-parsed items keep implicits in `implicit_mods`, so `mods` is
    already explicit-only. Ctrl+C-parsed items carry implicits inside
    `mods`, tagged via `mod_tags` — filter those (and enchants) out.
    """
    if "implicit_mods" in parsed:
        return list(parsed.get("mods") or [])
    mods = parsed.get("mods") or []
    tags = parsed.get("mod_tags") or [""] * len(mods)
    return [m for m, t in zip(mods, tags)
            if t not in ("implicit", "enchant")]


def compare(candidate: dict, equipped: dict) -> dict:
    """Diff candidate vs equipped -> deltas, mod churn, score, verdict.

    HONESTY NOTE: the score is a defensive-stat heuristic — life ±1 per
    point, each resistance point ±0.5, movement speed ±2 per %, links
    ±15 per link. It is NOT a PoB DPS/EHP calculation and knows nothing
    about damage mods, attributes, or build context. |score| < 15 is a
    "sidegrade"; treat every verdict as a prompt to look, not a command.
    `estimated` is True when either side's numbers came from unresolved
    PoB ranges (midpoint / {range:X} guesses).
    """
    cp = candidate.get("props") or _derive_props(candidate.get("mods") or [])
    ep = equipped.get("props") or _derive_props(equipped.get("mods") or [])
    d_life = cp["life"] - ep["life"]
    d_ms = cp["movespeed"] - ep["movespeed"]
    d_res = {k: cp["res"][k] - ep["res"][k] for k in cp["res"]}
    d_ele = d_res["fire"] + d_res["cold"] + d_res["lightning"]
    d_links = (candidate.get("links") or 0) - (equipped.get("links") or 0)

    cand_mods = _explicit_mods(candidate)
    worn_mods = _explicit_mods(equipped)
    gained = [m for m in cand_mods if m not in set(worn_mods)]
    lost = [m for m in worn_mods if m not in set(cand_mods)]

    score = (d_life * _W_LIFE
             + sum(d_res.values()) * _W_RES
             + d_ms * _W_MS
             + d_links * _W_LINK)
    if abs(score) < _SIDEGRADE_BAND:
        verdict = "sidegrade"
    elif score > 0:
        verdict = "upgrade"
    else:
        verdict = "downgrade"
    return {
        "life": d_life,
        "movespeed": d_ms,
        "res": d_res,
        "total_ele_res": d_ele,
        "links": d_links,
        "mods_gained": gained,
        "mods_lost": lost,
        "score": round(score, 1),
        "verdict": verdict,
        "estimated": bool(candidate.get("estimated")
                          or equipped.get("estimated")),
    }


def delta_text(diff: dict) -> str:
    """One human line: '+14 life, -12% fire res, +1 link' (nonzero only)."""
    parts = []
    if diff["life"]:
        parts.append(f"{diff['life']:+d} life")
    for k in ("fire", "cold", "lightning", "chaos"):
        if diff["res"][k]:
            parts.append(f"{diff['res'][k]:+d}% {k} res")
    if diff["movespeed"]:
        parts.append(f"{diff['movespeed']:+d}% move speed")
    if diff["links"]:
        parts.append(f"{diff['links']:+d} link{'s' if abs(diff['links']) != 1 else ''}")
    return ", ".join(parts) if parts else "no tracked stat changes"


# ------------------------------------------------------------ party builds

def newest_bundle(builds_dir: str) -> str | None:
    """Path of the most recently modified builds/*/party_bundle.json."""
    paths = glob.glob(os.path.join(builds_dir, "*", "party_bundle.json"))
    return max(paths, key=os.path.getmtime) if paths else None


def member_builds(bundle_path: str, fetch=None, cache=True, refresh=False):
    """Load a party bundle -> per-member equipped-slot maps.

    For each member, member["pob"] (raw code or supported build link) is
    resolved via pob.read_code (fetch injectable for tests) and decoded;
    the decoded XML is cached as <Player>.pobxml next to the bundle so
    later runs are fully offline. refresh=True ignores and rewrites the
    cache. Raw-code "pob" values never touch the network at all.

    Returns a list of {"player", "class", "role", "me", "slots"} dicts;
    a member whose PoB cannot be fetched/decoded yields
    {"player", "error"} instead of crashing the whole party.
    """
    with open(bundle_path, encoding="utf-8") as f:
        bundle = json.load(f)
    bundle_dir = os.path.dirname(os.path.abspath(bundle_path))
    out = []
    for m in bundle.get("members", []):
        player = m.get("player") or "?"
        cache_path = os.path.join(bundle_dir, f"{player}.pobxml")
        root = None
        if cache and not refresh and os.path.exists(cache_path):
            try:
                root = ET.parse(cache_path).getroot()
            except ET.ParseError:
                root = None       # corrupt cache -> refetch below
        if root is None:
            try:
                code = pob.read_code(m.get("pob") or "", fetch=fetch)
                root = pob.decode(code)
            except (sources.SourceError, *pob.DECODE_ERRORS) as e:
                out.append({"player": player, "error": str(e)})
                continue
            if cache:
                try:
                    ET.ElementTree(root).write(cache_path, encoding="utf-8")
                except OSError:
                    pass          # cache is an optimization, not a must
        out.append({
            "player": player,
            "class": m.get("class") or pob.build_info(root)["class"],
            "role": m.get("role") or "",
            "me": bool(m.get("me")),
            "slots": slot_map(root),
        })
    return out
