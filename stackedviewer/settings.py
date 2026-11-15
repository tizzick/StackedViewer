"""Remembers the last file, layout and offset between runs."""

from __future__ import annotations

from PySide6.QtCore import QSettings

MAX_ROWS = 12
MAX_COLS = 12


class Settings:
    def __init__(self):
        self._s = QSettings("StackedViewer", "StackedViewer")

    @property
    def video(self) -> str:
        return str(self._s.value("video", "") or "")

    @property
    def rows(self) -> int:
        return self._int("rows", 2, 1, MAX_ROWS)

    @property
    def cols(self) -> int:
        return self._int("cols", 2, 1, MAX_COLS)

    @property
    def offset_min(self) -> float:
        try:
            return max(0.0, float(self._s.value("offset_min", 5.0)))
        except (TypeError, ValueError):
            return 5.0

    @property
    def start_s(self) -> float:
        """Where the first tile started last time, for the remembered video."""
        try:
            return max(0.0, float(self._s.value("start_s", 0.0)))
        except (TypeError, ValueError):
            return 0.0

    @property
    def show_stats(self) -> bool:
        return str(self._s.value("show_stats", "false")).lower() in ("true", "1")

    def save(self, video: str, rows: int, cols: int, offset_min: float, start_s: float = 0.0,
             show_stats: bool = False) -> None:
        self._s.setValue("video", video)
        self._s.setValue("rows", rows)
        self._s.setValue("cols", cols)
        self._s.setValue("offset_min", offset_min)
        self._s.setValue("start_s", start_s)
        self._s.setValue("show_stats", "true" if show_stats else "false")
        self._s.sync()

    def _int(self, key: str, default: int, low: int, high: int) -> int:
        try:
            return min(high, max(low, int(self._s.value(key, default))))
        except (TypeError, ValueError):
            return default
