# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 1/13/2020                                          #
# MIT Licence                                              #
# ##########################################################

from appHandlers.appHistory import history_of, transform_metadata, apply_matrix_to_object
from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolAlignObjects.AlignUI import AlignUI
from appPlugins.ToolAlignObjects.AlignGen import (
    AlignError, build_snap_index, snap, solve_translate, solve_rigid, solve_similarity, solve_affine, residuals,
    auto_align, is_ambiguous
)
from defaults import AppDefaults

from shapely import LineString

import logging
import math

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# index of the object type group in the ObjectCollection model and the name of the type
OBJ_TYPES = {'grb': (0, "Gerber"), 'exc': (1, "Excellon"), 'geo': (2, "Geometry")}

ALIGN_TYPES = ('sp', 'dp', 'dps', 'fit', 'auto')
FIT_MODELS = ('rigid', 'similarity', 'affine')

# the click tolerance and the size of the markers, in screen pixels
SNAP_TOLERANCE_PX = 10
MARKER_SIZE_PX = 6

# the Auto mode shows at most this many pairs in the table and in the markers
AUTO_MAX_ROWS = 500
AUTO_MAX_MARKERS = 200

HIGHLIGHT_COLOR = "#15678abf"
START_COLOR = "#2e7d32ff"
DEST_COLOR = "#c62828ff"
LINK_COLOR = "#757575ff"


