# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Modified: Marius Adrian Stanciu (c)                 #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui  # noqa
from PyQt6.QtCore import Qt     # noqa

from appPlugins.ToolPaint.PaintUI import PaintUI
from appPlugins.ToolPaint.PaintGen import PaintGen
from appTool import AppTool
from appGUI.GUIElements import (
    VerticalScrollArea,
    FCComboBox,
    FCCheckBox,
    RadioSet,
    FCDoubleSpinner,
    FCInputDoubleSpinner,
)


from contextlib import contextmanager
from copy import deepcopy
import math
import numpy as np
from numpy import inf as Inf    # noqa

from shapely import Polygon

import gettext
import appTranslation as fcTranslate
import builtins

from appParsers.ParseGerber import Gerber
from appDatabase import load_tools_database, tools_database_error_message
from defaults import AppDefaults
from appCommon.SafeEval import safe_eval
from camlib import (
    deepcopy_keep_geometry,
    Geometry,
    grace,
    flatten_shapely_geometry,
)

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


@contextmanager
def _signals_blocked(obj):
    """Block the signals of obj and restore them on every exit path."""
    obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(False)


def _valid_tooldia(value):
    """Return the value as a float if it is a usable tool diameter (finite and > 0), else None."""
    try:
        dia = float(value)
    except (TypeError, ValueError):
        return None
    return dia if math.isfinite(dia) and dia > 0 else None


def tool_rows_order(paint_tools, order, decimals):
    """
    The tool uids in the order they are shown in the Tools table.

    :param paint_tools: the tools dict, keys are the tool uids
    :param order:       0 = Default (uid order), 1 = Forward (small to large), 2 = Reverse
    :param decimals:    diameters are compared rounded to this many decimals
    """
    uids = sorted(paint_tools, key=int)
    if order in (1, 2):
        uids.sort(key=lambda uid: float('%.*f' % (decimals, float(paint_tools[uid]['tooldia']))),
                  reverse=(order == 2))
    return uids


