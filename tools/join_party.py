#!/usr/bin/env python3
"""First-run wizard: set this PC up as YOU, no JSON editing.

setup_pc.bat runs this after installing deps; re-run it any time:

    .venv\\Scripts\\python.exe tools\\join_party.py

It finds Client.txt (common paths, Steam's own library list, a drive
scan), asks who you are (from builds/party_bundle.json, written by
`buildgen/party.py` on whoever generates the builds), then writes
overlay/config.json for this machine -- your name as `me`, the others
on the party row, your gem reminders wired up. Ends with the doctor
report (tools/preflight.py) so problems surface now, not at 8 pm on
league start.

Everything is also scriptable for tests/automation:
    join_party.py --who FriendA --client C:\\...\\Client.txt --yes
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "overlay")
if OVERLAY not in sys.path:  # find_client lives with the overlay
    sys.path.insert(0, OVERLAY)
if ROOT not in sys.path:     # tools.preflight when run from tools/
    sys.path.insert(0, ROOT)

import find_client                       # noqa: E402
from tools import preflight              # noqa: E402

# Fallback when config.json is missing or too broken to keep: the same
# defaults the repo ships in overlay/config.json.
DEFAULT_CONFIG = {
    "client_txt": "",
    "poll_ms": 300,
    "opacity": 0.92,
    "width": 360,
    "font_pt": 11,
    "lookahead": 4,
    "routes_dir": "../routes",
    "resume_route": True,
    "build_notes": None,
    "timer": True,
    "runs_dir": "../runs",
    "item_eval": True,
    "links_best": 3,
    "layouts": {"enabled": True, "auto_show": True, "dir": "assets/layouts"},
    "narration": {
        "enabled": False, "rate": 0, "volume": 100,
        "tips": True, "layout": True,
    },
    "party": {"me": "", "members": [], "gap_warn": 3},
    "hotkeys": {"prev": "F2", "next": "F3", "toggle": "F4",
                "settings": "F1", "clickthrough": "F6", "layouts": "F7",
                "narrate_repeat": "F8", "narrate_toggle": "F9",
                "choose_build": "F10"},
}


def _ask_default(prompt):
    try:
        return input(prompt)
    except EOFError:      # piped/headless run: accept every default
        return ""


def load_config(path, say=print):
    """Existing config (tweaks preserved), or defaults when it's absent
    or corrupt -- a corrupt one is backed up, never silently discarded."""
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg
        say(f"   (config at {path} isn't an object; starting fresh)")
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    except json.JSONDecodeError as e:
        bak = path + ".bak"
        shutil.copyfile(path, bak)
        say(f"   (config was invalid JSON -- line {e.lineno}: {e.msg}; "
            f"kept a copy at {os.path.basename(bak)}, starting fresh)")
    return json.loads(json.dumps(DEFAULT_CONFIG))


def load_bundle(path):
    """builds/party_bundle.json -> {'league', 'members': [...]}, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
        members = bundle.get("members") or []
        if members and all(m.get("player") for m in members):
            return bundle
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return None


def _relative_or_absolute(target, start):
    """Portable relative path, or absolute when Windows drives differ."""
    try:
        value = os.path.relpath(os.path.abspath(target),
                                os.path.abspath(start))
    except ValueError:
        value = os.path.abspath(target)
    return value.replace(os.sep, "/")


def step_client(cfg, client_arg, yes, ask, say):
    """Resolve Client.txt; returns the path to persist ('' = leave be)."""
    say("")
    say("-- Client.txt (the game's own text log; the overlay only ever "
        "reads it)")
    if client_arg:
        if not os.path.exists(client_arg):
            say(f"   note: {client_arg} does not exist (yet) -- saving it "
                "anyway as requested")
        return client_arg

    found, how = find_client.discover(cfg.get("client_txt") or "")
    if found:
        say(f"   found ({how}): {found}")
        if yes:
            return found
        typed = ask("   [Enter] use this, or paste a different path: "
                    ).strip().strip('"')
        if not typed:
            return found
        found = typed
    else:
        say("   not found automatically (probed common paths, Steam "
            "libraries, all drives).")
        if yes:
            say("   leaving it unset -- re-run this wizard once the game "
                "is installed.")
            return ""
        found = ask("   paste the full path to logs\\Client.txt "
                    "([Enter] to skip for now): ").strip().strip('"')

    while found and not os.path.exists(found):
        say(f"   that file does not exist: {found}")
        found = ask("   try again ([Enter] to skip for now): "
                    ).strip().strip('"')
    if not found:
        say("   skipped -- steps won't auto-advance until it's set "
            "(re-run me, or doctor.bat will remind you).")
    return found


def _describe(member):
    cls = member.get("class") or "?"
    asc = member.get("ascendancy")
    return f"{cls} ({asc})" if asc else cls


