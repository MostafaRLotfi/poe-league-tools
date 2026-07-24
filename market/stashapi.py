"""Legacy stash-tab API reader + chaos-recipe set math.

Reads ONE stash tab (the chaos-recipe dump tab) via the official legacy
character-window endpoint and computes exactly which slots are still
missing for the next full rare set. Read-only GETs against the official
site with the mandatory User-Agent; auth is the player's own POESESSID
session cookie, read from the environment only and never written to disk.
Without it everything raises StashUnavailable with instructions — callers
degrade politely (mirrors market/livesearch.py).

Endpoint (community-documented, NOT live-verified here — the legacy
character-window API has no public docs):

    GET https://www.pathofexile.com/character-window/get-stash-items
        ?accountName=<acct>&realm=pc&league=<league>&tabs=1&tabIndex=<n>
        Cookie: POESESSID=<value>

    VERIFY: at 3.29 launch, confirm against a live account that the
    response still looks like
        {"numTabs": N,
         "tabs": [{"n": <name>, "i": <index>, ...}, ...],   # tabs=1 only
         "items": [{"typeLine": ..., "baseType": ..., "ilvl": ...,
                    "identified": bool, "frameType": int,
                    "w": int, "h": int, ...}, ...]}
    (frameType 2 = rare; older responses may lack "baseType" — we fall
    back to "typeLine"). GGG throttles this endpoint aggressively and it
    can change without notice; the shared global 1-request/2-s floor from
    market.sources applies to every call, and 429/Retry-After is honored
    by the shared RateLimitedFetcher. Keep polling manual — one fetch per
    keypress, never a loop.

Chaos recipe (the set math is pure and unit-tested offline):
one set = body + helmet + gloves + boots + belt + amulet + 2 rings +
(one two-handed weapon OR two one-hand-equivalents). Bows count as
two-handed; a shield counts as a one-hand-equivalent (the vendor accepts
1H + shield); quivers are classified but NOT required. Every item is rare
(frameType 2) with item level 60-74 inclusive. A full-unidentified set
vendors for 2 chaos, a set containing identified pieces for 1.

Import-safe: no network, no side effects at import time.
"""
from __future__ import annotations

import os
import urllib.parse

from market.sources import RateLimitedFetcher, SourceError

SITE = "https://www.pathofexile.com"
STASH_ENDPOINT = f"{SITE}/character-window/get-stash-items"

ILVL_MIN = 60
ILVL_MAX = 74
FRAME_RARE = 2
UNID_SET_CHAOS = 2
MIXED_SET_CHAOS = 1

# Slots a set needs one of (rings/weapons handled separately below).
SINGLE_SLOTS = ("body", "helmet", "gloves", "boots", "belt", "amulet")
# Every bucket analyze() tracks (order = render order).
ALL_SLOTS = SINGLE_SLOTS + ("ring", "weapon1h", "weapon2h", "shield",
                            "quiver", "unknown")
# Shopping-list order (weapon = 1H-equivalents; a 2H covers 2).
MISSING_ORDER = SINGLE_SLOTS + ("ring", "weapon")


class StashUnavailable(RuntimeError):
    """No session cookie: the caller prints this and degrades."""


_POESESSID_HELP = (
    "stash access needs your logged-in session cookie:\n"
    "  1. log in at pathofexile.com in your browser\n"
    "  2. dev tools -> Application/Storage -> Cookies -> "
    "https://www.pathofexile.com -> copy the POESESSID value\n"
    "  3. export POESESSID=<value>   (PC: set POESESSID=<value>)\n"
    "the cookie is read from the environment only and never stored; "
    "offline, pass a saved API response via --json-file instead")


def _session_id(poesessid) -> str:
    sid = poesessid if poesessid is not None else os.environ.get("POESESSID")
    sid = (sid or "").strip()
    if not sid:
        raise StashUnavailable(_POESESSID_HELP)
    return sid


def stash_url(account: str, league: str, tab_index: int) -> str:
    query = urllib.parse.urlencode({
        "accountName": account, "realm": "pc", "league": league,
        "tabs": 1, "tabIndex": tab_index,
    }, quote_via=urllib.parse.quote)
    return f"{STASH_ENDPOINT}?{query}"


