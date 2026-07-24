"""Curse of the Allflame encounter tracker -- pure log-line logic.

Answers "is the league mechanic worth my time" from the player's own
Client.txt: voyage-zone entries and NPC-cued encounters per active hour,
per-zone tallies, and (via tools/allflame.py) chaos value of manually
logged loot.

Everything here is data-driven by a patterns config
(data/3.29/mechanic_lines.json by default; tests inject their own): the
3.29 mechanic's real zone names and NPC dialogue are unknown pre-launch,
so the config ships VERIFY-marked guesses and `candidates()` exists to
surface the real lines from a first play session.

Design rules (mirrors overlay/client_watcher.py, which is pinned and
untouched):
- Pure stdlib, no Qt, no file IO in the tracker class -- the caller
  feeds raw lines (tools/allflame.py owns reading the log, read-only).
- Deterministic and clock-free: all time comes from line timestamps.
- Anti-spoof: the system prefix is matched from the START of the raw
  line and requires ': ' immediately after '[INFO Client N]'. Player
  chat always carries 'Name:' between the bracket and the message, so a
  '] : You have entered X.' planted inside chat can never match (same
  rule as client_watcher / DECISIONS.md).
"""
import datetime
import re

# Raw-line prefix: `2026/07/24 20:11:03 1234 ac9 [INFO Client 5] : <msg>`.
# Anchored from line start; the two \S+ absorb the ms-counter and hex
# fields, whatever the client writes there.
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \S+ \S+ "
    r"\[INFO Client \d+\] : (?P<msg>.*)$")

# Matched against the extracted system message, from its start.
_ZONE_RE = re.compile(r"^You have entered (?P<zone>.+)\.\s*$")

# NPC dialogue is a system line of the form '<Speaker>: <text>'.
_NPC_RE = re.compile(r"^(?P<speaker>[^:]{1,60}?): (?P<text>.+)$")


def parse_raw(line):
    """Raw Client.txt line -> (datetime, system_msg) or None.

    Unparseable lines (chat, DEBUG lines, garbage, bad timestamps) are
    ignored -- callers just skip None.
    """
    m = _LINE_RE.match(line)
    if not m:
        return None
    try:
        ts = datetime.datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None
    return ts, m.group("msg")


def _town_matcher(config):
    towns = set(config.get("town_zones", []))
    subs = list(config.get("town_substrings", ["Hideout"]))

    def is_town(zone):
        return zone in towns or any(s in zone for s in subs)
    return is_town


class MechanicTracker:
    """Feed raw Client.txt lines; accumulate mechanic stats.

    A "play segment" is the stretch between entering a non-town zone and
    the next town/hideout entry. Active seconds are the deltas between
    consecutive parseable line timestamps while the current zone is a
    non-town zone -- so idling in town costs nothing, and no wall clock
    is ever consulted.
    """

    def __init__(self, config):
        self._voyage_res = [re.compile(p)
                            for p in config.get("voyage_zone_patterns", [])]
        self._npc_pats = [(d["id"], re.compile(d["pattern"]))
                          for d in config.get("npc_line_patterns", [])]
        self._is_town = _town_matcher(config)

        self.zone = None            # current zone name (None until first)
        self.zone_is_town = True    # unknown location counts as town
        self.zone_entered = None    # datetime of the current zone entry
        self._visit = 0             # increments on every zone entry
        self._seen = set()          # {(pattern_id, visit)} encounter dedupe
        self._last_ts = None

        self.voyages = 0
        self.encounters = 0
        self.per_zone = {}          # zone -> {"entries": n, "encounters": n}
        self.active_s = 0.0
        self.segments = []          # [{"start", "end", "active_s"}, ...]
        self._seg = None

    def feed(self, raw_line):
        """Consume one raw line. Returns an event tuple or None:

        ('town', zone) / ('voyage', zone) / ('zone', zone)
        ('encounter', (pattern_id, zone))
        """
        parsed = parse_raw(raw_line)
        if parsed is None:
            return None
        ts, msg = parsed

        # Active time: the interval since the previous line belongs to
        # the zone we were in when it elapsed (a town-entry line still
        # closes out field time).
        if self._last_ts is not None and self.zone is not None \
                and not self.zone_is_town:
            delta = (ts - self._last_ts).total_seconds()
            if delta > 0:
                self.active_s += delta
                if self._seg is not None:
                    self._seg["active_s"] += delta
        self._last_ts = ts

        m = _ZONE_RE.match(msg)
        if m:
            zone = m.group("zone")
            town = self._is_town(zone)
            self.zone, self.zone_is_town, self.zone_entered = zone, town, ts
            self._visit += 1
            if town:
                if self._seg is not None:       # segment ends in town
                    self._seg["end"] = ts.isoformat()
                    self._seg = None
                return ("town", zone)
            if self._seg is None:               # segment starts in the field
                self._seg = {"start": ts.isoformat(), "end": None,
                             "active_s": 0.0}
                self.segments.append(self._seg)
            tally = self.per_zone.setdefault(zone,
                                             {"entries": 0, "encounters": 0})
            tally["entries"] += 1
            if any(r.search(zone) for r in self._voyage_res):
                self.voyages += 1
                return ("voyage", zone)
            return ("zone", zone)

        # NPC dialogue only counts in the field.
        if self.zone is None or self.zone_is_town:
            return None
        if not _NPC_RE.match(msg):
            return None
        for pid, rx in self._npc_pats:
            if rx.search(msg):
                key = (pid, self._visit)        # once per (pattern, visit):
                if key in self._seen:           # a chatty NPC repeating its
                    return None                 # bark is ONE encounter
                self._seen.add(key)
                self.encounters += 1
                self.per_zone.setdefault(
                    self.zone, {"entries": 0, "encounters": 0}
                )["encounters"] += 1
                return ("encounter", (pid, self.zone))
        return None

    def summary(self):
        hours = self.active_s / 3600.0

        def rate(n):
            return round(n / hours, 2) if hours > 0 else 0.0
        return {
            "voyages": self.voyages,
            "encounters": self.encounters,
            "active_hours": round(hours, 2),
            "voyages_per_hour": rate(self.voyages),
            "encounters_per_hour": rate(self.encounters),
            "per_zone": {z: dict(t) for z, t in self.per_zone.items()},
            "segments": len(self.segments),
        }


