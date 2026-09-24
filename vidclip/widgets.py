"""Reusable widgets: video view, event timeline, behavior editor, settings."""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .project import Behavior, DEFAULT_COLORS, ExportSettings, safe_name

# Keys the app uses for navigation; behavior hotkeys must be other characters.
RESERVED_CHARS = {" "}


# ============================================================== video display
class VideoView(QLabel):
    """Displays a BGR frame scaled to fit, keeping aspect ratio."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background:#111; color:#888;")
        self.setFocusPolicy(Qt.StrongFocus)
        self._frame: np.ndarray | None = None
        self.setText("Open a project and select a video")

    def show_frame(self, frame: np.ndarray | None):
        self._frame = frame
        self._render()

    def clear_frame(self, text=""):
        self._frame = None
        self.clear()
        self.setText(text)

    def _render(self):
        if self._frame is None:
            return
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pm = QPixmap.fromImage(img).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()


# ================================================================== timeline
class Timeline(QWidget):
    """Whole-video timeline: one lane per behavior, click/drag to seek."""
    seek_requested = Signal(int)

    LANE_H = 10
    TOP = 18

    def __init__(self):
        super().__init__()
        self.n_frames = 1
        self.pos = 0
        self.behaviors: list[Behavior] = []
        self.events = []            # list[Event]
        self.open_events = {}       # behavior -> start frame
        self.selected = None        # (behavior, number)
        self.setMouseTracking(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._update_height()

    def _update_height(self):
        lanes = max(1, len(self.behaviors))
        self.setFixedHeight(self.TOP + lanes * (self.LANE_H + 2) + 6)

    def set_data(self, n_frames, behaviors, events, open_events, pos, selected=None):
        self.n_frames = max(1, n_frames)
        self.behaviors = list(behaviors)
        self.events = events
        self.open_events = open_events
        self.pos = pos
        self.selected = selected
        self._update_height()
        self.update()

    def set_pos(self, pos):
        self.pos = pos
        self.update()

    def _x(self, frame):
        return 6 + (self.width() - 12) * frame / max(1, self.n_frames - 1)

    def _frame_at(self, x):
        f = (x - 6) / max(1, self.width() - 12) * (self.n_frames - 1)
        return int(round(max(0, min(self.n_frames - 1, f))))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor("#1e1e1e"))
        # tick bar
        p.fillRect(QRectF(6, 6, self.width() - 12, 6), QColor("#444"))
        p.fillRect(QRectF(6, 6, self._x(self.pos) - 6, 6), QColor("#888"))
        lane_of = {b.name: i for i, b in enumerate(self.behaviors)}
        color_of = {b.name: QColor(b.color) for b in self.behaviors}
        for i in range(len(self.behaviors)):
            y = self.TOP + i * (self.LANE_H + 2)
            p.fillRect(QRectF(6, y, self.width() - 12, self.LANE_H), QColor("#2a2a2a"))
        for e in self.events:
            lane = lane_of.get(e.behavior)
            if lane is None:
                continue
            y = self.TOP + lane * (self.LANE_H + 2)
            x0, x1 = self._x(e.start_frame), self._x(e.end_frame)
            p.fillRect(QRectF(x0, y, max(2, x1 - x0), self.LANE_H), color_of[e.behavior])
            if self.selected == (e.behavior, e.number):
                p.setPen(QPen(QColor("white"), 2))
                p.drawRect(QRectF(x0, y, max(2, x1 - x0), self.LANE_H))
        for name, start in self.open_events.items():
            lane = lane_of.get(name)
            if lane is None:
                continue
            y = self.TOP + lane * (self.LANE_H + 2)
            a, b = sorted((start, self.pos))
            c = QColor(color_of[name]); c.setAlpha(110)
            p.fillRect(QRectF(self._x(a), y, max(2, self._x(b) - self._x(a)), self.LANE_H),
                       QBrush(c, Qt.Dense4Pattern))
        # playhead
        p.setPen(QPen(QColor("#ffd400"), 2))
        x = self._x(self.pos)
        p.drawLine(int(x), 2, int(x), self.height() - 2)

    def mousePressEvent(self, e):
        self.seek_requested.emit(self._frame_at(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.seek_requested.emit(self._frame_at(e.position().x()))


# =========================================================== behavior editor
class BehaviorDialog(QDialog):
    """Add / rename / re-key / recolor / remove behaviors."""

    def __init__(self, behaviors: list[Behavior], used_counts: dict[str, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit behaviors")
        self.resize(520, 380)
        self.used_counts = used_counts
        # rows: [original_name or None, name, key, color]
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Behavior name", "Hotkey", "Color"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for b in behaviors:
            self._add_row(b.name, b.key, b.color, original=b.name)

        add = QPushButton("Add behavior")
        rem = QPushButton("Remove selected")
        up = QPushButton("▲"); down = QPushButton("▼")
        add.clicked.connect(self._on_add)
        rem.clicked.connect(self._on_remove)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout()
        for w in (add, rem, up, down):
            row.addWidget(w)
        row.addStretch()

        hint = QLabel("Hotkey = one character (letter, digit or symbol). Press it once to "
                      "mark the start of a behavior and again to mark the end. "
                      "Space and arrow keys are reserved for playback.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(hint)
        lay.addWidget(bb)
        self.result_behaviors: list[Behavior] = []
        self.renames: dict[str, str] = {}

    def _add_row(self, name, key, color, original=None):
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(name)
        it.setData(Qt.UserRole, original)
        self.table.setItem(r, 0, it)
        ke = QLineEdit(key)
        ke.setMaxLength(1)
        ke.setAlignment(Qt.AlignCenter)
        self.table.setCellWidget(r, 1, ke)
        cb = QPushButton()
        cb.setProperty("color", color)
        cb.setStyleSheet(f"background:{color};")
        cb.clicked.connect(lambda _=False, b=cb: self._pick_color(b))
        self.table.setCellWidget(r, 2, cb)

    def _pick_color(self, btn):
        c = QColorDialog.getColor(QColor(btn.property("color")), self)
        if c.isValid():
            btn.setProperty("color", c.name())
            btn.setStyleSheet(f"background:{c.name()};")

    def _rows(self):
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            out.append((it.data(Qt.UserRole), it.text().strip(),
                        self.table.cellWidget(r, 1).text(),
                        self.table.cellWidget(r, 2).property("color")))
        return out

    def _on_add(self):
        used = {k.lower() for _, _, k, _ in self._rows()}
        key = next((c for c in "123456789qwertyuiopasdfghjklzxcvbnm" if c not in used), "")
        color = DEFAULT_COLORS[self.table.rowCount() % len(DEFAULT_COLORS)]
        self._add_row(f"behavior{self.table.rowCount() + 1}", key, color)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _on_remove(self):
        r = self.table.currentRow()
        if r < 0:
            return
        orig = self.table.item(r, 0).data(Qt.UserRole)
        n = self.used_counts.get(orig, 0) if orig else 0
        if n:
            ans = QMessageBox.question(
                self, "Remove behavior",
                f"'{orig}' has {n} annotated event(s). They stay in the CSV files but "
                f"won't be shown or exportable until the behavior is re-added.\n\nRemove anyway?")
            if ans != QMessageBox.Yes:
                return
        self.table.removeRow(r)

    def _move(self, d):
        r = self.table.currentRow()
        t = r + d
        if r < 0 or not (0 <= t < self.table.rowCount()):
            return
        rows = self._rows()
        rows[r], rows[t] = rows[t], rows[r]
        self.table.setRowCount(0)
        for orig, name, key, color in rows:
            self._add_row(name, key, color, original=orig)
        self.table.selectRow(t)

    def _on_ok(self):
        rows = self._rows()
        names, keys = set(), set()
        result, renames = [], {}
        for orig, name, key, color in rows:
            if not name:
                return self._err("Every behavior needs a name.")
            if safe_name(name) != name:
                return self._err(f"'{name}': use only letters, digits, '-', '_' or '.' "
                                 f"(it becomes part of clip filenames). Suggested: '{safe_name(name)}'.")
            if name.lower() in names:
                return self._err(f"Duplicate behavior name '{name}'.")
            if not key or key in RESERVED_CHARS:
                return self._err(f"'{name}' needs a hotkey (one character, not space).")
            k = key.lower()
            if k in keys:
                return self._err(f"Hotkey '{key}' is used more than once.")
            names.add(name.lower()); keys.add(k)
            result.append(Behavior(name=name, key=k, color=color))
            if orig and orig != name:
                renames[orig] = name
        self.result_behaviors, self.renames = result, renames
        self.accept()

    def _err(self, msg):
        QMessageBox.warning(self, "Behaviors", msg)


# ========================================================== export settings
class ExportSettingsDialog(QDialog):
    def __init__(self, settings: ExportSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export settings")
        self.crf = QSpinBox(); self.crf.setRange(0, 40); self.crf.setValue(settings.crf)
        self.crf.setToolTip("x264 CRF: 0 = lossless, 18 ≈ visually lossless, 23 = default, higher = smaller files")
        self.preset = QComboBox()
        self.preset.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast",
                              "medium", "slow", "slower", "veryslow"])
        self.preset.setCurrentText(settings.preset)
        form = QFormLayout(self)
        form.addRow("Quality (CRF)", self.crf)
        form.addRow("Encoder preset", self.preset)
        note = QLabel("Clips are H.264 .mp4, video only, at the source frame rate.")
        note.setStyleSheet("color:#888;")
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> ExportSettings:
        return ExportSettings(crf=self.crf.value(), preset=self.preset.currentText())
