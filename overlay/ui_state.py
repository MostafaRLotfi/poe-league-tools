"""Persists overlay UI state (scale, position, compact mode) between
sessions. Pure stdlib -- no Qt imports -- so it can be unit tested
headless.

Separate from config.json on purpose: the config is a hand-edited file
users copy from each other, this is machine-written runtime state that
should never generate merge questions ("why is your width different?").
"""
import json
import os

SCALE_MIN, SCALE_MAX = 0.5, 2.5

DEFAULTS = {
    "card":    {"scale": 1.0, "pos": None, "compact": False, "size": None},
    "layouts": {"scale": 1.0, "pos": None},
    # Appearance chosen in the settings dialog (settings_dialog.py); read
    # by overlay_window/layout_panel through theme.py. font_pt None ->
    # fall back to config.json's font_pt so an untouched install is
    # unchanged.
    "appearance": {"mode": "dark", "palette": "default", "font_pt": None},
}


def clamp_scale(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(SCALE_MAX, max(SCALE_MIN, value))


def valid_pos(value):
    """[x, y] ints -> (x, y); anything else -> None."""
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool)
                    for v in value)):
        return tuple(value)
    return None


def valid_size(value):
    """[w, h] positive ints -> (w, h); anything else -> None. A stale or
    hand-broken size must never resize the window to zero/negative."""
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0
                    for v in value)):
        return tuple(value)
    return None


class UiState:
    """Tiny sectioned key-value store; every set() writes through.

    IO failures are non-fatal by design (state is a convenience, the
    overlay must keep running from a read-only dir or a full disk).
    """

    def __init__(self, path):
        self.path = path
        self.data = {k: dict(v) for k, v in DEFAULTS.items()}
        self._save_failed = False
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(loaded, dict):
            return
        for section, values in loaded.items():
            if section in self.data and isinstance(values, dict):
                for key in self.data[section]:
                    if key in values:
                        self.data[section][key] = values[key]

    def get(self, section, key):
        return self.data.get(section, {}).get(key)

    def set(self, section, key, value):
        self.data.setdefault(section, {})[key] = value
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError as e:
            if not self._save_failed:      # warn once, not every wheel tick
                self._save_failed = True
                print(f"[ui] could not save UI state to {self.path}: {e}")