def fetch_stash_tab(account: str, league: str, tab_index: int, *,
                    poesessid=None, fetcher=None) -> dict:
    """GET one stash tab (see module docstring for the expected shape).

    poesessid defaults to the POESESSID environment variable; missing ->
    StashUnavailable with instructions. The fetcher is the shared
    market.sources.RateLimitedFetcher (global 2 s floor, 429 honored).
    """
    sid = _session_id(poesessid)
    fetcher = fetcher if fetcher is not None else RateLimitedFetcher()
    url = stash_url(account, league, tab_index)
    payload = fetcher.get_json(url, extra_headers={"Cookie": f"POESESSID={sid}"})
    if not isinstance(payload, dict) or "items" not in payload:
        raise SourceError(
            "get-stash-items: unexpected shape (no 'items'; the legacy "
            "endpoint may have changed — see VERIFY note in "
            "market/stashapi.py)")
    return payload


def list_tabs(account: str, league: str, *,
              poesessid=None, fetcher=None) -> list[dict]:
    """Tab catalog [{"n": name, "i": index, ...}] (tabs=1, tabIndex=0)."""
    payload = fetch_stash_tab(account, league, 0,
                              poesessid=poesessid, fetcher=fetcher)
    tabs = payload.get("tabs")
    if not isinstance(tabs, list):
        raise SourceError("get-stash-items: no 'tabs' list in response")
    return tabs


# ------------------------------------------------------- slot classification
# Dataset item classes (data/repoe_craft.json bases[<name>]["cls"]) -> slot.
_CLS_TO_SLOT = {
    "Body Armour": "body", "Helmet": "helmet", "Gloves": "gloves",
    "Boots": "boots", "Belt": "belt", "Amulet": "amulet", "Ring": "ring",
    "Shield": "shield", "Quiver": "quiver",
    # one-handed
    "Claw": "weapon1h", "Dagger": "weapon1h", "Rune Dagger": "weapon1h",
    "Wand": "weapon1h", "Sceptre": "weapon1h",
    "One Hand Sword": "weapon1h", "Thrusting One Hand Sword": "weapon1h",
    "One Hand Axe": "weapon1h", "One Hand Mace": "weapon1h",
    # two-handed (bows count as 2H for the recipe)
    "Bow": "weapon2h", "Staff": "weapon2h", "Warstaff": "weapon2h",
    "Two Hand Sword": "weapon2h", "Two Hand Axe": "weapon2h",
    "Two Hand Mace": "weapon2h",
}

# Keyword fallback, first match wins; matching is on whole words of the
# base name so "Full Ringmail" (body) never matches the "Ring" rule.
_KEYWORD_RULES = (
    ("amulet", ("Amulet", "Talisman")),
    ("ring", ("Ring",)),
    ("belt", ("Belt", "Sash", "Vise")),
    ("quiver", ("Quiver",)),
    ("shield", ("Shield", "Buckler", "Bundle")),
    ("helmet", ("Helmet", "Helm", "Hat", "Cap", "Hood", "Burgonet",
                "Bascinet", "Sallet", "Circlet", "Crown", "Mask", "Pelt",
                "Coif", "Casque", "Tricorne")),
    ("gloves", ("Gloves", "Gauntlets", "Mitts", "Mitten")),
    ("boots", ("Boots", "Greaves", "Slippers", "Shoes")),
    ("weapon2h", ("Bow", "Staff", "Stave", "Warstaff", "Quarterstaff",
                  "Maul", "Greatsword", "Sledge", "Poleaxe", "Halberd",
                  "Labrys", "Woodsplitter", "Lathi")),
    ("weapon1h", ("Wand", "Dagger", "Claw", "Sceptre", "Kris", "Skean",
                  "Stiletto", "Knife", "Rapier", "Foil", "Sabre",
                  "Cutlass", "Sword", "Blade", "Axe", "Mace", "Club",
                  "Hammer", "Flail", "Fist", "Paw", "Talons", "Ripper")),
    ("body", ("Vest", "Robe", "Regalia", "Garb", "Tunic", "Plate",
              "Chestplate", "Brigandine", "Doublet", "Hauberk", "Jacket",
              "Leather", "Ringmail", "Chainmail", "Wyrmscale",
              "Dragonscale", "Coat", "Vestment", "Lamellar", "Jerkin",
              "Raiment", "Silks", "Wrap", "Jack", "Armour")),
)


