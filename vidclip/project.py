"""Project data model: behaviors, videos, events, and CSV persistence.

Project folder layout
---------------------
<project>/
    project.json                      behaviors, video list, export settings
    annotations/<video>_annotations.csv   one CSV per video (autosaved)
    annotations/all_annotations.csv       all videos combined (rebuilt on save)
    clips/<video>_<behavior>_<N>.mp4      exported clips

Frame convention: frames are 0-indexed, and end_frame is INCLUSIVE,
so n_frames = end_frame - start_frame + 1.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

PROJECT_FILE = "project.json"
ANNOT_DIR = "annotations"
CLIP_DIR = "clips"
COMBINED_CSV = "all_annotations.csv"
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".mpg", ".mpeg",
              ".wmv", ".mts", ".m2ts", ".webm", ".flv"}

DEFAULT_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
                  "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
                  "#dcbeff", "#9a6324", "#800000", "#aaffc3", "#808000"]

CSV_COLUMNS = ["video", "behavior", "number", "clip_name",
               "start_frame", "end_frame", "n_frames",
               "start_time_s", "end_time_s", "duration_s",
               "fps", "exported", "video_path"]


def safe_name(s: str) -> str:
    """Make a string safe for use in a filename (keeps letters, digits, . _ -)."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s).strip()).strip("-")
    return s or "unnamed"


@dataclass
class Behavior:
    name: str
    key: str            # single character, stored lower-case for letters
    color: str = "#e6194b"


@dataclass
class Event:
    behavior: str
    number: int
    start_frame: int
    end_frame: int      # inclusive
    exported: bool = False

    @property
    def n_frames(self) -> int:
        return self.end_frame - self.start_frame + 1

    def clip_name(self, video_stem: str, ext: str = ".mp4") -> str:
        return f"{safe_name(video_stem)}_{safe_name(self.behavior)}_{self.number}{ext}"


@dataclass
class ExportSettings:
    crf: int = 18               # x264 quality (lower = better; 18 ~ visually lossless)
    preset: str = "medium"
    include_audio: bool = False  # kept for future use; clips are video-only


@dataclass
class Project:
    root: Path
    behaviors: list[Behavior] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)   # stored relative to root when possible
    export: ExportSettings = field(default_factory=ExportSettings)

    # ------------------------------------------------------------------ paths
    @property
    def annot_dir(self) -> Path:
        return self.root / ANNOT_DIR

    @property
    def clip_dir(self) -> Path:
        return self.root / CLIP_DIR

    def video_abspath(self, v: str) -> Path:
        p = Path(v)
        return p if p.is_absolute() else (self.root / p).resolve()

    def video_paths(self) -> list[Path]:
        return [self.video_abspath(v) for v in self.videos]

    def csv_path(self, video: Path) -> Path:
        return self.annot_dir / f"{safe_name(video.stem)}_annotations.csv"

    # ------------------------------------------------------------ load / save
    @classmethod
    def create(cls, root: Path) -> "Project":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if (root / PROJECT_FILE).exists():
            return cls.load(root)
        proj = cls(root=root)
        proj.save()
        return proj

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = Path(root)
        with open(root / PROJECT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            root=root,
            behaviors=[Behavior(**b) for b in d.get("behaviors", [])],
            videos=list(d.get("videos", [])),
            export=ExportSettings(**d.get("export", {})),
        )

    def save(self) -> None:
        self.annot_dir.mkdir(parents=True, exist_ok=True)
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        d = {
            "behaviors": [asdict(b) for b in self.behaviors],
            "videos": self.videos,
            "export": asdict(self.export),
        }
        tmp = self.root / (PROJECT_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.root / PROJECT_FILE)

    # ------------------------------------------------------------- behaviors
    def behavior(self, name: str) -> Behavior | None:
        for b in self.behaviors:
            if b.name == name:
                return b
        return None

    def behavior_for_key(self, key: str) -> Behavior | None:
        key = key.lower()
        for b in self.behaviors:
            if b.key.lower() == key:
                return b
        return None

    def rename_behavior_in_annotations(self, old: str, new: str) -> None:
        """Rewrite every per-video CSV so events of `old` become `new`."""
        for v in self.video_paths():
            p = self.csv_path(v)
            if not p.exists():
                continue
            events, fps = self.load_events(v)
            changed = False
            for e in events:
                if e.behavior == old:
                    e.behavior = new
                    changed = True
            if changed:
                self.save_events(v, events, fps)

    # ---------------------------------------------------------------- videos
    def add_video(self, path: Path) -> bool:
        """Add a video; returns False if already present."""
        path = Path(path).resolve()
        try:
            rel = os.path.relpath(path, self.root.resolve())
            stored = rel if not rel.startswith("..") else str(path)
        except ValueError:          # different drive on Windows
            stored = str(path)
        if any(self.video_abspath(v) == path for v in self.videos):
            return False
        self.videos.append(stored)
        self.save()
        return True

    def remove_video(self, path: Path) -> None:
        path = Path(path).resolve()
        self.videos = [v for v in self.videos if self.video_abspath(v) != path]
        self.save()

    def stem_conflicts(self, path: Path) -> list[Path]:
        """Other project videos with the same file stem (would collide in output names)."""
        return [v for v in self.video_paths()
                if safe_name(v.stem) == safe_name(Path(path).stem) and v != Path(path).resolve()]

    # ---------------------------------------------------------------- events
    def load_events(self, video: Path) -> tuple[list[Event], float | None]:
        p = self.csv_path(video)
        events: list[Event] = []
        fps = None
        if not p.exists():
            return events, fps
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    events.append(Event(
                        behavior=row["behavior"],
                        number=int(row["number"]),
                        start_frame=int(row["start_frame"]),
                        end_frame=int(row["end_frame"]),
                        exported=str(row.get("exported", "")).strip().lower() in ("true", "1", "yes"),
                    ))
                    if row.get("fps"):
                        fps = float(row["fps"])
                except (KeyError, ValueError):
                    continue
        return events, fps

    def save_events(self, video: Path, events: list[Event], fps: float | None) -> None:
        self.annot_dir.mkdir(parents=True, exist_ok=True)
        fps = fps or 0.0
        rows = []
        for e in sorted(events, key=lambda e: (e.start_frame, e.behavior, e.number)):
            st = e.start_frame / fps if fps else ""
            en = (e.end_frame + 1) / fps if fps else ""
            rows.append({
                "video": video.stem,
                "behavior": e.behavior,
                "number": e.number,
                "clip_name": e.clip_name(video.stem),
                "start_frame": e.start_frame,
                "end_frame": e.end_frame,
                "n_frames": e.n_frames,
                "start_time_s": f"{st:.4f}" if fps else "",
                "end_time_s": f"{en:.4f}" if fps else "",
                "duration_s": f"{e.n_frames / fps:.4f}" if fps else "",
                "fps": f"{fps:.6g}" if fps else "",
                "exported": e.exported,
                "video_path": str(video),
            })
        p = self.csv_path(video)
        tmp = p.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, p)
        self.rebuild_combined_csv()

    def rebuild_combined_csv(self) -> None:
        rows = []
        for p in sorted(self.annot_dir.glob("*_annotations.csv")):
            with open(p, newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
        out = self.annot_dir / COMBINED_CSV
        tmp = out.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, out)

    @staticmethod
    def next_number(events: list[Event], behavior: str) -> int:
        nums = [e.number for e in events if e.behavior == behavior]
        return (max(nums) + 1) if nums else 1
