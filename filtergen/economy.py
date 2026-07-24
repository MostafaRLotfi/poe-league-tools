"""Economy block logic: snapshot rows -> tiered loot-filter Show rules.

Consumes snapshot rows in the repo's standard shape (docs/INTERFACES.md,
"Market DB"): ``{ts, source, league, item, buy, sell, buy_vol, sell_vol,
raw}``. The price of a row is ``sell`` if present, else ``buy`` (chaos).

PoE loot filters are first-match-wins, so a rule block spliced at the very
top of a filter overrides the user's base filter (NeverSink etc.) for
exactly the listed BaseTypes and falls through for everything else.

Scope (v1): currencies, fragments, scarabs, essences, divination cards —
things whose *BaseType* is the priced name. Unique items are out of scope:
filters cannot match unique names, only base types, so rows that came from
stash item overviews (UniqueWeapon-style snapshots; ``raw`` carries
``chaosValue``/``listingCount``/``baseType``) are skipped, as are the
"<name> <links>L" link-disambiguated variants and "a->b" pair rows.

Filter-syntax notes (poewiki "Item filter" guide):
  * ``BaseType == "Name" ...`` — the ``==`` operator requires an exact
    full-name match (no substring surprises); values are quoted.
  * Divination cards match their card name as BaseType; their rules also
    carry ``Class == "Divination Cards"`` as a collision guard.
    VERIFY: the class name is the plural "Divination Cards" (as in the
    game's advanced item descriptions and NeverSink's filters) — confirm
    in-game at the 3.29 rehearsal if a card rule ever fails to match.

Pure stdlib, import-safe, no IO except what callers do themselves.
"""
from __future__ import annotations

import json
import re

# Marker lines for the spliced region. The begin line carries a variable
# suffix (timestamp + league), so it is matched by prefix; the end line is
# matched exactly.
BLOCK_BEGIN_PREFIX = "# >>> poe-league-tools economy block"
BLOCK_END_MARKER = "# <<< poe-league-tools economy block"
_DO_NOT_EDIT = ("# >>> do not edit inside this block; "
                "regenerate with tools/filter_update.py")

CARD_CLASS_CONDITION = 'Class == "Divination Cards"'

_LINKS_SUFFIX_RE = re.compile(r" \d+L$")   # "<unique name> 6L" (sources.py)


# --------------------------------------------------------------- row logic
def price_of(row) -> float | None:
    """Chaos price of a snapshot row: sell if present, else buy."""
    value = row.get("sell")
    if value is None:
        value = row.get("buy")
    return float(value) if value is not None else None


def _raw_dict(row) -> dict:
    """Decode the row's ``raw`` column (JSON text or dict) to a dict."""
    raw = row.get("raw")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _classify(row) -> str | None:
    """Group for a row: "base", "card", or None (excluded from the block).

    Card detection is layered: an explicit ``category`` key on the row
    (extra keys are allowed on snapshot rows; tools/filter_update.py tags
    live DivinationCard fetches) wins; otherwise the raw payload shape
    narrows the source endpoint, and within exchange rows (which serve
    exactly Scarab | Essence | DivinationCard in this repo) the name
    decides — scarabs all contain "Scarab", essences contain "Essence"
    (plus "Remnant of Corruption"), everything else is a card.
    """
    name = row.get("item")
    if not name or "->" in name:           # pair rows are not BaseTypes
        return None
    if _LINKS_SUFFIX_RE.search(name):      # links-disambiguated variant
        return None
    raw = _raw_dict(row)
    if "chaosValue" in raw or "listingCount" in raw or "baseType" in raw:
        return None                        # stash item overview (uniques)
    category = row.get("category")
    if category:
        lowered = str(category).lower()
        return "card" if ("card" in lowered or "divination" in lowered) \
            else "base"
    if "currencyTypeName" in raw:          # currency/fragment overview
        return "base"
    if "primaryValue" in raw:              # exchange overview
        if ("Scarab" in name or "Essence" in name
                or name == "Remnant of Corruption"):
            return "base"
        return "card"
    return "base"                          # unknown provenance: plain base


def _chaos_depth(row, price: float, raw: dict) -> float | None:
    """Liquidity in chaos: exchange volumes are already chaos-denominated
    (``volumePrimaryValue`` in raw); listing counts are multiplied by the
    unit price (docs/INTERFACES.md volume normalization). None = unknown.
    """
    vol = row.get("sell_vol")
    if vol is None:
        vol = row.get("buy_vol")
    if vol is None:
        return None
    if "volumePrimaryValue" in raw:
        return float(vol)
    return float(vol) * price


