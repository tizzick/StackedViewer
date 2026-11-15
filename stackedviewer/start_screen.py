"""Start screen: choose the video, the grid layout and the offset."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .probe import VideoProbe
from .settings import MAX_COLS, MAX_ROWS, Settings
from .sync import format_time, tile_starts

PRESETS = [(1, 2), (2, 2), (3, 3), (4, 4)]
# Largest grid confirmed smooth in testing so far. Above this, warn.
LARGE_GRID_TILES = 16

VIDEO_FILTER = (
    "Video files (*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.wmv *.flv *.mpg *.mpeg *.ts *.m2ts *.ogv *.3gp);;"
    "All files (*)"
)

ACCENT = "#4c9aff"
WRAP = "#f5a623"


@dataclass
class PlayConfig:
    video: str
    rows: int
    cols: int
    offset_min: float
    duration: float


class GridPreview(QWidget):
    """Draws the grid with each tile's start time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = 2
        self.cols = 2
        self.starts: list[tuple[float, bool]] = []
        self.aspect = 16 / 9
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_grid(self, rows, cols, starts, aspect):
        self.rows, self.cols, self.starts = rows, cols, starts
        self.aspect = aspect or 16 / 9
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Fit a grid of video-shaped cells into the widget.
        gap = 4
        avail_w = self.width() - gap * (self.cols - 1)
        avail_h = self.height() - gap * (self.rows - 1)
        cell_w = min(avail_w / self.cols, avail_h / self.rows * self.aspect)
        cell_h = cell_w / self.aspect
        total_w = cell_w * self.cols + gap * (self.cols - 1)
        total_h = cell_h * self.rows + gap * (self.rows - 1)
        x0 = (self.width() - total_w) / 2
        y0 = (self.height() - total_h) / 2

        font = QFont(self.font())
        font.setPixelSize(max(7, int(min(cell_h * 0.3, cell_w * 0.17, 20))))
        small = QFont(font)
        small.setPixelSize(max(6, int(font.pixelSize() * 0.7)))
        show_index = cell_h >= 44

        for i in range(self.rows * self.cols):
            r, c = divmod(i, self.cols)
            rect = QRectF(x0 + c * (cell_w + gap), y0 + r * (cell_h + gap), cell_w, cell_h)
            start, wrapped = self.starts[i] if i < len(self.starts) else (0.0, False)
            p.setPen(QPen(QColor(WRAP if wrapped else "#3a3f4b"), 1))
            p.setBrush(QColor("#1b1e25"))
            p.drawRoundedRect(rect, 3, 3)

            if cell_w < 34:
                continue  # too small for text
            p.setPen(QColor(WRAP if wrapped else "#e6e6e6"))
            p.setFont(font)
            text = format_time(start) if self.starts else "--:--:--"
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            if show_index:
                p.setPen(QColor("#8b93a1"))
                p.setFont(small)
                label = f"{i + 1}" + ("  wraps" if wrapped else "")
                p.drawText(rect.adjusted(5, 3, -5, -3),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label)
        p.end()


