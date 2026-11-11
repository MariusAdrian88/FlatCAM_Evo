# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     5/25/2020                                      #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import VerticalScrollArea, FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner, FCInputDialogSpinnerButton, FCTable, \
    OptionalInputSection

import logging
from copy import deepcopy
from typing import Iterable

import numpy as np
import sys
import math
from hashlib import sha256

import shapely
from shapely import LineString, MultiLineString, Polygon, MultiPolygon, Point, LinearRing
from shapely.ops import unary_union

from appHandlers.appHistory import history_of
import gettext
import appTranslation as fcTranslate
import builtins

from appDatabase import load_tools_database, tools_database_error_message
from camlib import deepcopy_keep_geometry, Geometry, grace, flatten_shapely_geometry, polygon_gap_pairs, outline_area, boundary_area, clip_paths_to_area, \
    area_outline_paths, boundary_passes_message, isolation_leftover_paths, milling_orient, pass_covers_area
from appCommon.SafeEval import safe_eval
from appPlugins.ToolIsolation.IsolationUI import IsoUI
from appPlugins.ToolIsolation import IsolationGen

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def pad_solids(gerber_obj, geometry=None):
    """
    The solids of the pads (the flashes that have a point to follow) of a Gerber object.

    :param gerber_obj:  the Gerber object
    :param geometry:    if given, only the pads that touch it: the extra pad passes follow the isolated geometry only
    :return:            list of Shapely Polygon
    """
    pads = [geo['solid'] for aperture in gerber_obj.tools.values() for geo in aperture['geometry']
            if geo.get('solid') is not None and isinstance(geo.get('follow'), Point)]
    if geometry is None or not pads:
        return pads
    touched = shapely.STRtree(flatten_shapely_geometry(geometry)).query(
        np.array(pads, dtype=object), predicate='intersects')[0]
    return [pads[idx] for idx in sorted(set(touched.tolist()))]


def tool_strategy(value):
    """
    The isolation strategy of a tool as 'offset' or 'voronoi' (None: Offset, as in an old tool; 0 / 1 are accepted).

    :return:    'offset', 'voronoi', or None if the value is not a strategy
    """
    if value is None:
        return 'offset'
    if isinstance(value, int) and not isinstance(value, bool):
        return ('offset', 'voronoi')[value] if 0 <= value <= 1 else None
    value = str(value).lower()
    return value if value in ('offset', 'voronoi') else None


def tools_for_run(tools_storage, sel_tools):
    """
    The selected tools copied for one isolation run: each has its own data and none has the geometry of an earlier run.
    """
    return {uid: {key: deepcopy(value) for key, value in tools_storage[uid].items() if key != 'solid_geometry'}
            for uid in sel_tools}


def tools_for_object(run_tools, uids):
    """
    The tools of a created object: the ones that made geometry in the run, each with its own copy of the data.
    """
    return {uid: {**run_tools[uid], 'data': deepcopy(run_tools[uid]['data'])} for uid in uids}


