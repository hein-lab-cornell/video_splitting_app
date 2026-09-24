"""Main application window."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings, QEvent
from PySide6.QtGui import QAction, QColor, QKeySequence, QPixmap, QIcon, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressDialog,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QStyle,
)

from .exporter import ClipJob, ExportWorker, find_ffmpeg
from .project import Event, Project, PROJECT_FILE, VIDEO_EXTS
from .video import VideoReader
from .widgets import BehaviorDialog, ExportSettingsDialog, Timeline, VideoView

SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
DEFAULT_SPEED_IDX = 3
TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)

SHORTCUT_HELP = """
<b>Playback</b><br>
Space &nbsp;— play / pause<br>
← / → &nbsp;— step 1 frame back / forward<br>
Shift + ← / → &nbsp;— step 10 frames<br>
↑ / ↓ &nbsp;— faster / slower (0.1× … 8×)<br>
Home / End &nbsp;— first / last frame<br><br>
<b>Annotating</b><br>
Behavior hotkey &nbsp;— 1st press marks START, 2nd press marks END<br>
Esc &nbsp;— cancel any behaviors still open<br>
Ctrl/Cmd + Z &nbsp;— remove the most recently added event<br>
Double-click an event &nbsp;— jump to its start<br><br>
Hotkeys are ignored while typing in a text/number box.
"""


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def swatch(color: str) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(QColor(color))
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Behavior Clipper")
        self.resize(1400, 850)
        self.settings = QSettings("BehaviorClipper", "BehaviorClipper")

        self.project: Project | None = None
        self.reader: VideoReader | None = None
        self.video_path: Path | None = None
        self.events: list[Event] = []
        self.open_events: dict[str, int] = {}
        self.playing = False
        self.speed_idx = DEFAULT_SPEED_IDX
        self._play_t0 = 0.0
        self._play_f0 = 0
        self.worker: ExportWorker | None = None

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)

        self._build_ui()
        self._build_menus()
        QApplication.instance().installEventFilter(self)
        self._set_enabled(False)

        last = self.settings.value("last_project", "")
        if last and (Path(last) / PROJECT_FILE).exists():
            self.load_project(Path(last))

    # ================================================================== UI
    def _build_ui(self):
        # ---- left: videos
        self.video_list = QListWidget()
        self.video_list.itemDoubleClicked.connect(self._on_video_activated)
        self.video_list.itemActivated.connect(self._on_video_activated)
        b_add = QPushButton("Add videos…")
        b_addf = QPushButton("Add folder…")
        b_rem = QPushButton("Remove")
        b_add.clicked.connect(self.add_videos)
        b_addf.clicked.connect(self.add_video_folder)
        b_rem.clicked.connect(self.remove_video)
        left = QGroupBox("Videos (double-click to open)")
        ll = QVBoxLayout(left)
        ll.addWidget(self.video_list)
        r = QHBoxLayout(); r.addWidget(b_add); r.addWidget(b_addf); r.addWidget(b_rem)
        ll.addLayout(r)
        self.project_label = QLabel("No project open")
        self.project_label.setWordWrap(True)
        self.project_label.setStyleSheet("color:#888;")
        ll.addWidget(self.project_label)

        # ---- center: video + transport
        self.view = VideoView()
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.seek)

        st = self.style()
        self.b_first = QPushButton(); self.b_first.setIcon(st.standardIcon(QStyle.SP_MediaSkipBackward))
        self.b_back = QPushButton(); self.b_back.setIcon(st.standardIcon(QStyle.SP_MediaSeekBackward))
        self.b_play = QPushButton(); self.b_play.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.b_fwd = QPushButton(); self.b_fwd.setIcon(st.standardIcon(QStyle.SP_MediaSeekForward))
        self.b_last = QPushButton(); self.b_last.setIcon(st.standardIcon(QStyle.SP_MediaSkipForward))
        self.b_first.setToolTip("First frame (Home)")
        self.b_back.setToolTip("Previous frame (←)")
        self.b_play.setToolTip("Play / pause (Space)")
        self.b_fwd.setToolTip("Next frame (→)")
        self.b_last.setToolTip("Last frame (End)")
        self.b_first.clicked.connect(lambda: self.seek(0))
        self.b_back.clicked.connect(lambda: self.step(-1))
        self.b_play.clicked.connect(self.toggle_play)
        self.b_fwd.clicked.connect(lambda: self.step(1))
        self.b_last.clicked.connect(lambda: self.seek(10**12))

        self.speed_box = QComboBox()
        self.speed_box.addItems([f"{s:g}×" for s in SPEEDS])
        self.speed_box.setCurrentIndex(self.speed_idx)
        self.speed_box.currentIndexChanged.connect(self.set_speed)
        self.speed_box.setToolTip("Playback speed (↑ / ↓)")

        self.goto_box = QSpinBox()
        self.goto_box.setRange(0, 0)
        self.goto_box.setKeyboardTracking(False)
        self.goto_box.setToolTip("Go to frame (type a number, press Enter)")
        self.goto_box.editingFinished.connect(self._on_goto)

        self.pos_label = QLabel("—")
        self.pos_label.setStyleSheet("font-family: monospace;")

        tr = QHBoxLayout()
        for w in (self.b_first, self.b_back, self.b_play, self.b_fwd, self.b_last):
            tr.addWidget(w)
        tr.addSpacing(12)
        tr.addWidget(QLabel("Speed")); tr.addWidget(self.speed_box)
        tr.addSpacing(12)
        tr.addWidget(QLabel("Frame")); tr.addWidget(self.goto_box)
        tr.addSpacing(12)
        tr.addWidget(self.pos_label, 1)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.view, 1)
        cl.addWidget(self.timeline)
        cl.addLayout(tr)

        # ---- right: behaviors + events
        self.beh_list = QListWidget()
        self.beh_list.setFocusPolicy(Qt.NoFocus)
        self.beh_list.itemDoubleClicked.connect(
            lambda it: self.toggle_behavior(it.data(Qt.UserRole)))
        b_edit = QPushButton("Edit behaviors…")
        b_edit.clicked.connect(self.edit_behaviors)
        beh_box = QGroupBox("Behaviors  (press hotkey to start / end)")
        bl = QVBoxLayout(beh_box)
        bl.addWidget(self.beh_list)
        bl.addWidget(b_edit)

        self.ev_table = QTableWidget(0, 7)
        self.ev_table.setHorizontalHeaderLabels(
            ["Behavior", "#", "Start", "End", "Frames", "Dur (s)", "Clip"])
        self.ev_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.ev_table.horizontalHeader().setStretchLastSection(True)
        self.ev_table.verticalHeader().setVisible(False)
        self.ev_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ev_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ev_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ev_table.cellDoubleClicked.connect(lambda r, c: self._goto_event(r, start=True))
        self.ev_table.itemSelectionChanged.connect(self._on_event_selection)

        def btn(text, tip, fn):
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(fn); return b

        e1 = QHBoxLayout()
        e1.addWidget(btn("⇤ Start", "Jump to event start", lambda: self._goto_event(None, True)))
        e1.addWidget(btn("End ⇥", "Jump to event end", lambda: self._goto_event(None, False)))
        e1.addWidget(btn("Set start", "Set selected event's start to the current frame",
                         lambda: self._set_bound(True)))
        e1.addWidget(btn("Set end", "Set selected event's end to the current frame",
                         lambda: self._set_bound(False)))
        e1.addWidget(btn("Delete", "Delete selected event(s)", self.delete_selected))
        e2 = QHBoxLayout()
        e2.addWidget(btn("Export selected", "Cut clips for the selected events",
                         lambda: self.export_clips("selected")))
        e2.addWidget(btn("Export new", "Cut clips for this video's events not yet exported",
                         lambda: self.export_clips("new")))
        e2.addWidget(btn("Export all videos…", "Cut clips for every un-exported event in the project",
                         lambda: self.export_clips("project")))

        ev_box = QGroupBox("Events in this video")
        el = QVBoxLayout(ev_box)
        el.addWidget(self.ev_table)
        el.addLayout(e1)
        el.addLayout(e2)

        right = QSplitter(Qt.Vertical)
        right.addWidget(beh_box)
        right.addWidget(ev_box)
        right.setSizes([250, 550])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(right)
        split.setSizes([260, 780, 400])
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)
        self.statusBar().showMessage("Create or open a project to begin (File menu).")

        # everything that needs a project/video
        self._video_widgets = [self.b_first, self.b_back, self.b_play, self.b_fwd,
                               self.b_last, self.speed_box, self.goto_box, self.timeline]
        self._project_widgets = [b_add, b_addf, b_rem, b_edit, self.video_list]

    def _build_menus(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("New project…", self); a.setShortcut(QKeySequence.New)
        a.triggered.connect(self.new_project); m.addAction(a)
        a = QAction("Open project…", self); a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.open_project); m.addAction(a)
        m.addSeparator()
        a = QAction("Add videos…", self); a.triggered.connect(self.add_videos); m.addAction(a)
        a = QAction("Add folder of videos…", self); a.triggered.connect(self.add_video_folder); m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self); a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close); m.addAction(a)

        m = self.menuBar().addMenu("&Edit")
        a = QAction("Undo last event", self); a.setShortcut(QKeySequence.Undo)
        a.triggered.connect(self.undo_last); m.addAction(a)
        a = QAction("Edit behaviors…", self); a.triggered.connect(self.edit_behaviors); m.addAction(a)
        a = QAction("Export settings…", self); a.triggered.connect(self.edit_export_settings); m.addAction(a)

        m = self.menuBar().addMenu("&Help")
        a = QAction("Keyboard shortcuts", self); a.setShortcut(QKeySequence.HelpContents)
        a.triggered.connect(lambda: QMessageBox.information(self, "Keyboard shortcuts", SHORTCUT_HELP))
        m.addAction(a)
        a = QAction("Open project folder", self)
        a.triggered.connect(self._reveal_project); m.addAction(a)

    def _set_enabled(self, has_video: bool):
        for w in self._video_widgets:
            w.setEnabled(has_video)
        for w in self._project_widgets:
            w.setEnabled(self.project is not None)

    # ============================================================ projects
    def new_project(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose (or create) an empty folder for the new project")
        if not d:
            return
        if (Path(d) / PROJECT_FILE).exists():
            QMessageBox.information(self, "Project exists",
                                    "That folder already contains a project — opening it.")
        self.load_project(Path(d), create=True)
        if self.project and not self.project.behaviors:
            self.edit_behaviors()

    def open_project(self):
        d = QFileDialog.getExistingDirectory(self, "Open project folder")
        if not d:
            return
        if not (Path(d) / PROJECT_FILE).exists():
            ans = QMessageBox.question(self, "No project here",
                                       f"No {PROJECT_FILE} found in that folder. Create a new project there?")
            if ans != QMessageBox.Yes:
                return
        self.load_project(Path(d), create=True)

    def load_project(self, root: Path, create=False):
        if not self._confirm_discard_open():
            return
        try:
            self.project = Project.create(root) if create else Project.load(root)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not open project", str(e))
            return
        self._close_video()
        self.settings.setValue("last_project", str(root))
        self.setWindowTitle(f"Behavior Clipper — {root.name}")
        self.project_label.setText(f"Project: {root}")
        self.refresh_video_list()
        self.refresh_behaviors()
        self._set_enabled(False)
        self.statusBar().showMessage(f"Opened project {root}")

    def _reveal_project(self):
        if self.project:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root)))

    # ============================================================== videos
    def refresh_video_list(self):
        self.video_list.clear()
        if not self.project:
            return
        for p in self.project.video_paths():
            n = 0
            if self.project.csv_path(p).exists():
                n = len(self.project.load_events(p)[0])
            it = QListWidgetItem(f"{p.name}   ({n})" if n else p.name)
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(str(p))
            if not p.exists():
                it.setForeground(QBrush(QColor("#d33")))
                it.setToolTip(f"MISSING: {p}")
            if self.video_path and p == self.video_path:
                f = it.font(); f.setBold(True); it.setFont(f)
            self.video_list.addItem(it)

    def _add_paths(self, paths):
        added, conflicts = 0, []
        for p in paths:
            p = Path(p)
            if self.project.stem_conflicts(p):
                conflicts.append(p.name)
                continue
            if self.project.add_video(p):
                added += 1
        self.refresh_video_list()
        msg = f"Added {added} video(s)."
        if conflicts:
            QMessageBox.warning(self, "Name conflict",
                                "These were skipped because another video in the project has the "
                                "same file name (clip and CSV names would collide):\n\n"
                                + "\n".join(conflicts))
        self.statusBar().showMessage(msg)

    def add_videos(self):
        if not self.project:
            return
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", str(self.project.root),
                                                f"Videos ({exts} {exts.upper()});;All files (*)")
        if files:
            self._add_paths(files)

    def add_video_folder(self):
        if not self.project:
            return
        d = QFileDialog.getExistingDirectory(self, "Add all videos in folder", str(self.project.root))
        if not d:
            return
        files = sorted(p for p in Path(d).iterdir()
                       if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."))
        if not files:
            QMessageBox.information(self, "No videos", "No video files found in that folder.")
            return
        self._add_paths(files)

    def remove_video(self):
        it = self.video_list.currentItem()
        if not it or not self.project:
            return
        p = Path(it.data(Qt.UserRole))
        ans = QMessageBox.question(self, "Remove video",
                                   f"Remove {p.name} from the project?\n\n"
                                   "The video file, its annotation CSV and any clips are NOT deleted.")
        if ans != QMessageBox.Yes:
            return
        if self.video_path == p:
            if not self._confirm_discard_open():
                return
            self._close_video()
        self.project.remove_video(p)
        self.refresh_video_list()

    def _on_video_activated(self, item):
        self.open_video(Path(item.data(Qt.UserRole)))

    def _close_video(self):
        self.pause()
        if self.reader:
            self.reader.close()
        self.reader, self.video_path = None, None
        self.events, self.open_events = [], {}
        self.view.clear_frame("Select a video")
        self.refresh_events_table()
        self.refresh_behaviors()
        self._set_enabled(False)

    def open_video(self, path: Path):
        if path == self.video_path:
            return
        if not path.exists():
            QMessageBox.warning(self, "Missing video", f"File not found:\n{path}")
            return
        if not self._confirm_discard_open():
            return
        self._close_video()
        try:
            self.reader = VideoReader(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not open video", str(e))
            return
        self.video_path = path
        self.events, _ = self.project.load_events(path)
        self.goto_box.setRange(0, self.reader.n_frames - 1)
        self.speed_idx = DEFAULT_SPEED_IDX
        self.speed_box.setCurrentIndex(self.speed_idx)
        self._set_enabled(True)
        self.seek(0)
        self.refresh_events_table()
        self.refresh_video_list()
        self.view.setFocus()
        r = self.reader
        self.statusBar().showMessage(
            f"{path.name}: {r.width}×{r.height}, {r.fps:.3f} fps, {r.n_frames} frames, "
            f"{len(self.events)} existing event(s)")

    # ============================================================ playback
    def _show_current(self):
        r = self.reader
        if not r or r.frame is None:
            return
        self.view.show_frame(r.frame)
        self.goto_box.blockSignals(True)
        self.goto_box.setMaximum(r.n_frames - 1)
        self.goto_box.setValue(r.pos)
        self.goto_box.blockSignals(False)
        rec = "   ● " + ", ".join(self.open_events) if self.open_events else ""
        self.pos_label.setText(
            f"{r.pos:>7d} / {r.n_frames - 1}   {fmt_time(r.pos / r.fps)}   "
            f"{SPEEDS[self.speed_idx]:g}×{rec}")
        self.timeline.n_frames = r.n_frames
        self.timeline.set_pos(r.pos)

    def seek(self, frame: int):
        if not self.reader:
            return
        self.reader.seek(frame)
        if self.playing:
            self._reset_clock()
        self._show_current()

    def step(self, n: int):
        if not self.reader:
            return
        self.pause()
        if n == 1:
            if self.reader.read_next() is None:
                return
            self._show_current()
        else:
            self.seek(self.reader.pos + n)

    def toggle_play(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if not self.reader:
            return
        if self.reader.at_end():
            self.reader.seek(0)
        self.playing = True
        self.b_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self._reset_clock()
        self.timer.start()

    def pause(self):
        self.playing = False
        self.timer.stop()
        self.b_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def _reset_clock(self):
        self._play_t0 = time.perf_counter()
        self._play_f0 = self.reader.pos
        rate = self.reader.fps * SPEEDS[self.speed_idx]
        self.timer.setInterval(int(max(8, min(40, 1000 / rate))))

    def _tick(self):
        r = self.reader
        if not r:
            return self.pause()
        target = self._play_f0 + int((time.perf_counter() - self._play_t0)
                                     * r.fps * SPEEDS[self.speed_idx])
        steps = target - r.pos
        if steps <= 0:
            return
        if steps > 1:
            if steps > 60:                  # decoder can't keep up; don't fall further behind
                self._reset_clock()
                steps = min(steps, 8)
            r.skip(steps - 1)
        if r.read_next() is None or r.at_end():
            self._show_current()
            self.pause()
            return
        self._show_current()

    def set_speed(self, idx: int):
        self.speed_idx = max(0, min(len(SPEEDS) - 1, idx))
        if self.speed_box.currentIndex() != self.speed_idx:
            self.speed_box.blockSignals(True)
            self.speed_box.setCurrentIndex(self.speed_idx)
            self.speed_box.blockSignals(False)
        if self.playing:
            self._reset_clock()
        self._show_current()
        self.view.setFocus()

    def _on_goto(self):
        if self.reader and self.goto_box.value() != self.reader.pos:
            self.seek(self.goto_box.value())
        self.view.setFocus()

    # ========================================================== behaviors
    def refresh_behaviors(self):
        self.beh_list.clear()
        if not self.project:
            return
        for b in self.project.behaviors:
            n = sum(1 for e in self.events if e.behavior == b.name)
            txt = f"[{b.key}]   {b.name}   ({n})"
            if b.name in self.open_events:
                txt += f"   ● REC from frame {self.open_events[b.name]}"
            it = QListWidgetItem(swatch(b.color), txt)
            it.setData(Qt.UserRole, b.name)
            if b.name in self.open_events:
                f = it.font(); f.setBold(True); it.setFont(f)
                it.setBackground(QBrush(QColor(b.color).darker(250)))
                it.setForeground(QBrush(QColor("white")))
            self.beh_list.addItem(it)
        self._refresh_timeline()

    def _refresh_timeline(self):
        sel = self._selected_keys()
        self.timeline.set_data(self.reader.n_frames if self.reader else 1,
                               self.project.behaviors if self.project else [],
                               self.events, self.open_events,
                               self.reader.pos if self.reader else 0,
                               sel[0] if len(sel) == 1 else None)

    def toggle_behavior(self, name: str):
        if not self.reader or not self.project:
            return
        cur = self.reader.pos
        if name in self.open_events:
            start = self.open_events.pop(name)
            a, b = sorted((start, cur))
            ev = Event(behavior=name, number=Project.next_number(self.events, name),
                       start_frame=a, end_frame=b)
            self.events.append(ev)
            self._save_events()
            self.refresh_events_table(select=(ev.behavior, ev.number))
            self.refresh_video_list()
            self.statusBar().showMessage(
                f"{name} #{ev.number}: frames {a}–{b} ({ev.n_frames} frames) saved")
        else:
            self.open_events[name] = cur
            self.statusBar().showMessage(f"{name} started at frame {cur} — press its key again to end")
        self.refresh_behaviors()
        self._show_current()

    def cancel_open(self):
        if self.open_events:
            names = ", ".join(self.open_events)
            self.open_events.clear()
            self.refresh_behaviors()
            self._show_current()
            self.statusBar().showMessage(f"Cancelled open: {names}")

    def undo_last(self):
        if not self.events:
            return
        # "last" = most recently created = highest position in list
        ev = self.events[-1]
        if ev.exported:
            ans = QMessageBox.question(self, "Undo", f"{ev.behavior} #{ev.number} was already "
                                       "exported. Remove the event anyway (clip file is kept)?")
            if ans != QMessageBox.Yes:
                return
        self.events.pop()
        self._save_events()
        self.refresh_events_table()
        self.refresh_behaviors()
        self.refresh_video_list()
        self.statusBar().showMessage(f"Removed {ev.behavior} #{ev.number}")

    def edit_behaviors(self):
        if not self.project:
            return
        counts: dict[str, int] = {}
        for v in self.project.video_paths():
            evs = self.events if v == self.video_path else self.project.load_events(v)[0]
            for e in evs:
                counts[e.behavior] = counts.get(e.behavior, 0) + 1
        dlg = BehaviorDialog(self.project.behaviors, counts, self)
        if dlg.exec() != BehaviorDialog.Accepted:
            return
        if self.video_path:
            self._save_events()
        for old, new in dlg.renames.items():
            self.project.rename_behavior_in_annotations(old, new)
            for e in self.events:
                if e.behavior == old:
                    e.behavior = new
            if old in self.open_events:
                self.open_events[new] = self.open_events.pop(old)
        if dlg.renames:
            QMessageBox.information(self, "Renamed",
                                    "Annotations were updated to the new name(s). Clips that were "
                                    "already exported keep their old file names; re-export them if "
                                    "you want matching names.")
            for e in self.events:
                if e.behavior in dlg.renames.values():
                    e.exported = False
            if self.video_path:
                self._save_events()
        names = {b.name for b in dlg.result_behaviors}
        self.open_events = {k: v for k, v in self.open_events.items() if k in names}
        self.project.behaviors = dlg.result_behaviors
        self.project.save()
        self.refresh_behaviors()
        self.refresh_events_table()
        self.refresh_video_list()

    def edit_export_settings(self):
        if not self.project:
            return
        dlg = ExportSettingsDialog(self.project.export, self)
        if dlg.exec() == ExportSettingsDialog.Accepted:
            self.project.export = dlg.values()
            self.project.save()

    # ============================================================== events
    def _save_events(self):
        if self.project and self.video_path:
            self.project.save_events(self.video_path, self.events,
                                     self.reader.fps if self.reader else None)

    def refresh_events_table(self, select=None):
        known = {b.name: b for b in self.project.behaviors} if self.project else {}
        evs = sorted(self.events, key=lambda e: (e.start_frame, e.behavior, e.number))
        fps = self.reader.fps if self.reader else 0
        self.ev_table.blockSignals(True)
        self.ev_table.setRowCount(len(evs))
        for r, e in enumerate(evs):
            vals = [e.behavior, e.number, e.start_frame, e.end_frame, e.n_frames,
                    f"{e.n_frames / fps:.2f}" if fps else "",
                    "✓ exported" if e.exported else "—"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setData(Qt.UserRole, (e.behavior, e.number))
                if c == 0:
                    b = known.get(e.behavior)
                    it.setIcon(swatch(b.color if b else "#777"))
                    if not b:
                        it.setToolTip("Behavior no longer defined in project")
                        it.setForeground(QBrush(QColor("#888")))
                if c in (1, 2, 3, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.ev_table.setItem(r, c, it)
        self.ev_table.clearSelection()
        if select:
            for r in range(self.ev_table.rowCount()):
                if self.ev_table.item(r, 0).data(Qt.UserRole) == select:
                    self.ev_table.selectRow(r)
                    self.ev_table.scrollToItem(self.ev_table.item(r, 0))
                    break
        self.ev_table.blockSignals(False)
        self._refresh_timeline()

    def _selected_keys(self) -> list[tuple]:
        rows = sorted({i.row() for i in self.ev_table.selectedIndexes()})
        return [self.ev_table.item(r, 0).data(Qt.UserRole) for r in rows]

    def _event(self, key) -> Event | None:
        return next((e for e in self.events if (e.behavior, e.number) == key), None)

    def _on_event_selection(self):
        self._refresh_timeline()

    def _goto_event(self, row, start=True):
        if row is not None:
            key = self.ev_table.item(row, 0).data(Qt.UserRole)
        else:
            keys = self._selected_keys()
            if not keys:
                return
            key = keys[0]
        e = self._event(key)
        if e:
            self.pause()
            self.seek(e.start_frame if start else e.end_frame)
            self.view.setFocus()

    def _set_bound(self, start: bool):
        keys = self._selected_keys()
        if len(keys) != 1 or not self.reader:
            self.statusBar().showMessage("Select exactly one event first.")
            return
        e = self._event(keys[0])
        cur = self.reader.pos
        if start:
            e.start_frame = cur
        else:
            e.end_frame = cur
        if e.start_frame > e.end_frame:
            e.start_frame, e.end_frame = e.end_frame, e.start_frame
        e.exported = False   # the clip on disk no longer matches
        self._save_events()
        self.refresh_events_table(select=keys[0])
        self.view.setFocus()

    def delete_selected(self):
        keys = self._selected_keys()
        if not keys:
            return
        evs = [self._event(k) for k in keys]
        exported = [e for e in evs if e and e.exported]
        msg = f"Delete {len(evs)} event(s)?"
        if exported:
            msg += (f"\n\n{len(exported)} of them have exported clips. The clip files will "
                    f"also be deleted so a future clip can't be confused with them.")
        if QMessageBox.question(self, "Delete events", msg) != QMessageBox.Yes:
            return
        for e in evs:
            if e is None:
                continue
            if e.exported:
                (self.project.clip_dir / e.clip_name(self.video_path.stem)).unlink(missing_ok=True)
            self.events.remove(e)
        self._save_events()
        self.refresh_events_table()
        self.refresh_behaviors()
        self.refresh_video_list()

    # ============================================================== export
    def export_clips(self, mode: str):
        if not self.project:
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Export", "An export is already running.")
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "ffmpeg not found",
                                 "Clip export needs ffmpeg. Install it with\n\n"
                                 "    pip install imageio-ffmpeg\n\nor from ffmpeg.org, then retry.")
            return
        jobs: list[ClipJob] = []
        known = {b.name for b in self.project.behaviors}

        def add_jobs(video: Path, evs):
            for e in evs:
                jobs.append(ClipJob(video=video, start_frame=e.start_frame, end_frame=e.end_frame,
                                    out_path=self.project.clip_dir / e.clip_name(video.stem),
                                    key=(str(video), e.behavior, e.number)))

        if mode in ("selected", "new"):
            if not self.video_path:
                return
            if mode == "selected":
                evs = [self._event(k) for k in self._selected_keys()]
                evs = [e for e in evs if e]
                if not evs:
                    self.statusBar().showMessage("No events selected.")
                    return
                again = [e for e in evs if e.exported]
                if again and QMessageBox.question(
                        self, "Re-export", f"{len(again)} selected clip(s) were already "
                        "exported and will be overwritten. Continue?") != QMessageBox.Yes:
                    return
            else:
                evs = [e for e in self.events if not e.exported and e.behavior in known]
            add_jobs(self.video_path, evs)
        else:  # whole project
            if self.video_path:
                self._save_events()
            for v in self.project.video_paths():
                if not v.exists():
                    continue
                evs = self.events if v == self.video_path else self.project.load_events(v)[0]
                add_jobs(v, [e for e in evs if not e.exported and e.behavior in known])
            if jobs and QMessageBox.question(
                    self, "Export all", f"Export {len(jobs)} un-exported clip(s) across the "
                    "project?") != QMessageBox.Yes:
                return
        if not jobs:
            self.statusBar().showMessage("Nothing to export.")
            return

        self.pause()
        self._export_errors = []
        self._export_short = []
        self.progress = QProgressDialog("Exporting…", "Cancel", 0, 1000, self)
        self.progress.setWindowTitle("Exporting clips")
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)
        self._n_jobs = len(jobs)
        self._job_idx = 0

        s = self.project.export
        self.worker = ExportWorker(jobs, ffmpeg, s.crf, s.preset)
        self.worker.clip_started.connect(self._on_clip_started)
        self.worker.clip_progress.connect(self._on_clip_progress)
        self.worker.clip_done.connect(self._on_clip_done)
        self.worker.failed.connect(lambda k, m: self._export_errors.append(m))
        self.worker.finished_all.connect(self._on_export_finished)
        self.progress.canceled.connect(self.worker.stop)
        self.worker.start()

    def _on_clip_started(self, i, n, name):
        self._job_idx = i
        self.progress.setLabelText(f"Clip {i + 1} of {n}:  {name}")

    def _on_clip_progress(self, done, total):
        frac = (self._job_idx + done / max(1, total)) / max(1, self._n_jobs)
        self.progress.setValue(int(frac * 1000))

    def _on_clip_done(self, key, written, expected):
        video, behavior, number = key
        if written != expected:
            self._export_short.append(f"{Path(video).stem} {behavior} #{number}: "
                                      f"{written}/{expected} frames")
        if self.video_path and str(self.video_path) == video:
            e = self._event((behavior, number))
            if e:
                e.exported = True
                self._save_events()
        else:
            evs, fps = self.project.load_events(Path(video))
            for e in evs:
                if (e.behavior, e.number) == (behavior, number):
                    e.exported = True
            self.project.save_events(Path(video), evs, fps)

    def _on_export_finished(self, ok, total):
        self.progress.setValue(1000)
        self.progress.close()
        self.refresh_events_table()
        msg = f"Exported {ok} of {total} clip(s) to {self.project.clip_dir}"
        self.statusBar().showMessage(msg)
        details = []
        if self._export_errors:
            details.append("Errors:\n" + "\n".join(self._export_errors[:10]))
        if self._export_short:
            details.append("Clips shorter than marked (video ended early?):\n"
                           + "\n".join(self._export_short[:10]))
        if details:
            QMessageBox.warning(self, "Export finished with problems", msg + "\n\n" + "\n\n".join(details))
        self.view.setFocus()

    # ======================================================== key handling
    def eventFilter(self, obj, event):
        if event.type() != QEvent.KeyPress or not self.isActiveWindow():
            return False
        if isinstance(QApplication.focusWidget(), TEXT_INPUTS):
            return False
        if self.handle_key(event):
            return True
        return False

    def handle_key(self, event) -> bool:
        key = event.key()
        mods = event.modifiers()
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False        # leave Ctrl/Cmd shortcuts to menus
        shift = bool(mods & Qt.ShiftModifier)
        if not self.reader:
            return False
        if key == Qt.Key_Space:
            if not event.isAutoRepeat():
                self.toggle_play()
            return True
        if key in (Qt.Key_Left, Qt.Key_Right):
            n = 10 if shift else 1
            self.step(n if key == Qt.Key_Right else -n)
            return True
        if key == Qt.Key_Up:
            self.set_speed(self.speed_idx + 1); return True
        if key == Qt.Key_Down:
            self.set_speed(self.speed_idx - 1); return True
        if key == Qt.Key_Home:
            self.pause(); self.seek(0); return True
        if key == Qt.Key_End:
            self.pause(); self.seek(10**12); return True
        if key == Qt.Key_Escape:
            self.cancel_open(); return True
        text = event.text()
        if text and self.project and not event.isAutoRepeat():
            b = self.project.behavior_for_key(text)
            if b:
                self.toggle_behavior(b.name)
                return True
        return False

    # ============================================================== closing
    def _confirm_discard_open(self) -> bool:
        if not self.open_events:
            return True
        ans = QMessageBox.question(
            self, "Unfinished behaviors",
            "These behaviors were started but not ended and will be discarded:\n\n"
            + "\n".join(f"  {k} (from frame {v})" for k, v in self.open_events.items())
            + "\n\nContinue?")
        if ans == QMessageBox.Yes:
            self.open_events.clear()
            return True
        return False

    def closeEvent(self, e):
        if not self._confirm_discard_open():
            e.ignore(); return
        if self.worker and self.worker.isRunning():
            if QMessageBox.question(self, "Export running",
                                    "An export is running. Stop it and quit?") != QMessageBox.Yes:
                e.ignore(); return
            self.worker.stop(); self.worker.wait(5000)
        self.pause()
        if self.reader:
            self.reader.close()
        e.accept()
