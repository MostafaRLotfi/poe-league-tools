"""Always-on-top overlay card (PyQt6). Purely visual — never sends input.

Stays out of the way: the gear button (or the settings hotkey) opens the
appearance panel — dark/light mode, color palette, font size. The card is
freely resizable by the ◢ handle in the bottom-right corner — drag it
smaller (down to the act/title line plus one row of instructions) and the
step text scrolls inside whatever space is left. Mouse wheel over the card
zooms the font, double-click collapses it to the header line, and F6
(Windows) makes it click-through entirely.

Appearance is chosen here, persisted through UiState, and painted from
theme.py — a pure stdlib module (no Qt) that holds every palette and the
QSS builders, so the color/sizing logic is unit-tested headless.
"""
import html

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import theme
from build_notes import select_note, select_passives
from ui_state import clamp_scale, valid_size

# Resize bounds for the card. Wide enough to read a step, narrow enough to
# tuck into a screen edge; the minimum height is derived from the header +
# one instruction row at runtime so it tracks the current font size.
MIN_WIDTH, MAX_WIDTH = 200, 1200
MAX_HEIGHT = 2000


class ResizeGrip(QLabel):
    """A visible bottom-right resize handle (◢). Dragging it resizes the
    target window; kept as an explicit glyph rather than a bare QSizeGrip
    because the native grip lines are near-invisible on the translucent
    card and users could not find them."""

    def __init__(self, target):
        super().__init__("◢")
        self._target = target
        self._origin = None            # global press point
        self._start = None             # window size at press
        self.setObjectName("grip")
        self.setToolTip("Drag to resize")
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignBottom
                          | Qt.AlignmentFlag.AlignRight)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.globalPosition().toPoint()
            self._start = self._target.size()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            d = e.globalPosition().toPoint() - self._origin
            self._target.resize_to(self._start.width() + d.x(),
                                   self._start.height() + d.y())
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._origin is not None:
            self._origin = None
            self._target.persist_size()
            e.accept()