def step_who(bundle, who, others_arg, cfg, yes, ask, say):
    """-> (me, members, notes_relref_or_None, exit_code). Bundle-driven
    when a bundle exists, manual entry otherwise."""
    party = cfg.get("party") or {}
    if bundle:
        members = bundle["members"]
        say("")
        say("-- Who are you?")
        default_i = next((i for i, m in enumerate(members) if m.get("me")), 0)
        for i, m in enumerate(members):
            say(f"   {i + 1}. {m['player']:<20} {_describe(m)}")
        if who:
            match = [m for m in members
                     if m["player"].lower() == who.lower()]
            if not match:
                say(f"!! --who '{who}' is not in the bundle "
                    f"({', '.join(m['player'] for m in members)})")
                return None, None, None, 2
            chosen = match[0]
        elif yes:
            chosen = members[default_i]
        else:
            while True:
                raw = ask(f"   number [{default_i + 1}]: ").strip()
                if not raw:
                    chosen = members[default_i]
                    break
                if raw.isdigit() and 1 <= int(raw) <= len(members):
                    chosen = members[int(raw) - 1]
                    break
                say(f"   pick 1-{len(members)}")
        me = chosen["player"]
        others = [m["player"] for m in members if m is not chosen]
        say(f"   -> you are {me}; party row will track: "
            f"{', '.join(others) or '(nobody -- solo bundle)'}")
        return me, others, chosen.get("notes"), 0

    # No bundle (e.g. a git clone without builds/): plain questions.
    say("")
    say("-- Party (no builds/party_bundle.json found -- ask whoever ran "
        "buildgen/party.py")
    say("   for the builds folder, or just type names; [Enter] keeps "
        "what's set)")
    me = who or ""
    others = [o.strip() for o in (others_arg or "").split(",") if o.strip()]
    if not me and not yes:
        cur = party.get("me") or ""
        me = ask(f"   your character name [{cur or 'solo'}]: ").strip() or cur
    if not others and not yes:
        cur = ", ".join(party.get("members") or [])
        raw = ask(f"   other members, comma-separated [{cur or 'none'}]: "
                  ).strip() or cur
        others = [o.strip() for o in raw.split(",") if o.strip()]
    me = me or (party.get("me") or "")
    others = others or list(party.get("members") or [])
    return me, others, None, 0


def wizard(config_path, bundle_path, client_arg=None, who=None, others=None,
           yes=False, ask=_ask_default, say=print, run_checks=True):
    say("== poe-league-tools first-run setup ==")
    rel = _relative_or_absolute(config_path, os.getcwd())
    shown = config_path if rel.startswith("..") else rel
    say(f"This writes {shown} for THIS machine. Safe to re-run any time.")

    cfg = load_config(config_path, say)
    bundle = load_bundle(bundle_path)

    client = step_client(cfg, client_arg, yes, ask, say)
    me, members, notes_name, code = step_who(
        bundle, who, others, cfg, yes, ask, say)
    if code:
        return code

    if client:
        cfg["client_txt"] = client
    party = dict(cfg.get("party") or {})
    party["me"] = me
    party["members"] = members
    party.setdefault("gap_warn", 3)
    cfg["party"] = party

    if notes_name:
        # Bundle stores basenames; wire the chosen player's notes as a
        # path relative to the config's own dir (how main.py resolves),
        # forward slashes so the JSON is copy-paste-safe on Windows.
        notes_abs = os.path.join(os.path.dirname(os.path.abspath(
            bundle_path)), notes_name)
        if os.path.exists(notes_abs):
            cfg["build_notes"] = _relative_or_absolute(
                notes_abs, os.path.dirname(os.path.abspath(config_path)))
            say(f"   gem reminders: {cfg['build_notes']}")
        else:
            say(f"   note: {notes_name} missing next to the bundle -- gem "
                "reminders not wired (re-run buildgen/party.py)")

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    say(f"\nWrote {config_path}.")

    if run_checks:
        say("\n== doctor ==")
        preflight.render(preflight.run_all(config_path), say)

    say("\n== next ==")
    say(" 1. In game: Options -> Graphics -> set 'Windowed Fullscreen'")
    say("    (the overlay cannot draw over exclusive fullscreen).")
    say(" 2. Double-click overlay\\run_overlay.bat -- that's it.")
    say("    Hotkeys: F2/F3 step back/forward, F4 hide, F6 click-through.")
    say("Anything weird later: double-click doctor.bat.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(OVERLAY, "config.json"))
    ap.add_argument("--bundle",
                    default=os.path.join(ROOT, "builds", "party_bundle.json"))
    ap.add_argument("--client", help="use this Client.txt path (skips "
                    "auto-detection)")
    ap.add_argument("--who", help="your character name as it appears in "
                    "the party bundle (skips the question)")
    ap.add_argument("--others", help="comma-separated other members "
                    "(only used when there is no bundle)")
    ap.add_argument("--yes", action="store_true",
                    help="non-interactive: accept every detected default")
    a = ap.parse_args(argv)
    return wizard(a.config, a.bundle, a.client, a.who, a.others, a.yes)


if __name__ == "__main__":
    sys.exit(main())
