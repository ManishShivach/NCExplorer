"""Animation dock: play a NetCDF layer over its time dimension.

Playback drives the *existing* time-index path — ``set_netcdf_time_index``
followed by ``update_netcdf_layer`` — rather than poking the artist directly, so
a frame change here is indistinguishable from one made by the standalone time
slider or the property editor, and every listener (colorbar, property panel)
updates the way it always has.

Two design points are worth stating outright:

* **Frames are dropped, never queued.** The timer does not advance by one step
  per tick; it computes which frame *should* be showing from the wall clock and
  jumps there. On a grid too large to render at the requested rate the animation
  runs slow-but-honest instead of accumulating a backlog that keeps rendering
  long after the user hit pause.
* **Export renders on the UI thread on purpose.** A matplotlib figure owned by a
  Qt widget cannot be drawn from a worker, so the frame loop stays here and
  keeps the window alive by pumping the event loop between frames — which is
  also what makes the Cancel button work. Only the encoder is free to be slow.
"""

from __future__ import annotations

import logging
import os
import time

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
    QProgressDialog, QPushButton, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..geocanvas.properties import find_case_insensitive_key
from ..utils.timeaxis import read_time_axis

logger = logging.getLogger(__name__)

# Dimension names that are definitely *not* the time axis.
SPATIAL_DIMS = frozenset({
    "lat", "latitude", "lon", "longitude", "x", "y", "nlat", "nlon",
    "lev", "level", "plev", "depth", "height", "z", "bnds", "nv", "nvertices",
})

MIN_FPS = 1
MAX_FPS = 30
DEFAULT_FPS = 6

#: Cheap placeholder shown when no layer can be animated.
NO_LAYER = "— no time-varying layer —"


def find_time_dimension(dataset, props=None) -> str | None:
    """Name of the animatable dimension of ``dataset``, or None.

    Prefers whatever the loader already recorded, then a dimension literally
    called time, and only then falls back to "the one dimension that is clearly
    not spatial" — the same order of preference the standalone slider uses.
    """
    recorded = getattr(getattr(props, "netcdf", None), "time_dimension", None)
    if recorded and recorded in dataset.dims:
        return recorded

    try:
        dims = list(dataset.dims)
    except Exception:
        return None

    named = find_case_insensitive_key(dims, "time", "t")
    if named:
        return named

    for dim in dims:
        if dim.lower() not in SPATIAL_DIMS and dataset.sizes.get(dim, 0) > 1:
            return dim
    return None


