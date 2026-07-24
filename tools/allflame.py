#!/usr/bin/env python3
"""Curse of the Allflame mechanic tracker CLI -- "is it worth my time?"

Reads Client.txt (READ-ONLY, docs/INTERFACES.md invariant 1) through
overlay/mechanic_tracker.py and the patterns config
data/3.29/mechanic_lines.json. Zone/NPC patterns for the 3.29 mechanic
are VERIFY-marked guesses until calibrated on launch night -- that is
what the `candidates` subcommand is for.

Subcommands:
  report      scan the whole log, print voyages/encounters per active
              hour, per-zone tallies, and chaos-per-encounter from
              manually logged loot (runs/allflame_loot.jsonl)
  watch       tail the log live with a one-line running status;
              Ctrl+C prints the final summary
  candidates  rank field NPC dialogue by frequency so you can paste the
              REAL mechanic lines into mechanic_lines.json on day one
  loot        log a loot drop by hand: `loot 15c ring from voyage boss`
              or `loot 1.5div --div-rate 200`

Examples:
  .venv/bin/python tools/allflame.py report
  .venv/bin/python tools/allflame.py watch --log /tmp/fake_client.txt
  .venv/bin/python tools/allflame.py candidates --top 30
  .venv/bin/python tools/allflame.py loot 25c "sunken chest"
"""
import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "overlay"))

import find_client                                          # noqa: E402
from mechanic_tracker import (MechanicTracker, candidates,  # noqa: E402
                              parse_chaos)

DEFAULT_CONFIG = os.path.join(ROOT, "data", "3.29", "mechanic_lines.json")
# Same directory name run_tracker uses (runs_dir="runs" at repo root).
DEFAULT_RUNS_DIR = os.path.join(ROOT, "runs")
LOOT_BASENAME = "allflame_loot.jsonl"


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_log(explicit):
    """--log wins; otherwise auto-discover the game's Client.txt."""
    if explicit:
        return explicit
    path, how = find_client.discover()
    if path:
        print(f"using Client.txt found via {how}: {path}")
        return path
    raise SystemExit(
        "Could not find Client.txt automatically on this machine (that is "
        "expected on the Mac dev box). Please pass --log PATH -- e.g. the "
        "game's logs/Client.txt on the PC, or a fake log written by "
        "tools/simulate_client.py.")


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read().splitlines()
    except OSError as e:
        raise SystemExit(f"cannot read log {path!r}: {e}")


