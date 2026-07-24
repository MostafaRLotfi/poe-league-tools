"""Regenerate the economy loot-filter block from live or stored prices.

Builds a top-of-filter block of loud Show rules for currencies, fragments,
scarabs, essences and divination cards whose live chaos value crosses the
authored tier thresholds in data/filter_tiers.json (user-editable). Loot
filters are first-match-wins, so the block overrides the base filter
(NeverSink etc.) for exactly those BaseTypes and falls through otherwise.

Price source precedence:
  1. --offline-rows PATH   JSON list of snapshot rows (tests / Mac dev)
  2. --live                fresh poe.ninja fetch via market.sources
                           (Currency, Fragment, Scarab, Essence,
                           DivinationCard; global 2 s rate floor)
  3. default               newest rows per item from market/market.db
                           (market.store) if it exists, else a hint to
                           run with --live.

Output: a standalone block file (--out, default economy_block.filter), or
--filter PATH to splice the block into an existing filter in place (backed
up to PATH.bak first; first-time insertion needs --install).

This tool ONLY writes filter text files. It never touches the game client,
and the game only reloads filters on demand — reload manually in-game.

Usage:
    python tools/filter_update.py --live --league Mercenaries
    python tools/filter_update.py --filter "C:/.../NeverSink.filter" --install
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from filtergen import economy  # noqa: E402

DEFAULT_TIERS_PATH = os.path.join(ROOT, "data", "filter_tiers.json")
DEFAULT_DB_PATH = os.path.join(ROOT, "market", "market.db")
CONFIG_PATH = os.path.join(ROOT, "market", "config.json")

RELOAD_REMINDER = ("Reminder: the game only reloads filters on demand - "
                   "reload manually in-game (Options > Game > Item Filter "
                   "> Reload Filter) to pick up this update.")

LIVE_CURRENCY_TYPES = ("Currency", "Fragment")
LIVE_ITEM_TYPES = ("Scarab", "Essence", "DivinationCard")


def _default_league() -> str | None:
    """League name from market/config.json, if readable."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            league = json.load(f).get("league")
        return league if isinstance(league, str) and league else None
    except (OSError, ValueError):
        return None


def _load_offline_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON list of snapshot rows")
    return rows


def _fetch_live_rows(league: str) -> list[dict]:
    """Fresh poe.ninja snapshot rows (rate-limited; 2 s global floor)."""
    from market.sources import NinjaClient
    client = NinjaClient(league)
    rows: list[dict] = []
    for type_ in LIVE_CURRENCY_TYPES:
        rows.extend(client.snapshot_currency(type_))
    for type_ in LIVE_ITEM_TYPES:
        fetched = client.snapshot_items(type_)
        if type_ == "DivinationCard":
            for row in fetched:            # tag so the Class rule is certain
                row["category"] = "DivinationCard"
        rows.extend(fetched)
    return rows


def _load_db_rows(db_path: str, league: str) -> list[dict]:
    """Newest snapshot row per (source, item) from the market DB."""
    from market.store import Store
    with Store(db_path) as store:
        rows = store.latest_snapshots()
    return [r for r in rows if r.get("league") == league]


def load_rows(args, league: str) -> tuple[list[dict] | None, str]:
    """(rows, source description); rows is None when no source is usable."""
    if args.offline_rows:
        return _load_offline_rows(args.offline_rows), \
            f"offline rows {args.offline_rows}"
    if args.live:
        return _fetch_live_rows(league), f"live poe.ninja ({league})"
    db_path = args.db
    if os.path.exists(db_path):            # never create the DB ourselves
        return _load_db_rows(db_path, league), \
            f"latest snapshots in {db_path} ({league})"
    return None, db_path


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="The tool only writes filter files; reloading is manual.")
    parser.add_argument("--offline-rows", metavar="PATH", default=None,
                        help="JSON list of snapshot rows (offline source)")
    parser.add_argument("--live", action="store_true",
                        help="fetch fresh prices from poe.ninja")
    parser.add_argument("--league", default=None,
                        help="league name (default: market/config.json)")
    parser.add_argument("--out", metavar="PATH", default="economy_block.filter",
                        help="standalone block file to write "
                             "(default: %(default)s)")
    parser.add_argument("--filter", metavar="PATH", default=None,
                        help="splice into this existing filter in place "
                             "(backs up to PATH.bak first)")
    parser.add_argument("--install", action="store_true",
                        help="allow first-time insertion of the block at "
                             "the top of --filter")
    parser.add_argument("--min-vol", type=float, default=0.0, metavar="CHAOS",
                        help="drop lines with less chaos-denominated "
                             "volume than this (default: 0 = keep all)")
    parser.add_argument("--tiers", metavar="PATH", default=DEFAULT_TIERS_PATH,
                        help="tier thresholds/styles file "
                             "(default: data/filter_tiers.json)")
    parser.add_argument("--db", metavar="PATH", default=DEFAULT_DB_PATH,
                        help="market snapshot DB for the default source "
                             "(default: market/market.db)")
    args = parser.parse_args(argv)

    league = args.league or _default_league() or "Standard"
    try:
        tiers = economy.load_tiers(args.tiers)
    except (OSError, ValueError) as exc:
        print(f"cannot load tiers: {exc}")
        return 2

    try:
        rows, source_desc = load_rows(args, league)
    except (OSError, ValueError) as exc:
        print(f"cannot load price rows: {exc}")
        return 2
    if rows is None:
        print(f"no market DB at {source_desc} and no --offline-rows given; "
              "run with --live to fetch fresh poe.ninja prices "
              "(or start market/daemon.py to populate the DB)")
        return 2
    if not rows:
        print(f"no snapshot rows for league {league!r} from {source_desc}; "
              "check --league or refresh with --live")
        return 2

    assignments = economy.assign_tiers(rows, tiers,
                                       min_vol_chaos=args.min_vol)
    cards = economy.card_basetypes(rows)
    generated_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = economy.render_block(assignments, tiers, league, generated_ts,
                                 cards=cards)

    if args.filter:
        try:
            with open(args.filter, encoding="utf-8", newline="") as f:
                filter_text = f.read()
        except OSError as exc:
            print(f"cannot read filter: {exc}")
            return 2
        shutil.copyfile(args.filter, args.filter + ".bak")
        try:
            new_text = economy.splice(filter_text, block,
                                      install=args.install)
        except ValueError as exc:
            print(f"refusing to modify {args.filter}: {exc}")
            return 2
        with open(args.filter, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        target = args.filter
        print(f"spliced economy block into {target} "
              f"(backup: {target}.bak)")
    else:
        target = args.out
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(block)
        print(f"wrote standalone economy block to {target}")

    counts = " / ".join(
        f"{len(assignments.get(t['id']) or [])} {t['id']}"
        for t in sorted(tiers, key=lambda t: -float(t["min_chaos"])))
    print(f"tiers: {counts} basetypes "
          f"(source: {source_desc}; min-vol {args.min_vol:g}c)")
    print(RELOAD_REMINDER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
