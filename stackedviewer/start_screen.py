"""Start screen: choose the video, the grid layout and the offset."""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .probe import VideoProbe
from .settings import MAX_COLS, MAX_ROWS, Settings
from .sync import format_time, tile_starts
from .thumbnails import Thumbnailer

PRESETS = [(1, 2), (2, 2), (3, 3), (4, 4)]
# Largest grid confirmed smooth in testing so far. Above this, warn.
LARGE_GRID_TILES = 16

VIDEO_FILTER = (
    "Video files (*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.wmv *.flv *.mpg *.mpeg *.ts *.m2ts *.ogv *.3gp);;"
    "All files (*)"
)

SLIDER_UNITS_PER_S = 10  # the start slider moves in tenths of a second

ACCENT = "#4c9aff"
WRAP = "#f5a623"


@dataclass
class PlayConfig:
    video: str
    rows: int
    cols: int
    offset_min: float
    duration: float
    start_s: float = 0.0
    show_stats: bool = False


class GridPreview(QWidget):
    """Draws the grid: each tile shows the video frame at its start time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = 2
        self.cols = 2
        self.starts: list[tuple[float, bool]] = []
        self.images: dict[int, QImage] = {}
        self.aspect = 16 / 9
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_grid(self, rows, cols, starts, aspect):
        self.rows, self.cols, self.starts = rows, cols, starts
        self.aspect = aspect or 16 / 9
        # Frames stay on screen until their replacements arrive, so the
        # preview does not flash while the slider moves.
        for index in [i for i in self.images if i >= rows * cols]:
            del self.images[index]
        self.update()

    def set_image(self, index: int, image: QImage):
        self.images[index] = image
        self.update()

    def clear_images(self):
        self.images.clear()
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

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
        font.setPixelSize(max(7, int(min(cell_h * 0.16, cell_w * 0.12, 15))))
        metrics = QFontMetrics(font)
        show_text = cell_w >= 40

        for i in range(self.rows * self.cols):
            r, c = divmod(i, self.cols)
            rect = QRectF(x0 + c * (cell_w + gap), y0 + r * (cell_h + gap), cell_w, cell_h)
            start, wrapped = self.starts[i] if i < len(self.starts) else (0.0, False)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1b1e25"))
            p.drawRect(rect)
            image = self.images.get(i)
            if image is not None:
                p.drawImage(rect, image)

            if show_text:
                text = format_time(start) if self.starts else "--:--:--"
                if wrapped and cell_w >= 110:
                    text += "  wraps"
                pad = 4
                label = QRectF(rect.left(), rect.bottom() - metrics.height() - pad,
                               metrics.horizontalAdvance(text) + 2 * pad + 2, metrics.height() + pad)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawRect(label)
                p.setPen(QColor(WRAP if wrapped else "#ffffff"))
                p.setFont(font)
                p.drawText(label, Qt.AlignmentFlag.AlignCenter, text)

            p.setPen(QPen(QColor(WRAP if wrapped else "#3a3f4b"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
        p.end()


class StartScreen(QWidget):
    start_requested = Signal(object)  # PlayConfig

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.info: dict | None = None
        self._probe: VideoProbe | None = None
        self._thumbs: Thumbnailer | None = None
        self._pending_start_s = 0.0
        # Exact frames are fetched once the controls have been still briefly.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(180)
        self._settle.timeout.connect(lambda: self._request_frames(exact=True))
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
        root.addWidget(self._caption("Preview: the frame each tile starts on"))
        self.preview = GridPreview()
        root.addWidget(self.preview, 1)

        # ---- first tile start ----
        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        slider_row.addWidget(self._caption("First tile starts at"))
        self.start_slider = QSlider(Qt.Orientation.Horizontal)
        self.start_slider.setRange(0, 0)
        self.start_slider.setSingleStep(SLIDER_UNITS_PER_S)        # arrow keys: 1 second
        self.start_slider.setPageStep(60 * SLIDER_UNITS_PER_S)     # page keys: 1 minute
        self.start_slider.setEnabled(False)
        slider_row.addWidget(self.start_slider, 1)
        self.start_label = QLabel("--:--:-- / --:--:--")
        self.start_label.setObjectName("timeReadout")
        slider_row.addWidget(self.start_label)
        root.addLayout(slider_row)

        self.notes = QLabel("")
        self.notes.setObjectName("notes")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self.notes)

        # ---- start ----
        bottom = QHBoxLayout()
        self.stats_check = QCheckBox("Show playback info on each tile (time, frame rate, dropped frames)")
        self.stats_check.setChecked(settings.show_stats)
        bottom.addWidget(self.stats_check)
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
        self.start_slider.valueChanged.connect(self._slider_moved)
        self.start_slider.sliderReleased.connect(lambda: self._request_frames(exact=True))
        self._refresh()

        if settings.video and os.path.isfile(settings.video):
            self.load_video(settings.video, start_s=settings.start_s)

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

    def load_video(self, path: str, start_s: float = 0.0):
        if self._probe is not None:
            self._probe.cancel()
            self._probe.deleteLater()
        self._close_thumbs()
        self.info = None
        self._pending_start_s = start_s
        self.preview.clear_images()
        self.start_slider.setEnabled(False)
        self.start_slider.blockSignals(True)
        self.start_slider.setRange(0, 0)
        self.start_slider.blockSignals(False)
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
        # The slider covers the whole video, in tenths of a second.
        start_s = min(self._pending_start_s, info["duration"])
        self.start_slider.blockSignals(True)
        self.start_slider.setRange(0, int(info["duration"] * SLIDER_UNITS_PER_S))
        self.start_slider.setValue(int(start_s * SLIDER_UNITS_PER_S))
        self.start_slider.blockSignals(False)
        self.start_slider.setEnabled(True)

        self._thumbs = Thumbnailer(info["path"], info["width"], info["height"], self)
        self._thumbs.ready.connect(self.preview.set_image)
        self._refresh()

    def _probe_failed(self, message: str):
        self.info = None
        self.file_info.setText(message)
        self.file_info.setStyleSheet("color: #ff6b6b;")
        self._refresh()

    def _close_thumbs(self):
        self._settle.stop()
        if self._thumbs is not None:
            self._thumbs.close()
            self._thumbs.deleteLater()
            self._thumbs = None

    def shutdown(self):
        """Release the hidden mpv instances. Call before the app exits."""
        if self._probe is not None:
            self._probe.cancel()
        self._close_thumbs()

    # ---- first tile start ----

    def first_start_s(self) -> float:
        return self.start_slider.value() / SLIDER_UNITS_PER_S

    def _slider_moved(self, _value):
        self._refresh(fast_frames=True)

    def _request_frames(self, exact: bool):
        if self._thumbs is None or not self.info:
            return
        if exact:
            self._settle.stop()
        count = self.rows_spin.value() * self.cols_spin.value()
        starts = tile_starts(count, self.offset_spin.value() * 60.0, self.info["duration"], self.first_start_s())
        # Stay clear of the very end, where there is no frame left to show.
        last = max(0.0, self.info["duration"] - 0.5)
        self._thumbs.request([min(start, last) for start, _ in starts], exact)

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

    def _refresh(self, *_args, fast_frames: bool = False):
        rows, cols = self.rows_spin.value(), self.cols_spin.value()
        offset_s = self.offset_spin.value() * 60.0
        count = rows * cols
        for r, c, button in self.preset_buttons:
            button.setChecked((r, c) == (rows, cols))

        notes = []
        if self.info:
            duration = self.info["duration"]
            first = self.first_start_s()
            starts = tile_starts(count, offset_s, duration, first)
            aspect = self.info["width"] / self.info["height"]
            self.start_label.setText(f"{format_time(first)} / {format_time(duration)}")
            wrapped = sum(1 for _, w in starts if w)
            if wrapped:
                notes.append(
                    f"<span style='color:{WRAP}'>{wrapped} of {count} tiles start past the end of the "
                    f"video ({format_time(duration)}), so they wrap around to the beginning.</span>"
                )
        else:
            starts, aspect = [], 16 / 9
            self.start_label.setText("--:--:-- / --:--:--")
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

        # While the slider is being dragged, fetch quick approximate frames
        # (nearest keyframe); once things settle, fetch the exact ones.
        if fast_frames and self.start_slider.isSliderDown():
            self._request_frames(exact=False)
        self._settle.start()

    def _start(self):
        if not self.info:
            return
        config = PlayConfig(
            video=self.info["path"],
            rows=self.rows_spin.value(),
            cols=self.cols_spin.value(),
            offset_min=self.offset_spin.value(),
            duration=self.info["duration"],
            start_s=self.first_start_s(),
            show_stats=self.stats_check.isChecked(),
        )
        self.settings.save(config.video, config.rows, config.cols, config.offset_min, config.start_s,
                           config.show_stats)
        self.start_requested.emit(config)


STYLE = f"""
QWidget {{ background: #111318; color: #e6e6e6; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#caption {{ color: #8b93a1; font-size: 12px; }}
QLabel#muted {{ color: #8b93a1; font-size: 12px; }}
QLabel#fileName {{ font-size: 15px; font-weight: 600; }}
QLabel#timeReadout {{ font-size: 13px; min-width: 150px; }}
QSlider::groove:horizontal {{ height: 6px; background: #2a2e37; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: #e6e6e6; width: 16px; height: 16px; margin: -5px 0;
                              border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::groove:horizontal:disabled {{ background: #1e2128; }}
QSlider::sub-page:horizontal:disabled {{ background: #2a2e37; }}
QSlider::handle:horizontal:disabled {{ background: #4a4f5a; }}
QCheckBox {{ background: transparent; color: #b8bfca; spacing: 8px; }}
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
