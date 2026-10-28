# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     6/15/2020                                      #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import VerticalScrollArea, FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCCheckBox, \
    FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner, NumericalEvalTupleEntry, NumericalEvalEntry, FCTable, \
    OptionalInputSection, OptionalHideInputSection
from appDatabase import load_tools_database, tools_database_error_message
from appCommon.ToolDatabase import matching_tools
from camlib import Geometry, flatten_shapely_geometry, object_quad_segs
from appCommon.SafeEval import safe_eval
from appCommon.Common import ExclusionAreas
from appPlugins.ToolDrilling import DrillingGen
from appPlugins.ToolDrilling.DrillingUI import DrillingUI


import logging
from copy import deepcopy
from contextlib import nullcontext
from appHandlers.appHistory import history_of
import numpy as np
import sys
import platform
import re

import shapely
from shapely import LineString

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# the 'owner' stored in the exclusion areas drawn in this plugin (the Milling areas have none)
EXCLUSION_OWNER = 'drilling'


def merge_same_diameter_tools(tools):
    """
    Merges the Excellon tools that have the same diameter. After a replacement from the Tools DB, more than one
    original tool can fit in the tolerance of one DB tool and get its diameter. The first tool keeps its data and
    gets the drills, slots and solid geometry of the others.

    :param tools:   Excellon tools, {tool_id: {'tooldia': float, 'drills': list, 'slots': list, ...}}
    :type tools:    dict
    :return:        the same dict if no diameter repeats, else a new dict keyed by the tool id of the first tool of
                    each diameter
    :rtype:         dict
    """
    return DrillingGen.merge_same_diameter_tools(tools)


