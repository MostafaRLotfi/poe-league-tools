#!/usr/bin/env python3
"""Doctor: check everything the overlay needs, in plain language.

Run it when something looks wrong (or double-click doctor.bat):

    .venv\\Scripts\\python.exe tools\\preflight.py

Every line is OK / INFO / WARN / FAIL with a one-line fix. FAIL means
the overlay is missing something it needs; WARN means it will run but
part of it is silently off (the failure mode this tool exists to catch:
wrong `me` name, vanished build_notes, a stale Client.txt from an old
install). Exit code 1 when anything FAILs, so scripts can gate on it.

Stdlib only, offline, read-only -- safe to run any time, including
mid-league. tools/join_party.py runs these same checks after writing
the config.
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "overlay")
if OVERLAY not in sys.path:  # for find_client / client_watcher / route_engine
    sys.path.insert(0, OVERLAY)

OK, INFO, WARN, FAIL = "OK", "INFO", "WARN", "FAIL"

# Everything overlay/main.py + overlay_window.py actually read; anything
# else in config.json is almost certainly a typo doing nothing.
KNOWN_KEYS = {"client_txt", "poll_ms", "opacity", "width", "font_pt",
              "lookahead", "routes_dir", "resume_route", "build_notes",
              "timer", "runs_dir", "item_eval", "links_best", "party",
              "hotkeys", "league"}
KNOWN_PARTY_KEYS = {"me", "members", "gap_warn"}
KNOWN_HOTKEYS = {"prev", "next", "toggle", "settings", "clickthrough",
                 "layouts", "narrate_repeat", "narrate_toggle",
                 "choose_build"}

STALE_LOG_DAYS = 7
TAIL_BYTES = 262144


def _resolve_from(base, path):
    """Mirror overlay/main.py's _resolve: relative paths in the config
    are relative to the dir config.json lives in (overlay/)."""
    return path if os.path.isabs(path) else os.path.join(base, path)


def _age(seconds):
    for unit, div in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= div:
            return f"{int(seconds // div)}{unit}"
    return f"{int(seconds)}s"


# ------------------------------------------------------------------ checks
# Each returns a list of (level, name, detail) rows and never raises;
# run_all wraps them anyway so one broken check can't kill the report.

def check_python():
    v = sys.version_info
    if v < (3, 10):
        return [(FAIL, "python", f"{v.major}.{v.minor} -- need 3.10+ "
                 "(install from python.org, then re-run setup_pc.bat)")]
    return [(OK, "python", f"{v.major}.{v.minor}.{v.micro}")]


def check_qt():
    # find_spec locates without importing -- keeps the doctor Qt-free.
    if importlib.util.find_spec("PyQt6") is None:
        return [(FAIL, "PyQt6", "not installed -- run setup_pc.bat "
                 "(or: pip install -r requirements.txt)")]
    return [(OK, "PyQt6", "installed")]


def check_config(config_path):
    """-> (rows, cfg_or_None). Parse + typo'd/duplicate keys."""
    rows = []
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return [(FAIL, "config", f"missing: {config_path} -- run "
                 "setup_pc.bat (the wizard writes it)")], None
    except json.JSONDecodeError as e:
        return [(FAIL, "config", f"invalid JSON at line {e.lineno} col "
                 f"{e.colno}: {e.msg} -- re-run setup_pc.bat to rewrite "
                 "it cleanly")], None
    if not isinstance(cfg, dict):
        return [(FAIL, "config", "top level must be an object {...}")], None
    rows.append((OK, "config", config_path))

    unknown = sorted(set(cfg) - KNOWN_KEYS)
    if unknown:
        rows.append((WARN, "config keys", f"unrecognized (typo? they do "
                     f"nothing): {', '.join(unknown)}"))
    party = cfg.get("party") or {}
    if isinstance(party, dict):
        bad = sorted(set(party) - KNOWN_PARTY_KEYS)
        if bad:
            rows.append((WARN, "config keys",
                         f"unrecognized under party: {', '.join(bad)}"))
    hk = cfg.get("hotkeys") or {}
    if isinstance(hk, dict):
        bad = sorted(set(hk) - KNOWN_HOTKEYS)
        if bad:
            rows.append((WARN, "config keys",
                         f"unrecognized under hotkeys: {', '.join(bad)}"))
        vals = [v for v in hk.values() if v]
        dupes = sorted({v for v in vals if vals.count(v) > 1})
        if dupes:
            rows.append((FAIL, "hotkeys", f"same key bound twice: "
                         f"{', '.join(dupes)}"))
    return rows, cfg


