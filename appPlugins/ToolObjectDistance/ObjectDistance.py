# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 09/29/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea

import logging
from copy import deepcopy
from shapely import Point
from appPlugins.ToolObjectDistance.ObjectDistanceUI import ObjectDistanceUI
from appPlugins.ToolObjectDistance.ObjectDistanceGen import (
    measurement_points, update_angle, update_deltas, update_distance, update_half_distance)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ObjectDistance(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.canvas = self.app.plotcanvas
        self.units = self.app.app_units.lower()
        self.decimals = self.app.decimals

        self.active = False
        self.original_call_source = None

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = ObjectDistanceUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.h_point = (0, 0)

    def run(self, toggle=False):
        # if the plugin was already launched do not do it again
        if self.active is True:
            return

        if self.app.plugin_tab_locked is True:
            return

        # if the splitter is hidden, display it
        if self.app.ui.splitter.sizes()[0] == 0:
            self.app.ui.splitter.setSizes([1, 1])

        if toggle:
            pass

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

        # Remove anything else in the appGUI
        self.app.ui.plugin_scroll_area.takeWidget()

        # Put oneself in the appGUI
        self.app.ui.plugin_scroll_area.setWidget(self)

        # Switch notebook to tool page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

        # the flag is cleared also when the measuring fails (not two objects selected): a flag left set made
        # Shift+M do nothing for the rest of the session
        self.active = True
        try:
            self.set_tool_ui()
            self.app.ui.notebook.setTabText(2, _("Object Distance"))

            # activate the plugin
            self.activate_measure_tool()
        finally:
            self.active = False

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Shift+M', **kwargs)

    def connect_signals_at_init(self):
        self.ui.measure_btn.clicked.connect(self.activate_measure_tool)
        self.ui.jump_hp_btn.clicked.connect(self.on_jump_to_half_point)
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        self.ui.distance_type_combo.currentIndexChanged.connect(self.on_didstance_type_changed)

    def set_tool_ui(self):
        self.units = self.app.app_units.lower()

        # initial view of the layout
        self.init_plugin()

    def init_plugin(self):
        ObjectDistanceUI.reset_measurement(self.ui)

    def on_didstance_type_changed(self):
        self.init_plugin()

    def activate_measure_tool(self):
        # ENABLE the Measuring TOOL
        self.ui.jump_hp_btn.setDisabled(False)

        self.units = self.app.app_units.lower()
        self.original_call_source = deepcopy(self.app.call_source)

        measuring_type = self.ui.distance_type_combo.get_value()

        if measuring_type == 0:      # 0 is "nearest points"
            if self.app.call_source == 'app':
                first_pos, last_pos = self.measure_nearest_in_app()
            elif self.app.call_source == 'geo_editor':
                first_pos, last_pos = self.measure_nearest_in_geo_editor()
            elif self.app.call_source == 'exc_editor':
                first_pos, last_pos = self.measure_nearest_in_exc_editor()
            elif self.app.call_source == 'grb_editor':
                first_pos, last_pos = self.measure_nearest_in_grb_editor()
            else:
                first_pos, last_pos = Point((0, 0)), Point((0, 0))
        else:
            if self.app.call_source == 'app':
                first_pos, last_pos = self.measure_center_in_app()
            elif self.app.call_source == 'geo_editor':
                first_pos, last_pos = self.measure_center_in_geo_editor()
            elif self.app.call_source == 'exc_editor':
                first_pos, last_pos = self.measure_center_in_exc_editor()
            elif self.app.call_source == 'grb_editor':
                first_pos, last_pos = self.measure_center_in_grb_editor()
            else:
                first_pos, last_pos = Point((0, 0)), Point((0, 0))

        if first_pos == "fail":
            return

        # self.ui.start_entry.set_value("(%.*f, %.*f)" % (self.decimals, first_pos.x, self.decimals, first_pos.y))
        # self.ui.stop_entry.set_value("(%.*f, %.*f)" % (self.decimals, last_pos.x, self.decimals, last_pos.y))

        # update start point
        val_start = self.update_start(first_pos)
        self.display_start(val_start)

        # update end point
        val_stop = self.update_end_point(last_pos)
        self.display_end(val_stop)

        # update deltas
        dx, dy = self.update_deltas(first_pt=first_pos, second_pt=last_pos)
        self.display_deltas(dx, dy)

        # update angle
        angle_val = self.update_angle(dx=dx, dy=dy)
        self.display_angle(angle_val)

        # update the total distance
        d = self.update_distance(dx, dy)
        self.display_distance(d)

        self.h_point = self.update_half_distance(first_pos, last_pos, dx, dy)
        if measuring_type == 0:  # 0 is "nearest points"
            if d != 0:
                self.display_half_distance(self.h_point)
            else:
                self.display_half_distance((0.0, 0.0))
                intersect_loc = "(%.*f, %.*f)" % (self.decimals, self.h_point[0], self.decimals, self.h_point[1])
                msg = '[WARNING_NOTCL] %s: %s' % (_("Objects intersects or touch at"), intersect_loc)
                self.app.inform.emit(msg)
        else:
            self.display_half_distance(self.h_point)

    def measure_nearest_in_app(self):
        selected_objs = self.app.collection.get_selected()
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].solid_geometry, selected_objs[1].solid_geometry)

    def measure_nearest_in_geo_editor(self):
        selected_objs = self.app.geo_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"
        return measurement_points(selected_objs[0].geo, selected_objs[1].geo, normalize=False)

    def measure_nearest_in_grb_editor(self):
        selected_objs = self.app.grb_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].geo['solid'], selected_objs[1].geo['solid'], normalize=False)

    def measure_nearest_in_exc_editor(self):
        selected_objs = self.app.exc_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].geo, selected_objs[1].geo, drills=True)

    def measure_center_in_app(self):
        selected_objs = self.app.collection.get_selected()
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].solid_geometry, selected_objs[1].solid_geometry, center=True)

    def measure_center_in_geo_editor(self):
        selected_objs = self.app.geo_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"
        return measurement_points(selected_objs[0].geo, selected_objs[1].geo, center=True, normalize=False)

    def measure_center_in_grb_editor(self):
        selected_objs = self.app.grb_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].geo['solid'], selected_objs[1].geo['solid'],
                                  center=True, normalize=False)

    def measure_center_in_exc_editor(self):
        selected_objs = self.app.exc_editor.selected
        if len(selected_objs) != 2:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                 (_("Select two objects and no more. Currently the selection has objects: "),
                                  str(len(selected_objs))))
            return "fail", "fail"

        return measurement_points(selected_objs[0].geo, selected_objs[1].geo, center=True, drills=True)

    def on_jump_to_half_point(self):
        self.app.on_jump_to(custom_location=self.h_point)
        self.app.inform.emit('[success] %s: %s' %
                             (_("Jumped to the half point between the two selected objects"),
                              "(%.*f, %.*f)" % (self.decimals, self.h_point[0], self.decimals, self.h_point[1])))

    def update_angle(self, dx, dy):
        try:
            angle = update_angle(dx, dy)
        except Exception as e:
            self.app.log.error("ObjectDistance.update_angle() -> %s" % str(e))
            return None
        return angle

    def display_angle(self, val):
        ObjectDistanceUI.display_angle(self.ui, val, self.app.dec_format, self.decimals)

    def update_start(self, pt):
        return ObjectDistanceUI.format_point(pt, self.app.dec_format, self.decimals)

    def display_start(self, val):
        ObjectDistanceUI.display_start(self.ui, val)

    def update_end_point(self, pt):
        # update the end point value
        return ObjectDistanceUI.format_point(pt, self.app.dec_format, self.decimals)

    def display_end(self, val):
        ObjectDistanceUI.display_end(self.ui, val)

    @staticmethod
    def update_deltas(first_pt, second_pt):
        return update_deltas(first_pt, second_pt)

    def display_deltas(self, dx, dy):
        ObjectDistanceUI.display_deltas(self.ui, dx, dy, self.app.dec_format, self.decimals)

    @staticmethod
    def update_distance(dx, dy):
        return update_distance(dx, dy)

    def display_distance(self, val):
        ObjectDistanceUI.display_distance(self.ui, val, self.decimals)

    @staticmethod
    def update_half_distance(first_pos, last_pos, dx, dy):
        return update_half_distance(first_pos, last_pos, dx, dy)

    def display_half_distance(self, val):
        ObjectDistanceUI.display_half_distance(self.ui, val, self.app.dec_format, self.decimals)

    def on_plugin_cleanup(self):
        self.active = False
        self.app.call_source = self.original_call_source
        self.app.inform.emit('%s' % _("Done."))