class ToolPaint(Geometry, AppTool):

    # (job token, result): the preview job hands the area to paint (or an error text) to the GUI thread
    preview_done_sig = QtCore.pyqtSignal(int, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        # no fixed steps: the circles follow the preferences "Circles" of Geometry (PaintGen._steps_at())
        Geometry.__init__(self, app=app)

        # #############################################################################
        # ########################## VARIABLES ########################################
        # #############################################################################

        self.obj_name = ""
        self.paint_obj = None
        self.bound_obj_name = ""
        self.bound_obj = None

        self.tooldia_list = []
        self.tooldia = None

        self.o_name = None
        self.overlap = None
        self.connect = None
        self.contour = None
        self.select_method = None

        self.units = ''
        self.paint_tools = {}
        self.tooluid = 0

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False


        # disconnect flags
        self.area_sel_disconnect_flag = False
        self.poly_sel_disconnect_flag = False

        self.area_to_paint_list = []

        # store here if the grid snapping is active
        self.grid_status_memory = False

        # dict to store the polygons selected for painting; key is the shape added to be plotted and value is the poly
        self.poly_dict = {}

        # the zones of the last Area Selection run and the polygons of the last Polygon Selection run:
        # the Preview of these modes uses them
        self.last_zones: list[Polygon] = []
        self.last_polygons: list[Polygon] = []
        # the shape collection of the preview, made on the first use
        self._preview_shapes = None
        self._preview_drawn = False
        # a preview result is drawn only if no clear_preview() happened since its job started
        self._preview_token = 0

        # store here the default data for Geometry Data
        self.default_data = {}

        self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

        # store here the points for the "Polygon" area selection shape
        self.points = []
        # set this as True when in middle of drawing a "Polygon" area selection shape
        # it is made False by first click to signify that the shape is complete
        self.poly_drawn = False

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = PaintUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self.form_fields = {
            "tools_paint_overlap":   self.ui.overlap_entry,
            "tools_paint_offset":    self.ui.offset_entry,
            "tools_paint_method":    self.ui.method_combo,
            "tools_paint_crosshatch_angle": self.ui.crosshatch_angle_entry,
            "tools_paint_connect":    self.ui.connect_cb,
            "tools_paint_contour":   self.ui.contour_cb,
            "tools_paint_final_cleanup": self.ui.final_cleanup_cb,
        }

        self.name2option = {
            'p_overlap':    "tools_paint_overlap",
            'p_offset':     "tools_paint_offset",
            'p_method':     "tools_paint_method",
            'p_crosshatch_angle': "tools_paint_crosshatch_angle",
            'p_connect':    "tools_paint_connect",
            'p_contour':    "tools_paint_contour",
            'p_final_cleanup': "tools_paint_final_cleanup",
        }

        # #############################################################################
        # ###################### Setup CONTEXT MENU ###################################
        # #############################################################################
        self.init_context_menu()

        # #############################################################################
        # Geometry generator
        # Instantiated in the set_tool_ui() method
        # #############################################################################
        self.gen: PaintGen | None = None

    def on_type_obj_changed(self, val):
        obj_type = 0 if val == 'gerber' else 2
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.obj_combo.setCurrentIndex(0)
        self.ui.obj_combo.obj_type = {"gerber": "Gerber", "geometry": "Geometry"}[val]

        idx = self.ui.method_combo.findText(_("Laser_lines"))
        if self.ui.type_obj_radio.get_value().lower() == 'gerber':
            self.ui.method_combo.model().item(idx).setEnabled(True)
        else:
            self.ui.method_combo.model().item(idx).setEnabled(False)
            if self.ui.method_combo.get_value() == idx:    # if its Laser Lines
                self.ui.method_combo.set_value(idx+1)

    def on_reference_combo_changed(self):
        obj_type = self.ui.reference_type_combo.currentIndex()
        self.ui.reference_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.reference_combo.setCurrentIndex(0)
        self.ui.reference_combo.obj_type = {0: "Gerber", 1: "Excellon", 2: "Geometry"}[obj_type]

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+P', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolPaint()")

        # AppTool.run() does nothing while the Plugin tab is locked; do not touch the tabs or the UI either
        if self.app.plugin_tab_locked is True:
            return

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
        self.build_ui()

        # all the tools are selected by default
        self.ui.tools_table.selectAll()
        self.update_ui()

        self.app.ui.notebook.setTabText(2, _("Paint"))

    def clear_context_menu(self):
        self.ui.tools_table.removeContextMenu()

    def init_context_menu(self):
        self.ui.tools_table.setupContextMenu()
        self.ui.tools_table.addContextMenu(
            _("Add"), self.on_add_tool_by_key, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Add from DB"), self.on_paint_tool_add_from_db_clicked, icon=QtGui.QIcon(self.app.resource_location + "/plus16.png")
        )
        self.ui.tools_table.addContextMenu(
            _("Delete"), lambda:
            self.on_tool_delete(rows_to_delete=None, all_tools=None),
            icon=QtGui.QIcon(self.app.resource_location + "/delete32.png")
        )

    def connect_signals(self):
        # #############################################################################
        # ################################# Signals ###################################
        # #############################################################################
        try:
            self.ui.level.toggled.disconnect(self.on_level_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.level.toggled.connect(self.on_level_changed)

        try:
            self.ui.new_tooldia_entry.returnPressed.disconnect(self.on_tool_add)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.new_tooldia_entry.returnPressed.connect(self.on_tool_add)

        try:
            self.ui.deltool_btn.clicked.disconnect(self.on_tool_delete)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.deltool_btn.clicked.connect(self.on_tool_delete)

        try:
            self.ui.generate_button.clicked.disconnect(self.gen.on_paint_button_click)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.generate_button.clicked.connect(self.gen.on_paint_button_click)

        try:
            self.ui.select_method_combo.currentIndexChanged.disconnect(self.ui.on_selection)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.select_method_combo.currentIndexChanged.connect(self.ui.on_selection)

        try:
            self.ui.method_combo.currentIndexChanged.disconnect(self.ui.on_method_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.method_combo.currentIndexChanged.connect(self.ui.on_method_changed)

        try:
            self.ui.reference_type_combo.currentIndexChanged.disconnect(self.on_reference_combo_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.reference_type_combo.currentIndexChanged.connect(self.on_reference_combo_changed)

        try:
            self.ui.type_obj_radio.activated_custom.disconnect(self.on_type_obj_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.type_obj_radio.activated_custom.connect(self.on_type_obj_changed)

        try:
            self.ui.apply_param_to_all.clicked.disconnect(self.on_apply_param_to_all_clicked)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.apply_param_to_all.clicked.connect(self.on_apply_param_to_all_clicked)

        try:
            self.ui.search_and_add_btn.clicked.disconnect(self.on_tool_add)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.search_and_add_btn.clicked.connect(lambda: self.on_tool_add())

        try:
            self.ui.addtool_from_db_btn.clicked.disconnect(self.on_paint_tool_add_from_db_clicked)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.addtool_from_db_btn.clicked.connect(self.on_paint_tool_add_from_db_clicked)

        try:
            self.app.proj_selection_changed.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

        try:
            self.ui.reset_button.clicked.disconnect(self.set_tool_ui)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.ui.reset_button.clicked.connect(self.set_tool_ui)

        try:
            self.app.cleanup.disconnect(self.set_tool_ui)
        except (TypeError, RuntimeError, AttributeError):
            pass
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        self.app.cleanup.connect(self.set_tool_ui)

        for signal, slot in (
                (self.ui.preview_button.clicked, self.on_preview),
                (self.ui.obj_combo.currentIndexChanged, self.on_source_changed),
                (self.ui.select_method_combo.currentIndexChanged, self.clear_preview),
                (self.ui.multi_objects_cb.stateChanged, self.clear_preview),
                (self.preview_done_sig, self.on_preview_done)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError, AttributeError):
                pass
            signal.connect(slot)

    def set_tool_ui(self):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        # a Reset drops the preview and the zones / polygons of the last run
        self.clear_preview()
        self.last_zones = []
        self.last_polygons = []

        self.clear_ui(self.layout)
        self.ui = PaintUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        self.form_fields = {
            "tools_paint_overlap":   self.ui.overlap_entry,
            "tools_paint_offset":    self.ui.offset_entry,
            "tools_paint_method":    self.ui.method_combo,
            "tools_paint_crosshatch_angle": self.ui.crosshatch_angle_entry,
            "tools_paint_connect":    self.ui.connect_cb,
            "tools_paint_contour":   self.ui.contour_cb,
            "tools_paint_final_cleanup": self.ui.final_cleanup_cb,
        }

        self.clear_context_menu()
        self.init_context_menu()

        self.ui.tools_frame.show()
        self.reset_fields()

        # updated units
        self.units = self.app.app_units.upper()

        # set the working variables to a known state
        self.paint_tools.clear()
        self.tooluid = 0

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

        # self.default_data.clear()
        # self.default_data.update({
        #     "name":                 '_paint',
        #     "plot":                 self.app.options["geometry_plot"],
        #     "cutz":                 float(self.app.options["tools_paint_cutz"]),
        #     "vtipdia":              float(self.app.options["tools_paint_tipdia"]),
        #     "vtipangle":            float(self.app.options["tools_paint_tipangle"]),
        #     "travelz":              float(self.app.options["geometry_travelz"]),
        #     "feedrate":             float(self.app.options["geometry_feedrate"]),
        #     "feedrate_z":           float(self.app.options["geometry_feedrate_z"]),
        #     "feedrate_rapid":       float(self.app.options["geometry_feedrate_rapid"]),
        #     "dwell":                self.app.options["geometry_dwell"],
        #     "dwelltime":            float(self.app.options["geometry_dwelltime"]),
        #     "multidepth":           self.app.options["geometry_multidepth"],
        #     "ppname_g":             self.app.options["geometry_ppname_g"],
        #     "depthperpass":         float(self.app.options["geometry_depthperpass"]),
        #     "extracut":             self.app.options["geometry_extracut"],
        #     "extracut_length":      self.app.options["geometry_extracut_length"],
        #     "toolchange":           self.app.options["geometry_toolchange"],
        #     "toolchangez":          float(self.app.options["geometry_toolchangez"]),
        #     "endz":                 float(self.app.options["geometry_endz"]),
        #     "endxy":                self.app.options["geometry_endxy"],
        #
        #     "spindlespeed":         self.app.options["geometry_spindlespeed"],
        #     "toolchangexy":         self.app.options["geometry_toolchangexy"],
        #     "startz":               self.app.options["geometry_startz"],
        #
        #     "area_exclusion":       self.app.options["geometry_area_exclusion"],
        #     "area_shape":           self.app.options["geometry_area_shape"],
        #     "area_strategy":        self.app.options["geometry_area_strategy"],
        #     "area_overz":           float(self.app.options["geometry_area_overz"]),
        #     "optimization_type":    self.app.options["geometry_optimization_type"],
        #
        #     "tooldia":              self.app.options["tools_paint_tooldia"],
        #     "tools_paint_offset":   self.app.options["tools_paint_offset"],
        #     "tools_paint_method":    self.app.options["tools_paint_method"],
        #     "tools_paint_selectmethod":   self.app.options["tools_paint_selectmethod"],
        #     "tools_paint_connect":    self.app.options["tools_paint_connect"],
        #     "tools_paint_contour":   self.app.options["tools_paint_contour"],
        #     "tools_paint_overlap":   self.app.options["tools_paint_overlap"],
        #     "tools_paint_rest":      self.app.options["tools_paint_rest"],
        # })

        # ## Init the GUI interface
        self.ui.order_combo.set_value(self.app.options.get(
            "tools_paint_order", app_defaults.get("tools_paint_order", AppDefaults.factory_defaults.get("tools_paint_order"))))
        self.ui.offset_entry.set_value(self.app.options.get(
            "tools_paint_offset", app_defaults.get("tools_paint_offset", AppDefaults.factory_defaults.get("tools_paint_offset"))))
        self.ui.method_combo.set_value(self.app.options.get(
            "tools_paint_method", app_defaults.get("tools_paint_method", AppDefaults.factory_defaults.get("tools_paint_method"))))
        self.ui.crosshatch_angle_entry.set_value(self.app.options.get(
            "tools_paint_crosshatch_angle",
            app_defaults.get("tools_paint_crosshatch_angle",
                             AppDefaults.factory_defaults.get("tools_paint_crosshatch_angle", 0.0))))
        self.ui.on_method_changed()
        self.ui.select_method_combo.set_value(self.app.options.get(
            "tools_paint_selectmethod",
            app_defaults.get("tools_paint_selectmethod", AppDefaults.factory_defaults.get("tools_paint_selectmethod"))))
        self.ui.area_shape_radio.set_value(self.app.options.get(
            "tools_paint_area_shape",
            app_defaults.get("tools_paint_area_shape", AppDefaults.factory_defaults.get("tools_paint_area_shape"))))
        self.ui.connect_cb.set_value(self.app.options.get(
            "tools_paint_connect", app_defaults.get("tools_paint_connect", AppDefaults.factory_defaults.get("tools_paint_connect"))))
        self.ui.contour_cb.set_value(self.app.options.get(
            "tools_paint_contour", app_defaults.get("tools_paint_contour", AppDefaults.factory_defaults.get("tools_paint_contour"))))
        self.ui.final_cleanup_cb.set_value(self.app.options.get(
            "tools_paint_final_cleanup", app_defaults.get("tools_paint_final_cleanup", False)))
        self.default_data['tools_paint_final_cleanup'] = self.ui.final_cleanup_cb.get_value()
        self.ui.overlap_entry.set_value(self.app.options.get(
            "tools_paint_overlap", app_defaults.get("tools_paint_overlap", AppDefaults.factory_defaults.get("tools_paint_overlap"))))

        self.ui.new_tooldia_entry.set_value(self.app.options.get(
            "tools_paint_newdia", app_defaults.get("tools_paint_newdia", AppDefaults.factory_defaults.get("tools_paint_newdia"))))
        self.ui.rest_cb.set_value(self.app.options.get(
            "tools_paint_rest", app_defaults.get("tools_paint_rest", AppDefaults.factory_defaults.get("tools_paint_rest"))))
        self.ui.rest_offset_entry.set_value(self.app.options.get(
            "tools_paint_rest_offset",
            app_defaults.get("tools_paint_rest_offset", AppDefaults.factory_defaults.get("tools_paint_rest_offset"))))
        self.ui.multi_objects_cb.set_value(self.app.options.get(
            "tools_paint_multi_objects",
            app_defaults.get("tools_paint_multi_objects", AppDefaults.factory_defaults.get("tools_paint_multi_objects"))))
        self.ui.valid_cb.set_value(self.app.options.get(
            "tools_paint_check_valid",
            app_defaults.get("tools_paint_check_valid", AppDefaults.factory_defaults.get("tools_paint_check_valid"))))

        # # make the default object type, "Geometry"
        # self.type_obj_radio.set_value("geometry")

        # use the current selected object and make it visible in the Paint object combobox
        sel_list = self.app.collection.get_selected()
        if len(sel_list) == 1:
            active = self.app.collection.get_active()
            kind = active.kind
            if kind == 'gerber':
                self.ui.type_obj_radio.set_value(kind)
            else:
                kind = 'geometry'
                self.ui.type_obj_radio.set_value(kind)

            # run those once so the obj_type attribute is updated in the FCComboBoxes
            # to make sure that the last loaded object is displayed in the combobox
            self.on_type_obj_changed(val=kind)
            self.on_reference_combo_changed()

            self.ui.obj_combo.set_value(active.obj_options['name'])
        else:
            kind = 'geometry'
            self.ui.type_obj_radio.set_value('geometry')

            # run those once so the obj_type attribute is updated in the FCComboBoxes
            # to make sure that the last loaded object is displayed in the combobox
            self.on_type_obj_changed(val=kind)
            self.on_reference_combo_changed()

        tool_dia = self.app.options.get(
            "tools_paint_tooldia", app_defaults.get("tools_paint_tooldia", AppDefaults.factory_defaults.get("tools_paint_tooldia")))
        try:
            diameters = [float(tool_dia)]
        except (ValueError, TypeError):
            if isinstance(tool_dia, str):
                diameters = [safe_eval(x) for x in tool_dia.split(",") if x != '']
            else:
                diameters = tool_dia

        if not diameters:
            self.app.log.error(
                "At least one tool diameter needed. Verify in Edit -> Preferences -> Plugins -> Paint Tools.")
            self.build_ui()
        else:
            for dia in diameters:
                self.on_tool_add(custom_dia=dia)

        self.ui.on_rest_machining_check(state=self.app.options.get(
            "tools_paint_rest", app_defaults.get("tools_paint_rest", AppDefaults.factory_defaults.get("tools_paint_rest"))))

        # make sure that we can't get selection of Laser Lines for Geometry even if it's set in the Preferences
        # because we don't select the default object type in Preferences but here
        idx = self.ui.method_combo.findText(_("Laser_lines"))
        if self.ui.type_obj_radio.get_value().lower() == 'gerber':
            self.ui.method_combo.model().item(idx).setEnabled(True)
        else:
            self.ui.method_combo.model().item(idx).setEnabled(False)
            if self.ui.method_combo.get_value() == idx:  # if its Laser Lines
                self.ui.method_combo.set_value(idx + 1)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        # if the Paint Method is "Polygon Selection" disable the tool table context menu
        # (done after the level is applied because the Advanced level restores the menu)
        if self.default_data.get("tools_paint_selectmethod") == 1:
            self.ui.tools_table.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # forget any half-made selection of the previous session
        self.reset_selection_state()
        self.poly_dict.clear()
        self.area_to_paint_list = []

        self.ui.tools_table.drag_drop_sig.connect(self.rebuild_ui)

        # #############################################################################
        # Geometry generator
        # #############################################################################
        self.gen = PaintGen(self)
        self.connect_signals()

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

            # Tool parameters section: the generator reads the checkbox, so Beginner must not leave it checked
            self.ui.rest_cb.set_value(False)
            self.ui.rest_cb.hide()

            # the generator reads the checkbox, so Beginner must not leave it checked
            self.ui.valid_cb.set_value(False)
            self.ui.valid_cb.hide()

            # All param section
            self.ui.apply_param_to_all.hide()

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

            # Tool parameters section: restore the app option for the checkbox
            app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
            self.ui.rest_cb.set_value(self.app.options.get(
                "tools_paint_rest",
                app_defaults.get("tools_paint_rest", AppDefaults.factory_defaults.get("tools_paint_rest"))))
            self.ui.rest_cb.show()

            self.ui.valid_cb.set_value(self.app.options.get(
                "tools_paint_check_valid",
                app_defaults.get("tools_paint_check_valid", AppDefaults.factory_defaults.get("tools_paint_check_valid"))))
            self.ui.valid_cb.show()

            # All param section
            self.ui.apply_param_to_all.show()

            # Context Menu section
            self.ui.tools_table.setupContextMenu()

    def on_toggle_all_rows(self):
        """
        will toggle the selection of all rows in Tools table

        :return:
        """
        sel_model = self.ui.tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns, too but I'm interested only on rows
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

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        # update UI only if only one row is selected otherwise having multiple rows selected will deform information
        # for the rows other that the current one (first selected)
        if len(sel_rows) == 1:
            self.update_ui()

    def on_object_selection_changed(self, current):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                name = current.indexes()[0].internalPointer().obj.obj_options['name']
                kind = current.indexes()[0].internalPointer().obj.kind

                if kind in ['gerber', 'geometry']:
                    self.ui.type_obj_radio.set_value(kind)

                self.ui.obj_combo.set_value(name)
            except Exception:
                pass

    def update_ui(self):
        with _signals_blocked(self):
            sel_rows = set()
            table_items = self.ui.tools_table.selectedItems()
            if table_items:
                for it in table_items:
                    sel_rows.add(it.row())

            if not sel_rows:
                self.ui.generate_button.setDisabled(True)
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
                )
                return
            else:
                self.ui.generate_button.setDisabled(False)

            for current_row in sel_rows:
                # populate the form with the data from the tool associated with the row parameter
                try:
                    item = self.ui.tools_table.item(current_row, 3)
                    if item is None:
                        return 'fail'
                    tooluid = int(item.text())
                except Exception as e:
                    self.app.log.error("Tool missing. Add a tool in the Tool Table. %s" % str(e))
                    return

                # update the QLabel that shows for which Tool we have the parameters in the UI form
                if len(sel_rows) == 1:
                    cr = self.ui.tools_table.item(current_row, 0).text()
                    self.ui.tool_data_label.setText(
                        "<b>%s: <font color='#0000FF'>%s %s</font></b>" % (_('Parameters for'), _("Tool"), cr)
                    )

                    try:
                        # set the form with data from the newly selected tool
                        for tooluid_key, tooluid_value in list(self.paint_tools.items()):
                            if int(tooluid_key) == tooluid:
                                self.storage_to_form(tooluid_value['data'])
                    except Exception as e:
                        self.app.log.error("ToolPaint ---> update_ui() " + str(e))
                else:
                    self.ui.tool_data_label.setText(
                        "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
                    )

    def storage_to_form(self, dict_storage):
        dict_storage.setdefault('tools_paint_final_cleanup', False)
        dict_storage.setdefault('tools_paint_crosshatch_angle', 0.0)
        for k in self.form_fields:
            try:
                self.form_fields[k].set_value(dict_storage[k])
            except Exception as err:
                self.app.log.error("ToolPaint.storage.form() --> %s" % str(err))
        self.ui.on_method_changed()

    def form_to_storage(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            return

        with _signals_blocked(self):
            widget_changed = self.sender()
            wdg_objname = widget_changed.objectName()
            option_changed = self.name2option[wdg_objname]

            rows = sorted(set(index.row() for index in self.ui.tools_table.selectedIndexes()))
            for row in rows:
                if row < 0:
                    row = 0
                tooluid_item = int(self.ui.tools_table.item(row, 3).text())

                for tooluid_key, tooluid_val in self.paint_tools.items():
                    if int(tooluid_key) == tooluid_item:
                        new_option_value = self.form_fields[option_changed].get_value()
                        if option_changed in tooluid_val:
                            tooluid_val[option_changed] = new_option_value
                        if option_changed in tooluid_val['data']:
                            tooluid_val['data'][option_changed] = new_option_value

    def on_apply_param_to_all_clicked(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            self.app.log.debug("ToolPaint.on_apply_param_to_all_clicked() --> no tool in Tools Table, aborting.")
            return

        with _signals_blocked(self):
            # the form shows the parameters of the selected row
            sel_rows = sorted({it.row() for it in self.ui.tools_table.selectedItems()})
            row = sel_rows[0] if sel_rows else self.ui.tools_table.currentRow()
            if row < 0:
                row = 0

            tooluid_item = int(self.ui.tools_table.item(row, 3).text())
            source_data = None
            for tooluid_key, tooluid_val in self.paint_tools.items():
                if int(tooluid_key) == tooluid_item:
                    source_data = tooluid_val['data']
                    break
            if source_data is None:
                return

            # copy only the parameters edited in the form, not the tool shape or the DB-derived keys
            params = {key: deepcopy(source_data[key]) for key in self.form_fields if key in source_data}
            for tooluid_val in self.paint_tools.values():
                tooluid_val['data'].update(deepcopy(params))

        self.build_ui()
        self.app.inform.emit('[success] %s' % _("Current Tool parameters were applied to all tools."))

    def on_add_tool_by_key(self):
        tool_add_popup = FCInputDoubleSpinner(title='%s...' % _("New Tool"),
                                              text='%s:' % _('Enter a Tool Diameter'),
                                              min=0.0000, max=99.9999, decimals=self.decimals,
                                              parent=self.app.ui)
        tool_add_popup.set_icon(QtGui.QIcon(self.app.resource_location + '/letter_t_32.png'))

        val, ok = tool_add_popup.get_value()
        if ok:
            if float(val) == 0:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Please enter a tool diameter with non-zero value, in Float format."))
                return
            self.on_tool_add(custom_dia=float(val))
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _("Adding Tool cancelled"))

    def on_tooltable_cell_widget_change(self):
        cw = self.sender()

        assert isinstance(cw, QtWidgets.QComboBox), \
            "Expected a QtWidgets.QComboBox, got %s" % isinstance(cw, QtWidgets.QComboBox)

        cw_index = self.ui.tools_table.indexAt(cw.pos())
        cw_row = cw_index.row()
        cw_col = cw_index.column()

        current_uid = int(self.ui.tools_table.item(cw_row, 3).text())

        # if the sender is in the column with index 2 then we update the tool_type key
        if cw_col == 2 and current_uid in self.paint_tools:
            tt = cw.currentText()
            typ = 'Iso' if tt == 'V' else 'Rough'

            self.paint_tools[current_uid].update({
                'type': typ,
                'tool_type': tt,
            })
            # build_ui() and the output read the tool shape from the tool data
            self.paint_tools[current_uid]['data']['tools_mill_tool_shape'] = cw.currentIndex()

    def on_order_changed(self, order):
        self.build_ui()

    def rebuild_ui(self):
        # read the table tools uid
        current_uid_list = []
        for row in range(self.ui.tools_table.rowCount()):
            uid = int(self.ui.tools_table.item(row, 3).text())
            current_uid_list.append(uid)

        new_tools = {}
        new_uid = 1

        for current_uid in current_uid_list:
            new_tools[new_uid] = deepcopy_keep_geometry(self.paint_tools[current_uid])
            new_uid += 1

        # keep the same dict object: PaintGen shares it
        self.paint_tools.clear()
        self.paint_tools.update(new_tools)

        # the tools table changed therefore we need to rebuild it
        QtCore.QTimer.singleShot(20, self.build_ui)

    def build_ui(self):
        self.ui_disconnect()

        # updated units
        self.units = self.app.app_units.upper()

        order = self.ui.order_combo.get_value()
        uid_order = tool_rows_order(self.paint_tools, order, self.decimals)

        n = len(uid_order)
        self.ui.tools_table.setRowCount(n)
        tool_id = 0

        selected_enabled_flag = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled   # noqa
        enabled_flag = QtCore.Qt.ItemFlag.ItemIsEnabled
        selected_enabled_editable_flag = (
                QtCore.Qt.ItemFlag.ItemIsEditable
                | QtCore.Qt.ItemFlag.ItemIsSelectable   # noqa
                | QtCore.Qt.ItemFlag.ItemIsEnabled
        )

        for tooluid_key in uid_order:
            tooluid_value = self.paint_tools[tooluid_key]
            tool_id += 1

            id_item = QtWidgets.QTableWidgetItem('%d' % int(tool_id))
            id_item.setFlags(selected_enabled_flag)
            row_no = tool_id - 1
            self.ui.tools_table.setItem(row_no, 0, id_item)  # Tool name/id

            dia = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, tooluid_value['tooldia']))
            dia.setFlags(enabled_flag)
            self.ui.tools_table.setItem(row_no, 1, dia)  # Diameter

            tool_type_item = FCComboBox()
            for item in self.tool_type_item_options:
                tool_type_item.addItem(item)
            idx = int(tooluid_value['data'].get('tools_mill_tool_shape', 0))
            tool_type_item.setCurrentIndex(idx)

            tool_uid_item = QtWidgets.QTableWidgetItem(str(int(tooluid_key)))
            self.ui.tools_table.setCellWidget(row_no, 2, tool_type_item)
            # ## REMEMBER: THIS COLUMN IS HIDDEN IN OBJECTUI.PY # ##
            self.ui.tools_table.setItem(row_no, 3, tool_uid_item)  # Tool unique ID

        # make the diameter column editable
        for row in range(tool_id):
            self.ui.tools_table.item(row, 1).setFlags(selected_enabled_editable_flag)

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

        # the table rebuild resets the selected tools and drop-down rows: the area to paint may have changed
        self.clear_preview()

    def on_tool_add(self, custom_dia=None):
        filename = self.app.tools_database_path()

        new_tools_dict = deepcopy(self.default_data)
        updated_tooldia = None

        # construct a list of all 'tooluid' in the self.paint_tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.paint_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        tooluid = int(max_uid + 1)

        tool_dias = []
        for k, v in self.paint_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(self.app.dec_format(v[tool_v], self.decimals))

        # determine the new tool diameter
        if custom_dia is None:
            tool_dia = _valid_tooldia(self.ui.new_tooldia_entry.get_value())
        else:
            tool_dia = _valid_tooldia(custom_dia)
        if tool_dia is None:
            self.build_ui()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            return
        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)

        # if new tool diameter already in the Tool List then abort
        if truncated_tooldia in tool_dias:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            return

        add_default_tool = False
        with _signals_blocked(self):
            try:
                tools_db_dict = load_tools_database(filename, self.app.options, target=4,
                                                   diameter=truncated_tooldia,
                                                   units=getattr(self.app, 'app_units', None))
            except (OSError, TypeError, ValueError) as error:
                self.app.log.error("Could not load tools DB file.")
                self.app.log.error(str(error))
                self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))
                tools_db_dict = {}
                add_default_tool = True

            if not add_default_tool:
                tool_found = len(tools_db_dict)

                offset = 'Path'
                offset_val = 0.0

                # look in database tools
                for db_tool, db_tool_val in tools_db_dict.items():
                    db_tooldia = db_tool_val['tooldia']
                    updated_tooldia = db_tooldia if db_tooldia != truncated_tooldia else None
                    offset = deepcopy(db_tool_val['data']['tools_mill_offset_type'])
                    offset_val = deepcopy(db_tool_val['data']['tools_mill_offset_value'])
                    for d in db_tool_val['data']:
                        if not d.startswith('tools_') or d.startswith(('tools_paint_', 'tools_mill_')):
                            new_tools_dict[d] = deepcopy(db_tool_val['data'][d])

                # test we found a suitable tool in Tools Database or if multiple ones
                if tool_found == 0:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tool not in Tools Database. Adding a default tool."))
                    add_default_tool = True
                elif tool_found > 1:
                    self.app.inform.emit(
                        '[WARNING_NOTCL] %s' % _("Cancelled.\n"
                                                 "Multiple tools for one tool diameter found in Tools Database."))
                    return
                # if new tool diameter found in Tools Database already in the Tool List then abort
                elif updated_tooldia is not None and updated_tooldia in tool_dias:
                    self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
                    return
                else:
                    new_tdia = deepcopy(updated_tooldia) if updated_tooldia is not None else deepcopy(truncated_tooldia)
                    self.paint_tools.update({
                        tooluid: {
                            'tooldia':          new_tdia,
                            'offset':           deepcopy(offset),
                            'offset_value':     deepcopy(offset_val),
                            'data':             deepcopy(new_tools_dict),
                            'solid_geometry':   []
                        }
                    })

        if add_default_tool:
            self.on_tool_default_add(dia=tool_dia)
            return

        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break

        # update the UI form
        self.update_ui()

        self.app.inform.emit('[success] %s' % _("New tool added to Tool Table from Tools Database."))

    def on_tool_default_add(self, dia=None, muted=None):
        self.units = self.app.app_units.upper()

        tool_dia = _valid_tooldia(dia if dia is not None else self.ui.new_tooldia_entry.get_value())
        if tool_dia is None:
            self.build_ui()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            return

        # construct a list of all 'tooluid' in the self.tools
        tool_uid_list = [int(tooluid_key) for tooluid_key in self.paint_tools]

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        max_uid = 0 if not tool_uid_list else max(tool_uid_list)
        self.tooluid = int(max_uid + 1)

        tool_dias = []
        for k, v in self.paint_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(self.app.dec_format(v[tool_v], self.decimals))

        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)
        if truncated_tooldia in tool_dias:
            if muted is None:
                self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            return

        with _signals_blocked(self):
            self.paint_tools.update({
                int(self.tooluid): {
                    'tooldia':          truncated_tooldia,
                    'data':             deepcopy(self.default_data),
                    'solid_geometry':   []
                }
            })

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
        edited_row = item.row()
        editeduid = int(self.ui.tools_table.item(edited_row, 3).text())

        with _signals_blocked(self):
            text = self.ui.tools_table.item(edited_row, 1).text()
            try:
                new_tool_dia = float(text)
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    new_tool_dia = float(text.replace(',', '.'))
                except ValueError:
                    new_tool_dia = None
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))

            if new_tool_dia is not None:
                if _valid_tooldia(new_tool_dia) is None:
                    new_tool_dia = None
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                                "in Float format."))
                else:
                    new_tool_dia = float('%.*f' % (self.decimals, new_tool_dia))
                    # the diameters of all the other tools
                    other_dias = [float('%.*f' % (self.decimals, float(v['tooldia'])))
                                  for k, v in self.paint_tools.items() if int(k) != editeduid]
                    if new_tool_dia in other_dias:
                        new_tool_dia = None
                        self.app.inform.emit(
                            '[WARNING_NOTCL] %s' % _("Cancelled. New diameter value is already in the Tool Table."))

            if new_tool_dia is not None and editeduid in self.paint_tools:
                self.paint_tools[editeduid]['tooldia'] = new_tool_dia
                self.app.inform.emit('[success] %s' % _("Tool from Tool Table was edited."))

        # rebuilding the table also restores the old diameter in the cell when the edit was rejected
        self.build_ui()

    def on_tool_delete(self, rows_to_delete=None, all_tools=None):
        """
        Will delete a tool in the tool table

        :param rows_to_delete:  which rows to delete; a row number or a list of row numbers
        :param all_tools:       delete all tools in the tool table
        :return:
        """
        deleted_tools_list = []

        with _signals_blocked(self):
            if all_tools:
                self.paint_tools.clear()
            else:
                if rows_to_delete is not None and not isinstance(rows_to_delete, bool):
                    if isinstance(rows_to_delete, (int, np.integer)):
                        rows_to_delete = [rows_to_delete]
                    rows = list(rows_to_delete)
                else:
                    # clicked() passes a bool here, the context menu passes None: use the selected rows
                    rows = sorted({it.row() for it in self.ui.tools_table.selectedItems()})

                for row in rows:
                    if row is None or row < 0:
                        continue
                    try:
                        deleted_tools_list.append(int(self.ui.tools_table.item(row, 3).text()))
                    except (AttributeError, ValueError):
                        continue

                if not deleted_tools_list:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Delete failed. Select a tool to delete."))
                    return

                for t in deleted_tools_list:
                    self.paint_tools.pop(t, None)

        if not all_tools:
            self.app.inform.emit('[success] %s' % _("Tools deleted from Tool Table."))
        self.build_ui()

    # To be called after clicking on the plot.
    def on_single_poly_mouse_release(self, event):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        event_pos = event.pos
        right_button = 2
        event_is_dragging = self.app.event_is_dragging

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        event_pos = (x, y)
        curr_pos = self.app.plotcanvas.translate_coords(event_pos)

        # do paint single only for left mouse clicks
        if event.button == 1:
            # Gerber is a Geometry subclass, so both kinds are flattened: a MultiPolygon is many polygons
            if isinstance(self.paint_obj, (Gerber, Geometry)):
                paint_geo = flatten_shapely_geometry(self.paint_obj.solid_geometry)
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The selected object is not a Gerber or Geometry object."))
                return None
            clicked_poly = self.find_polygon(point=(curr_pos[0], curr_pos[1]), geoset=paint_geo)

            if clicked_poly:
                if clicked_poly not in self.poly_dict.values():
                    shape_id = self.app.tool_shapes.add(tolerance=self.paint_obj.drawing_tolerance,
                                                        layer=0,
                                                        shape=clicked_poly,
                                                        color=self.app.options.get(
                                                            'global_sel_draw_color',
                                                            app_defaults.get('global_sel_draw_color',
                                                                             AppDefaults.factory_defaults.get('global_sel_draw_color'))) + 'AF',
                                                        face_color=self.app.options.get(
                                                            'global_sel_draw_color',
                                                            app_defaults.get('global_sel_draw_color',
                                                                             AppDefaults.factory_defaults.get('global_sel_draw_color'))) + 'AF',
                                                        visible=True)
                    self.poly_dict[shape_id] = clicked_poly
                    self.app.inform.emit(
                        '%s: %d. %s' % (_("Added polygon"),
                                        int(len(self.poly_dict)),
                                        _("Click to add next polygon or right click to start."))
                    )
                else:
                    try:
                        for k, v in list(self.poly_dict.items()):
                            if v == clicked_poly:
                                self.app.tool_shapes.remove(k)
                                self.poly_dict.pop(k)
                                break
                    except TypeError:
                        return
                    self.app.inform.emit(
                        '%s. %s' % (_("Removed polygon"),
                                    _("Click to add/remove next polygon or right click to start."))
                    )

                self.app.tool_shapes.redraw()
            else:
                self.app.inform.emit(_("No polygon detected under click position."))

        elif event.button == right_button and event_is_dragging is False:
            # restore the Grid snapping if it was active before
            if self.grid_status_memory is True:
                self.app.ui.grid_snap_btn.trigger()

            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_single_poly_mouse_release)
            self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

            self.app.plotcanvas.graph_event_connect('mouse_press',
                                                                  self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release',
                                                                  self.app.on_mouse_click_release_over_plot)

            # disconnect flags
            self.poly_sel_disconnect_flag = False

            self.app.tool_shapes.clear(update=True)

            if self.poly_dict:
                poly_list = deepcopy_keep_geometry(list(self.poly_dict.values()))
                self.last_polygons = list(poly_list)
                self.gen.paint_poly(self.paint_obj, inside_pt=(curr_pos[0], curr_pos[1]), poly_list=poly_list,
                                tooldia=self.tooldia_list)
                self.poly_dict.clear()
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("List of single polygons is empty. Aborting."))

    # To be called after clicking on the plot.
    def on_mouse_release(self, event):
        event_pos = event.pos
        # event_is_dragging = event.is_dragging
        right_button = 2

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        event_pos = (x, y)

        shape_type = self.ui.area_shape_radio.get_value()

        curr_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(curr_pos[0], curr_pos[1])

        x1, y1 = curr_pos[0], curr_pos[1]

        # do paint single only for left mouse clicks
        if event.button == 1:
            if shape_type == "square":
                if not self.first_click:
                    self.first_click = True
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the area."))

                    self.cursor_pos = self.app.plotcanvas.translate_coords(event_pos)
                    if self.app.grid_status():
                        self.cursor_pos = self.app.geo_editor.snap(self.cursor_pos[0], self.cursor_pos[1])
                else:
                    self.app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    self.app.delete_selection_shape()

                    x0, y0 = self.cursor_pos[0], self.cursor_pos[1]
                    pt1 = (x0, y0)
                    pt2 = (x1, y0)
                    pt3 = (x1, y1)
                    pt4 = (x0, y1)

                    self.area_to_paint_list.append(
                        Polygon(
                            [pt1, pt2, pt3, pt4]
                        )
                    )

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
                            self.area_to_paint_list.append(pol)
                            self.draw_selection_shape_polygon(points=self.points)
                            self.app.inform.emit(
                                _("Zone added. Click to start adding next zone or right click to finish."))

                    self.points.clear()
                    self.poly_drawn = False
                    return

            # the selection is finished (or abandoned with a single point): clear the rubber band, the zone
            # outlines and the click state
            self.reset_selection_state()

            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

            self.app.plotcanvas.graph_event_connect('mouse_press',
                                                                  self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_move',
                                                                  self.app.on_mouse_move_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release',
                                                                  self.app.on_mouse_click_release_over_plot)

            # disconnect flags
            self.area_sel_disconnect_flag = False
            self.app.ui.notebook.setDisabled(False)

            if len(self.area_to_paint_list) == 0:
                return

            self.last_zones = list(self.area_to_paint_list)
            self.gen.paint_area_selection_option(self.area_to_paint_list)

    # called on mouse move
    def on_mouse_move(self, event):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        shape_type = self.ui.area_shape_radio.get_value()

        event_pos = event.pos
        event_is_dragging = event.is_dragging

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return

        curr_pos = self.app.plotcanvas.translate_coords((x, y))

        # detect mouse dragging motion
        if event_is_dragging == 1:
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
                                             app_defaults.get("global_cursor_width",
                                                              AppDefaults.factory_defaults.get("global_cursor_width"))),
                                         size=self.app.options.get(
                                             "global_cursor_size",
                                             app_defaults.get("global_cursor_size",
                                                              AppDefaults.factory_defaults.get("global_cursor_size"))))

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
        if isinstance(event, int):
            key = event
        # events from the GUI are of type QKeyEvent
        elif isinstance(event, QtGui.QKeyEvent):
            key = event.key()

        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':
            self.end_selection()

    def end_selection(self):
        """
        Leave the area / polygon selection mode: give the canvas back to the app, enable the panel and forget the
        half-made selection. Used by the Escape key and when the plugin closes.
        """
        if self.area_sel_disconnect_flag is True:
            try:
                self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
                self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
                self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
            except Exception as e:
                self.app.log.error("ToolPaint.end_selection() _1 --> %s" % str(e))

            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

            self.area_sel_disconnect_flag = False
            self.app.ui.notebook.setDisabled(False)

        if self.poly_sel_disconnect_flag is True:
            try:
                # restore the Grid snapping if it was active before
                if self.grid_status_memory is True:
                    self.app.ui.grid_snap_btn.trigger()

                self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_single_poly_mouse_release)
                self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

                self.app.tool_shapes.clear(update=True)
            except Exception as e:
                self.app.log.error("ToolPaint.end_selection() _2 --> %s" % str(e))

            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.ui.notebook.setDisabled(False)

            self.poly_sel_disconnect_flag = False

        self.poly_dict.clear()
        self.reset_selection_state()

    def on_plugin_cleanup(self):
        """The plugin is closing: do not leave the canvas in the selection mode, take the preview off the canvas."""
        self.end_selection()
        self.clear_preview()

    # ------------------------------------------------------------------------------------------------------------
    # the preview of the area to paint
    # ------------------------------------------------------------------------------------------------------------
    def selected_tool_uids(self):
        """The uids of the tools selected in the Tools table."""
        uids = []
        for row in sorted({item.row() for item in self.ui.tools_table.selectedItems()}):
            try:
                uid = int(self.ui.tools_table.item(row, 3).text())
            except (AttributeError, ValueError):
                continue
            if uid in self.paint_tools:
                uids.append(uid)
        return uids

    def on_preview(self):
        """Compute the area that would be painted (in the worker) and draw it on the canvas."""
        self.clear_preview()
        uids = self.selected_tool_uids()
        if not uids:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
            return
        mode = self.ui.select_method_combo.get_value()
        if self.ui.multi_objects_cb.get_value() and mode in (0, 2, 3):
            objects = [obj for obj in self.app.collection.get_selected() if obj.kind in ('geometry', 'gerber')]
            if not objects:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Select Geometry or Gerber objects in the Project tab."))
                return
        else:
            obj_name = self.ui.obj_combo.currentText()
            try:
                source = self.app.collection.get_by_name(obj_name)
            except Exception:
                source = None
            if source is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(obj_name)))
                return
            objects = [source]

        reference = None
        if mode == 1 and not self.last_polygons:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "No polygon is selected yet. Click Generate to select the polygons, then use Preview."))
            return
        if mode == 2 and not self.last_zones:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "No area is drawn yet. Click Generate to draw the zones of the Area Selection, then use Preview."))
            return
        if mode == 3:
            ref_name = self.ui.reference_combo.currentText()
            try:
                reference = self.app.collection.get_by_name(ref_name)
            except Exception:
                reference = None
            if reference is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(ref_name)))
                return

        if self.ui.rest_cb.get_value():
            # rest machining shrinks the selection once, by the rest offset
            offsets = {float(self.ui.rest_offset_entry.get_value())}
        else:
            # every selected tool may have its own offset: the preview shows the union of the areas
            offsets = {float(self.paint_tools[uid]['data'].get('tools_paint_offset', 0.0)) for uid in uids}

        params = dict(objects=objects, mode=mode, offsets=sorted(offsets), reference=reference,
                      zones=list(self.last_zones), polygons=list(self.last_polygons), token=self._preview_token)
        self.app.inform.emit(_("Computing the area to paint ..."))
        self.app.worker_task.emit({'fcn': self.preview_job, 'params': [params]})

    def preview_job(self, params):
        """Worker: the area to paint. No toolpaths, no Qt widgets."""
        area, error = None, ''
        try:
            with self.app.proc_container.new('%s...' % _("Preview")):
                area = self.gen.preview_area(
                    params['objects'], params['mode'], params['offsets'], reference=params['reference'],
                    zones=params['zones'], polygons=params['polygons'])
        except grace:
            error = _("Aborted. The preview was not computed.")
        except Exception as e:
            self.app.log.error("ToolPaint.preview_job() --> %s" % str(e))
            error = _("The preview failed.")
        self.preview_done_sig.emit(params['token'], (area, error))

    def on_preview_done(self, token, result):
        """GUI thread: draw the area, unless the preview was cleared while the job was running."""
        if token != self._preview_token:
            return
        area, error = result
        if error:
            self.app.inform.emit('[ERROR_NOTCL] %s' % error)
            return
        polygons = [p for p in flatten_shapely_geometry(area) if not p.is_empty] if area is not None else []
        if not polygons:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The area to paint is empty."))
            return
        store = self._preview_store()
        if store is None:
            return
        store.clear(update=False)
        for poly in polygons:
            store.add(poly, color='#00AA00FF', face_color='#00AA0055', update=False, layer=0, tolerance=None)
        store.redraw()
        self._preview_drawn = True
        self.app.inform.emit('[success] %s' % _("Preview of the area to paint."))

    def _preview_store(self):
        """The shape collection of the preview, created on the first use (None without a canvas)."""
        if self._preview_shapes is None:
            try:
                self._preview_shapes = self.app.plotcanvas.new_shape_collection(layers=1)
            except Exception as e:
                self.app.log.error("ToolPaint._preview_store() --> %s" % str(e))
                return None
        return self._preview_shapes

    def on_source_changed(self, *args):
        """The object to paint changed: its preview and the zones / polygons of the last run do not apply any more."""
        self.last_zones = []
        self.last_polygons = []
        self.clear_preview()

    def clear_preview(self, *args):
        """Remove the preview from the canvas; a job still running will not draw its result."""
        self._preview_token += 1
        if self._preview_drawn and self._preview_shapes is not None:
            self._preview_shapes.clear(update=True)
        self._preview_drawn = False

    def reset_selection_state(self):
        """
        Forget a half-made area/polygon selection: click state, cursor position, drag flag, polygon points and the
        rubber band / zone outlines on the canvas. The lists are cleared in place because PaintGen shares them.
        """
        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False
        self.points.clear()
        self.poly_drawn = False

        self.delete_moving_selection_shape()
        self.delete_tool_selection_shape()

    # noinspection PyUnresolvedReferences
    def ui_connect(self):
        self.ui.tools_table.itemChanged.connect(self.on_tool_edit)

        # rows selected
        self.ui.tools_table.clicked.connect(self.on_row_selection_change)
        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_all_rows)

        # Table widgets
        for row in range(self.ui.tools_table.rowCount()):
            try:
                self.ui.tools_table.cellWidget(row, 2).currentIndexChanged.connect(self.on_tooltable_cell_widget_change)
            except AttributeError:
                pass

            try:
                self.ui.tools_table.cellWidget(row, 4).currentIndexChanged.connect(self.on_tooltable_cell_widget_change)
            except AttributeError:
                pass

        # Parameters FORM UI
        # first disconnect
        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                try:
                    current_widget.stateChanged.disconnect()
                except (TypeError, ValueError):
                    pass
            if isinstance(current_widget, RadioSet):
                try:
                    current_widget.activated_custom.disconnect()
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, FCDoubleSpinner):
                try:
                    current_widget.returnPressed.disconnect()
                except (TypeError, ValueError):
                    pass

        # then reconnect
        for opt in self.form_fields:
            current_widget = self.form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                current_widget.stateChanged.connect(self.form_to_storage)
            if isinstance(current_widget, RadioSet):
                current_widget.activated_custom.connect(self.form_to_storage)
            elif isinstance(current_widget, FCDoubleSpinner):
                current_widget.returnPressed.connect(self.form_to_storage)
            elif isinstance(current_widget, FCComboBox):
                current_widget.currentIndexChanged.connect(self.form_to_storage)

        # any input that changes the area to paint drops the displayed and any pending preview
        for signal in (
                self.ui.offset_entry.returnPressed,
                self.ui.rest_offset_entry.returnPressed,
                self.ui.tools_table.itemSelectionChanged,
                self.ui.rest_cb.stateChanged,
                self.ui.reference_combo.currentIndexChanged,
                self.ui.reference_type_combo.currentIndexChanged):
            try:
                signal.disconnect(self.clear_preview)
            except (TypeError, RuntimeError):
                pass
            signal.connect(self.clear_preview)

        self.ui.rest_cb.stateChanged.connect(self.ui.on_rest_machining_check)
        self.ui.order_combo.currentIndexChanged.connect(self.on_order_changed)

    def ui_disconnect(self):
        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.tools_table.itemChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        # rows selected
        try:
            self.ui.tools_table.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.tools_table.horizontalHeader().sectionClicked.disconnect()
        except (TypeError, AttributeError):
            pass

        # Table widgets
        for row in range(self.ui.tools_table.rowCount()):
            for col in [2, 4]:
                try:
                    self.ui.tools_table.cellWidget(row, col).currentIndexChanged.disconnect()
                except (TypeError, AttributeError):
                    pass

        # Parameters FORM UI
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
            elif isinstance(current_widget, FCDoubleSpinner):
                try:
                    current_widget.returnPressed.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget, FCComboBox):
                try:
                    current_widget.currentIndexChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass

        for signal in (
                self.ui.offset_entry.returnPressed,
                self.ui.rest_offset_entry.returnPressed,
                self.ui.tools_table.itemSelectionChanged,
                self.ui.rest_cb.stateChanged,
                self.ui.reference_combo.currentIndexChanged,
                self.ui.reference_type_combo.currentIndexChanged):
            try:
                signal.disconnect(self.clear_preview)
            except (TypeError, RuntimeError, AttributeError):
                pass

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.rest_cb.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.ui.order_combo.currentIndexChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    @staticmethod
    def paint_bounds(geometry):
        def bounds_rec(o):
            if type(o) is list:
                minx = Inf
                miny = Inf
                maxx = -Inf
                maxy = -Inf

                for k in o:
                    try:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                    except Exception:
                        return

                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return o.bounds

        return bounds_rec(geometry)

    def on_paint_tool_add_from_db_executed(self, tool):
        """
        Here add the tool from DB  in the selected geometry object
        :return:
        """
        tool_from_db = deepcopy(tool)

        if tool['data']['tool_target'] not in [0, 4]:   # [General, Paint]
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
            return 'fail'

        res = self.on_paint_tool_from_db_inserted(tool=tool_from_db)

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

    def on_paint_tool_from_db_inserted(self, tool):
        """
        Called from the Tools DB object through an App method when adding a tool from Tools Database
        :param tool: a dict with the tool data
        :return: None
        """

        self.ui_disconnect()
        self.units = self.app.app_units.upper()

        tooldia = float(tool['tooldia'])

        # construct a list of all 'tooluid' in the self.tools
        tool_uid_list = []
        for tooluid_key in self.paint_tools:
            tool_uid_item = int(tooluid_key)
            tool_uid_list.append(tool_uid_item)

        # find maximum from the temp_uid, add 1 and this is the new 'tooluid'
        if not tool_uid_list:
            max_uid = 0
        else:
            max_uid = max(tool_uid_list)
        tooluid = max_uid + 1

        tooldia = self.app.dec_format(tooldia, self.decimals)

        tool_dias = []
        for k, v in self.paint_tools.items():
            for tool_v in v.keys():
                if tool_v == 'tooldia':
                    tool_dias.append(float('%.*f' % (self.decimals, (v[tool_v]))))

        if float('%.*f' % (self.decimals, tooldia)) in tool_dias:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("Tool already in Tool Table.")))
            self.ui_connect()
            return 'fail'

        # merge the DB data over the defaults so the tools_paint_* and tools_mill_tool_shape keys always exist
        new_data = deepcopy(self.default_data)
        for d, value in tool['data'].items():
            if not d.startswith('tools_') or d.startswith(('tools_paint_', 'tools_mill_')):
                new_data[d] = deepcopy(value)

        self.paint_tools.update({
            tooluid: {
                'tooldia': float('%.*f' % (self.decimals, tooldia)),
                'data': new_data,
                'solid_geometry': []
            }
        })
        self.paint_tools[tooluid]['data']['name'] = '_paint'

        self.app.inform.emit('[success] %s' % _("New tool added to Tool Table."))

        self.ui_connect()
        self.build_ui()

        # select the tool just added
        for row in range(self.ui.tools_table.rowCount()):
            if int(self.ui.tools_table.item(row, 3).text()) == tooluid:
                self.ui.tools_table.selectRow(row)
                break

        return tooluid

    def on_paint_tool_add_from_db_clicked(self):
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
        ret_val = self.app.on_tools_database(source='paint')
        if ret_val == 'fail':
            return
        self.app.tools_db_tab.ok_to_add = True
        self.app.tools_db_tab.ui.buttons_frame.hide()
        self.app.tools_db_tab.ui.add_tool_from_db.show()
        self.app.tools_db_tab.ui.cancel_tool_from_db.show()

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