def check_client(cfg, override=None, now=time.time):
    """Found / fresh / actually looks like a PoE English-client log."""
    import find_client
    configured = override or (cfg or {}).get("client_txt") or ""
    path, how = find_client.discover(configured)
    if not path:
        return [(FAIL, "Client.txt", f"not found (configured: "
                 f"{configured or '<unset>'}); probed common paths, Steam "
                 "libraries and all drives -- is the game installed? Set "
                 "'client_txt' in overlay/config.json to your install's "
                 "logs\\Client.txt")]
    rows = []
    where = path if how == "config" else f"{path} (auto-detected: {how})"
    rows.append((OK, "Client.txt", where))
    if how != "config" and not override:
        rows.append((INFO, "Client.txt", "not saved in the config yet -- "
                     "run setup_pc.bat once to persist it"))

    try:
        size = os.path.getsize(path)
        age_s = max(0, now() - os.path.getmtime(path))
    except OSError as e:
        return rows + [(WARN, "Client.txt", f"could not stat: {e}")]
    if size == 0:
        rows.append((INFO, "log activity", "file is empty (fresh install "
                     "or rotated) -- fine, it fills as you play"))
        return rows
    if age_s > STALE_LOG_DAYS * 86400:
        rows.append((WARN, "log activity", f"last write {_age(age_s)} ago "
                     "-- right install? (an old copy of the game leaves a "
                     "stale log behind)"))
    else:
        rows.append((OK, "log activity", f"last write {_age(age_s)} ago"))

    # The parser only knows the English client's strings; a localized log
    # makes the overlay sit there doing nothing with no error anywhere.
    try:
        from client_watcher import parse_line
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="ignore")
        hits = sum(1 for line in tail.splitlines() if parse_line(line))
        if hits:
            rows.append((OK, "log parse", f"{hits} recognizable event(s) "
                         "in the recent log"))
        else:
            rows.append((WARN, "log parse", "no recognizable lines in the "
                         "log tail -- non-English game client? (the parser "
                         "needs English log strings) Or you simply haven't "
                         "entered a zone lately"))
    except OSError as e:
        rows.append((WARN, "log parse", f"could not read tail: {e}"))
    return rows


def check_routes(cfg, base=OVERLAY):
    from route_engine import RouteEngine
    routes_dir = _resolve_from(base, (cfg or {}).get("routes_dir",
                                                     "../routes"))
    try:
        eng = RouteEngine(routes_dir)
    except Exception as e:  # noqa: BLE001 -- any load error is the finding
        return [(FAIL, "routes", f"could not load from {routes_dir}: {e}")]
    acts = sorted({s.get("act") for s in eng.steps})
    rows = [(OK, "routes", f"{len(eng.steps)} steps across "
             f"{len(acts)} act(s)")]
    if len(acts) < 10:
        rows.append((WARN, "routes", f"only acts {acts} present -- "
                     "expected 1-10"))
    return rows


def check_notes(cfg, base=OVERLAY):
    bn = (cfg or {}).get("build_notes")
    if not bn:
        return [(INFO, "gem notes", "build_notes not set -- optional; "
                 "setup_pc.bat wires it from builds/party_bundle.json")]
    path = _resolve_from(base, bn)
    if not os.path.exists(path):
        return [(FAIL, "gem notes", f"build_notes points at a missing "
                 f"file: {path} -- re-run setup_pc.bat (gem reminders are "
                 "OFF until fixed)")]
    try:
        with open(path, encoding="utf-8") as f:
            notes = json.load(f)
        acts = sorted(int(x["act"]) for x in notes)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return [(FAIL, "gem notes", f"{path} is not a notes file "
                 f"([{{'act':1,'text':...}}]): {e}")]
    if not acts:
        return [(WARN, "gem notes", f"{os.path.basename(path)} has no "
                 "entries -- name PoB skill sets 'Act 1 ...' etc. and "
                 "re-run buildgen/party.py")]
    generic = all(isinstance(x, dict) and x.get("source") == "generic"
                  for x in notes)
    kind = ("generic class plan (the PoB had no act-tagged skill sets)"
            if generic else "from your PoB")
    return [(OK, "gem notes", f"{os.path.basename(path)} covers act(s) "
             f"{', '.join(map(str, acts))} -- {kind}")]


