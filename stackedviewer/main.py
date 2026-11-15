"""StackedViewer application: start screen, then the tiled player.

    python -m stackedviewer [VIDEO]
"""

from __future__ import annotations

import argparse
import locale
import os
import sys
from types import SimpleNamespace

from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from .settings import Settings
from .spike import SpikeWindow
from .start_screen import STYLE, PlayConfig, StartScreen


class MainWindow(QMainWindow):
    def __init__(self, video: str | None = None):
        super().__init__()
        self.setWindowTitle("StackedViewer")
        self.resize(1000, 720)
        self.setMinimumSize(720, 560)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.player = None

        self.start_screen = StartScreen(Settings())
        self.start_screen.setStyleSheet(STYLE)
        self.start_screen.start_requested.connect(self.play)
        self.stack.addWidget(self.start_screen)
        if video:
            self.start_screen.load_video(video)

    def play(self, config: PlayConfig):
        # Milestone 2 reuses the spike grid as the player. Esc returns here.
        args = SimpleNamespace(
            video=config.video, rows=config.rows, cols=config.cols, offset=config.offset_min,
            start=config.start_s, hwdec="auto-safe", benchmark=0, screenshot=None,
        )
        self.player = SpikeWindow(args, self)
        self.player.closed.connect(self._player_closed)
        self.stack.addWidget(self.player)
        self.stack.setCurrentWidget(self.player)
        self.player.setFocus()
        self.setWindowTitle(f"StackedViewer - {os.path.basename(config.video)}")

    def _player_closed(self):
        if self.player is None:
            return
        player, self.player = self.player, None
        if self.isFullScreen():
            self.showNormal()
        self.stack.setCurrentWidget(self.start_screen)
        self.stack.removeWidget(player)
        player.deleteLater()
        self.setWindowTitle("StackedViewer")

    def closeEvent(self, event):
        if self.player is not None:
            self.player.close()  # frees the mpv instances
        self.start_screen.shutdown()
        super().closeEvent(event)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stackedviewer", description="Tiled, time-offset video player.")
    parser.add_argument("video", nargs="?", help="video file to preload on the start screen")
    args = parser.parse_args(argv)
    if args.video and not os.path.isfile(args.video):
        parser.error(f"file not found: {args.video}")

    if sys.platform == "darwin":
        # macOS only offers modern OpenGL through a core profile.
        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("StackedViewer")
    # mpv requires the C numeric locale; Qt changes it when the app is created.
    locale.setlocale(locale.LC_NUMERIC, "C")

    window = MainWindow(os.path.abspath(args.video) if args.video else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