def _best_rows(rows, min_vol_chaos: float = 0.0) -> dict:
    """name -> (best price, is_card) after exclusions and liquidity gating.

    Duplicate names (multiple sources/timestamps) dedupe to the highest
    price, so the name lands in the highest tier it qualifies for. With
    ``min_vol_chaos`` > 0, rows with unknown volume count as illiquid.
    """
    best: dict = {}
    for row in rows:
        group = _classify(row)
        if group is None:
            continue
        price = price_of(row)
        if price is None:
            continue
        if min_vol_chaos > 0:
            depth = _chaos_depth(row, price, _raw_dict(row))
            if depth is None or depth < min_vol_chaos:
                continue
        name = row["item"]
        prev = best.get(name)
        if prev is None or price > prev[0]:
            best[name] = (price, group == "card")
    return best


def card_basetypes(rows) -> set:
    """Names among *rows* that are divination cards (need the Class rule)."""
    return {row["item"] for row in rows if _classify(row) == "card"}


# -------------------------------------------------------------- tier logic
def load_tiers(path: str) -> list[dict]:
    """Load and validate data/filter_tiers.json; returns the tier list."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    tiers = doc.get("tiers") if isinstance(doc, dict) else None
    if not isinstance(tiers, list) or not tiers:
        raise ValueError(f"{path}: expected an object with a 'tiers' list")
    seen = set()
    for tier in tiers:
        if not isinstance(tier, dict) or not isinstance(tier.get("id"), str):
            raise ValueError(f"{path}: every tier needs a string 'id'")
        if not isinstance(tier.get("min_chaos"), (int, float)):
            raise ValueError(f"{path}: tier {tier['id']!r}: numeric "
                             "'min_chaos' required")
        style = tier.get("style")
        if (not isinstance(style, list)
                or not all(isinstance(s, str) for s in style)):
            raise ValueError(f"{path}: tier {tier['id']!r}: 'style' must be "
                             "a list of filter action lines")
        if tier["id"] in seen:
            raise ValueError(f"{path}: duplicate tier id {tier['id']!r}")
        seen.add(tier["id"])
    return tiers


def assign_tiers(rows, tiers, min_vol_chaos: float = 0.0) -> dict:
    """tier_id -> sorted BaseType names whose price reaches the tier.

    Every tier id appears in the result (possibly with an empty list).
    Each name lands in exactly one tier: the highest one it qualifies for.
    """
    best = _best_rows(rows, min_vol_chaos)
    ordered = sorted(tiers, key=lambda t: -float(t["min_chaos"]))
    out: dict = {tier["id"]: [] for tier in ordered}
    for name, (price, _is_card) in best.items():
        for tier in ordered:
            if price >= float(tier["min_chaos"]):
                out[tier["id"]].append(name)
                break
    for names in out.values():
        names.sort()
    return out


# --------------------------------------------------------------- rendering
def render_block(tier_assignments: dict, tiers, league: str,
                 generated_ts: str, cards=frozenset()) -> str:
    """Render the marked economy block (ends with a newline).

    *cards* is the set of names that are divination cards; they render in
    a separate Show rule per tier carrying the Class condition. Everything
    else gets an exact-match BaseType-only rule.
    """
    lines = [
        f"{BLOCK_BEGIN_PREFIX} — generated {generated_ts} — league {league}",
        _DO_NOT_EDIT,
    ]
    for tier in sorted(tiers, key=lambda t: -float(t["min_chaos"])):
        names = tier_assignments.get(tier["id"]) or []
        if not names:
            continue
        bases = [n for n in names if n not in cards]
        card_names = [n for n in names if n in cards]
        lines.append("")
        lines.append(f"# tier {tier['id']} (>= {tier['min_chaos']}c): "
                     f"{len(names)} basetypes")
        for group, class_condition in ((bases, None),
                                       (card_names, CARD_CLASS_CONDITION)):
            if not group:
                continue
            lines.append("Show")
            if class_condition:
                lines.append("    " + class_condition)
            lines.append("    BaseType == "
                         + " ".join(f'"{name}"' for name in group))
            for action in tier.get("style", []):
                lines.append("    " + action)
    lines.append("")
    lines.append(BLOCK_END_MARKER)
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- splicing
def splice(filter_text: str, block: str, install: bool = False) -> str:
    """Replace the marked economy region of *filter_text* with *block*.

    Everything outside the marker lines is preserved byte-for-byte (CRLF
    endings included). Without markers a ValueError is raised unless
    *install* is true, which inserts the block plus a blank line at the
    very top. Splicing the same block twice is idempotent.
    """
    if not block.endswith("\n"):
        block += "\n"
    lines = filter_text.splitlines(keepends=True)
    begin = end = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if begin is None:
            if stripped.startswith(BLOCK_BEGIN_PREFIX):
                begin = i
        elif stripped == BLOCK_END_MARKER:
            end = i
            break
    if begin is None:
        if install:
            return block + "\n" + filter_text
        raise ValueError(
            "no economy-block markers found in the target filter; rerun "
            "with --install to insert the block at the top the first time")
    if end is None:
        raise ValueError(
            "found the economy-block begin marker but no end marker "
            f"({BLOCK_END_MARKER!r}); the filter's block region is "
            "damaged — restore it or delete the begin marker line")
    return "".join(lines[:begin]) + block + "".join(lines[end + 1:])