def _keyword_slot(text: str) -> str:
    words = set((text or "").replace("-", " ").split())
    for slot, keys in _KEYWORD_RULES:
        if words.intersection(keys):
            return slot
    return "unknown"


def classify_slot(item: dict, craft_data=None) -> str:
    """Recipe slot for one API item dict; 'unknown' rather than crash.

    Tries the compiled RePoE base->class lookup (craft.pool.CraftData)
    first when given, then falls back to keyword matching on the base
    name. craft_data anything without usable .find_base/.bases is
    skipped cleanly.
    """
    base = str(item.get("baseType") or item.get("typeLine") or "")
    if craft_data is not None:
        try:
            name = (craft_data.find_base(base)
                    or craft_data.find_base(str(item.get("typeLine") or "")))
            if name:
                cls = craft_data.bases[name].get("cls", "")
                slot = _CLS_TO_SLOT.get(cls)
                if slot:
                    return slot
        except (AttributeError, KeyError, TypeError):
            pass                          # dataset can't classify: fall back
    return _keyword_slot(base or str(item.get("typeLine") or ""))


# ------------------------------------------------------------------ set math

def _weapon_points(counts) -> int:
    """One-hand-equivalents: 2H = 2, 1H = 1, shield = 1 (2 per set)."""
    return (2 * counts["weapon2h"] + counts["weapon1h"] + counts["shield"])


