# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtGui, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appPlugins.ToolCutOut.CutOutUI import CutoutUI
from appPlugins.ToolCutOut import CutOutGen
import appPlugins.ToolCutOut as cutout_plugin
from appPlugins.ToolCutOut.CutOutGen import (
    _cutout_paths, _manual_gap_geometry, _manual_material_side, _manual_subtract_selected,
    _valid_mouse_bite_step, _validate_thin_depth,
)
from appDatabase import load_tools_database, tools_database_error_message
from defaults import AppDefaults

import math
from contextlib import nullcontext
from copy import deepcopy

from shapely import Polygon, MultiPolygon, GeometryCollection, Point, box
from shapely.ops import unary_union, linemerge


from appHandlers.appHistory import history_of, object_state, SnapshotStep, states_equal
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class CutOut(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.canvas = app.plotcanvas
        self.decimals = self.app.decimals
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = CutoutUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

        self.cutting_gapsize = 0.0
        self.cutting_dia = 0.0

        # true if we want to repeat the gap without clicking again on the button
        self.repeat_gap = False

        self.flat_geometry = []

        # this is the Geometry object generated in this class to be used for adding manual gaps
        self.man_cutout_obj = None

        # if mouse is dragging set the object True
        self.mouse_is_dragging = False

        # if mouse events are bound to local methods
        self.mouse_events_connected = False


        # hold the mouse position here
        self.x_pos = None
        self.y_pos = None

        # store the default data for the resulting Geometry Object
        self.default_data = {}

        # store the current cursor type to be restored after manual geo
        self.old_cursor_type = self.app.options.get(
            "global_cursor_type", app_defaults.get(
                "global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))

        # store the current selection shape status to be restored after manual geo
        self.old_selection_state = self.app.options.get(
            "global_selection_shape", app_defaults.get(
                "global_selection_shape", AppDefaults.factory_defaults.get("global_selection_shape")))

        # store original geometry for manual cutout
        self.manual_solid_geo = None
        self.manual_session = None
        self.manual_bridges = []
        self.selected_manual_bridge = None
        self.manual_grip_press = None

        # here will store the original geometry for manual cutout with mouse bytes
        self.mb_manual_solid_geo = None

        # here will store the geo rests when doing manual cutouts with mouse bites
        self.mb_manual_cuts = []

        # here store the tool data for the Cutout Tool
        self.cut_tool_dict = {}

        # gap type and convex shape the user had set before the Beginner level forced the basic ones
        self._advanced_values = None

    def inner_active(self):
        """The inner contours are cut: the source is marked as a board outline and the option is on."""
        return bool(self.ui.is_outline_cb.get_value()) and bool(self.ui.inner_cb.get_value()) and \
            not self.ui.cutout_shape_cb.get_value()

    def update_inner_state(self, *args):
        self.ui.update_inner_state()

    def reset_outline_mark(self):
        """The source is not marked as a board outline (the mark is never kept)."""
        self.ui.is_outline_cb.set_value(False)
        self.update_inner_state()

    def apply_outline_mark(self, name):
        """Mark the source as a board outline only if it is a Gerber with the X2 file function Profile."""
        self.reset_outline_mark()
        try:
            obj = self.app.collection.get_by_name(str(name))
        except Exception:
            return
        if obj is None or getattr(obj, 'kind', None) != 'gerber':
            return
        function = obj.obj_options.get('file_function') or []
        if 'Profile' in function:
            self.ui.is_outline_cb.set_value(True)
            self.update_inner_state()

    def on_source_object_changed(self, *args):
        self.apply_outline_mark(self.ui.obj_combo.currentText())

    def on_type_obj_changed(self, val):
        obj_type = {'grb': 0, 'geo': 2}[val]
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.obj_combo.setCurrentIndex(0)
        self.ui.obj_combo.obj_type = {"grb": "Gerber", "geo": "Geometry"}[val]
        self.reset_outline_mark()

        if val == 'grb':
            self.ui.convex_box_label.setDisabled(False)
            self.ui.convex_box_cb.setDisabled(False)
        else:
            self.ui.convex_box_label.setDisabled(True)
            self.ui.convex_box_cb.setDisabled(True)

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                found_obj = current.indexes()[0].internalPointer().obj
                name = found_obj.obj_options['name']
                kind = found_obj.kind

                if kind in ['gerber', 'geometry']:
                    obj_type = {'gerber': 'grb', 'geometry': 'geo'}[kind]
                    self.ui.type_obj_radio.set_value(obj_type)

                self.ui.obj_combo.set_value(name)
            except IndexError:
                pass

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolCutOut()")

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

        self.app.ui.notebook.setTabText(2, _("Cutout"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+X', **kwargs)

    def connect_signals_at_init(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.generate_cutout_btn.clicked.connect(self.on_cutout_generation)

        # adding tools
        self.ui.add_newtool_button.clicked.connect(lambda: self.on_tool_add())
        self.ui.addtool_from_db_btn.clicked.connect(self.on_tool_add_from_db_clicked)

        self.ui.type_obj_radio.activated_custom.connect(self.on_type_obj_changed)
        self.ui.cutout_shape_cb.stateChanged.connect(self.on_cutout_shape_changed)
        self.ui.cutout_shape_cb.stateChanged.connect(self.update_inner_state)
        self.ui.obj_combo.currentIndexChanged.connect(self.on_source_object_changed)
        self.ui.obj_kind_combo.activated_custom.connect(lambda val: self.reset_outline_mark())
        self.ui.is_outline_cb.stateChanged.connect(self.update_inner_state)
        self.ui.inner_cb.stateChanged.connect(self.update_inner_state)

        self.ui.cutout_type_radio.activated_custom.connect(self.on_cutout_type)

        self.ui.man_geo_creation_btn.clicked.connect(self.on_manual_geo)
        self.ui.man_gaps_creation_btn.clicked.connect(self.on_manual_gap_click)
        self.ui.drillcut_btn.clicked.connect(self.on_drill_cut_click)

        self.ui.reset_button.clicked.connect(self.set_tool_ui)

    def set_tool_ui(self):

        self.clear_ui(self.layout)
        self.ui = CutoutUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.reset_fields()

        # use the current selected object and make it visible in the object combobox
        sel_list = self.app.collection.get_selected()
        if len(sel_list) == 1:
            active = self.app.collection.get_active()
            kind = active.kind
            if kind == 'gerber':
                self.ui.type_obj_radio.set_value('grb')
            else:
                self.ui.type_obj_radio.set_value('geo')

            # run those once so the obj_type attribute is updated for the FCComboboxes
            # so the last loaded object is displayed
            if kind == 'gerber':
                self.on_type_obj_changed(val='grb')
            else:
                self.on_type_obj_changed(val='geo')

            self.ui.obj_combo.set_value(active.obj_options['name'])
        else:
            kind = 'gerber'
            self.ui.type_obj_radio.set_value('grb')

            # run those once so the obj_type attribute is updated for the FCComboboxes
            # so the last loaded object is displayed
            if kind == 'gerber':
                self.on_type_obj_changed(val='grb')
            else:
                self.on_type_obj_changed(val='geo')

        # init the working variables
        self.default_data.clear()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        kind = 'geometry'
        for option in self.app.options:
            if option.find(kind + "_") == 0:
                oname = option[len(kind) + 1:]
                self.default_data[oname] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

            if option.find('tools_') == 0:
                self.default_data[option] = self.app.options.get(
                    option, app_defaults.get(option, AppDefaults.factory_defaults.get(option)))

        self.ui.gaptype_combo.set_value(self.app.options.get(
            "tools_cutout_gap_type",
            app_defaults.get("tools_cutout_gap_type", AppDefaults.factory_defaults.get("tools_cutout_gap_type"))))
        self.ui.on_gap_type_radio(self.ui.gaptype_combo.get_value())

        # add a default tool
        self.ui.dia.set_value(float(self.app.options.get(
            "tools_cutout_tooldia",
            app_defaults.get("tools_cutout_tooldia", AppDefaults.factory_defaults.get("tools_cutout_tooldia")))))
        tool_dia = float(self.app.options.get(
            "tools_cutout_tooldia",
            app_defaults.get("tools_cutout_tooldia", AppDefaults.factory_defaults.get("tools_cutout_tooldia"))))
        self.on_tool_add(custom_dia=tool_dia)

        # set as default the automatic adding of gaps
        self.ui.cutout_type_radio.set_value('a')
        self.on_cutout_type(val='a')

        self.ui.cutout_shape_cb.set_value(False)
        self.on_cutout_shape_changed(self.ui.cutout_shape_cb.get_value())

        # the inner contours: the mark is off, the other two come from the preferences
        self.ui.inner_cb.set_value(bool(self.app.options.get(
            "tools_cutout_inner", app_defaults.get("tools_cutout_inner", False))))
        self.ui.inner_gaps_cb.set_value(bool(self.app.options.get(
            "tools_cutout_inner_gaps", app_defaults.get("tools_cutout_inner_gaps", False))))
        self.reset_outline_mark()
        if len(sel_list) == 1:
            self.apply_outline_mark(active.obj_options['name'])

        # set the Cut By Drilling parameters
        self.ui.drill_dia_entry.set_value(float(self.app.options.get(
            "tools_cutout_drill_dia",
            app_defaults.get("tools_cutout_drill_dia", AppDefaults.factory_defaults.get("tools_cutout_drill_dia")))))
        self.ui.drill_pitch_entry.set_value(float(self.app.options.get(
            "tools_cutout_drill_pitch",
            app_defaults.get("tools_cutout_drill_pitch", AppDefaults.factory_defaults.get("tools_cutout_drill_pitch")))))
        self.ui.drill_margin_entry.set_value(float(self.app.options.get(
            "tools_cutout_drill_margin",
            app_defaults.get("tools_cutout_drill_margin", AppDefaults.factory_defaults.get("tools_cutout_drill_margin")))))

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
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        if not checked:
            # Beginner level hides the gap type and the convex shape: use the basic values, keep the user's ones
            if self._advanced_values is None:
                self._advanced_values = (self.ui.gaptype_combo.get_value(), self.ui.convex_box_cb.get_value())
            gap_type, convex = 0, False   # "Basic Type of Gap"
        else:
            if self._advanced_values is not None:
                gap_type, convex = self._advanced_values
            elif self.cut_tool_dict:
                tool_data = self.cut_tool_dict['data']
                gap_type, convex = tool_data['tools_cutout_gap_type'], tool_data['tools_cutout_convexshape']
            else:
                gap_type = self.app.options.get(
                    "tools_cutout_gap_type",
                    app_defaults.get("tools_cutout_gap_type", AppDefaults.factory_defaults.get("tools_cutout_gap_type")))
                convex = self.app.options.get(
                    "tools_cutout_convexshape",
                    app_defaults.get("tools_cutout_convexshape", AppDefaults.factory_defaults.get("tools_cutout_convexshape")))
            self._advanced_values = None

        self.ui.on_level_changed(checked)
        self.ui.gaptype_combo.set_value(gap_type)
        self.ui.convex_box_cb.set_value(convex)
        self.ui.on_gap_type_radio(gap_type)

    def update_ui(self, tool_dict):
        self._advanced_values = None
        self.ui.obj_kind_combo.set_value(self.default_data["tools_cutout_kind"])
        self.ui.big_cursor_cb.set_value(self.default_data['tools_cutout_big_cursor'])

        # Entries that may be updated from database
        self.ui.margin.set_value(float(tool_dict["tools_cutout_margin"]))
        self.ui.gapsize.set_value(float(tool_dict["tools_cutout_gapsize"]))
        self.ui.gaptype_combo.set_value(tool_dict["tools_cutout_gap_type"])
        # the widgets of the gap type; on_cutout_type() takes the Automatic / Manual value, not this index
        self.ui.on_gap_type_radio(self.ui.gaptype_combo.get_value())

        self.ui.thin_depth_entry.set_value(float(tool_dict["tools_cutout_gap_depth"]))
        self.ui.mb_dia_entry.set_value(float(tool_dict["tools_cutout_mb_dia"]))
        self.ui.mb_spacing_entry.set_value(float(tool_dict["tools_cutout_mb_spacing"]))
        self.ui.convex_box_cb.set_value(tool_dict['tools_cutout_convexshape'])
        self.ui.gaps.set_value(tool_dict["tools_cutout_gaps_ff"])

        self.ui.cutz_entry.set_value(float(tool_dict["tools_cutout_z"]))
        self.ui.mpass_cb.set_value(bool(tool_dict["tools_cutout_mdepth"]))
        self.ui.maxdepth_entry.set_value(float(tool_dict["tools_cutout_depthperpass"]))

    def on_cutout_type(self, val):
        self.ui.on_cutout_type(val)

    def on_cutout_shape_changed(self, state):
        self.ui.on_cutout_shape_changed(state)

    def on_tool_add(self, custom_dia=None):
        self.blockSignals(True)

        filename = self.app.tools_database_path()

        new_tools_dict = deepcopy(self.default_data)
        updated_tooldia = None

        # determine the new tool diameter
        if custom_dia is None:
            tool_dia = self.ui.dia.get_value()
        else:
            tool_dia = custom_dia

        if tool_dia is None or tool_dia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Please enter a tool diameter with non-zero value, "
                                                          "in Float format."))
            self.blockSignals(False)
            return

        truncated_tooldia = self.app.dec_format(tool_dia, self.decimals)

        try:
            tools_db_dict = load_tools_database(filename, self.app.options, target=6,
                                               diameter=truncated_tooldia,
                                               units=getattr(self.app, 'app_units', None))
        except (OSError, TypeError, ValueError) as error:
            self.app.log.error("Could not load tools DB file.")
            self.app.log.error(str(error))
            self.app.inform.emit('[ERROR] %s' % tools_database_error_message(error))
            self.blockSignals(False)
            self.on_tool_default_add(dia=tool_dia)
            return

        tool_found = len(tools_db_dict)

        # look in database tools
        for db_tool, db_tool_val in tools_db_dict.items():
            db_tooldia = db_tool_val['tooldia']
            updated_tooldia = db_tooldia if db_tooldia != truncated_tooldia else None
            for d in db_tool_val['data']:
                if not d.startswith('tools_') or d.startswith(('tools_cutout_', 'tools_mill_')):
                    new_tools_dict[d] = deepcopy(db_tool_val['data'][d])

        # test we found a suitable tool in Tools Database or if multiple ones
        if tool_found == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Tool not in Tools Database. Adding a default tool."))
            self.on_tool_default_add(dia=tool_dia)
            self.blockSignals(False)
            return

        if tool_found > 1:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Cancelled.\n"
                                         "Multiple tools for one tool diameter found in Tools Database."))
            self.blockSignals(False)
            return

        new_tools_dict["tools_cutout_z"] = deepcopy(new_tools_dict["tools_mill_cutz"])
        new_tools_dict["tools_cutout_mdepth"] = deepcopy(new_tools_dict["tools_mill_multidepth"])
        new_tools_dict["tools_cutout_depthperpass"] = deepcopy(new_tools_dict["tools_mill_depthperpass"])

        new_tdia = deepcopy(updated_tooldia) if updated_tooldia is not None else deepcopy(truncated_tooldia)
        self.cut_tool_dict.update({
            'tooldia':          new_tdia,
            'data':             deepcopy(new_tools_dict),
            'solid_geometry':   []
        })

        self.update_ui(new_tools_dict)

        self.blockSignals(False)
        self.app.inform.emit('[success] %s' % _("Updated tool from Tools Database."))

    def on_tool_default_add(self, dia=None, muted=None):

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        dia = dia if dia else str(self.app.options.get(
            "tools_cutout_tooldia",
            app_defaults.get("tools_cutout_tooldia", AppDefaults.factory_defaults.get("tools_cutout_tooldia"))))

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

        self.cut_tool_dict.update({
            'tooldia':          dia,
            'data':             deepcopy(self.default_data),
            'solid_geometry':   []
        })

        self.update_ui(self.default_data)

        if muted is None:
            self.app.inform.emit('[success] %s' % _("Default tool added."))

    def on_cutout_tool_add_from_db_executed(self, tool):
        """
        Here add the tool from DB  in the selected geometry object
        :return:
        """

        if tool['data']['tool_target'] not in [0, 6]:   # [General, Cutout Tool]
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
            return 'fail'
        tool_from_db = deepcopy(tool)
        data = deepcopy(self.default_data)
        data.update(deepcopy(tool['data']))
        tool_from_db['data'] = data

        tool_from_db['data']["tools_cutout_tooldia"] = deepcopy(tool["tooldia"])
        tool_from_db['data']["tools_cutout_z"] = deepcopy(tool_from_db['data']["tools_mill_cutz"])
        tool_from_db['data']["tools_cutout_mdepth"] = deepcopy(tool_from_db['data']["tools_mill_multidepth"])
        tool_from_db['data']["tools_cutout_depthperpass"] = deepcopy(tool_from_db['data']["tools_mill_depthperpass"])

        self.cut_tool_dict.update(tool_from_db)
        self.cut_tool_dict['solid_geometry'] = []

        self.update_ui(tool_from_db['data'])
        self.ui.dia.set_value(float(tool_from_db['data']["tools_cutout_tooldia"]))

        self.app.inform.emit('[success] %s' % _("Tool updated from Tools Database."))
        return True

    def on_tool_from_db_inserted(self, tool):
        """
        Called from the Tools DB object through a App method when adding a tool from Tools Database
        :param tool: a dict with the tool data
        :return: None
        """

        tooldia = float(tool['tooldia'])

        truncated_tooldia = self.app.dec_format(tooldia, self.decimals)
        self.cutout_tools.update({
            1: {
                'tooldia':          truncated_tooldia,
                'data':             deepcopy(tool['data']),
                'solid_geometry':   []
            }
        })
        self.cutout_tools[1]['data']['name'] = '_cutout'

        return 1

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
        ret_val = self.app.on_tools_database(source='cutout')
        if ret_val == 'fail':
            return
        self.app.tools_db_tab.ok_to_add = True
        self.app.tools_db_tab.ui.buttons_frame.hide()
        self.app.tools_db_tab.ui.add_tool_from_db.show()
        self.app.tools_db_tab.ui.cancel_tool_from_db.show()

    def on_cutout_generation(self):
        cutout_rect_shape = self.ui.cutout_shape_cb.get_value()
        if cutout_rect_shape:
            self.on_rectangular_cutout()
        else:
            self.on_freeform_cutout()

    def on_freeform_cutout(self):
        self.app.log.debug("CutOut.on_freeform_cutout() is running....")
        name = self.ui.obj_combo.currentText()

        # Get source object.
        try:
            cutout_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_freeform_cutout() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return "Could not retrieve object: %s" % name

        if cutout_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("There is no object selected for Cutout.\nSelect one and try again."))
            return

        cut_dia = self.ui.dia.get_value()
        if not math.isfinite(cut_dia) or cut_dia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Tool Diameter is zero value. Change it to a positive real number."))
            return

        try:
            kind = self.ui.obj_kind_combo.get_value()
        except ValueError:
            return

        margin = self.ui.margin.get_value()

        try:
            gaps = self.ui.gaps.get_value()
        except TypeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Number of gaps value is missing. Add it and retry."))
            return

        if gaps not in ['None', 'LR', 'TB', '2LR', '2TB', '4', '8']:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Gaps value can be only one of: 'None', 'lr', 'tb', '2lr', '2tb', 4 or 8.\n"
                                   "Fill in a correct value and retry."))
            return

        gapsize = self.ui.gapsize.get_value()
        convex_box = self.ui.convex_box_cb.get_value()
        gap_type = self.ui.gaptype_combo.get_value()
        thin_entry = self.ui.thin_depth_entry.get_value()
        mb_dia = self.ui.mb_dia_entry.get_value()
        mb_spacing = self.ui.mb_spacing_entry.get_value()
        cutz = self.ui.cutz_entry.get_value()
        multidepth = self.ui.mpass_cb.get_value()
        depthperpass = self.ui.maxdepth_entry.get_value()
        cut_tool_dict = deepcopy(self.cut_tool_dict)
        has_mouse_bites = gap_type == 2     # "mouse bytes"
        try:
            _validate_thin_depth(gap_type, gaps, cutz, thin_entry)
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return
        if has_mouse_bites and not _valid_mouse_bite_step(mb_dia, mb_spacing):
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Mouse bite diameter and spacing must produce a positive finite step."))
            return

        with self.app.proc_container.new("Generating Cutout ..."):
            formatted_name = cutout_obj.obj_options["name"].rpartition('.')[0]
            if formatted_name == '':
                formatted_name = cutout_obj.obj_options["name"]
            outname = "%s_cutout" % formatted_name

            self.app.collection.promise(outname)

            inner_active = self.inner_active()
            inner_gaps = bool(self.ui.inner_gaps_cb.get_value()) and inner_active

            outname_exc = "%s_mouse_bites" % formatted_name
            reserved = [outname, outname_exc] if has_mouse_bites else [outname]
            if has_mouse_bites is True:
                self.app.collection.promise(outname_exc)

            created = set()

            def job_thread(app_obj):
                try:
                    result = cutout_plugin.freeform_cutout_paths(
                        cutout_obj, kind, cut_dia, margin, gaps, gapsize,
                        convex_box, gap_type, thin_entry, mb_dia, mb_spacing, self.any_cutout_handler,
                        inner_active=inner_active, inner_gaps=inner_gaps)
                    for message in result.errors:
                        self.app.inform.emit('[ERROR_NOTCL] %s' % message)
                    for message in result.warnings:
                        self.app.inform.emit('[WARNING_NOTCL] %s' % message)
                    if result.failed:
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                        return 'fail'

                    solid_geo = result.solid_geo
                    gaps_solid_geo = result.gaps_solid_geo
                    holes = result.holes
                    def geo_init(geo_obj, app_object):
                        geo_obj.multigeo = True
                        geo_obj.solid_geometry = deepcopy(solid_geo)

                        geo_obj.tools[1] = deepcopy(cut_tool_dict)
                        geo_obj.tools[1]['tooldia'] = str(abs(cut_dia))
                        geo_obj.tools[1]['solid_geometry'] = geo_obj.solid_geometry

                        geo_obj.tools[1]['data']['name'] = outname
                        geo_obj.tools[1]['data']['tools_mill_tooldia'] = str(abs(cut_dia))
                        geo_obj.tools[1]['data']['tools_mill_cutz'] = cutz

                        geo_obj.tools[1]['data']['tools_mill_multidepth'] = multidepth
                        geo_obj.tools[1]['data']['tools_mill_depthperpass'] = depthperpass

                        # if there are "thin gaps"
                        if gaps_solid_geo:
                            geo_obj.tools[99] = deepcopy(cut_tool_dict)
                            geo_obj.tools[99]['tooldia'] = str(abs(cut_dia))
                            geo_obj.tools[99]['solid_geometry'] = gaps_solid_geo

                            geo_obj.tools[99]['data']['name'] = outname
                            geo_obj.tools[99]['data']['tools_mill_tooldia'] = str(abs(cut_dia))
                            geo_obj.tools[99]['data']['tools_mill_cutz'] = thin_entry
                            geo_obj.tools[99]['data']['tools_mill_multidepth'] = multidepth
                            geo_obj.tools[99]['data']['tools_mill_depthperpass'] = depthperpass
                            # plot this tool in a different color
                            geo_obj.tools[99]['data']['override_color'] = "#29a3a3fa"

                        xmin, ymin, xmax, ymax = CutOut.recursive_bounds(geo_obj.solid_geometry)
                        geo_obj.obj_options['xmin'] = xmin
                        geo_obj.obj_options['ymin'] = ymin
                        geo_obj.obj_options['xmax'] = xmax
                        geo_obj.obj_options['ymax'] = ymax

                        geo_obj.obj_options['tools_mill_tooldia'] = str(abs(cut_dia))
                        geo_obj.obj_options['tools_mill_cutz'] = cutz
                        geo_obj.obj_options['tools_mill_multidepth'] = multidepth
                        geo_obj.obj_options['tools_mill_depthperpass'] = depthperpass

                    def excellon_init(exc_obj, app_o):
                        if not holes:
                            return 'fail'

                        exc_obj.multigeo = True
                        exc_obj.tools = {}
                        exc_obj.add_tool(mb_dia, drills=holes)
                        exc_obj.create_geometry()
                        exc_obj.regenerate_source()
                        # calculate the bounds
                        xmin, ymin, xmax, ymax = CutOut.recursive_bounds(exc_obj.solid_geometry)
                        exc_obj.obj_options['xmin'] = xmin
                        exc_obj.obj_options['ymin'] = ymin
                        exc_obj.obj_options['xmax'] = xmax
                        exc_obj.obj_options['ymax'] = ymax

                    try:
                        if gap_type == 2:  # "mouse bytes"
                            ret = self.new_object('excellon', outname_exc, excellon_init,
                                                  source=cutout_obj, autoselected=False)
                            if ret == 'fail':
                                app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Mouse bites failed."))
                            else:
                                created.add(outname_exc)
                            app_obj.should_we_save = True

                        ret = self.new_object('geometry', outname, geo_init, source=cutout_obj,
                                              autoselected=False)
                        if ret == 'fail':
                            self.app.log.debug("Cutout.on_freeform_cutout() -> Failure in creating an Geometry object..")
                            app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                            return
                        created.add(outname)

                        # cutout_obj.plot(plot_tool=1)
                        app_obj.inform.emit('[success] %s' % _("Any-form Cutout operation finished."))
                        # self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)
                        app_obj.should_we_save = True
                    except Exception as ee:
                        app_obj.log.error(str(ee))
                finally:
                    for name in reserved:
                        if name not in created:
                            self.app.collection.remove_promise(name)

            try:
                self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})
            except Exception:
                for name in reserved:
                    if name not in created:
                        self.app.collection.remove_promise(name)
                raise

    def any_cutout_handler(self, geom, cut_diameter, gaps, gapsize, margin):
        return CutOutGen.any_cutout_handler(geom, cut_diameter, gaps, gapsize, margin)

    def on_rectangular_cutout(self):
        self.app.log.debug("CutOut.on_rectangular_cutout() is running....")
        name = self.ui.obj_combo.currentText()

        # Get source object.
        try:
            cutout_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_rectangular_cutout() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return "Could not retrieve object: %s" % name

        if cutout_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Object not found"), str(name)))
            return

        cut_dia_val = float(self.ui.dia.get_value())
        if not math.isfinite(cut_dia_val) or cut_dia_val == 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Tool Diameter is zero value. Change it to a positive real number."))
            return

        try:
            kind = self.ui.obj_kind_combo.get_value()
        except ValueError:
            return

        margin_val = self.ui.margin.get_value()

        try:
            gaps_val = self.ui.gaps.get_value()
        except TypeError:
            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Number of gaps value is missing. Add it and retry."))
            return

        if gaps_val not in ['None', 'LR', 'TB', '2LR', '2TB', '4', '8']:
            msg = '[WARNING_NOTCL] %s' % _("Gaps value can be only one of: 'None', 'lr', 'tb', '2lr', '2tb', 4 or 8.\n"
                                           "Fill in a correct value and retry.")
            self.app.inform.emit(msg)
            return

        gapsize_val = self.ui.gapsize.get_value()
        mb_dia_val = self.ui.mb_dia_entry.get_value()
        mb_spacing_val = self.ui.mb_spacing_entry.get_value()
        gap_type_val = self.ui.gaptype_combo.get_value()
        thin_entry_val = self.ui.thin_depth_entry.get_value()
        has_mouse_bites_val = True if self.ui.gaptype_combo.get_value() == 2 else False  # "mouse bytes"
        if has_mouse_bites_val and not _valid_mouse_bite_step(mb_dia_val, mb_spacing_val):
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Mouse bite diameter and spacing must produce a positive finite step."))
            return
        cut_tool_dict = deepcopy(self.cut_tool_dict)
        cutz = self.ui.cutz_entry.get_value()
        multidepth = self.ui.mpass_cb.get_value()
        depthperpass = self.ui.maxdepth_entry.get_value()
        try:
            _validate_thin_depth(gap_type_val, gaps_val, cutz, thin_entry_val)
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        formatted_name = cutout_obj.obj_options["name"].rpartition('.')[0]
        outname = "%s_cutout" % formatted_name if formatted_name != '' else "%s_cutout" % cutout_obj.obj_options["name"]
        self.app.collection.promise(outname)

        outname_exc = cutout_obj.obj_options["name"] + "_mouse_bites"
        reserved = [outname, outname_exc] if has_mouse_bites_val else [outname]
        if has_mouse_bites_val is True:
            self.app.collection.promise(outname_exc)

        created = set()

        with self.app.proc_container.new("Generating Cutout ..."):
            def job_thread(self_c, app_obj, gaps, gapsize, gap_type, cut_dia, margin, mb_dia, mb_spacing, thin_entry,
                           has_mouse_bites):
                try:
                    if cutout_obj.multigeo is False:
                        object_geo = cutout_obj.solid_geometry
                    else:
                        # first tool in the tools dict
                        t_first = list(cutout_obj.tools.keys())[0]
                        object_geo = cutout_obj.tools[t_first]['solid_geometry']

                    result = CutOutGen.rectangular_cutout_paths(
                        object_geo, cutout_obj.kind, kind, gaps, gapsize, gap_type, cut_dia, margin,
                        mb_dia, mb_spacing, thin_entry, self_c.rect_cutout_handler)
                    for message in result.errors:
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % message)
                    if result.failed:
                        return "fail"
                    solid_geo = result.solid_geo
                    gaps_solid_geo = result.gaps_solid_geo
                    holes = result.holes

                    def geo_init(geo_obj, application_obj):
                        geo_obj.multigeo = True
                        geo_obj.solid_geometry = deepcopy(solid_geo)

                        geo_obj.tools[1] = deepcopy(cut_tool_dict)
                        geo_obj.tools[1]['tooldia'] = str(abs(cut_dia))
                        geo_obj.tools[1]['solid_geometry'] = geo_obj.solid_geometry

                        geo_obj.tools[1]['data']['name'] = outname
                        geo_obj.tools[1]['data']['tools_mill_tooldia'] = str(abs(cut_dia))
                        geo_obj.tools[1]['data']['tools_mill_cutz'] = cutz
                        geo_obj.tools[1]['data']['tools_mill_multidepth'] = multidepth
                        geo_obj.tools[1]['data']['tools_mill_depthperpass'] = depthperpass

                        if gaps_solid_geo:
                            geo_obj.tools[99] = deepcopy(cut_tool_dict)
                            geo_obj.tools[99]['tooldia'] = str(abs(cut_dia))
                            geo_obj.tools[99]['solid_geometry'] = gaps_solid_geo

                            geo_obj.tools[99]['data']['name'] = outname
                            geo_obj.tools[99]['data']['tools_mill_tooldia'] = str(abs(cut_dia))
                            geo_obj.tools[99]['data']['tools_mill_cutz'] = thin_entry
                            geo_obj.tools[99]['data']['tools_mill_multidepth'] = multidepth
                            geo_obj.tools[99]['data']['tools_mill_depthperpass'] = depthperpass
                            geo_obj.tools[99]['data']['override_color'] = "#29a3a3fa"

                        xmin, ymin, xmax, ymax = CutOut.recursive_bounds(geo_obj.solid_geometry)
                        geo_obj.obj_options['xmin'] = xmin
                        geo_obj.obj_options['ymin'] = ymin
                        geo_obj.obj_options['xmax'] = xmax
                        geo_obj.obj_options['ymax'] = ymax

                        geo_obj.obj_options['tools_mill_tooldia'] = str(abs(cut_dia))
                        geo_obj.obj_options['tools_mill_cutz'] = cutz
                        geo_obj.obj_options['tools_mill_multidepth'] = multidepth
                        geo_obj.obj_options['tools_mill_depthperpass'] = depthperpass

                    def excellon_init(exc_obj, app_o):
                        if not holes:
                            return 'fail'

                        exc_obj.tools = {}
                        exc_obj.add_tool(mb_dia, drills=holes)
                        exc_obj.create_geometry()
                        exc_obj.regenerate_source()
                        # calculate the bounds
                        e_xmin, e_ymin, e_xmax, e_ymax = CutOut.recursive_bounds(exc_obj.solid_geometry)
                        exc_obj.obj_options['xmin'] = e_xmin
                        exc_obj.obj_options['ymin'] = e_ymin
                        exc_obj.obj_options['xmax'] = e_xmax
                        exc_obj.obj_options['ymax'] = e_ymax

                    try:
                        if gap_type == 2:  # "mouse bytes"
                            ret = self_c.new_object('excellon', outname_exc, excellon_init,
                                                    source=cutout_obj, autoselected=False)
                            if ret == 'fail':
                                app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Mouse bites failed."))
                            else:
                                created.add(outname_exc)

                        ret = self_c.new_object('geometry', outname, geo_init, source=cutout_obj,
                                                autoselected=False)
                        if ret == 'fail':
                            self.app.log.debug("Cutout.on_rectangular_cutout() -> Could not create a Geometry object.")
                            app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                            return
                        created.add(outname)

                        # cutout_obj.plot(plot_tool=1)
                        app_obj.inform.emit('[success] %s' % _("Rectangular CutOut operation finished."))
                        # self_c.app.ui.notebook.setCurrentWidget(self_c.app.ui.project_tab)
                        app_obj.should_we_save = True
                    except Exception as ee:
                        app_obj.log.error(str(ee))
                finally:
                    for name in reserved:
                        if name not in created:
                            self.app.collection.remove_promise(name)

            try:
                self.app.worker_task.emit(
                    {
                        'fcn': job_thread,
                        'params': [
                            self, self.app, gaps_val, gapsize_val, gap_type_val, cut_dia_val, margin_val,
                            mb_dia_val, mb_spacing_val, thin_entry_val, has_mouse_bites_val
                        ]
                    })
            except Exception:
                for name in reserved:
                    if name not in created:
                        self.app.collection.remove_promise(name)
                raise

    def rect_cutout_handler(self, geom, cut_dia, gaps, gapsize, margin, xmin, ymin, xmax, ymax,
                            include_gaps=False):
        return CutOutGen.rect_cutout_handler(
            geom, cut_dia, gaps, gapsize, margin, xmin, ymin, xmax, ymax, include_gaps=include_gaps)

    def on_drill_cut_click(self):

        margin = self.ui.drill_margin_entry.get_value()
        pitch = self.ui.drill_pitch_entry.get_value()
        drill_dia = self.ui.drill_dia_entry.get_value()

        if not all(math.isfinite(value) and value > 0 for value in (pitch, drill_dia)):
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Failed."), _("Drill pitch and diameter must be positive finite values.")))
            return

        name = self.ui.drillcut_object_combo.currentText()

        # Get source object.
        try:
            obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_freeform_cutout() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return "Could not retrieve object: %s" % name

        if obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("There is no object selected for Cutout.\nSelect one and try again."))
            return

        drill_list = CutOutGen.drill_cut_geometry(obj.solid_geometry, margin, pitch)

        if not drill_list:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Failed."), _("Could not add drills.")))
            return

        formatted_name = obj.obj_options['name'].rpartition('.')[0]
        if formatted_name == '':
            formatted_name = obj.obj_options['name']
        outname = '%s_drillcut' % formatted_name

        def obj_init(obj_inst, app_inst):
            obj_inst.tools = {}
            obj_inst.add_tool(drill_dia, drills=drill_list)
            obj_inst.create_geometry()
            obj_inst.regenerate_source(name=outname)

        with self.app.proc_container.new('%s...' % _("Working")):
            try:
                ret = self.new_object("excellon", outname, obj_init, source=obj, autoselected=False)
            except Exception as e:
                self.app.log.error("Error on Drill Cutting Excellon object creation: %s" % str(e))
                return

            if ret != 'fail':
                self.app.inform.emit('[success] %s' % _("Done."))

    def on_manual_gap_click(self):
        if self.mouse_events_connected:
            return
        name = self.ui.man_object_combo.currentText()

        # Get source object.
        try:
            self.man_cutout_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_manual_cutout() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return

        if self.man_cutout_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' %
                                 (_("Geometry object for manual cutout not found"), self.man_cutout_obj))
            return

        self.cutting_dia = abs(self.ui.dia.get_value())
        if not math.isfinite(self.cutting_dia) or self.cutting_dia == 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Tool Diameter is zero value. Change it to a positive real number."))
            return

        mb_dia = None
        mb_spacing = None
        if self.ui.gaptype_combo.get_value() == 2:  # "mouse bytes"
            mb_dia = self.ui.mb_dia_entry.get_value()
            mb_spacing = self.ui.mb_spacing_entry.get_value()
            if not _valid_mouse_bite_step(mb_dia, mb_spacing):
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("Mouse bite diameter and spacing must produce a positive finite step."))
                return

        try:
            _validate_thin_depth(
                self.ui.gaptype_combo.get_value(), True,
                self.ui.cutz_entry.get_value(), self.ui.thin_depth_entry.get_value())
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        self.app.inform.emit(_("Click a contour to add a bridge. Click a bridge from this session to select it, "
                              "then drag its orange grip or click to move; Delete removes it. Right-click finishes; "
                              "Escape cancels all changes in this session."))
        self.app.geo_editor.tool_shape.enabled = True
        self.mb_manual_cuts[:] = []
        self.manual_solid_geo = deepcopy(self.flatten(self.man_cutout_obj.solid_geometry))

        if self.ui.gaptype_combo.get_value() == 2:  # "mouse bytes"
            b_dia = (self.cutting_dia / 2.0) - (mb_dia / 2.0)
            # flaten manual geometry
            unified_man_geo = unary_union(self.manual_solid_geo)
            buff_man_geo = unified_man_geo.buffer(b_dia)
            if isinstance(buff_man_geo, MultiPolygon):
                int_list = []
                for b_geo in buff_man_geo.geoms:
                    int_list += b_geo.interiors
            elif isinstance(buff_man_geo, Polygon):
                int_list = buff_man_geo.interiors
            else:
                self.app.log.debug("Not supported geometry at the moment: %s" % type(buff_man_geo))
                return
            self.mb_manual_solid_geo = self.flatten(int_list)

        self.cutting_gapsize = self.ui.gapsize.get_value()

        name = self.ui.man_object_combo.currentText()
        # Get Geometry source object to be used as target for Manual adding Gaps
        try:
            self.man_cutout_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_manual_cutout() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return

        # Keep previews out of history until the session is accepted. Escape must not leave
        # redoable milling gaps whose pending mouse-bite drilling has been discarded.
        self.manual_session = (self.man_cutout_obj, deepcopy({
            'solid_geometry': self.man_cutout_obj.solid_geometry,
            'tools': self.man_cutout_obj.tools,
            'multigeo': self.man_cutout_obj.multigeo
        }))
        self.manual_bridges = []
        self.selected_manual_bridge = None
        self.manual_grip_press = None
        self.mouse_is_dragging = False

        self.app.plotcanvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)
        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)

        self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)
        self.app.plotcanvas.graph_event_connect('mouse_press', self.on_mouse_click_press)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_click_release)

        self.mouse_events_connected = True

        if self.ui.big_cursor_cb.get_value():
            app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
            self.old_cursor_type = self.app.options.get(
                "global_cursor_type", app_defaults.get(
                    "global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))
            self.app.on_cursor_type(val="big")

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.old_selection_state = self.app.options.get(
            "global_selection_shape", app_defaults.get(
                "global_selection_shape", AppDefaults.factory_defaults.get("global_selection_shape")))
        self.app.options['global_selection_shape'] = False
        # disable the notebook until finished
        self.app.ui.notebook.setDisabled(True)

    def on_manual_cutout(self, click_pos):

        if self.man_cutout_obj is None:
            msg = '[ERROR_NOTCL] %s: %s' % (_("Geometry object for manual cutout not found"), self.man_cutout_obj)
            self.app.inform.emit(msg)
            return

        if 1 not in self.man_cutout_obj.tools or 'data' not in self.man_cutout_obj.tools[1]:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No tool in the Geometry object."))
            return

        if getattr(self, 'manual_session', None) is not None:
            self.edit_manual_bridge(click_pos)
            return

        # use the snapped position as reference
        snapped_pos = self.app.geo_editor.snap(click_pos[0], click_pos[1])

        try:
            selected_path, gap_line, cut_poly = _manual_gap_geometry(
                self.man_cutout_obj.solid_geometry,
                (snapped_pos[0], snapped_pos[1]),
                self.ui.dia.get_value(),
                self.ui.gapsize.get_value())
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        try:
            _validate_thin_depth(
                self.ui.gaptype_combo.get_value(), True,
                self.ui.cutz_entry.get_value(), self.ui.thin_depth_entry.get_value())
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        try:
            expected_gap_length = abs(float(self.ui.dia.get_value())) + float(self.ui.gapsize.get_value())
            new_solid_geometry = _manual_subtract_selected(
                self.man_cutout_obj.solid_geometry, selected_path, gap_line,
                expected_gap_length=expected_gap_length)
        except (TypeError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        gap_type = self.ui.gaptype_combo.get_value()
        gaps_solid_geo = None
        if gap_type == 1 and self.ui.thin_depth_entry.get_value() != 0:     # "Thin gaps"
            gaps_solid_geo = gap_line

        if gap_type == 2:   # "Mouse Bytes"
            mb_paths = _cutout_paths(self.mb_manual_solid_geo)
            if mb_paths:
                mb_path = min(mb_paths, key=Point(snapped_pos).distance)
                self.mb_manual_cuts.extend(_cutout_paths(mb_path.intersection(cut_poly)))

        session = getattr(self, 'manual_session', None)
        with (nullcontext() if session is not None else
              history_of(self.app).snapshot(_("Manual Cutout"), [self.man_cutout_obj])):
            # first subtract geometry for the total solid_geometry
            try:
                new_solid_geometry = linemerge(new_solid_geometry)
            except ValueError:
                pass
            self.man_cutout_obj.solid_geometry = new_solid_geometry

            # then do it on each tool in the manual cutout Geometry object
            try:
                self.man_cutout_obj.multigeo = True

                self.man_cutout_obj.tools[1]['solid_geometry'] = new_solid_geometry
                self.man_cutout_obj.tools[1]['data']['name'] = self.man_cutout_obj.obj_options['name'] + '_cutout'
                self.man_cutout_obj.tools[1]['data']['tools_mill_cutz'] = self.ui.cutz_entry.get_value()
                self.man_cutout_obj.tools[1]['data']['tools_mill_multidepth'] = self.ui.mpass_cb.get_value()
                self.man_cutout_obj.tools[1]['data']['tools_mill_depthperpass'] = self.ui.maxdepth_entry.get_value()
            except KeyError:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No tool in the Geometry object."))
                return

            dia = abs(self.ui.dia.get_value())
            if gaps_solid_geo:
                if 99 not in self.man_cutout_obj.tools:
                    self.man_cutout_obj.tools.update({
                        99: deepcopy(self.cut_tool_dict)
                    })
                    self.man_cutout_obj.tools[99]['tooldia'] = str(dia)
                    self.man_cutout_obj.tools[99]['solid_geometry'] = [gaps_solid_geo]

                    self.man_cutout_obj.tools[99]['data']['name'] = self.man_cutout_obj.obj_options['name'] + '_cutout'
                    self.man_cutout_obj.tools[99]['data']['tools_mill_cutz'] = self.ui.thin_depth_entry.get_value()
                    self.man_cutout_obj.tools[99]['data']['tools_mill_multidepth'] = self.ui.mpass_cb.get_value()
                    self.man_cutout_obj.tools[99]['data']['tools_mill_depthperpass'] = self.ui.maxdepth_entry.get_value()
                    self.man_cutout_obj.tools[99]['data']['override_color'] = "#29a3a3fa"
                else:
                    self.man_cutout_obj.tools[99]['solid_geometry'].append(gaps_solid_geo)

        self.man_cutout_obj.plot(plot_tool=1)
        self.app.inform.emit('%s' % _("Added manual Bridge Gap. Left click to add another or right click to finish."))

        self.app.should_we_save = True

    def edit_manual_bridge(self, click_pos=None):
        """Rebuild active-session bridges from original paths, never by filling a cut gap."""
        obj, original = self.manual_session
        bridges = list(self.manual_bridges)
        selected = self.selected_manual_bridge
        try:
            if click_pos is None:
                if selected is None:
                    return
                del bridges[selected]
            else:
                pos = self.app.geo_editor.snap(click_pos[0], click_pos[1])
                path, line, poly = _manual_gap_geometry(
                    original['solid_geometry'], pos, self.ui.dia.get_value(), self.ui.gapsize.get_value())
                _validate_thin_depth(self.ui.gaptype_combo.get_value(), True,
                                     self.ui.cutz_entry.get_value(), self.ui.thin_depth_entry.get_value())
                bites = []
                if self.ui.gaptype_combo.get_value() == 2:
                    # Offset the selected closed contour independently: buffering all
                    # contours together can merge a neighboring inner contour's drill row.
                    if path.is_closed:
                        offset = (abs(self.ui.dia.get_value()) - self.ui.mb_dia_entry.get_value()) / 2.0
                        # The drill row belongs on the board side: a contour enclosing material
                        # is offset inward, a hole boundary outward. Winding is unreliable, so the
                        # side comes from the original contour nesting.
                        offset *= _manual_material_side(original['solid_geometry'], path)
                        mb_paths = _cutout_paths(Polygon(path).buffer(offset))
                    else:
                        mb_paths = _cutout_paths(self.mb_manual_solid_geo)
                    if mb_paths:
                        mb_path = min(mb_paths, key=Point(pos).distance)
                        bites = [part for part in _cutout_paths(mb_path.intersection(poly)) if not part.is_empty]
                bridge = (path, line, poly, bites, Point(path.interpolate(path.project(Point(pos)))))
                if selected is None:
                    bridges.append(bridge)
                else:
                    bridges[selected] = bridge

            # Subtract all intervals together on their original contour. Overlapping bridges
            # must keep their shared gap when either bridge is moved or removed.
            paths = []
            for path in _cutout_paths(original['solid_geometry']):
                gaps = [bridge[1] for bridge in bridges if bridge[0].equals(path)]
                paths.extend(_cutout_paths(_manual_subtract_selected([path], path, gaps)) if gaps else [path])
            geometry = unary_union(paths)
            try:
                geometry = linemerge(geometry)
            except ValueError:
                pass
        except (TypeError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return

        tools = deepcopy(obj.tools)
        for tool_id in (1, 99):
            if tool_id in original['tools']:
                tools[tool_id] = deepcopy(original['tools'][tool_id])
            else:
                tools.pop(tool_id, None)
        if bridges:
            tools[1]['solid_geometry'] = geometry
            tools[1]['data'].update({
                'name': obj.obj_options['name'] + '_cutout',
                'tools_mill_cutz': self.ui.cutz_entry.get_value(),
                'tools_mill_multidepth': self.ui.mpass_cb.get_value(),
                'tools_mill_depthperpass': self.ui.maxdepth_entry.get_value()
            })
            if self.ui.gaptype_combo.get_value() == 1 and self.ui.thin_depth_entry.get_value() != 0:
                if 99 not in tools:
                    tools[99] = deepcopy(self.cut_tool_dict)
                    tools[99]['tooldia'] = str(abs(self.ui.dia.get_value()))
                    tools[99]['solid_geometry'] = []
                    tools[99]['data'].update({
                        'name': obj.obj_options['name'] + '_cutout',
                        'tools_mill_cutz': self.ui.thin_depth_entry.get_value(),
                        'tools_mill_multidepth': self.ui.mpass_cb.get_value(),
                        'tools_mill_depthperpass': self.ui.maxdepth_entry.get_value(),
                        'override_color': '#29a3a3fa'
                    })
                tools[99]['solid_geometry'] = _cutout_paths(unary_union(
                    _cutout_paths(tools[99].get('solid_geometry', [])) + [bridge[1] for bridge in bridges]))
        obj.solid_geometry = geometry if bridges else deepcopy(original['solid_geometry'])
        obj.multigeo = True if bridges else original['multigeo']
        obj.tools = tools
        cuts = unary_union([part for bridge in bridges for part in bridge[3]])
        try:
            cuts = linemerge(cuts)
        except ValueError:
            pass
        self.mb_manual_cuts[:] = _cutout_paths(cuts)
        self.manual_bridges = bridges
        self.selected_manual_bridge = None
        # Keep a captured press until its release, even if Delete removed the bridge.
        self.app.geo_editor.tool_shape.clear(update=True)
        obj.plot()
        self.app.should_we_save = True
        self.app.inform.emit(_("Bridge updated. Click to add or select a bridge from this session; "
                              "right-click finishes, Escape cancels this session."))

    def finish_manual_session(self, cancel=False):
        if self.manual_session is None:
            return
        obj, original = self.manual_session
        self.manual_session = None
        self.manual_bridges = []
        self.selected_manual_bridge = None
        self.manual_grip_press = None
        self.mouse_is_dragging = False
        self.app.geo_editor.tool_shape.clear(update=True)
        # Only the cutout's tools are session-owned. Keep other tools and object options.
        restored_tools = deepcopy(obj.tools)
        for tool_id in (1, 99):
            if tool_id in original['tools']:
                restored_tools[tool_id] = original['tools'][tool_id]
            else:
                restored_tools.pop(tool_id, None)
        original['tools'] = restored_tools
        if cancel:
            for attr, value in original.items():
                setattr(obj, attr, value)
            self.mb_manual_cuts[:] = []
            obj.build_ui()
            obj.plot()
        else:
            history = history_of(self.app)
            if history.is_recording():
                after = object_state(obj)
                before = dict(after, **original)
                if not states_equal(before, after):
                    history.push(SnapshotStep(_("Manual Cutout"), [(obj.history_uid, before, after)]))

    def on_manual_geo(self):
        name = self.ui.obj_combo.currentText()

        # Get source object.
        try:
            cutout_obj = self.app.collection.get_by_name(str(name))
        except Exception as e:
            self.app.log.error("CutOut.on_manual_geo() --> %s" % str(e))
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), name))
            return "Could not retrieve object: %s" % name

        if cutout_obj is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("There is no Gerber object selected for Cutout.\n"
                                   "Select one and try again."))
            return

        if cutout_obj.kind != 'gerber':
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("The selected object has to be of Gerber type.\n"
                                   "Select a Gerber file and try again."))
            return

        dia = float(self.ui.dia.get_value())
        try:
            kind = self.ui.obj_kind_combo.get_value()
        except ValueError:
            return

        margin = float(self.ui.margin.get_value())
        convex_box = self.ui.convex_box_cb.get_value()
        shape_type = self.ui.cutout_shape_cb.get_value()
        cutz = self.ui.cutz_entry.get_value()
        multidepth = self.ui.mpass_cb.get_value()
        depthperpass = self.ui.maxdepth_entry.get_value()
        cut_tool_dict = deepcopy(self.cut_tool_dict)
        inner_active = self.inner_active()

        def geo_init(geo_obj, app_obj):
            try:
                if inner_active:
                    # Reuse automatic outline classification and offsets, but leave all
                    # bridge placement to the manual session (including inner paths).
                    result = cutout_plugin.freeform_cutout_paths(
                        cutout_obj, kind, dia, margin, 'None', 0.0,
                        convex_box, 0, 0.0, 0.0, 0.0, self.any_cutout_handler,
                        inner_active=True)
                    for message in result.errors:
                        app_obj.inform.emit('[ERROR_NOTCL] %s' % message)
                    for message in result.warnings:
                        app_obj.inform.emit('[WARNING_NOTCL] %s' % message)
                    if result.failed:
                        return 'fail'
                    solid_geo = result.solid_geo
                else:
                    solid_geo = CutOutGen.manual_cutout_geometry(
                        cutout_obj.solid_geometry, convex_box, kind, dia, margin, shape_type)
            except ValueError as err:
                app_obj.inform.emit('[ERROR_NOTCL] %s' % str(err))
                return 'fail'
            if solid_geo is not None:
                geo_obj.solid_geometry = solid_geo

            geo_obj.multigeo = True

            geo_obj.tools.update({
                1: deepcopy(cut_tool_dict)
            })
            geo_obj.tools[1]['tooldia'] = str(dia)
            geo_obj.tools[1]['solid_geometry'] = geo_obj.solid_geometry

            geo_obj.tools[1]['data']['name'] = outname
            geo_obj.tools[1]['data']['tools_mill_tooldia'] = str(dia)
            geo_obj.tools[1]['data']['tools_mill_cutz'] = cutz
            geo_obj.tools[1]['data']['tools_mill_multidepth'] = multidepth
            geo_obj.tools[1]['data']['tools_mill_depthperpass'] = depthperpass

        outname = cutout_obj.obj_options["name"] + "_cutout"
        self.new_object('geometry', outname, geo_init, source=cutout_obj, autoselected=False)

    def cutting_geo(self, pos):
        self.cutting_dia = float(self.ui.dia.get_value())
        self.cutting_gapsize = float(self.ui.gapsize.get_value())
        try:
            __, __, cut_poly = _manual_gap_geometry(
                self.manual_session[1]['solid_geometry'] if self.manual_session is not None else
                self.man_cutout_obj.solid_geometry,
                pos,
                self.cutting_dia,
                self.cutting_gapsize)
            return cut_poly
        except (AttributeError, TypeError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
            return GeometryCollection()

    def draw_manual_grip(self, pos=None):
        """Use the same pixel-sized filled square as the editor's vertex grips."""
        if self.selected_manual_bridge is None:
            return
        center = self.manual_bridges[self.selected_manual_bridge][4]
        if pos is not None:
            path = min(_cutout_paths(self.manual_session[1]['solid_geometry']), key=Point(pos).distance)
            center = path.interpolate(path.project(Point(pos)))
        half = 4 * self.canvas.pixel_size()
        self.app.geo_editor.tool_shape.add(
            shape=box(center.x - half, center.y - half, center.x + half, center.y + half),
            color='#FF8000FF', face_color='#FF8000FF', update=False, layer=0, tolerance=None)
        self.app.geo_editor.tool_shape.redraw()

    def on_mouse_click_press(self, event):
        self.mouse_is_dragging = False
        self.manual_grip_press = None
        if event.button != 1 or self.selected_manual_bridge is None:
            return
        pos = self.canvas.translate_coords(event.pos)
        center = self.manual_bridges[self.selected_manual_bridge][4]
        if center.distance(Point(float(pos[0]), float(pos[1]))) <= 6 * self.canvas.pixel_size():
            self.manual_grip_press = (float(event.pos[0]), float(event.pos[1]))

    # To be called after clicking on the plot.
    def on_mouse_click_release(self, event):

        event_pos = event.pos
        right_button = 2

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return
        event_pos = (x, y)

        if self.manual_grip_press is not None:
            press = self.manual_grip_press
            self.manual_grip_press = None
            self.mouse_is_dragging = False
            if self.selected_manual_bridge is None:
                return
            if event.button == 1 and math.dist(press, event_pos) >= 3:
                pos = self.canvas.translate_coords(event_pos)
                self.edit_manual_bridge((float(pos[0]), float(pos[1])))
            else:
                self.app.geo_editor.tool_shape.clear(update=False)
                self.draw_utility_geometry(self.manual_bridges[self.selected_manual_bridge][2])
                self.draw_manual_grip()
            return
        if self.mouse_is_dragging or getattr(event, 'is_dragging', False):
            self.mouse_is_dragging = False
            return

        # do paint single only for left mouse clicks
        if event.button == 1:
            pos = self.app.plotcanvas.translate_coords(event_pos)
            pos = (float(pos[0]), float(pos[1]))
            if self.manual_session is not None and self.manual_bridges and self.selected_manual_bridge is None:
                point = Point(pos)
                nearest = min(_cutout_paths(self.manual_session[1]['solid_geometry']), key=point.distance)
                hits = [index for index, bridge in enumerate(self.manual_bridges)
                        if bridge[0].equals(nearest) and bridge[2].covers(point)]
                if hits:
                    self.selected_manual_bridge = min(hits, key=lambda index: self.manual_bridges[index][4].distance(point))
                    self.app.geo_editor.tool_shape.clear(update=False)
                    self.draw_utility_geometry(self.manual_bridges[self.selected_manual_bridge][2])
                    self.draw_manual_grip()
                    self.app.inform.emit(_("Bridge selected. Drag the orange grip or click a new position to move; Delete removes. "
                                          "Right-click keeps it and finishes; Escape cancels the entire session."))
                    return
            self.on_manual_cutout(click_pos=pos)

        # if RMB then we exit
        elif event.button == right_button and self.mouse_is_dragging is False:
            self.finish_manual_session()
            self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
            self.app.plotcanvas.graph_event_disconnect('mouse_press', self.on_mouse_click_press)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)

            self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)

            # Remove any previous utility shape
            self.app.geo_editor.tool_shape.clear(update=True)
            self.app.geo_editor.tool_shape.enabled = False

            # signal that the mouse events are disconnected from local methods
            self.mouse_events_connected = False

            if self.ui.big_cursor_cb.get_value():
                # restore cursor
                self.app.on_cursor_type(val=self.old_cursor_type)
            # restore selection
            self.app.options['global_selection_shape'] = self.old_selection_state

            # rebuild the manual Geometry object
            self.man_cutout_obj.build_ui()

            # plot the final object
            self.man_cutout_obj.plot()

            # mouse bytes
            if self.ui.gaptype_combo.get_value() == 2 and self.mb_manual_cuts:  # "mouse bytes"
                mb_dia = self.ui.mb_dia_entry.get_value()
                mb_spacing = self.ui.mb_spacing_entry.get_value()
                if not _valid_mouse_bite_step(mb_dia, mb_spacing):
                    self.app.inform.emit('[ERROR_NOTCL] %s' %
                                         _("Mouse bite diameter and spacing must produce a positive finite step."))
                    self.mb_manual_cuts[:] = []
                    self.app.ui.notebook.setDisabled(False)
                    return

                with self.app.proc_container.new("Generating Excellon ..."):
                    manual_source = self.man_cutout_obj
                    manual_cuts = deepcopy(self.mb_manual_cuts)
                    outname_exc = manual_source.obj_options["name"] + "_mouse_bites"
                    self.app.collection.promise(outname_exc)
                    self.mb_manual_cuts[:] = []

                    def job_thread(app_obj):
                        # list of Shapely Points to mark the drill points centers
                        holes = CutOutGen.mouse_bite_holes(manual_cuts, mb_dia, mb_spacing)

                        def excellon_init(exc_obj, app_o):
                            if not holes:
                                return 'fail'

                            exc_obj.tools = {}
                            exc_obj.add_tool(mb_dia, drills=holes)
                            exc_obj.create_geometry()
                            exc_obj.regenerate_source()
                            # calculate the bounds
                            xmin, ymin, xmax, ymax = CutOut.recursive_bounds(exc_obj.solid_geometry)
                            exc_obj.obj_options['xmin'] = xmin
                            exc_obj.obj_options['ymin'] = ymin
                            exc_obj.obj_options['xmax'] = xmax
                            exc_obj.obj_options['ymax'] = ymax

                        ret = self.new_object('excellon', outname_exc, excellon_init,
                                              source=manual_source, autoselected=False)
                        if ret == 'fail':
                            app_obj.inform.emit('[ERROR_NOTCL] %s' % _("Mouse bites failed."))

                    self.app.worker_task.emit({'fcn': job_thread, 'params': [self.app]})

            self.app.ui.notebook.setDisabled(False)
            self.app.inform.emit('[success] %s' % _("Finished manual adding of gaps."))

    def on_mouse_move(self, event):

        if self.manual_grip_press is None:
            self.app.on_mouse_move_over_plot(event=event)

        event_pos = event.pos
        event_is_dragging = event.is_dragging

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return
        event_pos = (x, y)

        pos = self.canvas.translate_coords(event_pos)

        if event_is_dragging is True:
            self.mouse_is_dragging = True
        else:
            self.mouse_is_dragging = False

        try:
            x = float(pos[0])
            y = float(pos[1])
        except TypeError:
            return

        if self.app.grid_status():
            snap_x, snap_y = self.app.geo_editor.snap(x, y)
        else:
            snap_x, snap_y = x, y

        self.x_pos, self.y_pos = snap_x, snap_y

        cut_geo = self.cutting_geo(pos=(snap_x, snap_y))
        if self.selected_manual_bridge is not None:
            cut_geo = unary_union([cut_geo, self.manual_bridges[self.selected_manual_bridge][2]])

        # Remove any previous utility shape
        self.app.geo_editor.tool_shape.clear(update=False)
        self.draw_utility_geometry(geo=cut_geo)
        self.draw_manual_grip((snap_x, snap_y) if self.manual_grip_press is not None else None)

    def draw_utility_geometry(self, geo):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.app.geo_editor.tool_shape.add(
            shape=geo,
            color=(self.app.options.get(
                "global_draw_color", app_defaults.get(
                    "global_draw_color", AppDefaults.factory_defaults.get("global_draw_color")))),
            update=False,
            layer=0,
            tolerance=None)
        self.app.geo_editor.tool_shape.redraw()

    def on_key_press(self, event):
        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if type(event) is int:
            key = event
        # events from the GUI are of type QKeyEvent
        elif type(event) == QtGui.QKeyEvent:
            key = event.key()
        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        if key == QtCore.Qt.Key.Key_Delete or key == 'Delete':
            if self.manual_session is not None:
                self.edit_manual_bridge()
            return

        # Escape cancels the entire manual session, including accepted moves/removals.
        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':
            if self.mouse_events_connected is True:
                self.finish_manual_session(cancel=True)
                self.mouse_events_connected = False
                self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
                self.app.plotcanvas.graph_event_disconnect('mouse_press', self.on_mouse_click_press)
                self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
                self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)

                self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
                self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
                self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
                self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)

                if self.ui.big_cursor_cb.get_value():
                    # restore cursor
                    self.app.on_cursor_type(val=self.old_cursor_type)
                # restore selection
                self.app.options['global_selection_shape'] = self.old_selection_state
                self.mb_manual_cuts[:] = []

            # Remove any previous utility shape
            self.app.geo_editor.tool_shape.clear(update=True)
            self.app.geo_editor.tool_shape.enabled = False

            # restore the notebook state
            self.app.ui.notebook.setDisabled(False)
            self.app.inform.emit("[WARNING_NOTCL] %s" % _("Cancelled."))

        # Grid toggle
        if key == QtCore.Qt.Key.Key_G or key == 'G':
            self.app.ui.grid_snap_btn.trigger()

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            l_x, l_y = self.app.on_jump_to()
            self.app.geo_editor.tool_shape.clear(update=True)
            geo = self.cutting_geo(pos=(l_x, l_y))
            self.draw_utility_geometry(geo=geo)

    def subtract_poly_from_geo(self, solid_geo, pts):
        """
        Subtract polygon made from points from the given object.
        This only operates on the paths in the original geometry,
        i.e. it converts polygons into paths.

        :param solid_geo:   Geometry from which to subtract.
        :param pts:         a tuple of coordinates in format (x0, y0, x1, y1)
        :type pts:          tuple

        x0: x coord for lower left vertex of the polygon.
        y0: y coord for lower left vertex of the polygon.
        x1: x coord for upper right vertex of the polygon.
        y1: y coord for upper right vertex of the polygon.

        :return: none
        """

        return CutOutGen.subtract_poly_from_geo(solid_geo, pts)

    @staticmethod
    def flatten(geometry):
        """
        Creates a list of non-iterable linear geometry objects.
        Polygons are expanded into its exterior and interiors.

        Results are placed in self.flat_geometry

        :param geometry: Shapely type or list or a list of lists of such.
        """
        return CutOutGen.flatten(geometry)

    @staticmethod
    def recursive_bounds(geometry):
        """
        Return the bounds of the biggest bounding box in geometry, one that include all.

        :param geometry:    a iterable object that holds geometry
        :return:            Returns coordinates of rectangular bounds of geometry: (xmin, ymin, xmax, ymax).
        """

        return CutOutGen.recursive_bounds(geometry)

    def subtract_geo(self, target_geo, subtractor):
        """
        Subtract subtractor polygon from the target_geo. This only operates on the paths in the target_geo,
        i.e. it converts polygons into paths.

        :param target_geo:      geometry from which to subtract
        :param subtractor:      a list of Points, a LinearRing or a Polygon that will be subtracted from target_geo
        :return:                a unary_union of the resulting geometry
        """

        return CutOutGen.subtract_geo(target_geo, subtractor)

    @staticmethod
    def intersect_geo(target_geo, second_geo):
        """

        :param target_geo:
        :type target_geo:
        :param second_geo:
        :type second_geo:
        :return:
        :rtype:
        """

        return CutOutGen.intersect_geo(target_geo, second_geo)

    def reset_fields(self):
        self.ui.obj_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