class OverlayWindow(QWidget):
    def __init__(self, cfg, state=None):
        super().__init__()
        self._drag = None
        self._dragged = False
        self._ready = False                  # gate size persistence to user acts
        self.level = 1
        self.notes = {}                      # act -> str or milestone rows
        self.passives = []                   # level-aware allocation rows
        self._party_text = ""
        self._flashing = False
        self._meta_bits = []                 # current step's meta lines
        self._status_text = ""               # run-tracker timer/XP bit
        self._last_render = None             # (step, progress, peek) for recolor
        self._panel_restyle = None           # optional layout-panel theme hook
        self._expanded_height = None         # remembered height for un-collapse

        self._state = state
        self._cfg = cfg
        self._base_width = cfg.get("width", 360)
        self._scale = clamp_scale(state.get("card", "scale")) if state else 1.0
        self._compact = bool(state.get("card", "compact")) if state else False

        # Appearance: mode/palette/font from UiState, font falling back to
        # config.json's font_pt so an install that never opens settings is
        # visually identical to before.
        saved_mode = state.get("appearance", "mode") if state else None
        saved_palette = state.get("appearance", "palette") if state else None
        self._palette, self._mode = theme.normalize(saved_palette, saved_mode)
        saved_pt = state.get("appearance", "font_pt") if state else None
        self._base_pt = theme.clamp_font(
            saved_pt if saved_pt is not None else cfg.get("font_pt", 11))
        self._roles = theme.resolve(self._palette, self._mode)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(cfg.get("opacity", 0.92))
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)

        card = QFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(14, 10, 14, 12)
        inner.setSpacing(6)

        # Header row: act/title/level line (left) + gear button (right).
        # Always visible -- it never scrolls, so the title stays put even
        # when the card is dragged down to a sliver.
        self.header = QLabel(objectName="hdr")
        self.header.setWordWrap(False)
        self.gear = QPushButton("⚙", objectName="gear")   # ⚙
        self.gear.setToolTip("Settings — mode, palette, font size")
        self.gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gear.setFixedWidth(22)
        self.gear.clicked.connect(self.open_settings)
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(4)
        hdr_row.addWidget(self.header, 1)
        hdr_row.addWidget(self.gear, 0, Qt.AlignmentFlag.AlignTop)
        inner.addLayout(hdr_row)

        # Scrollable body: everything under the title. When the card is
        # shorter than its contents a thin scrollbar appears and the text
        # scrolls instead of clipping or forcing the window taller.
        self.body = QLabel(wordWrap=True)
        self.meta = QLabel(wordWrap=True, objectName="meta")
        self.party = QLabel(wordWrap=True, objectName="party")
        self.item = QLabel(wordWrap=True, objectName="item")
        self.nxt = QLabel(wordWrap=True, objectName="next")
        content = QWidget()
        cbox = QVBoxLayout(content)
        cbox.setContentsMargins(0, 0, 0, 0)
        cbox.setSpacing(6)
        for w in (self.body, self.meta, self.item, self.nxt):
            cbox.addWidget(w)
        cbox.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("scroll")
        self.scroll.setWidget(content)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        # Wheel over the body must still zoom (the card's long-standing
        # gesture), not scroll -- scrolling is the scrollbar's job.
        self.scroll.viewport().installEventFilter(self)
        inner.addWidget(self.scroll, 1)

        # Party/status row stays OUTSIDE the scroll area: a death flash has
        # to punch through compact mode (where the body is hidden), so this
        # row must be reachable on its own.
        inner.addWidget(self.party)

        # Bottom row: the visible resize handle, pinned bottom-right.
        self.grip = ResizeGrip(self)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip_row.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignBottom
                           | Qt.AlignmentFlag.AlignRight)
        inner.addLayout(grip_row)

        self.party.setVisible(False)
        self.item.setVisible(False)          # transient item verdict line
        self._item_timer = QTimer(self)
        self._item_timer.setSingleShot(True)
        self._item_timer.timeout.connect(self._hide_item)
        # single restartable timer (same pattern as _item_timer): a new
        # flash cancels the pending restore, so overlapping flashes can
        # never cut each other short
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self._apply_style()
        if self._compact:
            self._apply_compact()

        # Restore the saved size (width + height are both user-controlled
        # now); a missing/corrupt size falls back to config width × zoom
        # and a natural content height.
        saved = valid_size(state.get("card", "size")) if state else None
        if saved:
            self.resize(self._clamp_w(saved[0]), self._clamp_h(saved[1]))
        else:
            self.resize(self._clamp_w(round(self._base_width * self._scale)),
                        self.sizeHint().height())
        self._ready = True

    # -- styling ----------------------------------------------------------
    def _apply_style(self):
        """(Re)build the style sheet for the current palette/mode/font."""
        pt = theme.effective_pt(self._base_pt, self._scale)
        self.setStyleSheet(theme.card_qss(self._roles, pt))
        self._update_min_height()

    def _update_min_height(self):
        """Floor the card at title + one instruction row (tracks the font).
        Relaxed to nothing while compact so it can collapse to the header."""
        line = self.fontMetrics().height()
        self.scroll.setMinimumHeight(line + 6)
        if self._compact:
            self.setMinimumHeight(0)
        else:
            self.setMinimumHeight(self.minimumSizeHint().height())

    def set_scale(self, value):
        value = clamp_scale(value)
        if value == self._scale:
            return
        self._scale = value
        self._apply_style()
        if self._state:
            self._state.set("card", "scale", value)

    # -- resize -----------------------------------------------------------
    def _clamp_w(self, w):
        return min(MAX_WIDTH, max(MIN_WIDTH, int(w)))

    def _clamp_h(self, h):
        # Clamp to the floor Qt actually enforces (set in _apply_style), so
        # the value we store round-trips to the same height on restore.
        floor = max(1, self.minimumHeight())
        return min(MAX_HEIGHT, max(floor, int(h)))

    def resize_to(self, w, h):
        """Clamped resize driven by the corner grip."""
        self.resize(self._clamp_w(w), self._clamp_h(h))

    def persist_size(self):
        if self._state and not self._compact:
            self._state.set("card", "size", [self.width(), self.height()])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._ready and not self._compact:
            self.persist_size()

    def eventFilter(self, obj, ev):
        # Keep wheel = zoom everywhere on the card, including over the
        # scrollable body; the scrollbar handles scrolling.
        if ev.type() == QEvent.Type.Wheel and obj is self.scroll.viewport():
            self.wheelEvent(ev)
            return True
        return super().eventFilter(obj, ev)

    # -- appearance (settings dialog) -------------------------------------
    def current_appearance(self):
        """(mode, palette, font_pt) for the settings dialog to seed from."""
        return self._mode, self._palette, self._base_pt

    def apply_appearance(self, mode, palette, font_pt):
        """Live-apply and persist mode/palette/font from the dialog."""
        self._palette, self._mode = theme.normalize(palette, mode)
        self._base_pt = theme.clamp_font(font_pt)
        self._roles = theme.resolve(self._palette, self._mode)
        if self._state:
            self._state.set("appearance", "mode", self._mode)
            self._state.set("appearance", "palette", self._palette)
            self._state.set("appearance", "font_pt", self._base_pt)
        self._apply_style()
        self._recolor()                      # re-render spans in new colors
        if self._panel_restyle:              # keep the layout panel in step
            self._panel_restyle(self._roles)

    def set_panel_restyler(self, callback):
        """main() wires this to restyle the zone-layout panel together with
        the card, so a theme change never leaves a mismatched window."""
        self._panel_restyle = callback

    def open_settings(self):
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.show()                           # modeless: tweak while playing
        dlg.raise_()
        dlg.activateWindow()

    def _recolor(self):
        """Repaint the already-rendered card (header kind color, party row)
        after a palette/mode change, without needing the route engine."""
        if self._last_render is not None:
            self.show_step(*self._last_render)
        if not self._flashing:
            self.set_party(self._party_text)

    # -- content ----------------------------------------------------------
    def show_step(self, step, progress, peek):
        self._last_render = (step, progress, peek)
        n, total, act = progress
        color = theme.kind_color(self._roles, step.get("kind", "travel"))
        self.header.setText(
            f"ACT {act} · {n}/{total} · "
            f"<span style='color:{color}'><b>{step.get('zone', '')}</b></span>"
            f" · lvl {self.level}")
        self.body.setText("<br>".join(f"• {d}" for d in step.get("do", [])))

        bits = []
        if step.get("layout"):
            bits.append(f"◆ {step['layout']}")
        if step.get("tip"):
            bits.append(f"✦ {step['tip']}")
        note = select_note(self.notes.get(act), self.level)
        if note:
            bits.append(f"⚙ {note}")
        passive = select_passives(self.passives, self.level)
        if passive:
            bits.append(f"◇ {html.escape(passive)}")
        self._meta_bits = bits
        self._render_meta()
        self.nxt.setText(f"next: {peek['zone']}" if peek else "— end of route —")

    def set_level(self, lvl):
        self.level = lvl

    def set_notes(self, notes):
        self.notes = notes

    def set_passives(self, passives):
        self.passives = passives

    # -- run tracker status (timer splits + XP warning) ----------------------
    def set_status(self, text):
        """Run-tracker bit appended to the meta row, e.g.
        'A3 41:22 (-2:10 PB)  ⚠ XP -38%'. Empty string hides it."""
        if text == self._status_text:
            return
        self._status_text = text
        self._render_meta()

    def _render_meta(self):
        bits = list(self._meta_bits)
        if self._status_text:
            bits.append(f"⏱ {self._status_text}")
        self.meta.setText("<br>".join(bits))
        self.meta.setVisible(bool(bits) and not self._compact)

    # -- clipboard item verdict (transient, separate from the death flash) ---
    def show_item(self, verdict, name, reason, ms=6000):
        """Show a color-coded item verdict for ~6 s on its own line.

        Uses a dedicated label + timer so it can never clobber a death
        flash on the party row; a new verdict simply replaces the old.
        """
        color = theme.verdict_color(self._roles, verdict)
        self.item.setText(
            f"<span style='color:{color}'><b>{html.escape(str(verdict))}"
            f"</b></span> {html.escape(str(name))} "
            f"<span style='color:{self._roles['meta']}'>— "
            f"{html.escape(str(reason))}</span>")
        self.item.setVisible(not self._compact)
        self._item_timer.start(ms)

    def _hide_item(self):
        self.item.setVisible(False)

    # -- party row ----------------------------------------------------------
    def set_party(self, text):
        """Persistent party status line; hidden when empty."""
        self._party_text = text
        if not self._flashing:
            self.party.setText(text)
            self.party.setVisible(bool(text) and not self._compact)

    def flash(self, text, ms=6000):
        """Show an urgent message on the party row, then restore it.
        A newer flash restarts the timer (full display time) instead of
        being wiped early by the previous flash's timeout. Deliberately
        breaks through compact mode (a death is worth the pixels)."""
        self._flashing = True
        self.party.setText(
            f"<span style='color:{self._roles['flash']}'>{text}</span>")
        self.party.setVisible(True)          # punches through compact mode
        self._flash_timer.start(ms)

    def _end_flash(self):
        self._flashing = False
        if self._compact:
            self.party.setVisible(False)     # back to a bare header line
        else:
            self.set_party(self._party_text)

    # -- window behaviour ---------------------------------------------------
    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def toggle_compact(self):
        """Collapse to the header line only (double-click toggles).

        A one-action 'get out of my way': the act/title/level line stays,
        the scrollable body is hidden and the card shrinks to fit. The
        previous height is restored on the way back out.
        """
        self._compact = not self._compact
        self._apply_compact()
        if self._state:
            self._state.set("card", "compact", self._compact)

    def _apply_compact(self):
        if self._compact:
            self._expanded_height = self.height()
            self.scroll.setVisible(False)
            self.grip.setVisible(False)
            self.party.setVisible(self._flashing)   # only a live death shows
            self._update_min_height()        # drop the floor so it collapses
            self.adjustSize()                # shrink to the header line
        else:
            self.scroll.setVisible(True)
            self.grip.setVisible(True)
            self._render_meta()
            if not self._flashing:
                self.party.setVisible(bool(self._party_text))
            self._update_min_height()        # restore the title + row floor
            if self._expanded_height:
                self.resize(self.width(), self._clamp_h(self._expanded_height))

    def toggle_clickthrough(self):
        # setWindowFlags hides the window as a side effect; only re-show
        # it if it was visible, so a global hotkey pressed while the
        # overlay is hidden (F4) does not force it back onto the screen.
        was_visible = self.isVisible()
        self.setWindowFlags(self.windowFlags()
                            ^ Qt.WindowType.WindowTransparentForInput)
        if was_visible:
            self.show()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._dragged = True

    def mouseReleaseEvent(self, e):
        self._drag = None
        if self._dragged and self._state:
            self._state.set("card", "pos", [self.x(), self.y()])
        self._dragged = False

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_compact()

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if delta:
            self.set_scale(self._scale + (0.1 if delta > 0 else -0.1))
