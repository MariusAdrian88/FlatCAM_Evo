
from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolDblSided.DblSidedUI import DsidedUI
from appPlugins.ToolDblSided.DblSidedGen import alignment_drills, valid_diameter, validate_holes, \
    bounds_drills, valid_drill_offset, combined_bounds, source_view, replace_generated, alignment_matrices, compose, \
    has_generated, unowned_features, retire_generated, drills_fit_after_flip
from appPlugins.ToolAlignObjects.AlignGen import build_snap_index, SnapIndex, apply_matrix, invert
from defaults import AppDefaults
from appParsers.feature_access import iter_features
from appCommon.SafeEval import safe_eval
from camlib import affine_mirror_matrix, to_dict, flatten_shapely_geometry
from appHandlers.appHistory import history_of, apply_matrix_to_object, object_state, restore_object_state, \
    HISTORY_EXTRA_ATTRS

import logging
import hashlib
import json
from time import perf_counter
import numpy as np
from numpy import inf as Inf

from shapely import Point, to_wkb
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def _process_timing(logger):
    started = perf_counter()
    finished = False
    logger.info('[2-Sided timing] Process started')

    def timing(error=None):
        nonlocal finished
        if finished:
            return
        finished = True
        elapsed = perf_counter() - started
        if error:
            logger.error('[2-Sided timing] Process finished total=%.6fs error=%s' % (elapsed, error))
        else:
            logger.info('[2-Sided timing] Process finished total=%.6fs' % elapsed)
    return timing


def _content_signature(value):
    """Hash project contents without copying state or expanding geometry into JSON/WKT/hex.

    Aliases don't affect the digest. Project save/load changes integer tool keys to strings and tuples
    to lists, so normalize those here too. Geometry memoization lasts only for this synchronous check;
    later checks still see every external replacement and in-place macro edit.
    """
    digest = hashlib.sha256()
    geometries = {}

    def visit(item):
        if isinstance(item, BaseGeometry):
            key = id(item)
            if key not in geometries:
                geometries[key] = hashlib.sha256(to_wkb(item, byte_order=1, include_srid=True)).digest()
            digest.update(b'g' + geometries[key])
        elif isinstance(item, dict):
            digest.update(b'{')
            for key in sorted(item, key=str):
                visit(str(key))
                visit(item[key])
            digest.update(b'}')
        elif isinstance(item, (list, tuple)):
            digest.update(b'[')
            for child in item:
                visit(child)
            digest.update(b']')
        elif isinstance(item, str):
            data = item.encode('utf-8')
            digest.update(b's' + str(len(data)).encode('ascii') + b':' + data)
        else:
            encoded = to_dict(item)
            if encoded is not item:
                visit(encoded)
            else:
                digest.update(b'j' + json.dumps(item).encode('utf-8') + b';')

    visit(value)
    return digest.digest()


