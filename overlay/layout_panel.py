"""Zone-layout panel (PyQt6): the Exile-UI "act decoder", overlay-native.

Shows every possible layout of the zone you just entered (hand-traced
images from the Exile-UI pack -- white outline, green path to the exit,
purple waypoint). Look at your minimap, left-click the variant that
matches and it stays pinned (with its continuation images) until the
next zone; right-click un-pins. Mouse wheel rescales, drag moves, both
persisted. Purely visual -- never sends input.

The panel drives itself off ('area', ...) watcher events, so it works
in every zone the pack covers even when the route card is elsewhere.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                             QWidget)

import theme
from ui_state import clamp_scale

BASE_IMG_W = 240        # on-screen width of one layout image at scale 1.0
DRAG_THRESHOLD = 6      # px of motion separating a drag from a click


class LayoutPanel(QWidget):
    def __init__(self, index, state=None, auto_show=True):
        super().__init__()
        self.index = index
        self._state = state
        self._auto_show = auto_show
        self._scale = (clamp_scale(state.get("layouts", "scale"))
                       if state else 1.0)
        self._area = None
        self._variants = []              # [(head, [paths])] for _area
        self._pinned = None              # head token, or None = show all
        self._user_hidden = False        # F7 latch; wins over auto_show
        self._drag = None
        self._dragged = False
        self._press_child = None
        self._thumbs = {}                # QLabel -> head token

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        card = QFrame(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self._inner = QVBoxLayout(card)
        self._inner.setContentsMargins(8, 6, 8, 8)
        self._inner.setSpacing(4)
        self.caption = QLabel(objectName="cap")
        self._inner.addWidget(self.caption)
        self.row = QHBoxLayout()
        self.row.setSpacing(6)
        self._inner.addLayout(self.row)

        # Match the card's chosen mode/palette (theme.py); the overlay
        # calls apply_theme() on later changes so the two never diverge.
        mode = state.get("appearance", "mode") if state else None
        palette = state.get("appearance", "palette") if state else None
        self.apply_theme(theme.resolve(palette, mode))

    def apply_theme(self, roles):
        """Restyle to a resolved theme.py role dict (from the card)."""
        self.setStyleSheet(theme.panel_qss(roles))

    # -- content ------------------------------------------------------------
    def set_area(self, area_id):
        """New instance generated. Re-entering the same zone keeps the
        pin (a new instance of The Coast is still The Coast); a different
        zone resets it."""
        if area_id == self._area:
            return
        self._area = area_id
        self._pinned = None
        self._variants = self.index.variants(area_id)
        if not self._variants:
            self.hide()
            return
        self._rebuild()
        if self._auto_show and not self._user_hidden:
            self.show()

    def _clear_row(self):
        # setParent(None) detaches NOW -- deleteLater alone leaves the
        # old thumbnails parented (still painted, still sizing the
        # window) until the event loop next spins, so a rebuild would
        # briefly show both generations and never shrink the window
        self._thumbs = {}
        while self.row.count():
            item = self.row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                sub = item.layout()
                while sub.count():
                    sw = sub.takeAt(0).widget()
                    if sw is not None:
                        sw.setParent(None)
                        sw.deleteLater()
                sub.deleteLater()

    def _rebuild(self):
        self._clear_row()
        if self._pinned is not None:
            shown = [v for v in self._variants if v[0] == self._pinned]
            self.caption.setText(
                f"{self._area} · variant {self._pinned} — right-click for all")
        else:
            shown = self._variants
            self.caption.setText(
                f"{self._area} — click your layout · wheel resizes"
                if len(shown) > 1 else f"{self._area} · wheel resizes")

        width = int(BASE_IMG_W * self._scale)
        added = []
        for head, paths in shown:
            # pinned: the head plus its continuations; unpinned: head only
            for path in (paths if self._pinned else paths[:1]):
                col = QVBoxLayout()
                col.setSpacing(2)
                if self._pinned is None and len(shown) > 1:
                    no = QLabel(head)
                    no.setProperty("variantNo", "true")
                    no.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                    col.addWidget(no)
                    added.append(no)
                    self._thumbs[no] = head    # clicking the number pins too
                img = QLabel()
                pix = QPixmap(path)
                if not pix.isNull():
                    img.setPixmap(pix.scaledToWidth(
                        width, Qt.TransformationMode.SmoothTransformation))
                self._thumbs[img] = head
                col.addWidget(img)
                col.addStretch(1)
                self.row.addLayout(col)
                added.append(img)

        # Qt defers all of this to the next event-loop pass: widgets
        # added to a visible parent stay hidden, and size hints keep
        # describing the previous contents -- the window would keep its
        # old (larger) footprint, a dead transparent margin that blocks
        # game clicks. Show and relayout synchronously instead.
        if self.isVisible():
            for w in added:
                w.show()
        for lay in (self.row, self._inner, self.layout()):
            lay.invalidate()
        self.layout().activate()
        self.adjustSize()

    # -- window behaviour -----------------------------------------------------
    def toggle_visible(self):
        """F7: hide/show. Hiding latches until F7 again -- entering the
        next zone must not undo an explicit 'go away'."""
        if self.isVisible():
            self._user_hidden = True
            self.hide()
        else:
            self._user_hidden = False
            if self._variants:
                self._rebuild()
                self.show()

    def toggle_clickthrough(self):
        was_visible = self.isVisible()
        self.setWindowFlags(self.windowFlags()
                            ^ Qt.WindowType.WindowTransparentForInput)
        if was_visible:
            self.show()

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self._dragged = False
        self._press_child = self.childAt(e.position().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        pos = e.globalPosition().toPoint() - self._drag
        if self._dragged or (pos - self.pos()).manhattanLength() > DRAG_THRESHOLD:
            self._dragged = True
            self.move(pos)

    def mouseReleaseEvent(self, e):
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if self._dragged:
            self._dragged = False
            if self._state:
                self._state.set("layouts", "pos", [self.x(), self.y()])
            return
        if e.button() == Qt.MouseButton.RightButton:
            if self._pinned is not None:
                self._pinned = None
                self._rebuild()
        elif e.button() == Qt.MouseButton.LeftButton:
            head = self._thumbs.get(self._press_child)
            if head is not None and self._pinned is None \
                    and len(self._variants) > 1:
                self._pinned = head
                self._rebuild()

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if not delta:
            return
        scale = clamp_scale(self._scale + (0.1 if delta > 0 else -0.1))
        if scale == self._scale:
            return
        self._scale = scale
        self._rebuild()
        if self._state:
            self._state.set("layouts", "scale", scale)
