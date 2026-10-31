# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/30/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Board registration tab of the Camera workflow: pick design points on the
selected project objects, pair them with fresh camera-centered points, preview
the rigid transform and apply it once as one Undo step.

Nothing here moves the machine. The only machine command is the guarded Stop
(feed hold) for manual positioning.
"""

import gettext
import builtins
import logging
import math

from PyQt6 import QtWidgets, QtCore
from shapely import affinity

from appGUI.GUIElements import FCLabel, FCButton, FCDoubleSpinner
from appPlugins.ToolAlignObjects.AlignGen import build_snap_index, snap
from appPlugins.ToolGcodeSender.CameraRegistration import (
    ALIGNMENT_TOLERANCE_KEY, ALIGNMENT_TOLERANCE_UNITS_KEY, CameraRegistration, RegistrationError,
    alignment_tolerance, apply_registration)

import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

SNAP_TOLERANCE_PX = 10
PREVIEW_COLOR = '#ff8c00ff'


class SenderRegistrationUI(QtWidgets.QWidget):
    capture_sig = QtCore.pyqtSignal()
    validate_sig = QtCore.pyqtSignal()
    stop_sig = QtCore.pyqtSignal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.decimals = app.decimals
        units = str(getattr(app, 'app_units', 'MM') or 'MM').upper()
        self.units = units if units in ('MM', 'IN') else 'MM'
        self.registration = CameraRegistration()
        self.registration.decimals = self.decimals
        self.fit = None
        self._connected = False
        self._busy = False
        self._awaiting = None
        self._revision = 0
        self._revision_at = 0
        self._applying = False
        self._pick_active = False
        self._pick_indexes = []
        self._grid_memory = False
        self._overlay = None
        self._build_ui()
        self._connect_app_selection()
        self._update_buttons()

    # ---------------------------------------------------------------------------------------------------------
    # UI
    # ---------------------------------------------------------------------------------------------------------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        guide = FCLabel(_(
            "1. Select the Gerber/Excellon/Geometry objects and press 'Use selected objects'.\n"
            "2. Pick a design fiducial on the canvas (or enter its X/Y).\n"
            "3. Jog the fiducial under the camera crosshair and press 'Capture camera point'.\n"
            "4. Repeat for at least two noncoincident fiducials, then Preview and Apply."))
        guide.setWordWrap(True)
        layout.addWidget(guide)

        object_row = QtWidgets.QHBoxLayout()
        self.use_selected_button = FCButton(_("Use selected objects"))
        self.use_selected_button.setToolTip(_(
            "Snapshot the currently selected project objects. A later selection change does not retarget Apply."))
        object_row.addWidget(self.use_selected_button)
        self.object_count_label = FCLabel(_("No objects chosen."))
        object_row.addWidget(self.object_count_label)
        object_row.addStretch(1)
        layout.addLayout(object_row)

        self.objects_list = QtWidgets.QListWidget()
        self.objects_list.setMaximumHeight(70)
        self.objects_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(self.objects_list)

        pick_row = QtWidgets.QHBoxLayout()
        self.pick_button = FCButton(_("Pick design point"), checkable=True)
        self.pick_button.setToolTip(_(
            "Click a pad, drill or vertex of a chosen object on the project canvas. Each click picks one point."))
        pick_row.addWidget(self.pick_button)
        pick_row.addWidget(FCLabel(_("X:")))
        self.design_x_entry = FCDoubleSpinner()
        self.design_x_entry.set_precision(self.decimals)
        self.design_x_entry.set_range(-1000000.0, 1000000.0)
        pick_row.addWidget(self.design_x_entry)
        pick_row.addWidget(FCLabel(_("Y:")))
        self.design_y_entry = FCDoubleSpinner()
        self.design_y_entry.set_precision(self.decimals)
        self.design_y_entry.set_range(-1000000.0, 1000000.0)
        pick_row.addWidget(self.design_y_entry)
        self.add_design_button = FCButton(_("Add X/Y"))
        self.add_design_button.setToolTip(_("Use the typed design coordinates instead of a canvas pick."))
        pick_row.addWidget(self.add_design_button)
        layout.addLayout(pick_row)

        capture_row = QtWidgets.QHBoxLayout()
        self.capture_button = FCButton(_("Capture camera point"))
        self.capture_button.setToolTip(_(
            "Center the same physical fiducial under the camera crosshair and press.\n"
            "Only a fresh machine position is read: nothing moves."))
        capture_row.addWidget(self.capture_button)
        self.stop_button = FCButton(_("Stop"))
        self.stop_button.setToolTip(_(
            "Feed hold / stop the manual positioning motion. The controller then needs Reset and Home."))
        capture_row.addWidget(self.stop_button)
        layout.addLayout(capture_row)

        self.pending_label = FCLabel(_("No pending design point."))
        self.pending_label.setWordWrap(True)
        layout.addWidget(self.pending_label)

        self.pairs_table = QtWidgets.QTableWidget(0, 6)
        self.pairs_table.setHorizontalHeaderLabels([
            '#', _("Design X"), _("Design Y"), _("Camera X"), _("Camera Y"), _("Error")])
        self.pairs_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pairs_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.pairs_table.setMaximumHeight(150)
        self.pairs_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.pairs_table)

        pair_row = QtWidgets.QHBoxLayout()
        self.remove_pair_button = FCButton(_("Remove selected pair"))
        pair_row.addWidget(self.remove_pair_button)
        self.restart_button = FCButton(_("Restart"))
        self.restart_button.setToolTip(_("Forget every design/camera pair and the preview."))
        pair_row.addWidget(self.restart_button)
        pair_row.addStretch(1)
        layout.addLayout(pair_row)

        tolerance_row = QtWidgets.QHBoxLayout()
        self.tolerance_label = FCLabel(_("Alignment acceptance tolerance (%s):") % self.units)
        self.tolerance_label.setToolTip(_(
            "The largest allowed residual of a design point after the rigid fit, in current units."))
        tolerance_row.addWidget(self.tolerance_label)
        self.tolerance_entry = FCDoubleSpinner()
        self.tolerance_entry.set_precision(max(self.decimals, 4))
        self.tolerance_entry.set_range(0.0001, 100.0 if self.units == 'MM' else 100.0 / 25.4)
        self.tolerance_entry.set_value(
            alignment_tolerance(self.units))
        tolerance_row.addWidget(self.tolerance_entry)
        tolerance_row.addStretch(1)
        layout.addLayout(tolerance_row)

        action_row = QtWidgets.QHBoxLayout()
        self.preview_button = FCButton(_("Preview"))
        self.preview_button.setToolTip(_("Show the rigid transform on the project canvas without changing the objects."))
        action_row.addWidget(self.preview_button)
        self.cancel_button = FCButton(_("Cancel preview"))
        action_row.addWidget(self.cancel_button)
        self.apply_button = FCButton(_("Apply"))
        self.apply_button.setToolTip(_(
            "Apply the previewed rigid transform to all chosen objects as one Undo step."))
        action_row.addWidget(self.apply_button)
        layout.addLayout(action_row)

        self.result_label = FCLabel(_("No preview."))
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        self.use_selected_button.clicked.connect(self.on_use_selected)
        self.pick_button.toggled.connect(self._on_pick_toggled)
        self.add_design_button.clicked.connect(self.on_add_numeric)
        self.capture_button.clicked.connect(self.on_capture)
        self.stop_button.clicked.connect(self.stop_sig)
        self.remove_pair_button.clicked.connect(self.on_remove_pair)
        self.restart_button.clicked.connect(self.on_restart)
        self.tolerance_entry.valueChanged.connect(self._on_tolerance_changed)
        self.preview_button.clicked.connect(self.on_preview)
        self.cancel_button.clicked.connect(self.on_cancel)
        self.apply_button.clicked.connect(self.on_apply)

    def _connect_app_selection(self):
        try:
            self.app.proj_selection_changed.connect(self._on_selection_changed)
        except (AttributeError, TypeError, RuntimeError):
            pass
        try:
            self.app.app_obj.object_changed.connect(self._on_object_changed)
        except (AttributeError, TypeError, RuntimeError):
            pass
        try:
            self.app.collection.rowsRemoved.connect(self._on_collection_changed)
            self.app.collection.modelReset.connect(self._on_collection_changed)
        except (AttributeError, TypeError, RuntimeError):
            pass
        try:
            selection_model = self.app.collection.view.selectionModel()
            self._selection_signal = selection_model.selectionChanged
            self._selection_signal.connect(self._on_selection_changed)
        except (AttributeError, TypeError, RuntimeError):
            self._selection_signal = None

    def _disconnect_app_selection(self):
        for signal, slot in (
                (getattr(self.app, 'proj_selection_changed', None), self._on_selection_changed),
                (getattr(getattr(self.app, 'app_obj', None), 'object_changed', None), self._on_object_changed),
                (getattr(getattr(self.app, 'collection', None), 'rowsRemoved', None), self._on_collection_changed),
                (getattr(getattr(self.app, 'collection', None), 'modelReset', None), self._on_collection_changed),
                (getattr(self, '_selection_signal', None), self._on_selection_changed)):
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def restore(self, options):
        """Restore the persisted acceptance tolerance for the current units."""
        saved_units = str(options.get(ALIGNMENT_TOLERANCE_UNITS_KEY, 'MM') or 'MM').upper()
        if saved_units not in ('MM', 'IN'):
            saved_units = 'MM'
        scale = 1.0 if saved_units == self.units else (25.4 if self.units == 'MM' else 1.0 / 25.4)
        value = options.get(ALIGNMENT_TOLERANCE_KEY, None)
        try:
            value = float(value) * scale
        except (TypeError, ValueError):
            value = None
        self.tolerance_entry.blockSignals(True)
        try:
            self.tolerance_entry.set_value(alignment_tolerance(self.units, value))
        finally:
            self.tolerance_entry.blockSignals(False)

    def remember_settings(self):
        self.app.options[ALIGNMENT_TOLERANCE_KEY] = self.tolerance()
        self.app.options[ALIGNMENT_TOLERANCE_UNITS_KEY] = self.units

    def tolerance(self):
        return alignment_tolerance(self.units, self.tolerance_entry.get_value())

    def _sync_units(self):
        """A live project unit change invalidates the picked design points instead of mixing frames."""
        live = str(getattr(self.app, 'app_units', 'MM') or 'MM').upper()
        if live not in ('MM', 'IN') or live == self.units:
            return
        self.units = live
        scale = 25.4 if live == 'MM' else 1.0 / 25.4
        value = self.tolerance_entry.get_value() * scale
        self.tolerance_entry.blockSignals(True)
        try:
            self.tolerance_entry.set_range(0.0001, 100.0 if live == 'MM' else 100.0 / 25.4)
            self.tolerance_entry.set_value(alignment_tolerance(live, value))
        finally:
            self.tolerance_entry.blockSignals(False)
        self.tolerance_label.setText(_("Alignment acceptance tolerance (%s):") % live)
        self._cancel_operation()
        self.fit = None
        self.registration.invalidate('units_changed')
        self.clear_preview()
        self.refresh_table()
        self._set_message(self._reason_text('units_changed'), error=True)
        self._update_buttons()

    def _cancel_operation(self):
        """Any edit or context change makes an outstanding capture or apply check old."""
        self._awaiting = None
        self._revision += 1

    def _finish_operation(self, kind, observation):
        """:return: True only for the pending action, its request token and the revision that started it."""
        if self._awaiting is None or self._awaiting[0] != kind:
            return False
        if self._revision != self._revision_at:
            return False
        request = observation.get('request') if isinstance(observation, dict) else None
        expected = self._awaiting[1]
        if request is not None and (expected is None or request != expected):
            return False
        self._awaiting = None
        return True

    def on_camera_request(self, kind, request):
        """The sender published the token of the request that it just started."""
        kind = {'observe': 'capture', 'reference': 'validate'}.get(kind)
        if self._awaiting is None or self._awaiting[0] != kind or self._awaiting[1] is not None:
            return
        self._awaiting = (kind, int(request))

    def _start_operation(self, kind):
        self._awaiting = (kind, None)
        self._revision_at = self._revision
        self._update_buttons()

    def set_connected(self, connected):
        self._connected = bool(connected)
        self._update_buttons()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_buttons()

    def _update_buttons(self):
        objects = bool(self.registration.objects)
        idle = not self._busy and self._awaiting is None
        self.use_selected_button.setEnabled(idle)
        self.pick_button.setEnabled(idle and objects and self.registration.pending_design is None)
        self.add_design_button.setEnabled(idle and objects and self.registration.pending_design is None)
        self.capture_button.setEnabled(
            self._connected and idle and objects and self.registration.pending_design is not None)
        self.stop_button.setEnabled(self._connected)
        self.remove_pair_button.setEnabled(
            idle and (bool(self.registration.design) or self.registration.pending_design is not None))
        self.restart_button.setEnabled(
            idle and (bool(self.registration.design) or self.registration.pending_design is not None or
                      self.fit is not None))
        self.preview_button.setEnabled(
            idle and objects and len(self.registration.design) >= 2)
        self.cancel_button.setEnabled(self.fit is not None or self._pick_active or self._awaiting is not None)
        self.apply_button.setEnabled(self.fit is not None and idle)

    def _set_message(self, text, error=False):
        self.result_label.setText(text)
        self.result_label.setStyleSheet('color: red;' if error else '')

    # ---------------------------------------------------------------------------------------------------------
    # Object snapshot and points
    # ---------------------------------------------------------------------------------------------------------
    def on_use_selected(self):
        self._sync_units()
        self._cancel_operation()
        self._stop_pick()
        selected = []
        try:
            selected = list(self.app.collection.get_selected())
        except (AttributeError, TypeError):
            pass
        try:
            self.registration.set_objects(selected)
        except RegistrationError as exc:
            self._set_message(str(exc), error=True)
            return
        self.fit = None
        self.clear_preview()
        self.objects_list.clear()
        for obj in self.registration.objects:
            name = getattr(obj, 'obj_options', {}).get('name', '') or getattr(obj, 'kind', '')
            self.objects_list.addItem('%s (%s)' % (name, str(getattr(obj, 'kind', '')).upper()))
        self.object_count_label.setText(_("%d object(s) chosen.") % len(self.registration.objects))
        self.refresh_table()
        ignored = len(selected) - len(self.registration.objects)
        if ignored > 0:
            self._set_message(_("%d object(s) chosen; %d unsupported or deleted object(s) ignored.") % (
                len(self.registration.objects), ignored), error=True)
        else:
            self._set_message(_("Objects chosen. Pick the first design point."))
        self._update_buttons()

    def _on_selection_changed(self, *args):
        self._cancel_operation()
        if self.fit is None:
            self._update_buttons()
            return
        # the snapshot stays; only the stale preview must be made again
        self.registration.invalidate('selection_changed', clear_points=False)
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("The project selection changed. Preview the snapshot again."), error=True)
        self._update_buttons()

    def _on_collection_changed(self, *args):
        if not self.registration.objects:
            return
        try:
            live = set(map(id, self.app.collection.get_list()))
        except Exception:
            live = None
        missing = [obj for obj in self.registration.objects if getattr(obj, 'deleted', False) or
                   (live is not None and id(obj) not in live)]
        if not missing:
            return
        self._stop_pick()
        self._cancel_operation()
        self.registration.reset()
        self.fit = None
        self.clear_preview()
        self.objects_list.clear()
        self.object_count_label.setText(_("No objects chosen."))
        self.refresh_table()
        self._set_message(_("A chosen object was deleted. Choose the objects again."), error=True)
        self._update_buttons()

    def _on_object_changed(self, obj):
        """The chosen geometry was transformed or edited: the old design points no longer describe it."""
        if self._applying or not any(obj is chosen for chosen in self.registration.objects):
            return
        self._stop_pick()
        self._cancel_operation()
        self.fit = None
        self.registration.invalidate('geometry_changed')
        self.clear_preview()
        self.refresh_table()
        self._set_message(self._reason_text('geometry_changed'), error=True)
        self._update_buttons()

    def on_add_numeric(self):
        self._sync_units()
        self._cancel_operation()
        try:
            self.registration.add_design(
                self.design_x_entry.get_value(), self.design_y_entry.get_value(), self.units)
        except RegistrationError as exc:
            self._set_message(str(exc), error=True)
            return
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Design point X %.*f  Y %.*f added. Capture its camera point.") % (
            self.decimals, self.registration.pending_design[0],
            self.decimals, self.registration.pending_design[1]))
        self._update_buttons()

    def on_remove_pair(self):
        if self._busy or self._awaiting is not None:
            return
        if not self.registration.design and self.registration.pending_design is None:
            return
        if QtWidgets.QMessageBox.question(
                self, _("Remove pair"), _("Remove the selected design/camera pair?"),
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._cancel_operation()
        row = self.pairs_table.currentRow()
        if row < 0:
            row = len(self.registration.design) + (1 if self.registration.pending_design else 0) - 1
        self.registration.remove_pair(row)
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Pair removed."))
        self._update_buttons()

    def on_restart(self):
        self._cancel_operation()
        self._stop_pick()
        self.registration.invalidate('restart')
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Registration restarted; the chosen objects stay selected."))
        self._update_buttons()

    def refresh_table(self):
        model = self.registration
        rows = len(model.design) + (1 if model.pending_design is not None else 0)
        self.pairs_table.setRowCount(rows)
        errors = self.fit.errors if self.fit is not None and len(self.fit.errors) == len(model.design) else []
        for row in range(rows):
            if row < len(model.design):
                design = model.design[row]
                measured = model.measured[row] if row < len(model.measured) else None
                error = errors[row] if row < len(errors) else None
            else:
                design, measured, error = model.pending_design, None, None
            cells = [
                str(row + 1),
                '%.*f' % (self.decimals, design[0]),
                '%.*f' % (self.decimals, design[1]),
                '%.*f' % (self.decimals, measured[0]) if measured else '',
                '%.*f' % (self.decimals, measured[1]) if measured else '',
                '%.*f' % (self.decimals, error) if error is not None else '',
            ]
            for col, text in enumerate(cells):
                self.pairs_table.setItem(row, col, QtWidgets.QTableWidgetItem(text))
        if model.pending_design is None:
            self.pending_label.setText(_("No pending design point."))
        else:
            self.pending_label.setText(_("Pending design point X %.*f  Y %.*f: capture the camera point.") % (
                self.decimals, model.pending_design[0], self.decimals, model.pending_design[1]))

    # ---------------------------------------------------------------------------------------------------------
    # Design pick on the project canvas
    # ---------------------------------------------------------------------------------------------------------
    def _on_pick_toggled(self, checked):
        if checked:
            self.start_pick()
        else:
            self._stop_pick()

    def start_pick(self):
        if self._pick_active:
            return
        if not self.registration.objects:
            self._set_message(_("Choose the project objects first."), error=True)
            self._set_pick_checked(False)
            return
        try:
            self._pick_indexes = [(obj, build_snap_index(obj)) for obj in self.registration.objects]
        except Exception as exc:
            log.debug("SenderRegistrationUI.start_pick() --> %s" % str(exc))
            self._set_message(_("Could not read the points of the chosen objects."), error=True)
            self._set_pick_checked(False)
            return
        canvas = self.app.plotcanvas
        try:
            checked = bool(self.app.ui.grid_snap_btn.isChecked())
        except (AttributeError, TypeError):
            checked = False
        self._grid_memory = checked
        if checked:
            self.app.ui.grid_snap_btn.trigger()
        self._pick_active = True
        try:
            try:
                canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
            except Exception as exc:
                log.debug("SenderRegistrationUI.start_pick() mouse disconnect --> %s" % str(exc))
            canvas.graph_event_connect('mouse_release', self._on_canvas_click)
            try:
                canvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)
            except Exception as exc:
                log.debug("SenderRegistrationUI.start_pick() key disconnect --> %s" % str(exc))
            canvas.graph_event_connect('key_press', self._on_key_press)
        except Exception as exc:
            log.error("SenderRegistrationUI.start_pick() --> %s" % str(exc))
            self._stop_pick()
            self._set_message(_("The canvas could not start the design pick."), error=True)
            return
        self._set_pick_checked(True)
        self._set_message(_("Click a pad, drill or vertex of the chosen objects. Right click or Esc cancels."))
        self._update_buttons()

    def _stop_pick(self):
        if not self._pick_active:
            self._set_pick_checked(False)
            return
        canvas = self.app.plotcanvas
        if self._grid_memory:
            try:
                self.app.ui.grid_snap_btn.trigger()
            except (AttributeError, RuntimeError):
                pass
        self._grid_memory = False
        try:
            canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            canvas.graph_event_disconnect('mouse_release', self._on_canvas_click)
        except Exception as exc:
            log.debug("SenderRegistrationUI._stop_pick() mouse restore --> %s" % str(exc))
        try:
            canvas.graph_event_disconnect('key_press', self._on_key_press)
            canvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
        except Exception as exc:
            log.debug("SenderRegistrationUI._stop_pick() key restore --> %s" % str(exc))
        self._pick_active = False
        self._pick_indexes = []
        self._set_pick_checked(False)
        self._update_buttons()

    def _set_pick_checked(self, checked):
        self.pick_button.blockSignals(True)
        try:
            self.pick_button.setChecked(bool(checked))
        finally:
            self.pick_button.blockSignals(False)

    def _pixel_size(self):
        try:
            a = self.app.plotcanvas.translate_coords((0, 0))
            b = self.app.plotcanvas.translate_coords((1, 0))
            size = math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
            if math.isfinite(size) and size > 0:
                return size
        except Exception as exc:
            log.debug("SenderRegistrationUI._pixel_size() --> %s" % str(exc))
        return 0.01

    def _on_canvas_click(self, event):
        if event.button == 2:
            if not getattr(self.app, 'event_is_dragging', False):
                self._stop_pick()
                self._set_message(_("Pick cancelled."))
            return
        if event.button != 1:
            return
        if getattr(self.app, 'selection_type', None) is not None:
            self.app.delete_selection_shape()
            self.app.selection_type = None
            return
        try:
            x, y = self.app.plotcanvas.translate_coords(event.pos)
        except Exception as exc:
            log.debug("SenderRegistrationUI._on_canvas_click() --> %s" % str(exc))
            return
        tolerance = SNAP_TOLERANCE_PX * self._pixel_size()
        best, best_distance = None, None
        try:
            for _obj, index in self._pick_indexes:
                point = snap(index, x, y, tolerance)
                if point is None:
                    continue
                distance = math.hypot(point[0] - x, point[1] - y)
                if best is None or distance < best_distance:
                    best, best_distance = point, distance
        except Exception as exc:
            log.error("SenderRegistrationUI._on_canvas_click() snap --> %s" % str(exc))
            self._stop_pick()
            self._set_message(_("The design point could not be read; pick again."), error=True)
            return
        if best is None:
            self._set_message(_("There is nothing to snap to at this position."), error=True)
            return
        self._stop_pick()
        self._sync_units()
        self._cancel_operation()
        try:
            self.registration.add_design(best[0], best[1], self.units)
        except RegistrationError as exc:
            self._set_message(str(exc), error=True)
            return
        self.design_x_entry.set_value(best[0])
        self.design_y_entry.set_value(best[1])
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Design point X %.*f  Y %.*f picked. Capture its camera point.") % (
            self.decimals, best[0], self.decimals, best[1]))
        self._update_buttons()

    def _on_key_press(self, event):
        key = event.key() if callable(getattr(event, 'key', None)) else event.key
        key_name = getattr(key, 'name', key)
        if key == QtCore.Qt.Key.Key_Escape or key_name == 'Escape':
            self._stop_pick()
            self._set_message(_("Pick cancelled."))
        elif key == QtCore.Qt.Key.Key_Backspace or key_name == 'Backspace':
            self.on_remove_pair()

    # ---------------------------------------------------------------------------------------------------------
    # Observation, preview, apply
    # ---------------------------------------------------------------------------------------------------------
    def on_capture(self):
        self._sync_units()
        if self.registration.pending_design is None:
            self._set_message(_("Pick or type a design point first."), error=True)
            return
        self._start_operation('capture')
        self._set_message(_("Reading a fresh camera position. Nothing moves."))
        self.capture_sig.emit()

    def on_camera_observation(self, observation):
        self._sync_units()
        if not self._finish_operation('capture', observation):
            return
        if not observation.get('ok'):
            self._set_message(self._reason_text(observation.get('reason')), error=True)
            self._update_buttons()
            return
        ok, reason = self.registration.add_observation(observation)
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        if not ok:
            self._set_message(self._reason_text(reason), error=True)
            self._update_buttons()
            return
        units = observation.get('units', self.units)
        x, y = observation.get('x'), observation.get('y')
        self._set_message(_("Camera point X %.*f  Y %.*f (%s) paired.") % (
            self.decimals, x, self.decimals, y, units))
        self._update_buttons()

    def on_preview(self):
        self._sync_units()
        if self._busy or self._awaiting is not None:
            return
        self.remember_settings()
        try:
            fit = self.registration.fit(self.tolerance())
        except RegistrationError as exc:
            self.fit = None
            self.clear_preview()
            self.refresh_table()
            self._set_message(str(exc), error=True)
            self._update_buttons()
            return
        if fit is None:
            self.fit = None
            self.clear_preview()
            self._set_message(_("At least two complete design/camera pairs are needed."), error=True)
            self._update_buttons()
            return
        self.fit = fit
        if not self._draw_preview():
            self.fit = None
            self.refresh_table()
            self._set_message(_("The preview could not be drawn. The objects are unchanged."), error=True)
            self._update_buttons()
            return
        self.refresh_table()
        text = '%s: %.*f°  %s: X %.*f  Y %.*f %s  %s: %.*f %s  %s: %.*f %s' % (
            _("Angle"), self.decimals, fit.angle,
            _("Translation"), self.decimals, fit.translation[0], self.decimals, fit.translation[1], self.units,
            _("RMS"), self.decimals, fit.rms, self.units,
            _("Max error"), self.decimals, fit.max_error, self.units)
        self._set_message('%s  (%s: %s)' % (text, _("tolerance"), self._format_limit(fit.tolerance)))
        self._update_buttons()

    def on_cancel(self):
        self._cancel_operation()
        self._stop_pick()
        self.fit = None
        self.registration.invalidate('cancelled', clear_points=False)
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Preview cleared. The objects are unchanged."))
        self._update_buttons()

    def on_apply(self):
        if self.fit is None or self._busy or self._awaiting is not None:
            return
        self._sync_units()
        if not self._objects_live():
            self.fit = None
            self.registration.invalidate('apply_failed', clear_points=False)
            self.clear_preview()
            self.refresh_table()
            self._set_message(_("A chosen object was deleted or removed. Preview again."), error=True)
            self._update_buttons()
            return
        self._start_operation('validate')
        self._set_message(_("Checking the current machine reference. Nothing moves."))
        self.validate_sig.emit()

    def _objects_live(self):
        try:
            live = set(map(id, self.app.collection.get_list()))
        except Exception:
            live = None
        for obj in self.registration.objects:
            if obj is None or getattr(obj, 'deleted', False):
                return False
            if live is not None and id(obj) not in live:
                return False
        return True

    def on_camera_reference(self, observation):
        self._sync_units()
        if not self._finish_operation('validate', observation):
            return
        if not observation or not observation.get('ok'):
            self._set_message(self._reason_text((observation or {}).get('reason')), error=True)
            self._update_buttons()
            return
        ok, reason = self.registration.check_context(observation)
        if not ok:
            self.fit = None
            self.registration.invalidate(reason)
            self.clear_preview()
            self.refresh_table()
            self._set_message(self._reason_text(reason), error=True)
            self._update_buttons()
            return
        fit = self.fit
        self._applying = True
        try:
            apply_registration(self.app, self.registration.objects, fit, label=_("Camera registration"))
        except RegistrationError as exc:
            self.fit = None
            self.registration.invalidate('apply_failed', clear_points=False)
            self.clear_preview()
            self.refresh_table()
            self._set_message(str(exc), error=True)
            self._update_buttons()
            return
        finally:
            self._applying = False
        self.registration.mark_applied()
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("Applied to %d object(s) as one Undo step.") % len(self.registration.objects))
        self._update_buttons()

    def on_context_changed(self, reason):
        self._cancel_operation()
        self._stop_pick()
        if (self.fit is None and not self.registration.design and
                self.registration.pending_design is None and self.registration.identity is None):
            self._update_buttons()
            return
        self.registration.invalidate(reason)
        self.fit = None
        self.clear_preview()
        self.refresh_table()
        self._set_message(self._reason_text(reason), error=True)
        self._update_buttons()

    def _on_tolerance_changed(self, *args):
        self._cancel_operation()
        self.remember_settings()
        if self.fit is None:
            return
        self.fit = None
        self.registration.invalidate('tolerance_changed', clear_points=False)
        self.clear_preview()
        self.refresh_table()
        self._set_message(_("The alignment tolerance changed. Preview again."), error=True)
        self._update_buttons()

    # ---------------------------------------------------------------------------------------------------------
    # Preview overlay
    # ---------------------------------------------------------------------------------------------------------
    def _draw_preview(self):
        self.clear_preview()
        if self.fit is None:
            return False
        drawn = 0
        try:
            if self._overlay is None:
                self._overlay = self.app.plotcanvas.new_shape_collection(layers=1)
            matrix = list(self.fit.matrix)
            for obj in self.registration.objects:
                for geometry in self._object_geometries(obj):
                    moved = affinity.affine_transform(geometry, matrix)
                    self._overlay.add(moved, color=PREVIEW_COLOR, update=True, layer=0,
                                      tolerance=None, linewidth=2)
                    drawn += 1
            self._overlay.redraw()
        except Exception as exc:
            log.error("SenderRegistrationUI._draw_preview() --> %s" % str(exc))
            self.clear_preview()
            self._set_message(_("The preview could not be drawn: %s") % str(exc), error=True)
            return False
        if not drawn:
            self.clear_preview()
            self._set_message(_("The chosen objects have no geometry to preview."), error=True)
            return False
        return True

    def clear_preview(self):
        if self._overlay is None:
            return
        try:
            self._overlay.clear(update=True)
            self._overlay.redraw()
        except Exception as exc:
            log.debug("SenderRegistrationUI.clear_preview() --> %s" % str(exc))
        finally:
            self._overlay = None

    @staticmethod
    def _object_geometries(obj):
        """Every shapely geometry of an object, including the tool paths of a multigeo Geometry."""
        if str(getattr(obj, 'kind', '')).lower() == 'geometry' and getattr(obj, 'multigeo', False):
            tools = getattr(obj, 'tools', None)
            if isinstance(tools, dict):
                out = []
                for tool in tools.values():
                    out.extend(SenderRegistrationUI._flatten(tool.get('solid_geometry')))
                return out
        return SenderRegistrationUI._flatten(getattr(obj, 'solid_geometry', None))

    @staticmethod
    def _flatten(geometry):
        """Every shapely geometry of the nested lists / tool dicts of an object."""
        out = []
        if geometry is None:
            return out
        if isinstance(geometry, dict):
            for value in geometry.values():
                out.extend(SenderRegistrationUI._flatten(value))
            return out
        if isinstance(geometry, (list, tuple)):
            for value in geometry:
                out.extend(SenderRegistrationUI._flatten(value))
            return out
        if hasattr(geometry, 'is_empty') and not geometry.is_empty:
            out.append(geometry)
        return out

    # ---------------------------------------------------------------------------------------------------------
    # Messages and teardown
    # ---------------------------------------------------------------------------------------------------------
    def _format_limit(self, value):
        return '%.*f %s' % (self.decimals, value, self.units)

    def _reason_text(self, reason):
        messages = {
            'no_work_offset': _(
                "The controller did not report the current work offset in time. Try again; if it persists check "
                "that the status report carries WCO."),
            'work_frame_required': _(
                "The camera observation is not in the work frame. Reconnect and observe again."),
            'session_changed': _("The controller session changed. Registration restarted."),
            'units_changed': _("The application units changed. Registration restarted."),
            'geometry_changed': _("A chosen object changed. Pick the design points again."),
            'camera_changed': _("The camera or its orientation changed. Registration restarted."),
            'calibration_changed': _("The camera calibration changed. Registration restarted."),
            'work_offset_changed': _(
                "The work offset changed between points. Registration restarted; observe the points again."),
            'selection_changed': _("The project selection changed. Preview the snapshot again."),
            'tolerance_changed': _("The alignment tolerance changed. Preview again."),
            'apply_failed': _("The registration failed and was rolled back."),
            'cancelled': _("Preview cleared."),
            'stale_result': _("The camera answer belongs to an older state and was discarded."),
            'camera_preview_unavailable': _("Start the live camera preview and wait for an image first."),
            'not_connected': _("Connect to the controller first."),
            'no_calibration': _("No camera calibration is saved."),
            'no_fresh_z': _("A fresh machine Z is required for a camera measurement."),
            'not_idle': _("The machine must be idle and stationary before a capture."),
            'no_report': _("No fresh status report from the controller."),
            'report_units_unknown': _("The GRBL report units are not known. Reconnect."),
            'not_verified_this_session': _("Verify the saved calibration at the marked point first."),
            'camera': _("The camera or its orientation changed. Registration restarted."),
            'calibration': _("The camera calibration changed. Registration restarted."),
            'disconnect': _("The controller disconnected. Registration restarted."),
            'reset': _("The controller was reset. Registration restarted."),
            'zero': _("A work offset changed. Registration restarted."),
            'home': _("The machine homed. Registration restarted."),
            'connect': _("A new controller session started. Registration restarted."),
        }
        return messages.get(reason, _("The registration operation was refused."))

    def shutdown(self):
        """Give the canvas handlers back and leave no preview behind."""
        self._stop_pick()
        self._cancel_operation()
        self.clear_preview()
        self._overlay = None
        self._disconnect_app_selection()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
