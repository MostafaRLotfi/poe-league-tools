"""Overlay settings dialog (PyQt6): mode, palette and font size.

A small always-on-top panel opened by the gear button (or the settings
hotkey, the only way to reach it while the card is click-through). Every
control applies live via callbacks on OverlayWindow and is persisted by
it -- this dialog owns no state of its own, so it can be opened, changed
and closed freely. Purely visual; never sends input to the game.

theme.py (pure) holds the palette list and bounds this reads, so the
choices here and the colors they map to can be tested without Qt.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFormLayout,
                             QHBoxLayout, QLabel, QRadioButton, QSpinBox,
                             QVBoxLayout)

import theme


class SettingsDialog(QDialog):
    """Live appearance editor. `overlay` must expose current_appearance()
    -> (mode, palette, font_pt) and apply_appearance(mode, palette,
    font_pt); the latter persists and restyles both windows."""

    def __init__(self, overlay):
        super().__init__(overlay)
        self._overlay = overlay
        self.setWindowTitle("Overlay settings")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowStaysOnTopHint)

        mode, palette, font_pt = overlay.current_appearance()

        form = QFormLayout(self)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        # -- dark / light -------------------------------------------------
        self._dark = QRadioButton("Dark")
        self._light = QRadioButton("Light")
        grp = QButtonGroup(self)
        grp.addButton(self._dark)
        grp.addButton(self._light)
        (self._dark if mode == "dark" else self._light).setChecked(True)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(self._dark)
        mode_row.addWidget(self._light)
        mode_row.addStretch(1)
        form.addRow("Mode", _wrap(mode_row))
        self._dark.toggled.connect(self._changed)

        # -- palette ------------------------------------------------------
        self._palette = QComboBox()
        for name in theme.PALETTE_ORDER:
            self._palette.addItem(theme.palette_label(name), name)
        i = self._palette.findData(palette)
        self._palette.setCurrentIndex(i if i >= 0 else 0)
        self._palette.currentIndexChanged.connect(self._changed)
        form.addRow("Color palette", self._palette)

        # -- font size ----------------------------------------------------
        self._font = QSpinBox()
        self._font.setRange(theme.FONT_MIN, theme.FONT_MAX)
        self._font.setSuffix(" pt")
        self._font.setValue(theme.clamp_font(font_pt))
        self._font.valueChanged.connect(self._changed)
        form.addRow("Font size", self._font)

        hint = QLabel("Drag the corner grip to resize · wheel to zoom · "
                      "double-click to collapse")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 8pt;")
        form.addRow(hint)

    def _changed(self, *_):
        self._overlay.apply_appearance(
            "dark" if self._dark.isChecked() else "light",
            self._palette.currentData(),
            self._font.value())


def _wrap(layout):
    from PyQt6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(layout)
    return w
