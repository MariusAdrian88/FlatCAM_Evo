# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/30/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Camera tab of the G-code Sender: device selection, live preview with a fixed
center crosshair, orientation controls and the calibration buttons.

The buttons only report operator captures; the sender reads the machine
position. No button here starts a drill, a jog or any other motion.
"""

import gettext
import builtins
import math
import time

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

from appGUI.GUIElements import FCLabel, FCButton, FCComboBox2, FCCheckBox, FCDoubleSpinner
from appPlugins.ToolGcodeSender.CameraCalibration import (
    ORIENTATIONS, VERIFY_TOLERANCE_MM, VIEW_Z_TOLERANCE_MM)

# a live preview is only usable while frames keep arriving
PREVIEW_FRAME_TIMEOUT = 2.0


def _stored_tolerance(options, key, default):
    try:
        value = float(options.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0.0 else default


def _stored_rotation(options):
    """A malformed stored orientation falls back to 0 instead of crashing the sender."""
    try:
        value = float(options.get('tools_camera_rotation', 0) or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    value = int(value) % 360
    return value if value in ORIENTATIONS else 0

import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class _CrosshairOverlay(QtWidgets.QWidget):
    """The fixed center mark over the preview; it never receives mouse events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor('red'), 1))
        center = self.rect().center()
        painter.drawLine(0, center.y(), self.width(), center.y())
        painter.drawLine(center.x(), 0, center.x(), self.height())
        painter.drawEllipse(center, 6, 6)


