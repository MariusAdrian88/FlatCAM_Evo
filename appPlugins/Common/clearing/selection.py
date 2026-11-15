# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The canvas selection of the NCC and Paint plugins: the zones of an Area Selection, the exclusion zones and the
polygons of a Polygon Selection (click), drawn and picked with the mouse on the canvas.

``AreaSelector`` is held by the controller (composition, not a base class). A selection takes the canvas from the app
handlers, shows the temporary shapes, and ends in one of 2 ways, both with the same cleanup: the handlers of the app are
connected again, the panel is enabled, the temporary shapes and the half-made input are gone. The commit callback runs
after the cleanup and only for a selection that holds something.

Reference Object needs no canvas input: the controller resolves the object of the combo box itself.
"""

import logging

import numpy as np
import shapely
from PyQt6 import QtCore, QtGui
from shapely import Polygon

import gettext
import builtins
import appTranslation as fcTranslate

from defaults import AppDefaults
from appPlugins.Common.clearing import regions

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

AREA = 'area'
EXCLUSION = 'exclusion'
CLICK = 'click'

ESCAPE_KEYS = (QtCore.Qt.Key.Key_Escape, 'Escape')
ENTER_KEYS = (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter, 'Enter', 'Return')


def key_of(event):
    """The key of a key event: an int / Qt key (from the app), a QKeyEvent (from the GUI) or a VisPy KeyEvent."""
    if isinstance(event, (int, QtCore.Qt.Key)):
        return event
    if isinstance(event, QtGui.QKeyEvent):
        return event.key()
    return event.key


class AreaSelector:
    """
    The modes:

    - ``area``: zones drawn as squares (2 clicks) or polygons (a click for each point, a right click closes one).
      A right click ends the selection; ``commit(zones)``.
    - ``exclusion``: the same drawing; ``commit(zones)``.
    - ``click``: a left click adds or removes the face (copper-free region, polygon) under the cursor; a right click or
      Enter ends the selection; ``commit(points, faces)`` with the clicked points and the picked faces.

    Escape cancels any mode without a commit.
    """

    def __init__(self, tool):
        self.tool = tool
        self.mode = None
        self.commit = None
        self.reset_state()

    @property
    def app(self):
        return self.tool.app

    @property
    def active(self):
        return self.mode is not None

    def reset_state(self):
        """Forget the input of a selection: the clicks, the points, the zones, the picked faces."""
        self.shape = 'square'
        self.first_click = False
        self.cursor_pos = None
        self.dragging = False
        self.vertices = []
        self.poly_drawn = False
        self.zones = []
        self.faces = []
        self.tolerance = None
        self.points = []
        self.picked = {}        # clicked point -> the face it picked
        self.shapes = {}
        self.snap_was_on = False

    # ------------------------------------------------------------------------------------------------------------
    # start / end
    # ------------------------------------------------------------------------------------------------------------
    def start(self, mode, commit, faces=None, tolerance=None):
        """
        Take the canvas for a selection; a selection that is running is cancelled first.

        :param mode:        'area', 'exclusion' or 'click'
        :param commit:      called with the result of a complete selection
        :param faces:       click: the polygons a click can pick
        :param tolerance:   click: the drawing tolerance of the picked shapes
        """
        self.cancel()
        self.mode = mode
        self.commit = commit
        app = self.app
        canvas = app.plotcanvas
        if mode == CLICK:
            self.faces = list(faces or [])
            self.tolerance = tolerance
            app.inform.emit('[WARNING_NOTCL] %s' % _("Click a polygon to add or remove it, right click to start."))
            # polygons are hard to click with the Grid snapping on
            if app.ui.grid_snap_btn.isChecked():
                self.snap_was_on = True
                app.ui.grid_snap_btn.trigger()
            canvas.graph_event_disconnect('mouse_release', app.on_mouse_click_release_over_plot)
            canvas.graph_event_disconnect('mouse_press', app.on_mouse_click_over_plot)
            canvas.graph_event_connect('mouse_release', self.on_click_release)
            canvas.graph_event_connect('key_press', self.on_key_press)
        else:
            radio = self.tool.ui.excl_shape_radio if mode == EXCLUSION else self.tool.ui.area_shape_radio
            self.shape = radio.get_value()
            app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))
            canvas.graph_event_disconnect('mouse_press', app.on_mouse_click_over_plot)
            canvas.graph_event_disconnect('mouse_move', app.on_mouse_move_over_plot)
            canvas.graph_event_disconnect('mouse_release', app.on_mouse_click_release_over_plot)
            canvas.graph_event_connect('mouse_release', self.on_mouse_release)
            canvas.graph_event_connect('mouse_move', self.on_mouse_move)
            canvas.graph_event_connect('key_press', self.on_key_press)
        app.ui.notebook.setDisabled(True)

    def cancel(self):
        """End the selection without a commit (Escape, a Reset, the plugin closing): the canvas is the app's again."""
        was_active = self.active
        if was_active:
            self.release_canvas()
        self.mode = None
        self.commit = None
        self.reset_state()
        if was_active:
            self.tool.delete_moving_selection_shape()
            self.tool.delete_tool_selection_shape()

    def release_canvas(self):
        app = self.app
        canvas = app.plotcanvas
        try:
            if self.mode == CLICK:
                canvas.graph_event_disconnect('mouse_release', self.on_click_release)
                canvas.graph_event_disconnect('key_press', self.on_key_press)
                # restore the Grid snapping if it was active before
                if self.snap_was_on:
                    app.ui.grid_snap_btn.trigger()
            else:
                canvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
                canvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
                canvas.graph_event_disconnect('key_press', self.on_key_press)
        except Exception as e:
            log.error("AreaSelector.release_canvas() --> %s" % str(e))
        if self.mode != CLICK:
            canvas.graph_event_connect('mouse_move', app.on_mouse_move_over_plot)
        canvas.graph_event_connect('mouse_press', app.on_mouse_click_over_plot)
        canvas.graph_event_connect('mouse_release', app.on_mouse_click_release_over_plot)
        app.ui.notebook.setDisabled(False)

    def finish(self):
        """End the selection and hand its result to the commit callback, if it holds something."""
        commit = self.commit
        if self.mode == CLICK:
            points = list(self.points)
            faces = [self.picked[p] for p in points]
            result = (points, faces) if points else None
        else:
            result = (list(self.zones),) if self.zones else None
        self.cancel()
        if result is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No area selected."))
            return
        commit(*result)

    # ------------------------------------------------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------------------------------------------------
    def position(self, event):
        """The position of a mouse event in the plot units (snapped to the grid when it is on), None if it has none."""
        try:
            x = float(event.pos[0])
            y = float(event.pos[1])
        except (TypeError, IndexError, ValueError):
            return None
        pos = self.app.plotcanvas.translate_coords((x, y))
        if self.app.grid_status():
            pos = self.app.geo_editor.snap(pos[0], pos[1])
        return pos[0], pos[1]

    def on_key_press(self, event):
        key = key_of(event)
        if key in ESCAPE_KEYS:
            self.cancel()
        elif self.mode == CLICK and key in ENTER_KEYS:
            self.finish()

    def on_mouse_release(self, event):
        """Area / exclusion zones: the clicks of a square or the points of a polygon; a right click ends."""
        pos = self.position(event)
        if pos is None:
            return
        x1, y1 = pos
        app = self.app
        if event.button == 1:
            if self.shape == 'square':
                if not self.first_click:
                    self.first_click = True
                    app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the area."))
                    self.cursor_pos = pos
                else:
                    app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    app.delete_selection_shape()
                    x0, y0 = self.cursor_pos
                    self.zones.append(Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
                    # add a temporary shape on canvas
                    self.tool.draw_tool_selection_shape(old_coords=(x0, y0), coords=(x1, y1))
                    self.first_click = False
            else:
                self.vertices.append(pos)
                if len(self.vertices) > 1:
                    self.poly_drawn = True
                    app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))
        elif event.button == 2 and not self.dragging:
            if self.shape == 'square':
                self.first_click = False
            elif self.poly_drawn:
                # add the point where we last clicked if it is not already there
                if pos != self.vertices[-1]:
                    self.vertices.append(pos)
                # a Polygon needs at least 3 points
                if len(self.vertices) > 2:
                    self.tool.delete_moving_selection_shape()
                    polygon = Polygon(self.vertices)
                    # do not add invalid polygons even if they are drawn by utility geometry
                    if polygon.is_valid:
                        self.zones.append(polygon)
                        self.tool.draw_selection_shape_polygon(points=self.vertices)
                        app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    else:
                        app.inform.emit('[WARNING_NOTCL] %s' % _("The polygon crosses itself. The zone was not added."))
                self.vertices = []
                self.poly_drawn = False
                return
            self.finish()

    def on_mouse_move(self, event):
        """Area / exclusion zones: the cursor, the status bar and the rubber band of the zone being drawn."""
        app = self.app
        self.dragging = bool(event.is_dragging)
        pos = self.position(event)
        if pos is None:
            return
        if app.grid_status():
            app.app_cursor.set_data(
                np.asarray([pos]), symbol='++', edge_color=app.plotcanvas.cursor_color,
                edge_width=self.option("global_cursor_width"), size=self.option("global_cursor_size"))

        if self.cursor_pos is None:
            self.cursor_pos = (0, 0)
        app.dx = pos[0] - float(self.cursor_pos[0])
        app.dy = pos[1] - float(self.cursor_pos[1])
        app.ui.update_location_labels(app.dx, app.dy, pos[0], pos[1])
        app.plotcanvas.on_update_text_hud(app.dx, app.dy, pos[0], pos[1])

        # draw the utility geometry
        if self.shape == 'square':
            if self.first_click:
                app.draw_moving_selection_shape(old_coords=(self.cursor_pos[0], self.cursor_pos[1]), coords=pos)
        else:
            self.tool.draw_moving_selection_shape_poly(points=self.vertices, data=pos)

    def on_click_release(self, event):
        """Polygon Selection: a left click adds / removes the face under the click, a right click ends."""
        app = self.app
        if event.button == 1:
            pos = self.position_without_snap(event)
            if pos is None:
                return
            face = regions.face_at(self.faces, pos)
            if face is None:
                app.inform.emit(_("No polygon detected under click position."))
                return
            known = [point for point in self.points if self.picked[point] is face]
            for point in known:
                self.points.remove(point)
                self.picked.pop(point)
                app.tool_shapes.remove(self.shapes.pop(point))
            if known:
                app.inform.emit(_("Removed polygon. Click to add / remove the next one or right click to start."))
            else:
                color = self.option('global_sel_draw_color') + 'AF'
                extra = {} if self.tolerance is None else {'tolerance': self.tolerance}
                point = (pos[0], pos[1])
                self.points.append(point)
                self.picked[point] = face
                self.shapes[point] = app.tool_shapes.add(
                    layer=0, shape=face, color=color, face_color=color, visible=True, **extra)
                app.inform.emit('%s: %d. %s' % (
                    _("Added polygon"), len(self.points), _("Click to add the next one or right click to start.")))
            app.tool_shapes.redraw()
        elif event.button == 2 and app.event_is_dragging is False:
            self.finish()

    def position_without_snap(self, event):
        """The position of a click in the plot units: a click picks a polygon, so the Grid snapping is not applied."""
        try:
            x = float(event.pos[0])
            y = float(event.pos[1])
        except (TypeError, IndexError, ValueError):
            return None
        return self.app.plotcanvas.translate_coords((x, y))

    def option(self, key):
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        return self.app.options.get(key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))