class StartScreen(QWidget):
    start_requested = Signal(object)  # PlayConfig

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.info: dict | None = None
        self._probe: VideoProbe | None = None
        self.setAcceptDrops(True)
        # Needed for a plain QWidget to paint its stylesheet background.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        title = QLabel("StackedViewer")
        title.setObjectName("title")
        root.addWidget(title)

        # ---- file ----
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        file_row = QHBoxLayout(self.drop_zone)
        file_row.setContentsMargins(16, 12, 12, 12)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.file_label = QLabel("Drop a video here, or choose a file")
        self.file_label.setObjectName("fileName")
        self.file_info = QLabel("")
        self.file_info.setObjectName("muted")
        self.file_info.setWordWrap(True)
        text_col.addWidget(self.file_label)
        text_col.addWidget(self.file_info)
        file_row.addLayout(text_col, 1)
        self.browse_btn = QPushButton("Choose file...")
        self.browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self.browse_btn)
        root.addWidget(self.drop_zone)

        # ---- layout + offset ----
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(self._caption("Layout"), 0, 0)
        layout_row = QHBoxLayout()
        layout_row.setSpacing(6)
        self.preset_buttons = []
        for rows, cols in PRESETS:
            b = QPushButton(f"{rows} x {cols}")
            b.setCheckable(True)
            b.setObjectName("preset")
            b.clicked.connect(lambda _=False, r=rows, c=cols: self._apply_preset(r, c))
            layout_row.addWidget(b)
            self.preset_buttons.append((rows, cols, b))
        layout_row.addSpacing(14)
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, MAX_ROWS)
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, MAX_COLS)
        layout_row.addWidget(QLabel("Rows"))
        layout_row.addWidget(self.rows_spin)
        layout_row.addWidget(QLabel("Columns"))
        layout_row.addWidget(self.cols_spin)
        layout_row.addStretch(1)
        form.addLayout(layout_row, 0, 1)

        form.addWidget(self._caption("Offset"), 1, 0)
        offset_row = QHBoxLayout()
        offset_row.setSpacing(6)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(0.0, 9999.0)
        self.offset_spin.setDecimals(2)
        self.offset_spin.setSingleStep(0.5)
        offset_row.addWidget(self.offset_spin)
        offset_row.addWidget(QLabel("minutes between each tile and the next"))
        offset_row.addStretch(1)
        form.addLayout(offset_row, 1, 1)
        form.setColumnStretch(1, 1)
        root.addLayout(form)

        # ---- preview ----
        root.addWidget(self._caption("Start time of each tile"))
        self.preview = GridPreview()
        root.addWidget(self.preview, 1)

        self.notes = QLabel("")
        self.notes.setObjectName("notes")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self.notes)

        # ---- start ----
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start)
        bottom.addWidget(self.start_btn)
        root.addLayout(bottom)

        # restore last settings
        self.rows_spin.setValue(settings.rows)
        self.cols_spin.setValue(settings.cols)
        self.offset_spin.setValue(settings.offset_min)
        self.rows_spin.valueChanged.connect(self._refresh)
        self.cols_spin.valueChanged.connect(self._refresh)
        self.offset_spin.valueChanged.connect(self._refresh)
        self._refresh()

        if settings.video and os.path.isfile(settings.video):
            self.load_video(settings.video)

    @staticmethod
    def _caption(text):
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    # ---- file handling ----

    def _browse(self):
        start_dir = os.path.dirname(self.info["path"]) if self.info else os.path.dirname(self.settings.video)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a video", start_dir, VIDEO_FILTER)
        if path:
            self.load_video(path)

    def load_video(self, path: str):
        if self._probe is not None:
            self._probe.cancel()
            self._probe.deleteLater()
        self.info = None
        self.file_label.setText(os.path.basename(path))
        self.file_info.setText("Reading file...")
        self.file_info.setStyleSheet("")
        self._refresh()
        self._probe = VideoProbe(path, self)
        self._probe.finished.connect(self._probe_finished)
        self._probe.failed.connect(self._probe_failed)

    def _probe_finished(self, info: dict):
        self.info = info
        fps = f"  -  {info['fps']:.2f} fps" if info["fps"] else ""
        self.file_info.setText(
            f"{format_time(info['duration'])}  -  {info['width']} x {info['height']}{fps}\n"
            f"{os.path.dirname(info['path'])}"
        )
        self._refresh()

    def _probe_failed(self, message: str):
        self.info = None
        self.file_info.setText(message)
        self.file_info.setStyleSheet("color: #ff6b6b;")
        self._refresh()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and os.path.isfile(urls[0].toLocalFile()):
            event.acceptProposedAction()
            self.drop_zone.setProperty("dragging", True)
            self._restyle(self.drop_zone)

    def dragLeaveEvent(self, _event):
        self.drop_zone.setProperty("dragging", False)
        self._restyle(self.drop_zone)

    def dropEvent(self, event):
        self.drop_zone.setProperty("dragging", False)
        self._restyle(self.drop_zone)
        self.load_video(event.mimeData().urls()[0].toLocalFile())

    @staticmethod
    def _restyle(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ---- layout + preview ----

    def _apply_preset(self, rows, cols):
        self.rows_spin.setValue(rows)
        self.cols_spin.setValue(cols)
        self._refresh()

    def _refresh(self):
        rows, cols = self.rows_spin.value(), self.cols_spin.value()
        offset_s = self.offset_spin.value() * 60.0
        count = rows * cols
        for r, c, button in self.preset_buttons:
            button.setChecked((r, c) == (rows, cols))

        notes = []
        if self.info:
            duration = self.info["duration"]
            starts = tile_starts(count, offset_s, duration)
            aspect = self.info["width"] / self.info["height"]
            wrapped = sum(1 for _, w in starts if w)
            if wrapped:
                notes.append(
                    f"<span style='color:{WRAP}'>{wrapped} of {count} tiles start past the end of the "
                    f"video ({format_time(duration)}), so they wrap around to the beginning.</span>"
                )
        else:
            starts, aspect = [], 16 / 9
        if count > 1 and offset_s == 0:
            notes.append("The offset is 0, so every tile will show the same moment.")
        if count > LARGE_GRID_TILES:
            notes.append(
                f"<span style='color:{WRAP}'>{count} tiles is a large grid. Playback may stutter, "
                f"depending on this computer and the video's resolution.</span>"
            )
        self.preview.set_grid(rows, cols, starts, aspect)
        self.notes.setText("<br>".join(notes))
        self.start_btn.setEnabled(self.info is not None)

    def _start(self):
        if not self.info:
            return
        config = PlayConfig(
            video=self.info["path"],
            rows=self.rows_spin.value(),
            cols=self.cols_spin.value(),
            offset_min=self.offset_spin.value(),
            duration=self.info["duration"],
        )
        self.settings.save(config.video, config.rows, config.cols, config.offset_min)
        self.start_requested.emit(config)


STYLE = f"""
QWidget {{ background: #111318; color: #e6e6e6; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#caption {{ color: #8b93a1; font-size: 12px; }}
QLabel#muted {{ color: #8b93a1; font-size: 12px; }}
QLabel#fileName {{ font-size: 15px; font-weight: 600; }}
QLabel#notes {{ color: #b8bfca; font-size: 12px; }}
QFrame#dropZone {{ background: #181b22; border: 1px dashed #3a3f4b; border-radius: 8px; }}
QFrame#dropZone[dragging="true"] {{ border: 1px dashed {ACCENT}; background: #1c2330; }}
QPushButton {{ background: #262a33; border: 1px solid #3a3f4b; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: #2f3440; }}
QPushButton:disabled {{ color: #6b7280; background: #1b1e25; border-color: #2a2e37; }}
QPushButton#preset:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: #0b0d11; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #0b0d11;
                       font-weight: 600; padding: 8px 34px; font-size: 14px; }}
QPushButton#primary:hover {{ background: #6aadff; }}
QPushButton#primary:disabled {{ background: #1b1e25; border-color: #2a2e37; color: #6b7280; }}
QSpinBox, QDoubleSpinBox {{ background: #181b22; border: 1px solid #3a3f4b; border-radius: 6px;
                            padding: 4px 6px; min-width: 56px; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
"""