class CameraView(QtWidgets.QWidget):
    """Live camera image with the fixed crosshair; rotation and mirror change only the image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = QtWidgets.QGraphicsView(self.scene, self)
        self.view.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet('background: black;')
        self.video_item = None
        self._new_video_item()
        self.overlay = _CrosshairOverlay(self)

    def _new_video_item(self):
        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)
        self._layout_video_item()

    def reset_video_item(self):
        """A new item owns a new video sink: no stale frames or duplicate connections."""
        old = self.video_item
        if old is not None:
            self.scene.removeItem(old)
            old.deleteLater()
        self._new_video_item()

    def _layout_video_item(self):
        self.video_item.setSize(QtCore.QSizeF(max(1, self.width()), max(1, self.height())))
        self.video_item.setTransformOriginPoint(self.video_item.boundingRect().center())
        self.view.fitInView(self.video_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        self.view.setGeometry(self.rect())
        self._layout_video_item()
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()
        super().resizeEvent(event)

    def set_orientation(self, rotation, mirror):
        self.video_item.setRotation(float(rotation))
        self.video_item.setTransform(QtGui.QTransform().scale(-1 if mirror else 1, 1))
        self.video_item.setTransformOriginPoint(self.video_item.boundingRect().center())
        self.view.fitInView(self.video_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.overlay.raise_()


class SenderCameraUI(QtWidgets.QWidget):
    capture_spindle_sig = QtCore.pyqtSignal()
    capture_camera_sig = QtCore.pyqtSignal()
    verify_sig = QtCore.pyqtSignal()
    clear_sig = QtCore.pyqtSignal()
    device_changed_sig = QtCore.pyqtSignal(str)
    orientation_changed_sig = QtCore.pyqtSignal()
    tolerance_changed_sig = QtCore.pyqtSignal()
    # True while the camera delivers current frames, False when the preview stops or fails
    preview_available_sig = QtCore.pyqtSignal(bool)

    def __init__(self, app, parent=None, view=None):
        super().__init__(parent)
        self.app = app
        self.decimals = app.decimals
        units = str(getattr(app, 'app_units', 'MM') or 'MM').upper()
        self.units = units if units in ('MM', 'IN') else 'MM'
        self._devices = []
        self.camera = None
        self.session = None
        self._connected = False
        self._busy = False
        self._has_calibration = False
        self._preview_device_id = ''
        self._preview_live = False
        self._last_frame_time = 0.0
        self._frame_sink = None
        # the live image and its controls can be shared with the board registration page
        self._external_view = view is not None
        self._provided_view = view
        self._build_ui()
        self.refresh_cameras()
        self._preview_guard_timer = QtCore.QTimer(self)
        self._preview_guard_timer.setInterval(1000)
        self._preview_guard_timer.timeout.connect(self._check_preview)
        self._preview_guard_timer.start()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # device selection, preview and orientation: kept visible above both camera tabs
        self.preview_controls = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(self.preview_controls)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)

        camera_row = QtWidgets.QHBoxLayout()
        camera_row.addWidget(FCLabel('%s:' % _("Camera")))
        self.camera_combo = QtWidgets.QComboBox()
        self.camera_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        camera_row.addWidget(self.camera_combo, stretch=1)
        self.refresh_button = FCButton(_("Refresh"))
        self.refresh_button.setToolTip(_("Search for the connected cameras."))
        camera_row.addWidget(self.refresh_button)
        preview_layout.addLayout(camera_row)

        self.preview_button = FCButton(_("Preview"), checkable=True)
        self.preview_button.setToolTip(_("Start or stop the live camera image."))
        preview_layout.addWidget(self.preview_button)

        orientation_row = QtWidgets.QHBoxLayout()
        orientation_row.addWidget(FCLabel('%s:' % _("Orientation")))
        self.orientation_combo = FCComboBox2()
        self.orientation_combo.addItems(['0°', '90°', '180°', '270°'])
        self.orientation_combo.setToolTip(_("Rotate the preview. A change needs revalidation."))
        orientation_row.addWidget(self.orientation_combo)
        self.mirror_cb = FCCheckBox(_("Mirror"))
        self.mirror_cb.setToolTip(_("Mirror the preview horizontally. A change needs revalidation."))
        orientation_row.addWidget(self.mirror_cb)
        orientation_row.addStretch(1)
        preview_layout.addLayout(orientation_row)

        self.view = self._provided_view if self._provided_view is not None else CameraView()
        if not self._external_view:
            layout.addWidget(self.preview_controls)
            layout.addWidget(self.view, stretch=1)

        tolerance_row = QtWidgets.QHBoxLayout()
        self.verify_tolerance_label = FCLabel(_("Repeatability tolerance (%s):") % self.units)
        self.verify_tolerance_label.setToolTip(_(
            "The largest allowed mismatch of the marked point during verification, in current units."))
        tolerance_row.addWidget(self.verify_tolerance_label)
        self.verify_tolerance_entry = FCDoubleSpinner()
        self.verify_tolerance_entry.set_precision(max(self.decimals, 4))
        self.verify_tolerance_entry.set_range(0.0001, 100.0 if self.units == 'MM' else 100.0 / 25.4)
        self.verify_tolerance_entry.set_value(
            VERIFY_TOLERANCE_MM if self.units == 'MM' else VERIFY_TOLERANCE_MM / 25.4)
        self.verify_tolerance_entry.setToolTip(_(
            "The largest allowed mismatch of the marked point during verification, in current units."))
        tolerance_row.addWidget(self.verify_tolerance_entry)

        self.view_tolerance_label = FCLabel(_("Viewing Z tolerance (%s):") % self.units)
        self.view_tolerance_label.setToolTip(_(
            "The largest allowed change of the camera viewing height, in current units."))
        tolerance_row.addWidget(self.view_tolerance_label)
        self.view_tolerance_entry = FCDoubleSpinner()
        self.view_tolerance_entry.set_precision(max(self.decimals, 4))
        self.view_tolerance_entry.set_range(0.0001, 100.0 if self.units == 'MM' else 100.0 / 25.4)
        self.view_tolerance_entry.set_value(
            VIEW_Z_TOLERANCE_MM if self.units == 'MM' else VIEW_Z_TOLERANCE_MM / 25.4)
        self.view_tolerance_entry.setToolTip(_(
            "The largest allowed change of the camera viewing height, in current units."))
        tolerance_row.addWidget(self.view_tolerance_entry)
        tolerance_row.addStretch(1)
        layout.addLayout(tolerance_row)

        self.status_label = FCLabel(_("No camera found. Connect a camera and press Refresh."))
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.calibration_label = FCLabel(_("No calibration saved."))
        self.calibration_label.setWordWrap(True)
        layout.addWidget(self.calibration_label)

        capture_row = QtWidgets.QHBoxLayout()
        self.capture_spindle_button = FCButton(_("Capture spindle XY"))
        self.capture_spindle_button.setToolTip(_(
            "Mount a thin drill in the spindle, put its tip on the reference mark and press.\n"
            "Only the machine position is read: nothing moves."))
        capture_row.addWidget(self.capture_spindle_button)

        self.capture_camera_button = FCButton(_("Capture camera XY + Z"))
        self.capture_camera_button.setToolTip(_(
            "Retract, set the camera viewing height and center the same mark under the crosshair.\n"
            "Only the machine position and viewing Z are read: nothing moves."))
        capture_row.addWidget(self.capture_camera_button)
        layout.addLayout(capture_row)

        action_row = QtWidgets.QHBoxLayout()
        self.verify_button = FCButton(_("Verify repeatability"))
        self.verify_button.setToolTip(_(
            "Center the same mark under the camera at the calibrated viewing Z.\n"
            "The predicted spindle position must match the first reference."))
        action_row.addWidget(self.verify_button)
        self.clear_button = FCButton(_("Clear calibration"))
        self.clear_button.setToolTip(_("Invalidate the saved calibration and require a new one."))
        action_row.addWidget(self.clear_button)
        layout.addLayout(action_row)

        self.guide_label = FCLabel(_(
            "1. Mount a thin drill and center the reference mark with the spindle.\n"
            "2. Capture the spindle XY.\n"
            "3. Retract, set the camera viewing height and center the same mark.\n"
            "4. Capture the camera XY and viewing Z.\n"
            "5. Verify the repeatability at the same mark."))
        self.guide_label.setWordWrap(True)
        layout.addWidget(self.guide_label)

        self.refresh_button.clicked.connect(lambda: self.refresh_cameras(announce=True))
        self.preview_button.toggled.connect(self._on_preview_toggled)
        self.camera_combo.currentIndexChanged.connect(self._on_device_changed)
        self.orientation_combo.currentIndexChanged.connect(self._on_orientation_changed)
        self.mirror_cb.toggled.connect(self._on_orientation_changed)
        self.verify_tolerance_entry.valueChanged.connect(self._on_tolerance_changed)
        self.view_tolerance_entry.valueChanged.connect(self._on_tolerance_changed)
        self.capture_spindle_button.clicked.connect(self.capture_spindle_sig)
        self.capture_camera_button.clicked.connect(self.capture_camera_sig)
        self.verify_button.clicked.connect(self.verify_sig)
        self.clear_button.clicked.connect(self.clear_sig)
        self.set_calibration_state('none', 'no_calibration', (0.0, 0.0), 0.0, False)

    def restore(self, options):
        self.refresh_cameras(selected=str(options.get('tools_camera_device', '') or ''))
        rotation = _stored_rotation(options)
        mirror = bool(options.get('tools_camera_mirror', False))
        saved_units = str(options.get('tools_camera_tolerance_units', 'MM') or 'MM').upper()
        if saved_units not in ('MM', 'IN'):
            saved_units = 'MM'
        scale = 1.0 if saved_units == self.units else (25.4 if self.units == 'MM' else 1.0 / 25.4)
        verify_tolerance = _stored_tolerance(
            options, 'tools_camera_verify_tolerance', VERIFY_TOLERANCE_MM) * scale
        view_tolerance = _stored_tolerance(
            options, 'tools_camera_view_tolerance', VIEW_Z_TOLERANCE_MM) * scale
        for widget in (self.orientation_combo, self.mirror_cb,
                       self.verify_tolerance_entry, self.view_tolerance_entry):
            widget.blockSignals(True)
        try:
            self.orientation_combo.setCurrentIndex(
                ORIENTATIONS.index(rotation) if rotation in ORIENTATIONS else 0)
            self.mirror_cb.set_value(mirror)
            self.verify_tolerance_entry.set_value(verify_tolerance)
            self.view_tolerance_entry.set_value(view_tolerance)
        finally:
            for widget in (self.orientation_combo, self.mirror_cb,
                           self.verify_tolerance_entry, self.view_tolerance_entry):
                widget.blockSignals(False)
        self.view.set_orientation(self.rotation(), self.mirror())

    @staticmethod
    def _device_id(device):
        """:return: the stable Qt device id as hex text; the description is only a label."""
        try:
            return bytes(device.id()).hex()
        except (AttributeError, TypeError, ValueError):
            return ''

    def refresh_cameras(self, selected=None, announce=False):
        previous = self.current_camera_id()
        prefer = str(selected or previous or '')
        try:
            devices = list(QMediaDevices.videoInputs())
        except Exception:
            devices = []
        self._devices = devices
        self.camera_combo.blockSignals(True)
        try:
            self.camera_combo.clear()
            for index, device in enumerate(devices):
                description = device.description() or _("Camera %d") % (index + 1)
                self.camera_combo.addItem(description, self._device_id(device))
            if prefer:
                index = self.camera_combo.findData(prefer)
                # a disappeared selection stays empty instead of silently using another camera
                self.camera_combo.setCurrentIndex(index if index >= 0 else -1)
        finally:
            self.camera_combo.blockSignals(False)

        if self._preview_device_id and self._preview_device_id != self.current_camera_id():
            self.stop_preview()

        if not devices:
            if self.preview_button.isChecked():
                self.preview_button.setChecked(False)
            self.stop_preview()
            self.preview_button.setEnabled(False)
            self.status_label.setText(_("No camera found. Connect a camera and press Refresh."))
            if prefer:
                self.device_changed_sig.emit('')
            if announce:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No camera found."))
            return
        self.preview_button.setEnabled(True)
        if self.current_camera_id():
            self.status_label.setText(_("Camera selected. Press Preview."))
        else:
            self.status_label.setText(_("The selected camera is gone. Choose a camera and press Refresh."))
        if announce:
            self.app.inform.emit("%s" % _("%d camera(s) found.") % len(devices))
        if self.current_camera_id() != previous:
            self.device_changed_sig.emit(self.current_camera_id())

    def set_connected(self, connected):
        self._connected = bool(connected)
        self._update_buttons()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_buttons()

    def _update_buttons(self):
        ready = self._connected and not self._busy
        preview = self.preview_usable()
        self.capture_spindle_button.setEnabled(ready)
        self.capture_camera_button.setEnabled(ready and preview)
        self.verify_button.setEnabled(ready and preview and self._has_calibration)

    def set_calibration_state(self, state, reason, offset, view_z, has_calibration):
        self._has_calibration = bool(has_calibration)
        decimals = self.decimals
        if not has_calibration:
            text = _("No calibration saved.")
        elif state == 'verified':
            text = _("Calibration verified this session. Offset X %.*f  Y %.*f, viewing Z %.*f.") % (
                decimals, offset[0], decimals, offset[1], decimals, view_z)
        elif state == 'stored':
            text = _("Saved calibration (not verified this session). Offset X %.*f  Y %.*f, viewing Z %.*f.") % (
                decimals, offset[0], decimals, offset[1], decimals, view_z)
        elif state == 'z_mismatch':
            text = _("Viewing Z is not the calibrated %.*f. Center the mark at that height again.") % (
                decimals, view_z)
        elif state == 'stale':
            text = _("Calibration needs revalidation: camera or orientation changed.")
        else:
            text = _("No calibration saved.")
        self.calibration_label.setText(text)
        self._update_buttons()

    def set_message(self, text, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet('color: red;' if error else '')

    def current_camera_id(self):
        index = self.camera_combo.currentIndex()
        if index < 0 or index >= len(self._devices):
            return ''
        return self._device_id(self._devices[index])

    def rotation(self):
        return ORIENTATIONS[max(0, min(self.orientation_combo.currentIndex(), len(ORIENTATIONS) - 1))]

    def mirror(self):
        return bool(self.mirror_cb.isChecked())

    def verify_tolerance(self):
        return float(self.verify_tolerance_entry.get_value())

    def view_tolerance(self):
        return float(self.view_tolerance_entry.get_value())

    def remember_settings(self):
        self.app.options['tools_camera_device'] = self.current_camera_id()
        self.app.options['tools_camera_rotation'] = self.rotation()
        self.app.options['tools_camera_mirror'] = self.mirror()
        self.app.options['tools_camera_verify_tolerance'] = self.verify_tolerance()
        self.app.options['tools_camera_view_tolerance'] = self.view_tolerance()
        self.app.options['tools_camera_tolerance_units'] = self.units

    def preview_usable(self):
        """Enumeration is not evidence of images: require a recent delivered frame."""
        if not (self._preview_live and self.camera is not None and self.session is not None):
            return False
        return (time.monotonic() - self._last_frame_time) <= PREVIEW_FRAME_TIMEOUT

    def _on_device_changed(self, index):
        self.remember_settings()
        if index < 0:
            self.stop_preview()
            self.device_changed_sig.emit('')
            return
        if self.preview_button.isChecked():
            self.start_preview()
        self.device_changed_sig.emit(self.current_camera_id())

    def _on_orientation_changed(self, *args):
        self.remember_settings()
        self.view.set_orientation(self.rotation(), self.mirror())
        self.orientation_changed_sig.emit()

    def _on_tolerance_changed(self, *args):
        self.remember_settings()
        self.tolerance_changed_sig.emit()

    def _on_preview_toggled(self, checked):
        if checked:
            self.start_preview()
        else:
            self.stop_preview()

    def _on_video_frame(self, frame):
        # a frame from a stopped preview or a previous device must not bless the current one
        if self.camera is None or self.session is None:
            return
        sender = self.sender() if callable(getattr(self, 'sender', None)) else None
        if sender is not None and sender is not self._frame_sink:
            return
        if not frame.isValid():
            return
        self._last_frame_time = time.monotonic()
        if not self._preview_live:
            self._preview_live = True
            self.status_label.setText(_("Live preview."))
            self.preview_available_sig.emit(True)
        self._update_buttons()

    def _check_preview(self):
        if self._preview_live and not self.preview_usable():
            # the device stopped delivering: measurements are refused until frames resume
            self._preview_live = False
            self.set_message(_("The camera image stopped. Start the preview again."), error=True)
            self.preview_available_sig.emit(False)
            self._update_buttons()

    def start_preview(self):
        if not self._devices:
            self.status_label.setText(_("No camera found. Connect a camera and press Refresh."))
            self.stop_preview()
            return False
        index = self.camera_combo.currentIndex()
        if index < 0 or index >= len(self._devices):
            self.status_label.setText(_("Choose a camera first."))
            self.stop_preview()
            return False
        self.stop_preview()
        self.view.reset_video_item()
        try:
            self.camera = QCamera(self._devices[index], self)
            self._preview_device_id = self._device_id(self._devices[index])
            self.session = QMediaCaptureSession(self)
            self.session.setCamera(self.camera)
            self.session.setVideoOutput(self.view.video_item)
            sink = self.view.video_item.videoSink()
            if sink is not None:
                self._frame_sink = sink
                sink.videoFrameChanged.connect(self._on_video_frame)
            self.camera.errorOccurred.connect(self._on_camera_error)
            self.view.set_orientation(self.rotation(), self.mirror())
            camera, session = self.camera, self.session
            camera.start()
        except Exception as error:
            self._report_camera_error(str(error))
            return False
        if self.camera is not camera or self.session is not session:
            return False
        if not self.preview_button.isChecked():
            self.preview_button.blockSignals(True)
            self.preview_button.setChecked(True)
            self.preview_button.blockSignals(False)
        self.status_label.setText(_("Waiting for the first camera image..."))
        return True

    def stop_preview(self):
        if self.preview_button.isChecked():
            self.preview_button.blockSignals(True)
            self.preview_button.setChecked(False)
            self.preview_button.blockSignals(False)
        camera = self.camera
        session = self.session
        sink = self._frame_sink
        self.camera = None
        self.session = None
        self._frame_sink = None
        self._preview_device_id = ''
        self._preview_live = False
        self._last_frame_time = 0.0
        try:
            if sink is not None:
                sink.videoFrameChanged.disconnect(self._on_video_frame)
        except (TypeError, RuntimeError):
            pass
        try:
            if camera is not None:
                camera.stop()
                camera.deleteLater()
            if session is not None:
                session.setCamera(None)
                session.deleteLater()
        except RuntimeError:
            pass
        self._update_buttons()
        self.preview_available_sig.emit(False)

    def _on_camera_error(self, error, message):
        sender = self.sender()
        if sender is not None and sender is not self.camera:
            # an error from a stopped or previous camera must not stop the current preview
            return
        self._report_camera_error(message)

    def _report_camera_error(self, message):
        self.stop_preview()
        self.set_message(_("Camera error: %s") % (message or _("the camera stopped")), error=True)
        try:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Camera error"), message))
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_preview()
        super().closeEvent(event)