def candidates(lines, config, top=20):
    """Day-one calibration: rank NPC dialogue heard OUTSIDE towns.

    Returns [(count, speaker, text), ...] (highest count first), where
    identical (speaker, text) pairs collapse into one row.

    Vendor-chatter heuristic: a speaker heard in town/hideout zones MORE
    often than in field zones is dropped entirely -- town NPCs (vendors,
    quest givers) bark constantly while you shop, whereas the mechanic's
    NPC talks in the field. Rough on purpose: a mechanic NPC you also
    visit in town could be suppressed, so eyeball the raw log if the
    list looks empty. Location before the first zone line is treated as
    town (conservative).
    """
    is_town = _town_matcher(config)
    in_town = True
    counts = {}                 # (speaker, text) -> field count
    field_by, town_by = {}, {}  # speaker -> count by location kind
    for raw in lines:
        parsed = parse_raw(raw)
        if parsed is None:
            continue
        _, msg = parsed
        zm = _ZONE_RE.match(msg)
        if zm:
            in_town = is_town(zm.group("zone"))
            continue
        nm = _NPC_RE.match(msg)
        if not nm:
            continue
        speaker, text = nm.group("speaker"), nm.group("text").strip()
        if in_town:
            town_by[speaker] = town_by.get(speaker, 0) + 1
        else:
            field_by[speaker] = field_by.get(speaker, 0) + 1
            counts[(speaker, text)] = counts.get((speaker, text), 0) + 1
    out = [(n, sp, text) for (sp, text), n in counts.items()
           if town_by.get(sp, 0) <= field_by.get(sp, 0)]
    out.sort(key=lambda row: (-row[0], row[1], row[2]))
    return out[:top]


_CHAOS_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:c|chaos)?\s*$", re.I)
_DIV_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:d|div|divine|divines)\s*$",
                     re.I)


def parse_chaos(value_str, div_rate=None):
    """Loot value string -> chaos. '15c'/'15' -> 15.0; '1.5div' needs
    div_rate (chaos per divine) -> 1.5 * div_rate; else ValueError."""
    m = _CHAOS_RE.match(value_str)
    if m:
        return float(m.group(1))
    m = _DIV_RE.match(value_str)
    if m:
        if div_rate is None:
            raise ValueError(
                f"{value_str!r} is a divine amount but no divine rate was "
                "given -- pass --div-rate <chaos per divine>")
        return float(m.group(1)) * float(div_rate)
    raise ValueError(
        f"cannot parse loot value {value_str!r} -- use e.g. '15c', '15', "
        "or '1.5div' with --div-rate")
