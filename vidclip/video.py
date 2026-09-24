"""Frame-indexed video reader built on OpenCV.

All frame numbers in the app (display, CSV, and clip export) come from this
reader's decoder, so annotated frames and exported clip frames always agree.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np


class VideoReader:
    CACHE_BYTES = 300 * 1024 * 1024   # memory budget for the back-stepping cache

    def __init__(self, path: Path):
        self.path = Path(path)
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            raise IOError(f"Could not open video: {self.path}")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps: float = fps if fps and 0 < fps < 1000 else 30.0
        self.n_frames: int = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.pos: int = -1            # index of the currently shown frame
        self.frame: np.ndarray | None = None
        self._next: int = 0           # index the decoder will return on next read
        frame_bytes = max(1, self.width * self.height * 3)
        self._cache_max = max(8, min(240, self.CACHE_BYTES // frame_bytes))
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()

    def close(self):
        self.cap.release()
        self._cache.clear()

    # --------------------------------------------------------------- internals
    def _remember(self, idx: int, frame: np.ndarray):
        self._cache[idx] = frame
        self._cache.move_to_end(idx)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def _position_decoder(self, idx: int):
        if self._next != idx:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            self._next = idx

    def _decode(self, idx: int) -> np.ndarray | None:
        self._position_decoder(idx)
        ok, frame = self.cap.read()
        if not ok:
            # metadata frame counts sometimes overshoot the real length
            if idx < self.n_frames:
                self.n_frames = max(1, idx)
            self._next = -1           # force a re-seek next time
            return None
        self._next = idx + 1
        self._remember(idx, frame)
        return frame

    # ----------------------------------------------------------------- public
    def at_end(self) -> bool:
        return self.pos >= self.n_frames - 1

    def read_next(self) -> np.ndarray | None:
        """Advance one frame. Returns None at end of video."""
        idx = self.pos + 1
        if idx >= self.n_frames:
            return None
        frame = self._cache.get(idx)
        if frame is None:
            frame = self._decode(idx)
            if frame is None:
                return None
        self.pos, self.frame = idx, frame
        return frame

    def skip(self, n: int) -> None:
        """Advance n frames without converting them (used for fast playback)."""
        for _ in range(n):
            idx = self.pos + 1
            if idx >= self.n_frames - 1:
                return
            if idx not in self._cache:
                self._position_decoder(idx)
                if not self.cap.grab():
                    self.n_frames = max(1, idx)
                    self._next = -1
                    return
                self._next = idx + 1
            self.pos = idx

    def seek(self, idx: int) -> np.ndarray | None:
        """Show frame `idx` (clamped to the video)."""
        idx = int(max(0, min(idx, self.n_frames - 1)))
        frame = self._cache.get(idx)
        if frame is not None:
            self._cache.move_to_end(idx)
        elif self.pos < idx <= self.pos + 30 and self._next == self.pos + 1:
            # short forward jump: decode through (more reliable than a seek)
            self.skip(idx - self.pos - 1)
            return self.read_next()
        else:
            frame = self._decode(idx)
            if frame is None:
                return self.seek(self.n_frames - 1) if idx > 0 else None
        self.pos, self.frame = idx, frame
        return frame