def _capacity(counts) -> int:
    """Complete sets these per-slot counts can fill."""
    caps = [counts[s] for s in SINGLE_SLOTS]
    caps.append(counts["ring"] // 2)
    caps.append(_weapon_points(counts) // 2)
    return max(0, min(caps))


def _shopping_list(counts, target: int) -> dict:
    """slot -> shortage to reach `target` sets (weapon in 1H-equivalents)."""
    missing = {}
    for slot in SINGLE_SLOTS:
        short = target - counts[slot]
        if short > 0:
            missing[slot] = short
    short = 2 * target - counts["ring"]
    if short > 0:
        missing["ring"] = short
    short = 2 * target - _weapon_points(counts)
    if short > 0:
        missing["weapon"] = short
    return missing


def analyze(items, craft_data=None) -> dict:
    """Pure: API item dicts -> chaos-recipe report (JSON-ready).

    {"counts": {slot: {"unid", "id", "total"}},
     "rejected": {"not_rare", "ilvl_low", "ilvl_high"},
     "unknown_bases": [...],
     "sets": {"total", "all_unid", "value_chaos"},
     "missing_for_next_set": {slot: n},   # weapon = 1H-equivalents
     "summary": one-line string}
    """
    counts = {slot: {"unid": 0, "id": 0} for slot in ALL_SLOTS}
    rejected = {"not_rare": 0, "ilvl_low": 0, "ilvl_high": 0}
    unknown_bases = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("frameType") != FRAME_RARE:
            rejected["not_rare"] += 1
            continue
        try:
            ilvl = int(item.get("ilvl") or 0)
        except (TypeError, ValueError):
            ilvl = 0
        if ilvl < ILVL_MIN:
            rejected["ilvl_low"] += 1
            continue
        if ilvl > ILVL_MAX:
            rejected["ilvl_high"] += 1
            continue
        slot = classify_slot(item, craft_data)
        if slot == "unknown":
            unknown_bases.append(
                str(item.get("baseType") or item.get("typeLine") or "?"))
        counts[slot]["id" if item.get("identified") else "unid"] += 1

    totals = {s: counts[s]["unid"] + counts[s]["id"] for s in ALL_SLOTS}
    unid_only = {s: counts[s]["unid"] for s in ALL_SLOTS}
    sets_total = _capacity(totals)
    sets_unid = min(_capacity(unid_only), sets_total)
    value = (sets_unid * UNID_SET_CHAOS
             + (sets_total - sets_unid) * MIXED_SET_CHAOS)
    missing = _shopping_list(totals, sets_total + 1)

    report = {
        "counts": {s: {**counts[s], "total": totals[s]} for s in ALL_SLOTS},
        "rejected": rejected,
        "unknown_bases": unknown_bases,
        "sets": {"total": sets_total, "all_unid": sets_unid,
                 "value_chaos": value},
        "missing_for_next_set": missing,
    }
    report["summary"] = summary_line(report)
    return report


# ------------------------------------------------------------------ renderer
_MISSING_LABELS = {          # (singular, plural)
    "body": ("body armour", "body armours"),
    "helmet": ("helmet", "helmets"),
    "gloves": ("gloves", "gloves"),
    "boots": ("boots", "boots"),
    "belt": ("belt", "belts"),
    "amulet": ("amulet", "amulets"),
    "ring": ("ring", "rings"),
    "weapon": ("one-handed weapon", "one-handed weapons (a 2H covers 2)"),
}
_ROW_LABELS = {
    "body": "body", "helmet": "helmet", "gloves": "gloves",
    "boots": "boots", "belt": "belt", "amulet": "amulet", "ring": "rings",
    "weapon1h": "1H weapons", "weapon2h": "2H weapons",
    "shield": "shields", "quiver": "quivers (not needed)",
    "unknown": "unknown",
}


def _missing_text(missing: dict) -> str:
    parts = []
    for slot in MISSING_ORDER:
        n = missing.get(slot, 0)
        if n > 0:
            parts.append(f"{n} {_MISSING_LABELS[slot][0 if n == 1 else 1]}")
    return ", ".join(parts)


def summary_line(report: dict) -> str:
    """The one-liner: '2 complete unid sets ready (4c). Missing ...'."""
    sets = report["sets"]
    total, unid, value = sets["total"], sets["all_unid"], sets["value_chaos"]
    plural = "" if total == 1 else "s"
    if total == 0:
        head = "0 complete sets ready."
    elif unid == total:
        head = f"{total} complete unid set{plural} ready ({value}c)."
    else:
        head = (f"{total} complete set{plural} ready "
                f"({unid} all-unid, {value}c).")
    missing = _missing_text(report["missing_for_next_set"])
    if missing:
        return f"{head} Missing for next set: {missing}."
    return f"{head} Next set: all slots covered."


def render(report: dict, allow_identified: bool = False) -> str:
    """Compact terminal table + summary + the jewellery callout."""
    lines = ["chaos recipe — dump tab (rares, ilvl 60-74)",
             f"  {'slot':<20}{'unid':>6}{'id':>5}{'total':>7}"]
    for slot in ALL_SLOTS:
        c = report["counts"][slot]
        if slot in ("quiver", "unknown") and c["total"] == 0:
            continue
        lines.append(f"  {_ROW_LABELS[slot]:<20}"
                     f"{c['unid']:>6}{c['id']:>5}{c['total']:>7}")
    rej = report["rejected"]
    skipped = rej["not_rare"] + rej["ilvl_low"] + rej["ilvl_high"]
    if skipped:
        lines.append(f"  skipped: {rej['not_rare']} non-rare, "
                     f"{rej['ilvl_low']} below ilvl {ILVL_MIN}, "
                     f"{rej['ilvl_high']} above ilvl {ILVL_MAX}")
    if report["unknown_bases"]:
        lines.append("  unclassified (not counted): "
                     + ", ".join(sorted(set(report["unknown_bases"]))))
    lines.append("")
    lines.append(report["summary"])
    ring_amu = (report["counts"]["ring"]["total"]
                + report["counts"]["amulet"]["total"])
    lines.append(f"jewellery is the classic bottleneck (must be ilvl {ILVL_MIN}+):"
                 f" grab every rare ring and amulet you see"
                 f" ({ring_amu} banked).")
    sets = report["sets"]
    if not allow_identified and sets["total"] > sets["all_unid"]:
        lines.append("note: some sets need identified pieces — a full-unid "
                     "set vendors for 2c, a mixed set only 1c "
                     "(--allow-identified hides this note).")
    return "\n".join(lines)