class DblSidedTool(AppTool):

    alignment_finished = QtCore.pyqtSignal(int, object, str)

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = self.app.decimals

        self.canvas = self.app.plotcanvas
        self.alignment_token = 0
        self.pending_alignment = None
        self.alignment_timings = {}
        self.alignment_finished.connect(self.on_alignment_finished)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = DsidedUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.drill_values = ""

        # will hold the Excellon object used for picking a hole as mirror reference
        self.exc_hole_obj = None

        # store the status of the grid
        self.grid_status_memory = None

        # set True if mouse events are locally connected
        self.local_connected = False

        self.app.proj_selection_changed.connect(self.on_object_selection_changed)
        self.app.collection.rowsInserted.connect(self.refresh_basic_sources)
        self.app.collection.rowsRemoved.connect(self.refresh_basic_sources)
        self.app.collection.modelReset.connect(self.refresh_basic_sources)
        self.app.collection.dataChanged.connect(self.refresh_basic_sources)
        self.refresh_basic_sources()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+U', **kwargs)

    def set_project_collection(self, old_collection, collection):
        if old_collection is collection:
            return
        super().set_project_collection(old_collection, collection)
        for name in ('rowsInserted', 'rowsRemoved', 'modelReset', 'dataChanged'):
            getattr(old_collection, name).disconnect(self.refresh_basic_sources)
            getattr(collection, name).connect(self.refresh_basic_sources)
        self.refresh_basic_sources()

    def run(self, toggle=True):
        self.app.defaults.report_usage("Tool2Sided()")

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

        self.app.ui.notebook.setTabText(2, _("2-Sided"))

    def connect_signals_at_init(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.object_type_combo.currentIndexChanged.connect(self.on_object_type)

        self.ui.add_point_button.clicked.connect(self.on_point_add)
        self.ui.delete_drill_point_button.clicked.connect(self.on_drill_delete_last)
        self.ui.box_type_radio.activated_custom.connect(self.on_combo_box_type)

        self.ui.axis_location.activated_custom.connect(self.on_toggle_pointbox)

        self.ui.point_entry.textChanged.connect(lambda val: self.ui.align_ref_label_val.set_value(val))
        self.ui.pick_hole_button.clicked.connect(self.on_pick_hole)
        self.ui.mirror_button.clicked.connect(self.on_mirror)

        self.ui.xmin_btn.clicked.connect(self.on_xmin_clicked)
        self.ui.ymin_btn.clicked.connect(self.on_ymin_clicked)
        self.ui.xmax_btn.clicked.connect(self.on_xmax_clicked)
        self.ui.ymax_btn.clicked.connect(self.on_ymax_clicked)

        self.ui.center_btn.clicked.connect(
            lambda: self.ui.point_entry.set_value(self.ui.center_entry.get_value())
        )

        self.ui.create_excellon_button.clicked.connect(self.on_create_alignment_holes)
        self.ui.calculate_bb_button.clicked.connect(self.on_bbox_coordinates)

        self.ui.auto_align_cb.toggled.connect(self.invalidate_alignment)
        self.ui.type_combo.currentIndexChanged.connect(self.invalidate_alignment)
        self.ui.failsafe_cb.toggled.connect(self.invalidate_alignment)
        for row, combo in enumerate(self.ui.basic_combos):
            combo.currentIndexChanged.connect(lambda index, r=row: self.on_basic_selection_changed(r))
        self.ui.process_button.clicked.connect(self.on_process)

        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self):
        self.invalidate_alignment()
        if self.local_connected:
            self.on_exit(cancelled=True)
        self.clear_ui(self.layout)
        self.ui = DsidedUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls(has_source=False)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.reset_fields()
        self.refresh_basic_sources()

        self.ui.point_entry.set_value("")
        self.ui.alignment_holes.set_value("")

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.ui.mirror_axis.set_value(self.app.options.get(
            "tools_2sided_mirror_axis", app_defaults.get("tools_2sided_mirror_axis", AppDefaults.factory_defaults.get("tools_2sided_mirror_axis"))))
        self.ui.axis_location.set_value(self.app.options.get(
            "tools_2sided_axis_loc", app_defaults.get("tools_2sided_axis_loc", AppDefaults.factory_defaults.get("tools_2sided_axis_loc"))))
        self.on_toggle_pointbox(self.ui.axis_location.get_value())

        self.ui.drill_dia.set_value(self.app.options.get(
            "tools_2sided_drilldia", app_defaults.get("tools_2sided_drilldia", AppDefaults.factory_defaults.get("tools_2sided_drilldia"))))
        self.ui.align_type_radio.set_value(self.app.options.get(
            "tools_2sided_align_type", app_defaults.get("tools_2sided_align_type", AppDefaults.factory_defaults.get("tools_2sided_align_type"))))
        self.ui.on_align_type_changed(val=self.ui.align_type_radio.get_value())

        self.ui.xmin_entry.set_value(0.0)
        self.ui.ymin_entry.set_value(0.0)
        self.ui.xmax_entry.set_value(0.0)
        self.ui.ymax_entry.set_value(0.0)
        self.ui.center_entry.set_value('')

        self.ui.align_ref_label_val.set_value('%.*f' % (self.decimals, 0.0))

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj:
            obj_name = obj.obj_options['name']
            if obj.kind == 'gerber':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.object_type_combo.set_value(0)
                self.on_object_type(0)  # Gerber
                self.ui.box_type_radio.set_value('grb')
                self.on_combo_box_type('grb')
            elif obj.kind == 'excellon':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.object_type_combo.set_value(1)
                self.on_object_type(1)  # Excellon
                self.ui.box_type_radio.set_value('exc')
                self.on_combo_box_type('exc')
            elif obj.kind == 'geometry':
                # run once to make sure that the obj_type attribute is updated in the FCComboBox
                self.ui.object_type_combo.set_value(2)
                self.on_object_type(2)  # Geometry
                self.ui.box_type_radio.set_value('geo')
                self.on_combo_box_type('geo')

            self.ui.object_combo.set_value(obj_name)
        else:
            self.ui.object_type_combo.set_value(0)
            self.on_object_type(0)  # Gerber

        if self.local_connected is True:
            self.disconnect_events()

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

    def change_level(self, level):
        """

        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """

        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):
        self.invalidate_alignment()
        if self.local_connected:
            self.on_exit(cancelled=True)
        DsidedUI.set_level(self.ui, checked)

    def on_object_type(self, val):
        """
        Will select the actual type of objects: Gerber, Excellon, Geometry

        :param val:     Index in the combobox where 0 = Gerber, 1 = Excellon and 2 = Geometry
        :type val:      int
        :return:        None
        :rtype:         None
        """
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(val, 0, QtCore.QModelIndex()))
        self.ui.object_combo.setCurrentIndex(0)
        self.ui.object_combo.obj_type = {
            0: "Gerber", 1: "Excellon", 2: "Geometry"}[val]

    def on_combo_box_type(self, val):
        obj_type = {'grb': 0, 'exc': 1, 'geo': 2}[val]
        self.ui.box_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.box_combo.setCurrentIndex(0)
        self.ui.box_combo.obj_type = {
            "grb": "Gerber", "exc": "Excellon", "geo": "Geometry"}[val]

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind

                if kind in ['gerber', 'excellon', 'geometry']:
                    index = {'gerber': 0, 'excellon': 1, 'geometry': 2}[kind]
                    self.ui.object_type_combo.set_value(index)

                    obj_type = {'gerber': 'grb', 'excellon': 'exc', 'geometry': 'geo'}[kind]
                    self.ui.box_type_radio.set_value(obj_type)

                    self.ui.object_combo.set_value(name)
            except Exception as err:
                self.app.log.error("DblSidedTool.on_object_selection_changed() --> %s" % str(err))

    def on_create_alignment_holes(self):
        align_type = self.ui.align_type_radio.get_value()
        mode = self.ui.axis_location.get_value()

        px = py = 0.0
        if align_type in ["X", "Y"]:
            if mode == "point":
                try:
                    px, py = self.ui.point_entry.get_value()
                except (TypeError, ValueError):
                    msg = '[WARNING_NOTCL] %s' % \
                          _("'Point' reference is selected and 'Point' coordinates are missing.")
                    self.app.inform.emit(msg)
                    return
            elif mode == 'box':
                selection_index = self.ui.box_combo.currentIndex()
                model_index = self.app.collection.index(selection_index, 0, self.ui.box_combo.rootModelIndex())
                try:
                    bb_obj = model_index.internalPointer().obj
                except AttributeError:
                    msg = '[WARNING_NOTCL] %s' % _("Box reference object is missing.")
                    self.app.inform.emit(msg)
                    return

                xmin, ymin, xmax, ymax = bb_obj.bounds()
                px = 0.5 * (xmin + xmax)
                py = 0.5 * (ymin + ymax)
            else:
                msg = '[ERROR_NOTCL] %s' % _("Not supported.")
                self.app.inform.emit(msg)
                return

        try:
            dia = valid_diameter(self.ui.drill_dia.get_value())
            holes = validate_holes(safe_eval('[{}]'.format(self.ui.alignment_holes.text())))
            validate_holes([(px, py)])
        except (TypeError, ValueError, OverflowError) as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return

        drills = alignment_drills(holes, align_type, (px, py))
        # the board is flipped about the Mirror axis: a Type other than that axis gives holes that the pins do not fit
        # after the flip, except for holes on the axis, so the holes are checked, not the two settings
        mirror_axis = self.ui.mirror_axis.get_value()
        no_fit = align_type in ["X", "Y"] and mirror_axis in ["X", "Y"] and \
            not drills_fit_after_flip(drills, mirror_axis, (px, py), 10 ** -self.decimals)

        def obj_init(obj_inst, app_inst):
            obj_inst.tools = {}
            obj_inst.add_tool(dia, drills=drills)
            if obj_inst.create_geometry() == 'fail':
                return 'fail'
            if obj_inst.regenerate_source() is False:
                return 'fail'

        ret_val = self.new_object("excellon", _("Alignment Drills"), obj_init, autoselected=False)
        self.drill_values = ''

        if not ret_val == 'fail':
            self.app.inform.emit('[success] %s' % _("Excellon object with alignment drills created..."))
            if no_fit:
                # after the success message, so it is the one left in the status bar
                self.app.inform.emit('[WARNING_NOTCL] %s' % (_(
                    "The alignment holes are not symmetric about the Mirror axis: the pins will not fit after the "
                    "board is flipped. Set the alignment Type to %s.") % mirror_axis))

    def on_pick_hole(self):

        if self.local_connected:
            return

        # get the Excellon file whose geometry will contain the desired drill hole
        selection_index = self.ui.exc_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.exc_combo.rootModelIndex())

        try:
            self.exc_hole_obj = model_index.internalPointer().obj
        except Exception:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Excellon object loaded ..."))
            return

        # disengage the grid snapping since it will be hard to find the drills or pads on grid
        if self.app.ui.grid_snap_btn.isChecked():
            self.grid_status_memory = True
            self.app.ui.grid_snap_btn.trigger()
        else:
            self.grid_status_memory = False

        self.local_connected = True

        # disable the Notebook while in this feature
        self.app.ui.notebook.setDisabled(True)
        self.app.call_source = "2_sided_tool"

        self.app.inform.emit('%s.' % _("Click on canvas within the desired Excellon drill hole"))
        self.canvas.graph_event_connect('mouse_release', self.on_mouse_click_release)

        self.canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

    def on_mouse_click_release(self, event):
        event_pos = event.pos
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)

        if event.button == 1:
            click_pt = Point([pos_canvas[0], pos_canvas[1]])

            if self.app.selection_type is not None:
                # delete previous selection shape
                self.app.delete_selection_shape()
                self.app.selection_type = None
            else:
                if self.exc_hole_obj.kind.lower() == 'excellon':
                    # the first hit sets the reference point and ends the tool, so the search stops there
                    for rec in iter_features(self.exc_hole_obj):
                        if click_pt.within(rec.solid):
                            center_pt = rec.solid.centroid
                            center_pt_coords = (
                                self.app.dec_format(center_pt.x, self.decimals),
                                self.app.dec_format(center_pt.y, self.decimals)
                            )
                            self.app.delete_selection_shape()

                            self.ui.axis_location.set_value('point')
                            # set the reference point for mirror
                            self.ui.point_entry.set_value(center_pt_coords)

                            self.on_exit()
                            self.app.inform.emit('[success] %s' % _("Mirror reference point set."))
                            break

        elif event.button == right_button and self.app.event_is_dragging is False:
            self.on_exit(cancelled=True)

    def on_plugin_mouse_click_release(self, pos):
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        # if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
        #     clip_val = self.app.clipboard.text()
        #     self.ui.point_entry.set_value(clip_val)
        clip_val = self.app.clipboard.text()

        if modifiers == QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier:
            alignment_holes = self.ui.alignment_holes.get_value()
            try:
                eval_clip_val = safe_eval(clip_val)
            except Exception as err:
                self.app.log.debug("DblSidedTool.on_plugin_mouse_click_release() --> %s" % str(err))
                return

            if alignment_holes == '' or alignment_holes is None:
                if isinstance(eval_clip_val, list):
                    altered_clip_val = str(eval_clip_val[-1])
                    self.app.clipboard.setText(altered_clip_val)
                else:
                    altered_clip_val = clip_val
            else:
                if isinstance(eval_clip_val, list):
                    # remove duplicates
                    clean_eval_clip = set(eval_clip_val)
                    # convert to string
                    clip_val = str(list(clean_eval_clip))
                altered_clip_val = clip_val.replace("[", '').replace("]", '')

            self.ui.alignment_holes.set_value(altered_clip_val)
            self.drill_values = altered_clip_val
        elif modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            self.ui.alignment_holes.set_value(clip_val)
            self.drill_values = clip_val

    def on_exit(self, cancelled=None):
        self.app.call_source = "app"
        self.app.ui.notebook.setDisabled(False)
        self.disconnect_events()

        if cancelled is True:
            self.app.delete_selection_shape()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled by user request."))

    def disconnect_events(self):
        self.canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.canvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)

        self.local_connected = False
        if self.grid_status_memory is not None:
            if self.app.ui.grid_snap_btn.isChecked() != self.grid_status_memory:
                self.app.ui.grid_snap_btn.trigger()
            self.grid_status_memory = None

    def on_mirror(self):
        selection_index = self.ui.object_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.object_combo.rootModelIndex())
        try:
            fcobj = model_index.internalPointer().obj
        except Exception:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
            return

        if fcobj.kind not in ['gerber', 'geometry', 'excellon']:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Only Gerber, Excellon and Geometry objects can be mirrored."))
            return

        axis = self.ui.mirror_axis.get_value()
        mode = self.ui.axis_location.get_value()

        if mode == "box":
            selection_index_box = self.ui.box_combo.currentIndex()
            model_index_box = self.app.collection.index(selection_index_box, 0, self.ui.box_combo.rootModelIndex())
            try:
                bb_obj = model_index_box.internalPointer().obj
            except Exception:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Box object loaded ..."))
                return

            xmin, ymin, xmax, ymax = bb_obj.bounds()
            px = 0.5 * (xmin + xmax)
            py = 0.5 * (ymin + ymax)
        else:
            try:
                px, py = self.ui.point_entry.get_value()
            except TypeError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("There are no Point coordinates in the Point field. "
                                                              "Add coords and try again ..."))
                return

        before = object_state(fcobj)
        try:
            with history_of(self.app).snapshot(_("Mirror"), [fcobj]):
                if fcobj.mirror(axis, [px, py]) == 'fail':
                    raise ValueError(_("Could not mirror the object."))
                if fcobj.regenerate_source() is False:
                    raise ValueError(_("Could not regenerate the mirrored source."))
        except Exception as err:
            restore_object_state(fcobj, before)
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return
        self.app.app_obj.object_changed.emit(fcobj)
        fcobj.plot()
        self.app.inform.emit('[success] %s: %s' % (_("Object was mirrored"), str(fcobj.obj_options['name'])))

    def on_point_add(self):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        val = self.app.options.get(
            "global_point_clipboard_format",
            app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
        ) % \
              (self.decimals, self.app.mouse_click_pos[0], self.decimals, self.app.mouse_click_pos[1])
        self.ui.point_entry.set_value(val)

    def on_drill_delete_last(self):
        drill_values_without_last_tupple = self.drill_values.rpartition('(')[0]
        self.drill_values = drill_values_without_last_tupple
        self.ui.alignment_holes.set_value(self.drill_values)

        # adjust the clipboard content too
        try:
            old_clipb = safe_eval(self.app.clipboard.text())
        except Exception:
            # self.log.error("App.on_mouse_and_key_modifiers() --> %s" % str(err))
            old_clipb = None

        if isinstance(old_clipb, list):
            red_clip = old_clipb[:-1]
            clip_text = str(red_clip[0]) if len(red_clip) == 1 else str(red_clip)
        else:
            clip_text = ''
        self.app.clipboard.setText(clip_text)

    def on_toggle_pointbox(self, val):
        DsidedUI.set_reference(self.ui, val)

    def on_bbox_coordinates(self):
        xmin = Inf
        ymin = Inf
        xmax = -Inf
        ymax = -Inf

        obj_list = self.app.collection.get_selected()
        if not obj_list:
            selection_index = self.ui.object_combo.currentIndex()
            model_index = self.app.collection.index(selection_index, 0, self.ui.object_combo.rootModelIndex())
            try:
                fcobj = model_index.internalPointer().obj
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))
                return
            xmin, ymin, xmax, ymax = fcobj.bounds()
        else:
            for obj in obj_list:
                try:
                    gxmin, gymin, gxmax, gymax = obj.bounds()
                    xmin = min([xmin, gxmin])
                    ymin = min([ymin, gymin])
                    xmax = max([xmax, gxmax])
                    ymax = max([ymax, gymax])
                except Exception as e:
                    self.app.log.error("Tried to get bounds of empty geometry in DblSidedTool. %s" % str(e))
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                    return

        self.ui.xmin_entry.set_value(xmin)
        self.ui.ymin_entry.set_value(ymin)
        self.ui.xmax_entry.set_value(xmax)
        self.ui.ymax_entry.set_value(ymax)
        cx = '%.*f' % (self.decimals, (((xmax - xmin) / 2.0) + xmin))
        cy = '%.*f' % (self.decimals, (((ymax - ymin) / 2.0) + ymin))
        val_txt = '(%s, %s)' % (cx, cy)

        self.ui.center_entry.set_value(val_txt)
        self.ui.axis_location.set_value('point')
        self.ui.point_entry.set_value(val_txt)
        self.app.delete_selection_shape()

    def on_xmin_clicked(self):
        xmin = self.ui.xmin_entry.get_value()
        self.ui.axis_location.set_value('point')
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        try:
            px, py = self.ui.point_entry.get_value()
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, xmin, self.decimals, py)
        except TypeError:
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, xmin, self.decimals, 0.0)
        self.ui.point_entry.set_value(val)

    def on_ymin_clicked(self):
        ymin = self.ui.ymin_entry.get_value()
        self.ui.axis_location.set_value('point')
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        try:
            px, py = self.ui.point_entry.get_value()
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, px, self.decimals, ymin)
        except TypeError:
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, 0.0, self.decimals, ymin)
        self.ui.point_entry.set_value(val)

    def on_xmax_clicked(self):
        xmax = self.ui.xmax_entry.get_value()
        self.ui.axis_location.set_value('point')
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        try:
            px, py = self.ui.point_entry.get_value()
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, xmax, self.decimals, py)
        except TypeError:
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, xmax, self.decimals, 0.0)
        self.ui.point_entry.set_value(val)

    def on_ymax_clicked(self):
        ymax = self.ui.ymax_entry.get_value()
        self.ui.axis_location.set_value('point')
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        try:
            px, py = self.ui.point_entry.get_value()
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, px, self.decimals, ymax)
        except TypeError:
            val = self.app.options.get(
                "global_point_clipboard_format",
                app_defaults.get("global_point_clipboard_format", AppDefaults.factory_defaults.get("global_point_clipboard_format"))
            ) % (self.decimals, 0.0, self.decimals, ymax)
        self.ui.point_entry.set_value(val)

    def reset_fields(self):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.box_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))

        self.ui.object_combo.setCurrentIndex(0)
        self.ui.box_combo.setCurrentIndex(0)
        self.ui.box_type_radio.set_value('grb')

        self.drill_values = ""
        self.ui.align_ref_label_val.set_value('')

    def refresh_basic_sources(self, *args):
        objects = self.app.collection.get_list()
        for row, (combo, kind) in enumerate(zip(self.ui.basic_combos, ('gerber', 'gerber', 'excellon'))):
            selected = combo.currentData()
            blocker = QtCore.QSignalBlocker(combo)
            combo.clear()
            combo.addItem('')
            for obj in objects:
                if obj.kind == kind:
                    combo.addItem(obj.obj_options['name'], obj)
            combo.setCurrentIndex(next((i for i in range(1, combo.count())
                                        if combo.itemData(i) is selected), 0))
            del blocker
            if combo.currentData() is not selected:
                self.on_basic_selection_changed(row)
        self.ui.update_basic_state()

    def on_basic_selection_changed(self, row):
        self.invalidate_alignment()

    def basic_sources(self):
        objects = []
        live = self.app.collection.get_list()
        for combo, kind in zip(self.ui.basic_combos, ('gerber', 'gerber', 'excellon')):
            obj = combo.currentData()
            if obj is not None:
                if obj not in live or obj.kind != kind:
                    raise ValueError(_('A selected source no longer exists or has the wrong type.'))
                if not any(obj is other for other in objects):
                    objects.append(obj)
        if not objects:
            raise ValueError(_('Select at least one source object.'))
        return objects

    def invalidate_alignment(self, *args):
        if self.pending_alignment is not None:
            context = self.alignment_timings.pop(self.alignment_token, None)
            if context:
                context()
        self.alignment_token += 1
        self.pending_alignment = None
        self.ui.process_button.setEnabled(True)

    def _drill_settings(self):
        if not self.ui.generate_cb.isChecked():
            return None
        offset = self.ui.offset_entry.get_value()
        diameter = valid_drill_offset(self.ui.basic_drill_dia.get_value(), offset)
        target = self.ui.drills_combo.currentData() if self.ui.append_drills_cb.isChecked() else None
        return diameter, offset, target, self.ui.failsafe_cb.isChecked()

    def on_process(self):
        timing = _process_timing(self.app.log)
        if self.pending_alignment is not None:
            timing()
            return
        try:
            if self.ui.top_combo.currentData() is not None and \
                    self.ui.top_combo.currentData() is self.ui.bottom_combo.currentData():
                raise ValueError(_('The same object cannot be both Top and Bottom.'))
            objects = self.basic_sources()
            settings = self._drill_settings()
            combined_bounds(objects)
            self._start_alignment(objects, settings, timing)
        except Exception as err:
            self.alignment_timings.pop(self.alignment_token, None)
            self.pending_alignment = None
            self.invalidate_alignment()
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            timing(str(err))

    def _history_position(self):
        history = history_of(self.app)
        return (tuple(id(s) for s in getattr(history, 'undo_stack', ())),
                tuple(id(s) for s in getattr(history, 'redo_stack', ())))

    def _source_signature(self, obj):
        # Read the same contents as object_state(), but don't build an undo snapshot just to hash them.
        state = {attr: getattr(obj, attr) for attr in obj.ser_attrs if hasattr(obj, attr)}
        state.update(kind=obj.kind, history_uid=obj.history_uid)
        extra = HISTORY_EXTRA_ATTRS.get(str(obj.kind).lower(), ())
        if extra:
            state['_history_extra'] = {attr: getattr(obj, attr) for attr in extra if hasattr(obj, attr)}
        return _content_signature(state)

    def _geometry_signature(self, obj):
        # Keep presentation state in serialized options, but invalidate it after any external geometry edit.
        # Legacy unversioned text digests are stale metadata, never authority to undo a presentation.
        return 'wkb1:' + _content_signature((obj.tools, obj.solid_geometry)).hex()

    def _presentation(self, obj):
        frame = obj.obj_options.get('_2sided_frame', {})
        if frame.get('signature') and frame['signature'] == self._geometry_signature(obj):
            return frame.get('matrix')
        return None

    def _start_alignment(self, objects, settings, timing=None):
        timing = timing or _process_timing(self.app.log)
        if not self.ui.auto_align_cb.isChecked() or len(objects) < 2:
            self._apply_basic(objects, {}, settings, timing)
            return
        indices = []
        for obj in objects:
            indices.append(build_snap_index(source_view(obj)))
        presentations = {}
        for i, obj in enumerate(objects):
            matrix = self._presentation(obj)
            if matrix is not None:
                presentations[i] = invert(matrix)
        for i, matrix in presentations.items():
            index = indices[i]
            # Stage only immutable feature geometry. Live objects stay unchanged during matching.
            solids = [] if index.tree is None else [affine_transform(geo, matrix) for geo in index.tree.geometries]
            indices[i] = SnapIndex(solids, apply_matrix(matrix, index.centers), apply_matrix(matrix, index.vertices),
                                   kind=index.kind)
        gerbers = [i for i, obj in enumerate(objects) if obj.kind == 'gerber']
        drill = next((i for i, obj in enumerate(objects) if obj.kind == 'excellon'), None)
        self.alignment_token += 1
        token = self.alignment_token
        states = []
        for obj in objects:
            states.append(self._source_signature(obj))
        self.pending_alignment = (objects, states, settings, self._history_position())
        self.ui.process_button.setEnabled(False)
        signal = self.alignment_finished
        proc_container = self.app.proc_container
        def job_thread_align():
            matrices, error = {}, ''
            try:
                with proc_container.new(_('Aligning PCB sources...')):
                    matrices = alignment_matrices(indices, gerbers, drill)
                    for i, matrix in presentations.items():
                        matrices[i] = compose(matrices.get(i, (1, 0, 0, 1, 0, 0)), matrix)
            except Exception as err:
                error = str(err)
            signal.emit(token, matrices, error)

        self.alignment_timings[token] = timing
        self.app.worker_task.emit({'fcn': job_thread_align, 'params': []})

    def on_alignment_finished(self, token, matrices, error):
        timing = self.alignment_timings.pop(token, None)
        if token != self.alignment_token or self.pending_alignment is None:
            if timing:
                timing(error or None)
            return
        timing = timing or _process_timing(self.app.log)
        objects, states, settings, history_position = self.pending_alignment
        self.invalidate_alignment()
        if error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            timing(error)
            return
        try:
            if self.basic_sources() != objects or self._history_position() != history_position or \
                    any(state != self._source_signature(obj) for obj, state in zip(objects, states)):
                raise ValueError(_('Sources or history changed during alignment. Run Process again.'))
            self._apply_basic(objects, matrices, settings, timing)
        except Exception as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            timing(str(err))

    def _notify_basic(self, obj, matrix=None):
        obj.set_offset_values(bounds=obj.bounds())
        self.app.app_obj.object_changed.emit(obj)
        if matrix is None:
            obj.plot()
        else:
            obj.plot_transformed(matrix)

    def _apply_basic(self, objects, matrices, settings, timing=None):
        timing = timing or _process_timing(self.app.log)
        history = history_of(self.app)
        output = None
        source_ids = sorted(obj.obj_options.get('_2sided_source_id', obj.history_uid) for obj in objects)
        previous, discard = [], []
        if settings:
            target = settings[2]
            output = target if target is not None else next((obj for obj in self.app.collection.get_list()
                if obj.kind == 'excellon' and obj.obj_options.get('_2sided_sources') == source_ids), None)
            # Our holes live in 1 output at a time: retire them from the location used before.
            for obj in self.app.collection.get_list():
                if obj is output or not has_generated(obj) or \
                        not (obj in objects or obj.obj_options.get('_2sided_sources') == source_ids):
                    continue
                if obj in objects or unowned_features(obj):
                    previous.append(obj)
                else:
                    discard.append(obj)
        affected = objects + [obj for obj in [output] + previous if obj is not None and obj not in objects]
        before = []
        for obj in affected:
            before.append(object_state(obj))
        presentations = {}
        for i, obj in enumerate(objects):
            presentations[i] = self._presentation(obj)
        identity = (1, 0, 0, 1, 0, 0)
        matrices = dict(matrices)
        registered_bounds = []
        for i, obj in enumerate(objects):
            recovery = invert(presentations[i]) if presentations[i] is not None else identity
            if not self.ui.auto_align_cb.isChecked() or len(objects) < 2:
                matrices[i] = recovery
            matrix = matrices.get(i, identity)
            frame = obj.obj_options.get('_2sided_frame', {})
            if presentations[i] is not None and np.allclose(matrix, recovery, atol=1e-10, rtol=0):
                matrices[i] = recovery
                bounds = frame['bounds']
            elif np.allclose(matrix, identity, atol=1e-10, rtol=0):
                matrices[i] = identity
                bounds = source_view(obj).bounds()
            else:
                geometry = flatten_shapely_geometry(source_view(obj).solid_geometry)
                staged_bounds = np.asarray([affine_transform(geo, matrix).bounds for geo in geometry])
                bounds = (*staged_bounds[:, :2].min(axis=0), *staged_bounds[:, 2:].max(axis=0))
            registered_bounds.append(bounds)
        xmin = min(b[0] for b in registered_bounds)
        ymin = min(b[1] for b in registered_bounds)
        xmax = max(b[2] for b in registered_bounds)
        ymax = max(b[3] for b in registered_bounds)
        bounds = xmin, ymin, xmax, ymax
        # L-R: the board turns over left/right (x -> 2cx - x); T-B: top/bottom (y -> 2cy - y).
        turn = ('L-R', 'T-B')[self.ui.type_combo.currentIndex()]
        flip = affine_mirror_matrix('Y' if turn == 'L-R' else 'X', ((xmin + xmax) / 2, (ymin + ymax) / 2))
        # The Bottom object is the one milled after turning the board over: manufactured (mirrored), by role.
        bottom = self.ui.bottom_combo.currentData()
        manufactured = {i for i, obj in enumerate(objects) if obj is bottom}
        points = bounds_drills(bounds, settings[0], settings[1], turn, settings[3]) if settings else None
        changed = {}
        for i, obj in enumerate(objects):
            matrix = matrices.get(i, identity)
            if i in manufactured:
                matrix = compose(flip, matrix)
            if not np.allclose(matrix, identity, atol=1e-10, rtol=0):
                changed[obj] = matrix
        try:
            with history.group(_('2-Sided Process')):
                with history.snapshot(_('2-Sided Process'), affected):
                    for obj, matrix in changed.items():
                        apply_matrix_to_object(obj, matrix)
                    if settings:
                        for obj in objects:
                            obj.obj_options.setdefault('_2sided_source_id', obj.history_uid)
                        for obj in previous:
                            retire_generated(obj)
                            changed[obj] = None
                        diameter = settings[0]
                        if output is not None:
                            replace_generated(output, diameter, points, self.decimals)
                            changed[output] = None
                    for i, obj in enumerate(objects):
                        # A new request toggles the manufacturing presentation, not input orientation correction.
                        if obj in changed:
                            if obj.regenerate_source() is False:
                                raise ValueError(_('Could not regenerate the aligned source.'))
                        if i in manufactured:
                            obj.obj_options['_2sided_frame'] = {
                                'matrix': flip, 'bounds': registered_bounds[i],
                                'signature': self._geometry_signature(obj)}
                        else:
                            obj.obj_options.pop('_2sided_frame', None)
                    # Publish a new output last: a failed source export must not leave an orphan/history step.
                    if settings and output is None:
                        def init(obj, app):
                            replace_generated(obj, diameter, points, self.decimals)
                            obj.obj_options['_2sided_sources'] = source_ids
                        output = self.new_object('excellon', _('Alignment Drills'), init,
                                                 autoselected=False)
                        if output is None or output == 'fail':
                            raise ValueError(_('Could not create alignment drills.'))
                    # The earlier output held only our holes: it has nothing left to drill.
                    if discard:
                        history.record_delete(_('2-Sided Process'), discard)
                        self.app.object_ops.delete_objects_now(discard)
        except Exception:
            for obj, state in zip(affected, before):
                restore_object_state(obj, state)
            raise
        for obj, matrix in changed.items():
            self._notify_basic(obj, matrix)
        self.app.inform.emit('[success] %s' % _('2-Sided operation completed.'))
        timing()
