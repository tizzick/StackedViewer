"""Reads a video's duration and size without showing it."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .libmpv import mpv

PROBE_TIMEOUT_MS = 10_000


class VideoProbe(QObject):
    """Opens `path` in a hidden mpv instance and reports what it finds.

    Emits `finished(info)` with keys path, duration, width, height, fps, or
    `failed(message)` if the file cannot be played.
    """

    finished = Signal(dict)
    failed = Signal(str)
    _loaded = Signal()
    _ended = Signal()

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self._done = False
        self._player = mpv.MPV(vo="null", ao="null", pause=True, aid="no", hwdec="no")

        # mpv calls back on its own threads; hop to the GUI thread via signals.
        self._loaded.connect(self._on_loaded, Qt.ConnectionType.QueuedConnection)
        self._ended.connect(self._on_ended, Qt.ConnectionType.QueuedConnection)
        self._player.event_callback("file-loaded")(lambda _e: self._loaded.emit())
        self._player.event_callback("end-file")(lambda _e: self._ended.emit())

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._fail("The file took too long to open."))
        self._timeout.start(PROBE_TIMEOUT_MS)

        self._player.play(path)

    def _on_loaded(self):
        if self._done:
            return
        p = self._player
        duration = float(p.duration or 0.0)
        width = int(p.width or 0)
        height = int(p.height or 0)
        if not width or not height:
            self._fail("This file has no video track.")
            return
        if duration <= 0:
            self._fail("Could not read the video's length.")
            return
        info = {
            "path": self.path,
            "duration": duration,
            "width": width,
            "height": height,
            "fps": float(p.container_fps or 0.0),
        }
        self._finish()
        self.finished.emit(info)

    def _on_ended(self):
        self._fail("This file could not be played. It may be damaged or not a video.")

    def _fail(self, message: str):
        if self._done:
            return
        self._finish()
        self.failed.emit(message)

    def _finish(self):
        self._done = True
        self._timeout.stop()
        self._player.terminate()
        self._player = None

    def cancel(self):
        """Stop without emitting anything."""
        if not self._done:
            self._finish()
