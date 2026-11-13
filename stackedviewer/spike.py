"""Milestone 1 feasibility spike.

Plays one file in a rows x cols grid of mpv tiles, each tile offset from the
one before it, and reports how well the machine keeps up.

    python -m stackedviewer.spike VIDEO --rows 2 --cols 2 --offset 5
    python -m stackedviewer.spike VIDEO --rows 4 --cols 4 --offset 1 --benchmark 30

Keys: Space = pause/resume, F = fullscreen, Esc/Q = quit.
Click a tile to switch its audio on (one tile at a time); click again to mute.
"""

from __future__ import annotations

import argparse
import locale
import os
import platform
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QGridLayout, QWidget

from .sync import drift, format_time, tile_position
from .tile import MpvTile


class SpikeWindow(QWidget):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle(f"StackedViewer spike - {args.rows}x{args.cols}, {args.offset} min offset")
        self.setStyleSheet("background: black;")
        self.resize(1280, 720)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        self.tiles: list[MpvTile] = []
        self._ready = 0
        self._paused = True
        self._bench_wall = None
        self._bench_cpu = None

        for i in range(args.rows * args.cols):
            tile = MpvTile(i, args.video, args.offset * 60.0, hwdec=args.hwdec)
            tile.clicked.connect(self._toggle_audio)
            tile.ready.connect(self._tile_ready, Qt.ConnectionType.QueuedConnection)
            grid.addWidget(tile, i // args.cols, i % args.cols)
            self.tiles.append(tile)

        self._overlay_timer = QTimer(self)
        self._overlay_timer.timeout.connect(self._refresh)
        self._overlay_timer.start(500)

    # Start all tiles together once every one has sought to its position.
    def _tile_ready(self, _index):
        self._ready += 1
        if self._ready == len(self.tiles):
            QTimer.singleShot(300, self._start)

    def _start(self):
        self._set_paused(False)
        if self.args.benchmark:
            # Let playback settle for 2 s before measuring.
            QTimer.singleShot(2000, self._begin_benchmark)

    def _begin_benchmark(self):
        self._bench_wall = time.monotonic()
        self._bench_cpu = time.process_time()
        self._bench_drops = [self._drops(t) for t in self.tiles]
        QTimer.singleShot(int(self.args.benchmark * 1000), self._end_benchmark)

    @staticmethod
    def _drops(tile):
        s = tile.stats()
        return s["dropped_output"] + s["dropped_decoder"]

    def _end_benchmark(self):
        wall = time.monotonic() - self._bench_wall
        cpu = time.process_time() - self._bench_cpu
        stats = [t.stats() for t in self.tiles]
        duration = self.tiles[0].duration
        master = stats[0]["position"]

        print()
        print("StackedViewer spike report")
        print(f"  system    : {platform.system()} {platform.release()} ({platform.machine()})")
        print(f"  video     : {os.path.basename(self.args.video)}  "
              f"{stats[0]['width']}x{stats[0]['height']} @ {stats[0]['source_fps']:.2f} fps, "
              f"{format_time(duration)}")
        print(f"  grid      : {self.args.rows}x{self.args.cols} = {len(self.tiles)} tiles, "
              f"offset {self.args.offset} min, hwdec requested '{self.args.hwdec}'")
        print(f"  measured  : {wall:.1f} s, process CPU {100 * cpu / wall:.0f}% of one core "
              f"({os.cpu_count()} cores available)")
        print()
        print("  tile  start     position  hwdec        fps (actual/source)  dropped  drift")
        worst_fps = 1.0
        total_drops = 0
        for t, s, d0 in zip(self.tiles, stats, self._bench_drops):
            expected = tile_position(t.index, t.offset_s, duration, master)
            dr = drift(s["position"], expected, duration)
            drops = s["dropped_output"] + s["dropped_decoder"] - d0
            total_drops += drops
            if s["source_fps"]:
                worst_fps = min(worst_fps, s["actual_fps"] / s["source_fps"])
            print(f"  {t.index:>4}  {format_time(s['start'])}  {format_time(s['position'])}  "
                  f"{s['hwdec']:<12} {s['actual_fps']:>6.1f} / {s['source_fps']:<6.1f}      "
                  f"{drops:>7}  {dr:+.2f}s")
        print()
        verdict = "OK" if worst_fps >= 0.95 and total_drops <= len(self.tiles) else "STRUGGLING"
        print(f"  verdict   : {verdict} (slowest tile at {100 * worst_fps:.0f}% of source fps, "
              f"{total_drops} frames dropped)")
        print()

        if self.args.screenshot:
            self.screen().grabWindow(self.winId()).save(self.args.screenshot)
        self.close()

    def _refresh(self):
        for t in self.tiles:
            if t.is_ready:
                t.refresh_overlay()

    def _set_paused(self, paused: bool):
        self._paused = paused
        for t in self.tiles:
            t.set_paused(paused)

    def _toggle_audio(self, index: int):
        target = self.tiles[index]
        turn_on = not target.audible
        for t in self.tiles:
            t.set_audible(turn_on and t is target)
        self._refresh()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._set_paused(not self._paused)
        elif key == Qt.Key.Key_F:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._overlay_timer.stop()
        for t in self.tiles:
            t.shutdown()
        super().closeEvent(event)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="stackedviewer.spike", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", help="path to the video file")
    p.add_argument("--rows", type=int, default=2)
    p.add_argument("--cols", type=int, default=2)
    p.add_argument("--offset", type=float, default=5.0, help="minutes between tiles (default 5)")
    p.add_argument("--hwdec", default="auto-safe",
                   help="mpv hardware decoding mode: auto-safe (default), auto-copy, no, ...")
    p.add_argument("--benchmark", type=float, default=0, metavar="SECONDS",
                   help="measure for this many seconds, print a report and exit")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--screenshot", metavar="PNG", help="with --benchmark: save a screenshot at the end")
    args = p.parse_args(argv)
    if args.rows < 1 or args.cols < 1:
        p.error("rows and cols must be at least 1")
    if not os.path.isfile(args.video):
        p.error(f"file not found: {args.video}")
    args.video = os.path.abspath(args.video)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if sys.platform == "darwin":
        # macOS only offers modern OpenGL through a core profile.
        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv[:1])
    # mpv requires the C numeric locale; Qt changes it when the app is created.
    locale.setlocale(locale.LC_NUMERIC, "C")

    window = SpikeWindow(args)
    window.showFullScreen() if args.fullscreen else window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
