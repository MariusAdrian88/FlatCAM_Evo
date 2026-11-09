# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 11/21/2019                                         #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from appCommon.Common import LoudDict
from appPlugins.ToolFiducials.FiducialsGen import auto_points, fiducial_geometry, fiducial_shapes
from appPlugins.ToolFiducials.FiducialsUI import FidoUI
from camlib import flatten_shapely_geometry

import logging
from copy import deepcopy
from shapely import Point

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolFiducials(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app

        self.cursor_color_memory = None
        # the cursor type to be restored after the manual placement; None: the placement did not change the cursor
        self.old_cursor_type = None

        self.decimals = self.app.decimals
        self.units = ''

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = FidoUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName

        # Objects involved in adding the fiducials
        self.grb_object = None
        self.sm_object = None

        self.mode_method = False

        # Tool properties
        self.margin_val = None
        self.sec_position = None

        # the points of the running manual placement
        self.click_points = []
        # the points of the last added fiducials; the soldermask openings go on them
        self.fiducial_points = []

        self.handlers_connected = False
        # storage for temporary shapes when adding manual markers
        self.temp_shapes = self.app.sel_shapes

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolFiducials()")

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

        self.app.ui.notebook.setTabText(2, _("Fiducials"))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+F', **kwargs)

    def connect_signals_at_init(self):
        # #############################################################################
        # ############################ SIGNALS ########################################
        # #############################################################################
        self.ui.level.toggled.connect(self.on_level_changed)
        self.ui.add_cfid_button.clicked.connect(self.add_fiducials)
        self.ui.add_sm_opening_button.clicked.connect(self.add_soldermask_opening)

        self.ui.fid_type_combo.currentIndexChanged.connect(self.on_fiducial_type)
        self.ui.pos_radio.activated_custom.connect(self.on_second_point)
        self.ui.mode_radio.activated_custom.connect(self.on_method_change)

        self.ui.big_cursor_cb.stateChanged.connect(self.on_cursor_change)

        # the UI is rebuilt, so it can't happen inside the clicked slot of its own button
        self.ui.reset_button.clicked.connect(lambda: QtCore.QTimer.singleShot(0, self.set_tool_ui))

    def set_tool_ui(self):
        if self.handlers_connected:
            self.on_exit(cancelled=True)

        self.units = self.app.app_units

        self.clear_ui(self.layout)
        self.ui = FidoUI(layout=self.layout, app=self.app)
        self.add_object_creation_controls()
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        self.ui.fid_size_entry.set_value(self.app.options.get(
            "tools_fiducials_dia",
            self.app.defaults.get("tools_fiducials_dia", self.app.defaults.factory_defaults.get("tools_fiducials_dia"))
        ))
        self.ui.margin_entry.set_value(float(self.app.options.get(
            "tools_fiducials_margin",
            self.app.defaults.get("tools_fiducials_margin", self.app.defaults.factory_defaults.get("tools_fiducials_margin"))
        )))
        self.ui.mode_radio.set_value(self.app.options.get(
            "tools_fiducials_mode",
            self.app.defaults.get("tools_fiducials_mode", self.app.defaults.factory_defaults.get("tools_fiducials_mode"))
        ))
        self.ui.pos_radio.set_value(self.app.options.get(
            "tools_fiducials_second_pos",
            self.app.defaults.get("tools_fiducials_second_pos", self.app.defaults.factory_defaults.get("tools_fiducials_second_pos"))
        ))
        self.ui.fid_type_combo.set_value(self.app.options.get(
            "tools_fiducials_type",
            self.app.defaults.get("tools_fiducials_type", self.app.defaults.factory_defaults.get("tools_fiducials_type"))
        ))
        # needed so the visibility of some objects will be updated
        self.on_fiducial_type(val=self.ui.fid_type_combo.get_value())
        self.ui.line_thickness_entry.set_value(float(self.app.options.get(
            "tools_fiducials_line_thickness",
            self.app.defaults.get("tools_fiducials_line_thickness",
                                  self.app.defaults.factory_defaults.get("tools_fiducials_line_thickness"))
        )))

        self.click_points = []
        self.fiducial_points = []
        self.ui.bottom_left_coords_entry.set_value('')
        self.ui.top_right_coords_entry.set_value('')
        self.ui.sec_points_coords_entry.set_value('')

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            self.app.defaults.get("global_app_level", self.app.defaults.factory_defaults.get("global_app_level"))
        )
        self.change_level(app_mode)

        # SELECT THE CURRENT OBJECT
        obj = self.app.collection.get_active()
        if obj and obj.kind == 'gerber':
            obj_name = obj.obj_options['name']
            self.ui.grb_object_combo.set_value(obj_name)

        if obj is None:
            self.ui.grb_object_combo.setCurrentIndex(0)

        self.ui.big_cursor_cb.set_value(self.app.options.get(
            "tools_fiducials_big_cursor",
            self.app.defaults.get("tools_fiducials_big_cursor",
                                  self.app.defaults.factory_defaults.get("tools_fiducials_big_cursor"))
        ))

        # set_value() emits the signals only on a change, so the dependent widgets are updated here
        self.on_method_change(self.ui.mode_radio.get_value())
        self.on_second_point(self.ui.pos_radio.get_value())

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
        FidoUI.set_level(self.ui, checked)

    def on_second_point(self, val):
        FidoUI.set_second_point(self.ui, val)

    def on_method_change(self, val):
        """
        Make sure that on method change we disconnect the event handlers and reset the points storage

        :param val:     value of the Radio button which trigger this method
        :return:        None
        """
        self.click_points = []

        if val == 'auto':
            try:
                self.disconnect_event_handlers()
            except TypeError:
                pass

            self.ui.big_cursor_cb.hide()
        else:
            self.ui.big_cursor_cb.show()

    def on_cursor_change(self, val):
        if val:
            self.app.options['tools_fiducials_big_cursor'] = True
        else:
            self.app.options['tools_fiducials_big_cursor'] = False

    def on_fiducial_type(self, val):
        FidoUI.set_fiducial_type(self.ui, val)

    def selected_gerber(self):
        """The Gerber object selected in the Source Object combo or None if there is none."""
        selection_index = self.ui.grb_object_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.grb_object_combo.rootModelIndex())

        try:
            return model_index.internalPointer().obj
        except Exception as e:
            self.app.log.error("ToolFiducials.selected_gerber() --> %s" % str(e))
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
            return None

    def auto_fiducial_points(self, g_obj):
        """The points of the automatic mode for the bounds of g_obj. They are also shown in the Coordinates."""
        points = auto_points(g_obj.bounds(), self.ui.margin_entry.get_value(), self.ui.pos_radio.get_value(),
                             self.decimals)
        for point, entry in zip(points, (self.ui.bottom_left_coords_entry, self.ui.top_right_coords_entry,
                                         self.ui.sec_points_coords_entry)):
            entry.set_value('(%.*f, %.*f)' % (self.decimals, point[0], self.decimals, point[1]))
        return points

    def add_fiducials(self):
        self.mode_method = self.ui.mode_radio.get_value()
        self.sec_position = self.ui.pos_radio.get_value()
        fid_type = self.ui.fid_type_combo.get_value()

        # get the Gerber object on which the Fiducial will be inserted
        self.grb_object = self.selected_gerber()
        if self.grb_object is None:
            return

        self.app.call_source = "fiducials_tool"
        self.app.ui.notebook.setDisabled(True)

        self.click_points = []
        self.fiducial_points = []

        if self.mode_method == 'auto':
            self.click_points = self.auto_fiducial_points(self.grb_object)
            ret_val = self.add_fiducials_geo(self.click_points, g_obj=self.grb_object, fid_type=fid_type)
            if ret_val != 'fail':
                self.fiducial_points = list(self.click_points)
            self.on_exit(failed=(ret_val == 'fail'))
        else:
            self.app.inform.emit(_("Click to add first Fiducial. Bottom Left..."))
            self.ui.bottom_left_coords_entry.set_value('')
            self.ui.top_right_coords_entry.set_value('')
            self.ui.sec_points_coords_entry.set_value('')

            self.old_cursor_type = self.app.options.get(
                "global_cursor_type",
                self.app.defaults.get("global_cursor_type",
                                      self.app.defaults.factory_defaults.get("global_cursor_type"))
            )
            if self.ui.big_cursor_cb.get_value():
                self.app.on_cursor_type(val="big", control_cursor=True)
                self.cursor_color_memory = self.app.plotcanvas.cursor_color
                self.app.plotcanvas.cursor_color = '#000000FF'
                self.app.app_cursor.enabled = True
            else:
                self.app.on_cursor_type(val="small", control_cursor=True)

            self.connect_event_handlers()

        # To be called after clicking on the plot.

    def add_fiducials_geo(self, points_list, g_obj, fid_size=None, fid_type=None, line_size=None):
        """
        Add geometry to the solid_geometry of the copper Gerber object

        :param points_list:     list of coordinates for the fiducials
        :param g_obj:           the Gerber object where to add the geometry
        :param fid_size:        the overall size of the fiducial or fiducial opening depending on the g_obj type
        :param fid_type:        the type of fiducial: 0 circular, 1 cross, 2 chess
        :param line_size:       the line thickenss when the fiducial type is cross
        :return:
        """
        fid_size = self.ui.fid_size_entry.get_value() if fid_size is None else fid_size
        fid_type = 0 if fid_type is None else fid_type  # default is 'circular' <=> 0
        line_thickness = self.ui.line_thickness_entry.get_value() if line_size is None else line_size

        new_apertures, solid_geometry, geo_list = fiducial_geometry(
            points_list, g_obj.tools, g_obj.solid_geometry, fid_size, fid_type, line_thickness,
            self.app.options, self.decimals, self.app.app_units)

        outname = '%s_%s' % (str(g_obj.obj_options['name']), 'fid')

        def initialize(grb_obj, app_obj):
            grb_obj.obj_options = LoudDict()
            for opt in g_obj.obj_options:
                if opt != 'name':
                    grb_obj.obj_options[opt] = deepcopy(g_obj.obj_options[opt])
            grb_obj.obj_options['name'] = outname
            grb_obj.multitool = False
            grb_obj.multigeo = False
            grb_obj.follow = deepcopy(g_obj.follow)
            grb_obj.tools = new_apertures
            grb_obj.solid_geometry = solid_geometry
            old_follow = [] if g_obj.follow_geometry is None else deepcopy(g_obj.follow_geometry)
            grb_obj.follow_geometry = flatten_shapely_geometry(old_follow) + flatten_shapely_geometry(geo_list)

            grb_obj.regenerate_source(name=outname)

        ret = self.new_object('gerber', outname, initialize, source=g_obj, plot=True)

        return ret

    def add_soldermask_opening(self):
        sm_opening_dia = self.ui.fid_size_entry.get_value() * 2.0

        # get the Gerber object on which the soldermask openings will be inserted
        self.sm_object = self.selected_gerber()
        if self.sm_object is None:
            return

        # the openings must sit over the copper fiducials, so they use the points of the last added fiducials
        points = list(self.fiducial_points)
        if not points:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Add the fiducials first ..."))
            return

        ret_val = self.add_fiducials_geo(points, g_obj=self.sm_object, fid_size=sm_opening_dia, fid_type=0)
        self.on_exit(failed=(ret_val == 'fail'))

    def on_mouse_release(self, event):
        event_pos = event.pos
        right_button = 2

        if event.button == 1 and not self.app.event_is_dragging:
            pos_canvas = self.app.plotcanvas.translate_coords(event_pos)
            if self.app.grid_status():
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
            else:
                pos = (pos_canvas[0], pos_canvas[1])
            click_pt = Point([pos[0], pos[1]])

            self.click_points.append(
                (
                    float('%.*f' % (self.decimals, click_pt.x)),
                    float('%.*f' % (self.decimals, click_pt.y))
                )
            )
            # the preview is drawn only while the placement continues
            if not self.check_points():
                self.draw_utility_geometry(pos=pos)
        elif event.button == right_button and self.app.event_is_dragging is False:
            self.on_exit(cancelled=True)

    def check_points(self):
        """
        Show the clicked points. When the last one is clicked add the fiducials and end the placement.

        :return:    True if the placement ended
        """
        fid_type = self.ui.fid_type_combo.get_value()
        needed = 2 if self.sec_position == 'no' else 3
        count = len(self.click_points)

        if count == 1:
            self.ui.bottom_left_coords_entry.set_value(self.click_points[0])
            self.app.inform.emit(_("Click to add the last fiducial. Top Right..."))
        elif count == 2:
            self.ui.top_right_coords_entry.set_value(self.click_points[1])
            if needed == 3:
                self.app.inform.emit(_("Click to add the second fiducial. Top Left or Bottom Right..."))
        elif count == 3:
            self.ui.sec_points_coords_entry.set_value(self.click_points[2])

        if count < needed:
            return False

        self.app.inform.emit('[success] %s' % _("Done."))
        self.fiducial_points = list(self.click_points)
        ret_val = self.add_fiducials_geo(self.click_points, g_obj=self.grb_object, fid_type=fid_type)
        self.on_exit(failed=(ret_val == 'fail'))
        return True

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

    def on_exit(self, cancelled=False, failed=False):
        """
        The end of every placement: done, failed or cancelled.
        The new object is plotted by new_object(), the source objects are not changed.
        """
        # restore the cursor only if the manual placement changed it
        if self.old_cursor_type is not None:
            self.app.on_cursor_type(val=self.old_cursor_type, control_cursor=False)
            self.old_cursor_type = None
        if self.cursor_color_memory is not None:
            self.app.plotcanvas.cursor_color = self.cursor_color_memory
            self.cursor_color_memory = None

        self.clear_utility_geometry()
        self.click_points = []

        self.disconnect_event_handlers()

        self.app.call_source = "app"
        self.app.ui.notebook.setDisabled(False)

        if cancelled is True:
            self.app.delete_selection_shape()
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled by user request."))
            return

        if failed is True:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        self.app.inform.emit('[success] %s' % _("Fiducials Tool exit."))

    def draw_utility_geometry(self, pos):
        fid_type = self.ui.fid_type_combo.get_value()
        line_thickness = self.ui.line_thickness_entry.get_value()
        fid_size = self.ui.fid_size_entry.get_value()
        pt = pos[0], pos[1]     # we ensure that this works in case the pt tuple is a return from Vispy (4 members)
        geo_list = fiducial_shapes(pt, fid_size, fid_type, line_thickness, self.app.options,
                                   self.app.app_units)

        outline = '#0000FFAF'
        for util_geo in geo_list:
            self.temp_shapes.add(util_geo, color=outline, update=True, layer=0, tolerance=None)

        self.temp_shapes.redraw()

    def clear_utility_geometry(self):
        self.temp_shapes.clear(update=True)
        self.temp_shapes.redraw()

    def on_plugin_cleanup(self):
        if self.handlers_connected:
            self.on_exit(cancelled=True)
        else:
            self.clear_utility_geometry()
