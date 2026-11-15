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
from appGUI.GUIElements import (
    FCComboBox,
    FCCheckBox,
    RadioSet,
    FCDoubleSpinner,
    FCInputDoubleSpinner,
)


from copy import deepcopy
import math
from numpy import inf as Inf    # noqa

from shapely import Polygon
from shapely.ops import unary_union

import gettext
import appTranslation as fcTranslate
import builtins

from appParsers.ParseGerber import Gerber
from appDatabase import load_tools_database, tools_database_error_message
from defaults import AppDefaults
from appCommon.SafeEval import safe_eval
from appPlugins.Common.clearing.controller_base import ClearingToolBase, signals_blocked
from appPlugins.Common.clearing.methods import PAINT_METHODS
from camlib import (
    deepcopy_keep_geometry,
    Geometry,
    grace,
    flatten_shapely_geometry,
)

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


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


class ToolPaint(Geometry, ClearingToolBase):

    key_prefix = 'tools_paint'
    tools_attr = 'paint_tools'
    tool_target = 4
    method_map = PAINT_METHODS
    usage_name = "ToolPaint()"

    # (job token, result): the preview job hands the area to paint (or an error text) to the GUI thread
    preview_done_sig = QtCore.pyqtSignal(int, object)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        ClearingToolBase.__init__(self, app)
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

        # the polygons of the last Polygon Selection run: the Preview of that mode uses them
        self.last_polygons: list[Polygon] = []

        # store here the default data for Geometry Data
        self.default_data = {}

        self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

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
            "tools_paint_clear_milling": self.ui.clear_dir_radio,
            "tools_paint_final_cleanup": self.ui.final_cleanup_cb,
        }

        self.name2option = {
            'p_overlap':    "tools_paint_overlap",
            'p_offset':     "tools_paint_offset",
            'p_method':     "tools_paint_method",
            'p_crosshatch_angle': "tools_paint_crosshatch_angle",
            'p_connect':    "tools_paint_connect",
            'p_contour':    "tools_paint_contour",
            'p_clear_milling': "tools_paint_clear_milling",
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
        ClearingToolBase.install(self, icon, separator, shortcut='Alt+P', **kwargs)

    def tab_title(self):
        return _("Paint")

    def generate_slot(self):
        return self.gen.on_paint_button_click

    def type_obj_slot(self):
        return self.on_type_obj_changed

    def multi_objects_widget(self):
        return self.ui.multi_objects_cb

    def reset_last_regions(self):
        self.last_zones = []
        self.last_polygons = []

    def extra_signals(self):
        return [
            (self.ui.new_tooldia_entry.returnPressed, self.on_tool_add),
            (self.ui.select_method_combo.currentIndexChanged, self.clear_preview),
        ]

    # ------------------------------------------------------------------------------------------------------------
    # the canvas selection
    # ------------------------------------------------------------------------------------------------------------
    def start_click_selection(self):
        """
        Polygon Selection: a left click adds / removes the polygon of the object under the cursor, a right click or
        Enter paints the picked polygons, Escape cancels. The panel is disabled until then.
        """
        if not isinstance(self.paint_obj, (Gerber, Geometry)):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The selected object is not a Gerber or Geometry object."))
            return
        # Gerber is a Geometry subclass, so both kinds are flattened: a MultiPolygon is many polygons
        self.selector.start('click', commit=self.on_polygons_picked,
                            faces=flatten_shapely_geometry(self.paint_obj.solid_geometry),
                            tolerance=self.paint_obj.drawing_tolerance)

    def on_polygons_picked(self, points, faces):
        poly_list = deepcopy_keep_geometry(list(faces))
        self.last_polygons = list(poly_list)
        self.gen.paint_poly(self.paint_obj, inside_pt=points[-1], poly_list=poly_list, tooldia=self.tooldia_list)

    def on_zones_selected(self, zones):
        # the Preview of this mode draws these zones
        self.last_zones = list(zones)
        self.gen.paint_area_selection_option(zones)

    def on_paint_tool_add_from_db_executed(self, tool):
        return self.on_tool_add_from_db_executed(tool)

    def on_paint_tool_add_from_db_clicked(self):
        return self.on_tool_add_from_db_clicked()

    def set_tool_ui(self):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        # a Reset drops the selection, the preview, the zones from the canvas and the zones / polygons of the last run
        self.reset_drawn_regions()

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
            "tools_paint_clear_milling": self.ui.clear_dir_radio,
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
        self.ui.excl_shape_radio.set_value(self.ui.area_shape_radio.get_value())
        self.ui.connect_cb.set_value(self.app.options.get(
            "tools_paint_connect", app_defaults.get("tools_paint_connect", AppDefaults.factory_defaults.get("tools_paint_connect"))))
        self.ui.contour_cb.set_value(self.app.options.get(
            "tools_paint_contour", app_defaults.get("tools_paint_contour", AppDefaults.factory_defaults.get("tools_paint_contour"))))
        self.ui.clear_dir_radio.set_value(self.app.options.get(
            "tools_paint_clear_milling", app_defaults.get("tools_paint_clear_milling", "none")))
        self.default_data['tools_paint_clear_milling'] = self.ui.clear_dir_radio.get_value()
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

        self.ui.tools_table.drag_drop_sig.connect(self.rebuild_ui)

        # #############################################################################
        # Geometry generator
        # #############################################################################
        self.gen = PaintGen(self)
        self.connect_signals()
        self.refresh_zone_view()

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

    def storage_to_form(self, dict_storage):
        dict_storage.setdefault('tools_paint_final_cleanup', False)
        dict_storage.setdefault('tools_paint_clear_milling', 'none')
        dict_storage.setdefault('tools_paint_crosshatch_angle', 0.0)
        for k in self.form_fields:
            try:
                self.form_fields[k].set_value(dict_storage[k])
            except Exception as err:
                self.app.log.error("ToolPaint.storage.form() --> %s" % str(err))
        self.ui.on_method_changed()

    def on_apply_param_to_all_clicked(self):
        if self.ui.tools_table.rowCount() == 0:
            # there is no tool in tool table, so we can't save the GUI elements values to storage
            self.app.log.debug("ToolPaint.on_apply_param_to_all_clicked() --> no tool in Tools Table, aborting.")
            return

        with signals_blocked(self):
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
        with signals_blocked(self):
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

        with signals_blocked(self):
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

        with signals_blocked(self):
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

    def zone_sources(self):
        """The objects to paint: the selected Geometry / Gerber objects when All selected is on (not in Polygon
        Selection), else the object of the combo."""
        if self.ui.multi_objects_cb.get_value() and self.ui.select_method_combo.get_value() in (0, 2, 3):
            selected = [obj for obj in self.app.collection.get_selected() if obj.kind in ('geometry', 'gerber')]
            if selected:
                return selected
        return self.combo_source()

    def exclusion_zone_list(self):
        """The exclusion zones of the sources as a list, empty when they are off or there are none."""
        try:
            use = bool(self.ui.excl_cb.get_value())
        except RuntimeError:
            return []
        return self.source_zones() if use else []

    def clear_exclusions(self):
        """The exclusion zones as one geometry, or None when they are off or there are none."""
        zones = self.exclusion_zone_list()
        return unary_union(zones) if zones else None

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
                      zones=list(self.last_zones), polygons=list(self.last_polygons),
                      exclusion=self.clear_exclusions(), token=self._preview_token)
        self.app.inform.emit(_("Computing the area to paint ..."))
        self.app.worker_task.emit({'fcn': self.preview_job, 'params': [params]})

    def preview_job(self, params):
        """Worker: the area to paint. No toolpaths, no Qt widgets."""
        area, error = None, ''
        try:
            with self.app.proc_container.new('%s...' % _("Preview")):
                area = self.gen.preview_area(
                    params['objects'], params['mode'], params['offsets'], reference=params['reference'],
                    zones=params['zones'], polygons=params['polygons'], exclusion=params['exclusion'])
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
        store = self._shape_store('_preview_shapes')
        if store is None:
            return
        store.clear(update=False)
        for poly in polygons:
            store.add(poly, color='#00AA00FF', face_color='#00AA0055', update=False, layer=0, tolerance=None)
        store.redraw()
        self._preview_drawn = True
        self.app.inform.emit('[success] %s' % _("Preview of the area to paint."))

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

    def on_tool_from_db_inserted(self, tool):
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

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
