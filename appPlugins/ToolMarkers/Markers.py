# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 5/17/2020                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea

import logging
from copy import deepcopy

import gettext
import appTranslation as fcTranslate
import builtins

from appCommon.Common import LoudDict
from defaults import AppDefaults
from appPlugins.ToolMarkers.MarkersUI import MarkersUI
from appPlugins.ToolMarkers.MarkersGen import (
    create_marker_geometry, marker_cross, gerber_marker_data, geometry_marker_data, drill_locations)

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolMarkers(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        self.cursor_color_memory = None
        # store the current cursor type to be restored after manual geo
        self.old_cursor_type = self.app.options.get(
            "global_cursor_type",
            app_defaults.get("global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))

        self.decimals = self.app.decimals
        self.units = ''

        # here we store the locations of the selected locations
        self.points = LoudDict()
        self.points.set_change_callback(self.on_points_changed)

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = MarkersUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        # Objects involved in Copper thieving
        self.grb_object = None

        self.handlers_connected = False

        # storage for temporary shapes when adding manual markers
        self.temp_shapes = self.app.sel_shapes

        # app signal: connect only once, the plugin UI (and its widgets) is rebuilt on every run
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

    def on_insert_type_changed(self, val):
        obj_type = 2 if val == 'geo' else 0
        self.ui.obj_insert_combo.setRootModelIndex(self.app.collection.index(obj_type, 0, QtCore.QModelIndex()))
        self.ui.obj_insert_combo.setCurrentIndex(0)
        self.ui.obj_insert_combo.obj_type = {
            "grb": "gerber", "geo": "geometry"
        }[self.ui.insert_type_radio.get_value()]

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

                if kind in ['gerber', 'geometry']:
                    obj_type = {'gerber': 'grb', 'geometry': 'geo'}[kind]
                    self.ui.insert_type_radio.set_value(obj_type)
                    self.ui.obj_insert_combo.set_value(name)
            except Exception:
                pass

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolMarkers()")

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

        self.app.ui.notebook.setTabText(2, _("Markers"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+B', **kwargs)

    def connect_signals_at_init(self):

        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.add_marker_button.clicked.connect(self.add_markers)
        self.ui.toggle_all_cb.toggled.connect(self.on_toggle_all)
        self.ui.drill_button.clicked.connect(self.on_create_drill_object)
        self.ui.check_button.clicked.connect(self.on_create_check_object)
        self.ui.mode_combo.currentIndexChanged.connect(self.on_selection_changed)
        self.ui.insert_type_radio.activated_custom.connect(self.on_insert_type_changed)
        self.ui.big_cursor_cb.stateChanged.connect(self.on_cursor_change)
        self.ui.insert_markers_button.clicked.connect(self.on_insert_markers_in_external_objects)
        # set_tool_ui() rebuilds the UI, so defer it until the button's own signal returns
        self.ui.reset_button.clicked.connect(lambda: QtCore.QTimer.singleShot(0, self.set_tool_ui))

    def set_tool_ui(self):
        self.units = self.app.app_units

        self.clear_ui(self.layout)
        self.ui = MarkersUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.ui.thick_entry.set_value(self.app.options.get(
            "tools_markers_thickness",
            app_defaults.get("tools_markers_thickness", AppDefaults.factory_defaults.get("tools_markers_thickness"))))
        self.ui.l_entry.set_value(float(self.app.options.get(
            "tools_markers_length",
            app_defaults.get("tools_markers_length", AppDefaults.factory_defaults.get("tools_markers_length")))))

        self.ui.ref_radio.set_value(self.app.options.get(
            "tools_markers_reference",
            app_defaults.get("tools_markers_reference", AppDefaults.factory_defaults.get("tools_markers_reference"))))
        self.ui.offset_x_entry.set_value(float(self.app.options.get(
            "tools_markers_offset_x",
            app_defaults.get("tools_markers_offset_x", AppDefaults.factory_defaults.get("tools_markers_offset_x")))))
        self.ui.offset_y_entry.set_value(float(self.app.options.get(
            "tools_markers_offset_y",
            app_defaults.get("tools_markers_offset_y", AppDefaults.factory_defaults.get("tools_markers_offset_y")))))
        self.ui.offset_link_button.setChecked(True)
        self.ui.on_link_checked(True)

        self.ui.toggle_all_cb.set_value(False)
        self.ui.type_radio.set_value(self.app.options.get(
            "tools_markers_type",
            app_defaults.get("tools_markers_type", AppDefaults.factory_defaults.get("tools_markers_type"))))
        self.ui.drill_dia_entry.set_value(self.app.options.get(
            "tools_markers_drill_dia",
            app_defaults.get("tools_markers_drill_dia", AppDefaults.factory_defaults.get("tools_markers_drill_dia"))))
        self.ui.mode_combo.set_value(self.app.options.get(
            "tools_markers_mode",
            app_defaults.get("tools_markers_mode", AppDefaults.factory_defaults.get("tools_markers_mode"))))
        self.on_selection_changed(self.app.options.get(
            "tools_markers_mode",
            app_defaults.get("tools_markers_mode", AppDefaults.factory_defaults.get("tools_markers_mode"))))
        self.ui.insert_type_radio.set_value(val="grb")

        self.ui.big_cursor_cb.set_value(self.app.options.get(
            "tools_markers_big_cursor",
            app_defaults.get("tools_markers_big_cursor", AppDefaults.factory_defaults.get("tools_markers_big_cursor"))))

        self.points.clear()
        self.on_points_changed(None)

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.object_combo.set_value(obj_name)

        if obj is None:
            self.ui.object_combo.setCurrentIndex(0)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        # set cursor
        self.old_cursor_type = self.app.options.get(
            "global_cursor_type",
            app_defaults.get("global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))

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
        MarkersUI.on_level_changed(self.ui, checked)

    def on_toggle_all(self, val):
        MarkersUI.on_toggle_all(self.ui, val)

    def on_selection_changed(self, val):
        MarkersUI.on_selection_changed(self.ui, val)

    def on_cursor_change(self, val):
        if val:
            self.app.options['tools_markers_big_cursor'] = True
        else:
            self.app.options['tools_markers_big_cursor'] = False

    def add_markers(self):
        self.app.call_source = "markers_tool"

        # cleanup previous possible markers
        self.points.clear()

        select_type = self.ui.mode_combo.get_value()
        if select_type == 0:    # 'auto'
            self.handle_automatic_placement()
        elif select_type == 1:  # 'manual'
            self.app.inform.emit('%s' % _("Click to add next marker or right click to finish."))

            if self.ui.big_cursor_cb.get_value():
                self.app.on_cursor_type(val="big", control_cursor=True)
                self.cursor_color_memory = self.app.plotcanvas.cursor_color
                self.app.plotcanvas.cursor_color = '#000000FF'
                self.app.app_cursor.enabled = True
            else:
                self.app.on_cursor_type(val="small", control_cursor=True)

            # it works only with cross markers
            self.ui.type_radio.set_value('c')
            self.app.ui.notebook.setDisabled(True)
            self.connect_event_handlers()
        else:   # 'numeric'
            self.handle_numeric_placement()

    def handle_automatic_placement(self):
        self.points.clear()

        tl_state = self.ui.tl_cb.get_value()
        tr_state = self.ui.tr_cb.get_value()
        bl_state = self.ui.bl_cb.get_value()
        br_state = self.ui.br_cb.get_value()

        self.grb_object = self._selected_gerber()
        if self.grb_object is None:
            self.app.call_source = "app"
            return

        xmin, ymin, xmax, ymax = self.grb_object.bounds()

        if tl_state:
            self.points['tl'] = (xmin, ymax)
        if tr_state:
            self.points['tr'] = (xmax, ymax)
        if bl_state:
            self.points['bl'] = (xmin, ymin)
        if br_state:
            self.points['br'] = (xmax, ymin)

        ret_val = self.add_marker_geo(self.points, g_obj=self.grb_object)
        self.app.call_source = "app"
        if ret_val == 'fail':
            self.app.call_source = "app"
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        self.on_exit()

    def handle_manual_placement(self):
        # self.app.inform.emit('[ERROR_NOTCL] %s' % "Not implemented yet.")

        self.grb_object = self._selected_gerber()
        if self.grb_object is None:
            self.on_exit()
            return

        ret_val = self.add_marker_geo(self.points, g_obj=self.grb_object)
        if ret_val == 'fail':
            self.on_exit(ok=False)
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return
        self.on_exit()

    def handle_numeric_placement(self):
        location = self.ui.marker_coords_entry.get_value()
        if not isinstance(location, tuple):
            self.app.log.error("ToolMarkers.handle_numeric_placement() -> not a tuple")
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        if 'manual' not in self.points:
            self.points['manual'] = []
        self.points['manual'].append(location)
        self.draw_utility_geometry(pos=location)

        self.grb_object = self._selected_gerber()
        if self.grb_object is None:
            self.on_exit()
            return

        ret_val = self.add_marker_geo(self.points, g_obj=self.grb_object)
        if ret_val == 'fail':
            self.app.log.error("ToolMarkers.handle_numeric_placement() -> failed to add numeric geo")
            self.on_exit(ok=False)
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return
        self.on_exit()

    def add_marker_geo(self, points_storage, g_obj):
        """
        Add geometry to the solid_geometry of the copper Gerber object

        :param points_storage:  a dictionary holding the points where to add markers
        :param g_obj:           the Gerber object where to add the geometry
        :return:                None
        """

        geo_list = self.create_marker_geometry(points_storage)
        if geo_list == "fail" or not geo_list:
            return "fail"

        assert isinstance(geo_list, list), 'Geometry list should be a list but the type is: %s' % str(type(geo_list))
        new_name = '%s_%s' % (str(self.grb_object.obj_options['name']), 'markers')

        return_val = self.generate_gerber_obj_with_markers(
            new_grb_obj=g_obj, marker_geometry=geo_list, outname=new_name)

        return return_val

    def _selected_gerber(self):
        """
        Get the Gerber object currently selected in the object combo.

        :return:    the Gerber object or None (an error is logged and the user is warned)
        """
        selection_index = self.ui.object_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.object_combo.rootModelIndex())

        try:
            return model_index.internalPointer().obj
        except Exception as e:
            self.app.log.error("ToolMarkers._selected_gerber() --> %s" % str(e))
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
            return None

    def offset_values(self, offset_reference=None, offset_x=None, offset_y=None):
        """
        Will offset a set of x, y coordinates depending on the chosen reference

        :param offset_reference:    can be 'c' = center of the bounding box or 'e' = edge of the bounding box
        :type offset_reference:     str
        :param offset_x:            value to offset on X axis
        :type offset_x:             float
        :param offset_y:            value to offset on Y axis
        :type offset_y:             float
        :return:                    a tuple of offsets (x, y) or None if the reference object is not available
        :rtype:                     tuple | None
        """
        if offset_reference is None:
            offset_reference = self.ui.ref_radio.get_value()
        if offset_x is None:
            offset_x = self.ui.offset_x_entry.get_value()
        if offset_y is None:
            offset_y = self.ui.offset_y_entry.get_value()

        if offset_reference == 'c':  # reference from the bounding box center
            self.grb_object = self._selected_gerber()
            if self.grb_object is None:
                return None

            xmin, ymin, xmax, ymax = self.grb_object.bounds()
            center_x = (xmax - xmin) / 2
            center_y = (ymax - ymin) / 2
            offset_x -= center_x
            offset_y -= center_y

        return offset_x, offset_y

    def create_marker_geometry(self, points_storage):
        """
        Will create the marker geometry in the specified points.

        :param points_storage:  a dictionary holding the marker locations
        :type points_storage:   dict
        :return:                a list of Shapely lines
        :rtype:                 list
        """
        marker_type = self.ui.type_radio.get_value()
        line_thickness = self.ui.thick_entry.get_value()
        line_length = self.ui.l_entry.get_value() / 2.0
        mode = self.ui.mode_combo.get_value()

        offsets = self.offset_values()
        if offsets is None:
            return 'fail'
        offset_x, offset_y = offsets

        if not points_storage:
            self.app.inform.emit("[ERROR_NOTCL] %s." % _("Please select at least a location"))
            return 'fail'

        geo_list = create_marker_geometry(points_storage, marker_type, line_thickness, line_length,
                                          mode, offset_x, offset_y)
        if geo_list == 'fail':
            self.app.log.warning("Not enough points.")
        return geo_list

    def generate_gerber_obj_with_markers(self, new_grb_obj, marker_geometry, outname):
        """

        :param new_grb_obj:     a Gerber App object
        :type new_grb_obj:
        :param marker_geometry: a list of Shapely geometries
        :type marker_geometry:  list
        :param outname:         the name for the new Gerber object
        :type outname:          str
        :return:
        :rtype:
        """
        line_thickness = self.ui.thick_entry.get_value()

        new_apertures, solid_geometry, follow_geometry = gerber_marker_data(
            new_grb_obj.tools, new_grb_obj.solid_geometry, new_grb_obj.follow_geometry,
            marker_geometry, line_thickness, self.app.options, self.app.app_units)

        def initialize(grb_obj, app_obj):
            grb_obj.obj_options = LoudDict()
            for opt in new_grb_obj.obj_options:
                if opt != 'name':
                    grb_obj.obj_options[opt] = deepcopy(new_grb_obj.obj_options[opt])
            grb_obj.obj_options['name'] = outname
            grb_obj.multitool = False
            grb_obj.multigeo = False
            grb_obj.follow = deepcopy(new_grb_obj.follow)
            grb_obj.tools = new_apertures
            grb_obj.solid_geometry = solid_geometry
            grb_obj.follow_geometry = follow_geometry

            grb_obj.regenerate_source(name=outname)

        ret = self.new_object('gerber', outname, initialize, source=new_grb_obj, plot=True, autoselected=False)

        return ret

    def generate_geo_obj_with_markers(self, new_geo_obj, marker_geometry, outname):
        """

        :param new_geo_obj:     a Geometry App object
        :type new_geo_obj:
        :param marker_geometry: a list of Shapely geometries
        :type marker_geometry:  list
        :param outname:         the name for the new Geometry object
        :type outname:          str
        :return:
        :rtype:
        """
        tooldia = self.ui.thick_entry.get_value()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        new_data = {}
        tool_found = next((tool for tool, tool_dict in new_geo_obj.tools.items()
                           if tool_dict['tooldia'] == tooldia), None)
        if not tool_found:
            for opt_key in self.app.options:
                if opt_key.find('geometry' + "_") == 0:
                    oname = opt_key[len('geometry') + 1:]
                    new_data[oname] = self.app.options.get(
                        opt_key, app_defaults.get(opt_key, AppDefaults.factory_defaults.get(opt_key)))
            for opt_key in self.app.options:
                if opt_key.find('tools_') == 0:
                    new_data[opt_key] = self.app.options.get(
                        opt_key, app_defaults.get(opt_key, AppDefaults.factory_defaults.get(opt_key)))

        new_tools, solid_geometry = geometry_marker_data(
            new_geo_obj.tools, new_geo_obj.solid_geometry, marker_geometry, tooldia, new_data)

        def initialize(geo_obj, app_obj):
            geo_obj.obj_options = LoudDict()
            for opt in new_geo_obj.obj_options:
                geo_obj.obj_options[opt] = deepcopy(new_geo_obj.obj_options[opt])
            geo_obj.obj_options['name'] = outname

            # Propagate options
            geo_obj.obj_options["tools_mill_tooldia"] = app_obj.options.get(
                "tools_mill_tooldia",
                app_obj.defaults.get("tools_mill_tooldia",
                                     app_obj.defaults.factory_defaults.get("tools_mill_tooldia")))
            geo_obj.solid_geometry = solid_geometry

            geo_obj.multitool = True
            geo_obj.multigeo = True
            geo_obj.tools = deepcopy(new_tools)

        ret = self.new_object('geometry', outname, initialize, source=new_geo_obj, plot=True, autoselected=False)

        return ret

    def on_create_drill_object(self):
        self.app.call_source = "markers_tool"

        tooldia = self.ui.drill_dia_entry.get_value()

        if tooldia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("The tool diameter is zero.")))
            return

        line_thickness = self.ui.thick_entry.get_value()
        tl_state = self.ui.tl_cb.get_value()
        tr_state = self.ui.tr_cb.get_value()
        bl_state = self.ui.bl_cb.get_value()
        br_state = self.ui.br_cb.get_value()

        self.grb_object = self._selected_gerber()
        if self.grb_object is None:
            self.app.call_source = "app"
            return

        if tl_state is False and tr_state is False and bl_state is False and br_state is False and \
                'manual' not in self.points:
            self.app.inform.emit("[ERROR_NOTCL] %s." % _("Please select at least a location"))
            self.app.call_source = "app"
            return

        xmin, ymin, xmax, ymax = self.grb_object.bounds()
        offsets = self.offset_values()
        if offsets is None:
            self.app.call_source = "app"
            return
        offset_x, offset_y = offsets

        if 'manual' in self.points and not self.points['manual']:
            self.app.inform.emit("[ERROR_NOTCL] %s." % _("Please select at least a location"))
            self.app.call_source = "app"
            return
        drill_list = drill_locations((xmin, ymin, xmax, ymax), (tl_state, tr_state, bl_state, br_state),
                                     self.points, line_thickness, offset_x, offset_y)

        def obj_init(obj_inst, app_inst):
            obj_inst.obj_options.update({
                'name': outname
            })
            obj_inst.tools = {}
            obj_inst.add_tool(tooldia, drills=drill_list, data={})
            obj_inst.create_geometry()
            obj_inst.regenerate_source()

        outname = '%s_%s' % (str(self.grb_object.obj_options['name']), 'corner_drills')
        ret_val = self.new_object("excellon", outname, obj_init, source=self.grb_object, autoselected=False)

        self.app.call_source = "app"
        if ret_val == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
        else:
            self.app.inform.emit('[success] %s' % _("Excellon object with corner drills created."))

    def on_create_check_object(self):
        self.app.call_source = "markers_tool"

        tooldia = 0.1 if self.units == 'MM' else 0.0254

        if tooldia == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("The tool diameter is zero.")))
            return

        line_thickness = self.ui.thick_entry.get_value()
        tl_state = self.ui.tl_cb.get_value()
        tr_state = self.ui.tr_cb.get_value()
        bl_state = self.ui.bl_cb.get_value()
        br_state = self.ui.br_cb.get_value()

        self.grb_object = self._selected_gerber()
        if self.grb_object is None:
            self.app.call_source = "app"
            return

        if tl_state is False and tr_state is False and bl_state is False and br_state is False and \
                'manual' not in self.points:
            self.app.inform.emit("[ERROR_NOTCL] %s." % _("Please select at least a location"))
            self.app.call_source = "app"
            return

        xmin, ymin, xmax, ymax = self.grb_object.bounds()
        offsets = self.offset_values()
        if offsets is None:
            self.app.call_source = "app"
            return
        offset_x, offset_y = offsets

        if 'manual' in self.points and not self.points['manual']:
            self.app.inform.emit("[ERROR_NOTCL] %s." % _("Please select at least a location"))
            self.app.call_source = "app"
            return
        drill_list = drill_locations((xmin, ymin, xmax, ymax), (tl_state, tr_state, bl_state, br_state),
                                     self.points, line_thickness, offset_x, offset_y)

        def obj_init(new_obj, app_inst):
            new_obj.tools = {}
            new_obj.add_tool(tooldia, drills=drill_list, data={})

            # make sure we use the special preprocessor for checking
            for tool in new_obj.tools:
                new_obj.tools[tool]['data']['tools_drill_ppname_e'] = 'Check_points'

            new_obj.create_geometry()
            new_obj.obj_options.update({
                'name': outname,
                'tools_drill_cutz': -0.1 if self.units == 'MM' else -0.004,
                'tools_drill_ppname_e': 'Check_points'
            })
            new_obj.regenerate_source()

        outname = '%s_%s' % (str(self.grb_object.obj_options['name']), 'verification')
        ret_val = self.new_object("excellon", outname, obj_init, source=self.grb_object, autoselected=False)

        self.app.call_source = "app"
        if ret_val == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
        else:
            self.app.inform.emit('[success] %s' % _("Excellon object with verification points created."))

    def on_insert_markers_in_external_objects(self):
        obj_type = self.ui.insert_type_radio.get_value()    # values in ["grb", "geo"]

        geo_list = self.create_marker_geometry(self.points)
        if geo_list == "fail" or not geo_list:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return
        assert isinstance(geo_list, list), 'Geometry list should be a list but the type is: %s' % str(
            type(geo_list))

        if obj_type == 'grb':
            # get the Gerber object on which the corner marker will be inserted
            selection_index = self.ui.obj_insert_combo.currentIndex()
            model_index = self.app.collection.index(selection_index, 0, self.ui.obj_insert_combo.rootModelIndex())

            try:
                new_grb_obj = model_index.internalPointer().obj
            except Exception as e:
                self.app.log.error("ToolMarkers.on_insert_markers_in_external_objects() Gerber --> %s" % str(e))
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
                self.app.call_source = "app"
                return

            new_name = '%s_%s' % (str(new_grb_obj.obj_options['name']), 'markers')
            return_val = self.generate_gerber_obj_with_markers(new_grb_obj=new_grb_obj, marker_geometry=geo_list,
                                                               outname=new_name)
        else:
            # get the Geometry object on which the corner marker will be inserted
            geo_obj_name = self.ui.obj_insert_combo.get_value()

            try:
                new_geo_obj = self.app.collection.get_by_name(geo_obj_name)
            except Exception as e:
                self.app.log.error("ToolMarkers.on_insert_markers_in_external_objects() Geometry --> %s" % str(e))
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Geometry object available."))
                self.app.call_source = "app"
                return

            new_name = '%s_%s' % (str(new_geo_obj.obj_options['name']), 'markers')
            return_val = self.generate_geo_obj_with_markers(new_geo_obj=new_geo_obj, marker_geometry=geo_list,
                                                            outname=new_name)
        if return_val == "fail":
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
        else:
            self.app.inform.emit('[success] %s' % _("Done."))

    def on_points_changed(self, val=None):
        MarkersUI.on_points_changed(self.ui, self.points)

    def on_exit(self, cancelled=None, ok=True):
        self.clear_utility_geometry()

        # restore cursor
        self.app.on_cursor_type(val=self.old_cursor_type, control_cursor=False)
        if self.cursor_color_memory is not None:
            self.app.plotcanvas.cursor_color = self.cursor_color_memory
            self.cursor_color_memory = None

        # update the bounding box values
        try:
            a, b, c, d = self.grb_object.bounds()
            self.grb_object.obj_options['xmin'] = a
            self.grb_object.obj_options['ymin'] = b
            self.grb_object.obj_options['xmax'] = c
            self.grb_object.obj_options['ymax'] = d
        except Exception as e:
            self.app.log.error("ToolMarkers.on_exit() copper_obj bounds error --> %s" % str(e))

        self.app.call_source = "app"
        self.app.ui.notebook.setDisabled(False)
        self.disconnect_event_handlers()

        if cancelled is True:
            self.app.delete_selection_shape()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled by user request."))
            return

        if ok:
            self.app.inform.emit('[success] %s' % _("A Gerber object with corner markers was created."))

    def connect_event_handlers(self):
        if self.handlers_connected is False:
            self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

            self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_release)

            self.handlers_connected = True

    def disconnect_event_handlers(self):
        if self.handlers_connected is True:
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)

            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)

            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            self.handlers_connected = False
            self.app.ui.notebook.setDisabled(False)

    def on_mouse_release(self, event):
        event_pos = event.pos
        right_button = 2

        if event.button == 1:
            pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
            if self.app.grid_status():
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
            else:
                pos = (pos_canvas[0], pos_canvas[1])

            if 'manual' not in self.points:
                self.points['manual'] = []
            self.points['manual'].append(pos)
            self.draw_utility_geometry(pos=pos)

            self.app.inform.emit(
                '%s: %d. %s' %
                (_("Added marker"), len(self.points['manual']),
                 _("Click to add next marker or right click to finish.")))

        elif event.button == right_button and self.app.event_is_dragging is False:
            if not self.points.get('manual'):
                self.on_exit(cancelled=True)
                return
            self.handle_manual_placement()

    def draw_utility_geometry(self, pos):
        line_thickness = self.ui.thick_entry.get_value()
        line_length = self.ui.l_entry.get_value() / 2.0

        MarkersUI.draw_utility_geometry(self.temp_shapes, marker_cross(pos, line_thickness, line_length))

    def clear_utility_geometry(self):
        self.temp_shapes.clear(update=True)
        self.temp_shapes.redraw()

    def on_plugin_cleanup(self):
        if self.handlers_connected:
            self.on_exit(ok=False)
        else:
            self.clear_utility_geometry()
