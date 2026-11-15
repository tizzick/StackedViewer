"""Still frames of a video at chosen times, for the start screen preview."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from .libmpv import mpv
from .render import SoftwareRenderContext

WORKERS = 3
THUMB_WIDTH = 320
JOB_TIMEOUT_S = 4.0
# After a seek finishes, wait this long for the new frame to be announced
# before drawing whatever mpv has.
FRAME_GRACE_S = 0.08


class _Mailbox:
    """Flags set by mpv's threads and read on the GUI thread."""

    def __init__(self):
        self.frame = False
        self.loaded = False

    def on_frame(self):
        self.frame = True

    def on_duration(self, _name, value):
        if value:
            self.loaded = True


class _Worker:
    """One hidden, paused mpv instance that seeks around and hands back frames."""

    def __init__(self, path: str, width: int, height: int):
        self.width, self.height = width, height
        self.box = _Mailbox()
        self.job = None  # (generation, index)
        self._started = 0.0
        self._seek_done = 0.0
        self.player = mpv.MPV(
            vo="libmpv", ao="null", aid="no", sid="no", pause=True, hwdec="no",
            keep_open="yes", osc="no", input_default_bindings="no", input_vo_keyboard="no",
        )
        self.ctx = SoftwareRenderContext(self.player)
        self.ctx.set_update_callback(self.box.on_frame)
        self.player.observe_property("duration", self.box.on_duration)
        self.player.play(path)

    @property
    def idle(self) -> bool:
        return self.job is None and self.box.loaded

    def start(self, generation: int, index: int, seconds: float, exact: bool):
        self.job = (generation, index)
        self.box.frame = False
        self._started = time.monotonic()
        self._seek_done = 0.0
        mode = "absolute+exact" if exact else "absolute+keyframes"
        # Synchronous, so that mpv's "seeking" property is already true when
        # this returns and can be watched for the seek finishing.
        self.player.command("seek", f"{seconds:.3f}", mode)

    def poll(self):
        """Returns (generation, index, QImage or None) when the job is done."""
        if self.job is None:
            return None
        now = time.monotonic()
        if not self._seek_done and not self.player.seeking:
            self._seek_done = now
        ready = self._seek_done and (self.box.frame or now - self._seek_done > FRAME_GRACE_S)
        timed_out = now - self._started > JOB_TIMEOUT_S
        if not ready and not timed_out:
            return None
        generation, index = self.job
        self.job = None
        image = None
        if ready:
            try:
                raw = self.ctx.render(self.width, self.height)
                image = QImage(raw, self.width, self.height, self.width * 4,
                               QImage.Format.Format_RGBX8888).convertToFormat(QImage.Format.Format_RGB888)
            except Exception:  # noqa: BLE001 - a failed thumbnail just stays blank
                image = None
        return generation, index, image

    def close(self):
        self.ctx.free()
        self.player.terminate()


class Thumbnailer(QObject):
    """Produces a still frame for each requested time.

    Call `request(times, exact)`; `ready(index, image)` is emitted as each one
    arrives. A new request replaces any that is still in progress.
    """

    ready = Signal(int, QImage)

    def __init__(self, path: str, video_width: int, video_height: int, parent=None):
        super().__init__(parent)
        width = THUMB_WIDTH
        height = max(2, round(width * video_height / max(1, video_width)))
        self._workers = [_Worker(path, width, height) for _ in range(WORKERS)]
        self._generation = 0
        self._queue: list[tuple[int, float]] = []
        self._exact = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def request(self, times: list[float], exact: bool):
        self._generation += 1
        self._queue = list(enumerate(times))
        self._exact = exact
        if not self._timer.isActive():
            self._timer.start(10)

    def _tick(self):
        busy = False
        for worker in self._workers:
            result = worker.poll()
            if result is not None:
                generation, index, image = result
                if generation == self._generation and image is not None:
                    self.ready.emit(index, image)
            if worker.idle and self._queue:
                index, seconds = self._queue.pop(0)
                worker.start(self._generation, index, seconds, self._exact)
            if worker.job is not None or not worker.box.loaded:
                busy = True
        if not busy and not self._queue:
            self._timer.stop()

    def close(self):
        self._timer.stop()
        self._queue = []
        for worker in self._workers:
            worker.close()
        self._workers = []
