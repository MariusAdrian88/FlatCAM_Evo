# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appTool import AppTool
from appGUI.GUIElements import VerticalScrollArea
from defaults import AppDefaults
from appPlugins.ToolDistance.DistanceUI import DistanceUI
from appPlugins.ToolDistance.DistanceGen import (
    point_distance, point_deltas, measurement_angle, closest_center, app_snap_index, editor_snap_index,
    click_action, preview_active, dimension_segments, NEW, CLOSE, CANCEL, EXIT)

import logging
from copy import copy
import numpy as np

from shapely import Point

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Distance(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.app = app
        self.decimals = self.app.decimals

        self.canvas = self.app.plotcanvas
        self.units = self.app.app_units.lower()

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = DistanceUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName

        # the committed points of the current (or last closed) measurement
        self.points = []

        # the tool is active (it owns the canvas mouse events)
        self.active = False
        # a measurement is open (OPEN state); False is the READY state
        self.measuring = False
        # last cursor position, the free end of the preview segment
        self.cursor_pos = None
        # units of the values shown on the Tool tab; a units change makes them stale
        self.results_units = None

        self.original_call_source = 'app'

        # monitor if the tool was used
        self.tool_done = False

        self.total_distance = 0.0

        # store the grid status here
        self.grid_status_memory = False

        # store here if the snap button was clicked
        self.snap_toggled = None

        self.mouse_is_dragging = False
        self._snap_cache = {}

        # store here the cursor color
        self.cursor_color_memory = None
        # store the current cursor type to be restored after manual geo
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.old_cursor_type = self.app.options.get(
            "global_cursor_type",
            app_defaults.get("global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))

        # dimension graphics (DimensionLines), made on the first draw
        self.dims = None
        # segments of the earlier measurements of this tool session; they stay on the canvas until the tool stops
        self.kept_segments = []

        # Signals
        self.ui.measure_btn.clicked.connect(self.on_start_measuring)
        self.ui.multipoint_cb.stateChanged.connect(self.on_multipoint_measurement_changed)
        self.ui.big_cursor_cb.stateChanged.connect(self.on_cursor_change)

    def run(self, toggle=False):

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

                        if self.active:
                            self.on_exit()
                        return
            except AttributeError:
                pass

            super().run()
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        self.on_start_measuring() if self.active is False else self.on_exit()

    def init_plugin(self):
        self.reset_measurement()
        self.original_call_source = copy(self.app.call_source)
        self.units = self.app.app_units.lower()

    def reset_measurement(self):
        # The Tool tab is not touched: its values survive a tool restart. Only the start of a new
        # measurement (begin_measurement) clears them.
        self.points = []
        self.measuring = False
        self.tool_done = False
        self.total_distance = 0.0
        self.mouse_is_dragging = False
        self.invalidate_snap_cache()
        self.kept_segments = []
        self.clear_dimensions()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Ctrl+M', **kwargs)

    def set_tool_ui(self):
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

        self.app.ui.notebook.setTabText(2, _("Distance"))

        # Remove anything else in the appGUI
        self.app.ui.plugin_scroll_area.takeWidget()

        # Put ourselves in the appGUI
        self.app.ui.plugin_scroll_area.setWidget(self)

        # Switch notebook to tool page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
        self.units = self.app.app_units.lower()

        self.app.command_active = "Distance"
        self.tool_done = False
        self.grid_status_memory = True if self.app.ui.grid_snap_btn.isChecked() else False

        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.ui.snap_center_cb.set_value(self.app.options.get(
            'tools_dist_snap_center',
            app_defaults.get('tools_dist_snap_center', AppDefaults.factory_defaults.get('tools_dist_snap_center'))))
        self.ui.big_cursor_cb.set_value(self.app.options.get(
            'tools_dist_big_cursor',
            app_defaults.get('tools_dist_big_cursor', AppDefaults.factory_defaults.get('tools_dist_big_cursor'))))

        snap_center = self.app.options.get(
            'tools_dist_snap_center',
            app_defaults.get('tools_dist_snap_center', AppDefaults.factory_defaults.get('tools_dist_snap_center')))
        self.on_snap_toggled(snap_center)

        try:
            self.ui.snap_center_cb.toggled.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.snap_center_cb.toggled.connect(self.on_snap_toggled)

        # this is a hack; seems that triggering the grid will make the visuals better
        # trigger it twice to return to the original state
        self.app.ui.grid_snap_btn.trigger()
        self.app.ui.grid_snap_btn.trigger()

        DistanceUI.set_units(self.ui, self.units)
        # the results of the last measurement stay on the tab unless the units changed since
        if self.results_units != self.units:
            self.initial_view()
            self.results_units = self.units

        big_cursor = self.ui.big_cursor_cb.get_value()
        self.app.on_cursor_type(val="big" if big_cursor else "small", control_cursor=True)
        if big_cursor:
            self.app.app_cursor.enabled = True

        self.app.call_source = 'measurement'

    def initial_view(self):
        self.display_start((0.0, 0.0))
        self.display_end((0.0, 0.0))
        DistanceUI.reset_results(self.ui, self.decimals)

    def on_snap_toggled(self, state):
        self.app.options['tools_dist_snap_center'] = state
        if state and self.active:
            # disengage the grid snapping since it will be hard to find the drills or pads on grid
            if self.app.ui.grid_snap_btn.isChecked():
                self.app.ui.grid_snap_btn.trigger()

    def on_start_measuring(self):
        if self.active:
            self.on_exit()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.old_cursor_type = self.app.options.get(
            "global_cursor_type",
            app_defaults.get("global_cursor_type", AppDefaults.factory_defaults.get("global_cursor_type")))
        self.cursor_color_memory = self.app.plotcanvas.cursor_color
        # ENABLE the Measuring TOOL
        self.active = True

        # disable the measuring button
        self.ui.measure_btn.setDisabled(True)
        self.ui.measure_btn.setText('%s...' % _("Working"))

        self.init_plugin()
        self.ui_connect()
        self.set_tool_ui()

        self.app.inform.emit(_("MEASURING: Click on the Start point ..."))

    def ui_connect(self):
        self.app.app_obj.object_changed.connect(self.invalidate_snap_cache)
        # we can connect the app mouse events to the measurement tool
        # NEVER DISCONNECT THOSE before connecting some other handlers; it breaks something in VisPy
        self.canvas.graph_event_connect('mouse_move', self.on_mouse_move)
        self.canvas.graph_event_connect('mouse_release', self.on_mouse_click_release)

        # we disconnect the mouse/key handlers from wherever the measurement tool was called
        if self.app.call_source == 'app':
            self.canvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
            self.canvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
            self.canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        elif self.app.call_source == 'geo_editor':
            self.canvas.graph_event_disconnect('mouse_move', self.app.geo_editor.on_canvas_move)
            self.canvas.graph_event_disconnect('mouse_press', self.app.geo_editor.on_canvas_click)
            self.canvas.graph_event_disconnect('mouse_release', self.app.geo_editor.on_canvas_click_release)

        elif self.app.call_source == 'exc_editor':
            self.canvas.graph_event_disconnect('mouse_move', self.app.exc_editor.on_canvas_move)
            self.canvas.graph_event_disconnect('mouse_press', self.app.exc_editor.on_canvas_click)
            self.canvas.graph_event_disconnect('mouse_release', self.app.exc_editor.on_exc_click_release)

        elif self.app.call_source == 'grb_editor':
            self.canvas.graph_event_disconnect('mouse_move', self.app.grb_editor.on_canvas_move)
            self.canvas.graph_event_disconnect('mouse_press', self.app.grb_editor.on_canvas_click)
            self.canvas.graph_event_disconnect('mouse_release', self.app.grb_editor.on_canvas_click_release)

    def ui_disconnect(self):
        self.app.app_obj.object_changed.disconnect(self.invalidate_snap_cache)
        source = self.original_call_source if self.app.call_source == 'measurement' else None
        if source == 'app':
            self.canvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
            self.canvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

        elif source == 'geo_editor':
            self.canvas.graph_event_connect('mouse_move', self.app.geo_editor.on_canvas_move)
            self.canvas.graph_event_connect('mouse_press', self.app.geo_editor.on_canvas_click)
            self.canvas.graph_event_connect('mouse_release', self.app.geo_editor.on_canvas_click_release)

        elif source == 'exc_editor':
            self.canvas.graph_event_connect('mouse_move', self.app.exc_editor.on_canvas_move)
            self.canvas.graph_event_connect('mouse_press', self.app.exc_editor.on_canvas_click)
            self.canvas.graph_event_connect('mouse_release', self.app.exc_editor.on_exc_click_release)

        elif source == 'grb_editor':
            self.canvas.graph_event_connect('mouse_move', self.app.grb_editor.on_canvas_move)
            self.canvas.graph_event_connect('mouse_press', self.app.grb_editor.on_canvas_click)
            self.canvas.graph_event_connect('mouse_release', self.app.grb_editor.on_canvas_click_release)

        # disconnect the mouse/key events from functions of measurement tool
        self.canvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
        self.canvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)

    def on_exit(self):
        if not self.active:
            return
        owns_interaction = self.app.call_source == 'measurement'
        # DISABLE the Measuring TOOL. The Tool tab keeps the values of the last measurement.
        self.active = False
        self.measuring = False
        self.points = []
        self.total_distance = 0.0
        self.mouse_is_dragging = False
        self.invalidate_snap_cache()

        # enable the measuring button
        self.ui.measure_btn.setDisabled(False)
        self.ui.measure_btn.setText(_("Measure"))

        self.ui_disconnect()

        # remove the dimension graphics of all the measurements
        self.kept_segments = []
        self.clear_dimensions()

        if not owns_interaction:
            return
        self.app.call_source = copy(self.original_call_source)
        self.app.command_active = None

        # restore cursor
        self.app.on_cursor_type(val=self.old_cursor_type, control_cursor=False)
        if self.cursor_color_memory is not None:
            self.app.plotcanvas.cursor_color = self.cursor_color_memory

        # restore the grid status
        if self.app.ui.grid_snap_btn.isChecked() != self.grid_status_memory:
            self.app.ui.grid_snap_btn.trigger()

        if self.tool_done is False:
            self.tool_done = True
            self.app.inform.emit('%s' % _("Done."))

    def calculate_distance(self, pos):
        if not self.points:
            return
        if len(self.points) > 1:
            self.total_distance += point_distance(self.points[-1], self.points[-2])
        self.display_committed_results()
        self.redraw_dimensions()
        if not self.ui.multipoint_cb.get_value():
            if len(self.points) == 1:
                self.app.inform.emit(_("Click on the DESTINATION point ..."))
            else:
                self.tool_done = True
                self.app.inform.emit("[success] %s %s" % (_("Done."), self.next_step_hint()))
            return
        self.app.inform.emit(_("Click to add next point or right click to finish. Backspace removes the last point."))

    @staticmethod
    def next_step_hint():
        return _("Left click: new measurement. Right click: exit.")

    def begin_measurement(self):
        """Left click in READY: clear the tab; the click becomes point 1. The graphics of the earlier
        measurements stay on the canvas until the tool stops."""
        self.kept_segments += dimension_segments(self.points, self.decimals)
        self.points = []
        self.total_distance = 0.0
        self.measuring = True
        self.tool_done = False
        self.initial_view()
        self.results_units = self.units
        self.redraw_dimensions()

    def close_measurement(self, cancel=False):
        """Right click in OPEN: back to READY. A close keeps the tab values and the committed graphics;
        a cancel (fewer than 2 points) leaves zeros on the tab and removes only its own graphics."""
        self.measuring = False
        if cancel:
            self.points = []
            self.total_distance = 0.0
            self.initial_view()
            self.redraw_dimensions()
            self.app.inform.emit("[WARNING_NOTCL] %s %s" % (_("Cancelled."), self.next_step_hint()))
            return
        self.display_committed_results()
        self.redraw_dimensions()
        self.tool_done = True
        self.app.inform.emit("[success] %s %s" % (_("Done."), self.next_step_hint()))

    def dimension_lines(self):
        if self.dims is None:
            # imported here: the visual is needed only once the tool draws
            from appGUI.DimensionVisual import DimensionLines
            self.dims = DimensionLines(self.canvas)
        return self.dims

    def redraw_dimensions(self):
        """Earlier measurements, the committed segments and, while the measurement takes points, the preview
        to the cursor."""
        show_preview = preview_active(self.measuring, self.ui.multipoint_cb.get_value(), len(self.points))
        cursor = self.cursor_pos if show_preview else None
        segments = self.kept_segments + dimension_segments(self.points, self.decimals, cursor)
        if segments:
            self.dimension_lines().set_data(segments)
        else:
            self.clear_dimensions()

    def clear_dimensions(self):
        if self.dims is not None:
            self.dims.clear()

    def display_committed_results(self):
        start = self.points[0] if self.points else (0.0, 0.0)
        end = self.points[-1] if self.points else start
        dx, dy = point_deltas(end, start)
        self.display_start(self.update_end_point(start))
        self.display_end(self.update_end_point(end))
        self.display_deltas(dx, dy)
        self.display_angle(self.update_angle(dx, dy))
        self.display_distance(self.total_distance)
        segment = point_distance(self.points[-1], self.points[-2]) if len(self.points) > 1 else 0.0
        DistanceUI.display_feedback(self.ui, segment, len(self.points), self.decimals)
        self.app.ui.update_location_labels(dx=dx, dy=dy, x=end[0], y=end[1])

    def undo_last_point(self):
        if not self.active or not self.measuring or not self.ui.multipoint_cb.get_value() or not self.points:
            return
        self.points.pop()
        self.total_distance = sum(point_distance(b, a) for a, b in zip(self.points, self.points[1:]))
        if not self.points:
            # nothing left to measure from: back to READY
            self.measuring = False
            self.initial_view()
            self.redraw_dimensions()
            self.app.inform.emit(_("MEASURING: Click on the Start point ..."))
            return
        self.display_committed_results()
        self.redraw_dimensions()

    def invalidate_snap_cache(self, *args):
        self._snap_cache = {}

    def resolve_snap(self, pos):
        current_pt = Point(pos)
        cache = self._snap_cache
        centers = []
        source = self.original_call_source
        if source == 'app':
            objects = self.app.collection.get_list()
            for obj in objects:
                if obj.kind not in ('gerber', 'excellon') or not obj.obj_options.get('plot', True):
                    continue
                key = id(obj)
                cached = cache.get(key)
                if cached is None or cached[0] is not obj.tools or cached[1] is not obj.solid_geometry:
                    cached = cache[key] = (obj.tools, obj.solid_geometry, app_snap_index(obj))
                center = cached[2].closest(current_pt)
                if center is not None:
                    centers.append(center)
        elif source in ('exc_editor', 'grb_editor'):
            editor = self.app.exc_editor if source == 'exc_editor' else self.app.grb_editor
            storage = editor.storage_dict
            # Existing editor revisions include in-place edits, worker commits and
            # undo/redo; list identity and length alone miss same-count changes.
            revision = editor.history.revision if source == 'exc_editor' else editor.geometry_revision
            key = (source, id(storage), editor.history.session, revision, tuple(
                (k, id(s), id(s.objects), len(s.objects)) if source == 'exc_editor' else
                (k, id(s.get('geometry')), len(s.get('geometry', ()))) for k, s in storage.items()))
            if key not in cache:
                cache.clear()
                cache[key] = editor_snap_index(source, storage)
            center = cache[key].closest(current_pt)
            if center is not None:
                centers.append(center)
        if not centers:
            return pos
        center = closest_center(centers, current_pt)
        return center.x, center.y

    def snap_handler(self, pos):
        snapped = self.resolve_snap(pos)
        if snapped == pos:
            return pos
        pos = snapped
        self.app.on_jump_to(custom_location=pos, fit_center=False)
        self.update_cursor(pos)
        return pos

    def update_cursor(self, pos):
        self.app.app_cursor.enabled = True
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        self.app.app_cursor.set_data(np.asarray([(pos[0], pos[1])]),
                                     symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                     edge_width=self.app.options.get(
                                         "global_cursor_width",
                                         app_defaults.get("global_cursor_width",
                                                          AppDefaults.factory_defaults.get("global_cursor_width"))),
                                     size=self.app.options.get(
                                         "global_cursor_size",
                                         app_defaults.get("global_cursor_size",
                                                          AppDefaults.factory_defaults.get("global_cursor_size"))))

    def on_multipoint_measurement_changed(self, val):
        DistanceUI.on_multipoint_measurement_changed(self.ui, val)
        if self.active and self.measuring:
            # the open measurement belongs to the other mode: close it as a right click would
            self.close_measurement(cancel=len(self.points) < 2)

    def on_cursor_change(self, val):
        self.app.options['tools_dist_big_cursor'] = bool(val)
        if not self.active:
            return
        if val:
            self.app.on_cursor_type(val="big", control_cursor=True)
        else:
            self.app.on_cursor_type(val="small", control_cursor=True)

    def update_position_info(self, pos_canvas):
        pos = (pos_canvas[0], pos_canvas[1])
        snap_center = self.ui.snap_center_cb.get_value()
        grid = self.app.grid_status() and not snap_center
        if snap_center:
            pos = self.resolve_snap(pos)
        elif grid:
            pos = self.app.geo_editor.snap(*pos)
        if self.ui.big_cursor_cb.get_value() or grid or pos != tuple(pos_canvas):
            self.update_cursor(pos)
        else:
            self.app.app_cursor.enabled = False
        return pos

    def on_mouse_click_release(self, event):
        # mouse click releases will be accepted only if the left button is clicked
        # this is necessary because right mouse click or middle mouse click
        # are used for panning on the canvas
        # log.debug("Distance Tool --> mouse click release")

        if not self.active:
            return
        if self.app.call_source != 'measurement':
            self.on_exit()
            return
        press = getattr(event, 'press_event', None)
        if press is not None:
            # VisPy's release is_dragging means "has a press", even for a click.
            dragging = self.mouse_is_dragging or not np.array_equal(event.pos, press.pos)
        else:
            dragging = self.mouse_is_dragging or bool(getattr(event, 'is_dragging', False))
        self.mouse_is_dragging = False
        if dragging:
            return
        action = click_action(self.measuring, event.button, self.ui.multipoint_cb.get_value(), len(self.points))
        if action is None:
            return
        if action == EXIT:
            already_done = self.tool_done
            self.on_exit()
            self.close_plugin_tab()
            if already_done:
                self.app.inform.emit('%s' % _("Done."))
            return
        if action in (CLOSE, CANCEL):
            self.close_measurement(cancel=action == CANCEL)
            return
        snap_enabled = self.ui.snap_center_cb.get_value()

        event_pos = event.pos

        pos_canvas = self.canvas.translate_coords(event_pos)
        if snap_enabled is False:
            # if GRID is active we need to get the snapped positions
            if self.app.grid_status():
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
            else:
                pos = pos_canvas[0], pos_canvas[1]
        else:
            pos = self.snap_handler(pos=(pos_canvas[0], pos_canvas[1]))

        if pos is not None and self.active:
            if action == NEW:
                self.begin_measurement()
            # Reset here the relative coordinates so there is a new reference on the click position
            if len(self.points) == 1:
                self.app.ui.update_location_labels(0.0, 0.0, pos[0], pos[1])

            self.cursor_pos = pos
            self.points.append(pos)
            self.calculate_distance(pos=pos)

    def on_mouse_move(self, event):
        if not self.active:
            return
        if self.app.call_source != 'measurement':
            self.on_exit()
            return
        multipoint = self.ui.multipoint_cb.get_value()

        try:  # May fail in case mouse not within axes
            event_pos = event.pos
            self.mouse_is_dragging = event.is_dragging

            try:
                x = float(event_pos[0])
                y = float(event_pos[1])
            except TypeError:
                return
            pos_canvas = self.app.plotcanvas.translate_coords((x, y))

            # update position info
            pos = self.update_position_info(pos_canvas)

            # ------------------------------------------------------------
            # Update Status Bar location labels
            # ------------------------------------------------------------
            dx = pos[0]
            dy = pos[1]
            if self.measuring and len(self.points) == 1:
                dx = pos[0] - float(self.points[0][0])
                dy = pos[1] - float(self.points[0][1])
            elif self.measuring and len(self.points) == 2:
                dx = float(self.points[1][0]) - float(self.points[0][0])
                dy = float(self.points[1][1]) - float(self.points[0][1])
            self.app.ui.update_location_labels(dx=dx, dy=dy, x=pos[0], y=pos[1])

            # self.app.ui.update_location_labels(dx=None, dy=None, x=pos[0], y=pos[1])
            self.app.plotcanvas.on_update_text_hud(dx, dy, pos[0], pos[1])

        except Exception as e:
            self.app.log.error("Distance.on_mouse_move() position --> %s" % str(e))
            self.app.ui.update_location_labels(dx=None, dy=None, x=None, y=None)
            self.app.plotcanvas.on_update_text_hud('0.0', '0.0', '0.0', '0.0')
            return

        self.cursor_pos = pos
        if not preview_active(self.measuring, multipoint, len(self.points)):
            return
        try:
            if multipoint is False:
                if len(self.points) == 1:
                    # update angle
                    angle_val = self.update_angle(dx=dx, dy=dy)
                    self.display_angle(angle_val)
                    # update end_point
                    end_val = self.update_end_point(pos=pos)
                    self.display_end(end_val)
                    # update deltas
                    deltax, deltay = self.update_deltas(pos=pos)
                    self.display_deltas(deltax, deltay)
                    # update distance
                    dist_val = self.update_distance(pos=pos)
                    self.display_distance(dist_val)
                    DistanceUI.display_feedback(self.ui, dist_val, len(self.points), self.decimals)
            else:
                segment = self.update_distance(pos, prev_pos=self.points[-1])
                self.display_distance(self.total_distance + segment)
                self.display_end(self.update_end_point(pos))
                DistanceUI.display_feedback(self.ui, segment, len(self.points), self.decimals)
            # dimension graphics: committed segments + the live preview segment
            self.redraw_dimensions()
        except Exception as e:
            self.app.log.error("Distance.on_mouse_move() update --> %s" % str(e))
            self.app.ui.update_location_labels(None, None, None, None)

    def update_angle(self, dx, dy):
        try:
            angle = measurement_angle(dx, dy)
        except Exception as e:
            self.app.log.error("Distance.update_angle() -> %s" % str(e))
            return None
        return angle

    def display_angle(self, val):
        DistanceUI.display_angle(self.ui, val, self.app.dec_format, self.decimals)

    def display_start(self, val):
        DistanceUI.display_start(self.ui, val)

    def update_end_point(self, pos):
        # update the end point value
        return DistanceUI.format_point(pos, self.app.dec_format, self.decimals)

    def display_end(self, val):
        DistanceUI.display_end(self.ui, val)

    def update_deltas(self, pos):
        return point_deltas(pos, self.points[0])

    def display_deltas(self, dx, dy):
        DistanceUI.display_deltas(self.ui, dx, dy, self.app.dec_format, self.decimals)

    def update_distance(self, pos, prev_pos=None):
        if prev_pos is None:
            prev_pos = self.points[0]
        return point_distance(pos, prev_pos)

    def display_distance(self, val):
        DistanceUI.display_distance(self.ui, val, self.decimals)

    def on_plugin_cleanup(self):
        self.on_exit()
        if self.dims is not None:
            self.dims.delete()
            self.dims = None

    def hideEvent(self, event):
        super().hideEvent(event)
        # takeWidget() also runs while refreshing this plugin. Check ownership
        # after the replacement has been installed, not in the middle of that swap.
        QtCore.QTimer.singleShot(0, self.on_plugin_hidden)

    def close_plugin_tab(self):
        """Close the Plugin tab when it shows this plugin. The result fields keep their values for the next start."""
        ui = self.app.ui
        if ui.plugin_scroll_area.widget() is not self:
            return
        idx = ui.notebook.indexOf(ui.plugin_tab)
        if idx < 0:
            return
        # the Properties tab: in an editor it holds the editor UI
        ui.notebook.setCurrentWidget(ui.properties_tab)
        ui.notebook.removeTab(idx)
        # with no objects in the project the notebook has nothing to show
        if not self.app.collection.get_list():
            ui.splitter.setSizes([0, 1])

    def on_plugin_hidden(self):
        if self.active and self.app.ui.plugin_scroll_area.widget() is not self:
            self.on_exit()
