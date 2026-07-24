"""Headless tests for the overlay appearance layer.

Covers theme.py (palettes, mode/font resolution, QSS builders) and the
UiState additions that persist the choice (appearance section +
valid_size). Pure stdlib, no Qt -- same bootstrap as test_layouts.py.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "overlay")]

import theme                                          # noqa: E402
from ui_state import UiState, valid_size, valid_pos   # noqa: E402

# ------------------------------------------------ every palette is complete
assert theme.PALETTE_ORDER == ["default", "high_contrast",
                               "colorblind", "easy"]
for name in theme.PALETTE_ORDER:
    assert name in theme.PALETTES, name
    entry = theme.PALETTES[name]
    assert entry["label"], name
    for mode in theme.MODES:
        roles = entry[mode]
        missing = [k for k in theme.ROLE_KEYS if k not in roles]
        assert not missing, f"{name}/{mode} missing roles: {missing}"
        # every role is a non-empty color string
        assert all(isinstance(roles[k], str) and roles[k] for k in roles)

# the dropdown label matches the table
assert theme.palette_label("colorblind") == "Color-blind friendly"
assert theme.palette_label("nope") == theme.PALETTES["default"]["label"]

# ------------------------------------------------ resolve + normalize fallback
assert theme.normalize("easy", "light") == ("easy", "light")
assert theme.normalize("bogus", "bogus") == ("default", "dark")
assert theme.normalize(None, None) == ("default", "dark")

roles = theme.resolve("high_contrast", "light")
assert set(roles) >= set(theme.ROLE_KEYS)
assert theme.resolve("junk", "junk") == theme.resolve("default", "dark"), \
    "unknown palette/mode resolves to the shipped default"
# resolve returns a copy -- mutating it can't corrupt the shared table
roles["text"] = "#deadbe"
assert theme.PALETTES["high_contrast"]["light"]["text"] != "#deadbe"

# ------------------------------------------------ font sizing
assert theme.effective_pt(11, 1.0) == 11
assert theme.effective_pt(10, 2.0) == 20
assert theme.effective_pt(11, 0.1) == 6, "never below the 6pt floor"
assert theme.effective_pt("junk", None) == 11, "junk -> sane default"
assert theme.clamp_font(3) == theme.FONT_MIN
assert theme.clamp_font(999) == theme.FONT_MAX
assert theme.clamp_font(13) == 13
assert theme.clamp_font("x") == 11
assert theme.FONT_MIN <= theme.clamp_font(None) <= theme.FONT_MAX

# ------------------------------------------------ inline span colors
dark = theme.resolve("default", "dark")
assert theme.kind_color(dark, "kill") == dark["kind_kill"]
assert theme.kind_color(dark, "town") == dark["kind_town"]
assert theme.kind_color(dark, "mystery") == dark["kind_travel"], "kind fallback"
assert theme.verdict_color(dark, "TAKE") == dark["verdict_take"]
assert theme.verdict_color(dark, "SKIP") == dark["verdict_skip"]
assert theme.verdict_color(dark, "???") == dark["verdict_skip"], "verdict fallback"

# ------------------------------------------------ QSS builders are usable
css = theme.card_qss(dark, 12)
assert "{" in css and "}" in css and "{{" not in css, "no stray format braces"
assert dark["bg"] in css and dark["header"] in css
assert "12pt" in css and "10pt" in css      # pt and pt-2 both present
assert "#gear" in css and "#grip" in css    # settings button + resize handle
assert "QScrollBar" in css                   # thin overflow scrollbar
panel = theme.panel_qss(theme.resolve("easy", "light"))
assert "#cap" in panel and "{{" not in panel

# ------------------------------------------------ UiState: size + appearance
assert valid_size([360, 220]) == (360, 220)
assert valid_size([0, 220]) is None, "non-positive rejected"
assert valid_size([-5, 10]) is None
assert valid_size([360.0, 220]) is None, "floats rejected (mirror valid_pos)"
assert valid_size([True, True]) is None
assert valid_size("360x220") is None
assert valid_size(None) is None
# valid_pos still behaves (regression alongside the new helper)
assert valid_pos([40, 140]) == (40, 140)

tmp = tempfile.mkdtemp()
try:
    path = os.path.join(tmp, "ui_state.json")
    st = UiState(path)
    # shipped appearance defaults
    assert st.get("appearance", "mode") == "dark"
    assert st.get("appearance", "palette") == "default"
    assert st.get("appearance", "font_pt") is None
    assert st.get("card", "size") is None

    st.set("appearance", "mode", "light")
    st.set("appearance", "palette", "colorblind")
    st.set("appearance", "font_pt", 14)
    st.set("card", "size", [420, 260])

    st2 = UiState(path)                          # round-trips from disk
    assert st2.get("appearance", "mode") == "light"
    assert st2.get("appearance", "palette") == "colorblind"
    assert st2.get("appearance", "font_pt") == 14
    assert valid_size(st2.get("card", "size")) == (420, 260)
    # unrelated defaults survive a partial write
    assert st2.get("card", "scale") == 1.0
    assert st2.get("card", "compact") is False

    # a stored appearance still resolves even if a future edit invalidates it
    assert theme.normalize(st2.get("appearance", "palette"),
                           st2.get("appearance", "mode")) == \
        ("colorblind", "light")

    with open(path, "w", encoding="utf-8") as f:
        f.write("{ broken json")
    st3 = UiState(path)                          # corrupt file -> defaults
    assert st3.get("appearance", "palette") == "default"
finally:
    import shutil
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  palettes: {', '.join(theme.PALETTE_ORDER)} x {len(theme.MODES)} modes")
