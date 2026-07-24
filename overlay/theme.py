"""Overlay appearance: dark/light mode, font size, and color palettes.

Pure stdlib -- no Qt imports -- so the whole palette/QSS layer is unit
tested headless (tests/test_theme.py), the same split as ui_state.py.
overlay_window.py and layout_panel.py import this to build their style
sheets and to color inline spans (step kinds, item verdicts, deaths).

A *palette* names an accessibility intent (default, high contrast,
color-blind friendly, easy on the eyes); each carries a full role map
for both *modes* (dark / light). `resolve(palette, mode)` returns the
role dict; `card_qss` / `panel_qss` turn a role dict + point size into a
Qt style sheet; `effective_pt` folds the wheel-zoom scale into the base
font size the same way everywhere.
"""
from __future__ import annotations

# Semantic roles every palette/mode must define. Kept explicit (not
# derived) so a palette reads as exactly what it paints and a test can
# assert none are missing.
ROLE_KEYS = (
    "bg", "border", "text", "header", "meta", "party", "item", "next",
    "flash", "kind_kill", "kind_town", "kind_trial", "kind_travel",
    "verdict_take", "verdict_skip", "verdict_check",
)

# step "kind" -> role key (overlay_window colors the zone name by it)
_KIND_ROLE = {"kill": "kind_kill", "town": "kind_town",
              "trial": "kind_trial", "travel": "kind_travel"}
# item-evaluator verdict -> role key
_VERDICT_ROLE = {"TAKE": "verdict_take", "SKIP": "verdict_skip",
                 "CHECK": "verdict_check"}

FONT_STACK = "'Segoe UI', 'Helvetica Neue', sans-serif"

# Font-size bounds for the settings control (points, pre-zoom).
FONT_MIN, FONT_MAX = 7, 22

# --------------------------------------------------------------- palettes
# Each palette: {"label": str, "dark": {roles}, "light": {roles}}.
# "default" mirrors the original hand-tuned overlay look so nothing
# changes for users who never open settings.

