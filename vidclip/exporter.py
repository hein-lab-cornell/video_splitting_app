"""Frame-accurate clip export.

Frames are decoded with OpenCV (the same decoder used for annotation, so the
frame indices match the CSV exactly) and piped to ffmpeg for H.264 encoding.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
from PySide6.QtCore import QThread, Signal


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@dataclass
class ClipJob:
    video: Path
    start_frame: int
    end_frame: int      # inclusive
    out_path: Path
    key: tuple          # (behavior, number) — lets the UI mark the event exported


def export_clip(job: ClipJob, ffmpeg: str, crf: int = 18, preset: str = "medium",
                progress=None, should_stop=None) -> int:
    """Write one clip. Returns number of frames written."""
    cap = cv2.VideoCapture(str(job.video))
    if not cap.isOpened():
        raise IOError(f"Could not open {job.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = job.end_frame - job.start_frame + 1
    job.out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = job.out_path.with_name(job.out_path.stem + ".partial" + job.out_path.suffix)

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", f"{fps:.6f}", "-i", "-",
           "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
           "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp_out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    written = 0
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, job.start_frame)
        for i in range(n):
            if should_stop and should_stop():
                break
            ok, frame = cap.read()
            if not ok:
                break
            proc.stdin.write(frame.tobytes())
            written += 1
            if progress and i % 10 == 0:
                progress(written, n)
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:
            pass
        err = proc.stderr.read().decode(errors="ignore")
        rc = proc.wait()
    if rc != 0:
        tmp_out.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed for {job.out_path.name}: {err.strip()}")
    if should_stop and should_stop():
        tmp_out.unlink(missing_ok=True)
        return 0
    tmp_out.replace(job.out_path)
    return written


class ExportWorker(QThread):
    """Exports a list of ClipJobs in the background."""
    clip_started = Signal(int, int, str)        # index, total, name
    clip_progress = Signal(int, int)            # frames done, frames total
    clip_done = Signal(object, int, int)        # key, frames written, frames expected
    failed = Signal(object, str)                # key, message
    finished_all = Signal(int, int)             # n ok, n total

    def __init__(self, jobs: list[ClipJob], ffmpeg: str, crf: int, preset: str):
        super().__init__()
        self.jobs, self.ffmpeg, self.crf, self.preset = jobs, ffmpeg, crf, preset
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ok = 0
        for i, job in enumerate(self.jobs):
            if self._stop:
                break
            self.clip_started.emit(i, len(self.jobs), job.out_path.name)
            try:
                n = export_clip(job, self.ffmpeg, self.crf, self.preset,
                                progress=lambda d, t: self.clip_progress.emit(d, t),
                                should_stop=lambda: self._stop)
                if self._stop:
                    break
                expected = job.end_frame - job.start_frame + 1
                self.clip_done.emit(job.key, n, expected)
                ok += 1
            except Exception as e:  # noqa: BLE001
                self.failed.emit(job.key, str(e))
        self.finished_all.emit(ok, len(self.jobs))
