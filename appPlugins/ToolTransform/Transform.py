# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from appHandlers.appHistory import (history_of, object_state, restore_object_state, states_equal,
                                    transform_metadata, SnapshotStep, HistoryManager, invert_matrix, HistoryError)
from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from defaults import AppDefaults
from appCommon.SafeEval import safe_eval
from appPlugins.ToolTransform.TransformUI import TransformUI
from appPlugins.ToolTransform.TransformGen import alt_bounds, bounds_center, SUPPORTED_KINDS, AFFINE_KINDS

import logging
import math
from camlib import (affine_rotate_matrix, affine_scale_matrix, affine_skew_matrix, affine_mirror_matrix,
                    affine_translate_matrix)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolTransform(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = self.app.decimals
        self.ui = TransformUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self._transform_busy = False
        self.connect_signals_at_init()

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolTransform()")

        if getattr(self.app, 'plugin_tab_locked', False) is True:
            return

        found_idx = None
        for idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                found_idx = idx
                break
        tab_existed = found_idx is not None

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            if not tab_existed:
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
                found_idx = self.app.ui.notebook.indexOf(self.app.ui.plugin_tab)
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                # only a tab that existed before this run is the tool page to close; a tab just created here must
                # stay open even when the scroll area still holds this tool's widget from the previous close
                if tab_existed and self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and \
                        found_idx is not None and found_idx >= 0:
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(found_idx)
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
                        # the tab is gone; do not set up the tool for a page that no longer exists
                        return
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()
        if found_idx is not None and found_idx >= 0:
            self.app.ui.notebook.setTabText(found_idx, _("Transformation"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+T', **kwargs)

    def connect_signals_at_init(self):
        self.ui.ref_combo.currentIndexChanged.connect(self.ui.on_reference_changed)
        self.ui.type_obj_combo.currentIndexChanged.connect(self.on_type_obj_index_changed)
        self.ui.point_button.clicked.connect(self.on_add_coords)
        self.ui.rotate_button.clicked.connect(self.on_rotate)
        self.ui.skewx_button.clicked.connect(self.on_skewx)
        self.ui.skewy_button.clicked.connect(self.on_skewy)
        self.ui.skewxy_button.clicked.connect(self.on_skewxy)
        self.ui.scalex_button.clicked.connect(self.on_scalex)
        self.ui.scaley_button.clicked.connect(self.on_scaley)
        self.ui.scalexy_button.clicked.connect(self.on_scalexy)
        self.ui.offx_button.clicked.connect(self.on_offx)
        self.ui.offy_button.clicked.connect(self.on_offy)
        self.ui.offsetxy_button.clicked.connect(self.on_offsetxy)
        self.ui.flipx_button.clicked.connect(self.on_flipx)
        self.ui.flipy_button.clicked.connect(self.on_flipy)
        self.ui.buffer_button.clicked.connect(self.on_buffer_by_distance)
        self.ui.buffer_factor_button.clicked.connect(self.on_buffer_by_factor)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self):
        self.clear_ui(self.layout)
        self.ui = TransformUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # ## Initialize form
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.ui.ref_combo.set_value(self.app.options.get(
            "tools_transform_reference", app_defaults.get("tools_transform_reference", AppDefaults.factory_defaults.get("tools_transform_reference"))))
        self.ui.type_obj_combo.set_value(self.app.options.get(
            "tools_transform_ref_object", app_defaults.get("tools_transform_ref_object", AppDefaults.factory_defaults.get("tools_transform_ref_object"))))
        self.ui.point_entry.set_value(self.app.options.get(
            "tools_transform_ref_point", app_defaults.get("tools_transform_ref_point", AppDefaults.factory_defaults.get("tools_transform_ref_point"))))
        self.ui.rotate_entry.set_value(self.app.options.get(
            "tools_transform_rotate", app_defaults.get("tools_transform_rotate", AppDefaults.factory_defaults.get("tools_transform_rotate"))))
        self.ui.skewx_entry.set_value(self.app.options.get(
            "tools_transform_skew_x", app_defaults.get("tools_transform_skew_x", AppDefaults.factory_defaults.get("tools_transform_skew_x"))))
        self.ui.skewy_entry.set_value(self.app.options.get(
            "tools_transform_skew_y", app_defaults.get("tools_transform_skew_y", AppDefaults.factory_defaults.get("tools_transform_skew_y"))))
        self.ui.skew_link_cb.set_value(self.app.options.get(
            "tools_transform_skew_link", app_defaults.get("tools_transform_skew_link", AppDefaults.factory_defaults.get("tools_transform_skew_link"))))
        self.ui.scalex_entry.set_value(self.app.options.get(
            "tools_transform_scale_x", app_defaults.get("tools_transform_scale_x", AppDefaults.factory_defaults.get("tools_transform_scale_x"))))
        self.ui.scaley_entry.set_value(self.app.options.get(
            "tools_transform_scale_y", app_defaults.get("tools_transform_scale_y", AppDefaults.factory_defaults.get("tools_transform_scale_y"))))
        self.ui.scale_link_cb.set_value(self.app.options.get(
            "tools_transform_scale_link", app_defaults.get("tools_transform_scale_link", AppDefaults.factory_defaults.get("tools_transform_scale_link"))))
        self.ui.offx_entry.set_value(self.app.options.get(
            "tools_transform_offset_x", app_defaults.get("tools_transform_offset_x", AppDefaults.factory_defaults.get("tools_transform_offset_x"))))
        self.ui.offy_entry.set_value(self.app.options.get(
            "tools_transform_offset_y", app_defaults.get("tools_transform_offset_y", AppDefaults.factory_defaults.get("tools_transform_offset_y"))))
        self.ui.buffer_entry.set_value(self.app.options.get(
            "tools_transform_buffer_dis", app_defaults.get("tools_transform_buffer_dis", AppDefaults.factory_defaults.get("tools_transform_buffer_dis"))))
        self.ui.buffer_factor_entry.set_value(self.app.options.get(
            "tools_transform_buffer_factor", app_defaults.get("tools_transform_buffer_factor", AppDefaults.factory_defaults.get("tools_transform_buffer_factor"))))
        self.ui.buffer_rounded_cb.set_value(self.app.options.get(
            "tools_transform_buffer_corner", app_defaults.get("tools_transform_buffer_corner", AppDefaults.factory_defaults.get("tools_transform_buffer_corner"))))

        # the visibility of the Point / Object controls follows the restored reference
        self.ui.on_reference_changed(self.ui.ref_combo.currentIndex())

    def on_type_obj_index_changed(self, index):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(index, 0, QtCore.QModelIndex()))
        self.ui.object_combo.setCurrentIndex(0)
        self.ui.object_combo.obj_type = {
            _("Gerber"): "Gerber", _("Excellon"): "Excellon", _("Geometry"): "Geometry"
        }[self.ui.type_obj_combo.get_value()]

    def on_calculate_reference(self, kinds=SUPPORTED_KINDS):
        ref_val = self.ui.ref_combo.currentIndex()
        if ref_val == 0:    # "Origin" reference
            return 0, 0
        elif ref_val == 1:  # "Selection" reference
            sel_list = self.app.collection.get_selected()
            if sel_list:
                xmin, ymin, xmax, ymax = alt_bounds(sel_list, kinds)
                if not ToolTransform._is_finite(xmin, ymin, xmax, ymax):
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("The selected objects have no geometry."))
                    return "fail"
                return bounds_center((xmin, ymin, xmax, ymax))
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
                return "fail"
        elif ref_val == 2:  # "Point" reference
            point_val = self.ui.point_entry.get_value()
            try:
                px, py = safe_eval('{}'.format(point_val))
                px, py = float(px), float(py)
            except (TypeError, ValueError, OverflowError):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Incorrect format for Point value. Needs format X,Y"))
                return "fail"
            if not ToolTransform._is_finite(px, py):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Incorrect format for Point value. Needs format X,Y"))
                return "fail"
            return px, py
        else:               # "Object" reference
            obj_name = self.ui.object_combo.get_value()
            ref_obj = self.app.collection.get_by_name(obj_name)
            if ref_obj is None or getattr(ref_obj, 'deleted', False):
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Reference object not found"), str(obj_name)))
                return "fail"
            try:
                xmin, ymin, xmax, ymax = ref_obj.bounds()
            except (TypeError, ValueError, AttributeError, OverflowError):
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Reference object has no geometry"), str(obj_name)))
                return "fail"
            if not ToolTransform._is_finite(xmin, ymin, xmax, ymax):
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Reference object has no geometry"), str(obj_name)))
                return "fail"
            return bounds_center((xmin, ymin, xmax, ymax))

    def on_add_coords(self):
        val = self.app.clipboard.text()
        self.ui.point_entry.set_value(val)

    @staticmethod
    def _is_finite(*values):
        try:
            return all(math.isfinite(float(value)) for value in values)
        except (TypeError, ValueError, OverflowError):
            return False

    def _entry_number(self, entry, label):
        try:
            value = float(entry.get_value())
        except (TypeError, ValueError, OverflowError):
            value = None
        if value is None or not math.isfinite(value):
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Invalid numeric value"), label))
            return None
        return value

    @staticmethod
    def _object_kind(obj):
        return str(getattr(obj, 'kind', '')).lower()

    @staticmethod
    def _supported_selection(obj_list, kinds=SUPPORTED_KINDS):
        supported = []
        skipped = []
        for obj in obj_list:
            if getattr(obj, 'deleted', False):
                continue
            if ToolTransform._object_kind(obj) in kinds:
                supported.append(obj)
            else:
                skipped.append(obj)
        return supported, skipped

    def _warn_skipped(self, skipped, cncjob_msg=None):
        if not skipped:
            return
        cnc = [obj for obj in skipped if ToolTransform._object_kind(obj) == 'cncjob']
        others = [obj for obj in skipped if ToolTransform._object_kind(obj) != 'cncjob']
        if cnc:
            msg = cncjob_msg or _("CNCJob objects can't be transformed.")
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                 (msg, ", ".join(ToolTransform._obj_name(obj) for obj in cnc)))
        if others:
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                 (_("Only Gerber, Excellon and Geometry objects can be transformed. Skipped"),
                                  ", ".join(ToolTransform._obj_name(obj) for obj in others)))

    def _selected_objects(self, kinds=SUPPORTED_KINDS):
        """The selection at the moment of the click; the worker must not read the GUI selection model."""
        selected = list(self.app.collection.get_selected())
        supported, skipped = ToolTransform._supported_selection(selected, kinds)
        ToolTransform._warn_skipped(self, skipped)
        if not supported and not skipped:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        return supported

    def _start_transform(self):
        if getattr(self, '_transform_busy', False):
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Another transformation is still running. Please wait for it to finish."))
            return False
        self._transform_busy = True
        return True

    def _queue(self, fcn, params):
        """Schedule the worker and always release the busy guard when the scheduling or the action fails."""
        if not ToolTransform._start_transform(self):
            return

        def guarded(*args):
            try:
                return fcn(*args)
            except Exception as e:
                self.app.log.error("ToolTransform.%s --> %s" % (getattr(fcn, '__name__', 'action'), str(e)))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Action was not executed"), str(e)))
            finally:
                self._transform_busy = False

        try:
            self.app.worker_task.emit({'fcn': guarded, 'params': params})
        except Exception as e:
            self._transform_busy = False
            self.app.log.error("ToolTransform scheduling --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Action was not executed"), str(e)))

    def on_rotate(self):
        value = self._entry_number(self.ui.rotate_entry, _("Angle"))
        if value is None:
            return
        if math.isclose(value % 360.0, 0.0, abs_tol=1e-12):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Rotate transformation would not change anything."))
            return
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_rotate_action, [value, point, objs])

    def on_flipx(self):
        axis = 'Y'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_flip, [axis, point, objs])

    def on_flipy(self):
        axis = 'X'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_flip, [axis, point, objs])

    def on_skewx(self):
        xvalue = self._entry_number(self.ui.skewx_entry, _("X angle"))
        if xvalue is None:
            return
        if xvalue == 0:
            return
        if self.ui.skew_link_cb.get_value():
            yvalue = xvalue
        else:
            yvalue = 0
        axis = 'X'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_skew, [axis, xvalue, yvalue, point, objs])

    def on_skewy(self):
        xvalue = 0
        yvalue = self._entry_number(self.ui.skewy_entry, _("Y angle"))
        if yvalue is None:
            return
        if yvalue == 0:
            return
        axis = 'Y'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_skew, [axis, xvalue, yvalue, point, objs])

    def on_skewxy(self):
        xvalue = self._entry_number(self.ui.skewx_entry, _("X angle"))
        if xvalue is None:
            return
        if self.ui.skew_link_cb.get_value():
            yvalue = xvalue
        else:
            yvalue = self._entry_number(self.ui.skewy_entry, _("Y angle"))
            if yvalue is None:
                return
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_skew, ['XY', xvalue, yvalue, point, objs])

    def on_scalex(self):
        xvalue = self._entry_number(self.ui.scalex_entry, _("X factor"))
        if xvalue is None:
            return
        if xvalue == 0 or xvalue == 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Scale transformation can not be done for a factor of 0 or 1."))
            return
        if self.ui.scale_link_cb.get_value():
            yvalue = xvalue
        else:
            yvalue = 1
        axis = 'X'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_scale, [axis, xvalue, yvalue, point, objs])

    def on_scaley(self):
        xvalue = 1
        yvalue = self._entry_number(self.ui.scaley_entry, _("Y factor"))
        if yvalue is None:
            return
        if yvalue == 0 or yvalue == 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Scale transformation can not be done for a factor of 0 or 1."))
            return
        axis = 'Y'
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_scale, [axis, xvalue, yvalue, point, objs])

    def on_scalexy(self):
        xvalue = self._entry_number(self.ui.scalex_entry, _("X factor"))
        if xvalue is None:
            return
        if self.ui.scale_link_cb.get_value():
            yvalue = xvalue
        else:
            yvalue = self._entry_number(self.ui.scaley_entry, _("Y factor"))
            if yvalue is None:
                return
        if xvalue == 0 or yvalue == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Scale transformation can not be done for a factor of 0 or 1."))
            return
        if xvalue == 1 and yvalue == 1:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Scale transformation would not change anything."))
            return
        point = self.on_calculate_reference(AFFINE_KINDS)
        if point == 'fail':
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_scale, ['XY', xvalue, yvalue, point, objs])

    def on_offx(self):
        value = self._entry_number(self.ui.offx_entry, _("X val"))
        if value is None:
            return
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset transformation can not be done for a value of 0."))
            return
        axis = 'X'
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_offset, [axis, value, objs])

    def on_offy(self):
        value = self._entry_number(self.ui.offy_entry, _("Y val"))
        if value is None:
            return
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset transformation can not be done for a value of 0."))
            return
        axis = 'Y'
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_offset, [axis, value, objs])

    def on_offsetxy(self):
        xvalue = self._entry_number(self.ui.offx_entry, _("X val"))
        if xvalue is None:
            return
        yvalue = self._entry_number(self.ui.offy_entry, _("Y val"))
        if yvalue is None:
            return
        if xvalue == 0 and yvalue == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Offset transformation can not be done for a value of 0."))
            return
        objs = self._selected_objects(AFFINE_KINDS)
        if not objs:
            return
        self._queue(self.on_offsetxy_action, [xvalue, yvalue, objs])

    def on_buffer_by_distance(self):
        value = self._entry_number(self.ui.buffer_entry, _("Distance"))
        if value is None:
            return
        if value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Buffer transformation would not change anything."))
            return
        join = 1 if self.ui.buffer_rounded_cb.get_value() else 2
        objs = self._selected_objects()
        if not objs:
            return
        self._queue(self.on_buffer_action, [value, join, None, objs])

    def on_buffer_by_factor(self):
        raw_value = self._entry_number(self.ui.buffer_factor_entry, _("Value"))
        if raw_value is None:
            return
        if raw_value == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Buffer transformation would not change anything."))
            return
        value = 1 + raw_value / 100.0
        join = 1 if self.ui.buffer_rounded_cb.get_value() else 2
        factor = True
        objs = self._selected_objects()
        if not objs:
            return
        self._queue(self.on_buffer_action, [value, join, factor, objs])

    def _transform_selected(self, title, cncjob_msg, apply_fn, matrix, meta_fn, done_msg, set_offset=True,
                            selected=None, kinds=SUPPORTED_KINDS):
        """
        Runs one affine action on the selected objects (called from a worker thread). Per object: apply_fn(obj) does
        the transform, meta_fn(obj) stores the metadata in obj_options, then a queued signal fills the Offset field on
        the GUI thread. AppObject.on_object_changed computes fresh bounds when its queued signal is delivered.
        A failure on one object is logged and does not stop the others; the failed names are reported at the end.

        :param title:       text for the progress container
        :param cncjob_msg:  message for the CNCJob objects, shown once
        :param apply_fn:    callable(obj) that executes the transform; may return 'fail' (object unchanged)
        :param matrix:      the shapely 6-tuple that apply_fn applies to the geometry; None: not affine, plot() is used
        :param meta_fn:     callable(obj) that updates obj.obj_options
        :param done_msg:    success message
        :param set_offset:  update the Offset field in the Properties tab
        :param selected:    the selection captured on the GUI thread; None: read the collection (direct call)
        :param kinds:       the object kinds that this action can transform
        :return:            None
        """
        try:
            obj_list = list(selected) if selected is not None else list(self.app.collection.get_selected())
            obj_list, skipped = ToolTransform._supported_selection(obj_list, kinds)
            if selected is None:
                # a direct call did not filter on the GUI thread; report what its transform cannot handle
                ToolTransform._warn_skipped(self, skipped, cncjob_msg)
            if not obj_list:
                if not skipped:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
                return

            if matrix is not None:
                # the same tolerance as the history that has to undo the step; nothing is changed for a matrix that
                # collapses the geometry or that cannot be inverted
                try:
                    invert_matrix(matrix)
                except (HistoryError, TypeError, ValueError) as e:
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Action was not executed"), str(e)))
                    return

            with self.app.proc_container.new('%s...' % title):
                failed = []
                done = 0
                history = history_of(self.app)
                with history.group(title):
                    for sel_obj in obj_list:
                        checkpoint = object_state(sel_obj)
                        before_meta = transform_metadata(sel_obj)
                        before_has_source = hasattr(sel_obj, 'source_file')
                        before_source = getattr(sel_obj, 'source_file', None) if before_has_source else None
                        group_steps = history._group[1] if isinstance(history, HistoryManager) else None
                        recorded = len(group_steps) if group_steps is not None else 0
                        # obj_options is a LoudDict whose callback writes the object form; meta_fn runs in this
                        # worker thread, so defer those GUI writes to the queued transform completion
                        option_callback = getattr(getattr(sel_obj, 'obj_options', None), 'callback', None)
                        if option_callback is not None:
                            try:
                                sel_obj.obj_options.set_change_callback(lambda key: None)
                            except AttributeError:
                                option_callback = None
                        try:
                            flow_blocked = getattr(sel_obj, 'gcode_flow_blocked', None)
                            if callable(flow_blocked) and flow_blocked('transform'):
                                # an imported program with macros or subprograms can not be rewritten
                                raise ValueError('The G-code can not be transformed')
                            result = apply_fn(sel_obj)
                            if result == 'fail':
                                raise ValueError('Object transformation failed')
                            meta_fn(sel_obj)
                            ToolTransform._after_change(self, sel_obj, matrix, set_offset)
                            if matrix is not None:
                                history_of(self.app).record_matrix(title, [(sel_obj, matrix, before_meta)])
                            else:
                                after = object_state(sel_obj)
                                if not states_equal(checkpoint, after):
                                    history.push(SnapshotStep(title, [(sel_obj.history_uid, checkpoint, after)]))
                            done += 1
                        except Exception as e:
                            if group_steps is not None:
                                del group_steps[recorded:]
                            self.app.log.error("ToolTransform.%s --> %s" % (title, str(e)))
                            failed.append(ToolTransform._obj_name(sel_obj))
                            changed = not states_equal(checkpoint, object_state(sel_obj))
                            if changed:
                                restore_object_state(sel_obj, checkpoint)
                            # source_file is not part of the history snapshot; put back the exact text even when
                            # the geometry is unchanged, and never depend on another export to recover
                            if before_has_source:
                                try:
                                    sel_obj.source_file = before_source
                                except Exception as recovery:
                                    self.app.log.error("ToolTransform rollback source --> %s" % str(recovery))
                            if changed:
                                try:
                                    ToolTransform._after_change(self, sel_obj, None, set_offset)
                                except Exception as recovery:
                                    self.app.log.error("ToolTransform rollback plot --> %s" % str(recovery))
                        finally:
                            if option_callback is not None:
                                try:
                                    sel_obj.obj_options.set_change_callback(option_callback)
                                except Exception as recovery:
                                    self.app.log.error("ToolTransform option callback restore --> %s" % str(recovery))

                if failed:
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s.' % (_("Action was not executed"), ", ".join(failed)))
                elif done:
                    self.app.inform.emit(done_msg)
        finally:
            self._transform_busy = False

    @staticmethod
    def _obj_name(obj):
        try:
            return str(obj.obj_options['name'])
        except Exception:
            return str(obj)

    def _after_change(self, sel_obj, matrix, set_offset=True):
        """Source, notification, Offset field and plot after a geometry change."""
        if matrix is not None and ToolTransform._object_kind(sel_obj) in ('gerber', 'excellon', 'cncjob'):
            # the project file and Save source use source_file, so it must follow the geometry
            if getattr(sel_obj, 'regenerate_source', None) is not None and sel_obj.regenerate_source() is False:
                raise ValueError('Object source regeneration failed')
        self.app.app_obj.object_changed.emit(sel_obj)
        # the Offset entry is a Qt widget: fill it on the GUI thread with the queued signal; the completion reuses
        # the bounds that on_object_changed stored in obj_options instead of computing them a second time
        self.app.app_obj.object_transform_done.emit(sel_obj, set_offset)
        if matrix is None:
            sel_obj.plot()
        else:
            sel_obj.plot_transformed(matrix)

    def on_rotate_action(self, num, point, objs=None):
        if not math.isfinite(num):
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Invalid numeric value"))
            return
        if math.isclose(num % 360.0, 0.0, abs_tol=1e-12):
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Rotate transformation would not change anything."))
            return
        px, py = point
        matrix = affine_rotate_matrix(-num, (px, py))

        def meta(o):
            o.obj_options['rotate'] = num

        ToolTransform._transform_selected(self,
            _("Rotating"), _("CNCJob objects can't be rotated."),
            lambda o: o.rotate(-num, point=(px, py)), matrix, meta, '[success] %s...' % _('Rotate done'),
            selected=objs, kinds=AFFINE_KINDS)

    def on_flip(self, axis, point, objs=None):
        px, py = point
        matrix = affine_mirror_matrix(axis, (px, py))

        def meta(o):
            # The axis is reversed because of the reference.
            key = 'mirror_y' if axis == 'X' else 'mirror_x'
            if key in o.obj_options:
                o.obj_options[key] = not o.obj_options[key]
            else:
                o.obj_options[key] = True

        if axis == 'X':
            msg = '[success] %s...' % _('Flip on Y axis done')
        else:
            msg = '[success] %s...' % _('Flip on X axis done')
        ToolTransform._transform_selected(self,
            _("Flipping"), _("CNCJob objects can't be mirrored/flipped."),
            lambda o: o.mirror(axis, (px, py)), matrix, meta, msg, selected=objs, kinds=AFFINE_KINDS)

    @staticmethod
    def _skew_angle_is_singular(angle):
        # tan(angle) is undefined at 90 + k * 180 degrees; the matrix would collapse or blow up the coordinates
        return math.isclose(math.cos(math.radians(angle)), 0.0, abs_tol=1e-12)

    def on_skew(self, axis, xvalue, yvalue, point, objs=None):
        if xvalue == 0 and yvalue == 0:
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Skew transformation would not change anything."))
            return
        if ToolTransform._skew_angle_is_singular(xvalue) or ToolTransform._skew_angle_is_singular(yvalue):
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Skew transformation can not be done for an angle that collapses the geometry."))
            return
        if math.isclose(math.sin(math.radians(xvalue)), 0.0, abs_tol=1e-12) and \
                math.isclose(math.sin(math.radians(yvalue)), 0.0, abs_tol=1e-12):
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Skew transformation would not change anything."))
            return
        px, py = point
        matrix = affine_skew_matrix(xvalue, yvalue, (px, py))

        def meta(o):
            o.obj_options['skew_x'] = xvalue
            o.obj_options['skew_y'] = yvalue

        if axis == 'XY':
            msg = '[success] %s...' % _('Skew on both axes done')
        else:
            msg = '[success] %s %s %s...' % (_('Skew on the'), str(axis), _("axis done"))
        ToolTransform._transform_selected(self,
            _("Skewing"), _("CNCJob objects can't be skewed."),
            lambda o: o.skew(xvalue, yvalue, point=(px, py)), matrix, meta, msg, selected=objs, kinds=AFFINE_KINDS)

    def on_scale(self, axis, xfactor, yfactor, point=None, objs=None):
        px, py = (0, 0) if point is None else point
        matrix = affine_scale_matrix(xfactor, yfactor, (px, py))

        def meta(o):
            o.obj_options['scale_x'] = xfactor
            o.obj_options['scale_y'] = yfactor

        if axis == 'XY':
            msg = '[success] %s...' % _('Scale on both axes done')
        else:
            msg = '[success] %s %s %s...' % (_('Scale on the'), str(axis), _('axis done'))
        ToolTransform._transform_selected(self,
            _("Scaling"), _("CNCJob objects can't be scaled."),
            lambda o: o.scale(xfactor, yfactor, point=(px, py)), matrix, meta, msg, selected=objs,
            kinds=AFFINE_KINDS)

    def on_offset(self, axis, num, objs=None):
        vect = (num, 0) if axis == 'X' else (0, num)
        matrix = affine_translate_matrix(vect[0], vect[1])

        def meta(o):
            o.obj_options['offset_x' if axis == 'X' else 'offset_y'] = num

        ToolTransform._transform_selected(self,
            _("Offsetting"), _("CNCJob objects can't be offset."),
            lambda o: o.offset(vect), matrix, meta,
            '[success] %s %s %s...' % (_('Offset on the'), str(axis), _('axis done')), selected=objs,
            kinds=AFFINE_KINDS)

    def on_offsetxy_action(self, xvalue, yvalue, objs=None):
        vect = (xvalue, yvalue)
        matrix = affine_translate_matrix(xvalue, yvalue)

        def meta(o):
            o.obj_options['offset_x'] = xvalue
            o.obj_options['offset_y'] = yvalue

        ToolTransform._transform_selected(self,
            _("Offsetting"), _("CNCJob objects can't be offset."),
            lambda o: o.offset(vect), matrix, meta,
            '[success] %s...' % _('Offset on both axes done'), selected=objs, kinds=AFFINE_KINDS)

    def on_buffer_action(self, value, join, factor=None, objs=None):
        # The buffer is not affine: every object is plotted again.
        no_change = value == 0 if factor is None else math.isclose(value, 1.0, abs_tol=1e-12)
        if no_change:
            self._transform_busy = False
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Buffer transformation would not change anything."))
            return

        def apply(o):
            kind = o.kind.lower()
            if kind in ('gerber', 'excellon'):
                if o.buffer(value, join, factor) == 'fail':
                    return 'fail'
                if o.regenerate_source() is False:
                    return 'fail'
            elif kind == 'geometry':
                if o.buffer(value, join, factor) == 'fail':
                    return 'fail'

        ToolTransform._transform_selected(self,
            _("Buffering"), _("CNCJob objects can't be buffered."),
            apply, None, lambda o: None, '[success] %s...' % _('Buffer done'), selected=objs)

    @staticmethod
    def alt_bounds(obj_list):
        return alt_bounds(obj_list)