PALETTES = {
    "default": {
        "label": "Default",
        "dark": {
            "bg": "rgba(14, 16, 21, 235)", "border": "#2a2f3a",
            "text": "#dfe5ee", "header": "#8fa3bf", "meta": "#9aa4b2",
            "party": "#b7c0cc", "item": "#dfe5ee", "next": "#6d7683",
            "flash": "#e46a6a",
            "kind_kill": "#e46a6a", "kind_town": "#6ab0e4",
            "kind_trial": "#c9a44a", "kind_travel": "#9aa4b2",
            "verdict_take": "#7ec27d", "verdict_skip": "#8a93a0",
            "verdict_check": "#c9a44a",
        },
        "light": {
            "bg": "rgba(248, 249, 251, 242)", "border": "#c2c8d2",
            "text": "#1c2430", "header": "#4a5568", "meta": "#5a6472",
            "party": "#3a4452", "item": "#1c2430", "next": "#8a93a0",
            "flash": "#c02626",
            "kind_kill": "#c0392b", "kind_town": "#2b6cb0",
            "kind_trial": "#9a7b1a", "kind_travel": "#6a7280",
            "verdict_take": "#2f855a", "verdict_skip": "#718096",
            "verdict_check": "#9a7b1a",
        },
    },
    "high_contrast": {
        "label": "High contrast",
        "dark": {
            "bg": "rgba(0, 0, 0, 255)", "border": "#ffffff",
            "text": "#ffffff", "header": "#ffff00", "meta": "#ffffff",
            "party": "#ffffff", "item": "#ffffff", "next": "#cfcfcf",
            "flash": "#ff4040",
            "kind_kill": "#ff5555", "kind_town": "#55aaff",
            "kind_trial": "#ffd000", "kind_travel": "#ffffff",
            "verdict_take": "#40ff40", "verdict_skip": "#cfcfcf",
            "verdict_check": "#ffd000",
        },
        "light": {
            "bg": "rgba(255, 255, 255, 255)", "border": "#000000",
            "text": "#000000", "header": "#0000cc", "meta": "#000000",
            "party": "#000000", "item": "#000000", "next": "#333333",
            "flash": "#cc0000",
            "kind_kill": "#cc0000", "kind_town": "#0000cc",
            "kind_trial": "#a06000", "kind_travel": "#000000",
            "verdict_take": "#007000", "verdict_skip": "#333333",
            "verdict_check": "#a06000",
        },
    },
    # Okabe-Ito colour-blind-safe hues (distinguishable for deuter-,
    # prot- and tritanopia): vermillion=danger, bluish-green=take,
    # orange=caution, sky-blue=town.
    "colorblind": {
        "label": "Color-blind friendly",
        "dark": {
            "bg": "rgba(14, 16, 21, 235)", "border": "#3a4150",
            "text": "#f0f0f0", "header": "#56b4e9", "meta": "#c7ccd4",
            "party": "#e6e8ec", "item": "#f0f0f0", "next": "#8a919c",
            "flash": "#d55e00",
            "kind_kill": "#d55e00", "kind_town": "#56b4e9",
            "kind_trial": "#e69f00", "kind_travel": "#cc79a7",
            "verdict_take": "#009e73", "verdict_skip": "#9aa4b2",
            "verdict_check": "#e69f00",
        },
        "light": {
            "bg": "rgba(250, 250, 250, 244)", "border": "#b8bec8",
            "text": "#1a1a1a", "header": "#0072b2", "meta": "#4a4f57",
            "party": "#2a2e35", "item": "#1a1a1a", "next": "#7a818c",
            "flash": "#b34d00",
            "kind_kill": "#b34d00", "kind_town": "#0072b2",
            "kind_trial": "#8a6000", "kind_travel": "#8a4f78",
            "verdict_take": "#00795a", "verdict_skip": "#6a7078",
            "verdict_check": "#8a6000",
        },
    },
    # Warm, low-glare tones for long sessions (dim amber on soft dark /
    # sepia on cream) -- reduced blue and reduced contrast.
    "easy": {
        "label": "Easy on the eyes",
        "dark": {
            "bg": "rgba(28, 26, 23, 236)", "border": "#3a352d",
            "text": "#d8cbb8", "header": "#b09a72", "meta": "#9c8f78",
            "party": "#c2b39a", "item": "#d8cbb8", "next": "#6f6656",
            "flash": "#cf7a5a",
            "kind_kill": "#cf7a5a", "kind_town": "#7fa6a0",
            "kind_trial": "#c2a24a", "kind_travel": "#9c8f78",
            "verdict_take": "#8faa72", "verdict_skip": "#8a8272",
            "verdict_check": "#c2a24a",
        },
        "light": {
            "bg": "rgba(247, 241, 229, 244)", "border": "#d8cdb5",
            "text": "#4a4336", "header": "#7a6f52", "meta": "#6f6656",
            "party": "#574f3f", "item": "#4a4336", "next": "#9a8f72",
            "flash": "#a85a3a",
            "kind_kill": "#a85a3a", "kind_town": "#3a7a72",
            "kind_trial": "#8a6a1a", "kind_travel": "#7a7058",
            "verdict_take": "#5a7a3a", "verdict_skip": "#7a7058",
            "verdict_check": "#8a6a1a",
        },
    },
}

# Menu order for the settings dropdown (stable, default first).
PALETTE_ORDER = ["default", "high_contrast", "colorblind", "easy"]
MODES = ("dark", "light")

DEFAULT_PALETTE = "default"
DEFAULT_MODE = "dark"


def palette_label(name: str) -> str:
    return PALETTES.get(name, PALETTES[DEFAULT_PALETTE])["label"]


