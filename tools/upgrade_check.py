#!/usr/bin/env python3
"""Upgrade checker: Ctrl+C an item, diff it against the party's PoBs.

Usage:
    python tools/upgrade_check.py item.txt
    Get-Clipboard | python tools/upgrade_check.py -      (PC, after Ctrl+C)
    pbpaste | python tools/upgrade_check.py -            (Mac, dev)
    python tools/upgrade_check.py                        (Mac: reads pbpaste)

Reads one Ctrl+C item text (file argument, stdin pipe, or — with no
input at all on macOS — the clipboard via pbpaste), maps its item class
to the PoB slot(s) it would occupy, and prints per-member stat deltas
against the equivalent equipped item in each party member's Path of
Building build, with an honest defensive-stat verdict. Rings and
one-handed weapons are compared against BOTH candidate slots.

The first run per bundle fetches each member's PoB link (rate-limited,
per sources.py) and caches the decoded XML as <Player>.pobxml next to
the bundle; every later run is offline. --refresh refetches.

An optional LLM one-liner follows the deterministic table when the LLM
is configured; on LLMDisabled/LLMError it is silently skipped — the
deterministic output is the product. --no-llm forces that.

Read-only everywhere: the player copies the item with the game's own
Ctrl+C; nothing here touches the game. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "overlay"), os.path.join(ROOT, "buildgen")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gear      # noqa: E402
import itemtext  # noqa: E402


def _acquire_text(file_arg: str | None) -> str:
    """Item text from --file / positional file, stdin pipe, or pbpaste."""
    if file_arg and file_arg != "-":
        with open(file_arg, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if file_arg == "-" or not sys.stdin.isatty():
        return sys.stdin.read()
    if sys.platform == "darwin":
        try:
            return subprocess.run(["pbpaste"], capture_output=True,
                                  text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return ""


def _member_report(member: dict, candidate: dict, slots: list[str]) -> dict:
    """Deterministic comparison rows for one party member."""
    rows = []
    for slot in slots:
        equipped = member["slots"].get(slot)
        if equipped is None:
            rows.append({"slot": slot, "empty": True})
        else:
            rows.append({"slot": slot, "empty": False,
                         "equipped": equipped["name"],
                         "equipped_base": equipped["base"],
                         "diff": gear.compare(candidate, equipped)})
    return {"player": member["player"], "class": member.get("class", ""),
            "role": member.get("role", ""), "rows": rows}


def _print_report(report: dict) -> None:
    extra = ", ".join(x for x in (report["class"], report["role"]) if x)
    print(f"\n{report['player']}" + (f" ({extra})" if extra else ""))
    for row in report["rows"]:
        if row["empty"]:
            print(f"  {row['slot']}: empty slot — anything is an upgrade")
            continue
        d = row["diff"]
        est = ", est." if d["estimated"] else ""
        print(f"  {row['slot']}: {row['equipped']} ({row['equipped_base']})")
        print(f"    {gear.delta_text(d)} -> {d['verdict'].upper()} "
              f"(score {d['score']:+g}{est})")
        if d["mods_gained"]:
            print(f"    gains: {'; '.join(d['mods_gained'])}")
        if d["mods_lost"]:
            print(f"    loses: {'; '.join(d['mods_lost'])}")


def _llm_note(candidate: dict, reports: list[dict]) -> str | None:
    """One-paragraph recommendation, or None when the LLM is unavailable."""
    try:
        from llm.client import LLM, LLMDisabled, LLMError
    except ImportError:                       # pragma: no cover
        return None
    lines = [f"Candidate item: {candidate['name']} ({candidate['base']}), "
             f"{candidate['rarity']}, ilvl {candidate['ilvl']}"]
    lines += [f"  {m}" for m in candidate.get("mods", [])]
    lines.append("Per-member deltas vs their PoB-equipped item "
                 "(positive = candidate better):")
    for r in reports:
        for row in r["rows"]:
            if row.get("empty"):
                lines.append(f"- {r['player']} {row['slot']}: empty slot")
            else:
                d = row["diff"]
                lines.append(f"- {r['player']} ({r['role']}) {row['slot']} "
                             f"vs {row['equipped']}: {gear.delta_text(d)} "
                             f"[{d['verdict']}, score {d['score']:+g}]")
    prompt = ("\n".join(lines)
              + "\n\nIn one short paragraph: who (if anyone) should take "
              "this item and why? Consider each member's role. The deltas "
              "are defensive stats only — flag if offense/role concerns "
              "might override them. Be direct; 'nobody' is a fine answer.")
    try:
        return LLM("standard").complete(
            system=("You advise a 4-player Path of Exile party on whether "
                    "a dropped item beats what each member has equipped."),
            messages=prompt, max_tokens=300, feature="upgrade_check")
    except (LLMDisabled, LLMError):
        return None


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default=None,
                    help="item text file, '-' for stdin (default: stdin "
                         "pipe, else pbpaste on macOS)")
    ap.add_argument("--file", dest="file_flag", default=None,
                    help="item text file (same as the positional argument)")
    ap.add_argument("--bundle", default=None,
                    help="party_bundle.json (default: newest builds/*/)")
    ap.add_argument("--member", action="append", default=None,
                    help="only this member (repeatable, case-insensitive)")
    ap.add_argument("--refresh", action="store_true",
                    help="refetch PoBs, ignore the .pobxml cache")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="structured JSON output")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the LLM recommendation")
    args = ap.parse_args(argv)

    text = _acquire_text(args.file_flag or args.file)
    candidate = itemtext.parse(text)
    if candidate is None:
        print("input does not look like Ctrl+C item text", file=sys.stderr)
        return 2

    slots = gear.infer_slots(candidate)
    if not slots:
        cls = candidate.get("item_class") or "this item type"
        print(f"{candidate['name']}: sorry, {cls} isn't supported by the "
              "upgrade checker yet (flasks/jewels need more than a "
              "stat-line diff)")
        return 0

    bundle = args.bundle or gear.newest_bundle(os.path.join(ROOT, "builds"))
    if not bundle or not os.path.exists(bundle):
        print("no party bundle found — pass --bundle "
              "builds/<x>/party_bundle.json", file=sys.stderr)
        return 1

    members = gear.member_builds(bundle, refresh=args.refresh)
    if args.member:
        wanted = {w.lower() for w in args.member}
        members = [m for m in members if m["player"].lower() in wanted]
        if not members:
            print(f"no bundle member matches {sorted(wanted)}",
                  file=sys.stderr)
            return 1

    reports = []
    errors = []
    for m in members:
        if "error" in m:
            errors.append(m)
            continue
        reports.append(_member_report(m, candidate, slots))

    if args.as_json:
        print(json.dumps({"candidate": candidate, "slots": slots,
                          "bundle": bundle, "members": reports,
                          "errors": errors},
                         indent=1, ensure_ascii=False))
        return 0

    print(f"Candidate: {candidate['name']} ({candidate['base']}), "
          f"{candidate['rarity']}, ilvl {candidate['ilvl']}")
    try:
        shown = os.path.relpath(bundle, ROOT)
    except ValueError:  # Windows: bundle on a different drive than the repo
        shown = bundle
    if shown.startswith(".."):
        shown = bundle
    print(f"Slot(s) checked: {', '.join(slots)}   [bundle: {shown}]")
    for r in reports:
        _print_report(r)
    for e in errors:
        print(f"\n{e['player']}: PoB unavailable — {e['error']}",
              file=sys.stderr)
    print("\nverdicts are a defensive-stat heuristic (life/res/movespeed/"
          "links), not a PoB DPS calc — always eyeball weapons and "
          "offense mods yourself")

    if not args.no_llm:
        note = _llm_note(candidate, reports)
        if note:
            print(f"\nLLM take: {note.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
