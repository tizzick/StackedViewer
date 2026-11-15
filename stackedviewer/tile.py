"""One video tile: an mpv instance rendering into a Qt OpenGL widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QLabel

from .libmpv import mpv
from .render import RenderContext
from .sync import format_time, tile_position


def _get_proc_address(_ctx, name):
    gl = QOpenGLContext.currentContext()
    if gl is None:
        return 0
    return int(gl.getProcAddress(name) or 0)


# Cheapest rendering path: simple scaling straight to the screen, with no
# high-precision intermediate buffers. Many small tiles gain nothing from the
# high-quality path, and it costs GPU time per tile. It also proved unreliable
# when a tile's first frame is drawn late (the tile stayed black).
FAST_RENDER_OPTIONS = {
    "scale": "bilinear",
    "dscale": "bilinear",
    "cscale": "bilinear",
    "dither": "no",
    "correct_downscaling": "no",
    "linear_downscaling": "no",
    "sigmoid_upscaling": "no",
    "hdr_compute_peak": "no",
}


class _Poller:
    """One GUI-thread timer that delivers mailbox messages to every live tile."""

    _timer = None
    _tiles: list = []

    @classmethod
    def add(cls, tile):
        if cls._timer is None:
            cls._timer = QTimer()
            cls._timer.setTimerType(Qt.TimerType.PreciseTimer)
            cls._timer.timeout.connect(cls._tick)
        cls._tiles.append(tile)
        if not cls._timer.isActive():
            cls._timer.start(4)

    @classmethod
    def remove(cls, tile):
        if tile in cls._tiles:
            cls._tiles.remove(tile)
        if not cls._tiles and cls._timer is not None:
            cls._timer.stop()

    @classmethod
    def _tick(cls):
        for tile in list(cls._tiles):
            tile._poll_flags()


class _Mailbox:
    """Where mpv's threads leave messages for the GUI thread.

    mpv calls back on its own threads. Those callbacks must not touch any Qt
    object, so they only set fields on this plain Python object, which the
    tile reads from a timer on the GUI thread.
    """

    def __init__(self):
        self.frame = False
        self.duration = 0.0

    def on_frame(self):
        self.frame = True

    def on_duration(self, _name, value):
        if value:
            self.duration = float(value)


class MpvTile(QOpenGLWidget):
    """Plays `path` starting at `first_start_s + index * offset_s`, wrapped to
    the video's length, and loops forever."""

    clicked = Signal(int)
    ready = Signal(int)  # emitted once the tile has sought to its start position

    def __init__(self, index: int, path: str, offset_s: float, hwdec: str = "auto-safe",
                 first_start_s: float = 0.0, parent=None):
        super().__init__(parent)
        self.index = index
        self.path = path
        self.offset_s = offset_s
        self.first_start_s = first_start_s
        self.duration = 0.0
        self.start_pos = 0.0
        self.is_ready = False
        self.total_drops = 0
        self._last_drop_reading = 0
        self._render_ctx = None
        # Keep a reference: mpv holds a C pointer to this callback.
        self._proc_address_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)

        self._box = _Mailbox()
        self.player = mpv.MPV(
            vo="libmpv",
            hwdec=hwdec,
            loop_file="inf",
            pause=True,
            aid="no",  # muted tiles skip audio decoding entirely
            keep_open="yes",
            osc="no",
            input_default_bindings="no",
            input_vo_keyboard="no",
            **FAST_RENDER_OPTIONS,
        )
        self.player.observe_property("duration", self._box.on_duration)

        _Poller.add(self)
        self.frameSwapped.connect(self._on_swapped)

        self.overlay = QLabel(self)
        self.overlay.setStyleSheet(
            "color: white; background: rgba(0, 0, 0, 150); padding: 3px 6px;"
            "font-size: 11px;"
        )
        self.overlay.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.overlay.move(6, 6)
        self.overlay.setText(f"tile {index}: loading")
        self.overlay.adjustSize()

    # ---- OpenGL ---------------------------------------------------------

    def initializeGL(self):
        # An exception escaping a Qt virtual method can crash the process.
        try:
            self._render_ctx = RenderContext(self.player, self._proc_address_fn)
            # Called on an mpv thread: it only sets a flag in the mailbox.
            self._render_ctx.set_update_callback(self._box.on_frame)
            self.player.play(self.path)
        except Exception as exc:  # noqa: BLE001
            self._render_ctx = None
            self.overlay.setText(f"tile {self.index}: could not start video ({exc})")
            self.overlay.adjustSize()

    def paintGL(self):
        if self._render_ctx is None:
            return
        ratio = self.devicePixelRatioF()
        self._render_ctx.render(
            self.defaultFramebufferObject(),
            int(self.width() * ratio),
            int(self.height() * ratio),
        )

    def _poll_flags(self):
        box = self._box
        if box.frame:
            box.frame = False
            self.update()
        if box.duration and not self.is_ready:
            self.duration = box.duration
            self.start_pos = tile_position(self.index, self.offset_s, self.duration, self.first_start_s)
            self.player.command_async("seek", str(self.start_pos), "absolute+exact")
            self.is_ready = True
            self.ready.emit(self.index)

    def _on_swapped(self):
        if self._render_ctx is not None:
            self._render_ctx.report_swap()

    # ---- playback -------------------------------------------------------

    def set_paused(self, paused: bool):
        self.player.pause = paused

    def set_audible(self, audible: bool):
        self.player.aid = "auto" if audible else "no"

    @property
    def audible(self) -> bool:
        return self.player.aid not in (False, None, "no")

    def position(self) -> float:
        return float(self.player.time_pos or 0.0)

    def stats(self) -> dict:
        p = self.player
        params = p.video_params or {}
        return {
            "tile": self.index,
            "start": self.start_pos,
            "position": self.position(),
            "hwdec": p.hwdec_current or "no",
            "source_fps": float(p.container_fps or 0.0),
            "actual_fps": float(p.estimated_vf_fps or 0.0),
            "dropped_output": int(p.frame_drop_count or 0),
            "dropped_decoder": int(p.decoder_frame_drop_count or 0),
            "width": params.get("w", 0),
            "height": params.get("h", 0),
        }

    def poll_drops(self) -> int:
        """Running total of dropped frames. mpv resets its own counters each
        time the file loops, so accumulate the increases instead."""
        p = self.player
        reading = int(p.frame_drop_count or 0) + int(p.decoder_frame_drop_count or 0)
        if reading >= self._last_drop_reading:
            self.total_drops += reading - self._last_drop_reading
        else:
            self.total_drops += reading
        self._last_drop_reading = reading
        return self.total_drops

    def refresh_overlay(self):
        s = self.stats()
        dropped = self.poll_drops()
        self.overlay.setText(
            f"tile {self.index}  {format_time(s['position'])}  "
            f"{s['actual_fps']:.1f}/{s['source_fps']:.1f} fps  "
            f"drop {dropped}  hwdec {s['hwdec']}"
            + ("  [AUDIO]" if self.audible else "")
        )
        self.overlay.adjustSize()

    # ---- events ---------------------------------------------------------

    def mousePressEvent(self, event):
        self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def shutdown(self):
        """Free the render context (needs the GL context current), then mpv."""
        _Poller.remove(self)
        if self._render_ctx is not None:
            self.makeCurrent()
            self._render_ctx.free()
            self._render_ctx = None
            self.doneCurrent()
        if self.player is not None:
            self.player.terminate()
            self.player = None