class ToolDrilling(Geometry, AppTool):
    build_ui_sig = QtCore.pyqtSignal()

    def __init__(self, app):
        self.app = app
        self.dec_format = self.app.dec_format

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        Geometry.__init__(
            self,
            geo_steps_per_circle=int(self.app.options.get(
                "excellon_circle_steps",
                app_defaults.get("excellon_circle_steps", AppDefaults.factory_defaults.get("excellon_circle_steps")))),
            app=app)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = None
        self.pluginName = _("Drilling")

        # #############################################################################
        # ########################## VARIABLES ########################################
        # #############################################################################
        self.units = ''
        self.excellon_tools = {}
        # True while self.excellon_tools is the dict of self.excellon_obj.tools (an edit reaches the object)
        self.tools_are_object_tools = False
        self.tooluid = 0
        self.kind = "excellon"

        # dict that holds the object names and the option name
        # the key is the object name (defines in ObjectUI) for each UI element that is a parameter
        # particular for a tool and the value is the actual name of the option that the UI element is changing
        self.name2option = {}

        # store here the default data for Geometry Data
        self.default_data = {}

        self.obj_name = ""
        self.excellon_obj = None

        # The areas that select the holes (Holes: Inside area). Drawn with the helper of the exclusion areas but kept in
        # their own storage (not in app.exc_areas). Created when the plugin opens, when the canvas exists.
        self.hole_areas = None
        # the Add objects selection that the tools table shows (ids of the objects)
        self._merged_extras = ()

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        # store here the points for the "Polygon" area selection shape
        self.points = []

        # variable to store the total amount of drills per job
        self.tot_drill_cnt = 0
        self.tool_row = 0

        # variable to store the total amount of slots per job
        self.tot_slot_cnt = 0
        self.tool_row_slots = 0

        # variable to store the distance travelled
        self.travel_distance = 0.0

        self.grid_status_memory = self.app.ui.grid_snap_btn.isChecked()

        # store here the state of the exclusion checkbox state to be restored after building the UI
        self.exclusion_area_cb_is_checked = False

        # store here solid_geometry when there are tool with isolation job
        self.solid_geometry = []

        self.circle_steps = int(self.app.options.get(
            "geometry_circle_steps",
            app_defaults.get("geometry_circle_steps", AppDefaults.factory_defaults.get("geometry_circle_steps"))))

        self.tooldia = None

        # multiprocessing
        self.pool = self.app.pool
        self.results = []

        # disconnect flags
        self.area_sel_disconnect_flag = False
        self.poly_sel_disconnect_flag = False

        # Tools Database
        self.tools_db_dict = None

        self.tool_form_fields = {}

        self.general_form_fields = {}

        self.name2option = {}

        self.poly_drawn = False

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+D', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolDrilling()")

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
                        self.on_plugin_cleanup()
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

        self.set_tool_ui()

        super().run()

        self.on_object_changed()
        # self.build_tool_ui()

        # all the tools are selected by default
        self.ui.tools_table.selectAll()

        self.app.ui.notebook.setTabText(2, _("Drilling"))

    def connect_main_signals(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.build_ui_sig.connect(self.build_tool_ui)

        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.search_load_db_btn.clicked.connect(self.on_tool_db_load)

        self.ui.apply_param_to_all.clicked.connect(self.on_apply_param_to_all_clicked)
        self.ui.generate_cnc_button.clicked.connect(self.on_generate_cnc_job)
        self.ui.tools_table.drag_drop_sig.connect(self.rebuild_ui)

        # Exclusion areas signals
        self.ui.exclusion_table.horizontalHeader().sectionClicked.connect(self.exclusion_table_toggle_all)
        self.ui.exclusion_table.lost_focus.connect(self.clear_selection)
        self.ui.exclusion_table.itemClicked.connect(self.draw_sel_shape)
        self.ui.add_area_button.clicked.connect(self.on_add_area_click)
        self.ui.delete_area_button.clicked.connect(self.on_clear_area_click)
        self.ui.delete_sel_area_button.clicked.connect(self.on_delete_sel_areas)
        self.ui.strategy_radio.activated_custom.connect(self.on_strategy)

        # more objects in the job, holes inside areas
        self.ui.extra_objects_list.itemSelectionChanged.connect(self.on_extra_objects_changed)
        self.ui.hole_area_button.clicked.connect(self.on_add_hole_area_click)
        self.ui.hole_areas_clear_button.clicked.connect(self.on_clear_hole_areas_click)
        if self.hole_areas is not None:
            self.hole_areas.e_shape_modified.connect(self.update_hole_areas_label)

        self.ui.pp_excellon_name_cb.activated.connect(self.on_pp_changed)

        self.ui.reset_button.clicked.connect(self.on_reset_clicked)
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        self.app.cleanup.connect(self.set_tool_ui)
        # Undo / Redo gives the object a new tools dict
        self.app.app_obj.object_changed.connect(self.on_object_tools_replaced)

    def disconnect_main_signals(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        try:
            self.build_ui_sig.disconnect()

        except (TypeError, AttributeError):
            pass
        try:
            self.ui.level.toggled.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.search_load_db_btn.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.apply_param_to_all.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.generate_cnc_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.tools_table.drag_drop_sig.disconnect()
        except (TypeError, AttributeError):
            pass

        # Exclusion areas signals
        try:
            self.ui.exclusion_table.horizontalHeader().sectionClicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.exclusion_table.lost_focus.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.exclusion_table.itemClicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.add_area_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        for signal, slot in ((self.ui.extra_objects_list.itemSelectionChanged, self.on_extra_objects_changed),
                             (self.ui.hole_area_button.clicked, self.on_add_hole_area_click),
                             (self.ui.hole_areas_clear_button.clicked, self.on_clear_hole_areas_click),
                             (getattr(self.hole_areas, 'e_shape_modified', None), self.update_hole_areas_label)):
            try:
                signal.disconnect(slot)
            except (TypeError, AttributeError, RuntimeError):
                pass
        try:
            self.ui.delete_area_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.delete_sel_area_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.strategy_radio.activated_custom.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.pp_excellon_name_cb.activated.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.ui.reset_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        try:
            self.app.cleanup.disconnect(self.set_tool_ui)
        except (TypeError, AttributeError):
            pass
        try:
            self.app.app_obj.object_changed.disconnect(self.on_object_tools_replaced)
        except (TypeError, AttributeError, RuntimeError):
            pass

    def on_plugin_cleanup(self):
        """The plugin is closed: leave the global signals (they would keep calling a hidden plugin)."""
        for signal, slot in ((self.app.proj_selection_changed, self.on_object_selection_changed),
                             (self.app.exc_areas.e_shape_modified, self.update_exclusion_table),
                             (self.app.cleanup, self.set_tool_ui),
                             (self.app.app_obj.object_changed, self.on_object_tools_replaced)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError, AttributeError):
                pass
        self._clear_hole_areas()
        self.app.delete_selection_shape()

    def set_context_menu(self):
        # #############################################################################################################
        # ############################## EXCLUSION TABLE context menu #################################################
        # #############################################################################################################
        self.ui.exclusion_table.setupContextMenu()
        self.ui.exclusion_table.addContextMenu(
            '%s %s' % (_("Copy"), _("coords")), self.on_copy_exclusion_area_coords,
            icon=QtGui.QIcon(self.app.resource_location + "/copy16.png")
        )
        self.ui.exclusion_table.addContextMenu(
            _("Delete"), self.on_delete_sel_areas, icon=QtGui.QIcon(self.app.resource_location + "/trash16.png")
        )

    def unset_context_menu(self):
        self.ui.exclusion_table.removeContextMenu()

    def init_ui(self):
        self.ui = DrillingUI(layout=self.layout, app=self.app, name=self.pluginName)
        self.add_object_creation_controls()

    def set_tool_ui(self):
        self.reset_object_creation_controls()
        self.units = self.app.app_units.upper()

        self.clear_ui(self.layout)
        self.init_ui()

        if self.hole_areas is None:
            self.hole_areas = ExclusionAreas(self.app)
            self._hole_area_button_stub = FCButton()

        self.disconnect_main_signals()
        self.connect_main_signals()

        self.unset_context_menu()
        self.set_context_menu()

        # try to select in the Excellon combobox the active object
        try:
            selected_obj = self.app.collection.get_active()
            if selected_obj.kind == 'excellon':
                current_name = selected_obj.obj_options['name']
                self.ui.object_combo.set_value(current_name)
        except Exception:
            pass

        try:
            loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        except Exception as err:
            self.app.log.error("ToolDrilling -> Loaded Excellon object error. %s" % str(err))
            loaded_obj = None
        # the object shown in the combo is the one of the plugin (also after Reset, cleanup or a deleted object)
        self.excellon_obj = loaded_obj
        self._clear_hole_areas()
        self._populate_extra_objects()

        self.tool_form_fields.update({
            "tools_drill_cutz": self.ui.cutz_entry,
            "tools_drill_multidepth": self.ui.mpass_cb,
            "tools_drill_depthperpass": self.ui.maxdepth_entry,
            "tools_drill_travelz": self.ui.travelz_entry,
            "tools_drill_feedrate_z": self.ui.feedrate_z_entry,
            "tools_drill_feedrate_rapid": self.ui.feedrate_rapid_entry,

            "tools_drill_spindlespeed": self.ui.spindlespeed_entry,
            "tools_drill_min_power": self.ui.las_min_pwr_entry,
            "tools_drill_laser_on": self.ui.laser_turn_on_combo,
            "tools_drill_dwell": self.ui.dwell_cb,
            "tools_drill_dwelltime": self.ui.dwelltime_entry,
            "tools_drill_dwell_bottom": self.ui.dwell_bottom_cb,
            "tools_drill_peck": self.ui.peck_cb,
            "tools_drill_peck_depth": self.ui.peck_depth_entry,
            "tools_drill_peck_retract": self.ui.peck_retract_entry,
            "tools_drill_spindledir": self.ui.spindledir_radio,
            "tools_drill_f_plunge": self.ui.fplunge_cb,
            "tools_drill_f_retract": self.ui.fretract_cb,

            "tools_drill_offset": self.ui.offset_entry,

            "tools_drill_drill_slots": self.ui.drill_slots_cb,
            "tools_drill_drill_overlap": self.ui.drill_overlap_entry,
            "tools_drill_last_drill": self.ui.last_drill_cb,
        })

        self.general_form_fields.update({
            "tools_drill_toolchange": self.ui.toolchange_cb,
            "tools_drill_toolchangez": self.ui.toolchangez_entry,
            "tools_drill_toolchangexy": self.ui.toolchangexy_entry,
            "tools_drill_startz": self.ui.estartz_entry,

            "tools_drill_endz": self.ui.endz_entry,
            "tools_drill_endxy": self.ui.endxy_entry,

            "tools_drill_spot": self.ui.spot_cb,
            "tools_drill_spot_dia": self.ui.spot_dia_entry,
            "tools_drill_spot_cutz": self.ui.spot_cutz_entry,
            "tools_drill_spot_feedrate_z": self.ui.spot_feedrate_entry,

            "tools_drill_job_offset_xy": self.ui.job_offset_entry,
            "tools_drill_mirror": self.ui.mirror_radio,
            "tools_drill_mirror_point": self.ui.mirror_point_entry,
            "tools_drill_array_columns": self.ui.array_columns_entry,
            "tools_drill_array_rows": self.ui.array_rows_entry,
            "tools_drill_array_pitch": self.ui.array_pitch_entry,
            "tools_drill_holes_selection": self.ui.holes_radio,

            "tools_drill_z_p_depth": self.ui.pdepth_entry,
            "tools_drill_probe_plate": self.ui.probe_plate_entry,
            "tools_drill_feedrate_probe": self.ui.feedrate_probe_entry,

            "tools_drill_ppname_e": self.ui.pp_excellon_name_cb,

            "tools_drill_area_exclusion": self.ui.exclusion_cb,
            "tools_drill_area_strategy": self.ui.strategy_radio,
            "tools_drill_area_overz": self.ui.over_z_entry,
            "tools_drill_area_shape": self.ui.area_shape_radio
        })

        self.name2option.update({
            "e_cutz": "tools_drill_cutz",
            "e_multidepth": "tools_drill_multidepth",
            "e_depthperpass": "tools_drill_depthperpass",
            "e_travelz": "tools_drill_travelz",
            "e_feedratez": "tools_drill_feedrate_z",
            "e_fr_rapid": "tools_drill_feedrate_rapid",

            "e_spindlespeed": "tools_drill_spindlespeed",
            "e_minpower": "tools_drill_min_power",
            "e_laser_turn_on": "tools_drill_laser_on",
            "e_dwell": "tools_drill_dwell",
            "e_dwelltime": "tools_drill_dwelltime",
            "e_dwell_bottom": "tools_drill_dwell_bottom",
            "e_peck": "tools_drill_peck",
            "e_peck_depth": "tools_drill_peck_depth",
            "e_peck_retract": "tools_drill_peck_retract",
            "e_spindledir": "tools_drill_spindledir",
            "e_f_plunge": "tools_drill_f_plunge",
            "e_f_retract": "tools_drill_f_retract",

            "e_offset": "tools_drill_offset",

            "e_drill_slots": "tools_drill_drill_slots",
            "e_drill_slots_overlap": "tools_drill_drill_overlap",
            "e_drill_last_drill": "tools_drill_last_drill",

            # General Parameters
            "e_toolchange": "tools_drill_toolchange",
            "e_toolchangez": "tools_drill_toolchangez",
            "e_toolchangexy": "tools_drill_toolchangexy",

            "e_startz": "tools_drill_startz",

            "e_endz": "tools_drill_endz",
            "e_endxy": "tools_drill_endxy",

            "e_spot": "tools_drill_spot",
            "e_spot_dia": "tools_drill_spot_dia",
            "e_spot_cutz": "tools_drill_spot_cutz",
            "e_spot_feedrate": "tools_drill_spot_feedrate_z",

            "e_job_offset": "tools_drill_job_offset_xy",
            "e_mirror": "tools_drill_mirror",
            "e_mirror_point": "tools_drill_mirror_point",
            "e_array_columns": "tools_drill_array_columns",
            "e_array_rows": "tools_drill_array_rows",
            "e_array_pitch": "tools_drill_array_pitch",
            "e_holes_selection": "tools_drill_holes_selection",

            "e_depth_probe": "tools_drill_z_p_depth",
            "e_probe_plate": "tools_drill_probe_plate",
            "e_fr_probe": "tools_drill_feedrate_probe",

            "e_pp": "tools_drill_ppname_e",

            "e_area_exclusion": "tools_drill_area_exclusion",
            "e_area_strategy": "tools_drill_area_strategy",
            "e_area_overz": "tools_drill_area_overz",
            "e_area_shape": "tools_drill_area_shape",
        })

        pp_list = self._populate_pp_list(loaded_obj)

        self._set_tool_order(self.app.options.get(
            "tools_drill_tool_order",
            AppDefaults.factory_defaults.get("tools_drill_tool_order")))
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        if loaded_obj:
            outname = loaded_obj.obj_options['name']
        else:
            outname = ''

        # init the working variables
        self.default_data.clear()

        # the options of the object; what the object lacks (an old object) comes from the App options
        self.default_data.update(self.app.options)
        if loaded_obj:
            self.default_data.update(loaded_obj.obj_options)

        self.default_data['name'] = outname + '_drill'

        self.first_click = False
        self.cursor_pos = None
        self.mouse_is_dragging = False

        self.units = self.app.app_units.upper()

        # ########################################
        # #######3 TEMP SETTINGS #################
        # ########################################

        self.ui.tools_table.setRowCount(2)
        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        # make sure to update the UI on init
        if loaded_obj is not None:
            self.excellon_tools = loaded_obj.tools
            self.tools_are_object_tools = True
        else:
            # no object loaded (or it was deleted)
            self.excellon_tools = {}
            self.tools_are_object_tools = False
        self.build_tool_ui()

        # #############################################################################################################
        # #############################################################################################################
        # ################################### Fill in the parameters ##################################################
        # #############################################################################################################
        # #############################################################################################################

        # The form shows the defaults of the object. It must not write them into the tools: the stored values of
        # each tool are shown by update_ui() below (Reset Tool restores the defaults in on_reset_clicked()).
        self.ui_disconnect()
        try:
            # tool parameters
            for key in self.tool_form_fields:
                self._set_field(self.tool_form_fields[key], self._default(key))
            # common parameters
            for key in self.general_form_fields:
                self._set_field(self.general_form_fields[key], self._default(key))

            # preprocessor
            if self._default("tools_drill_ppname_e") in pp_list:
                sel_ppname_e = self._default("tools_drill_ppname_e")
            else:
                sel_ppname_e = 'default'
            self.ui.pp_excellon_name_cb.set_value(sel_ppname_e)
        finally:
            self.ui_connect()

        # Overlap and Last drill are visible only when the slots are drilled (the checkbox drives them)
        self.ui.ois_drill_overlap.on_cb_change()

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level", app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        self.ui.tools_frame.show()

        # show the stored values of the first selected tool
        self.update_ui()

        try:
            self.ui.object_combo.currentTextChanged.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.object_combo.currentTextChanged.connect(self.on_object_changed)

    def _default(self, key):
        """The default of a form field: the object's options, else the App options, else the factory defaults."""
        if key in self.default_data:
            return self.default_data[key]
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))

    @staticmethod
    def _set_field(widget, value):
        """Set a form widget. A blank X,Y / Start Z entry (None) is shown empty, not as the text 'None'."""
        if value is None and isinstance(widget, (NumericalEvalTupleEntry, NumericalEvalEntry)):
            widget.setText('')
            return
        widget.set_value(value)

    def _populate_pp_list(self, loaded_obj):
        """Fill the Excellon preprocessor combo; an object with the Check_points preprocessor allows only that one."""
        combo = self.ui.pp_excellon_name_cb
        obj_pp = loaded_obj.obj_options.get('tools_drill_ppname_e') if loaded_obj else None
        if obj_pp == 'Check_points':
            pp_list = ['Check_points']
        else:
            pp_list = []
            for name in self.app.preprocessors.keys():
                # the HPGL preprocessor or Paste_ are not for Excellon job therefore don't add it
                if name in ['hpgl', 'Paste_1', 'Check_points']:
                    continue
                pp_list.append(name)
            pp_list.sort()
        if 'default' in pp_list:
            pp_list.remove('default')
            pp_list.insert(0, 'default')

        # a refill must not be stored in the tools as a user choice
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(pp_list)
            # add tooltips
            for it in range(combo.count()):
                combo.setItemData(it, combo.itemText(it), QtCore.Qt.ItemDataRole.ToolTipRole)
        finally:
            combo.blockSignals(False)
        return pp_list

    # ------------------------------------------------------------------------------------------------------------
    # Tool order: the combo holds an index (0, 1, 2); the options and the generation use 'no', 'fwd', 'rev'
    # ------------------------------------------------------------------------------------------------------------
    TOOL_ORDERS = ('no', 'fwd', 'rev')

    def _tool_order(self):
        """The tool order chosen in the UI: 'no' (as in the file), 'fwd' (small to big) or 'rev' (big to small)."""
        idx = self.ui.order_combo.get_value()
        return self.TOOL_ORDERS[idx] if 0 <= idx < len(self.TOOL_ORDERS) else 'no'

    def _set_tool_order(self, value):
        """Set the order combo from an option value: 'no' / 'fwd' / 'rev' or the index the preferences save."""
        if isinstance(value, str):
            idx = self.TOOL_ORDERS.index(value) if value in self.TOOL_ORDERS else 0
        else:
            try:
                idx = int(value)
            except (TypeError, ValueError):
                idx = 0
            if not 0 <= idx < len(self.TOOL_ORDERS):
                idx = 0
        self.ui.order_combo.blockSignals(True)
        try:
            self.ui.order_combo.set_value(idx)
        finally:
            self.ui.order_combo.blockSignals(False)

    def _ordered_tool_ids(self):
        """The ids of self.excellon_tools in the order of the Order combo. The tools themselves are never touched."""
        ids = list(self.excellon_tools)
        order = self._tool_order()
        if order == 'fwd':
            return sorted(ids, key=lambda k: float(self.excellon_tools[k]['tooldia']))
        if order == 'rev':
            return sorted(ids, key=lambda k: float(self.excellon_tools[k]['tooldia']), reverse=True)
        return ids

    def _is_beginner(self):
        return not self.ui.level.isChecked()

    def _effective_tool_data(self, tool_id):
        """
        The data of a tool as the job generation must use it. In Beginner mode the advanced options are hidden, so
        their safe values are used; the values stored in the tool stay as the user set them.
        """
        data = deepcopy(self.excellon_tools[tool_id]['data'])
        if self._is_beginner():
            data['tools_drill_multidepth'] = False
            data['tools_drill_dwell'] = False
            data['tools_drill_dwell_bottom'] = False
            data['tools_drill_peck'] = False
            data['tools_drill_spot'] = False
            data.update(tools_drill_job_offset_xy=None, tools_drill_mirror='no', tools_drill_array_columns=1,
                        tools_drill_array_rows=1)
            data['tools_drill_drill_slots'] = False
            data['tools_drill_area_exclusion'] = False
        return data

    def _has_toolchange(self, selected_ids):
        """True if one of the selected tools has the toolchange option on (the other tools do not matter)."""
        for tool_id in selected_ids:
            tool = self.excellon_tools.get(tool_id)
            if tool and tool.get('data', {}).get('tools_drill_toolchange'):
                return True
        return False

    def _validate_xy_fields(self, selected_ids=None):
        """
        Check the X,Y entries of the Common Parameters before a job: the Toolchange X-Y (needed when a selected tool
        has toolchange on) and the End move X,Y (optional, but if there is text it must be a pair of numbers).

        :return:    True if the job can use them
        """
        def is_xy(val):
            # a blank (None or '') is no XY move: valid; a text "x, y" or a pair is read as in the job
            try:
                DrillingGen.parse_xy(val, '')
            except (TypeError, ValueError):
                return False
            return True

        ids = list(self.excellon_tools) if selected_ids is None else selected_ids
        for tool_id in ids:
            data = self.excellon_tools.get(tool_id, {}).get('data', {})
            if data.get('tools_drill_toolchange') and not is_xy(data.get('tools_drill_toolchangexy')):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Toolchange X-Y is not valid. Use two numbers: x, y."))
                return False
            if not is_xy(data.get('tools_drill_endxy')):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("End move X,Y is not valid. Use two numbers: x, y."))
                return False
        return True

    def on_reset_clicked(self):
        """Reset Tool: the parameters of every tool go back to the defaults of the object."""
        with self.history_step(_("Drilling: reset the tool parameters")):
            for tool in self.excellon_tools.values():
                data = tool.setdefault('data', {})
                for key in list(self.tool_form_fields) + list(self.general_form_fields):
                    data[key] = deepcopy(self._default(key))
        self.set_tool_ui()

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
        # The level only shows or hides controls. The values stored in the tools are never changed here: in Beginner
        # mode the job generation uses safe values for the hidden options (see _effective_tool_data()).
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            self.ui.search_load_db_btn.hide()

            self.ui.mpass_cb.hide()
            self.ui.maxdepth_entry.hide()

            self.ui.dwell_cb.hide()
            self.ui.dwelltime_entry.hide()
            for widget in self.ui.advanced_drill_widgets:
                widget.hide()

            self.ui.tool_offset_label.hide()
            self.ui.offset_entry.hide()

            self.ui.drill_slots_cb.hide()
            self.ui.drill_overlap_label.hide()
            self.ui.drill_overlap_entry.hide()
            self.ui.last_drill_cb.hide()

            # All param section
            self.ui.apply_param_to_all.hide()

            # General param
            self.ui.estartz_label.hide()
            self.ui.estartz_entry.hide()
            self.ui.endmove_xy_label.hide()
            self.ui.endxy_entry.hide()

            for widget in self.ui.spot_widgets + self.ui.transform_widgets + self.ui.hole_widgets + \
                    self.ui.extra_objects_widgets:
                widget.hide()

            self.ui.exclusion_cb.hide()
            self.ui.exclusion_frame.hide()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            self.ui.search_load_db_btn.show()

            self.ui.mpass_cb.show()
            self.ui.maxdepth_entry.show()

            self.ui.dwell_cb.show()
            self.ui.dwelltime_entry.show()
            laser = 'laser' in str(self.ui.pp_excellon_name_cb.get_value()).lower()
            for widget in self.ui.advanced_drill_widgets:
                if not (laser and widget in self.ui.no_laser_drill_widgets):
                    widget.show()

            self.ui.tool_offset_label.show()
            self.ui.offset_entry.show()

            self.ui.drill_slots_cb.show()
            # Overlap and Last drill follow the Drill slots checkbox
            self.ui.ois_drill_overlap.on_cb_change()

            # All param section
            self.ui.apply_param_to_all.show()

            # General param
            self.ui.estartz_label.show()
            self.ui.estartz_entry.show()
            self.ui.endmove_xy_label.show()
            self.ui.endxy_entry.show()

            # the spot fields are enabled by the spot checkbox
            for widget in self.ui.spot_widgets + self.ui.transform_widgets + self.ui.hole_widgets + \
                    self.ui.extra_objects_widgets:
                widget.show()

            self.ui.exclusion_cb.show()
            # the exclusion frame follows the exclusion checkbox
            self.ui.ois_exclusion_exc.on_cb_change()

        # update the changes in UI depending on the selected preprocessor in Preferences
        # after this moment all the changes in the Posprocessor combo will be handled by the activated signal of the
        # self.ui.pp_excellon_name_cb combobox
        self.on_pp_changed()

        # the extra objects are an Advanced option: the tools table follows the level
        self.on_extra_objects_changed()

    def rebuild_ui(self):
        # read the table tools uid
        current_uid_list = []
        for row in range(self.ui.tools_table.rowCount()):
            try:
                uid = int(self.ui.tools_table.item(row, 3).text())
            except (AttributeError, ValueError):
                continue
            if uid in self.excellon_tools:
                current_uid_list.append(uid)

        if not current_uid_list:
            return

        new_tools = {}
        new_uid = 1

        for current_uid in current_uid_list:
            new_tools[new_uid] = deepcopy(self.excellon_tools[current_uid])
            new_uid += 1

        self.excellon_tools = new_tools
        self.tools_are_object_tools = False

        # the rows are now in the order the user dragged them: the Order combo must not sort them again
        self._set_tool_order('no')

        # the tools table changed therefore we need to rebuild it
        QtCore.QTimer.singleShot(20, self.build_tool_ui)

    def build_tool_ui(self):
        self.app.log.debug("ToolDrilling.build_tool_ui()")
        self.ui_disconnect()

        # the tools selected before the table is rebuilt (the id is in column 3): a rebuild must not change the
        # selection of the user. An other Excellon object starts with all its tools selected.
        previous_uids = set()
        if getattr(self, '_table_obj', None) is self.excellon_obj:
            for row in self._selected_tool_rows():
                item = self.ui.tools_table.item(row, 3)
                if item is not None and item.text().isdigit():
                    previous_uids.add(int(item.text()))
        self._table_obj = self.excellon_obj

        # The Order combo sorts only the rows of the table (the tool ids stay, the tools are not touched); the job
        # generation sorts the same way with _tool_order().
        if self.excellon_obj and self.excellon_tools:
            self.ui.exc_param_frame.setDisabled(False)
            tools = self._ordered_tool_ids()
        else:
            self.ui.exc_param_frame.setDisabled(True)
            self.ui.generate_cnc_button.setDisabled(True)
            self.ui.tools_table.setRowCount(2)
            tools = []

        n = len(tools)
        # we have (n+2) rows because there are 'n' tools, each a row, plus the last 2 rows for totals.
        self.ui.tools_table.setRowCount(n + 2)
        self.tool_row = 0

        tot_drill_cnt = 0
        tot_slot_cnt = 0

        for tool_no in tools:

            # Find no of drills for the current tool
            try:
                drill_cnt = len(self.excellon_tools[tool_no]["drills"])  # variable to store the nr of drills per tool
            except KeyError:
                drill_cnt = 0
            tot_drill_cnt += drill_cnt

            # Find no of slots for the current tool
            try:
                slot_cnt = len(self.excellon_tools[tool_no]["slots"])   # variable to store the nr of slots per tool
            except KeyError:
                slot_cnt = 0
            tot_slot_cnt += slot_cnt

            # Tool name/id
            exc_id_item = QtWidgets.QTableWidgetItem('%d' % int(tool_no))
            exc_id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled |
                                 QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.ui.tools_table.setItem(self.tool_row, 0, exc_id_item)

            # Tool Diameter
            dia_item = QtWidgets.QTableWidgetItem(str(self.dec_format(self.excellon_tools[tool_no]['tooldia'])))
            dia_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled |
                              QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.ui.tools_table.setItem(self.tool_row, 1, dia_item)

            # Number of drills per tool
            drill_count_item = QtWidgets.QTableWidgetItem('%d' % drill_cnt)
            drill_count_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.ui.tools_table.setItem(self.tool_row, 2, drill_count_item)

            # Tool unique ID
            tool_uid_item = QtWidgets.QTableWidgetItem(str(int(tool_no)))
            # ## REMEMBER: THIS COLUMN IS HIDDEN in UI
            self.ui.tools_table.setItem(self.tool_row, 3, tool_uid_item)

            # Number of slots per tool
            # if the slot number is zero is better to not clutter the GUI with zero's so we print a space
            slot_count_str = '%d' % slot_cnt if slot_cnt > 0 else ''
            slot_count_item = QtWidgets.QTableWidgetItem(slot_count_str)
            slot_count_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.ui.tools_table.setItem(self.tool_row, 4, slot_count_item)

            self.tool_row += 1

        # add a last row with the Total number of drills
        empty_1 = QtWidgets.QTableWidgetItem('')
        empty_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        empty_1_1 = QtWidgets.QTableWidgetItem('')
        empty_1_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_drill_count = QtWidgets.QTableWidgetItem(_('Total Drills'))
        label_tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        tot_drill_count = QtWidgets.QTableWidgetItem('%d' % tot_drill_cnt)
        tot_drill_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table.setItem(self.tool_row, 0, empty_1)
        self.ui.tools_table.setItem(self.tool_row, 1, label_tot_drill_count)
        self.ui.tools_table.setItem(self.tool_row, 2, tot_drill_count)  # Total number of drills
        self.ui.tools_table.setItem(self.tool_row, 4, empty_1_1)

        font = QtGui.QFont()
        font.setBold(True)
        # font.setWeight(75)

        for k in [1, 2]:
            self.ui.tools_table.item(self.tool_row, k).setForeground(QtGui.QColor(127, 0, 255))
            self.ui.tools_table.item(self.tool_row, k).setFont(font)

        self.tool_row += 1

        # add a last row with the Total number of slots
        empty_2 = QtWidgets.QTableWidgetItem('')
        empty_2.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        empty_2_1 = QtWidgets.QTableWidgetItem('')
        empty_2_1.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

        label_tot_slot_count = QtWidgets.QTableWidgetItem(_('Total Slots'))
        tot_slot_count = QtWidgets.QTableWidgetItem('%d' % tot_slot_cnt)
        label_tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        tot_slot_count.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table.setItem(self.tool_row, 0, empty_2)
        self.ui.tools_table.setItem(self.tool_row, 1, label_tot_slot_count)
        self.ui.tools_table.setItem(self.tool_row, 2, empty_2_1)
        self.ui.tools_table.setItem(self.tool_row, 4, tot_slot_count)  # Total number of slots

        for kl in [1, 2, 4]:
            self.ui.tools_table.item(self.tool_row, kl).setFont(font)
            self.ui.tools_table.item(self.tool_row, kl).setForeground(QtGui.QColor(0, 70, 255))

        # make the diameter column editable
        # for row in range(self.ui.tools_table.rowCount() - 2):
        #     self.ui.tools_table.item(row, 1).setFlags(
        #         QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.tools_table.resizeColumnsToContents()
        self.ui.tools_table.resizeRowsToContents()

        vertical_header = self.ui.tools_table.verticalHeader()
        vertical_header.hide()
        self.ui.tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.tools_table.horizontalHeader()
        self.ui.tools_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)

        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.tools_table.setSortingEnabled(False)

        self.ui.tools_table.setMinimumHeight(self.ui.tools_table.getHeight())
        self.ui.tools_table.setMaximumHeight(self.ui.tools_table.getHeight())

        # the first build (nothing selected before, or none of the remembered tools is there) selects all the tools;
        # a rebuild keeps the selection of the user
        self.ui.tools_table.clearSelection()
        rows_to_select = [r for r in range(len(tools))
                          if self.ui.tools_table.item(r, 3) is not None and
                          self.ui.tools_table.item(r, 3).text().isdigit() and
                          int(self.ui.tools_table.item(r, 3).text()) in previous_uids]
        if rows_to_select:
            # selectRow() would replace the selection: each row is added to the selection model
            sel_model = self.ui.tools_table.selectionModel()
            for r in rows_to_select:
                sel_model.select(self.ui.tools_table.model().index(r, 0),
                                 QtCore.QItemSelectionModel.SelectionFlag.Select |
                                 QtCore.QItemSelectionModel.SelectionFlag.Rows)
        else:
            self.ui.tools_table.selectAll()

        # #############################################################################################################
        # ################################### Build Exclusion Areas section ###########################################
        # #############################################################################################################
        self.ui_disconnect()

        # Only the areas added from an Excellon object are shown. The table row r is the area at
        # self._area_positions()[r] in the shared storage; the shown number is r + 1.
        area_positions = self._area_positions()
        e_len = len(area_positions)
        self.ui.exclusion_table.setRowCount(e_len)

        for area, storage_pos in enumerate(area_positions):
            area_dict = self.app.exc_areas.exclusion_areas_storage[storage_pos]

            area_id_item = QtWidgets.QTableWidgetItem('%d' % (area + 1))
            area_id_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.exclusion_table.setItem(area, 0, area_id_item)  # Area id

            object_item = QtWidgets.QTableWidgetItem('%s' % area_dict["obj_type"])
            object_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.exclusion_table.setItem(area, 1, object_item)  # Origin Object

            # strategy_item = QtWidgets.QTableWidgetItem('%s' % area_dict["strategy"])
            # strategy_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            strategy_item = FCComboBox2(policy=False)
            strategy_item.addItems([_("Around"), _("Over")])
            idx = 0 if area_dict["strategy"] == 'around' else 1
            strategy_item.setCurrentIndex(idx)
            self.ui.exclusion_table.setCellWidget(area, 2, strategy_item)  # Strategy

            overz_item = QtWidgets.QTableWidgetItem('%s' % area_dict["overz"])
            overz_item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.exclusion_table.setItem(area, 3, overz_item)  # Over Z

        # make the Overz column editable
        for row in range(e_len):
            self.ui.exclusion_table.item(row, 3).setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable |
                                                          QtCore.Qt.ItemFlag.ItemIsEditable |
                                                          QtCore.Qt.ItemFlag.ItemIsEnabled)

        self.ui.exclusion_table.resizeColumnsToContents()
        self.ui.exclusion_table.resizeRowsToContents()

        area_vheader = self.ui.exclusion_table.verticalHeader()
        area_vheader.hide()
        self.ui.exclusion_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        area_hheader = self.ui.exclusion_table.horizontalHeader()
        area_hheader.setMinimumSectionSize(10)
        area_hheader.setDefaultSectionSize(70)

        area_hheader.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        area_hheader.resizeSection(0, 20)
        area_hheader.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        area_hheader.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        area_hheader.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        # area_hheader.setStretchLastSection(True)
        self.ui.exclusion_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.ui.exclusion_table.setColumnWidth(0, 20)

        self.ui.exclusion_table.setMinimumHeight(self.ui.exclusion_table.getHeight())
        self.ui.exclusion_table.setMaximumHeight(self.ui.exclusion_table.getHeight())

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
        elif len(sel_rows) == 1:
            # update the QLabel that shows for which Tool we have the parameters in the UI form
            toolnr = int(self.ui.tools_table.item(list(sel_rows)[0], 0).text())
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), toolnr)
            )

    def on_object_changed(self):
        self.app.log.debug("ToolDrilling.on_object_changed()")
        # updated units
        self.units = self.app.app_units.upper()

        # load the Excellon object
        self.obj_name = self.ui.object_combo.currentText()

        # Get source object.
        obj = None
        try:
            obj = self.app.collection.get_by_name(self.obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(self.obj_name)))

        if obj is None:
            self._clear_for_no_object()
            return

        self.excellon_obj = obj
        self.app.collection.set_active(self.obj_name)
        self.ui.exc_param_frame.setDisabled(False)

        # the defaults and the preprocessor list are those of the new object
        self.default_data.clear()
        self.default_data.update(self.app.options)
        self.default_data.update(obj.obj_options)
        self.default_data['name'] = obj.obj_options['name'] + '_drill'
        self._populate_pp_list(obj)

        self.excellon_tools = obj.tools
        self.tools_are_object_tools = True
        # the extra objects and the hole areas belong to the previous object
        self._clear_hole_areas()
        self._populate_extra_objects()

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        autoload = self.app.options.get(
            "excellon_autoload_db",
            app_defaults.get("excellon_autoload_db", AppDefaults.factory_defaults.get("excellon_autoload_db")))
        # on_tool_db_load() builds the tool UI when it replaces the tools; if it fails or is cancelled, the UI is
        # built here from the new object anyway
        if not (autoload and self.on_tool_db_load()):
            self.build_tool_ui()

        self.update_ui()

        if not self.ui.tools_table.selectedItems():
            self.ui.generate_cnc_button.setDisabled(True)
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
        else:
            self.ui.generate_cnc_button.setDisabled(False)

    # ------------------------------------------------------------------------------------------------------------
    # More Excellon objects in one job ("Add objects", Advanced level)
    # ------------------------------------------------------------------------------------------------------------
    def _populate_extra_objects(self):
        """Fill the Add objects list with the other Excellon objects of the project; nothing is selected."""
        lst = self.ui.extra_objects_list
        lst.blockSignals(True)
        try:
            lst.clear()
            main_name = self.excellon_obj.obj_options['name'] if self.excellon_obj else None
            for obj in self.app.collection.get_list():
                if obj.kind == 'excellon' and obj.obj_options['name'] != main_name:
                    item = QtWidgets.QListWidgetItem(obj.obj_options['name'])
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, obj)
                    lst.addItem(item)
        finally:
            lst.blockSignals(False)
        self._merged_extras = ()
        # no extra object is selected: the per-tool parameters are editable again
        self._apply_extras_lock()

    def _apply_extras_lock(self):
        """While extra objects are selected the per-tool parameters are read-only: the tools are a merged copy."""
        locked = bool(self._merged_extras)
        self.ui.tool_param_frame.setDisabled(locked)
        self.ui.apply_param_to_all.setDisabled(locked)
        self.ui.extras_note_label.setVisible(locked)

    def _extra_objects(self):
        """The Excellon objects selected in the Add objects list (none in Beginner mode or without a main object)."""
        if self.excellon_obj is None or self._is_beginner():
            return []
        live = self.app.collection.get_list()
        extras = []
        for item in self.ui.extra_objects_list.selectedItems():
            # the item holds the object: a renamed object is still found, a deleted one is dropped
            obj = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if any(obj is o for o in live) and obj.kind == 'excellon' and obj is not self.excellon_obj:
                extras.append(obj)
        return extras

    def on_extra_objects_changed(self):
        """
        The tools table shows the tools of the main object and the extra objects merged by diameter (a copy: the
        objects are not changed), or the tools of the main object alone when no extra object is selected.
        """
        obj = self.excellon_obj
        if obj is None:
            return
        extras = self._extra_objects()
        ids = tuple(id(extra) for extra in extras)
        if ids == self._merged_extras:
            return
        self._merged_extras = ids

        if extras:
            merged, conflicts = DrillingGen.merge_object_tools(obj.tools, [extra.tools for extra in extras])
            for dia, tool_ids, keys in conflicts:
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("Tools {ids} have the same diameter ({dia}) but different parameters ({keys}).")
                    .format(ids=', '.join(str(i) for i in tool_ids), dia=dia, keys=', '.join(keys))))
            self.excellon_tools = merged
            self.tools_are_object_tools = False
        else:
            self.excellon_tools = obj.tools
            self.tools_are_object_tools = True
        self.build_tool_ui()
        self.update_ui()
        self._apply_extras_lock()

    # ------------------------------------------------------------------------------------------------------------
    # Holes inside areas: the areas are drawn like the exclusion areas but are kept apart from them
    # ------------------------------------------------------------------------------------------------------------
    def _clear_hole_areas(self):
        if self.hole_areas is None:
            return
        try:
            self.hole_areas.clear_shapes()
        except AttributeError:
            self.hole_areas.exclusion_areas_storage.clear()
        self.update_hole_areas_label()

    def update_hole_areas_label(self):
        count = len(self.hole_areas.exclusion_areas_storage) if self.hole_areas is not None else 0
        self.ui.hole_areas_label.setText(_("{n} hole areas").format(n=count) if count else _("No hole areas"))

    def on_add_hole_area_click(self):
        if self.excellon_obj is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Could not retrieve object"))
            return
        # no object geometry to test the areas against: the areas may cover the holes
        self.hole_areas.on_add_area_click(
            shape_button=self.ui.hole_area_shape_radio, overz_button=self.ui.over_z_entry,
            cnc_button=self._hole_area_button_stub, strategy_radio=self.ui.strategy_radio,
            solid_geo=[], obj_type='holes', owner=EXCLUSION_OWNER)

    def on_clear_hole_areas_click(self):
        self._clear_hole_areas()

    def _selected_hole_areas(self):
        """The polygons that select the holes; None when the job drills all the holes (Beginner or Holes: All)."""
        if self._is_beginner() or self.ui.holes_radio.get_value() != 'area' or self.hole_areas is None:
            return None
        return [area['shape'] for area in self.hole_areas.exclusion_areas_storage]

    def _clear_for_no_object(self):
        """There is no Excellon object (none is loaded or it was deleted): empty table, nothing to edit or generate."""
        self.excellon_obj = None
        self.excellon_tools = {}
        self.tools_are_object_tools = False
        self.build_tool_ui()
        self.ui.exc_param_frame.setDisabled(True)
        self.ui.generate_cnc_button.setDisabled(True)
        self.ui.tool_data_label.setText(
            "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
        )

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                sel_obj = current.indexes()[0].internalPointer().obj
                name = sel_obj.obj_options['name']
                kind = sel_obj.kind
                if kind in ['geometry', 'excellon']:
                    self.ui.object_combo.set_value(name)
            except Exception:
                pass

    def ui_connect(self):
        # connecting twice would call a slot twice: start from a clean state
        self.ui_disconnect()

        # When object selection on canvas change
        # self.app.collection.view.selectionModel().selectionChanged.connect(self.on_object_selection_changed)
        try:
            self.app.proj_selection_changed.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

        # Area Exception - exclusion shape added signal
        try:
            self.app.exc_areas.e_shape_modified.disconnect(self.update_exclusion_table)
        except (TypeError, RuntimeError, AttributeError):
            pass
        self.app.exc_areas.e_shape_modified.connect(self.update_exclusion_table)

        # rows selected
        self.ui.tools_table.selectionModel().selectionChanged.connect(self.on_row_selection_change)
        self.ui.tools_table.horizontalHeader().sectionClicked.connect(self.on_toggle_all_rows)

        # Tool Parameters
        for opt in self.tool_form_fields:
            current_widget = self.tool_form_fields[opt]
            if isinstance(current_widget, FCCheckBox):
                current_widget.stateChanged.connect(self.form_to_storage)
            if isinstance(current_widget, RadioSet):
                current_widget.activated_custom.connect(self.form_to_storage)
            elif isinstance(current_widget, FCDoubleSpinner) or isinstance(current_widget, FCSpinner):
                current_widget.returnPressed.connect(self.form_to_storage)
            elif isinstance(current_widget, FCComboBox):
                current_widget.currentIndexChanged.connect(self.form_to_storage)

        # General Parameters
        for opt in self.general_form_fields:
            current_widget2 = self.general_form_fields[opt]
            if isinstance(current_widget2, FCCheckBox):
                current_widget2.stateChanged.connect(self.form_to_storage)
            if isinstance(current_widget2, RadioSet):
                current_widget2.activated_custom.connect(self.form_to_storage)
            elif isinstance(current_widget2, FCDoubleSpinner) or isinstance(current_widget2, FCSpinner):
                current_widget2.returnPressed.connect(self.form_to_storage)
            elif isinstance(current_widget2, FCComboBox):
                current_widget2.currentIndexChanged.connect(self.form_to_storage)
            elif isinstance(current_widget2, NumericalEvalEntry):
                current_widget2.editingFinished.connect(self.form_to_storage)
            elif isinstance(current_widget2, NumericalEvalTupleEntry):
                current_widget2.editingFinished.connect(self.form_to_storage)

        self.ui.order_combo.currentIndexChanged.connect(self.on_order_changed)

        # Exclusion Table widgets connect
        for row in range(self.ui.exclusion_table.rowCount()):
            cell_widget = self.ui.exclusion_table.cellWidget(row, 2)
            if cell_widget is not None:
                cell_widget.currentIndexChanged.connect(self.on_exclusion_table_strategy)

        self.ui.exclusion_table.itemChanged.connect(self.on_exclusion_table_overz)

    def ui_disconnect(self):
        # When object selection on canvas change
        # self.app.collection.view.selectionModel().selectionChanged.disconnect()
        try:
            self.app.proj_selection_changed.disconnect(self.on_object_selection_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass

        try:
            self.app.exc_areas.e_shape_modified.disconnect(self.update_exclusion_table)
        except (TypeError, RuntimeError, AttributeError):
            pass

        # rows selected
        try:
            self.ui.tools_table.selectionModel().selectionChanged.disconnect(self.on_row_selection_change)
        except (TypeError, AttributeError, RuntimeError):
            pass
        try:
            self.ui.tools_table.horizontalHeader().sectionClicked.disconnect(self.on_toggle_all_rows)
        except (TypeError, AttributeError):
            pass

        # tool table widgets
        for row in range(self.ui.tools_table.rowCount()):

            try:
                self.ui.tools_table.cellWidget(row, 2).currentIndexChanged.disconnect()
            except (TypeError, AttributeError):
                pass

        # Tool Parameters
        for opt in self.tool_form_fields:
            current_widget = self.tool_form_fields[opt]
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
            elif isinstance(current_widget, FCComboBox):
                try:
                    current_widget.currentIndexChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass

        # General Parameters
        for opt in self.general_form_fields:
            current_widget2 = self.general_form_fields[opt]
            if isinstance(current_widget2, FCCheckBox):
                try:
                    current_widget2.stateChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            if isinstance(current_widget2, RadioSet):
                try:
                    current_widget2.activated_custom.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget2, FCDoubleSpinner) or isinstance(current_widget2, FCSpinner):
                try:
                    current_widget2.returnPressed.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget2, FCComboBox):
                try:
                    current_widget2.currentIndexChanged.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget2, NumericalEvalEntry):
                try:
                    current_widget2.editingFinished.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
            elif isinstance(current_widget2, NumericalEvalTupleEntry):
                try:
                    current_widget2.editingFinished.disconnect(self.form_to_storage)
                except (TypeError, ValueError):
                    pass
        try:
            self.ui.order_combo.currentIndexChanged.disconnect()
        except (TypeError, ValueError):
            pass

        # Exclusion Table widgets disconnect
        for row in range(self.ui.exclusion_table.rowCount()):
            try:
                self.ui.exclusion_table.cellWidget(row, 2).currentIndexChanged.disconnect()
            except (TypeError, AttributeError):
                pass

        try:
            self.ui.exclusion_table.itemChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    def on_tool_db_load(self):

        filename = self.app.tools_database_path()

        try:
            self.tools_db_dict = load_tools_database(filename, self.app.options, target=2,
                                                    units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))
            return False

        if not self.tools_db_dict:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tools DB empty."))
            return False

        return self.replace_tools()

    def replace_tools(self):
        self.app.log.debug("ToolDrilling.replace_tools()")

        if self.excellon_obj:
            new_tools_dict = deepcopy(self.excellon_tools)

            for orig_tool, orig_tool_val in self.excellon_tools.items():
                orig_tooldia = orig_tool_val['tooldia']

                tool_found = 0

                # look in database tools
                matches = matching_tools(self.tools_db_dict, target=2, diameter=orig_tooldia)
                for db_tool, db_tool_val in matches.items():
                    db_tooldia = db_tool_val['tooldia']
                    low_limit = float(db_tool_val['data']['tol_min'])
                    high_limit = float(db_tool_val['data']['tol_max'])

                    # test if the targeted tool is Drilling Tool, if not, skip to next tool
                    targeted_tool = db_tool_val['data']['tool_target']
                    if targeted_tool not in (0, 2):
                        continue

                    # if we find a tool with the same diameter in the Tools DB just update it's data
                    if orig_tooldia == db_tooldia:
                        tool_found += 1
                        for d in db_tool_val['data']:
                            if d.find('tools_drill_') == 0:
                                new_tools_dict[orig_tool]['data'][d] = deepcopy(db_tool_val['data'][d])
                            elif d.find('tools_') == 0:
                                # don't need data for other App Tools; this tests after 'tools_drill_'
                                continue
                            else:
                                new_tools_dict[orig_tool]['data'][d] = deepcopy(db_tool_val['data'][d])
                    # search for a tool that has a tolerance that the tool fits in
                    elif high_limit >= orig_tooldia >= low_limit:
                        tool_found += 1
                        new_tools_dict[orig_tool]['tooldia'] = db_tooldia
                        for d in db_tool_val['data']:
                            if d.find('tools_drill_') == 0:
                                new_tools_dict[orig_tool]['data'][d] = deepcopy(db_tool_val['data'][d])
                            elif d.find('tools_') == 0:
                                # don't need data for other App Tools; this tests after 'tools_drill_'
                                continue
                            else:
                                new_tools_dict[orig_tool]['data'][d] = deepcopy(db_tool_val['data'][d])

                if tool_found > 1:
                    self.app.inform.emit(
                        '[WARNING_NOTCL] %s' % _("Cancelled.\n"
                                                 "Multiple tools for one tool diameter found in Tools Database."))
                    self.blockSignals(False)
                    return False

            # more than one original tool can fit in the tolerance of the same DB tool: the merged tool keeps the data
            # of the first one, tell the user which data of the others is lost
            for dia, tool_ids, keys in DrillingGen.diameter_conflicts(new_tools_dict):
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("Tools %s have the same diameter %s but different data, the data of the first one is used. "
                      "Different: %s") % (', '.join(str(i) for i in tool_ids), dia, ', '.join(keys))))
            self.excellon_tools = merge_same_diameter_tools(new_tools_dict)
            self.tools_are_object_tools = False
            self.build_tool_ui()
            return True
        return False

    def _selected_tool_rows(self):
        """The selected rows of the Tools table that are tool rows (not the 2 totals rows)."""
        n_tools = max(self.ui.tools_table.rowCount() - 2, 0)
        return sorted(set(
            idx.row() for idx in self.ui.tools_table.selectionModel().selectedIndexes() if idx.row() < n_tools))

    def on_toggle_all_rows(self):
        """
        will toggle the selection of all rows in Tools table

        :return:
        """
        # the last 2 rows of the table are the totals; they are not tools and cannot be selected
        n_tools = self.ui.tools_table.rowCount() - 2
        if n_tools <= 0:
            return

        if len(self._selected_tool_rows()) == n_tools:
            self.ui.tools_table.clearSelection()
            self.ui.exc_param_frame.setDisabled(True)

            self.ui.generate_cnc_button.setDisabled(True)
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
            )
        else:
            self.ui.tools_table.selectAll()
            self.ui.exc_param_frame.setDisabled(False)
            self.ui.generate_cnc_button.setDisabled(False)
            self.ui.tool_data_label.setText(
                "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
            )

    def on_row_selection_change(self):
        # One tool: its parameters and name. More tools (also a keyboard selection): the label says "Multiple Tools"
        # and the form shows the first selected tool.
        self.update_ui()

    def update_ui(self):
        self.blockSignals(True)
        self.ui_disconnect()
        try:
            sel_rows = self._selected_tool_rows()

            if not sel_rows:
                self.ui.generate_cnc_button.setDisabled(True)
                self.ui.exc_param_frame.setDisabled(True)
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("No Tool Selected"))
                )
                return

            self.ui.generate_cnc_button.setDisabled(False)
            self.ui.exc_param_frame.setDisabled(False)

            # the form shows the data of the first selected tool
            try:
                item = self.ui.tools_table.item(sel_rows[0], 3)
                tooluid = int(item.text())
                tool_data = self.excellon_tools[tooluid]['data']
            except Exception as e:
                self.app.log.error("Tool missing. Add a tool in the Tool Table. %s" % str(e))
                return

            if len(sel_rows) == 1:
                # update the QLabel that shows for which Tool we have the parameters in the UI form
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s %d</font></b>" % (_('Parameters for'), _("Tool"), tooluid)
                )
            else:
                self.ui.tool_data_label.setText(
                    "<b>%s: <font color='#0000FF'>%s</font></b>" % (_('Parameters for'), _("Multiple Tools"))
                )
            self.storage_to_form(tool_data)
        finally:
            self.blockSignals(False)
            self.ui_connect()

    def storage_to_form(self, dict_storage):
        """
        Will update the GUI with data from the "storage" in this case the dict self.tools

        :param dict_storage:    A dictionary holding the data relevant for gnerating Gcode from Excellon
        :type dict_storage:     dict
        :return:                None
        :rtype:
        """
        # a key that the storage lacks (an old object) shows its default, not the value of the previous tool
        for fields, source in ((self.tool_form_fields, "tool parameters"),
                               (self.general_form_fields, "common parameters")):
            for form_key, widget in fields.items():
                value = dict_storage[form_key] if form_key in dict_storage else self._default(form_key)
                try:
                    self._set_field(widget, value)
                except Exception as e:
                    self.app.log.error("ToolDrilling.storage_to_form() %s --> %s" % (source, str(e)))

        # the preprocessor combo changed by code: show the fields that depend on it
        self.on_pp_changed()

    def history_step(self, label):
        """
        The Undo / Redo step (a snapshot of the Excellon object) for an action of the user on the tools (apply to all,
        Order). The parameter edits are not recorded: each widget has its own undo. It is
        used only in the handlers of the user's actions, never in a UI rebuild. If self.excellon_tools is not the dict
        of the object (after a Tools DB load or a drag and drop of the rows) the edit does not reach the object and
        there is nothing to record.
        """
        obj = self.excellon_obj
        if obj is None or not self.tools_are_object_tools or self.excellon_tools is not getattr(obj, 'tools', None):
            return nullcontext()
        return history_of(self.app).snapshot(label, [obj])

    def on_object_tools_replaced(self, obj):
        """Undo / Redo restores the tools of the Excellon object as a new dict: take it again and show it."""
        if obj is not self.excellon_obj or not self.tools_are_object_tools:
            return
        if self.excellon_tools is not obj.tools:
            self.excellon_tools = obj.tools
            self.build_tool_ui()
            self.update_ui()

    def _read_field(self, widget):
        """
        The value of a form widget. A blank X,Y / Start Z entry gives None. Text that cannot be read also gives None,
        and the user gets a warning (the value is not stored as a number).
        """
        value = widget.get_value()
        if value is None and isinstance(widget, (NumericalEvalTupleEntry, NumericalEvalEntry)) and \
                widget.text().strip():
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Could not interpret the entered text"), widget.text()))
        return value

    def form_to_storage(self):
        """
        Will update the 'storage' attribute which is the dict self.tools with data collected from GUI

        :return:    None
        :rtype:
        """
        if self.ui.tools_table.rowCount() == 2:
            # there is no tool in tool table so we can't save the GUI elements values to storage
            # Excellon Tool Table has 2 rows by default
            return

        widget_changed = self.sender()
        if widget_changed is None:
            return
        option_changed = self.name2option.get(widget_changed.objectName())
        if option_changed is None:
            return

        self.ui_disconnect()
        try:
            # update tool parameters: only the selected tools. A key that the tool lacks (an old object) is added.
            if option_changed in self.tool_form_fields:
                new_option_value = self._read_field(self.tool_form_fields[option_changed])
                for row in self._selected_tool_rows():
                    item = self.ui.tools_table.item(row, 3)
                    if item is None:
                        continue
                    tool = self.excellon_tools.get(int(item.text()))
                    if tool is not None:
                        tool.setdefault('data', {})[option_changed] = new_option_value

            # update general parameters
            # they are updated for all tools
            if option_changed in self.general_form_fields:
                new_option_value = self._read_field(self.general_form_fields[option_changed])
                for tool in self.excellon_tools.values():
                    tool.setdefault('data', {})[option_changed] = new_option_value
        finally:
            self.ui_connect()

    def get_selected_tools_table_items(self):
        """
        Returns a list of lists, each list in the list is made out of row elements

        :return:    List of table_tools items.
        :rtype:     list
        """
        table_tools_items = []

        rows = set()
        for x in self.ui.tools_table.selectedItems():
            rows.add(x.row())

        for row in sorted(rows):
            elem = []

            for column in range(self.ui.tools_table.columnCount()):
                if column == 3:
                    # disregard this column since it's the toolID
                    continue

                # the text of a column must not carry over from the previous column
                txt = ''
                try:
                    txt = self.ui.tools_table.item(row, column).text()
                except AttributeError:
                    try:
                        txt = self.ui.tools_table.cellWidget(row, column).currentText()
                    except AttributeError:
                        pass
                elem.append(txt)
            table_tools_items.append(deepcopy(elem))
        for item in table_tools_items:
            item[0] = str(item[0])
        return table_tools_items

    def on_apply_param_to_all_clicked(self):
        n_tools = self.ui.tools_table.rowCount() - 2
        if n_tools <= 0:
            # there is no tool in tool table so we can't save the GUI elements values to storage
            self.app.log.debug("ToolDrilling.on_apply_param_to_all_clicked() --> no tool in Tools Table, aborting.")
            return

        # the source is the current tool row; if that is a totals row or nothing, the first selected tool
        row = self.ui.tools_table.currentRow()
        if not 0 <= row < n_tools:
            selected = self._selected_tool_rows()
            if not selected:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please select one or more tools from the list and "
                                                              "try again."))
                return
            row = selected[0]

        try:
            source = self.excellon_tools[int(self.ui.tools_table.item(row, 3).text())]['data']
        except (AttributeError, KeyError, ValueError):
            self.app.log.debug("ToolDrilling.on_apply_param_to_all_clicked() --> the tool row has no data, aborting.")
            return
        if not source:
            return
        temp_tool_data = deepcopy(source)

        self.blockSignals(True)
        try:
            with self.history_step(_("Drilling: apply the parameters to all tools")):
                for tooluid_val in self.excellon_tools.values():
                    tooluid_val['data'] = deepcopy(temp_tool_data)
        finally:
            self.blockSignals(False)

        self.app.inform.emit('[success] %s' % _("Current Tool parameters were applied to all tools."))

    def on_order_changed(self, order):
        # The order is a view of the tools table (and the order of the job): it does not change the tools, so it is
        # not an Undo / Redo step.
        self.build_tool_ui()
        self.update_ui()

    def on_tooltable_cellwidget_change(self):
        cw = self.sender()
        assert isinstance(cw, QtWidgets.QComboBox), \
            "Expected a QtWidgets.QComboBox, got %s" % isinstance(cw, QtWidgets.QComboBox)

        cw_index = self.ui.tools_table.indexAt(cw.pos())
        cw_row = cw_index.row()
        cw_col = cw_index.column()

        current_uid = int(self.ui.tools_table.item(cw_row, 3).text())

        # if the sender is in the column with index 2 then we update the tool_type key
        if cw_col == 2:
            tt = cw.currentText()
            typ = 'Iso' if tt == 'V' else 'Rough'

            self.excellon_tools[current_uid].update({
                'type': typ,
                'tool_type': tt,
            })

    def on_pp_changed(self):
        current_pp = self.ui.pp_excellon_name_cb.get_value()
        self.ui.on_pp_changed(current_pp, self.ui.level.isChecked())

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
            self.points = []
            self.poly_drawn = False
            self.delete_moving_selection_shape()
            self.delete_tool_selection_shape()

    def _area_positions(self):
        """
        The positions in the shared exclusion areas storage of the areas that Drilling shows and uses: those drawn in
        this plugin (owner 'drilling'). The areas of the Milling plugin are in the same storage, also those on an
        Excellon object, so obj_type alone does not tell them apart.
        The row r of the exclusion table is the area at _area_positions()[r], so the table works with any 'idx'.
        """
        return [pos for pos, area in enumerate(self.app.exc_areas.exclusion_areas_storage)
                if area.get('owner') == EXCLUSION_OWNER]

    def _drill_exclusion_areas(self):
        """The exclusion areas (dicts) that apply to a drilling job."""
        storage = self.app.exc_areas.exclusion_areas_storage
        return [storage[pos] for pos in self._area_positions()]

    def _area_at_row(self, row):
        """The storage entry of a row of the exclusion table, None for a row out of the table."""
        positions = self._area_positions()
        if not 0 <= row < len(positions):
            return None
        return self.app.exc_areas.exclusion_areas_storage[positions[row]]

    def _selected_area_rows(self):
        return sorted(set(idx.row() for idx in self.ui.exclusion_table.selectionModel().selectedIndexes()))

    def on_add_area_click(self):
        if self.excellon_obj is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Could not retrieve object"))
            return

        shape_button = self.ui.area_shape_radio
        overz_button = self.ui.over_z_entry
        strategy_radio = self.ui.strategy_radio
        cnc_button = self.ui.generate_cnc_button
        solid_geo = self.excellon_obj.solid_geometry
        obj_type = self.excellon_obj.kind

        self.app.exc_areas.on_add_area_click(
            shape_button=shape_button, overz_button=overz_button, cnc_button=cnc_button, strategy_radio=strategy_radio,
            solid_geo=solid_geo, obj_type=obj_type, owner=EXCLUSION_OWNER)

    def on_clear_area_click(self):
        # only the Drilling areas: the areas of the Milling plugin stay
        positions = self._area_positions()
        if not positions:
            self.app.inform.emit("[WARNING_NOTCL] %s" % _("Delete failed. There are no exclusion areas to delete."))
            return

        self._delete_area_positions(positions)

    def on_delete_sel_areas(self):
        sel_rows = self._selected_area_rows()
        if not sel_rows:
            self.app.inform.emit("[WARNING_NOTCL] %s" % _("Delete failed. Nothing is selected."))
            return

        positions = self._area_positions()
        self._delete_area_positions([positions[row] for row in sel_rows if 0 <= row < len(positions)])

    def _delete_area_positions(self, positions):
        """Delete the areas at these positions of the shared storage and refresh the tables."""
        if not positions:
            return
        exc_areas = self.app.exc_areas
        if exc_areas.cnc_button is None:
            # the areas can come from a project: the button was never given by an 'Add Area' click
            exc_areas.cnc_button = self.ui.generate_cnc_button
        exc_areas.delete_sel_shapes(idxs=list(positions))
        exc_areas.e_shape_modified.emit()

    def on_copy_exclusion_area_coords(self):
        sel_rows = self._selected_area_rows()
        if len(sel_rows) != 1:
            self.app.inform.emit("[WARNING_NOTCL] %s" % _("Only one selected row is allowed."))
            return

        area = self._area_at_row(sel_rows[0])
        if area is None:
            return
        poly_coords = list(area['shape'].exterior.coords)

        self.app.clipboard.setText(str(poly_coords))
        self.app.inform.emit('[success] %s' % _("Copied to clipboard."))

    def draw_sel_shape(self):
        sel_rows = self._selected_area_rows()

        self.delete_sel_shape()

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        face = self.app.options.get(
            'global_sel_fill',
            app_defaults.get('global_sel_fill', AppDefaults.factory_defaults.get('global_sel_fill')))[:-2] + \
            str(hex(int(0.2 * 255)))[2:]
        outline = self.app.options.get(
            'global_sel_line',
            app_defaults.get('global_sel_line', AppDefaults.factory_defaults.get('global_sel_line')))[:-2] + \
            str(hex(int(0.8 * 255)))[2:]

        for row in sel_rows:
            area = self._area_at_row(row)
            if area is None:
                continue
            self.app.sel_shapes.add(area['shape'], color=outline, face_color=face, update=True, layer=0,
                                    tolerance=None)
        self.app.sel_shapes.redraw()

    def clear_selection(self):
        self.app.delete_selection_shape()
        # self.ui.exclusion_table.clearSelection()

    def delete_sel_shape(self):
        self.app.delete_selection_shape()

    def update_exclusion_table(self):
        self.exclusion_area_cb_is_checked = True if self.ui.exclusion_cb.isChecked() else False

        self.build_tool_ui()
        self.ui.exclusion_cb.set_value(self.exclusion_area_cb_is_checked)

    def on_strategy(self, val):
        if val == 'around':
            self.ui.over_z_label.setDisabled(True)
            self.ui.over_z_entry.setDisabled(True)
        else:
            self.ui.over_z_label.setDisabled(False)
            self.ui.over_z_entry.setDisabled(False)

    def exclusion_table_toggle_all(self):
        """
        will toggle the selection of all rows in Exclusion Areas table

        :return:
        """
        if self._selected_area_rows():
            self.ui.exclusion_table.clearSelection()
            self.delete_sel_shape()
        else:
            self.ui.exclusion_table.selectAll()
            self.draw_sel_shape()

    def on_exclusion_table_overz(self, current_item):
        self.ui_disconnect()
        try:
            current_row = current_item.row()
            area = self._area_at_row(current_row)
            try:
                d = float(self.ui.exclusion_table.item(current_row, 3).text())
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    d = float(self.ui.exclusion_table.item(current_row, 3).text().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wrong value format entered, use a number."))
                    # show the stored value again
                    self.build_ui_sig.emit()
                    return
            except AttributeError:
                return

            if area is None:
                return
            area['overz'] = self.app.dec_format(d, self.decimals)

            self.app.inform.emit('[success] %s' % _("Value edited in Exclusion Table."))
            self.build_ui_sig.emit()
        finally:
            self.ui_connect()

    def on_exclusion_table_strategy(self):
        cw = self.sender()
        if cw is None:
            return
        cw_row = self.ui.exclusion_table.indexAt(cw.pos()).row()
        area = self._area_at_row(cw_row)

        self.ui_disconnect()
        try:
            if area is not None:
                strategy = cw.currentIndex()
                area['strategy'] = "around" if strategy == 0 else 'over'
                self.app.inform.emit('[success] %s' % _("Value edited in Exclusion Table."))
        finally:
            self.ui_connect()
        self.build_ui_sig.emit()

    @staticmethod
    def process_slot_as_drills(slot, overlap, add_last_pt=False):
        return DrillingGen.process_slot_as_drills(slot, overlap, add_last_pt)

    def is_valid_excellon(self):
        slots_as_drills = self.ui.drill_slots_cb.get_value()

        has_drills = None
        for tool_key, tool_dict in self.excellon_tools.items():
            if 'drills' in tool_dict and tool_dict['drills']:
                has_drills = True
                break
        has_slots = None
        for tool_key, tool_dict in self.excellon_tools.items():
            if 'slots' in tool_dict and tool_dict['slots']:
                has_slots = True
                break

        if not has_drills:
            if slots_as_drills and has_slots:
                return True
            else:
                return False
        return True

    def get_selected_tools_uid(self):
        """
        Return a list of the selected tools UID from the Tool Table
        """
        selected_uid = set()
        for sel_it in self.ui.tools_table.selectedItems():
            uid = int(self.ui.tools_table.item(sel_it.row(), 3).text())
            selected_uid.add(uid)
        return list(selected_uid)

    def create_drill_points(self, selected_tools, selected_sorted_tools, excellon_tools=None):
        """
        The drill points of the selected tools (and their slots, for the tools that drill slots). The slot spacing and
        the last drill are the values of each tool.
        """
        if excellon_tools is None:
            excellon_tools = self.excellon_tools
        self.app.log.debug("Found %d TOOLS with drills." % sum(
            bool(tl_dict.get('drills')) for tool_key, tl_dict in excellon_tools.items()
            if tool_key in selected_tools))
        points = DrillingGen.create_drill_points(
            selected_tools, selected_sorted_tools, excellon_tools, None, None,
            slot_processor=self.process_slot_as_drills)
        self.app.log.debug("Found %d TOOLS with drills after converting slots to drills." % len(points))
        return points

    def check_intersection(self, points, excellon_tools=None):
        """True if a drill point is inside an exclusion area of the Drilling plugin (grown as for the travel lines)."""
        if excellon_tools is None:
            excellon_tools = self.excellon_tools
        areas = self._drill_exclusion_areas()
        for tool_key in points:
            if not points[tool_key]:
                continue
            pts = np.empty(len(points[tool_key]), dtype=object)
            pts[:] = points[tool_key]
            for area in areas:
                # a drill point inside a grown area would start or end a travel inside it
                grown = self.app.exc_areas.grown_shape(area['shape'], excellon_tools[tool_key]['tooldia'])
                if shapely.intersects(grown, pts).any():
                    return True
        return False

    def on_generate_cnc_job(self):
        obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            obj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            return

        if obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s.' % _("Object not found"))
            return

        # update the Excellon Tools
        # we don't do it here since it will break any changes we've done in the Drilling Tool UI
        # self.excellon_tools = obj.tools

        # the bounds of the job: the union of the bounds of the main object and the extra objects
        job_objects = [obj] + self._extra_objects()
        xmin = min(o.obj_options['xmin'] for o in job_objects)
        ymin = min(o.obj_options['ymin'] for o in job_objects)
        xmax = max(o.obj_options['xmax'] for o in job_objects)
        ymax = max(o.obj_options['ymax'] for o in job_objects)

        job_name = obj.obj_options["name"] + "_cnc"
        obj.pp_excellon_name = self.ui.pp_excellon_name_cb.get_value()

        if self.is_valid_excellon() is False:
            self.app.log.debug("ToolDrilling.on_cnc_button_click() --> "
                               "The loaded Excellon file has no drills ...")
            self.app.inform.emit('[ERROR_NOTCL] %s...' % _('The loaded Excellon file has no drills'))
            return

        # Get the tools from the Tool Table
        selected_tools_ids = self.get_selected_tools_uid()
        if not selected_tools_ids:
            # if there is a single tool in the table (remember that the last 2 rows are for totals and do not count in
            # tool number) it means that there are 3 rows (1 tool and 2 totals).
            # in this case regardless of the selection status of that tool, use it.
            if self.ui.tools_table.rowCount() == 3:
                first_selected = int(self.ui.tools_table.item(0, 3).text())
                selected_tools_ids.append(first_selected)
            else:
                msg = '[ERROR_NOTCL] %s' % _("Please select one or more tools from the list and try again.")
                self.app.inform.emit(msg)
                return

        # a stale row of the table may name a tool that is not there anymore
        selected_tools_ids = [t_id for t_id in selected_tools_ids if t_id in self.excellon_tools]
        if not selected_tools_ids:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The selected tools are not in the Excellon object."))
            return

        # #############################################################################################################
        # TOOLS: the selected ones, in the order of the Order combo (by diameter, or as in the file)
        # #############################################################################################################
        sel_tools = [t_id for t_id in self._ordered_tool_ids() if t_id in selected_tools_ids]
        sorted_tools = [(t_id, float(self.excellon_tools[t_id]['tooldia'])) for t_id in sel_tools]
        self.app.log.debug("Tools sorted are: %s" % str(sel_tools))

        # determine if we have toolchange event or not: only the selected tools count
        has_toolchange = self._has_toolchange(sel_tools)
        if not self._validate_xy_fields(sel_tools):
            return

        # The tools of this job: only the selected ones, with the data the job generates with (in Beginner mode the
        # safe values of the hidden options). The tools of the object and the plugin are not changed.
        gen_tools = {t_id: dict(self.excellon_tools[t_id], data=self._effective_tool_data(t_id)) for t_id in sel_tools}

        # #############################################################################################################
        # #### Create Points (Group by tool): a dictionary of shapely Point geo elements grouped by tool number #######
        # #############################################################################################################
        self.app.inform.emit(_("Creating a list of points to drill..."))

        # points is a dictionary: keys are tools ad values are lists of Shapely Points
        points = self.create_drill_points(selected_tools=sel_tools, selected_sorted_tools=sorted_tools,
                                          excellon_tools=gen_tools)

        # the slots that are not drilled: tell the user
        for t_id in sel_tools:
            tool = gen_tools[t_id]
            if tool.get('slots') and not tool['data'].get('tools_drill_drill_slots'):
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("{nr} slots of tool {tool} are not drilled. Enable 'Drill slots' or use the Milling plugin.")
                    .format(nr=len(tool['slots']), tool=t_id)))

        # Holes: Inside area. Only the holes inside the drawn areas, before the job transform.
        hole_areas = self._selected_hole_areas()
        if hole_areas is not None:
            if not hole_areas:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Holes: Inside area needs at least one area. Add a hole "
                                                            "area or select Holes: All."))
                return
            points = DrillingGen.points_in_areas(points, hole_areas)

        # only the tools that have something to drill take part in the job (first / last tool, toolchanges)
        gen_ids = [t_id for t_id in sel_tools if points.get(t_id)]
        if not gen_ids:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The selected tools have no drill points."))
            return

        # the job XY transform (mirror, offset, array) of the drill points; the Excellon object is not changed. The
        # spot pass below takes its points from these, so it is transformed too.
        try:
            points = DrillingGen.transform_job_points(points, gen_tools[gen_ids[0]]['data'])
        except ValueError as e:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
            return

        # check if there are drill points in the exclusion areas (only if the tools use the areas)
        use_areas = bool(gen_tools[gen_ids[0]]['data'].get('tools_drill_area_exclusion'))
        if use_areas and self._drill_exclusion_areas() and self.check_intersection(points, gen_tools) is True:
            self.app.inform.emit("[ERROR_NOTCL] %s %s" % (_("Failed."), _("Drill points inside the exclusion zones.")))
            return 'fail'

        # tools with the same diameter that have different parameters
        for dia, ids, keys in DrillingGen.diameter_conflicts({t_id: gen_tools[t_id] for t_id in gen_ids}):
            self.app.inform.emit('[WARNING_NOTCL] %s' % (
                _("Tools {ids} have the same diameter ({dia}) but different parameters ({keys}).")
                .format(ids=', '.join(str(i) for i in ids), dia=dia, keys=', '.join(keys))))

        # without a toolchange the parameters of the first tool are used for all the tools
        if not has_toolchange and len(gen_ids) > 1:
            first_data = gen_tools[gen_ids[0]]['data']
            differ = [key for key in ('tools_drill_cutz', 'tools_drill_feedrate_z', 'tools_drill_spindlespeed')
                      if any(gen_tools[t_id]['data'].get(key) != first_data.get(key) for t_id in gen_ids[1:])]
            if differ:
                self.app.inform.emit('[WARNING_NOTCL] %s' % (
                    _("There is no toolchange: the parameters of tool {tool} (Cut Z, Feedrate Z, Spindle speed) are "
                      "used for all the selected tools.").format(tool=gen_ids[0])))

        # the spot drilling pass: a job option, one extra tool that drills all the holes of the selected tools first.
        # It needs the toolchange (the spot drill is another tool): without it the pass is left out. The laser
        # preprocessors have no tools to change: they ignore it.
        if gen_tools[gen_ids[0]]['data'].get('tools_drill_spot') and \
                'laser' not in str(self.ui.pp_excellon_name_cb.get_value()).lower():
            if not has_toolchange:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The spot drilling pass needs a toolchange: it is left out. Enable the toolchange to use it."))
            else:
                spot_id = DrillingGen.next_tool_id(self.excellon_tools)
                gen_tools[spot_id] = DrillingGen.spot_tool(gen_tools[gen_ids[0]]['data'])
                points[spot_id] = [pt for t_id in gen_ids for pt in points[t_id]]
                gen_ids = [spot_id] + gen_ids

        # a value that makes dangerous or wrong G-code refuses the job; without a toolchange only the data of the first
        # tool is used
        laser = 'laser' in str(self.ui.pp_excellon_name_cb.get_value()).lower()
        refused = False
        for t_id in (gen_ids if has_toolchange else gen_ids[:1]):
            for message in DrillingGen.tool_setting_problems(gen_tools[t_id]['data'], laser):
                refused = True
                self.app.inform.emit('[ERROR_NOTCL] %s (%s %s)' % (message, _("tool"), t_id))
        if refused:
            return

        # #############################################################################################################
        # General Parameters
        # #############################################################################################################
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        used_exc_optim_type = self.app.options.get(
            "excellon_optimization_type",
            app_defaults.get("excellon_optimization_type",
                            AppDefaults.factory_defaults.get("excellon_optimization_type")))
        current_platform = platform.architecture()[0]
        if current_platform != '64bit':
            used_exc_optim_type = 'T'

        # #############################################################################################################
        # GCODE creation
        # #############################################################################################################
        self.app.inform.emit('%s...' % _("Starting G-Code"))

        # Object initialization function for app.app_obj.new_object()
        def job_init(cnc_job_obj, app_obj):
            assert cnc_job_obj.kind == 'cncjob', "Initializer expected a CNCJobObject, got %s" % type(cnc_job_obj)
            app_obj.inform.emit(_("Generating CNCJob..."))

            # the G-code and its parsed form of this job only
            total_gcode = ''
            total_gcode_parsed = []

            # #########################################################################################################
            # fill the data into the self.tools dictionary attribute of the CNCJob object
            # #########################################################################################################
            def tool_entry(tool_id, ids):
                """The CNCJob tool of tool_id with the geometry of the drills (and slots) of the tools in ids."""
                tool = gen_tools[tool_id]
                entry = dict(tool, drills=[], slots=list(tool.get('slots') or []))
                geo = []
                drill_nr = 0
                slot_nr = 0
                for t_id in ids:
                    radius = float(gen_tools[t_id]['tooldia']) / 2.0
                    # the same segments as the circles of the Excellon object for this tool diameter
                    steps = object_quad_segs(self.app.options, 'excellon', radius, self.units)
                    # the points, not the drills of the tool: slots may have been converted to drills
                    pts = points.get(t_id) or []
                    if pts:
                        drill_nr += len(pts)
                        entry['drills'] += pts
                        geo += list(shapely.buffer(np.array(pts, dtype=object), radius, quad_segs=steps))
                    slots = gen_tools[t_id].get('slots')
                    if slots and not gen_tools[t_id]['data'].get('tools_drill_drill_slots'):
                        slot_nr += len(slots)
                        lines = np.empty(len(slots), dtype=object)
                        lines[:] = [LineString([(s[0].x, s[0].y), (s[1].x, s[1].y)]) for s in slots]
                        geo += list(shapely.buffer(lines, radius, quad_segs=steps))

                # adjust Offset for current tool
                try:
                    z_off = float(tool['data'].get('tools_drill_offset') or 0.0) * (-1)
                except (TypeError, ValueError):
                    z_off = 0

                entry['nr_drills'] = drill_nr
                entry['nr_slots'] = slot_nr
                entry['offset'] = z_off
                entry['gcode'] = ''
                entry['gcode_parsed'] = []
                entry['solid_geometry'] = geo
                return entry

            cnc_job_obj.tools = {}
            if has_toolchange:
                for t_id in gen_ids:
                    cnc_job_obj.tools[t_id] = tool_entry(t_id, [t_id])
            else:
                # the first tool of the job is the tool that we are going to use, for all the selected tools
                cnc_job_obj.tools[gen_ids[0]] = tool_entry(gen_ids[0], gen_ids)

            # #########################################################################################################
            # Initialization
            # #########################################################################################################
            # Preprocessor
            cnc_job_obj.pp_excellon_name = self.ui.pp_excellon_name_cb.get_value()
            cnc_job_obj.pp_excellon = self.app.preprocessors[cnc_job_obj.pp_excellon_name]

            cnc_job_obj.obj_options['type'] = 'Excellon'
            cnc_job_obj.obj_options['ppname_e'] = obj.pp_excellon_name

            cnc_job_obj.obj_options['xmin'] = xmin
            cnc_job_obj.obj_options['ymin'] = ymin
            cnc_job_obj.obj_options['xmax'] = xmax
            cnc_job_obj.obj_options['ymax'] = ymax

            cnc_job_obj.use_ui = True

            cnc_job_obj.coords_decimals = int(self.app.options.get(
                "cncjob_coords_decimals",
                app_defaults.get("cncjob_coords_decimals", AppDefaults.factory_defaults.get("cncjob_coords_decimals"))))
            cnc_job_obj.fr_decimals = int(self.app.options.get(
                "cncjob_fr_decimals",
                app_defaults.get("cncjob_fr_decimals", AppDefaults.factory_defaults.get("cncjob_fr_decimals"))))
            cnc_job_obj.multitool = True

            # it does not matter for the Excellon codes because we are not going to autolevel GCode out of Excellon
            # but it is here for uniformity between the Geometry and Excellon objects
            cnc_job_obj.seg_x = self.app.options.get(
                "geometry_seg_x", app_defaults.get("geometry_seg_x", AppDefaults.factory_defaults.get("geometry_seg_x")))
            cnc_job_obj.seg_y = self.app.options.get(
                "geometry_seg_y", app_defaults.get("geometry_seg_y", AppDefaults.factory_defaults.get("geometry_seg_y")))

            # first drill point
            # I can read the toolchange x,y point from any tool since it is the same for all, so I read it
            # from the first tool that is available
            cnc_job_obj.xy_toolchange = gen_tools[gen_ids[0]]['data'].get("tools_drill_toolchangexy")

            x_tc, y_tc = [0, 0]
            if cnc_job_obj.xy_toolchange not in (None, ''):
                try:
                    tcxy_temp = re.sub(r'[()\[\]]', '', str(cnc_job_obj.xy_toolchange))
                    if tcxy_temp:
                        x_tc, y_tc = [float(safe_eval(a)) for a in tcxy_temp.split(",")]
                except Exception:
                    x_tc, y_tc = [0, 0]
                    self.app.inform.emit('[ERROR]%s' % _("The Toolchange X,Y format has to be (x, y)."))

            cnc_job_obj.oldx = x_tc
            cnc_job_obj.oldy = y_tc
            first_drill_point = (cnc_job_obj.oldx, cnc_job_obj.oldy)

            def fail_job():
                app_obj.inform.emit('[ERROR_NOTCL] %s' % _("G-Code generation failed."))
                return 'fail'

            # #########################################################################################################
            # ####################### NO TOOLCHANGE ###################################################################
            # #########################################################################################################
            if has_toolchange is False:
                tool_points = []
                for tool in gen_ids:
                    tool_points += points[tool]

                # use the first tool in the selection as the tool that we are going to use
                used_tool = gen_ids[0]
                used_tooldia = gen_tools[used_tool]['tooldia']

                # those are used by the preprocessors to display data on the toolchange line
                cnc_job_obj.tool = used_tool
                cnc_job_obj.used_tools = [used_tool]
                cnc_job_obj.postdata['toolC'] = used_tooldia

                # generate GCode
                tool_gcode, __, start_gcode = cnc_job_obj.excellon_tool_gcode_gen(used_tool, tool_points,
                                                                                  gen_tools,
                                                                                  first_pt=first_drill_point,
                                                                                  is_first=True,
                                                                                  is_last=True,
                                                                                  opt_type=used_exc_optim_type,
                                                                                  toolchange=False,
                                                                                  exclusion_owner=EXCLUSION_OWNER)
                if tool_gcode == 'fail':
                    return fail_job()

                # parse the Gcode
                tool_gcode_parsed = cnc_job_obj.excellon_tool_gcode_parse(used_tooldia, gcode=tool_gcode,
                                                                          start_pt=first_drill_point)

                # store the results in Excellon CNC tools storage
                cnc_job_obj.tools[used_tool]['gcode'] = tool_gcode
                cnc_job_obj.tools[used_tool]['gcode_parsed'] = tool_gcode_parsed

                if start_gcode != '':
                    cnc_job_obj.gc_start = start_gcode

                total_gcode = tool_gcode
                total_gcode_parsed = tool_gcode_parsed

            # ####################### TOOLCHANGE ACTIVE ######################################################
            else:
                cnc_job_obj.used_tools = list(gen_ids)
                for tool_id in gen_ids:
                    tool_points = points[tool_id]
                    used_tooldia = gen_tools[tool_id]['tooldia']

                    # those are used by the preprocessors to display data on the toolchange line
                    cnc_job_obj.tool = tool_id
                    cnc_job_obj.postdata['toolC'] = used_tooldia

                    # calculate if the current tool is the first one or if it is the last one
                    # for the first tool we add some extra GCode (start Gcode, header etc)
                    # for the last tool we add other GCode (the end code, what is happening at the end of the job)
                    is_last_tool = tool_id == gen_ids[-1]
                    is_first_tool = tool_id == gen_ids[0]

                    # Generate Gcode for the current tool
                    tool_gcode, last_pt, start_gcode = cnc_job_obj.excellon_tool_gcode_gen(tool_id, tool_points,
                                                                                           gen_tools,
                                                                                           first_pt=first_drill_point,
                                                                                           is_first=is_first_tool,
                                                                                           is_last=is_last_tool,
                                                                                           opt_type=used_exc_optim_type,
                                                                                           toolchange=True,
                                                                                           exclusion_owner=EXCLUSION_OWNER)
                    if tool_gcode == 'fail':
                        return fail_job()

                    # parse Gcode for the current tool
                    tool_gcode_parsed = cnc_job_obj.excellon_tool_gcode_parse(used_tooldia, gcode=tool_gcode,
                                                                              start_pt=first_drill_point)
                    first_drill_point = last_pt

                    # store the results of GCode generation and parsing
                    cnc_job_obj.tools[tool_id]['gcode'] = tool_gcode
                    cnc_job_obj.tools[tool_id]['gcode_parsed'] = tool_gcode_parsed

                    if start_gcode != '':
                        cnc_job_obj.gc_start = start_gcode

                    total_gcode += tool_gcode
                    total_gcode_parsed += tool_gcode_parsed

            cnc_job_obj.gcode = total_gcode
            cnc_job_obj.source_file = total_gcode
            cnc_job_obj.gcode_parsed = total_gcode_parsed

            # the geometry of the whole job for plotting (the tools only hold their own)
            cnc_job_obj.create_geometry()

            if used_exc_optim_type == 'M':
                app_obj.log.debug("The total travel distance with OR-TOOLS Metaheuristics is: %s" %
                                  str(cnc_job_obj.measured_distance))
            elif used_exc_optim_type == 'B':
                app_obj.log.debug("The total travel distance with OR-TOOLS Basic Algorithm is: %s" %
                                  str(cnc_job_obj.measured_distance))
            elif used_exc_optim_type == 'T':
                app_obj.log.debug(
                    "The total travel distance with Travelling Salesman Algorithm is: %s" %
                    str(cnc_job_obj.measured_distance))
            else:
                app_obj.log.debug("The total travel distance with with no optimization is: %s" %
                                  str(cnc_job_obj.measured_distance))

            # #########################################################################################################
            # ############################# Calculate DISTANCE and ESTIMATED TIME #####################################
            # #########################################################################################################
            if cnc_job_obj.xy_end is None:
                cnc_job_obj.xy_end = [cnc_job_obj.oldx, cnc_job_obj.oldy]

            cnc_job_obj.measured_distance += abs(distance_euclidian(
                cnc_job_obj.oldx, cnc_job_obj.oldy, cnc_job_obj.xy_end[0], cnc_job_obj.xy_end[1])
            )
            app_obj.log.debug("The total travel distance including travel to end position is: %s" %
                              str(cnc_job_obj.measured_distance) + '\n')
            cnc_job_obj.travel_distance = cnc_job_obj.measured_distance

            # I use the value of self.feedrate_rapid for the feadrate in case of the measure_lift_distance and for
            # traveled_time because it is not always possible to determine the feedrate that the CNC machine uses
            # for G0 move (the fastest speed available to the CNC router). Although self.feedrate_rapids is used only
            # with Marlin preprocessor and derivatives.
            # A feedrate of zero adds no time (no division by zero)
            z_feed = cnc_job_obj.z_feedrate
            rapid_feed = cnc_job_obj.feedrate_rapid
            routing_time = 0.0
            if z_feed:
                routing_time += (cnc_job_obj.measured_down_distance + cnc_job_obj.measured_up_to_zero_distance) / z_feed
            if rapid_feed:
                routing_time += (cnc_job_obj.measured_lift_distance + cnc_job_obj.measured_distance) / rapid_feed
            cnc_job_obj.routing_time = routing_time

        # To be run in separate thread
        def job_thread(a_obj):
            with self.app.proc_container.new(_("Generating CNC Code")):
                self.new_object("cncjob", job_name, job_init, source=obj)

        # Create promise for the new name.
        self.app.collection.promise(job_name)

        # Send to worker
        # self.app.worker.add_task(job_thread, [self.app])
        self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

    def reset_fields(self):
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))


def distance(pt1, pt2):
    return DrillingGen.distance(pt1, pt2)


def distance_euclidian(x1, y1, x2, y2):
    return DrillingGen.distance_euclidian(x1, y1, x2, y2)