class ToolIsolation(Geometry, AppTool):
    optimal_found_sig = QtCore.pyqtSignal(float)
    # The generator runs in a worker: the failure region is drawn by a queued signal, never on the worker thread.
    voronoi_diagnostic_sig = QtCore.pyqtSignal(object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        Geometry.__init__(
            self,
            geo_steps_per_circle=self.app.options.get(
                "gerber_circle_steps",
                app_defaults.get("gerber_circle_steps", AppDefaults.factory_defaults.get("gerber_circle_steps"))),
            app=app)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = IsoUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # the signals of the app and of the plugin itself: connected once, the UI is rebuilt in set_tool_ui()
        # Custom Signal
        self.optimal_found_sig.connect(self.on_optimal_found)
        self.voronoi_diagnostic_sig.connect(self.on_voronoi_diagnostic)
        self._voronoi_diagnostic_token = object()
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        self.app.cleanup.connect(self.set_tool_ui)

        self.init_context_menu()

        # #############################################################################
        # ########################## VARIABLES ########################################
        # #############################################################################
        self.units = ''
        self.iso_tools = {}
        self.tooluid = 0

        # store here the default data for Geometry Data
        self.default_data = {}

        self.obj_name = ""
        self.grb_obj = None

        self.sel_rect = []

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        # store here the points for the "Polygon" area selection shape
        self.points = []

        # set this as True when in middle of drawing a "Polygon" area selection shape
        # it is made False by first click to signify that the shape is complete
        self.poly_drawn = False


        # store geometry from Polygon selection
        self.poly_dict = {}
        # the same selection by geometry: {polygon wkb: shape id}, for the O(1) add / remove of a polygon
        self._poly_keys = {}
        # (object, its solid_geometry, number of items, flat polygons, STRtree) of the object in the selection mode
        self._sel_cache = None
        # True while the plugin has the grid snapping switched off for the Polygon selection
        self._grid_disabled = False
        # (Gerber object, tool diameters) of the tool check that runs when the Area / Polygon selection is finished
        self.pending_validation = None

        self.grid_status_memory = self.app.ui.grid_snap_btn.isChecked()

        # store here the state of the combine_cb GUI element
        # used when the rest machining is toggled
        self.old_combine_state = self.app.options.get(
            "tools_iso_combine_passes",
            app_defaults.get("tools_iso_combine_passes", AppDefaults.factory_defaults.get("tools_iso_combine_passes")))

        # store here the isolation type of each tool (by uid) while the level is Beginner, which isolates 'full'
        self.advanced_isotype = {}

        # store here solid_geometry when there are tool with isolation job
        self.solid_geometry = []

        self.tool_type_item_options = []


        self.tooldia = None
        # store here the tool diameter that is guaranteed to isolate the object
        self.safe_tooldia = None

        # multiprocessing
        self.pool = self.app.pool
        self.results = []

        # store here the validation status (if tool validation is used)
        self.validation_status = True

        # disconnect flags
        self.area_sel_disconnect_flag = False
        self.poly_sel_disconnect_flag = False

        self.form_fields = {
            "tools_mill_tool_shape":    self.ui.tool_shape_combo,
            "tools_mill_cutz":          self.ui.cutz_entry,
            "tools_mill_vtipdia":       self.ui.tipdia_entry,
            "tools_mill_vtipangle":     self.ui.tipangle_entry,
            "tools_iso_passes":         self.ui.passes_entry,
            "tools_iso_pad_passes":     self.ui.pad_passes_entry,
            "tools_iso_overlap":        self.ui.iso_overlap_entry,
            "tools_iso_milling_type":   self.ui.milling_type_radio,
            "tools_iso_combine":        self.ui.combine_passes_cb,
            "tools_iso_isotype":        self.ui.iso_type_radio,
            "tools_iso_strategy":       self.ui.strategy_combo,
            "tools_iso_voronoi_spacing": self.ui.voronoi_spacing_entry,
            "tools_iso_voronoi_pads": self.ui.voronoi_pads_cb
        }

        self.name2option = {
            "i_tool_shape":     "tools_mill_tool_shape",
            "i_cutz":           "tools_mill_cutz",
            "i_tipdia":         "tools_mill_vtipdia",
            "i_tipangle":       "tools_mill_vtipangle",
            "i_passes":         "tools_iso_passes",
            "i_pad_passes":     "tools_iso_pad_passes",
            "i_overlap":        "tools_iso_overlap",
            "i_milling_type":   "tools_iso_milling_type",
            "i_combine":        "tools_iso_combine",
            "i_iso_type":       "tools_iso_isotype",
            "i_strategy":       "tools_iso_strategy",
            "i_voronoi_spacing": "tools_iso_voronoi_spacing",
            "i_voronoi_pads": "tools_iso_voronoi_pads"
        }

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+I', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolIsolation()")

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

        # reset those objects on a new run
        self.grb_obj = None
        self.obj_name = ''

        self.build_ui()

        # all the tools are selected by default
        self.ui.tools_table.selectAll()

        self.app.ui.notebook.setTabText(2, _("Isolation"))

    def clear_contex_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):
        # #############################################################################
        # ###################### Setup CONTEXT MENU ###################################
        # #############################################################################
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Search and Add"),
            self.on_add_tool_by_key,
            icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Pick from DB"),
            self.on_tool_add_from_db_clicked,
            icon=QtGui.QIcon(self.app.resource_location + "/search_db32.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Delete"),
            lambda: self.on_tool_delete(rows_to_delete=None, all_tools=None),
            icon=QtGui.QIcon(self.app.resource_location + "/trash16.png")
        )

    def connect_signals_at_init(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.deltool_btn.clicked.connect(self.on_tool_delete)

        self.ui.find_optimal_button.clicked.connect(self.on_find_optimal_tooldia)

        self.ui.reference_combo_type.currentIndexChanged.connect(self.on_reference_combo_changed)
        self.ui.select_combo.currentIndexChanged.connect(self.on_toggle_reference)

        self.ui.type_excobj_radio.activated_custom.connect(self.on_type_excobj_index_changed)
        self.ui.type_boundary_radio.activated_custom.connect(self.on_type_boundary_index_changed)
        self.ui.apply_param_to_all.clicked.connect(self.on_apply_param_to_all_clicked)

        # adding Tools
        self.ui.search_and_add_btn.clicked.connect(lambda: self.on_tool_add())
        self.ui.addtool_from_db_btn.clicked.connect(self.on_tool_add_from_db_clicked)

        self.ui.generate_iso_button.clicked.connect(self.on_iso_button_click)
        try:
            self.ui.milling_button.clicked.disconnect(self.on_milling_button_click)
        except TypeError:
            pass
        self.ui.milling_button.clicked.connect(self.on_milling_button_click)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

        # Select All/None when in Polygon Selection mode
        self.ui.sel_all_btn.clicked.connect(self.on_select_all_polygons)
        self.ui.clear_all_btn.clicked.connect(self.on_deselect_all_polygons)

        # Drag and drop of the rows of the Tools table
        self.ui.tools_table.drag_drop_sig.connect(self.rebuild_ui)

    def on_optimal_found(self, val):
        self.ui.new_tooldia_entry.set_value(float(val))

    def on_voronoi_diagnostic(self, payload):
        """Clears or draws the Voronoi failure region; this slot always runs on the GUI thread."""
        if (payload or {}).get('token') is not self._voronoi_diagnostic_token:
            return
        if payload.get('action') == 'pad_report':
            report = payload['report']
            message = _("Pad isolation: %d unique surviving flashed pads; %d with added cuts; %d skipped.") % (
                report['candidates'], report['cut'], report['skipped'])
            if not report['candidates']:
                message += ' ' + _("No supported flashed-pad provenance; region-only and negative images are skipped.")
            elif report['skipped']:
                message += ' ' + _("Skipped pads have no additional safe room with this cutter and output precision.")
            self.app.inform.emit(('[INFO_NOTCL] ' if report['cut'] else '[WARNING_NOTCL] ') + message)
            return
        try:
            self.temp_shapes.clear(update=True)
            region = (payload or {}).get('region')
            if region is not None and not region.is_empty:
                self.temp_shapes.add(shape=region, color='#FF0000CC', update=True, layer=0)
                self.temp_shapes.redraw()
        except Exception as err:
            self.app.log.debug("ToolIsolation.on_voronoi_diagnostic() -> %s" % str(err))

    def on_milling_button_click(self):
        self.app.milling_tool.run()

    def on_type_excobj_index_changed(self, val):
        obj_type = 0 if val == 'gerber' else 2
        self.ui.exc_obj_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.exc_obj_combo.setCurrentIndex(0)
        self.ui.exc_obj_combo.obj_type = {
            "gerber": "Gerber", "geometry": "Geometry"
        }[self.ui.type_excobj_radio.get_value()]

    def on_type_boundary_index_changed(self, val):
        obj_type = 0 if val == 'gerber' else 2
        self.ui.boundary_obj_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.boundary_obj_combo.setCurrentIndex(0)
        self.ui.boundary_obj_combo.obj_type = {
            "gerber": "Gerber", "geometry": "Geometry"
        }[self.ui.type_boundary_radio.get_value()]

    def get_boundary_area(self):
        """
        The boundary area from the UI: a concave enclosure around the selected object's geometry.

        :return:    Polygon / MultiPolygon, None when the boundary is not used, or 'fail' (with a message)
        """
        if not self.ui.boundary_cb.get_value():
            return None

        boundary_obj = self.app.collection.get_by_name(self.ui.boundary_obj_combo.currentText())
        if boundary_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No boundary object is selected."))
            return 'fail'

        area = boundary_area(boundary_obj)
        if area is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                _("The boundary object encloses no area"), boundary_obj.obj_options['name']))
            return 'fail'
        return area

    def set_tool_ui(self):
        self._voronoi_diagnostic_token = object()
        # leave a running selection mode (canvas handlers, grid snap, shapes) before the UI is rebuilt
        try:
            self.end_selection_mode()
        except AttributeError as err:
            self.app.log.debug("ToolIsolation.set_tool_ui() end_selection_mode -> %s" % str(err))

        self.units = self.app.app_units.upper()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        self.clear_ui(self.layout)
        self.ui = IsoUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.clear_contex_menu()
        self.init_context_menu()

        self.form_fields = {
            "tools_mill_tool_shape":    self.ui.tool_shape_combo,
            "tools_mill_cutz":          self.ui.cutz_entry,
            "tools_mill_vtipdia":       self.ui.tipdia_entry,
            "tools_mill_vtipangle":     self.ui.tipangle_entry,
            "tools_iso_passes":         self.ui.passes_entry,
            "tools_iso_pad_passes":     self.ui.pad_passes_entry,
            "tools_iso_overlap":        self.ui.iso_overlap_entry,
            "tools_iso_milling_type":   self.ui.milling_type_radio,
            "tools_iso_combine":        self.ui.combine_passes_cb,
            "tools_iso_isotype":        self.ui.iso_type_radio,
            "tools_iso_strategy":       self.ui.strategy_combo,
            "tools_iso_voronoi_spacing": self.ui.voronoi_spacing_entry,
            "tools_iso_voronoi_pads": self.ui.voronoi_pads_cb
        }

        # reset the value to prepare for another isolation
        self.safe_tooldia = None

        # try to select in the Gerber combobox the active object
        try:
            selected_obj = self.app.collection.get_active()
            if selected_obj is not None and selected_obj.kind == 'gerber':
                current_name = selected_obj.obj_options['name']
                self.ui.object_combo.set_value(current_name)
            if selected_obj is None and [self.ui.object_combo.itemText(i) for i in range(self.ui.object_combo.count())]:
                self.ui.object_combo.setCurrentIndex(0)
        except Exception as ee:
            self.app.log.debug("ToolIsolation.set_tool_ui() Select Gerber object -> %s" % str(ee))

        if self.app.options.get(
                "gerber_buffering", app_defaults.get("gerber_buffering", AppDefaults.factory_defaults.get("gerber_buffering"))) == 'no':
            self.ui.create_buffer_button.show()
            try:
                self.ui.create_buffer_button.clicked.disconnect(self.on_generate_buffer)
            except TypeError:
                pass
            self.ui.create_buffer_button.clicked.connect(self.on_generate_buffer)
        else:
            self.ui.create_buffer_button.hide()

        self.ui.tools_frame.show()

        self.ui.type_excobj_radio.set_value('gerber')
        self.ui.type_boundary_radio.set_value('gerber')
        self.ui.boundary_cb.set_value(False)
        self.ui.trace_boundary_cb.set_value(True)

        # run those once so the obj_type attribute is updated for the FCComboboxes
        # so the last loaded object is displayed
        self.on_type_excobj_index_changed(val="gerber")
        self.on_type_boundary_index_changed(val="gerber")
        self.on_reference_combo_changed()

        self.ui.iso_order_combo.set_value(self.app.options.get(
            "tools_iso_order", app_defaults.get("tools_iso_order", AppDefaults.factory_defaults.get("tools_iso_order"))))
        self.ui.tool_shape_combo.set_value(self.app.options.get(
            "tools_iso_tool_shape", app_defaults.get("tools_iso_tool_shape", AppDefaults.factory_defaults.get("tools_iso_tool_shape"))))

        strategy_value = str(self.app.options.get(
            "tools_iso_strategy",
            app_defaults.get("tools_iso_strategy", AppDefaults.factory_defaults.get("tools_iso_strategy")))).lower()
        self.ui.strategy_combo.set_value('voronoi' if strategy_value == 'voronoi' else 'offset')
        self.ui.voronoi_spacing_entry.set_value(self.app.options.get(
            "tools_iso_voronoi_spacing",
            app_defaults.get("tools_iso_voronoi_spacing",
                              AppDefaults.factory_defaults.get("tools_iso_voronoi_spacing"))))
        self.ui.voronoi_pads_cb.set_value(self.app.options.get(
            "tools_iso_voronoi_pads", app_defaults.get("tools_iso_voronoi_pads", False)))

        self.ui.tipdia_entry.set_value(self.app.options.get(
            "tools_iso_vtipdia", app_defaults.get("tools_iso_vtipdia", AppDefaults.factory_defaults.get("tools_iso_vtipdia"))))
        self.ui.tipangle_entry.set_value(self.app.options.get(
            "tools_iso_vtipangle", app_defaults.get("tools_iso_vtipangle", AppDefaults.factory_defaults.get("tools_iso_vtipangle"))))
        self.ui.cutz_entry.set_value(self.app.options.get(
            "tools_iso_cutz", app_defaults.get("tools_iso_cutz", AppDefaults.factory_defaults.get("tools_iso_cutz"))))

        self.ui.passes_entry.set_value(self.app.options.get(
            "tools_iso_passes", app_defaults.get("tools_iso_passes", AppDefaults.factory_defaults.get("tools_iso_passes"))))
        self.ui.pad_passes_entry.set_value(self.app.options.get(
            "tools_iso_pad_passes", app_defaults.get("tools_iso_pad_passes", AppDefaults.factory_defaults.get("tools_iso_pad_passes"))))
        self.ui.iso_overlap_entry.set_value(self.app.options.get(
            "tools_iso_overlap", app_defaults.get("tools_iso_overlap", AppDefaults.factory_defaults.get("tools_iso_overlap"))))
        self.ui.milling_type_radio.set_value(self.app.options.get(
            "tools_iso_milling_type", app_defaults.get("tools_iso_milling_type", AppDefaults.factory_defaults.get("tools_iso_milling_type"))))
        self.ui.combine_passes_cb.set_value(self.app.options.get(
            "tools_iso_combine_passes", app_defaults.get("tools_iso_combine_passes", AppDefaults.factory_defaults.get("tools_iso_combine_passes"))))
        self.ui.clear_leftovers_cb.set_value(self.app.options.get(
            "tools_iso_clear_leftovers",
            app_defaults.get("tools_iso_clear_leftovers", AppDefaults.factory_defaults.get("tools_iso_clear_leftovers"))))
        self.ui.valid_cb.set_value(self.app.options.get(
            "tools_iso_check_valid", app_defaults.get("tools_iso_check_valid", AppDefaults.factory_defaults.get("tools_iso_check_valid"))))
        self.ui.simplify_cb.set_value(self.app.options.get(
            "tools_iso_simplification", app_defaults.get("tools_iso_simplification", AppDefaults.factory_defaults.get("tools_iso_simplification"))))
        self.ui.sim_tol_entry.set_value(self.app.options.get(
            "tools_iso_simplification_tol",
            app_defaults.get("tools_iso_simplification_tol", AppDefaults.factory_defaults.get("tools_iso_simplification_tol"))))

        self.ui.area_shape_radio.set_value(self.app.options.get(
            "tools_iso_area_shape", app_defaults.get("tools_iso_area_shape", AppDefaults.factory_defaults.get("tools_iso_area_shape"))))
        self.ui.poly_int_cb.set_value(self.app.options.get(
            "tools_iso_poly_ints", app_defaults.get("tools_iso_poly_ints", AppDefaults.factory_defaults.get("tools_iso_poly_ints"))))
        self.ui.forced_rest_iso_cb.set_value(self.app.options.get(
            "tools_iso_force", app_defaults.get("tools_iso_force", AppDefaults.factory_defaults.get("tools_iso_force"))))

        self.ui.new_tooldia_entry.set_value(self.app.options.get(
            "tools_iso_newdia", app_defaults.get("tools_iso_newdia", AppDefaults.factory_defaults.get("tools_iso_newdia"))))

        self.ui.select_combo.set_value(self.app.options.get(
            "tools_iso_selection", app_defaults.get("tools_iso_selection", AppDefaults.factory_defaults.get("tools_iso_selection"))))
        self.ui.multi_objects_cb.set_value(self.app.options.get(
            "tools_iso_multi_objects",
            app_defaults.get("tools_iso_multi_objects", AppDefaults.factory_defaults.get("tools_iso_multi_objects"))))

        # loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        # if loaded_obj:
        #     outname = loaded_obj.obj_options['name']
        # else:
        #     outname = ''

        # init the working variables
        self.default_data.clear()
        kind = 'geometry'
        for option in self.app.options:
            if option.find(kind + "_") == 0:
                oname = option[len(kind) + 1:]
                self.default_data[oname] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

            if option.find('tools_') == 0:
                self.default_data[option] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

        # transfer some Isolation Plugin values to the Milling Plugin; that works only for adding a default tool
        self.default_data['tools_mill_tool_shape'] = deepcopy(self.default_data['tools_iso_tool_shape'])
        self.default_data['tools_mill_cutz'] = deepcopy(self.default_data['tools_iso_cutz'])
        self.default_data['tools_mill_vtipdia'] = deepcopy(self.default_data['tools_iso_vtipdia'])
        self.default_data['tools_mill_vtipangle'] = deepcopy(self.default_data['tools_iso_vtipangle'])

        # self.default_data.update({
        #     "name":                     outname + '_iso',
        #     "plot":                     self.app.options["geometry_plot"],
        #     "cutz":                     float(self.app.options["tools_iso_tool_cutz"]),
        #     "vtipdia":                  float(self.app.options["tools_iso_tool_vtipdia"]),
        #     "vtipangle":                float(self.app.options["tools_iso_tool_vtipangle"]),
        #     "travelz":                  self.app.options["geometry_travelz"],
        #     "feedrate":                 self.app.options["geometry_feedrate"],
        #     "feedrate_z":               self.app.options["geometry_feedrate_z"],
        #     "feedrate_rapid":           self.app.options["geometry_feedrate_rapid"],
        #
        #     "multidepth":               self.app.options["geometry_multidepth"],
        #     "ppname_g":                 self.app.options["geometry_ppname_g"],
        #     "depthperpass":             self.app.options["geometry_depthperpass"],
        #     "extracut":                 self.app.options["geometry_extracut"],
        #     "extracut_length":          self.app.options["geometry_extracut_length"],
        #     "toolchange":               self.app.options["geometry_toolchange"],
        #     "toolchangez":              self.app.options["geometry_toolchangez"],
        #     "endz":                     self.app.options["geometry_endz"],
        #     "endxy":                    self.app.options["geometry_endxy"],
        #
        #     "dwell":                    self.app.options["geometry_dwell"],
        #     "dwelltime":                self.app.options["geometry_dwelltime"],
        #     "spindlespeed":             self.app.options["geometry_spindlespeed"],
        #     "spindledir":               self.app.options["geometry_spindledir"],
        #
        #     "optimization_type":        self.app.options["geometry_optimization_type"],
        #     "search_time":              self.app.options["geometry_search_time"],
        #     "toolchangexy":             self.app.options["geometry_toolchangexy"],
        #     "startz":                   self.app.options["geometry_startz"],
        #
        #     "area_exclusion":           self.app.options["geometry_area_exclusion"],
        #     "area_shape":               self.app.options["geometry_area_shape"],
        #     "area_strategy":            self.app.options["geometry_area_strategy"],
        #     "area_overz":               float(self.app.options["geometry_area_overz"]),
        # })

        try:
            dias = [float(self.app.options.get(
                "tools_iso_tooldia", app_defaults.get("tools_iso_tooldia", AppDefaults.factory_defaults.get("tools_iso_tooldia"))))]
        except (ValueError, TypeError):
            tool_dia_option = self.app.options.get(
                "tools_iso_tooldia", app_defaults.get("tools_iso_tooldia", AppDefaults.factory_defaults.get("tools_iso_tooldia")))
            if isinstance(tool_dia_option, str):
                dias = [float(safe_eval(dia)) for dia in tool_dia_option.split(",") if dia != '']
            else:
                dias = tool_dia_option

        # reset the state first: it is clean also when there is no tool diameter in the preferences
        self.tooluid = 0
        self.iso_tools.clear()
        self.advanced_isotype.clear()
        self.old_combine_state = self.app.options.get(
            "tools_iso_combine_passes",
            app_defaults.get("tools_iso_combine_passes", AppDefaults.factory_defaults.get("tools_iso_combine_passes")))

        if not dias:
            self.app.log.error(
                "At least one tool diameter needed. Verify in Edit -> Preferences -> Plugins -> Isolation Tools.")
            self.build_ui()
            self.update_ui()

        for tool_dia in dias:
            self.on_tool_add(custom_dia=tool_dia)

        self.obj_name = ""
        self.grb_obj = None

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        prog_plot = True if self.app.options.get(
            "tools_iso_plotting", app_defaults.get("tools_iso_plotting", AppDefaults.factory_defaults.get("tools_iso_plotting"))) == 'progressive' else False
        # also removes a Voronoi failure region from a previous run
        self.temp_shapes.clear(update=True)

        self.sel_rect = []

        self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

        # the rest machining checkbox shows the preference
        self.ui.rest_cb.set_value(self.app.options.get(
            "tools_iso_rest", app_defaults.get("tools_iso_rest", AppDefaults.factory_defaults.get("tools_iso_rest"))))

        # Show/Hide Advanced Options; the Beginner level has no rest machining and the selection 'All'
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        self.on_toggle_reference()
        # the state of the rest machining follows the checkbox that the user sees
        self.on_rest_machining_check(state=self.ui.rest_cb.get_value())

        # the V-shape parameters are shown when the shape is V
        self.ui_update_v_shape(self.ui.tool_shape_combo.get_value(), update_dia=False)

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
        # the widgets are changed here to show the level: that is not a change of the parameters of a tool
        self.ui_disconnect()
        old_order = self.ui.iso_order_combo.get_value()
        try:
            self.apply_level(checked)
            self.on_rest_machining_check(self.ui.rest_cb.get_value())
        finally:
            self.ui_connect()

        if self.ui.iso_order_combo.get_value() != old_order:
            self.build_ui()
        if checked and self.ui.tools_table.selectedItems():
            # the form shows the isolation type of the selected tool
            self.update_ui()

    def apply_level(self, checked):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            # Add Tool section
            self.ui.add_tool_frame.hide()

            # Tool parameters section: Beginner isolates 'full'; the isolation type of each tool is kept
            # and it is back when the level is Advanced
            for tool_uid, tool in self.iso_tools.items():
                if tool_uid not in self.advanced_isotype:
                    self.advanced_isotype[tool_uid] = tool['data'].get('tools_iso_isotype')
                tool['data']['tools_iso_isotype'] = 'full'

            self.ui.milling_type_label.hide()
            self.ui.milling_type_radio.hide()

            self.ui.iso_type_label.hide()
            self.ui.iso_type_radio.set_value('full')
            self.ui.iso_type_radio.hide()

            # All param section
            self.ui.apply_param_to_all.hide()

            # Common Parameters
            self.ui.rest_cb.set_value(False)
            self.ui.rest_cb.hide()
            self.ui.forced_rest_iso_cb.hide()

            self.ui.except_cb.set_value(False)
            self.ui.except_cb.hide()

            self.ui.type_excobj_radio.hide()
            self.ui.exc_obj_combo.hide()

            self.ui.select_label.hide()
            self.ui.select_combo.hide()

            self.ui.multi_objects_cb.set_value(False)
            self.ui.multi_objects_cb.hide()

            # make sure that on hide is selected an option that does not display child widgets
            self.ui.select_combo.set_value(0)

            self.ui.reference_combo_type_label.hide()
            self.ui.reference_combo_type.hide()
            self.ui.reference_combo.hide()
            self.ui.poly_int_cb.hide()
            self.ui.sel_all_btn.hide()
            self.ui.clear_all_btn.hide()
            self.ui.area_shape_label.hide()
            self.ui.area_shape_radio.hide()

            # Context Menu section
            self.ui.tools_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            # Add Tool section
            self.ui.add_tool_frame.show()

            # Tool parameters section: the isolation type that each tool had before Beginner
            for tool_uid, isotype in self.advanced_isotype.items():
                if tool_uid in self.iso_tools and isotype is not None:
                    self.iso_tools[tool_uid]['data']['tools_iso_isotype'] = isotype
            self.advanced_isotype.clear()

            self.ui.milling_type_label.show()
            self.ui.milling_type_radio.show()

            self.ui.iso_type_label.show()
            self.ui.iso_type_radio.set_value(self.app.options.get(
                "tools_iso_isotype", app_defaults.get("tools_iso_isotype", AppDefaults.factory_defaults.get("tools_iso_isotype"))))
            self.ui.iso_type_radio.show()

            # All param section
            self.ui.apply_param_to_all.show()

            # Common Parameters
            self.ui.rest_cb.set_value(self.app.options.get(
                "tools_iso_rest", app_defaults.get("tools_iso_rest", AppDefaults.factory_defaults.get("tools_iso_rest"))))
            self.ui.rest_cb.show()
            self.ui.forced_rest_iso_cb.show()

            self.ui.except_cb.set_value(self.app.options.get(
                "tools_iso_isoexcept", app_defaults.get("tools_iso_isoexcept", AppDefaults.factory_defaults.get("tools_iso_isoexcept"))))
            self.ui.except_cb.show()

            self.ui.type_excobj_radio.show()
            self.ui.exc_obj_combo.show()

            self.ui.select_label.show()
            self.ui.select_combo.show()

            self.ui.multi_objects_cb.show()

            # Context Menu section
            self.ui.tools_table.setupContextMenu()

    def rebuild_ui(self):
        # read the table tools uid
        currenuid_list = []
        for row in range(self.ui.tools_table.rowCount()):
            uid = int(self.ui.tools_table.item(row, 3).text())
            currenuid_list.append(uid)

        # the tools in the order of the rows, each with its own uid
        new_tools = {currenuid: self.iso_tools[currenuid] for currenuid in currenuid_list}
        self.iso_tools.clear()
        self.iso_tools.update(new_tools)

        # the user ordered the tools by hand: a sort by diameter would undo it
        # (the order is fixed to Reverse while the rest machining is on)
        if self.ui.iso_order_combo.isEnabled() and self.ui.iso_order_combo.get_value() != 0:
            self.ui_disconnect()
            self.ui.iso_order_combo.set_value(0)  # "Default"
            self.ui_connect()

        # the tools table changed therefore we need to rebuild it
        QtCore.QTimer.singleShot(20, self.build_ui)

    def build_ui(self):
        self.ui_disconnect()

        # updated units
        units = self.app.app_units.upper()
        self.units = units

        self.sort_iso_tools()

        n = len(self.iso_tools)
        self.ui.tools_table.setRowCount(n)
        tool_id = 0

        for tooluid_key, tooluid_value in self.iso_tools.items():
            tool_id += 1

            # Tool name/id
            id_ = QtWidgets.QTableWidgetItem('%d' % int(tool_id))
            id_.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            row_no = tool_id - 1
            self.ui.tools_table.setItem(row_no, 0, id_)

            # Diameter
            truncated_dia = self.app.dec_format(tooluid_value['tooldia'], self.decimals)
            dia = QtWidgets.QTableWidgetItem(str(truncated_dia))
            dia.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.tools_table.setItem(row_no, 1, dia)

            # Tool unique ID
            # REMEMBER: THIS COLUMN IS HIDDEN
            tool_uid_item = QtWidgets.QTableWidgetItem(str(int(tooluid_key)))
            self.ui.tools_table.setItem(row_no, 3, tool_uid_item)

        # make the diameter column editable
        for row in range(tool_id):
            self.ui.tools_table.item(row, 1).setFlags(
                QtCore.Qt.ItemFlag.ItemIsEditable |
                QtCore.Qt.ItemFlag.ItemIsSelectable |
                QtCore.Qt.ItemFlag.ItemIsEnabled)

        # all the tools are selected by default
        self.ui.tools_table.selectColumn(0)
        #
        self.ui.tools_table.resizeColumnsToContents()
        self.ui.tools_table.resizeRowsToContents()

        vertical_header = self.ui.tools_table.verticalHeader()
        vertical_header.hide()
        self.ui.tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

        # self.ui.tools_table.setSortingEnabled(True)
        # sort by tool diameter
        # self.ui.tools_table.sortItems(1)

        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        self.ui_connect()

        # set the text on tool_data_label after loading the object
        sel_rows = set()
        sel_items = self.ui.tools_table.selectedItems()
        for it in sel_items:
            sel_rows.add(it.row())
        if len(sel_rows) > 1:
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def ui_connect(self):
        # connect each signal exactly once, whatever the previous state
        self.ui_disconnect()

        self.ui.tool_shape_combo.currentIndexChanged.connect(self.on_tt_change)

        self.ui.tools_table.itemChanged.connect(self.on_tool_edit)

        # V-shape tool parameters changes
        self.ui.cutz_entry.editingFinished.connect(self.on_update_tool_dia)
        self.ui.tipdia_entry.editingFinished.connect(self.on_update_tool_dia)
        self.ui.tipangle_entry.editingFinished.connect(self.on_update_tool_dia)

        # rows selected
        self.ui.tools_table.clicked.connect(self.on_row_selection_change)
        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_all_rows)

        # Tool Parameters
        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                current_widget.stateChanged.connect(self.form_to_storage)
            if isinstance(current_widget, RadioSet):
                current_widget.activated_custom.connect(self.form_to_storage)
            elif isinstance(current_widget, FCDoubleSpinner) or isinstance(current_widget, FCSpinner):
                current_widget.returnPressed.connect(self.form_to_storage)
            elif isinstance(current_widget, (FCComboBox, FCComboBox2)):
                current_widget.currentIndexChanged.connect(self.form_to_storage)

        self.ui.rest_cb.stateChanged.connect(self.on_rest_machining_check)
        self.ui.iso_order_combo.currentIndexChanged.connect(self.on_order_changed)

    def ui_disconnect(self):

        # only our slots are disconnected: the widgets have receivers of their own
        try:
            self.ui.tool_shape_combo.currentIndexChanged.disconnect(self.on_tt_change)
        except (TypeError, AttributeError, RuntimeError):
            pass

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.tools_table.itemChanged.disconnect(self.on_tool_edit)
        except (TypeError, AttributeError, RuntimeError):
            pass

        # V-shape tool parameters changes
        try:
            # only our slot: the widget has its own editingFinished receiver
            self.ui.cutz_entry.editingFinished.disconnect(self.on_update_tool_dia)
        except (TypeError, AttributeError):
            pass
        try:
            # only our slot: the widget has its own editingFinished receiver
            self.ui.tipdia_entry.editingFinished.disconnect(self.on_update_tool_dia)
        except (TypeError, AttributeError):
            pass
        try:
            # only our slot: the widget has its own editingFinished receiver
            self.ui.tipangle_entry.editingFinished.disconnect(self.on_update_tool_dia)
        except (TypeError, AttributeError):
            pass

        # rows selected
        try:
            self.ui.tools_table.clicked.disconnect(self.on_row_selection_change)
        except (TypeError, AttributeError, RuntimeError):
            pass
        try:
            self.ui.tools_table.horizontalHeader().sectionClicked.disconnect(self.on_toggle_all_rows)
        except (TypeError, AttributeError, RuntimeError):
            pass

        # Tool Parameters
        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                try:
                    current_widget.stateChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            if isinstance(current_widget, RadioSet):
                try:
                    current_widget.activated_custom.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, FCDoubleSpinner) or isinstance(current_widget, FCSpinner):
                try:
                    current_widget.returnPressed.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, (FCComboBox, FCComboBox2)):
                try:
                    current_widget.currentIndexChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass

        try:
            self.ui.rest_cb.stateChanged.disconnect(self.on_rest_machining_check)
        except (TypeError, ValueError, RuntimeError):
            pass
        try:
            self.ui.iso_order_combo.currentIndexChanged.disconnect(self.on_order_changed)
        except (TypeError, ValueError, RuntimeError):
            pass

    def sort_iso_tools(self):
        order = self.ui.iso_order_combo.get_value()
        if order == 0:  # "Default"
            return

        # sort the tools dictionary having the 'tooldia' as sorting key
        new_tools_list = []
        if order == 1:  # "Forward"
            new_tools_list = deepcopy_keep_geometry(sorted(self.iso_tools.items(), key=lambda x: x[1]['tooldia'], reverse=False))
        elif order == 2:  # "Reverse"
            new_tools_list = deepcopy_keep_geometry(sorted(self.iso_tools.items(), key=lambda x: x[1]['tooldia'], reverse=True))

        # recreate the tools dictionary in a ordered fashion; each tool keeps its uid
        self.iso_tools.clear()
        self.iso_tools.update(new_tools_list)

    def on_tt_change(self):
        tool_type = self.ui.tool_shape_combo.get_value()
        self.ui_update_v_shape(tool_type)
        self.form_to_storage()

    def ui_update_v_shape(self, tool_type_txt, update_dia=True):
        """
        Shows the V-Tip parameters when the tool shape is V, else hides them.

        :param tool_type_txt:   the tool shape as in the Tool Shape combobox
        :param update_dia:      if True, the diameter of the selected V tools is recomputed
        """
        if tool_type_txt == self.V_SHAPE_INDEX:  # 'V'
            self.ui.v_frame.show()
            if update_dia:
                self.on_update_tool_dia()
            return
        self.ui.v_frame.hide()

    V_SHAPE_INDEX = 5

    @staticmethod
    def is_v_shape_data(data):
        return data.get('tools_mill_tool_shape') == ToolIsolation.V_SHAPE_INDEX

    @staticmethod
    def v_effective_diameter(data):
        """
        Effective diameter of a V-shaped tool: tip + 2 * |cut_z| * tan(angle / 2).

        :param data: tool data dict (tools_mill_vtipdia, tools_mill_vtipangle, tools_mill_cutz)
        :return: the diameter or None if the parameters are missing or not usable
        """
        return IsolationGen.v_effective_diameter(data)

    def on_update_tool_dia(self):
        if not self.ui.v_frame.isVisible():
            return

        # the widget that was edited: only its value is stored in all the selected tools, the other V
        # parameters of a tool are its own
        v_entries = {
            self.ui.tipdia_entry:   'tools_mill_vtipdia',
            self.ui.tipangle_entry: 'tools_mill_vtipangle',
            self.ui.cutz_entry:     'tools_mill_cutz',
        }
        sender = self.sender()
        edited_key = v_entries.get(sender) if isinstance(sender, QtCore.QObject) else None

        rows = sorted({item.row() for item in self.ui.tools_table.selectedItems()})
        current_row = self.ui.tools_table.currentRow()
        if not rows:
            rows = [current_row]

        self.ui_disconnect()
        try:
            tools = []
            for row in rows:
                tool_uid_item = self.ui.tools_table.item(row, 3)
                if tool_uid_item is None:
                    continue
                tool_uid = int(tool_uid_item.text())
                if tool_uid not in self.iso_tools:
                    continue
                tools.append((row, tool_uid))

            widget_values = {
                'tools_mill_vtipdia':   self.ui.tipdia_entry.get_value(),
                'tools_mill_vtipangle': self.ui.tipangle_entry.get_value(),
                'tools_mill_cutz':      self.ui.cutz_entry.get_value(),
            }

            # the edited values go to copies first: a rejected diameter must not leave the tool data changed
            candidates = []
            for row, tool_uid in tools:
                data = dict(self.iso_tools[tool_uid]['data'])
                if edited_key is not None:
                    data[edited_key] = widget_values[edited_key]
                elif row == current_row or len(tools) == 1:
                    data.update(widget_values)
                candidates.append((row, tool_uid, data))

            # the tools outside of this change keep their diameter: a new diameter can not be one of them
            updated_uids = {tool_uid for __, tool_uid in tools}
            other_dias = [
                self.app.dec_format(v['tooldia'], self.decimals)
                for uid, v in self.iso_tools.items() if 'tooldia' in v and uid not in updated_uids]
            warned = False
            for row, tool_uid, data in candidates:
                tool = self.iso_tools[tool_uid]
                new_tooldia = self.v_effective_diameter(data)
                if new_tooldia is None:
                    tool['data'] = data
                    continue
                f_new_tool_dia = self.app.dec_format(new_tooldia, self.decimals)

                current_dia = tool.get('tooldia')
                if current_dia is not None and f_new_tool_dia == self.app.dec_format(current_dia, self.decimals):
                    tool['data'] = data
                    continue

                if f_new_tool_dia in other_dias:
                    # we do not want duplicates: keep the old value
                    if not warned:
                        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Tool already in Tool Table."))
                        warned = True
                    continue

                tool['data'] = data
                tool['tooldia'] = deepcopy(f_new_tool_dia)
                tool['data']['tools_iso_tooldia'] = deepcopy(f_new_tool_dia)
                tool_dia_item = self.ui.tools_table.item(row, 1)
                if tool_dia_item is not None:
                    tool_dia_item.setText(str(f_new_tool_dia))
        finally:
            self.ui_connect()

    def on_toggle_all_rows(self):
        """
        will toggle the selection of all rows in Tools table

        :return:
        """
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if len(sel_rows) == self.ui.tools_table.rowCount():
            self.ui.tools_table.clearSelection()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
        else:
            self.ui.tools_table.selectAll()
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def on_row_selection_change(self):
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        # update UI only if only one row is selected otherwise having multiple rows selected will deform information
        # for the rows other that the current one (first selected)
        if len(sel_rows) == 1:
            self.update_ui()

    def update_ui(self):
        self.ui_disconnect()

        sel_rows = set()
        table_items = self.ui.tools_table.selectedItems()
        if table_items:
            for it in table_items:
                sel_rows.add(it.row())
            # sel_rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))

        if not sel_rows or len(sel_rows) == 0:
            self.ui.generate_iso_button.setDisabled(True)
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
            self.ui_connect()
            return
        else:
            self.ui.generate_iso_button.setDisabled(False)

        for current_row in sel_rows:
            # populate the form with the data from the tool associated with the row parameter
            try:
                item = self.ui.tools_table.item(current_row, 3)
                if item is not None:
                    tooluid = int(item.text())
                else:
                    self.ui_connect()
                    return
            except Exception as e:
                self.app.log.error("Tool missing. Add a tool in the Tool Table. %s" % str(e))
                self.ui_connect()
                return

            # update the QLabel that shows for which Tool we have the parameters in the UI form
            if len(sel_rows) == 1:
                cr = current_row + 1
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), cr)
                )
                try:
                    # set the form with data from the newly selected tool
                    for tooluid_key, tooluid_value in list(self.iso_tools.items()):
                        if int(tooluid_key) == tooluid:
                            for key, value in tooluid_value.items():
                                if key == 'data':
                                    self.storage_to_form(tooluid_value['data'])
                    # the signals are disconnected: show or hide the V parameters for the shape of this tool
                    self.ui_update_v_shape(self.ui.tool_shape_combo.get_value(), update_dia=False)
                except Exception as e:
                    self.app.log.error("ToolIsolation ---> update_ui() " + str(e))
            else:
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
                )

        self.ui_connect()

    def storage_to_form(self, dict_storage):
        for form_key in self.form_fields:
            if form_key in dict_storage:
                try:
                    self.form_fields[form_key].set_value(dict_storage[form_key])
                except Exception as e:
                    self.app.log.error("ToolIsolation.storage_to_form() --> %s" % str(e))
                    pass
            elif form_key in ('tools_iso_strategy', 'tools_iso_voronoi_spacing', 'tools_iso_voronoi_pads'):
                # an old saved tool has no strategy: it is an Offset tool
                self.form_fields[form_key].set_value(AppDefaults.factory_defaults.get(form_key))

    def form_to_storage(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table so we can't save the GUI elements values to storage
            return

        widget_changed = self.sender()
        wdg_objname = widget_changed.objectName()
        option_changed = self.name2option[wdg_objname]

        # the V parameters decide the diameter of each V tool: on_update_tool_dia() writes them, and only when the
        # new diameter is accepted
        if option_changed in ('tools_mill_vtipdia', 'tools_mill_vtipangle', 'tools_mill_cutz') \
                and self.ui.v_frame.isVisible():
            self.on_update_tool_dia()
            return

        self.blockSignals(True)

        # row = self.ui.tools_table.currentRow()
        rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))
        for row in rows:
            if row < 0:
                row = 0
            tooluid_item = int(self.ui.tools_table.item(row, 3).text())

            for tooluid_key, tooluid_val in self.iso_tools.items():
                if int(tooluid_key) == tooluid_item:
                    new_option_value = self.form_fields[option_changed].get_value()
                    if option_changed in tooluid_val:
                        tooluid_val[option_changed] = new_option_value
                    if option_changed in tooluid_val['data'] or option_changed in (
                            'tools_iso_strategy', 'tools_iso_voronoi_spacing', 'tools_iso_voronoi_pads'):
                        tooluid_val['data'][option_changed] = new_option_value

        self.blockSignals(False)

        # the V-shape parameters decide the diameter of each V tool
        if option_changed in ('tools_mill_tool_shape', 'tools_mill_vtipdia', 'tools_mill_vtipangle',
                              'tools_mill_cutz') and self.ui.tool_shape_combo.get_value() == self.V_SHAPE_INDEX:
            self.on_update_tool_dia()

    def on_apply_param_to_all_clicked(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table so we can't save the GUI elements values to storage
            self.app.log.debug("ToolIsolation.on_apply_param_to_all_clicked() --> no tool in Tools Table, aborting.")
            return

        self.blockSignals(True)

        # the source: the current row when it is selected, else the first selected row, else the current row
        sel_rows = sorted({item.row() for item in self.ui.tools_table.selectedItems()})
        row = self.ui.tools_table.currentRow()
        if sel_rows and row not in sel_rows:
            row = sel_rows[0]
        if row < 0:
            row = 0

        tooluid_item = int(self.ui.tools_table.item(row, 3).text())
        if tooluid_item not in self.iso_tools:
            self.blockSignals(False)
            return
        # the 'data' of the tool that corresponds to the source row in the tool table
        temp_tool_data = self.iso_tools[tooluid_item]['data']

        v_changed = False
        for tooluid_key, tooluid_val in self.iso_tools.items():
            if int(tooluid_key) == tooluid_item:
                continue
            own_dia = tooluid_val['tooldia']
            tooluid_val['data'] = deepcopy(temp_tool_data)
            if self.is_v_shape_data(tooluid_val['data']):
                # the diameter of a V tool comes from its V parameters
                v_dia = self.v_effective_diameter(tooluid_val['data'])
                if v_dia is not None and v_dia > 0:
                    own_dia = self.app.dec_format(v_dia, self.decimals)
                    v_changed = v_changed or own_dia != tooluid_val['tooldia']
                    tooluid_val['tooldia'] = own_dia
            # the other tools keep their own diameter
            tooluid_val['data']['tools_iso_tooldia'] = deepcopy(own_dia)

        if v_changed:
            self.build_ui()

        self.app.inform.emit('[success] %s' % _("Current Tool parameters were applied to all tools."))
        self.blockSignals(False)

    def on_add_tool_by_key(self):
        # tool_add_popup = FCInputDialog(title='%s...' % _("New Tool"),
        #                                text='%s:' % _('Enter a Tool Diameter'),
        #                                min=0.0001, max=10000.0000, decimals=self.decimals)
        btn_icon = QtGui.QIcon(self.app.resource_location + '/open_excellon32.png')

        tool_add_popup = FCInputDialogSpinnerButton(title='%s...' % _("New Tool"),
                                                    text='%s:' % _('Enter a Tool Diameter'),
                                                    min=-10000.0000, max=10000.0000, decimals=self.decimals,
                                                    button_icon=btn_icon,
                                                    callback=self.on_find_optimal_tooldia,
                                                    parent=self.app.ui)
        tool_add_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/letter_t_32.png'))

        def find_optimal(valor):
            tool_add_popup.set_value(float(valor))

        self.optimal_found_sig.connect(find_optimal)

        val, ok = tool_add_popup.get_results()
        if ok:
            if float(val) == 0:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Please enter a tool diameter with non-zero value, in Float format."))
                self.optimal_found_sig.disconnect(find_optimal)
                return
            self.on_tool_add(custom_dia=float(val))
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _("Adding Tool cancelled"))
        self.optimal_found_sig.disconnect(find_optimal)

    def on_reference_combo_changed(self):
        obj_type = self.ui.reference_combo_type.currentIndex()
        self.ui.reference_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.reference_combo.setCurrentIndex(0)
        self.ui.reference_combo.obj_type = {0: "Gerber", 1: "Excellon", 2: "Geometry"}[obj_type]

    def on_toggle_reference(self):
        val = self.ui.select_combo.get_value()

        if val == 0:  # ALl
            self.ui.reference_combo.hide()
            self.ui.reference_combo_type.hide()
            self.ui.reference_combo_type_label.hide()
            self.ui.area_shape_label.hide()
            self.ui.area_shape_radio.hide()

            self.ui.poly_int_cb.hide()
            self.ui.sel_all_btn.hide()
            self.ui.clear_all_btn.hide()

            # rest-machining is allowed
            self.ui.rest_cb.setDisabled(False)
            # the Gerber objects selected in the Project tab can be isolated in one run
            self.ui.multi_objects_cb.setDisabled(False)
        elif val == 1:  # Area Selection
            self.ui.reference_combo.hide()
            self.ui.reference_combo_type.hide()
            self.ui.reference_combo_type_label.hide()
            self.ui.area_shape_label.show()
            self.ui.area_shape_radio.show()

            self.ui.poly_int_cb.hide()
            self.ui.sel_all_btn.hide()
            self.ui.clear_all_btn.hide()

            # disable rest-machining for area isolation
            self.ui.rest_cb.set_value(False)
            self.ui.rest_cb.setDisabled(True)
            # one object only
            self.ui.multi_objects_cb.setDisabled(True)
        elif val == 2:  # Polygon Selection
            self.ui.reference_combo.hide()
            self.ui.reference_combo_type.hide()
            self.ui.reference_combo_type_label.hide()
            self.ui.area_shape_label.hide()
            self.ui.area_shape_radio.hide()

            self.ui.poly_int_cb.show()
            self.ui.sel_all_btn.show()
            self.ui.clear_all_btn.show()

            # the isolation of the selected polygons can use rest machining, but with one object only
            self.ui.rest_cb.setDisabled(False)
            self.ui.multi_objects_cb.setDisabled(True)

        else:  # Reference Object
            self.ui.reference_combo.show()
            self.ui.reference_combo_type.show()
            self.ui.reference_combo_type_label.show()
            self.ui.area_shape_label.hide()
            self.ui.area_shape_radio.hide()

            self.ui.poly_int_cb.hide()
            self.ui.sel_all_btn.hide()
            self.ui.clear_all_btn.hide()

            # rest-machining is allowed
            self.ui.rest_cb.setDisabled(False)
            self.ui.multi_objects_cb.setDisabled(False)

    def on_order_changed(self):
        self.build_ui()

    def on_rest_machining_check(self, state):
        if state:
            self.ui.iso_order_combo.set_value(2)  # "Reverse"
            self.ui.order_label.setDisabled(True)
            self.ui.iso_order_combo.setDisabled(True)

            # the checkbox is disabled when it is already forced: its value is not the one of the user
            if self.ui.combine_passes_cb.isEnabled():
                self.old_combine_state = self.ui.combine_passes_cb.get_value()
            self.ui.combine_passes_cb.set_value(True)
            self.ui.combine_passes_cb.setDisabled(True)

            self.ui.forced_rest_iso_cb.setDisabled(False)
        else:
            self.ui.order_label.setDisabled(False)
            self.ui.iso_order_combo.setDisabled(False)

            self.ui.combine_passes_cb.set_value(self.old_combine_state)
            self.ui.combine_passes_cb.setDisabled(False)

            self.ui.forced_rest_iso_cb.setDisabled(True)

    def on_find_optimal_tooldia(self):
        self.find_safe_tooldia_worker()

    @staticmethod
    def find_optim_mp(aperture_storage, decimals, abort=None):
        msg = 'ok'
        total_geo = IsolationGen.aperture_geometry(aperture_storage, abort=abort)

        if len(total_geo) <= 1:
            msg = ('[ERROR_NOTCL] %s' % _("Too few polygons in the Gerber object to determine distances."))
            return msg, np.inf

        min_dist = IsolationGen.safe_tooldia(total_geo, decimals)
        min_dist = round(min_dist, decimals)
        if min_dist <= 0:
            return '[ERROR_NOTCL] %s' % _("Could not find a safe tool diameter."), np.inf

        return msg, min_dist

    def validation_inputs(self, fcobj=None):
        """
        The part of the tool validation that reads the GUI: run it in the GUI thread.

        :return:    (the Gerber object, {the uid of each selected tool: its diameter}) or None (the error is reported)
        """
        tools_available = self.ui.tools_table.rowCount()
        if tools_available == 0:
            self.app.inform.emit(f'[ERROR_NOTCL] {_("There are no tools in the Tool Table.")}')
            return None
        if tools_available > 1:
            sel_table_items = self.ui.tools_table.selectedItems()
            if not sel_table_items:
                self.app.inform.emit(f'[ERROR_NOTCL] {_("There are no tools selected in the Tool Table.")}')
                return None
            sel_rows = {t.row() for t in sel_table_items}
            if not sel_rows:
                self.app.inform.emit(f'[ERROR_NOTCL] {_("There are no tools selected in the Tool Table.")}')
                return None
        else:
            sel_rows = {0}

        self.units = self.app.app_units.upper()

        # Get source object.
        if fcobj is None:
            obj_name = self.ui.object_combo.currentText()
            try:
                fcobj = self.app.collection.get_by_name(obj_name)
                if fcobj is None:
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(obj_name)))
                    return None
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
                return None

        # the selected tool ID's and their diameters; the table diameter of a V-shape tool is its effective
        # diameter (on_update_tool_dia() keeps it), the same one that the isolation uses
        tool_dias = {}
        for row in sel_rows:
            tid = int(self.ui.tools_table.item(row, 3).text())
            tool_dias[tid] = float(self.iso_tools[tid]['tooldia'])
        return fcobj, tool_dias

    def check_tools_validity(self, fcobj, tool_dias):
        """
        Checks that the tools fit in the smallest gap between the copper features of the Gerber object and sets
        self.validation_status. It runs in the calling (worker) thread, and it finishes before it returns.

        :param fcobj:       the Gerber object
        :param tool_dias:   {tool uid: tool diameter}
        :return:            True if all the tools are valid
        :raises grace:      if the user aborts
        """
        self.app.inform.emit(_("Checking tools for validity."))

        def check_abort():
            if self.app.abort_flag:
                raise grace

        try:
            msg, min_dist = self.find_optim_mp(fcobj.tools, self.decimals, abort=check_abort)
            if msg != 'ok':
                self.validation_status = False
                self.app.inform.emit(msg)
                return False

            self.safe_tooldia = self.app.dec_format(float(min_dist), self.decimals)

            # check if the tools diameters are less then the safe tool diameter
            for tool_dia in tool_dias.values():
                if tool_dia > self.safe_tooldia:
                    msg = _("Incomplete isolation. At least one tool could not do a complete isolation.")
                    self.app.log.warning(msg)
                    self.app.inform.emit(f'[WARNING] {_("Incomplete isolation.")}')
                    self.validation_status = False
                    return False
        except grace:
            raise
        except Exception as e:
            self.app.log.error(str(e))
            self.validation_status = False
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Tool validation failed"), str(e)))
            return False
        finally:
            # reset the value to prepare for another isolation
            self.safe_tooldia = None

        self.app.inform.emit(_("Tool validation passed."))
        return True

    def find_safe_tooldia_multiprocessing(self):
        """
        The Check validity step alone: runs check_tools_validity() in a worker task. (The distances are measured in
        the worker thread, there is no process pool.)
        """
        validation = self.validation_inputs()
        if validation is None:
            return

        def job_thread(app_obj):
            with self.app.proc_container.new(_("Checking ...")):
                try:
                    self.check_tools_validity(*validation)
                except grace:
                    app_obj.inform.emit('[WARNING_NOTCL] %s' % _("Tool validation aborted."))

        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

    def find_safe_tooldia_worker(self):
        self.safe_tooldia = None
        self.app.inform.emit(_("Checking tools for validity."))
        self.units = self.app.app_units.upper()

        obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            fcobj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            return

        if fcobj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(obj_name)))
            return

        def job_thread(app_obj):
            with self.app.proc_container.new(_("Checking ...")):
                try:
                    app_obj.proc_container.update_view_text(' %d%%' % 0)
                    def check_abort():
                        if self.app.abort_flag:
                            raise grace

                    total_geo = IsolationGen.aperture_geometry(fcobj.tools, abort=check_abort)

                    geo_len = len(total_geo)
                    if geo_len <= 1:
                        msg = _("The Gerber object has one Polygon as geometry.\n"
                                "There are no distances between geometry elements to be found.")
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % msg)
                        return 'fail'

                    # the distances are rounded to the decimals, so the smallest one can be larger than the real gap;
                    # use the same safe value as the tool validation (find_optim_mp())
                    min_dist = IsolationGen.safe_tooldia(total_geo, self.decimals)
                    app_obj.proc_container.update_view_text(' %d%%' % 100)

                    min_dist_truncated = self.app.dec_format(float(min_dist), self.decimals)
                    if min_dist_truncated <= 0:
                        # the smallest gap is below the resolution of the decimals
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Could not find a safe tool diameter."))
                        return 'fail'
                    self.safe_tooldia = min_dist_truncated

                    self.optimal_found_sig.emit(min_dist_truncated)

                    app_obj.inform.emit('[success] %s: %s %s' %
                                        (_("Optimal tool diameter found"), str(min_dist_truncated),
                                         self.units.lower()))
                except grace:
                    app_obj.inform.emit('[WARNING_NOTCL] %s' % _("Aborted."))
                    return 'fail'
                except Exception as ee:
                    self.app.log.error(str(ee))
                    app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not find a safe tool diameter."), str(ee)))
                    return 'fail'

        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

    def tool_dia_in_table(self, tool_dia, except_uid=None):
        """
        :param tool_dia:    a tool diameter
        :param except_uid:  the uid of a tool that is not compared
        :return:            True if another tool of the Tool Table has this diameter
        """
        new_dia = self.app.dec_format(tool_dia, self.decimals)
        return any(
            self.app.dec_format(tool['tooldia'], self.decimals) == new_dia
            for uid, tool in self.iso_tools.items() if 'tooldia' in tool and uid != except_uid)

    def on_tool_add(self, custom_dia=None):
        self.ui_disconnect()

        filename = self.app.tools_database_path()

        tool_dia = custom_dia if custom_dia is not None else self.ui.new_tooldia_entry.get_value()
        # construct a list of all 'tooluid' in the self.iso_tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.iso_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        tooluid = int(max_uid) + 1

        new_tools_dict = deepcopy(self.default_data)
        updated_tooldia = None

        # determine the new tool diameter
        derive_v_dia = tool_dia is None or tool_dia == 0
        if derive_v_dia and self.is_v_shape_data(new_tools_dict):
            # 1. provisional diameter from the V parameters of the defaults
            provisional_dia = self.v_effective_diameter(new_tools_dict)
            if provisional_dia is not None and provisional_dia > 0:
                tool_dia = provisional_dia
        if tool_dia is None or tool_dia == 0:
            self.build_ui()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            self.ui_connect()
            return
        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)

        # if new tool diameter already in the Tool List then abort
        if ToolIsolation.tool_dia_in_table(self, truncated_tooldia):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Tool already in Tool Table."))
            self.ui_connect()
            return

        try:
            # Preserve the former module's database monkeypatch target through the package export.
            tools_db_dict = sys.modules[__package__].load_tools_database(
                filename, self.app.options, target=3, diameter=truncated_tooldia,
                units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))
            self.ui_connect()
            self.on_tool_default_add(dia=tool_dia, v_derived=derive_v_dia)
            return

        tool_found = len(tools_db_dict)

        # look in database tools
        for db_tool, db_tool_val in tools_db_dict.items():
            db_tooldia = db_tool_val['tooldia']
            updated_tooldia = db_tooldia if db_tooldia != truncated_tooldia else None
            for d in db_tool_val['data']:
                if not d.startswith('tools_') or d.startswith(('tools_iso_', 'tools_mill_')):
                    new_tools_dict[d] = deepcopy(db_tool_val['data'][d])

        # test we found a suitable tool in Tools Database or if multiple ones
        if tool_found == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tool not in Tools Database. Adding a default tool."))
            self.on_tool_default_add(dia=tool_dia, v_derived=derive_v_dia)
            return

        if tool_found > 1:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Cancelled.\n"
                                         "Multiple tools for one tool diameter found in Tools Database."))
            self.ui_connect()
            return

        new_tdia = deepcopy(updated_tooldia) if updated_tooldia is not None else deepcopy(truncated_tooldia)
        if derive_v_dia and self.is_v_shape_data(new_tools_dict):
            # 4. + 5. the final (merged) data decides the diameter; no 2nd Tools DB lookup
            final_dia = self.v_effective_diameter(new_tools_dict)
            if final_dia is not None and final_dia > 0:
                new_tdia = self.app.dec_format(final_dia, self.decimals)
                new_tools_dict['tools_iso_tooldia'] = deepcopy(new_tdia)

        # if the diameter of the tool found in Tools Database is already in the Tool List then abort
        if ToolIsolation.tool_dia_in_table(self, new_tdia):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Tool already in Tool Table."))
            self.ui_connect()
            return

        self.iso_tools.update({
            tooluid: {
                'tooldia':          new_tdia,
                'data':             deepcopy(new_tools_dict),
                'solid_geometry':   []
            }
        })
        self.tooluid = tooluid
        self.build_ui()

        # select the tool just added: the uid of a tool does not change when the table is sorted
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break

        # update the UI form
        self.update_ui()

        self.app.inform.emit('[success] %s' % _("New tool added to Tool Table from Tools Database."))

    def on_tool_default_add(self, dia=None, muted=None, v_derived=False):
        self.ui_disconnect()

        tool_dia = dia if dia is not None else self.ui.new_tooldia_entry.get_value()

        new_data = deepcopy(self.default_data)
        derive_v_dia = v_derived or tool_dia is None or tool_dia == 0
        if (tool_dia is None or tool_dia == 0) and self.is_v_shape_data(new_data):
            v_dia = self.v_effective_diameter(new_data)
            if v_dia is not None and v_dia > 0:
                tool_dia = v_dia

        if tool_dia is None or tool_dia == 0:
            self.build_ui()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            return

        # construct a list of all 'tooluid' in the self.iso_tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.iso_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        self.tooluid = int(max_uid + 1)

        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)
        if self.tool_dia_in_table(truncated_tooldia):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Tool already in Tool Table."))
            self.ui_connect()
            return

        if derive_v_dia and self.is_v_shape_data(new_data):
            new_data['tools_iso_tooldia'] = deepcopy(truncated_tooldia)
        self.iso_tools.update({
            int(self.tooluid): {
                'tooldia':          truncated_tooldia,
                'data':             new_data,
                'solid_geometry':   []
            }
        })
        # print("after", self.iso_tools)

        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == self.tooluid:
                self.ui.tools_table.selectRow(row)
                break

        # update the UI form
        self.update_ui()

        if muted is None:
            self.app.inform.emit('[success] %s' % _("Default tool added to Tool Table."))

    def on_tool_edit(self, item):
        self.ui_disconnect()

        edited_row = item.row()
        uid_item = self.ui.tools_table.item(edited_row, 3)
        dia_item = self.ui.tools_table.item(edited_row, 1)
        if item.column() != 1 or uid_item is None or dia_item is None or int(uid_item.text()) not in self.iso_tools:
            self.ui_connect()
            return
        editeduid = int(uid_item.text())

        try:
            new_tool_dia = float(dia_item.text())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                new_tool_dia = float(dia_item.text().replace(',', '.'))
            except ValueError:
                self.app.inform.emit('[ERROR_NOTCL]  %s' % _("Wrong value format entered, use a number."))
                # restore the old diameter in the table
                self.build_ui()
                return

        # the diameter can be negative (isolation inside the features) but not zero
        if new_tool_dia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            self.build_ui()
            return

        f_new_tool_dia = self.app.dec_format(new_tool_dia, self.decimals)
        if self.tool_dia_in_table(f_new_tool_dia, except_uid=editeduid):
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Cancelled. New diameter value is already in the Tool Table."))
            self.build_ui()
            return

        self.iso_tools[editeduid]['tooldia'] = deepcopy(f_new_tool_dia)
        self.iso_tools[editeduid]['data']['tools_iso_tooldia'] = deepcopy(f_new_tool_dia)

        self.app.inform.emit('[success] %s' % _("Tool from Tool Table was edited."))
        self.build_ui()

    def on_tool_delete(self, rows_to_delete=None, all_tools=None):
        """
        Will delete a tool in the tool table

        :param rows_to_delete:      which rows to delete; can be a list
        :param all_tools:           delete all tools in the tool table
        :return:
        """
        self.ui_disconnect()

        deleted_tools_list = []

        if all_tools:
            self.iso_tools.clear()
            self.advanced_isotype.clear()
            self.build_ui()
            self.update_ui()
            return

        if rows_to_delete:
            try:
                for row in rows_to_delete:
                    tooluid_del = int(self.ui.tools_table.item(row, 3).text())
                    deleted_tools_list.append(tooluid_del)
            except TypeError:
                tooluid_del = int(self.ui.tools_table.item(rows_to_delete, 3).text())
                deleted_tools_list.append(tooluid_del)

            for t in deleted_tools_list:
                self.iso_tools.pop(t, None)
                self.advanced_isotype.pop(t, None)

            self.build_ui()
            self.update_ui()
            return

        try:
            if self.ui.tools_table.selectedItems():
                for row_sel in self.ui.tools_table.selectedItems():
                    row = row_sel.row()
                    if row < 0:
                        continue
                    tooluid_del = int(self.ui.tools_table.item(row, 3).text())
                    if tooluid_del not in deleted_tools_list:
                        deleted_tools_list.append(tooluid_del)

                for t in deleted_tools_list:
                    self.iso_tools.pop(t, None)
                    self.advanced_isotype.pop(t, None)

        except AttributeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
            self.ui_connect()
            return
        except Exception as e:
            self.app.log.error(str(e))

        if not deleted_tools_list:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
            self.ui_connect()
            return

        self.app.inform.emit('[success] %s' % _("Tools deleted from Tool Table."))
        self.build_ui()
        # no tool selected after the delete: the Generate button is disabled
        self.update_ui()

    def on_generate_buffer(self):
        self.app.inform.emit('[WARNING_NOTCL] %s...' % _("Buffering solid geometry"))

        self.obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            self.grb_obj = self.app.collection.get_by_name(self.obj_name)
        except Exception as e:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(self.obj_name)))
            return "Could not retrieve object: %s with error: %s" % (self.obj_name, str(e))

        if self.grb_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(self.obj_name)))
            return

        def buffer_task(app_obj):
            with app_obj.proc_container.new('%s...' % _("Buffering")):
                with history_of(app_obj).snapshot(_("Buffer"), [self.grb_obj]):
                    solid_geometry = self.grb_obj.solid_geometry
                    if isinstance(solid_geometry, list):
                        # the list can hold MultiPolygons (and nested lists)
                        solid_geometry = unary_union(flatten_shapely_geometry(solid_geometry))

                    self.grb_obj.solid_geometry = solid_geometry.buffer(0.0000001)
                    self.grb_obj.solid_geometry = self.grb_obj.solid_geometry.buffer(-0.0000001)
                app_obj.inform.emit('[success] %s' % _("Done."))
                self.grb_obj.plot_single_object.emit()

        self.app.worker_task.emit({'fcn': buffer_task, 'params': [self.app]})

    def on_iso_button_click(self):
        use_validation = self.ui.valid_cb.get_value()
        # assume that the validation is OK
        self.validation_status = True

        # the Gerber objects selected in the Project tab, when asked and when the isolation is of the whole object
        selected_objs = []
        if self.ui.multi_objects_cb.get_value() and self.ui.select_combo.get_value() in (0, 3):
            selected_objs = [obj for obj in self.app.collection.get_selected() if obj.kind == 'gerber']

        self.obj_name = self.ui.object_combo.currentText()
        if selected_objs:
            self.grb_obj = selected_objs[0]
            self.obj_name = self.grb_obj.obj_options['name']
        else:
            # Get source object.
            try:
                self.grb_obj = self.app.collection.get_by_name(self.obj_name)
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(self.obj_name)))
                return

            if self.grb_obj is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(self.obj_name)))
                return
        isolated_objs = selected_objs if selected_objs else [self.grb_obj]

        # a selection mode that is still open is ended first: no double connected handlers, no old selection
        self.end_selection_mode()

        # everything that reads the widgets is read here, in the GUI thread: the worker gets plain values
        sel_tools = self._selected_tool_ids()
        if not sel_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
            return

        # the check reads the tool table: collect its inputs here, in the GUI thread
        validation = self.validation_inputs(isolated_objs[0]) if use_validation is True else None

        selection = self.ui.select_combo.get_value()
        if selection in (1, 2):
            # Area / Polygon Selection: the mode only connects canvas handlers, so it starts here
            self.grb_obj = isolated_objs[0]
            # the check runs when the selection is finished, in the same task as the isolation that reads its result
            self.pending_validation = (isolated_objs[0], validation[1]) if validation is not None else None
            self.start_selection_mode(self.grb_obj, selection)
            return

        # the selected tools are copied here, in the GUI thread: a later edit of the Tool Table does not change
        # the run that was submitted
        run_tools = tools_for_run(self.iso_tools, sel_tools)

        ref_name = self.ui.reference_combo.get_value() if selection == 3 else None
        settings = self.run_settings()
        if settings['boundary'] == 'fail':
            return

        def worker_task(iso_class):
            with iso_class.app.proc_container.new('%s ...' % _("Isolating")):
                try:
                    for isolated_obj in isolated_objs:
                        iso_class.grb_obj = isolated_obj
                        # each object has its own check: the isolation reads its result
                        iso_class.validation_status = True
                        if validation is not None:
                            iso_class.check_tools_validity(isolated_obj, validation[1])
                        iso_class.isolate_handler(isolated_obj, selection, sel_tools, settings, ref_name,
                                                  tools_storage=run_tools)
                except grace:
                    iso_class.app.inform.emit('[WARNING_NOTCL] %s' % _("Isolation aborted."))

        self.app.worker_task.emit({'fcn': worker_task, 'params': [self]})

    def run_settings(self):
        """
        Reads the widgets that a run needs. It runs in the GUI thread: a worker must not read the widgets.

        :return:    the keyword arguments of isolate() (the boundary area included: None, a polygon or 'fail')
        """
        self._voronoi_diagnostic_token = object()
        self.voronoi_diagnostic_sig.emit({'action': 'clear', 'token': self._voronoi_diagnostic_token})
        return {
            'diagnostic_token': self._voronoi_diagnostic_token,
            'combine': self.ui.combine_passes_cb.get_value(),
            'iso_except': self.ui.except_cb.get_value(),
            'e_passes': self.ui.pad_passes_entry.get_value(),
            'area_except': self.ui.except_cb.get_value(),
            'area_shape': self.ui.area_shape_radio.get_value(),
            'rest': self.ui.rest_cb.get_value(),
            'sel_type': self.ui.select_combo.get_value(),
            'tip_shape': self.ui.tool_shape_combo.get_value(),
            'iso_simplification': self.ui.simplify_cb.get_value(),
            'simplification_tol': self.ui.sim_tol_entry.get_value(),
            'boundary': self.get_boundary_area(),
            'trace_boundary': self.ui.trace_boundary_cb.get_value(),
            'forced_rest': self.ui.forced_rest_iso_cb.get_value(),
            'order': self.ui.iso_order_combo.get_value(),
            'clear_leftovers': self.ui.clear_leftovers_cb.get_value(),
            'exc_name': self.ui.exc_obj_combo.currentText(),
        }

    def isolate_handler(self, isolated_obj, selection, sel_tools, settings, ref_name=None, tools_storage=None):
        """
        Creates a geometry object with paths around the gerber features (selection 0: All, 3: Reference Object).
        It runs in the worker and it reads no widget: the values come from the GUI thread.

        :param isolated_obj:    Gerber object for which to generate the isolating routing geometry
        :type isolated_obj:     AppObjects.FlatCAMGerber.GerberObject
        :param selection:       the selection mode
        :param sel_tools:       the uids of the selected tools
        :param settings:        run_settings()
        :param ref_name:        the name of the reference object (selection 3)
        :param tools_storage:   the tools copied when the run was submitted (None: the plugin tools)
        :type tools_storage:    dict
        :return: None
        """
        if tools_storage is None:
            tools_storage = self.iso_tools

        if selection == 0:  # ALL
            self.isolate(isolated_obj=isolated_obj, sel_tools=sel_tools, tools_storage=tools_storage, **settings)
        elif selection == 3:  # Reference Object
            ref_obj = self.app.collection.get_by_name(ref_name)
            if ref_obj is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(ref_name)))
                return 'fail'
            ref_geo = unary_union(ref_obj.solid_geometry)
            use_geo = unary_union(isolated_obj.solid_geometry).difference(ref_geo)
            self.isolate(isolated_obj=isolated_obj, geometry=use_geo, sel_tools=sel_tools,
                         tools_storage=tools_storage, **settings)

    def start_selection_mode(self, isolated_obj, selection):
        """Starts the Area (1) or Polygon (2) selection mode: canvas handlers, so it runs in the GUI thread."""
        # a mode that is still open is ended first: no double connected handlers, no old selection
        self.end_selection_mode()

        if selection == 1:  # Area Selection
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))

            self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

            self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_release)
            self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)
            self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)

            # disconnect flags
            self.area_sel_disconnect_flag = True

        else:  # Polygon Selection
            # disengage the grid snapping since it may be hard to click on polygons with grid snapping on
            # (the state is stored once per session: end_selection_mode() restores it)
            self.grid_status_memory = self.app.ui.grid_snap_btn.isChecked()
            if self.grid_status_memory:
                self.app.ui.grid_snap_btn.trigger()
                self._grid_disabled = True

            # the flat polygons and their STRtree are made here, not in the first click
            self.grb_obj = isolated_obj
            self._object_polygons()

            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click on a polygon to isolate it."))
            self.app.plotcanvas.graph_event_connect('mouse_release', self.on_poly_mouse_click_release)
            self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)

            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

            # disconnect flags
            self.poly_sel_disconnect_flag = True

    def isolate(self, isolated_obj, sel_tools, tools_storage, geometry=None, limited_area=None, negative_dia=None,
                plot=True, **args):
        """
        Creates an isolation routing geometry object in the project.

        The run works on a copy of the selected tools (without the geometry of any earlier run): tools_storage is
        not changed.

        :param isolated_obj:    Gerber object for which to generate the isolating routing geometry
        :type isolated_obj:     AppObjects.FlatCAMGerber.GerberObject
        :param sel_tools:       A list of tools to be used for isolation
        :type sel_tools:        list
        :param tools_storage:   A dictionary that holds the isolation object tools
        :type tools_storage:    dict
        :param geometry:        specific geometry to isolate; if it is given but empty nothing is isolated
        :type geometry:         List of Shapely polygon
        :param limited_area:    if not None isolate only this area
        :type limited_area:     Shapely Polygon or a list of them
        :param negative_dia:    isolate the geometry with a negative value for the tool diameter
        :type negative_dia:     bool
        :param plot:            if to plot the resulting geometry object
        :type plot:             bool
        :return: 'fail' if nothing was isolated (an error was reported), else None

        A worker passes all of run_settings() in **args: a missing one is read from its widget (GUI thread only).
        """
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        # use_combine: True/False
        use_combine = args['combine'] if 'combine' in args else self.ui.combine_passes_cb.get_value()
        # use_iso_except: True/False
        use_iso_except = args['iso_except'] if 'iso_except' in args else self.ui.except_cb.get_value()
        # use_extra_passes: True/False
        use_extra_passes = args['e_passes'] if 'e_passes' in args else self.ui.pad_passes_entry.get_value()
        # use_area_exception: True/False
        use_area_exception = args['area_except'] if 'area_except' in args else self.ui.except_cb.get_value()
        # sel_area_shape: ['square', 'polygon']
        sel_area_shape = args['area_shape'] if 'area_shape' in args else self.ui.area_shape_radio.get_value()
        # use_rest: True/False
        use_rest = args['rest'] if 'rest' in args else self.ui.rest_cb.get_value()
        # selection_type: [_("All"), _("Area Selection"), _("Polygon Selection"), _("Reference Object")]
        selection_type = args['sel_type'] if 'sel_type' in args else self.ui.select_combo.get_value()
        # tool_tip_shape: ["C1", "C2", "C3", "C4", "B", "V", "L"]
        tool_tip_shape = args['tip_shape'] if 'tip_shape' in args else self.ui.tool_shape_combo.get_value()
        # use simplification: True/False with simplificaiton_tol: Float
        use_simplification = args['iso_simplification'] if 'iso_simplification' in args else \
            self.ui.simplify_cb.get_value()
        simplification_tol = args['simplification_tol'] if 'simplification_tol' in args else \
            self.ui.sim_tol_entry.get_value()
        ui = getattr(self, 'ui', None)
        # use_clear_leftovers: True/False (combined passes only)
        use_clear_leftovers = args['clear_leftovers'] if 'clear_leftovers' in args else (
            ui.clear_leftovers_cb.get_value() if ui is not None else False)
        # the object of the exclusion areas
        # (None: area_subtraction() reads the widget)
        self._exc_name = args.get('exc_name')
        # boundary: None or the area (Polygon / MultiPolygon) the isolation is limited to
        boundary = args['boundary'] if 'boundary' in args else self.get_boundary_area()
        if boundary == 'fail':
            return 'fail'
        trace_boundary = boundary is not None and (
            args['trace_boundary'] if 'trace_boundary' in args else self.ui.trace_boundary_cb.get_value())
        if use_rest:
            # rest machining only: isolate the polygons even if their interiors can not be isolated; the order of the tools
            use_forced_rest = args['forced_rest'] if 'forced_rest' in args else self.ui.forced_rest_iso_cb.get_value()
            rest_order = args['order'] if 'order' in args else self.ui.iso_order_combo.get_value()

        if geometry is not None:
            # a given but empty geometry (e.g. a Reference Object that covers all the copper) is an empty selection:
            # it must not fall back to the isolation of the whole board
            geometry = flatten_shapely_geometry(geometry)
            if not geometry:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
                return 'fail'

        # the run uses its own copy of the selected tools, so the plugin tools keep no geometry and no run values
        run_tools = tools_for_run(tools_storage, sel_tools)
        # the strategy, the Voronoi spacing and the pad isolation are values of each tool; one given in args
        # (e.g. by a script) is for all the tools
        strategy_given = args.get('strategy')
        if strategy_given is not None and tool_strategy(strategy_given) is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The isolation strategy must be Offset or Voronoi."))
            return 'fail'
        for run_tool in run_tools.values():
            tool_data = run_tool['data']
            strategy = tool_strategy(strategy_given if strategy_given is not None
                                     else tool_data.get('tools_iso_strategy'))
            if strategy is None:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The isolation strategy must be Offset or Voronoi."))
                return 'fail'
            voronoi_spacing = args['voronoi_spacing'] if args.get('voronoi_spacing') is not None else \
                tool_data.get('tools_iso_voronoi_spacing', 0.05)
            pad_isolation = args['pad_isolation'] if args.get('pad_isolation') is not None else \
                tool_data.get('tools_iso_voronoi_pads', False)
            run_tool['data'].update({
                "tools_iso_rest": use_rest,
                "tools_iso_combine_passes": use_combine,
                "tools_iso_simplification": use_simplification,
                "tools_iso_simplification_tol": simplification_tol,
                "tools_iso_isoexcept": use_iso_except,
                "tools_iso_selection": selection_type,
                "tools_iso_area_shape": sel_area_shape,
                "tools_mill_job_type": 2,  # _("Isolation")
                # generated Isolation paths are already the tool centerline: Milling must not offset them again
                "tools_iso_centerline": True,
                "tools_mill_tool_shape": run_tool['data'].get('tools_mill_tool_shape', tool_tip_shape)
                    if strategy == 'voronoi' else tool_tip_shape,
                "tools_iso_strategy": strategy,
                "tools_iso_voronoi_spacing": float(voronoi_spacing),
                "tools_iso_voronoi_pads": bool(pad_isolation) and strategy == 'voronoi',
            })
        voronoi_tools = [uid for uid in sel_tools if run_tools[uid]['data']['tools_iso_strategy'] == 'voronoi']

        prog_plot = self.app.options.get(
            "tools_iso_plotting", app_defaults.get("tools_iso_plotting", AppDefaults.factory_defaults.get("tools_iso_plotting")))

        try:
            if voronoi_tools:
                if selection_type != 0 or use_area_exception:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "The Voronoi strategy needs All copper; area, polygon and reference selections are not supported."))
                    return 'fail'
                # each Voronoi tool uses its own spacing and pad isolation (its tool data)
                if self.voronoi_isolation(
                        iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools, sel_tools=voronoi_tools,
                        boundary=boundary, use_rest=use_rest,
                        negative_dia=negative_dia, iso_except=use_iso_except, extra_passes=use_extra_passes,
                        clear_leftovers=use_clear_leftovers, limited_area=limited_area,
                        simp_en=use_simplification, simp_tol=simplification_tol, plot=plot,
                        diagnostic_token=args.get('diagnostic_token')) == 'fail':
                    return 'fail'
                # the tools of the Offset strategy follow
                sel_tools = [uid for uid in sel_tools if uid not in voronoi_tools]
                if not sel_tools:
                    return None

            if use_combine:
                if use_rest:
                    return self.combined_rest(iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools,
                                              lim_area=limited_area, sel_tools=sel_tools, iso_except=use_iso_except,
                                              forced_rest=use_forced_rest, order=rest_order,
                                              negative_dia=negative_dia, simp_en=use_simplification,
                                              simp_tol=simplification_tol, plot=plot, boundary=boundary,
                                              trace_boundary=trace_boundary)

                # use_clear_leftovers: True/False (combined passes only)
                use_clear_leftovers = args['clear_leftovers'] if 'clear_leftovers' in args else \
                    self.ui.clear_leftovers_cb.get_value()
                return self.combined_normal(iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools,
                                            sel_tools=sel_tools, iso_except=use_iso_except, lim_area=limited_area,
                                            negative_dia=negative_dia, extra_passes=use_extra_passes,
                                            simp_en=use_simplification, simp_tol=simplification_tol, plot=plot,
                                            clear_leftovers=use_clear_leftovers, boundary=boundary,
                                            trace_boundary=trace_boundary)

            if geometry is None:
                # we do isolation over all the geometry of the Gerber object
                # because it is already fused together
                work_geo = flatten_shapely_geometry(isolated_obj.solid_geometry)
            else:
                work_geo = geometry
            # the pads that get the extra passes: the same for all the tools and passes
            pads = None

            for tool in sel_tools:
                if self.app.abort_flag:
                    raise grace

                tool_data = run_tools[tool]['data']

                iso_t = {
                    'ext':  0,
                    'int':  1,
                    'full': 2
                }[tool_data['tools_iso_isotype']]

                passes = tool_data['tools_iso_passes']
                overlap = tool_data['tools_iso_overlap']
                overlap /= 100.0

                milling_type = tool_data['tools_iso_milling_type']

                tool_dia = run_tools[tool]['tooldia']
                iso_offsets = [IsolationGen.pass_offset(tool_dia, nr, overlap, negative_dia)
                               for nr in range(passes)]
                # the passes are buffered ahead, in parallel threads
                buffers = IsolationGen.pass_buffers(isolated_obj, iso_offsets, self.app, geometry=work_geo)
                # climb milling (M3 spindle): the tool goes clockwise around the features; conventional reverses it
                mill_dir = 0 if milling_type == 'cl' else 1

                outname = "%s_%.*f" % (isolated_obj.obj_options["name"], self.decimals, float(tool_dia))

                # the passes that made paths: [the pass number, the object name, the paths]
                made = []
                # the envelopes of those passes, for the pad passes
                envelopes = []
                for i in range(passes):
                    if self.app.abort_flag:
                        raise grace

                    iso_offset = iso_offsets[i]

                    if passes > 1:
                        iso_name = outname + "_iso" + str(i + 1)
                        if iso_t == 0:
                            iso_name = outname + "_ext_iso" + str(i + 1)
                        elif iso_t == 1:
                            iso_name = outname + "_int_iso" + str(i + 1)
                    else:
                        iso_name = outname + "_iso"
                        if iso_t == 0:
                            iso_name = outname + "_ext_iso"
                        elif iso_t == 1:
                            iso_name = outname + "_int_iso"

                    iso_geo = self.generate_envelope(isolated_obj, iso_offset, mill_dir, geometry=work_geo,
                                                     env_iso_type=iso_t, nr_passes=i, prog_plot=prog_plot,
                                                     buffers=buffers)
                    if iso_geo == 'fail':
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                        continue

                    # the envelope, for the pad passes (with a boundary iso_geo becomes the clipped paths)
                    envelope_geo = iso_geo
                    if boundary is not None:
                        iso_geo = clip_paths_to_area(iso_geo, boundary)
                        if not iso_geo:
                            # a later pass can still reach an area of the boundary that is not connected to this one
                            if isinstance(boundary, MultiPolygon) and not (
                                    iso_offset > 0 and pass_covers_area(envelope_geo, boundary)):
                                continue
                            # this pass is completely outside the boundary, and so are the next ones
                            break

                    if use_extra_passes > 0:
                        envelopes += flatten_shapely_geometry(envelope_geo)
                    made.append([i, iso_name, iso_geo])

                # the number of passes up to the last one with paths inside the boundary
                passes_made = made[-1][0] + 1 if made else 0

                # Extra Pads isolations: the rings start after the last pass and are only in its object
                if made and use_extra_passes > 0:
                    if pads is None:
                        pads = pad_solids(isolated_obj, geometry)
                    if pads:
                        pad_geo = flatten_shapely_geometry(IsolationGen.pad_passes(
                            pads, envelopes, tool_dia, passes_made, use_extra_passes, overlap, self.app.options,
                            negative_dia=negative_dia, units=IsolationGen.object_units(isolated_obj, self.app)))
                        if boundary is not None:
                            pad_geo = clip_paths_to_area(pad_geo, boundary)
                        made[-1][2] = flatten_shapely_geometry(made[-1][2]) + pad_geo

                for n, (__, iso_name, iso_geo) in enumerate(made):
                    iso_geo = flatten_shapely_geometry(iso_geo)

                    if boundary is not None:
                        # the boundary is traced once, with the first pass that has paths inside it
                        if trace_boundary and n == 0:
                            iso_geo += area_outline_paths(boundary)
                        if use_simplification:
                            iso_geo = [g.simplify(tolerance=simplification_tol) for g in iso_geo if not g.is_empty]
                            iso_geo = clip_paths_to_area(iso_geo, boundary)

                    # ############################################################
                    # ########## AREA SUBTRACTION ################################
                    # ############################################################
                    if use_iso_except:
                        self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                        iso_geo = self.area_subtraction(iso_geo)

                    if limited_area:
                        self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                        iso_geo = self.area_intersection(iso_geo, intersection_geo=limited_area)

                    # make sure that no empty geometry element is in the solid_geometry
                    new_solid_geo = flatten_shapely_geometry(
                        iso_geo, simplify_tolerance=simplification_tol if use_simplification and boundary is None
                        else 0.0)

                    # each object has its own data of the tool
                    obj_data = deepcopy(tool_data)
                    obj_data.update({"name": iso_name, "tools_mill_tooldia": float(tool_dia)})

                    def iso_init(geo_obj, fc_obj):
                        # Propagate options
                        geo_obj.obj_options["tools_mill_tooldia"] = str(tool_dia)

                        geo_obj.solid_geometry = list(new_solid_geo)

                        # ############################################################
                        # ########## AREA SUBTRACTION ################################
                        # ############################################################
                        # (it is done already with the same exclusion area if use_iso_except)
                        if use_area_exception and not use_iso_except:
                            self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                            geo_obj.solid_geometry = flatten_shapely_geometry(
                                self.area_subtraction(geo_obj.solid_geometry))

                        geo_obj.tools = {
                            1: {
                                'tooldia':          float(tool_dia),
                                'data':             obj_data,
                                'solid_geometry':   list(geo_obj.solid_geometry)
                            }
                        }

                        # detect if solid_geometry is empty
                        if not geo_obj.solid_geometry:
                            msg = '[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), geo_obj.obj_options["name"])
                            fc_obj.inform.emit(msg)
                            return 'fail'
                        else:
                            if self.validation_status:
                                msg = '[success] %s: %s' % (_("Isolation geometry created"),
                                                            geo_obj.obj_options["name"])
                                fc_obj.inform.emit(msg)
                        geo_obj.multigeo = True

                    a_select = True if self.validation_status else False
                    self.new_object("geometry", iso_name, iso_init, source=isolated_obj,
                                    plot=plot, autoselected=a_select)

                if boundary is not None:
                    report = boundary_passes_message(outname, passes_made, passes)
                    self.app.log.info("Isolation boundary: " + report)
                    self.app.inform.emit(report)

            # clean the progressive plotted shapes if it was used

            if plot and prog_plot == 'progressive':
                try:
                    self.temp_shapes.clear(update=True)
                except Exception as err:
                    self.app.log.debug("ToolIsolation.isolate(). Clear temp_shapes -> %s" % str(err))
        except grace:
            # the user stopped the isolation: the objects made until now are kept
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Isolation aborted."))
            if plot and prog_plot == 'progressive':
                try:
                    self.temp_shapes.clear(update=True)
                except Exception as err:
                    self.app.log.debug("ToolIsolation.isolate(). Clear temp_shapes -> %s" % str(err))
            return 'fail'

        # Switch notebook to Properties page
        # self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

    def voronoi_isolation(self, iso_obj, iso2geo, tools_storage, sel_tools, boundary, spacing=None,
                          use_rest=False, negative_dia=None, iso_except=False, extra_passes=0,
                          clear_leftovers=False, limited_area=None, simp_en=False, simp_tol=0.001, plot=True,
                          diagnostic_token=None):
        """
        Inter-body Voronoi isolation: one object for each selected tool, one pass, no contour around a single body.

        :param iso_obj:         the isolated Gerber object
        :param iso2geo:         specific geometry to isolate
        :param tools_storage:   the tools of this run
        :param sel_tools:       the selected tool uids
        :param boundary:        island enclosure, made convex; its compensated perimeter is cut, with a working collar
                                for the full cutter width, output rounding and polygonal buffer approximation
        :param spacing:         sampling step along the copper boundaries, in the application units; None: the
                                "tools_iso_voronoi_spacing" of each tool
        :param use_rest:        rest machining is not supported by this strategy
        :param negative_dia:    a negative diameter isolates inside the copper; not supported
        :param iso_except:      an excluded area is not supported
        :param extra_passes:    pad passes are not supported
        :param clear_leftovers: leftover clearing is not supported
        :param limited_area:    a limited area is not supported
        :param simp_en:         simplify the generated paths and re-check the cutter sweep
        :param simp_tol:        simplification tolerance
        :param plot:            if to plot the resulting geometry object
        :return:                'fail' if nothing was isolated (an error was reported), else None
        """
        if use_rest:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support Rest Machining. Use the Offset strategy for rest machining."))
            return 'fail'
        if negative_dia:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support a negative tool diameter (inside-only isolation)."))
            return 'fail'
        if iso_except or limited_area:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support an excluded or a limited area."))
            return 'fail'
        if extra_passes or clear_leftovers:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support extra pad passes or clearing leftovers."))
            return 'fail'
        if boundary is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Voronoi isolation needs the Boundary option: select the board outline object."))
            return 'fail'

        # A rerun must not leave the previous failure region on the canvas.
        if diagnostic_token is None:
            diagnostic_token = self._voronoi_diagnostic_token
        self.voronoi_diagnostic_sig.emit({'action': 'clear', 'token': diagnostic_token})
        boundary = boundary.convex_hull
        work_geo = iso_obj.solid_geometry if iso2geo is None else iso2geo
        prepared = []
        # Generation-space checks only: preserve these paths and reject stale metadata at CNC generation.
        try:
            coord_decimals = int(self.app.options.get('cncjob_coords_decimals', 4))
        except (TypeError, ValueError):
            coord_decimals = 4
        for tool in sel_tools:
            if self.app.abort_flag:
                raise grace

            tool_data = tools_storage[tool]['data']
            passes = int(tool_data['tools_iso_passes'])
            iso_type = tool_data['tools_iso_isotype']
            if passes != 1:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The Voronoi strategy uses one pass: it has no pass-width interpretation."))
                return 'fail'
            if iso_type != 'full':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The Voronoi strategy needs Full isolation; exteriors or interiors alone are not supported."))
                return 'fail'

            tool_spacing = float(spacing if spacing is not None else tool_data.get('tools_iso_voronoi_spacing', 0.05))
            tool_dia = float(tools_storage[tool]['tooldia'])
            tool_shape = tool_data.get('tools_mill_tool_shape')
            if tool_shape in (self.V_SHAPE_INDEX, 'V', 'v'):
                effective = IsolationGen.v_effective_diameter(tool_data)
                if effective is None:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "The effective diameter of the V-shaped tool is not valid."))
                    return 'fail'
                tool_dia = effective
            if not math.isfinite(tool_dia) or tool_dia <= 0:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "Voronoi isolation needs a positive, finite effective tool diameter."))
                return 'fail'

            # Include physical cutter room for the compensated outer partition edges and output rounding.
            __, tool_boundary = IsolationGen.voronoi_working_bounds(boundary, tool_dia, coord_decimals)
            try:
                gen_report = {}
                paths = IsolationGen.voronoi_isolation(
                    work_geo, tool_boundary, tool_dia, tool_spacing, units=self.units,
                    simplify_tol=simp_tol if simp_en else 0.0, coord_decimals=coord_decimals,
                    abort=lambda: self.app.abort_flag, isolation_area=boundary, report=gen_report)
                if gen_report.get('spacing', tool_spacing) > tool_spacing:
                    # a big board: the requested spacing would give more sites than the budget
                    self.app.inform.emit('[WARNING_NOTCL] %s' % (_(
                        "The Voronoi spacing was increased from %.4f to %.4f %s to stay within the site budget.") %
                        (tool_spacing, gen_report['spacing'], self.units)))
                    tool_spacing = gen_report['spacing']
                pad_report = None
                if tool_data.get('tools_iso_voronoi_pads', False):
                    paths, pad_report = IsolationGen.voronoi_pad_isolation(
                        paths, iso_obj, tool_boundary, tool_dia, coord_decimals=coord_decimals,
                        abort=lambda: self.app.abort_flag, isolation_area=boundary)
            except IsolationGen.VoronoiAborted:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Voronoi isolation aborted."))
                return 'fail'
            except IsolationGen.VoronoiIsolationError as err:
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
                diagnostics = getattr(err, 'diagnostics', None) or {}
                region = diagnostics.get('region_geometry')
                if region is not None and not region.is_empty:
                    self.voronoi_diagnostic_sig.emit({'action': 'show', 'region': region, 'token': diagnostic_token})
                return 'fail'

            outname = "%s_%.*f_voronoi" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))
            # two tools with the same diameter in the name (e.g. 0.1 and 0.1004) must not share an object name
            base_name, count = outname, 1
            while outname in [item[0] for item in prepared]:
                count += 1
                outname = "%s_%d" % (base_name, count)
            obj_data = deepcopy(tool_data)
            obj_data.pop('tools_iso_voronoi_pad_report', None)
            if pad_report is not None:
                obj_data['tools_iso_voronoi_pad_report'] = pad_report
            obj_data.update({
                "name": outname,
                "tools_mill_tooldia": float(tool_dia),
                "tools_mill_job_type": 2,  # _("Isolation")
                "tools_iso_centerline": True,
                "tools_iso_strategy": "voronoi",
                "tools_iso_voronoi_spacing": tool_spacing,
                # The paths were validated on this output grid and must not be simplified again by the CNC job.
                "tools_iso_validated_paths": True,
                "tools_iso_voronoi_coord_decimals": coord_decimals,
                "tools_iso_voronoi_path_signature": sha256(
                    shapely.GeometryCollection(paths).normalize().wkb).hexdigest(),
                "tools_iso_voronoi_units": self.units.upper(),
                "tools_iso_voronoi_tooldia": float(tool_dia),
            })
            prepared.append((outname, paths, tool_dia, obj_data))

        if not prepared:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi isolation geometry could not be generated."))
            return 'fail'
        if self.app.abort_flag:
            raise grace

        # Publish only after every selected tool has passed clearance and separation checks.
        for outname, paths, tool_dia, obj_data in prepared:
            if 'tools_iso_voronoi_pad_report' in obj_data:
                self.voronoi_diagnostic_sig.emit({
                    'action': 'pad_report', 'token': diagnostic_token,
                    'report': obj_data['tools_iso_voronoi_pad_report']})
            def iso_init(geo_obj, fc_obj, paths=paths, tool_dia=tool_dia, obj_data=obj_data):
                geo_obj.obj_options["tools_mill_tooldia"] = str(tool_dia)
                geo_obj.solid_geometry = list(paths)
                geo_obj.tools = {
                    1: {
                        'tooldia':          float(tool_dia),
                        'data':             deepcopy(obj_data),
                        'solid_geometry':   list(geo_obj.solid_geometry)
                    }
                }
                if not geo_obj.solid_geometry:
                    msg = '[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), geo_obj.obj_options["name"])
                    fc_obj.inform.emit(msg)
                    return 'fail'
                if self.validation_status:
                    msg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                    fc_obj.inform.emit(msg)
                geo_obj.multigeo = True

            a_select = True if self.validation_status else False
            self.new_object("geometry", outname, iso_init, source=iso_obj, plot=plot, autoselected=a_select)

    def combined_rest(self, iso_obj, iso2geo, tools_storage, lim_area, sel_tools, iso_except=False, forced_rest=False,
                      order=0, negative_dia=None, simp_en=False, simp_tol=0.001, plot=True, boundary=None,
                      trace_boundary=False):
        """
        Isolate the provided Gerber object using "rest machining" strategy

        :param iso_obj:         the isolated Gerber object
        :type iso_obj:          AppObjects.FlatCAMGerber.GerberObject
        :param iso2geo:         specific geometry to isolate
        :type iso2geo:          list of Shapely Polygon
        :param tools_storage:   a dictionary that holds the tools and geometry
        :type tools_storage:    dict
        :param lim_area:        if not None restrict isolation to this area
        :type lim_area:         Shapely Polygon or a list of them
        :param sel_tools:       a list of the selected tools (the uids)
        :type sel_tools:        list
        :param iso_except:      If True it will isolate except a special area
        :type iso_except:       bool
        :param forced_rest:     isolate the polygons even if their interiors can not be isolated
        :type forced_rest:      bool
        :param order:           the order of the tools in the Tool Table: 0 default, 1 Forward, 2 Reverse. The rest
                                machining always goes from the largest tool to the smallest one, the smaller tools
                                isolate what the larger ones left
        :type order:            int
        :param negative_dia:    isolate the geometry with a negative value for the tool diameter
        :type negative_dia:     bool
        :param simp_en:         enable simplification of geometry
        :type simp_en:          bool
        :param simp_tol:        simplification tolerance
        :type simp_tol:         float
        :param plot:            if to plot the resulting geometry object
        :type plot:             bool
        :param boundary:        if not None, the area to which the isolation is limited
        :type boundary:         Shapely Polygon or MultiPolygon
        :param trace_boundary:  also add the outline of the boundary as a path
        :type trace_boundary:   bool
        :return:                'fail' if nothing was isolated (an error was reported), else None
        """

        self.app.log.debug("ToolIsolation.combine_rest()")
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        total_solid_geometry = []
        # the tools that made geometry in this run
        used_tools = []

        iso_name = iso_obj.obj_options["name"] + '_iso_rest'
        # the rest machining works with polygons (it buffers and tests them one by one)
        work_geo = [geo for geo in flatten_shapely_geometry(iso_obj.solid_geometry if iso2geo is None else iso2geo)
                    if isinstance(geo, Polygon)]
        if not work_geo:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
            return 'fail'

        if not sel_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
            return 'fail'

        # the largest tool first, whatever the order in the Tool Table: with the smallest first, it would isolate
        # everything and there would be nothing left for the other tools
        sorted_tools = sorted(sel_tools, key=lambda uid: float(tools_storage[uid]['tooldia']), reverse=True)
        if order == 1:  # "Forward"
            self.app.log.info("ToolIsolation.combined_rest() -> the order Forward is not used with rest machining.")

        # decide to use "progressive" or "normal" plotting
        prog_plot = self.app.options.get(
            "tools_iso_plotting", app_defaults.get("tools_iso_plotting", AppDefaults.factory_defaults.get("tools_iso_plotting")))

        for tool in sorted_tools:
            if self.app.abort_flag:
                raise grace

            tool_dia = tools_storage[tool]['tooldia']
            tool_data = tools_storage[tool]['data']

            passes = tool_data['tools_iso_passes']
            overlap = tool_data['tools_iso_overlap']
            overlap /= 100.0

            milling_type = tool_data['tools_iso_milling_type']
            # climb milling (M3 spindle): the tool goes clockwise around the features; conventional reverses it
            mill_dir = False if milling_type == 'cl' else True
            iso_t = {
                'ext': 0,
                'int': 1,
                'full': 2
            }[tool_data['tools_iso_isotype']]

            outname = "%s_%.*f" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))
            internal_name = outname + "_iso"
            if iso_t == 0:
                internal_name = outname + "_ext_iso"
            elif iso_t == 1:
                internal_name = outname + "_int_iso"

            tool_data.update({
                "name": internal_name,
            })

            solid_geo, left_geo = self.generate_rest_geometry(geometry=work_geo, tooldia=tool_dia,
                                                              passes=passes, overlap=overlap, invert=mill_dir,
                                                              env_iso_type=iso_t, negative_dia=negative_dia,
                                                              forced_rest=forced_rest,
                                                              prog_plot=prog_plot,
                                                              prog_plot_handler=self.plot_temp_shapes,
                                                              plot=plot)
            if isinstance(solid_geo, str):
                # ('fail', 'fail'): the geometry stays for the next tools
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                continue
            work_geo = left_geo

            if boundary is not None:
                solid_geo = clip_paths_to_area(solid_geo, boundary)
                # the boundary is traced once, with the first tool that has paths inside it
                if trace_boundary and solid_geo and not total_solid_geometry:
                    solid_geo += area_outline_paths(boundary)
                if simp_en:
                    solid_geo = [g.simplify(tolerance=simp_tol) for g in solid_geo if not g.is_empty]
                    solid_geo = clip_paths_to_area(solid_geo, boundary)

            # ############################################################
            # ########## AREA SUBTRACTION ################################
            # ############################################################
            if iso_except:
                self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                solid_geo = self.area_subtraction(solid_geo)

            if lim_area:
                self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                solid_geo = self.area_intersection(solid_geo, intersection_geo=lim_area)

            # make sure that no empty geometry element is in the solid_geometry
            new_solid_geo = flatten_shapely_geometry(
                solid_geo, simplify_tolerance=simp_tol if simp_en and boundary is None else 0.0)

            tool_data['tools_mill_tooldia'] = float(tool_dia)
            tools_storage[tool] = {
                'tooldia':          float(tool_dia),
                'data':             tool_data,
                'solid_geometry':   new_solid_geo
            }
            if new_solid_geo:
                used_tools.append(tool)
                total_solid_geometry += new_solid_geo

            # if the geometry is all isolated
            if not work_geo:
                break

        # clean the progressive plotted shapes if it was used
        if plot and prog_plot == 'progressive':
            try:
                self.temp_shapes.clear(update=True)
            except Exception as err:
                self.app.log.debug("ToolIsolation.combined_rest(). Progressive plotting -> %s" % str(err))

        if not used_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), iso_name))
            result = 'fail'
        else:
            # the diameter of the object: the one of its largest tool
            obj_dia = max(float(tools_storage[uid]['tooldia']) for uid in used_tools)

            def iso_init(geo_obj, app_obj):
                geo_obj.obj_options["tools_mill_tooldia"] = str(obj_dia)

                geo_obj.tools = tools_for_object(tools_storage, used_tools)
                geo_obj.solid_geometry = total_solid_geometry
                # even if combine is checked, one pass is still single-geo
                geo_obj.multigeo = True

                if self.validation_status:
                    mssg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                    app_obj.inform.emit(mssg)

            a_select = True if self.validation_status else False
            self.new_object("geometry", iso_name, iso_init, source=iso_obj, plot=plot, autoselected=a_select)
            result = None

        # the tools are finished but the isolation is not finished therefore it failed
        if work_geo:
            self.app.inform.emit("[WARNING] %s" % _("Partial failure. The geometry was processed with all tools.\n"
                                                    "But there are still not-isolated geometry elements. "
                                                    "Try to include a tool with smaller diameter."))
            msg = _("The following are coordinates for the copper features that could not be isolated:")
            self.app.inform_shell.emit(msg)
            msg = ''
            for geo in work_geo:
                pt = geo.representative_point()
                coords = '(%s, %s), ' % (str(pt.x), str(pt.y))
                msg += coords
            self.app.inform_shell.emit(msg)
        return result

    def combined_normal(self, iso_obj, iso2geo, tools_storage, lim_area, sel_tools, iso_except, extra_passes=None,
                        negative_dia=None, simp_en=False, simp_tol=0.001, plot=True, prog_plot=None,
                        clear_leftovers=False, boundary=None, trace_boundary=False):
        """

        :param iso_obj:         the isolated Gerber object
        :type iso_obj:          AppObjects.FlatCAMGerber.GerberObject
        :param iso2geo:         specific geometry to isolate
        :type iso2geo:          list of Shapely Polygon
        :param tools_storage:   a dictionary that holds the tools and geometry
        :type tools_storage:    dict
        :param lim_area:        if not None restrict isolation to this area
        :type lim_area:         Shapely Polygon or a list of them
        :param sel_tools:       a list of the selected tools
        :type sel_tools:        list
        :param iso_except:      If True it will isolate except a special area
        :type iso_except:       bool
        :param extra_passes:    If not None then the value is the nr of extra isolation passes for pads only
        :type extra_passes:     int
        :param negative_dia:    isolate the geometry with a negative value for the tool diameter
        :type negative_dia:     bool
        :param simp_en:         enable simplification of geometry
        :type simp_en:          bool
        :param simp_tol:        simplification tolerance
        :type simp_tol:         float
        :param plot:            if to plot the resulting geometry object
        :type plot:             bool
        :param prog_plot:       If True a progressive plot is used
        :type prog_plot:        bool
        :param clear_leftovers: add paths that clear the copper left between the passes (full isolation only)
        :type clear_leftovers:  bool
        :param boundary:        if not None, the area to which the isolation is limited; the passes stop at the
                                first one that is completely outside it
        :type boundary:         Shapely Polygon or MultiPolygon
        :param trace_boundary:  also add the outline of the boundary as a path
        :type trace_boundary:   bool
        :return:                'fail' if nothing was isolated (an error was reported), else None
        """
        self.app.log.debug("ToolIsolation.combined_normal()")
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        total_solid_geometry = []
        # the tools that made geometry in this run
        used_tools = []

        iso_name = iso_obj.obj_options["name"] + '_iso_combined'
        geometry = iso2geo
        work_geo = iso_obj.solid_geometry if geometry is None else geometry
        if prog_plot is None:
            prog_plot = self.app.options.get(
                "tools_iso_plotting", app_defaults.get("tools_iso_plotting", AppDefaults.factory_defaults.get("tools_iso_plotting")))
        # the pads that get the extra passes: the same for all the tools and passes
        pads = None

        for tool in sel_tools:
            if self.app.abort_flag:
                raise grace

            tool_dia = tools_storage[tool]['tooldia']
            tool_has_offset = tools_storage[tool]['data']['tools_mill_offset_type']
            tool_offset_value = tools_storage[tool]['data']['tools_mill_offset_value']
            tool_type = tools_storage[tool]['data']['tools_mill_tool_shape']
            tool_data = tools_storage[tool]['data']

            iso_t = {
                'ext': 0,
                'int': 1,
                'full': 2
            }[tool_data['tools_iso_isotype']]

            passes = tool_data['tools_iso_passes']
            overlap = tool_data['tools_iso_overlap']
            overlap /= 100.0

            milling_type = tool_data['tools_iso_milling_type']

            outname = "%s_%.*f" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))

            internal_name = outname + "_iso"
            if iso_t == 0:
                internal_name = outname + "_ext_iso"
            elif iso_t == 1:
                internal_name = outname + "_int_iso"

            tool_data.update({
                "name": internal_name,
            })

            # the copper left between the passes is found from the complete passes of the full isolation, outwards
            use_leftovers = clear_leftovers and iso_t == 2 and not negative_dia
            iso_offsets = [IsolationGen.pass_offset(tool_dia, nr, overlap, negative_dia) for nr in range(passes)]
            # the passes are buffered ahead, in parallel threads
            buffers = IsolationGen.pass_buffers(iso_obj, iso_offsets, self.app, geometry=work_geo)
            # climb milling (M3 spindle): the tool goes clockwise around the features; conventional reverses it
            mill_dir = 0 if milling_type == 'cl' else 1

            def pass_envelopes():
                for nr_pass, iso_offset in enumerate(iso_offsets):
                    if self.app.abort_flag:
                        raise grace
                    iso_geo = self.generate_envelope(
                        iso_obj, iso_offset, mill_dir, geometry=work_geo, env_iso_type=iso_t,
                        nr_passes=nr_pass, prog_plot=prog_plot, buffers=buffers)
                    if iso_geo == 'fail':
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                    yield iso_geo

            solid_geo, envelopes, leftover_geo, passes_made = IsolationGen.combined_passes(
                pass_envelopes(), iso_offsets, tool_dia,
                self.app.options, mill_dir,
                clear_leftovers=use_leftovers, boundary=boundary,
                units=IsolationGen.object_units(iso_obj, self.app))

            if use_leftovers:
                self.app.log.debug("ToolIsolation.combined_normal() -> %d paths clear the leftovers" %
                                   len(leftover_geo))
            if boundary is not None:
                report = boundary_passes_message(outname, passes_made, passes)
                self.app.log.info("Isolation boundary: " + report)
                self.app.inform.emit(report)
                if passes_made == 0:
                    # no path of this tool is inside the boundary: no pad passes and no traced boundary either
                    continue

            # Extra Pads isolations
            if extra_passes is not None and extra_passes > 0:
                if pads is None:
                    pads = pad_solids(iso_obj, geometry)
                if pads:
                    # after the last pass made (with a boundary the passes can stop before the requested number)
                    solid_geo += IsolationGen.pad_passes(
                        pads, envelopes if boundary is not None else solid_geo,
                        tool_dia, passes_made, extra_passes, overlap,
                        self.app.options, negative_dia=negative_dia, units=IsolationGen.object_units(iso_obj, self.app))

            solid_geo += leftover_geo

            if boundary is not None:
                solid_geo = clip_paths_to_area(solid_geo, boundary)
                if trace_boundary and not total_solid_geometry:
                    solid_geo += area_outline_paths(boundary)
                if simp_en:
                    solid_geo = [g.simplify(tolerance=simp_tol) for g in solid_geo if not g.is_empty]
                    solid_geo = clip_paths_to_area(solid_geo, boundary)

            # ############################################################
            # ########## AREA SUBTRACTION ################################
            # ############################################################
            if iso_except:
                self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                solid_geo = self.area_subtraction(solid_geo)

            if lim_area:
                self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                solid_geo = self.area_intersection(solid_geo, intersection_geo=lim_area)

            # make sure that no empty geometry element is in the solid_geometry
            new_solid_geo = flatten_shapely_geometry(
                solid_geo, simplify_tolerance=simp_tol if simp_en and boundary is None else 0.0)

            tool_data['tools_mill_tooldia'] = float(tool_dia)
            tools_storage[tool] = {
                'tooldia':          float(tool_dia),
                'offset':           tool_has_offset,
                'offset_value':     tool_offset_value,
                'tool_type':        tool_type,
                'data':             tool_data,
                'solid_geometry':   new_solid_geo
            }
            if new_solid_geo:
                used_tools.append(tool)
                total_solid_geometry += new_solid_geo

        # clean the progressive plotted shapes if it was used
        if plot and prog_plot == 'progressive':
            try:
                self.temp_shapes.clear(update=True)
            except Exception as err:
                self.app.log.debug("ToolIsolation.combined_normal(). Progressive plotting -> %s" % str(err))

        if not used_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), iso_name))
            return 'fail'

        # the diameter of the object: the one of its largest tool
        obj_dia = max(float(tools_storage[uid]['tooldia']) for uid in used_tools)

        def iso_init(geo_obj, app_obj):
            geo_obj.obj_options["tools_mill_tooldia"] = str(obj_dia)

            geo_obj.tools = tools_for_object(tools_storage, used_tools)
            geo_obj.solid_geometry = total_solid_geometry
            # even if combine is checked, one pass is still single-geo
            geo_obj.multigeo = True

            if self.validation_status:
                msg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                app_obj.inform.emit(msg)

        a_select = True if self.validation_status else False
        self.new_object("geometry", iso_name, iso_init, source=iso_obj, plot=plot, autoselected=a_select)

    def area_subtraction(self, geo, subtraction_geo=None):
        """
        Subtracts the subtraction_geo (if present else self.solid_geometry) from the geo

        :param geo:                 target geometry from which to subtract
        :param subtraction_geo:     geometry that acts as subtraction geo
        :return:
        """
        if subtraction_geo:
            subtractor_geo = subtraction_geo
        else:
            name = getattr(self, '_exc_name', None)
            if name is None:
                name = self.ui.exc_obj_combo.currentText()
            subtractor_obj = self.app.collection.get_by_name(name)
            subtractor_geo = subtractor_obj.solid_geometry
        return IsolationGen.area_subtraction(geo, subtractor_geo)

    def area_intersection(self, geo, intersection_geo=None):
        """
        Return the intersection geometry between geo and intersection_geo

        :param geo:                 target geometry
        :param intersection_geo:    second geometry
        :return:
        """
        return IsolationGen.area_intersection(geo, intersection_geo)

    # ------------------------------------------------------------------------------------------------------------
    # Area / Polygon selection modes
    # ------------------------------------------------------------------------------------------------------------

    def _selected_tool_ids(self):
        sel_rows = {t.row() for t in self.ui.tools_table.selectedItems()}
        return [int(self.ui.tools_table.item(row, 3).text()) for row in sel_rows]

    @classmethod
    def _flat_polygons(cls, geo):
        """The non empty Polygons of a Shapely geometry or of any nesting of lists / tuples of them."""
        if geo is None:
            return
        if isinstance(geo, Polygon):
            if not geo.is_empty:
                yield geo
        elif hasattr(geo, 'geoms'):
            for sub in geo.geoms:
                yield from cls._flat_polygons(sub)
        elif isinstance(geo, Iterable):
            for sub in geo:
                yield from cls._flat_polygons(sub)

    def _object_polygons(self):
        """
        The flat polygons of the source object and the STRtree over them. They are cached for as long as the object
        and its solid_geometry stay the same.

        :return: (list of Polygon, STRtree)
        """
        obj = self.grb_obj
        geo = obj.solid_geometry
        size = len(geo) if isinstance(geo, (list, tuple)) else -1
        cache = self._sel_cache
        if cache is not None and cache[0] is obj and cache[1] is geo and cache[2] == size:
            return cache[3], cache[4]

        polys = list(self._flat_polygons(geo))
        tree = shapely.STRtree(polys)
        self._sel_cache = (obj, geo, size, polys, tree)
        return polys, tree

    def polygon_at_point(self, point, ignore_interiors=False):
        """
        The polygon of the source object under a point.

        :param point:               (x, y)
        :param ignore_interiors:    if True the polygons are tested with their holes filled and the result is the hole
                                    (as a Polygon) that contains the point, or None
        :return:                    Polygon or None
        """
        polys, tree = self._object_polygons()
        pt = Point(point)
        for idx in sorted(tree.query(pt)):
            poly = polys[idx]
            if ignore_interiors:
                if Polygon(poly.exterior).contains(pt):
                    return self.get_selected_interior(poly, point=point)
            elif poly.contains(pt):
                return poly
        return None

    def _add_selected_polygons(self, polys):
        """
        Adds polygons to the selection and draws them with one batch. The ones already selected are skipped.

        :return: the number of polygons added
        """
        if not polys:
            return 0
        color = self.app.options.get(
            'global_sel_draw_color',
            (getattr(self.app, "defaults", None) or AppDefaults.factory_defaults).get(
                'global_sel_draw_color', AppDefaults.factory_defaults.get('global_sel_draw_color')))[:7] + 'AF'

        new_polys = []
        new_wkb = []
        for poly, wkb in zip(polys, shapely.to_wkb(np.asarray(polys, dtype=object))):
            if wkb not in self._poly_keys:
                self._poly_keys[wkb] = None     # a duplicate in the same batch
                new_polys.append(poly)
                new_wkb.append(wkb)

        shape_ids = self.app.tool_shapes.add_batch(
            [{'shape': p, 'color': color, 'face_color': color} for p in new_polys],
            layer=0, tolerance=self.drawing_tolerance)
        for poly, wkb, shape_id in zip(new_polys, new_wkb, shape_ids):
            self._poly_keys[wkb] = shape_id
            self.poly_dict[shape_id] = poly
        return len(new_polys)

    def _remove_selected_polygon(self, poly):
        """Removes one polygon from the selection and from the canvas. Returns False if it was not selected."""
        shape_id = self._poly_keys.pop(shapely.to_wkb(poly), None)
        if shape_id is None:
            return False
        self.poly_dict.pop(shape_id, None)
        self.app.tool_shapes.remove(shape_id)
        return True

    def end_selection_mode(self):
        """
        Ends the Area / Polygon selection mode: gives the canvas handlers back to the app, restores the grid snapping
        and drops every part of the selection (shapes on the canvas, picked polygons, zones, corners, points).
        Safe to call at any time, also when no mode is active.
        """
        plotcanvas = getattr(self.app, 'plotcanvas', None)
        if plotcanvas is not None:
            if self.area_sel_disconnect_flag is True:
                plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
                plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
                plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

                plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
                plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
                plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

            if self.poly_sel_disconnect_flag is True:
                plotcanvas.graph_event_disconnect('mouse_release', self.on_poly_mouse_click_release)
                plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

                plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.area_sel_disconnect_flag = False
        self.poly_sel_disconnect_flag = False

        # restore the Grid snapping only if this plugin switched it off, and by state, not by toggling blindly
        if self._grid_disabled:
            self._grid_disabled = False
            if self.grid_status_memory is True and not self.app.ui.grid_snap_btn.isChecked():
                self.app.ui.grid_snap_btn.trigger()

        self.poly_dict.clear()
        self._poly_keys.clear()
        self._sel_cache = None
        self.sel_rect = []
        self.first_click = False
        self.points = []
        self.poly_drawn = False

        if self.app.tool_shapes is not None:
            self.app.tool_shapes.clear(update=True)
        self.delete_moving_selection_shape()
        self.delete_tool_selection_shape()

    def isolate_in_worker(self, **kwargs):
        """
        Runs isolate(**kwargs) in the worker, with the progress indicator, so that the GUI does not hang. The tool check
        of the Generate click (if any) runs first, in the same task: the isolation reads its result.
        """
        validation = self.pending_validation
        self.pending_validation = None
        self._voronoi_diagnostic_token = object()
        kwargs['diagnostic_token'] = self._voronoi_diagnostic_token
        self.voronoi_diagnostic_sig.emit({'action': 'clear', 'token': self._voronoi_diagnostic_token})

        def worker_task(iso_class):
            with iso_class.app.proc_container.new('%s ...' % _("Isolating")):
                try:
                    iso_class.validation_status = True
                    if validation is not None:
                        iso_class.check_tools_validity(*validation)
                    iso_class.isolate(**kwargs)
                except grace:
                    iso_class.app.inform.emit('[WARNING_NOTCL] %s' % _("Isolation aborted."))

        self.app.worker_task.emit({'fcn': worker_task, 'params': [self]})

    def on_poly_mouse_click_release(self, event):
        self.app.mouse_down = False
        event_pos = event.pos
        right_button = 2

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        event_pos = (x, y)
        curr_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])
        else:
            curr_pos = (curr_pos[0], curr_pos[1])

        if event.button == 1:
            if self.app.selection_type is not None:
                self.selection_area_handler(self.app.mouse_pos, curr_pos, self.app.selection_type)
                self.app.selection_type = None
                return

            clicked_poly = self.polygon_at_point((curr_pos[0], curr_pos[1]),
                                                 ignore_interiors=self.ui.poly_int_cb.get_value() is True)
            if clicked_poly is None:
                self.app.inform.emit(_("No polygon detected under click position."))
                return

            if self._remove_selected_polygon(clicked_poly):
                self.app.inform.emit(
                    '%s. %s' % (_("Removed polygon"),
                                _("Click to add/remove next polygon or right click to start."))
                )
            else:
                self._add_selected_polygons([clicked_poly])
                self.app.inform.emit(
                    '%s: %d. %s' % (_("Added polygon"), int(len(self.poly_dict)),
                                    _("Click to add next polygon or right click to start."))
                )
            self.app.tool_shapes.redraw()
        elif event.button == right_button and self.app.ui.popMenu.mouse_is_panning is False:
            # the selection is taken first: ending the mode clears it
            poly_list = list(self.poly_dict.values())
            interiors = self.ui.poly_int_cb.get_value() is True
            self.end_selection_mode()

            sel_tools = self._selected_tool_ids()
            if not sel_tools:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
                return 'fail'

            if not poly_list:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("List of single polygons is empty. Aborting."))
                return "fail"

            settings = self.run_settings()
            if interiors:
                # isolate the interior polygons with a negative tool
                self.isolate_in_worker(isolated_obj=self.grb_obj, geometry=poly_list, negative_dia=True,
                                       sel_tools=sel_tools, tools_storage=tools_for_run(self.iso_tools, sel_tools),
                                       **settings)
            else:
                self.isolate_in_worker(isolated_obj=self.grb_obj, geometry=poly_list, sel_tools=sel_tools,
                                       tools_storage=tools_for_run(self.iso_tools, sel_tools), **settings)

    def on_select_all_polygons(self):
        self.app.log.debug("ToolIsolation.on_select_all_polygons()")

        self.obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            self.grb_obj = self.app.collection.get_by_name(self.obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(self.obj_name)))
            return

        if self.grb_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(self.obj_name)))
            return

        polys, _tree = self._object_polygons()
        self._add_selected_polygons(polys)
        self.app.tool_shapes.redraw()

    def on_deselect_all_polygons(self):
        self.app.log.debug("ToolIsolation.on_deselect_all_polygons()")

        self.poly_dict.clear()
        self._poly_keys.clear()
        self.app.tool_shapes.clear(update=True)

    def selection_area_handler(self, start_pos, end_pos, sel_type):
        """
        :param start_pos: mouse position when the selection LMB click was done
        :param end_pos: mouse position when the left mouse button is released
        :param sel_type: if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :return:
        """
        poly_selection = Polygon([start_pos,(end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])

        # delete previous selection shape
        self.app.delete_selection_shape()

        # enclosure (left to right) takes the polygons fully inside the rectangle, 'touch' the ones it intersects
        polys, tree = self._object_polygons()
        hits = tree.query(poly_selection, predicate='contains' if sel_type is True else 'intersects')
        added_poly_count = self._add_selected_polygons([polys[i] for i in sorted(hits)])

        if added_poly_count > 0:
            self.app.tool_shapes.redraw()
            self.app.inform.emit(
                '%s: %d. %s' % (_("Added polygon"),
                                int(added_poly_count),
                                _("Click to add next polygon or right click to start."))
            )
        else:
            self.app.inform.emit(_("No polygon in selection."))

    # To be called after clicking on the plot.
    def on_mouse_release(self, event):
        shape_type = self.ui.area_shape_radio.get_value()

        event_pos = event.pos
        right_button = 2

        event_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(event_pos[0], event_pos[1])
        else:
            curr_pos = (event_pos[0], event_pos[1])

        x1, y1 = curr_pos[0], curr_pos[1]

        # do clear area only for left mouse clicks
        if event.button == 1:
            if shape_type == "square":
                if self.first_click is False:
                    self.first_click = True
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the paint area."))

                    # event_pos is already translated (and curr_pos snapped, if the grid snapping is on)
                    self.cursor_pos = (curr_pos[0], curr_pos[1])
                else:
                    self.app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    self.app.delete_selection_shape()

                    x0, y0 = self.cursor_pos[0], self.cursor_pos[1]

                    pt1 = (x0, y0)
                    pt2 = (x1, y0)
                    pt3 = (x1, y1)
                    pt4 = (x0, y1)

                    new_rectangle = Polygon([pt1, pt2, pt3, pt4])
                    self.sel_rect.append(new_rectangle)

                    # add a temporary shape on canvas
                    self.draw_tool_selection_shape(old_coords=(x0, y0), coords=(x1, y1))

                    self.first_click = False
                    return
            else:
                self.points.append((x1, y1))

                if len(self.points) > 1:
                    self.poly_drawn = True
                    self.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))

                return ""
        elif event.button == right_button and self.mouse_is_dragging is False:

            shape_type = self.ui.area_shape_radio.get_value()

            if shape_type == "square":
                self.first_click = False
            else:
                # if we finish to add a polygon
                if self.poly_drawn is True:
                    try:
                        # try to add the point where we last clicked if it is not already in the self.points
                        last_pt = (x1, y1)
                        if last_pt != self.points[-1]:
                            self.points.append(last_pt)
                    except IndexError:
                        pass

                    # we need to add a Polygon and a Polygon can be made only from at least 3 points
                    if len(self.points) > 2:
                        self.delete_moving_selection_shape()
                        pol = Polygon(self.points)
                        # do not add invalid polygons even if they are drawn by utility geometry
                        if pol.is_valid:
                            self.sel_rect.append(pol)
                            self.draw_selection_shape_polygon(points=self.points)
                            self.app.inform.emit(
                                _("Zone added. Click to start adding next zone or right click to finish."))

                    self.points = []
                    self.poly_drawn = False
                    return

            # the zones are taken first: ending the mode clears them (and the points, and the shapes)
            sel_zones = list(self.sel_rect)
            self.end_selection_mode()

            if len(sel_zones) == 0:
                return

            sel_tools = self._selected_tool_ids()
            if not sel_tools:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
                return 'fail'

            self.isolate_in_worker(isolated_obj=self.grb_obj, limited_area=unary_union(sel_zones),
                                   sel_tools=sel_tools, tools_storage=tools_for_run(self.iso_tools, sel_tools),
                                   plot=True, **self.run_settings())

    # called on mouse move
    def on_mouse_move(self, event):
        shape_type = self.ui.area_shape_radio.get_value()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        event_pos = event.pos
        event_is_dragging = event.is_dragging

        curr_pos = self.app.plotcanvas.translate_coords(event_pos)

        # detect mouse dragging motion
        if event_is_dragging is True:
            self.mouse_is_dragging = True
        else:
            self.mouse_is_dragging = False

        # update the cursor position
        if self.app.grid_status():
            # Update cursor
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])

            self.app.app_cursor.set_data(np.asarray([(curr_pos[0], curr_pos[1])]),
                                         symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                         edge_width=self.app.options.get(
                                             "global_cursor_width",
                                             app_defaults.get("global_cursor_width", AppDefaults.factory_defaults.get("global_cursor_width"))),
                                         size=self.app.options.get(
                                             "global_cursor_size",
                                             app_defaults.get("global_cursor_size", AppDefaults.factory_defaults.get("global_cursor_size"))))

        if self.cursor_pos is None:
            self.cursor_pos = (0, 0)

        self.app.dx = curr_pos[0] - float(self.cursor_pos[0])
        self.app.dy = curr_pos[1] - float(self.cursor_pos[1])

        # # update the positions on status bar
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (curr_pos[0], curr_pos[1]))
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])

        # units = self.app.app_units.lower()
        # self.app.plotcanvas.text_hud.text = \
        #     'Dx:\t{:<.4f} [{:s}]\nDy:\t{:<.4f} [{:s}]\n\nX:  \t{:<.4f} [{:s}]\nY:  \t{:<.4f} [{:s}]'.format(
        #         self.app.dx, units, self.app.dy, units, curr_pos[0], units, curr_pos[1], units)
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])

        # draw the utility geometry
        if shape_type == "square":
            if self.first_click:
                self.app.draw_moving_selection_shape(old_coords=(self.cursor_pos[0], self.cursor_pos[1]),
                                                     coords=(curr_pos[0], curr_pos[1]))
        else:
            self.draw_moving_selection_shape_poly(points=self.points, data=(curr_pos[0], curr_pos[1]))

    def on_key_press(self, event):
        # modifiers = QtWidgets.QApplication.keyboardModifiers()

        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if type(event) is int:
            key = event
        # events from the GUI are of type QKeyEvent
        elif type(event) == QtGui.QKeyEvent:
            key = event.key()

        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':

            self.end_selection_mode()

    def on_iso_tool_add_from_db_executed(self, tool):
        """
        Here add the tool from DB  in the selected geometry object
        :return:
        """
        tool_from_db = deepcopy(tool)

        if tool['data']['tool_target'] not in [0, 3]:  # [General, Isolation]
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
            return 'fail'

        res = self.on_tool_from_db_inserted(tool=tool_from_db)

        if res == 'fail':
            return 'fail'
        self.app.inform.emit('[success] %s' % _("Tool from DB added in Tool Table."))

        # select last tool added
        toolid = res
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == toolid:
                self.ui.tools_table.selectRow(row)
        self.on_row_selection_change()
        return res

    def on_tool_from_db_inserted(self, tool):
        """
        Called from the Tools DB object through a App method when adding a tool from Tools Database
        :param tool: a dict with the tool data
        :return: None
        """

        self.ui_disconnect()
        self.units = self.app.app_units.upper()

        tooldia = float(tool['tooldia'])

        # construct a list of all 'tooluid' in the self.tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.iso_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        tooluid = max_uid + 1

        # the Tools Database keeps one unit (the one of the application): the diameter and the data need no conversion
        truncated_tooldia = self.app.dec_format(tooldia, self.decimals)
        if self.tool_dia_in_table(truncated_tooldia):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled. Tool already in Tool Table."))
            self.ui_connect()
            return 'fail'

        # the DB data over the defaults: a key that the DB tool does not have is still there for the isolation
        new_data = deepcopy(self.default_data)
        for d in tool['data']:
            if not d.startswith('tools_') or d.startswith(('tools_iso_', 'tools_mill_')):
                new_data[d] = deepcopy(tool['data'][d])
        new_data['name'] = '_iso'
        new_data['tools_iso_tooldia'] = deepcopy(truncated_tooldia)

        self.iso_tools.update({
            tooluid: {
                'tooldia':          truncated_tooldia,
                'data':             new_data,
                'solid_geometry':   []
            }
        })
        self.tooluid = tooluid

        self.app.inform.emit('[success] %s' % _("New tool added to Tool Table."))

        self.build_ui()

        # select the tool just added: the uid of a tool does not change when the table is sorted
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break
        self.update_ui()

        return tooluid

    def on_tool_add_from_db_clicked(self):
        """
        Called when the user wants to add a new tool from Tools Database. It will create the Tools Database object
        and display the Tools Database tab in the form needed for the Tool adding
        :return: None
        """

        # if the Tools Database is already opened focus on it
        for idx in range(self.app.ui.plot_tab_area.count()):
            if self.app.ui.plot_tab_area.tabText(idx) == _("Tools Database"):
                self.app.ui.plot_tab_area.setCurrentWidget(self.app.tools_db_tab)
                break
        ret_val = self.app.on_tools_database(source='iso')
        if ret_val == 'fail':
            return
        self.app.tools_db_tab.ok_to_add = True
        self.app.tools_db_tab.ui.buttons_frame.hide()
        self.app.tools_db_tab.ui.add_tool_from_db.show()
        self.app.tools_db_tab.ui.cancel_tool_from_db.show()

    def reset_fields(self):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))

    @staticmethod
    def poly2rings(poly):
        return IsolationGen.poly2rings(poly)

    @staticmethod
    def poly2ext(poly):
        return IsolationGen.poly2ext(poly)

    @staticmethod
    def poly2ints(poly):
        return IsolationGen.poly2ints(poly)

    def generate_envelope(self, iso_obj, offset, invert, geometry=None, env_iso_type=2, nr_passes=0,
                          prog_plot=False, buffers=None):
        """
        isolation_geometry() gives the polygons with the exteriors clockwise and the holes counter-clockwise: the
        copper features are on the right of the tool, which is climb milling with a spindle that turns clockwise (M3).
        invert reverses all the rings, for conventional milling.

        :param iso_obj:         The Gerber object to be isolated
        :param offset:          Offset distance to be passed to the obj.isolation_geometry() method
        :type offset:           float
        :param invert:          If to invert the direction of geometry (CW to CCW or reverse)
        :type invert:           int
        :param geometry:        Shapely Geometry for which to generate envelope
        :type geometry:
        :param env_iso_type:    type of isolation, can be 0 = exteriors or 1 = interiors or 2 = both (complete)
        :type env_iso_type:     int
        :param nr_passes:       Number of passes
        :type nr_passes:        int
        :param prog_plot:       Type of plotting: "normal" or "progressive"
        :type prog_plot:        str
        :param buffers:         the passes buffered ahead (iso_obj.isolation_buffers()), or None
        :type buffers:          camlib.PassBuffers
        :return:                The buffered geometry
        :rtype:                 MultiPolygon or Polygon
        """

        try:
            geom_shp = iso_obj.isolation_geometry(offset, geometry=geometry, iso_type=env_iso_type,
                                                  passes=nr_passes, prog_plot=prog_plot, buffers=buffers)
        except Exception as e:
            self.app.log.error('ToolIsolation.generate_envelope() --> %s' % str(e))
            return 'fail'

        return IsolationGen.orient_envelope(geom_shp, invert, self.app.log.debug, self.app.log.error)

    def generate_rest_geometry(self, geometry, tooldia, passes, overlap, invert, env_iso_type=2, negative_dia=None,
                               forced_rest=False,
                               prog_plot="normal", prog_plot_handler=None, plot=False):
        """
        Will try to isolate the geometry and return a tuple made of list of paths made through isolation
        and a list of Shapely Polygons that could not be isolated

        :param geometry:            A list of Shapely Polygons to be isolated
        :type geometry:             list
        :param tooldia:             The tool diameter used to do the isolation
        :type tooldia:              float
        :param passes:              Number of passes that will made the isolation
        :type passes:               int
        :param overlap:             How much to overlap the previous pass; in percentage [0.00, 99.99]%
        :type overlap:              float
        :param invert:              If to invert the direction of the resulting isolated geometries
        :type invert:               bool
        :param env_iso_type:        can be either 0 = keep exteriors or 1 = keep interiors or 2 = keep all paths
        :type env_iso_type:         int
        :param negative_dia:        isolate the geometry with a negative value for the tool diameter
        :type negative_dia:         bool
        :param forced_rest:         isolate the polygon even if the interiors can not be isolated
        :type forced_rest:          bool
        :param prog_plot:           kind of plotting: "progressive" or "normal"
        :type prog_plot:            str
        :param prog_plot_handler:   method used to plot shapes if plot_prog is "proggressive"
        :type prog_plot_handler:
        :param plot:                If True plot
        :type plot:                 bool
        :return:                    Tuple made from list of isolating paths and list of not isolated Polygons
        :rtype:                     tuple
        """

        return IsolationGen.generate_rest_geometry(
            geometry, tooldia, passes, overlap, invert, env_iso_type=env_iso_type, negative_dia=negative_dia,
            forced_rest=forced_rest, prog_plot=prog_plot, prog_plot_handler=prog_plot_handler, plot=plot,
            error_handler=self.app.log.error)

    @staticmethod
    def get_selected_interior(poly: Polygon, point: tuple) -> [Polygon, None]:
        return IsolationGen.get_selected_interior(poly, point)

    def find_polygon_ignore_interiors(self, point, geoset=None):
        """
        Find an object that object.contains(Point(point)) in
        poly, which can can be iterable, contain iterable of, or
        be itself an implementer of .contains(). Will test the Polygon as it is full with no interiors.

        :param point: See description
        :param geoset: a polygon or list of polygons where to find if the param point is contained
        :return: Polygon containing point or None.
        """

        if geoset is None:
            geoset = self.solid_geometry

        # a MultiPolygon is not iterable in Shapely 2: hand over its Polygons
        return IsolationGen.find_polygon_ignore_interiors(point, list(self._flat_polygons(geoset)))

    def flatten_list(self, obj_list):
        yield from IsolationGen.flatten_list(obj_list)
