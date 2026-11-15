"""One video tile: an mpv instance rendering into a Qt OpenGL widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QLabel

from .libmpv import mpv
from .sync import format_time, tile_position


def _get_proc_address(_ctx, name):
    gl = QOpenGLContext.currentContext()
    if gl is None:
        return 0
    return int(gl.getProcAddress(name) or 0)


class MpvTile(QOpenGLWidget):
    """Plays `path` starting `index * offset_s` seconds in, looping forever."""

    _frame_ready = Signal()
    clicked = Signal(int)
    ready = Signal(int)  # emitted once the tile has sought to its start position

    def __init__(self, index: int, path: str, offset_s: float, hwdec: str = "auto-safe", parent=None):
        super().__init__(parent)
        self.index = index
        self.path = path
        self.offset_s = offset_s
        self.duration = 0.0
        self.start_pos = 0.0
        self.is_ready = False
        self.total_drops = 0
        self._last_drop_reading = 0
        self._render_ctx = None
        # Keep a reference: mpv holds a C pointer to this callback.
        self._proc_address_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)

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
        )
        self.player.observe_property("duration", self._on_duration)

        self._frame_ready.connect(self.update, Qt.ConnectionType.QueuedConnection)
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
        self._render_ctx = mpv.MpvRenderContext(
            self.player,
            "opengl",
            opengl_init_params={"get_proc_address": self._proc_address_fn},
        )
        # Called on an mpv thread; hop to the GUI thread through a signal.
        self._render_ctx.update_cb = self._frame_ready.emit
        self.player.play(self.path)

    def paintGL(self):
        if self._render_ctx is None:
            return
        ratio = self.devicePixelRatioF()
        self._render_ctx.render(
            flip_y=True,
            # All tiles render on the one GUI thread. Never wait inside mpv for
            # a frame's display time: that would stall every other tile.
            block_for_target_time=False,
            opengl_fbo={
                "w": int(self.width() * ratio),
                "h": int(self.height() * ratio),
                "fbo": self.defaultFramebufferObject(),
            },
        )

    def _on_swapped(self):
        if self._render_ctx is not None:
            self._render_ctx.report_swap()

    # ---- playback -------------------------------------------------------

    def _on_duration(self, _name, value):
        # Runs on an mpv thread. Property writes and commands are thread-safe.
        if not value or self.is_ready:
            return
        self.duration = float(value)
        self.start_pos = tile_position(self.index, self.offset_s, self.duration)
        self.player.command_async("seek", str(self.start_pos), "absolute+exact")
        self.is_ready = True
        self.ready.emit(self.index)

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
        if self._render_ctx is not None:
            self.makeCurrent()
            self._render_ctx.update_cb = None
            self._render_ctx.free()
            self._render_ctx = None
            self.doneCurrent()
        if self.player is not None:
            self.player.terminate()
            self.player = None