def check_party(cfg, base=OVERLAY):
    party = (cfg or {}).get("party") or {}
    me = party.get("me") or ""
    members = list(party.get("members") or [])
    rows = []

    for name in [me] + members:
        if name != name.strip():
            rows.append((FAIL, "party", f"'{name}' has leading/trailing "
                         "spaces -- log events match names exactly, so "
                         "this will never match"))
    if me and me in members:
        rows.append((FAIL, "party", f"'{me}' is both me and a member -- "
                     "members must be the OTHER players only"))
    if not me and members:
        rows.append((FAIL, "party", "party.me is empty but members are "
                     "set -- your levels/deaths can't be told apart; run "
                     "setup_pc.bat and pick your character"))
    if not me and not members:
        rows.append((INFO, "party", "solo mode (no names configured) -- "
                     "run setup_pc.bat after buildgen/party.py to join "
                     "the party"))
    if len(members) > 2:
        rows.append((WARN, "party", f"{len(members)} other members -- "
                     "tooling is tuned for a party of 2-3"))

    # Names are typed by hand; the bundle knows how they're really spelt.
    bundle_path = os.path.join(os.path.dirname(base), "builds",
                               "party_bundle.json")
    if me and os.path.exists(bundle_path):
        try:
            with open(bundle_path, encoding="utf-8") as f:
                players = [m.get("player")
                           for m in json.load(f).get("members", [])]
            if players and me not in players:
                rows.append((WARN, "party", f"me='{me}' is not in "
                             f"builds/party_bundle.json ({', '.join(players)})"
                             " -- typo, or an out-of-date bundle?"))
        except (OSError, json.JSONDecodeError, AttributeError):
            rows.append((WARN, "party", "builds/party_bundle.json exists "
                         "but could not be read -- regenerate with "
                         "buildgen/party.py"))
    if me and not any(r[0] in (FAIL, WARN) for r in rows):
        who = f"me={me}" + (f"; others: {', '.join(members)}"
                            if members else " (no other members)")
        rows.insert(0, (OK, "party", who))
    return rows


def check_runs(cfg, base=OVERLAY):
    if not (cfg or {}).get("timer", True):
        return [(INFO, "run timer", "disabled in config")]
    runs_dir = os.path.normpath(
        _resolve_from(base, (cfg or {}).get("runs_dir", "../runs")))
    probe = runs_dir
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not os.access(probe or os.sep, os.W_OK):
        return [(WARN, "run timer", f"runs dir not writable: {runs_dir} "
                 "-- splits/PB won't be saved")]
    return [(OK, "run timer", f"saving runs to {runs_dir}")]


def check_llm():
    have_pkg = importlib.util.find_spec("anthropic") is not None
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if os.environ.get("POE_TOOLS_LLM", "").lower() == "off":
        return [(INFO, "LLM extras", "kill switch on (POE_TOOLS_LLM=off)")]
    if have_pkg and have_key:
        return [(OK, "LLM extras", "anthropic + API key present")]
    return [(INFO, "LLM extras", "off (optional) -- overlay/routes/party "
             "all work without them")]


def check_platform():
    if sys.platform == "win32":
        return [(OK, "platform", "Windows: global hotkeys + click-through "
                 "available")]
    return [(INFO, "platform", f"{sys.platform}: dev-machine mode -- "
             "hotkeys are window-local, click-through off")]


# ------------------------------------------------------------------ driver

def run_all(config_path=None, client=None):
    """Every check, crash-proofed; returns [(level, name, detail), ...]."""
    config_path = config_path or os.path.join(OVERLAY, "config.json")
    base = os.path.dirname(os.path.abspath(config_path))
    rows = []

    def run(fn, *a, **kw):
        try:
            rows.extend(fn(*a, **kw))
        except Exception as e:  # noqa: BLE001 -- doctor must always finish
            rows.append((WARN, fn.__name__, f"check itself failed: {e!r}"))

    run(check_python)
    run(check_qt)
    try:
        cfg_rows, cfg = check_config(config_path)
    except Exception as e:  # noqa: BLE001
        cfg_rows, cfg = [(WARN, "config", f"check itself failed: {e!r}")], None
    rows.extend(cfg_rows)
    run(check_client, cfg, client)
    run(check_routes, cfg, base)
    run(check_notes, cfg, base)
    run(check_party, cfg, base)
    run(check_runs, cfg, base)
    run(check_llm)
    run(check_platform)
    return rows


def render(rows, say=print):
    """Print the report; return the exit code (1 if anything FAILed)."""
    for level, name, detail in rows:
        say(f" {level:<4}  {name:<12}  {detail}")
    fails = sum(1 for r in rows if r[0] == FAIL)
    warns = sum(1 for r in rows if r[0] == WARN)
    if fails or warns:
        say(f"\n{fails} FAIL / {warns} WARN -- fix FAILs first; "
            "WARNs mean something is silently off.")
    else:
        say("\nAll good. Remember: the game must run in Windowed "
            "Fullscreen for the overlay to show.")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config",
                    default=os.path.join(OVERLAY, "config.json"))
    ap.add_argument("--client", help="check this Client.txt path instead "
                    "of the configured/auto-detected one")
    args = ap.parse_args(argv)
    print("== poe-league-tools doctor ==")
    return render(run_all(args.config, args.client))


if __name__ == "__main__":
    sys.exit(main())