class TimePlayerDock(QDockWidget):
    """Transport controls and a frame exporter for one NetCDF layer."""

    def __init__(self, main_window):
        super().__init__("Animation", main_window)
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea
        )

        self.main_window = main_window
        self.canvas = main_window.geo_canvas

        self._layer_name: str | None = None
        self._time_dim: str | None = None
        self._labels: list[str] = []
        self._frame_count = 0
        self._index = 0

        # Re-entry guard: set while we are the ones changing the index, so the
        # time_index_changed we cause does not bounce back and re-render.
        self._syncing = False
        self._rendering = False

        self._playing = False
        self._play_started_at = 0.0
        self._play_start_index = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()

        # Keep in step with everything else that can move the time index.
        self.canvas.time_index_changed.connect(self._on_external_index_changed)
        self.canvas.layer_added.connect(lambda *_: self.refresh_layers())
        self.canvas.layer_removed.connect(lambda *_: self.refresh_layers())

        self.refresh_layers()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        container = QWidget(self)
        root = QVBoxLayout(container)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # --- top row: layer picker and the decoded date ------------------
        top = QHBoxLayout()
        top.setSpacing(6)

        top.addWidget(QLabel("Layer:"))
        self.layer_combo = QComboBox()
        self.layer_combo.setMinimumWidth(180)
        self.layer_combo.currentTextChanged.connect(self._on_layer_selected)
        top.addWidget(self.layer_combo)

        top.addSpacing(12)
        self.time_label = QLabel("—")
        self.time_label.setToolTip("Date of the frame on screen")
        font = self.time_label.font()
        font.setBold(True)
        self.time_label.setFont(font)
        top.addWidget(self.time_label)

        self.frame_label = QLabel("")
        self.frame_label.setStyleSheet("color: palette(mid);")
        top.addWidget(self.frame_label)

        top.addStretch(1)
        root.addLayout(top)

        # --- transport ---------------------------------------------------
        controls = QHBoxLayout()
        controls.setSpacing(4)

        self.first_button = self._tool_button("|◀", "Jump to first frame", self.go_first)
        self.back_button = self._tool_button("◀", "Step back (,)", self.step_back)
        self.play_button = self._tool_button("▶", "Play / pause (Space)", self.toggle_play)
        self.forward_button = self._tool_button("▶", "Step forward (.)", self.step_forward)
        self.last_button = self._tool_button("▶|", "Jump to last frame", self.go_last)
        for button in (self.first_button, self.back_button, self.play_button,
                       self.forward_button, self.last_button):
            controls.addWidget(button)

        self.loop_button = self._tool_button("↻", "Loop at the end of the series", None)
        self.loop_button.setCheckable(True)
        self.loop_button.setChecked(True)
        controls.addWidget(self.loop_button)

        controls.addSpacing(10)
        controls.addWidget(QLabel("fps:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(MIN_FPS, MAX_FPS)
        self.fps_spin.setValue(DEFAULT_FPS)
        self.fps_spin.setToolTip("Target frames per second; large grids may play slower")
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        controls.addWidget(self.fps_spin)

        controls.addSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.setTracking(True)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider.valueChanged.connect(self._on_slider_moved)
        controls.addWidget(self.slider, 1)

        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Write the animation to a GIF or MP4 file")
        self.export_button.clicked.connect(self.export_animation)
        controls.addWidget(self.export_button)

        root.addLayout(controls)
        self.setWidget(container)

    def _tool_button(self, text, tooltip, slot):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFixedWidth(38)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------
    # Layer discovery
    # ------------------------------------------------------------------
    def animatable_layers(self) -> list[str]:
        """Every loaded NetCDF layer that has more than one timestep."""
        found = []
        try:
            layers = dict(self.canvas.layers)
        except Exception:
            return found

        for name, layer in layers.items():
            if layer.get('type') != 'netcdf':
                continue
            dataset = layer.get('dataset')
            if dataset is None:
                continue
            try:
                props = self.canvas.property_manager.get_layer_property(name)
            except Exception:
                props = None
            dim = find_time_dimension(dataset, props)
            if dim and dataset.sizes.get(dim, 0) > 1:
                found.append(name)
        return found

    def refresh_layers(self):
        """Rebuild the layer list, keeping the current selection if it survives."""
        try:
            names = self.animatable_layers()
        except Exception as exc:
            logger.error("Could not list animatable layers: %s", exc, exc_info=True)
            names = []

        previous = self._layer_name
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        self.layer_combo.addItems(names or [NO_LAYER])
        if previous in names:
            self.layer_combo.setCurrentText(previous)
        self.layer_combo.blockSignals(False)

        chosen = self.layer_combo.currentText()
        self._set_layer(chosen if names else None)

    def _on_layer_selected(self, name):
        if name and name != NO_LAYER:
            self._set_layer(name)

    def _set_layer(self, name):
        """Point the transport at ``name`` and read its time axis."""
        self.pause()
        self._layer_name = name
        self._time_dim = None
        self._labels = []
        self._frame_count = 0
        self._index = 0

        enabled = False
        if name:
            try:
                enabled = self._read_axis(name)
            except Exception as exc:
                logger.error("Could not read the time axis of '%s': %s", name, exc,
                             exc_info=True)
                self.canvas.status_update.emit(f"Could not read the time axis of '{name}'")

        self._set_controls_enabled(enabled)

        self.slider.blockSignals(True)
        self.slider.setMaximum(max(0, self._frame_count - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)

        if enabled:
            # Adopt whatever frame the layer is already showing rather than
            # snapping it back to zero just because the dock opened.
            self._index = self._current_layer_index()
            self.slider.blockSignals(True)
            self.slider.setValue(self._index)
            self.slider.blockSignals(False)

        self._update_labels()

    def _read_axis(self, name) -> bool:
        """Load the decoded labels for ``name``; False when it cannot animate."""
        layer = self.canvas.layers.get(name)
        if not layer or layer.get('type') != 'netcdf':
            return False
        dataset = layer.get('dataset')
        if dataset is None:
            return False

        try:
            props = self.canvas.property_manager.get_layer_property(name)
        except Exception:
            props = None

        dim = find_time_dimension(dataset, props)
        if not dim:
            return False

        count = int(dataset.sizes.get(dim, 0))
        if count <= 1:
            return False

        axis = read_time_axis(dataset, dim)
        self._time_dim = dim
        self._labels = list(axis.labels)
        self._frame_count = count

        # update_netcdf_layer() renders from the property manager's variable, so
        # a layer whose property was never populated would refuse to redraw.
        # Seed it from what the layer itself is displaying.
        self._ensure_variable_recorded(name, layer, props, dataset)
        return True

    @staticmethod
    def _ensure_variable_recorded(name, layer, props, dataset):
        netcdf_props = getattr(props, "netcdf", None)
        if netcdf_props is None:
            return
        current = getattr(netcdf_props, "current_variable", None)
        if current and current in dataset.data_vars:
            return
        fallback = layer.get('variable')
        if fallback and fallback in dataset.data_vars:
            netcdf_props.current_variable = fallback
            logger.debug("Seeded current_variable=%s for layer '%s'", fallback, name)

    def _current_layer_index(self) -> int:
        try:
            props = self.canvas.property_manager.get_layer_property(self._layer_name)
            index = int(getattr(props.netcdf, "current_time_index", 0) or 0)
        except Exception:
            index = 0
        return max(0, min(self._frame_count - 1, index))

    def _set_controls_enabled(self, enabled):
        for widget in (self.first_button, self.back_button, self.play_button,
                       self.forward_button, self.last_button, self.loop_button,
                       self.fps_spin, self.slider, self.export_button):
            widget.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    @property
    def is_playing(self) -> bool:
        return self._playing

    def toggle_play(self):
        self.pause() if self._playing else self.play()

    def play(self):
        if self._frame_count <= 1 or self._playing:
            return
        self._playing = True
        self.play_button.setText("❚❚")
        self._restart_clock()
        self._timer.start(self._interval_ms())

    def pause(self):
        if not self._playing:
            return
        self._playing = False
        self._timer.stop()
        self.play_button.setText("▶")

    def step_forward(self):
        self.pause()
        self._go_to((self._index + 1) % max(1, self._frame_count))

    def step_back(self):
        self.pause()
        self._go_to((self._index - 1) % max(1, self._frame_count))

    def go_first(self):
        self.pause()
        self._go_to(0)

    def go_last(self):
        self.pause()
        self._go_to(max(0, self._frame_count - 1))

    def _interval_ms(self) -> int:
        return max(1, int(round(1000.0 / max(MIN_FPS, self.fps_spin.value()))))

    def _restart_clock(self):
        self._play_started_at = time.perf_counter()
        self._play_start_index = self._index

    def _on_fps_changed(self, _value):
        # Re-base the clock so the new rate takes effect from now rather than
        # replaying the elapsed time at the new speed.
        if self._playing:
            self._restart_clock()
            self._timer.start(self._interval_ms())

    def _on_tick(self):
        if not self._playing or self._frame_count <= 1:
            return
        if self._rendering:
            return  # a previous frame is still drawing; this tick is dropped

        elapsed = time.perf_counter() - self._play_started_at
        advanced = int(elapsed * self.fps_spin.value())
        target = self._play_start_index + advanced

        if target >= self._frame_count:
            if not self.loop_button.isChecked():
                self._go_to(self._frame_count - 1)
                self.pause()
                return
            target %= self._frame_count

        if target == self._index:
            return  # the clock has not reached the next frame yet
        self._go_to(target)

    def _on_slider_moved(self, value):
        if self._syncing:
            return
        self.pause()
        self._go_to(int(value))

    def _go_to(self, index):
        """Show frame ``index``, updating the map and every synced widget."""
        if self._frame_count <= 0 or not self._layer_name:
            return
        index = max(0, min(self._frame_count - 1, int(index)))
        self._index = index

        self._syncing = True
        self._rendering = True
        try:
            self._render_frame(index)
        except Exception as exc:
            # A bad frame pauses playback rather than raising once per tick.
            logger.error("Could not render frame %s of '%s': %s", index,
                         self._layer_name, exc, exc_info=True)
            self.pause()
            self.canvas.status_update.emit(f"Animation stopped: {exc}")
        finally:
            self._rendering = False
            self._syncing = False

        self._sync_widgets(index)
        self._update_labels()

    def _render_frame(self, index):
        """Move the layer to ``index`` through the canvas's own time path."""
        self.canvas.set_netcdf_time_index(self._layer_name, index)
        self.canvas.update_netcdf_layer(self._layer_name)

    def _sync_widgets(self, index):
        """Mirror the index onto our slider and the standalone time slider."""
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)

        # The pop-up slider from the layer context menu is a separate widget
        # with its own state; move it too, with signals off so it does not
        # trigger a second render of the frame we just drew.
        dialog = getattr(self.canvas, 'time_dialog', None)
        slider = getattr(self.canvas, 'time_slider', None)
        if dialog is None or slider is None:
            return
        try:
            if not dialog.isVisible():
                return
            if getattr(self.canvas, 'current_time_layer', None) != self._layer_name:
                return
            if 0 <= index <= slider.maximum():
                slider.blockSignals(True)
                slider.setValue(index)
                slider.blockSignals(False)
                label = getattr(self.canvas, 'time_label', None)
                if label is not None:
                    label.setText(f"Time: {self.label_for(index)}")
        except RuntimeError:
            # The dialog was closed and its C++ side deleted; nothing to sync.
            pass

    def label_for(self, index) -> str:
        if 0 <= index < len(self._labels):
            return self._labels[index]
        return str(index)

    def _update_labels(self):
        if self._frame_count <= 0:
            self.time_label.setText("—")
            self.frame_label.setText("")
            return
        self.time_label.setText(self.label_for(self._index))
        self.frame_label.setText(f"({self._index + 1} / {self._frame_count})")

    def _on_external_index_changed(self, layer_name, index):
        """Follow a time change made elsewhere (slider, property editor)."""
        if self._syncing or layer_name != self._layer_name:
            return
        index = max(0, min(self._frame_count - 1, int(index)))
        if index == self._index:
            return
        # Someone else already rendered this frame; only the widgets need moving.
        self.pause()
        self._index = index
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        self._update_labels()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    @staticmethod
    def _imageio():
        try:
            import imageio.v2 as imageio
            return imageio
        except Exception:
            try:
                import imageio
                return imageio
            except Exception:
                return None

    @staticmethod
    def _ffmpeg_available() -> bool:
        try:
            import imageio_ffmpeg  # noqa: F401
            return True
        except Exception:
            return False

    def export_animation(self):
        """Write every frame to a GIF or MP4 chosen by the user."""
        if self._frame_count <= 1 or not self._layer_name:
            self.canvas.status_update.emit("Nothing to export: no animated layer")
            return

        imageio = self._imageio()
        if imageio is None:
            self.canvas.status_update.emit(
                "Export needs the 'imageio' package — pip install imageio"
            )
            return

        self.pause()

        has_ffmpeg = self._ffmpeg_available()
        filters = ["Animated GIF (*.gif)"]
        if has_ffmpeg:
            filters.append("MP4 video (*.mp4)")

        title = "Export animation"
        if not has_ffmpeg:
            # Said in the chooser's own title, because a user who came looking
            # for MP4 will otherwise just see it missing with no explanation.
            title += " (GIF only — install imageio-ffmpeg for MP4)"

        default = os.path.join(
            os.path.expanduser("~"), f"{self._safe_stem()}.gif"
        )
        path, selected = QFileDialog.getSaveFileName(
            self.main_window, title, default, ";;".join(filters)
        )
        if not path:
            return

        extension = os.path.splitext(path)[1].lower()
        if not extension:
            extension = ".mp4" if "MP4" in (selected or "") else ".gif"
            path += extension

        if extension in (".mp4", ".m4v", ".mov") and not has_ffmpeg:
            self.canvas.status_update.emit(
                "MP4 export needs the 'imageio-ffmpeg' package — writing GIF instead"
            )
            path = os.path.splitext(path)[0] + ".gif"
            extension = ".gif"

        try:
            written = self._write_frames(imageio, path, extension)
        except Exception as exc:
            logger.error("Animation export failed: %s", exc, exc_info=True)
            self.canvas.status_update.emit(f"Export failed: {exc}")
            return

        if written is None:
            self.canvas.status_update.emit("Export cancelled")
        else:
            self.canvas.status_update.emit(
                f"Exported {written} frames to {os.path.basename(path)}"
            )

    def _safe_stem(self) -> str:
        stem = (self._layer_name or "animation").strip()
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem) or "animation"

    def _write_frames(self, imageio, path, extension):
        """Render and encode every frame; returns the count, or None if cancelled."""
        fps = max(MIN_FPS, self.fps_spin.value())
        start_index = self._index

        progress = QProgressDialog(
            f"Exporting {self._frame_count} frames…", "Cancel", 0, self._frame_count,
            self.main_window,
        )
        progress.setWindowTitle("Export animation")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        writer_kwargs = {"fps": fps}
        if extension == ".gif":
            writer_kwargs["loop"] = 0 if self.loop_button.isChecked() else 1
        else:
            # yuv420p is what ordinary players expect; without it the file opens
            # in ffplay and nowhere else.
            writer_kwargs.update(macro_block_size=None, pixelformat="yuv420p")

        written = 0
        cancelled = False
        writer = imageio.get_writer(path, **writer_kwargs)
        try:
            for index in range(self._frame_count):
                if progress.wasCanceled():
                    cancelled = True
                    break

                self._go_to(index)
                frame = self._grab_frame(even_dimensions=(extension != ".gif"))
                if frame is not None:
                    writer.append_data(frame)
                    written += 1

                progress.setValue(index + 1)
                # Keeps the dialog painting and the Cancel button live while the
                # UI thread is busy rendering frames.
                QApplication.processEvents()
        finally:
            try:
                writer.close()
            except Exception as exc:
                logger.warning("Could not close the export writer: %s", exc)
            progress.close()
            self._go_to(start_index)  # leave the map on the frame we started from

        if cancelled:
            # A half-written file is worse than none: it looks like a successful
            # export until it is opened.
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug("Could not remove the cancelled export %s: %s", path, exc)
            return None

        return written

    def _grab_frame(self, even_dimensions=False):
        """Capture the figure as an RGB array, or None if the buffer is unusable."""
        try:
            self.canvas.draw()
            buffer = np.asarray(self.canvas.buffer_rgba())
        except Exception as exc:
            logger.warning("Could not capture a frame: %s", exc)
            return None

        if buffer.ndim != 3 or buffer.shape[2] < 3:
            return None
        frame = buffer[:, :, :3]

        if even_dimensions:
            # H.264 needs even dimensions; trimming a row or column is cheaper
            # and less visible than rescaling the whole frame.
            height = frame.shape[0] - (frame.shape[0] % 2)
            width = frame.shape[1] - (frame.shape[1] % 2)
            frame = frame[:height, :width]

        return np.ascontiguousarray(frame)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self.pause()
        super().closeEvent(event)