def normalize(palette: str, mode: str) -> tuple[str, str]:
    """Fall back to the shipped defaults for anything unrecognized, so a
    hand-edited or older state file can never crash the overlay."""
    if palette not in PALETTES:
        palette = DEFAULT_PALETTE
    if mode not in MODES:
        mode = DEFAULT_MODE
    return palette, mode


def resolve(palette: str, mode: str) -> dict:
    """(palette, mode) -> role dict. Always returns every ROLE_KEYS key."""
    palette, mode = normalize(palette, mode)
    return dict(PALETTES[palette][mode])


def effective_pt(base_pt, scale) -> int:
    """Fold the wheel-zoom scale into the base point size (floor 6pt),
    the single source of truth both windows size their fonts by."""
    try:
        base_pt = float(base_pt)
        scale = float(scale)
    except (TypeError, ValueError):
        base_pt, scale = 11.0, 1.0
    return max(6, round(base_pt * scale))


def clamp_font(value) -> int:
    """Clamp a settings font size into [FONT_MIN, FONT_MAX]."""
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return 11
    return min(FONT_MAX, max(FONT_MIN, value))


def kind_color(roles: dict, kind: str) -> str:
    return roles.get(_KIND_ROLE.get(kind, "kind_travel"), roles["kind_travel"])


def verdict_color(roles: dict, verdict: str) -> str:
    return roles.get(_VERDICT_ROLE.get(verdict, "verdict_skip"),
                     roles["verdict_skip"])


def card_qss(roles: dict, pt: int) -> str:
    """Style sheet for the route card, given resolved roles + point size.

    The gear button and size grip are styled here too so the whole card
    re-themes from one string on every mode/palette/font change.
    """
    return f"""
        #card {{ background: {roles['bg']};
                 border: 1px solid {roles['border']}; border-radius: 10px; }}
        QLabel {{ color: {roles['text']}; font-size: {pt}pt;
                  font-family: {FONT_STACK}; }}
        #hdr  {{ color: {roles['header']}; font-size: {pt - 2}pt;
                 letter-spacing: 1px; }}
        #meta {{ color: {roles['meta']}; font-size: {pt - 2}pt; }}
        #party {{ color: {roles['party']}; font-size: {pt - 2}pt; }}
        #item {{ color: {roles['item']}; font-size: {pt - 1}pt; }}
        #next {{ color: {roles['next']}; font-size: {pt - 2}pt; }}
        #gear {{ color: {roles['header']}; font-size: {pt}pt;
                 border: none; background: transparent; padding: 0 2px; }}
        #gear:hover {{ color: {roles['text']}; }}
        #scroll {{ background: transparent; border: none; }}
        #scroll > QWidget > QWidget {{ background: transparent; }}
        /* The corner resize handle: a visible ◢ glyph, brightening on
           hover so it reads clearly as a drag target. */
        #grip {{ color: {roles['header']}; font-size: {pt + 3}pt;
                 padding: 0 1px 0 6px; }}
        #grip:hover {{ color: {roles['text']}; }}
        QScrollBar:vertical {{ background: transparent; width: 8px;
                 margin: 0; }}
        QScrollBar::handle:vertical {{ background: {roles['border']};
                 border-radius: 4px; min-height: 24px; }}
        QScrollBar::handle:vertical:hover {{ background: {roles['header']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                 height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                 background: transparent; }}
    """


def panel_qss(roles: dict, pt: int = 8) -> str:
    """Style sheet for the zone-layout panel, so it matches the card's
    mode/palette instead of staying dark under a light theme."""
    return f"""
        #card {{ background: {roles['bg']};
                 border: 1px solid {roles['border']}; border-radius: 10px; }}
        #cap  {{ color: {roles['header']}; font-size: {pt}pt;
                 letter-spacing: 1px; font-family: {FONT_STACK}; }}
        QLabel[variantNo="true"] {{ color: {roles['kind_trial']};
                 font-size: {pt + 2}pt; font-weight: bold; }}
    """
