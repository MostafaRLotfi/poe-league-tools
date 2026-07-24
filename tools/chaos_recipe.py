#!/usr/bin/env python3
"""chaos_recipe — what is my chaos-recipe dump tab still missing?

    export POESESSID=...                       # session cookie, never stored
    python tools/chaos_recipe.py --account MyAccount --tab 2
    python tools/chaos_recipe.py --account MyAccount --tab-name CHAOS
    python tools/chaos_recipe.py --json-file saved_response.json   # offline

Reads ONE stash tab via the official legacy get-stash-items endpoint
(read-only GET, shared global 1 request / 2 s floor) and prints per-slot
counts plus the one-liner:

    2 complete unid sets ready (4c). Missing for next set: 3 rings, 1 belt.

A set = body + helmet + gloves + boots + belt + amulet + 2 rings + one 2H
weapon (bows count) or two 1H-equivalents (a shield counts as one).
Quivers are not required. Eligible items are rares at item level 60-74.
Full-unid sets vendor for 2c, sets with identified pieces for 1c.

Flags:
  --account NAME        forum/account name (required unless --json-file)
  --league NAME         default: "league" from market/config.json
  --tab INDEX           tab index (default 0)
  --tab-name NAME       look the index up by tab name (one extra request)
  --json-file PATH      parse a saved API response instead of fetching
                        (offline / works-on-the-Mac path)
  --json                structured report on stdout
  --allow-identified    no warning about identified pieces diluting 2c sets

Without POESESSID and without --json-file this prints instructions and
exits 1 — it never tracebacks. ToS: read-only official API, no automation
of any game or trade action.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from market import stashapi                            # noqa: E402
from market.sources import SourceError                 # noqa: E402

MARKET_CONFIG_PATH = os.path.join(ROOT, "market", "config.json")


def default_league(path: str = MARKET_CONFIG_PATH) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            league = (json.load(f) or {}).get("league", "")
        if league:
            return str(league)
    except (OSError, ValueError):
        pass
    return "Standard"


def load_craft_data():
    """Base->class dataset for slot classification; None -> keyword fallback."""
    try:
        from craft.pool import CraftData
        return CraftData.load()
    except Exception:
        return None


def resolve_tab_index(args, out) -> int | None:
    """--tab / --tab-name -> index; None means fail (message printed)."""
    if args.tab_name is None:
        return args.tab or 0
    tabs = stashapi.list_tabs(args.account, args.league)
    want = args.tab_name.strip().lower()
    for tab in tabs:
        if str(tab.get("n", "")).strip().lower() == want:
            return int(tab.get("i", 0))
    names = ", ".join(repr(str(t.get("n", "?"))) for t in tabs) or "(none)"
    print(f"no tab named {args.tab_name!r}; tabs: {names}", file=out)
    return None


def main(argv=None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default=None)
    ap.add_argument("--league", default=None)
    tab = ap.add_mutually_exclusive_group()
    tab.add_argument("--tab", type=int, default=None, help="tab index")
    tab.add_argument("--tab-name", default=None, help="tab name lookup")
    ap.add_argument("--json-file", default=None,
                    help="saved get-stash-items response (offline)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--allow-identified", action="store_true")
    args = ap.parse_args(argv)
    args.league = args.league or default_league()

    if args.json_file:
        try:
            with open(args.json_file, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"could not read {args.json_file}: {exc}", file=out)
            return 1
        if not isinstance(payload, dict):
            print(f"{args.json_file}: expected a JSON object", file=out)
            return 1
    else:
        if not args.account:
            ap.error("--account is required unless --json-file is given")
        try:
            index = resolve_tab_index(args, out)
            if index is None:
                return 1
            payload = stashapi.fetch_stash_tab(
                args.account, args.league, index)
        except stashapi.StashUnavailable as exc:
            print(str(exc), file=out)
            return 1
        except SourceError as exc:
            print(f"stash fetch failed: {exc}", file=out)
            return 1

    report = stashapi.analyze(payload.get("items") or [],
                              craft_data=load_craft_data())
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True), file=out)
    else:
        print(stashapi.render(report,
                              allow_identified=args.allow_identified),
              file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
