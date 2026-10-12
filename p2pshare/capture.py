"""Screen capture as an aiortc video track."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import mss
import numpy as np
from aiortc import MediaStreamTrack
from av import VideoFrame

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = Fraction(1, VIDEO_CLOCK_RATE)


def list_monitors() -> list[dict]:
    """Index 0 is the union of all monitors; 1.. are the individual screens."""
    with mss.mss() as sct:
        return list(sct.monitors)


class ScreenTrack(MediaStreamTrack):
    """Grabs a monitor at a fixed frame rate, downscaled to at most max_width."""

    kind = "video"

    def __init__(self, monitor: int = 1, fps: float = 15, max_width: int = 1920) -> None:
        super().__init__()
        monitors = list_monitors()
        if not 0 <= monitor < len(monitors):
            raise ValueError(f"monitor {monitor} does not exist (0-{len(monitors) - 1} available)")
        self.monitor = monitor
        self.fps = fps
        self.max_width = max_width
        # Grabbing and colour conversion are slow; keep them off the event loop.
        # mss handles are not thread safe, so each capture thread owns one.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen-capture")
        self._local = threading.local()
        self._start: float | None = None
        self._next = 0.0

    def _grab(self) -> VideoFrame:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = mss.mss()
        shot = sct.grab(sct.monitors[self.monitor])
        frame = VideoFrame.from_ndarray(np.asarray(shot), format="bgra")

        width = min(self.max_width, frame.width)
        height = round(frame.height * width / frame.width)
        # yuv420p needs even dimensions; converting here also spares the encoder.
        return frame.reformat(width=width & ~1, height=height & ~1, format="yuv420p")

    async def recv(self) -> VideoFrame:
        now = time.monotonic()
        if self._start is None:
            self._start = self._next = now
        else:
            # If capture fell behind, resume from now instead of bursting to catch up.
            self._next = max(self._next + 1 / self.fps, now)
            await asyncio.sleep(self._next - now)

        frame = await asyncio.get_running_loop().run_in_executor(self._executor, self._grab)
        frame.pts = int((self._next - self._start) * VIDEO_CLOCK_RATE)
        frame.time_base = VIDEO_TIME_BASE
        return frame

    def stop(self) -> None:
        super().stop()
        self._executor.shutdown(wait=False)