class AlignObjects(AppTool):

    pluginName = _("Align Objects")

    # emitted by the worker of the Auto search: (search token, list of AutoResult or None, error message)
    auto_finished = QtCore.pyqtSignal(int, object, str)

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.decimals = app.decimals

        self.canvas = self.app.plotcanvas

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = AlignUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        self.auto_finished.connect(self.on_auto_finished)

        # the Auto mode: the candidates of the last search, the shown one and the id of the running search
        self.auto_results = []
        self.auto_index = 0
        self.auto_token = 0

        # if the mouse events are connected to a local method set this True
        self.local_connected = False
        # the key events of the canvas go to a local method
        self.key_connected = False

        # store the status of the grid
        self.grid_status_memory = None

        self.aligned_obj = None
        self.aligner_obj = None

        # snap indexes of the 2 objects
        self.aligned_index = None
        self.aligner_index = None

        # this is one of the objects: self.aligned_obj or self.aligner_obj
        self.target_obj = None

        # here store the alignment points: START points on the aligned object, DESTINATION on the aligner
        self.start_points = []
        self.dest_points = []

        self.align_type = None

        # temporary shapes for the picked points
        self.markers = None

        # old colors of objects involved in the alignment
        self.aligner_old_fill_color = None
        self.aligner_old_line_color = None
        self.aligned_old_fill_color = None
        self.aligned_old_line_color = None

    def connect_signals_at_init(self):
        # Signals
        self.ui.align_object_button.clicked.connect(self.on_align)
        self.ui.apply_button.clicked.connect(self.on_apply)
        self.ui.find_button.clicked.connect(self.on_find)
        self.ui.next_button.clicked.connect(self.on_next_candidate)
        self.ui.type_obj_radio.activated_custom.connect(self.on_type_obj_changed)
        self.ui.type_aligner_obj_radio.activated_custom.connect(self.on_type_aligner_changed)
        self.ui.a_type_radio.activated_custom.connect(self.on_mode_changed)
        self.ui.fit_model_combo.currentIndexChanged.connect(self.update_apply_state)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        self.ui.level.toggled.connect(self.on_level_changed)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolAlignObjects()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            # show the Tab
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                # focus on Tool Tab
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Align Tool"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+A', **kwargs)

    def set_tool_ui(self):
        # restore the colors, the events and the markers if a pick is running
        if self.local_connected is True:
            self.disconnect_cal_events()
        self.clear_markers()
        self.auto_results = []
        self.auto_index = 0
        self.auto_token += 1

        self.clear_ui(self.layout)
        self.ui = AlignUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.reset_fields()

        self.start_points = []
        self.dest_points = []
        self.target_obj = None
        self.aligned_obj = None
        self.aligner_obj = None
        self.aligned_index = None
        self.aligner_index = None

        self.aligner_old_fill_color = None
        self.aligner_old_line_color = None
        self.aligned_old_fill_color = None
        self.aligned_old_line_color = None

        self.ui.a_type_radio.set_value(self._pref_value("tools_align_objects_align_type", ALIGN_TYPES))
        self.ui.set_fit_model(self._pref_value("tools_align_objects_fit_model", FIT_MODELS))
        self.ui.allow_mirror_cb.set_value(self._pref_value("tools_align_objects_allow_mirror", (True, False)))
        self.ui.show_mode(self.ui.a_type_radio.get_value())
        self.ui.type_obj_radio.set_value('grb')
        self.ui.type_aligner_obj_radio.set_value('grb')
        self.change_level(self._pref_value('global_app_level', ('a', 'b')))

        self.update_apply_state()

    def change_level(self, level):
        self.ui.level.setChecked(level == 'a')
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        self.ui.set_level(checked)

    def _pref_value(self, key, allowed):
        """
        The stored preference, or the factory default if the stored value is not one of the allowed ones.
        """
        factory = AppDefaults.factory_defaults.get(key)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        try:
            value = self.app.options.get(key, app_defaults.get(key, factory))
        except Exception:
            value = factory
        return value if value in allowed else factory

    def on_type_obj_changed(self, val):
        obj_type, obj_name = OBJ_TYPES[val]
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.object_combo.setCurrentIndex(0)
        self.ui.object_combo.obj_type = obj_name

    def on_type_aligner_changed(self, val):
        obj_type, obj_name = OBJ_TYPES[val]
        self.ui.aligner_object_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.aligner_object_combo.setCurrentIndex(0)
        self.ui.aligner_object_combo.obj_type = obj_name

    def on_mode_changed(self, val):
        self.clear_auto()
        self.ui.show_mode(val)
        self.update_apply_state()

    def _min_pairs(self):
        """The number of complete pairs that the current mode needs."""
        mode = self.ui.a_type_radio.get_value()
        if mode == 'sp':
            return 1
        if mode == 'fit' and self.ui.get_fit_model() == 'affine':
            return 3
        return 2

    def update_apply_state(self, *args):
        """Apply is enabled in the Best Fit mode, during a pick, when there are enough complete pairs, and in the
        Auto mode when there is a candidate."""
        if self.ui.a_type_radio.get_value() == 'auto':
            self.ui.apply_button.setEnabled(bool(self.auto_results))
            self.ui.next_button.setEnabled(len(self.auto_results) > 1)
            return
        enabled = (self.local_connected is True and self.ui.a_type_radio.get_value() == 'fit' and
                   len(self.dest_points) >= self._min_pairs())
        self.ui.apply_button.setEnabled(enabled)

    def on_align(self):
        self.app.delete_selection_shape()

        # an unfinished pick must not leave its colors, events and markers behind
        if self.local_connected is True:
            self.disconnect_cal_events()

        if not self._read_objects():
            return

        self.align_type = self.ui.a_type_radio.get_value()

        try:
            self.aligned_index = build_snap_index(self.aligned_obj)
            self.aligner_index = build_snap_index(self.aligner_obj)
        except Exception as e:
            log.error("AlignObjects.on_align() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not read the points of the objects."))
            return

        self.start_points = []
        self.dest_points = []
        self.ui.fill_table([], [])
        self.ui.result_label.setText('')

        # disengage the grid snapping since it will be hard to find the drills or pads on grid
        if self.app.ui.grid_snap_btn.isChecked():
            self.grid_status_memory = True
            self.app.ui.grid_snap_btn.trigger()
        else:
            self.grid_status_memory = False

        self.local_connected = True

        self.aligner_old_fill_color = self.aligner_obj.fill_color
        self.aligner_old_line_color = self.aligner_obj.outline_color
        self.aligned_old_fill_color = self.aligned_obj.fill_color
        self.aligned_old_line_color = self.aligned_obj.outline_color

        self.target_obj = self.aligned_obj
        self.set_color()

        self.app.inform.emit(self._point_message())
        self.canvas.graph_event_connect('mouse_release', self.on_mouse_click_release)
        self.canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        # the keys of the pick: Backspace, Enter, Esc
        try:
            self.canvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)
        except Exception as e:
            log.debug("AlignObjects.on_align() key_press disconnect --> %s" % str(e))
        self.canvas.graph_event_connect('key_press', self.on_key_press)
        self.key_connected = True

        self.update_apply_state()

    def _read_objects(self):
        """Read the aligned and the aligner object from the combos. Warns and returns False if one is missing."""
        obj_sel_index = self.ui.object_combo.currentIndex()
        obj_model_index = self.app.collection.index(obj_sel_index, 0, self.ui.object_combo.rootModelIndex())
        try:
            self.aligned_obj = obj_model_index.internalPointer().obj
        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no aligned FlatCAM object selected..."))
            return False

        aligner_obj_sel_index = self.ui.aligner_object_combo.currentIndex()
        aligner_obj_model_index = self.app.collection.index(
            aligner_obj_sel_index, 0, self.ui.aligner_object_combo.rootModelIndex())

        try:
            self.aligner_obj = aligner_obj_model_index.internalPointer().obj
        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no aligner FlatCAM object selected..."))
            return False

        return True

    def _point_message(self):
        """The instruction for the next click."""
        n = len(self.dest_points) + 1
        if len(self.start_points) == len(self.dest_points):
            return '%s %d: %s. %s' % (_("Point"), n, _("Click on the START point."), _("Or right click to cancel."))
        return '%s %d: %s. %s' % (_("Point"), n, _("Click on the DESTINATION point ..."),
                                  _("Or right click to cancel."))

    def _snap_tolerance(self):
        """The click tolerance in data units: 10 screen pixels."""
        return SNAP_TOLERANCE_PX * self._pixel_size()

    def _pixel_size(self):
        """Data units per screen pixel."""
        try:
            a = self.canvas.translate_coords((0, 0))
            b = self.canvas.translate_coords((1, 0))
            size = math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
            if math.isfinite(size) and size > 0:
                return size
        except Exception as e:
            log.debug("AlignObjects._pixel_size() --> %s" % str(e))
        return 0.01

    def on_mouse_click_release(self, event):
        event_pos = event.pos
        right_button = 2

        if event.button == 1:
            if self.app.selection_type is not None:
                # delete previous selection shape
                self.app.delete_selection_shape()
                self.app.selection_type = None
            else:
                pos_canvas = self.canvas.translate_coords(event_pos)
                self.add_point(pos_canvas[0], pos_canvas[1])

        elif event.button == right_button and self.app.event_is_dragging is False:
            self.cancel()

    def add_point(self, x, y):
        """
        Handles a click at the data coordinates (x, y): the point snaps to the target object (smallest feature
        center under the click, or the nearest vertex within 10 pixels). No snap -> warning, no point.

        :return:    True if a point was added
        """
        if self.local_connected is not True:
            return False

        expect_start = len(self.start_points) == len(self.dest_points)
        index = self.aligned_index if expect_start else self.aligner_index

        snapped = snap(index, x, y, self._snap_tolerance())
        if snapped is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is nothing to snap to at this position."))
            return False

        point = (float(snapped[0]), float(snapped[1]))
        if expect_start:
            self.start_points.append(point)
        else:
            self.dest_points.append(point)

        self.target_obj = self.aligned_obj if len(self.start_points) == len(self.dest_points) else self.aligner_obj
        self.reset_color()
        self.set_color()
        self.refresh_points()

        if len(self.dest_points) == len(self.start_points) and self.align_type != 'fit' and \
                len(self.dest_points) >= self._min_pairs():
            self.finish()
        else:
            self.app.inform.emit(self._point_message())
        return True

    def remove_last_point(self):
        if self.dest_points and len(self.dest_points) == len(self.start_points):
            self.dest_points.pop()
        elif self.start_points:
            self.start_points.pop()
        else:
            return

        self.target_obj = self.aligned_obj if len(self.start_points) == len(self.dest_points) else self.aligner_obj
        self.reset_color()
        self.set_color()
        self.refresh_points()
        self.app.inform.emit(self._point_message())

    def on_key_press(self, event):
        # events from the GUI are of type QKeyEvent, events from Vispy are of type KeyEvent
        key = event.key() if callable(getattr(event, 'key', None)) else event.key
        key_name = getattr(key, 'name', key)

        if key == QtCore.Qt.Key.Key_Escape or key_name == 'Escape':
            self.cancel()
        elif key == QtCore.Qt.Key.Key_Backspace or key_name == 'Backspace':
            self.remove_last_point()
        elif key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) or key_name in ('Enter', 'Return'):
            if self.align_type == 'fit':
                self.on_apply()

    def cancel(self):
        self.disconnect_cal_events()
        self.start_points = []
        self.dest_points = []
        self.ui.fill_table([], [])
        self.clear_auto()
        self.update_apply_state()
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled by user request."))

    def on_apply(self):
        """Apply button and Enter key of the Best Fit mode; the Apply button of the Auto mode."""
        if self.ui.a_type_radio.get_value() == 'auto':
            self.apply_auto()
            return
        if self.local_connected is not True:
            return
        self.finish()

    def _solve(self, P, Q):
        mode = self.align_type
        if mode == 'sp':
            return solve_translate(P, Q)
        if mode == 'dp':
            return solve_rigid(P, Q)
        if mode == 'dps':
            return solve_similarity(P, Q)
        model = self.ui.get_fit_model()
        if model == 'similarity':
            return solve_similarity(P, Q)
        if model == 'affine':
            return solve_affine(P, Q)
        return solve_rigid(P, Q)

    def finish(self):
        """
        Solves the matrix from the complete pairs and applies it ONE time. A solver error keeps the points, so the
        user can add or remove a point.
        """
        pairs = min(len(self.start_points), len(self.dest_points))
        P = self.start_points[:pairs]
        Q = self.dest_points[:pairs]

        try:
            matrix = self._solve(P, Q)
        except AlignError as e:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
            return

        # the highlight must be gone before the shapes are transformed
        aligned_obj = self.aligned_obj
        self.disconnect_cal_events()

        before = transform_metadata(aligned_obj)
        try:
            apply_matrix_to_object(aligned_obj, matrix)
        except Exception as e:
            log.error("AlignObjects.finish() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Alignment failed"), str(e)))
            return

        self._after_apply(aligned_obj, matrix)
        history_of(self.app).record_matrix(_("Align Objects"), [(aligned_obj, matrix, before)])

        errors, rms = residuals(matrix, P, Q)
        max_err = max(errors) if len(errors) else 0.0
        self.ui.fill_table(self.start_points, self.dest_points, errors)
        text = '%s: %.*f  %s: %.*f' % (_("RMS error"), self.decimals, rms, _("Max error"), self.decimals, max_err)
        self.ui.result_label.setText(text)
        self.app.inform.emit('[success] %s %s' % (_("Done."), text))

        self.start_points = []
        self.dest_points = []

    def _after_apply(self, obj, matrix):
        """Notify the app about the changed object and plot only that object."""
        try:
            obj.set_offset_values(bounds=obj.bounds())
        except Exception as e:
            log.debug("AlignObjects._after_apply() set_offset_values --> %s" % str(e))
        if str(getattr(obj, 'kind', '')).lower() in ('gerber', 'excellon'):
            # View Source, Save source and the project file use source_file: it must follow the aligned geometry, as
            # in the Transform plugin (a failed export keeps the old text and logs a warning)
            obj.regenerate_source()
        try:
            self.app.app_obj.object_changed.emit(obj)
        except Exception as e:
            log.debug("AlignObjects._after_apply() object_changed --> %s" % str(e))
        obj.plot_transformed(matrix)

    # #################################################################################################################
    # Auto mode
    # #################################################################################################################
    def on_find(self):
        """Start the automatic search in a worker. The result arrives in on_auto_finished() on the GUI thread."""
        self.app.delete_selection_shape()
        self.clear_auto()

        if not self._read_objects():
            return

        model = self.ui.get_fit_model()
        allow_mirror = bool(self.ui.allow_mirror_cb.get_value())
        pair_limit = (int(self.ui.limit_pairs_entry.get_value())
                      if self.ui.level.isChecked() and self.ui.limit_pairs_cb.isChecked() else 0)
        minimum = 3 if model == 'affine' else 2
        if pair_limit and pair_limit < minimum:
            self.app.inform.emit('[ERROR_NOTCL] %s' % (_("At least %d point pairs are needed.") % minimum))
            return

        try:
            src_index = build_snap_index(self.aligned_obj)
            dst_index = build_snap_index(self.aligner_obj)
        except Exception as e:
            log.error("AlignObjects.on_find() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not read the points of the objects."))
            return

        self.auto_token += 1
        token = self.auto_token
        proc_container = self.app.proc_container
        signal = self.auto_finished

        def progress(percent):
            proc_container.update_view_text(' %d%%' % int(percent))

        def job_thread_auto():
            results, error = None, ''
            with proc_container.new(_("Searching the alignment...")):
                try:
                    results = auto_align(src_index, dst_index, model=model, allow_mirror=allow_mirror,
                                         max_results=5, progress=progress, pair_limit=pair_limit)
                except AlignError as e:
                    error = str(e)
                except Exception as e:
                    log.error("AlignObjects.on_find() worker --> %s" % str(e))
                    error = '%s: %s' % (_("The search failed"), str(e))
            signal.emit(token, results, error)

        self.ui.find_button.setEnabled(False)
        self.app.inform.emit(_("Searching the alignment..."))
        self.app.worker_task.emit({'fcn': job_thread_auto, 'params': []})

    def on_auto_finished(self, token, results, error):
        """GUI thread: the search ended."""
        if token != self.auto_token:
            # a newer search or a clear happened meanwhile
            return
        self.ui.find_button.setEnabled(True)

        if error or not results:
            self.app.inform.emit('[ERROR_NOTCL] %s' % (error or _("No alignment was found.")))
            self.update_apply_state()
            return

        self.auto_results = list(results)
        self.auto_index = 0
        self.show_candidate(0)

        if is_ambiguous(self.auto_results):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "Another solution has almost the same score. Check the result and use \"Next candidate\"."))

    def show_candidate(self, index):
        """Show the table, the markers and the label of one candidate."""
        if not self.auto_results:
            return
        self.auto_index = index % len(self.auto_results)
        res = self.auto_results[self.auto_index]

        fit_P = res.fit_P if res.fit_P is not None else res.P
        fit_Q = res.fit_Q if res.fit_Q is not None else res.Q
        P = [(float(x), float(y)) for x, y in fit_P[:AUTO_MAX_ROWS]]
        Q = [(float(x), float(y)) for x, y in fit_Q[:AUTO_MAX_ROWS]]
        errors = residuals(res.matrix, P, Q)[0]
        self.ui.fill_table(P, Q, errors)
        self.draw_markers(P[:AUTO_MAX_MARKERS], Q[:AUTO_MAX_MARKERS])

        max_err = max(res.errors) if len(res.errors) else 0.0
        text = '%d / %d %s, %s: %.*f, %s: %.*f, %s %d/%d' % (
            res.matched, res.total, _("matched"), _("RMS"), self.decimals, res.rms, _("Max"), self.decimals,
            max_err, _("candidate"), self.auto_index + 1, len(self.auto_results))
        if res.mirrored:
            text += ' (%s)' % _("mirrored")
        if res.fit_P is not None:
            text += ', %d %s (%s)' % (len(res.fit_P), _("fit pairs"), _("RMS/Max: full verification"))
        self.ui.result_label.setText(text)
        self.update_apply_state()

    def on_next_candidate(self):
        if len(self.auto_results) > 1:
            self.show_candidate(self.auto_index + 1)

    def apply_auto(self):
        """Apply the matrix of the shown candidate ONE time; the results and the markers are cleared."""
        if not self.auto_results:
            return
        res = self.auto_results[self.auto_index]
        matrix = res.matrix
        aligned_obj = self.aligned_obj

        if aligned_obj is None or getattr(aligned_obj, 'deleted', False) or \
                aligned_obj not in self.app.collection.get_list():
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The aligned object does not exist anymore."))
            self.clear_auto()
            return

        before = transform_metadata(aligned_obj)
        try:
            apply_matrix_to_object(aligned_obj, matrix)
        except Exception as e:
            log.error("AlignObjects.apply_auto() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Alignment failed"), str(e)))
            return

        self._after_apply(aligned_obj, matrix)
        history_of(self.app).record_matrix(_("Align Objects"), [(aligned_obj, matrix, before)])

        text = self.ui.result_label.text()
        # the table and the label keep the applied candidate
        self.auto_results = []
        self.auto_index = 0
        self.auto_token += 1
        self.clear_markers()
        self.update_apply_state()
        self.app.inform.emit('[success] %s %s' % (_("Done."), text))

    def clear_auto(self):
        """Forget the candidates, ignore a running search, clear the markers, the table and the label."""
        had_results = bool(self.auto_results)
        self.auto_results = []
        self.auto_index = 0
        self.auto_token += 1
        self.ui.find_button.setEnabled(True)
        if had_results:
            self.clear_markers()
            self.ui.fill_table([], [])
            self.ui.result_label.setText('')
        self.update_apply_state()

    def disconnect_cal_events(self):
        """End of the pick: restore the grid snap, the app handlers and the colors; clear the markers."""
        # restore the Grid snapping if it was active before
        if self.grid_status_memory is True:
            self.app.ui.grid_snap_btn.trigger()
        self.grid_status_memory = None

        self.reset_color()

        self.canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.canvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)

        if self.key_connected is True:
            self.canvas.graph_event_disconnect('key_press', self.on_key_press)
            self.canvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
            self.key_connected = False

        self.local_connected = False
        self.clear_markers()
        self.aligned_index = None
        self.aligner_index = None

        self.aligner_old_fill_color = None
        self.aligner_old_line_color = None
        self.aligned_old_fill_color = None
        self.aligned_old_line_color = None

        self.update_apply_state()

    def set_color(self):
        if self.target_obj is None:
            return
        self.target_obj.shapes.redraw(
            update_colors=(HIGHLIGHT_COLOR, HIGHLIGHT_COLOR)
        )

    def reset_color(self):
        if self.aligned_obj is not None and self.aligned_old_fill_color is not None:
            self.aligned_obj.shapes.redraw(
                update_colors=(self.aligned_old_fill_color, self.aligned_old_line_color)
            )

        if self.aligner_obj is not None and self.aligner_old_fill_color is not None:
            self.aligner_obj.shapes.redraw(
                update_colors=(self.aligner_old_fill_color, self.aligner_old_line_color)
            )

    def refresh_points(self):
        """Redraw the markers and the table for the points picked so far."""
        self.ui.fill_table(self.start_points, self.dest_points)
        self.update_apply_state()
        self.draw_markers()

    def draw_markers(self, start_points=None, dest_points=None):
        """
        START: cross, DESTINATION: circle, a line per pair. The size is in pixels (current zoom).
        Without parameters it draws the picked points.
        """
        if start_points is None:
            start_points = self.start_points
            dest_points = self.dest_points
        self.clear_markers()
        if not start_points:
            return

        if self.markers is None:
            self.markers = self.app.plotcanvas.new_shape_collection(layers=1)

        size = MARKER_SIZE_PX * self._pixel_size()
        steps = 24
        for idx, (sx, sy) in enumerate(start_points):
            self.markers.add(LineString([(sx - size, sy), (sx + size, sy)]), color=START_COLOR, update=True,
                             layer=0, tolerance=None, linewidth=2)
            self.markers.add(LineString([(sx, sy - size), (sx, sy + size)]), color=START_COLOR, update=True,
                             layer=0, tolerance=None, linewidth=2)
            if idx < len(dest_points):
                dx, dy = dest_points[idx]
                ring = [(dx + size * math.cos(2 * math.pi * i / steps), dy + size * math.sin(2 * math.pi * i / steps))
                        for i in range(steps + 1)]
                self.markers.add(LineString(ring), color=DEST_COLOR, update=True, layer=0, tolerance=None,
                                 linewidth=2)
                self.markers.add(LineString([(sx, sy), (dx, dy)]), color=LINK_COLOR, update=True, layer=0,
                                 tolerance=None, linewidth=1)
        self.markers.redraw()

    def clear_markers(self):
        if self.markers is not None:
            try:
                self.markers.clear(update=True)
                self.markers.redraw()
            except Exception as e:
                log.debug("AlignObjects.clear_markers() --> %s" % str(e))

    def reset_fields(self):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.aligner_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