# ------------------------------------------------------------------ loot
def append_loot(value, note="", div_rate=None, runs_dir=DEFAULT_RUNS_DIR,
                now=None):
    """Parse and append one loot record to runs/allflame_loot.jsonl."""
    chaos = parse_chaos(value, div_rate)
    os.makedirs(runs_dir, exist_ok=True)
    rec = {
        "ts": (now or datetime.datetime.now(datetime.timezone.utc)
               ).isoformat(timespec="seconds"),
        "chaos": chaos,
        "raw": value,
        "note": note,
    }
    with open(os.path.join(runs_dir, LOOT_BASENAME), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def loot_total(runs_dir=DEFAULT_RUNS_DIR):
    """(total_chaos, n_records) from the loot journal; (0.0, 0) if none."""
    path = os.path.join(runs_dir, LOOT_BASENAME)
    total, n = 0.0, 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    total += float(json.loads(line)["chaos"])
                    n += 1
                except (ValueError, KeyError, TypeError):
                    continue        # malformed line: skip, don't crash
    except OSError:
        pass
    return total, n


# ---------------------------------------------------------------- report
def print_summary(s, runs_dir=DEFAULT_RUNS_DIR):
    print("Curse of the Allflame -- mechanic report")
    print(f"  play segments:     {s['segments']}")
    print(f"  active time:       {s['active_hours']} h (non-town, from log "
          "timestamps)")
    print(f"  voyages:           {s['voyages']}"
          f"   ({s['voyages_per_hour']}/h)")
    print(f"  encounters:        {s['encounters']}"
          f"   ({s['encounters_per_hour']}/h)")
    if s["per_zone"]:
        print("  per zone:")
        width = max(len(z) for z in s["per_zone"])
        for zone in sorted(s["per_zone"],
                           key=lambda z: -s["per_zone"][z]["entries"]):
            t = s["per_zone"][zone]
            print(f"    {zone:<{width}}  entries {t['entries']}"
                  f"  encounters {t['encounters']}")
    total, n = loot_total(runs_dir)
    if n:
        print(f"  loot ({LOOT_BASENAME}, {n} entries):")
        print(f"    total logged:    {round(total, 1)}c")
        if s["encounters"]:
            print(f"    per encounter:   "
                  f"{round(total / s['encounters'], 1)}c")
        if s["active_hours"] > 0:
            print(f"    per active hour: "
                  f"{round(total / s['active_hours'], 1)}c")
    else:
        print("  loot: none logged yet -- "
              "`tools/allflame.py loot 15c some note`")


def cmd_report(a):
    log = resolve_log(a.log)
    tracker = MechanicTracker(load_config(a.config))
    for line in read_lines(log):
        tracker.feed(line)
    print_summary(tracker.summary(), a.runs_dir)


# ----------------------------------------------------------------- watch
def cmd_watch(a):
    log = resolve_log(a.log)
    tracker = MechanicTracker(load_config(a.config))
    try:
        pos = os.path.getsize(log)
    except OSError as e:
        raise SystemExit(f"cannot read log {log!r}: {e}")
    buf = b""
    print(f"watching {log} (Ctrl+C for the final summary)")
    try:
        while True:
            try:
                size = os.path.getsize(log)
            except OSError:
                time.sleep(a.interval)
                continue
            if size < pos:              # truncated/replaced -> restart
                pos, buf = 0, b""
            if size > pos:
                with open(log, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                pos += len(chunk)
                buf += chunk
                # Only consume complete lines (the game's writer can
                # flush mid-line -- same rule as ClientWatcher.poll).
                end = buf.rfind(b"\n")
                if end >= 0:
                    text = buf[:end + 1].decode("utf-8", errors="ignore")
                    buf = buf[end + 1:]
                    for line in text.splitlines():
                        tracker.feed(line)
                s = tracker.summary()
                status = (f"voyages {s['voyages']}"
                          f" ({s['voyages_per_hour']}/h)"
                          f" | encounters {s['encounters']}"
                          f" ({s['encounters_per_hour']}/h)"
                          f" | active {s['active_hours']}h"
                          f" | zone: {tracker.zone or '?'}")
                print("\r" + status.ljust(100)[:100], end="", flush=True)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
        print_summary(tracker.summary(), a.runs_dir)


# ------------------------------------------------------------ candidates
def cmd_candidates(a):
    log = resolve_log(a.log)
    rows = candidates(read_lines(log), load_config(a.config), top=a.top)
    if not rows:
        print("no field NPC dialogue found in the log -- play a session "
              "that engages the mechanic first (or check --log).")
        return
    print("NPC lines heard OUTSIDE towns/hideouts, most frequent first")
    print("(speakers chattier in town than in the field are suppressed "
          "as vendor noise):\n")
    for count, speaker, text in rows:
        print(f"  {count:>4} | {speaker} | {text}")
    print(
        "\nCalibration: pick the lines that fire when the league mechanic "
        "starts/appears,\nthen paste them into data/3.29/mechanic_lines.json "
        "as npc_line_patterns entries:\n"
        '  {"id": "<stable-id>", "pattern": "<regex over '
        "'Speaker: text'>\", \"kind\": \"encounter\"}\n"
        "and put the real seafloor/Voyage zone names into "
        "voyage_zone_patterns.\nRe-run `tools/allflame.py report` to check "
        "the counters move.")


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--log", default=None,
                       help="Client.txt path (default: auto-discover)")
        p.add_argument("--config", default=DEFAULT_CONFIG,
                       help=f"patterns config (default {DEFAULT_CONFIG})")
        p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR,
                       help=argparse.SUPPRESS)   # test seam

    p = sub.add_parser("report", help="scan the whole log, print stats")
    common(p)
    p.add_argument("--div-rate", type=float, default=None,
                   help="chaos per divine (only affects future loot "
                        "logging; report reads stored chaos values)")

    p = sub.add_parser("watch", help="tail the log live")
    common(p)
    p.add_argument("--interval", type=float, default=2.0,
                   help="poll interval seconds (default 2)")

    p = sub.add_parser("candidates",
                       help="rank field NPC lines for calibration")
    common(p)
    p.add_argument("--top", type=int, default=20)

    p = sub.add_parser("loot", help="log a loot drop by hand")
    p.add_argument("value", help="e.g. 15c or 1.5div")
    p.add_argument("note", nargs="*", help="free-text note")
    p.add_argument("--div-rate", type=float, default=None,
                   help="chaos per divine (required for div values)")
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR,
                   help=argparse.SUPPRESS)       # test seam

    a = ap.parse_args(argv)
    if a.cmd == "report":
        cmd_report(a)
    elif a.cmd == "watch":
        cmd_watch(a)
    elif a.cmd == "candidates":
        cmd_candidates(a)
    elif a.cmd == "loot":
        try:
            rec = append_loot(a.value, " ".join(a.note), a.div_rate,
                              a.runs_dir)
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"logged {rec['chaos']}c ({rec['raw']})"
              + (f" -- {rec['note']}" if rec["note"] else ""))


if __name__ == "__main__":
    main()
