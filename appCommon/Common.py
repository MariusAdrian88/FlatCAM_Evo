# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File Modified (major mod): Marius Adrian Stanciu         #
# Date: 11/4/2019                                          #
# ##########################################################
from PyQt6 import QtCore

from math import sqrt

from shapely import Polygon, Point, LineString, get_coordinates, prepare
from shapely.ops import unary_union
from appGUI.VisPyVisuals import ShapeCollection

from appTool import AppTool

from copy import deepcopy
import collections
from datetime import datetime
from heapq import heappop, heappush
import numpy as np
# from voronoi import Voronoi
# from voronoi import Polygon as voronoi_polygon

import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class GracefulException(Exception):
    """
    Graceful Exception raised when the user is requesting to cancel the current threaded task
    """

    def __init__(self):
        super().__init__()

    def __str__(self):
        return '\n\n%s' % _("The user requested a graceful exit of the current task.")


class LoudDict(dict):
    """
    A Dictionary with a callback for item changes.
    """

    def __init__(self, *args, **kwargs):
        dict.__init__(self, *args, **kwargs)
        self.callback = lambda x: None

    def __setitem__(self, key, value):
        """
        Overridden __setitem__ method. Will emit 'changed(QString)' if the item was changed, with key as parameter.
        """
        if key in self and self.__getitem__(key) == value:
            return

        dict.__setitem__(self, key, value)
        self.callback(key)

    def update(self, *args, **kwargs):
        if len(args) > 1:
            raise TypeError("update expected at most 1 arguments, got %d" % len(args))
        other = dict(*args, **kwargs)
        for key in other:
            self[key] = other[key]

    def set_change_callback(self, callback):
        """
        Assigns a function as callback on item change. The callback
        will receive the key of the object that was changed.

        :param callback: Function to call on item change.
        :type callback: func
        :return: None
        """

        self.callback = callback


class LoudUniqueList(list, collections.abc.MutableSequence):
    """
    A List with a callback for item changes, callback which returns the index where the items are added/modified.
    A List that will allow adding only items that are not in the list.
    """

    def __init__(self, arg=None):
        super().__init__()
        self.callback = lambda x: None

        if arg is not None:
            if isinstance(arg, list):
                self.extend(arg)
            else:
                self.extend([arg])

    def insert(self, i, v):
        if v in self:
            raise ValueError("One of the added items is already in the list.")
        self.callback(i)
        return super().insert(i, v)

    def append(self, v):
        if v in self:
            raise ValueError("One of the added items is already in the list.")
        le = len(self)
        self.callback(le)
        return super().append(v)

    def extend(self, t):
        for v in t:
            if v in self:
                raise ValueError("One of the added items is already in the list.")
        le = len(self)
        self.callback(le)
        return super().extend(t)

    def __add__(self, t):  # This is for something like `LoudUniqueList([1, 2, 3]) + list([4, 5, 6])`...
        for v in t:
            if v in self:
                raise ValueError("One of the added items is already in the list.")
        le = len(self)
        self.callback(le)
        return super().__add__(t)

    def __iadd__(self, t):  # This is for something like `l = LoudUniqueList(); l += [1, 2, 3]`
        for v in t:
            if v in self:
                raise ValueError("One of the added items is already in the list.")
        le = len(self)
        self.callback(le)
        return super().__iadd__(t)

    def __setitem__(self, i, v):
        try:
            for v1 in v:
                if v1 in self:
                    raise ValueError("One of the modified items is already in the list.")
        except TypeError:
            if v in self:
                raise ValueError("One of the modified items is already in the list.")
        if v is not None:
            self.callback(i)
        return super().__setitem__(i, v)

    def set_callback(self, callback):
        """
        Assigns a function as callback on item change. The callback
        will receive the index of the object that was changed.

        :param callback: Function to call on item change.
        :type callback: func
        :return: None
        """

        self.callback = callback


class FCSignal:
    """
    Taken from here: https://blog.abstractfactory.io/dynamic-signals-in-pyqt/
    """

    def __init__(self):
        self.__subscribers = []

    def emit(self, *args, **kwargs):
        for subs in self.__subscribers:
            subs(*args, **kwargs)

    def connect(self, func):
        self.__subscribers.append(func)

    def disconnect(self, func):
        try:
            self.__subscribers.remove(func)
        except ValueError:
            print('Warning: function %s not removed '
                  'from signal %s' % (func, self))


def color_variant(hex_color, bright_factor=1):
    """
    Takes a color in HEX format #FF00FF and produces a lighter or darker variant

    :param hex_color:           color to change
    :type hex_color:            str
    :param bright_factor:       factor to change the color brightness [0 ... 1]
    :type bright_factor:        float
    :return:                    Modified color
    :rtype:                     str
    """

    if len(hex_color) != 7:
        print("Color is %s, but needs to be in #FF00FF format. Returning original color." % hex_color)
        return hex_color

    if bright_factor > 1.0:
        bright_factor = 1.0
    if bright_factor < 0.0:
        bright_factor = 0.0

    rgb_hex = [hex_color[x:x + 2] for x in [1, 3, 5]]
    new_rgb = []
    for hex_value in rgb_hex:
        # adjust each color channel and turn it into a INT suitable as argument for hex()
        mod_color = round(int(hex_value, 16) * bright_factor)
        # make sure that each color channel has two digits without the 0x prefix
        mod_color_hex = str(hex(mod_color)[2:]).zfill(2)
        new_rgb.append(mod_color_hex)

    return "#" + "".join([i for i in new_rgb])


class ExclusionAreas(QtCore.QObject):
    """
    Functionality for adding Exclusion Areas for the Excellon and Geometry FlatCAM Objects
    """
    e_shape_modified = QtCore.pyqtSignal()

    def __init__(self, app):
        """
        A class that create interdiction areas that need to be avoided by GCode

        :param app: The main application
        """
        super().__init__()

        self.app = app

        self.app.log.debug("+ Adding Exclusion Areas")
        # Storage for shapes, storage that can be used by FlatCAm tools for utility geometry
        # VisPy visuals
        try:
            self.exclusion_shapes = self.app.plotcanvas.new_shape_collection(layers=1)
        except AttributeError:
            self.exclusion_shapes = None

        # variables to be used in area exclusion
        self.cursor_pos = (0, 0)
        self.first_click = False
        self.points = []
        self.poly_drawn = False

        '''
        Here we store the exclusion shapes and some other information's
        Each list element is a dictionary with the format:
        
        {
            "obj_type":   string ("excellon" or "geometry")   <- self.obj_type
            "shape":      Shapely polygon
            "strategy":   string ("over" or "around")         <- self.strategy_button
            "overz":      float                               <- self.over_z_button
        }
        '''
        self.exclusion_areas_storage = []
        self._travel_cache = None

        self.mouse_is_dragging = False

        self.solid_geometry = []
        self.obj_type = None
        self.owner = None

        self.shape_type_button = None
        self.over_z_button = None
        self.strategy_button = None
        self.cnc_button = None

    def on_add_area_click(self, shape_button, overz_button, strategy_radio, cnc_button, solid_geo, obj_type,
                          owner=None):
        """

        :param shape_button:    a FCButton that has the value for the shape
        :param overz_button:    a FCDoubleSpinner that holds the Over Z value
        :param strategy_radio:  a RadioSet button with the strategy_button value
        :param cnc_button:      a FCButton in Object UI that when clicked the CNCJob is created
                                We have a reference here so we can change the color signifying that exclusion areas are
                                available.
        :param solid_geo:       reference to the object solid geometry for which we add exclusion areas
        :param obj_type:        Type of FlatCAM object that called this method. String: "excellon" or "geometry"
        :type obj_type:         str
        :param owner:           the plugin that draws the areas ('drilling'), stored in each new area; None for the
                                plugins that use every area (Milling)
        :return:                None
        """
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the start point of the area."))
        self.app.call_source = 'geometry'

        self.shape_type_button = shape_button

        self.over_z_button = overz_button
        self.strategy_button = strategy_radio
        self.cnc_button = cnc_button

        self.solid_geometry = solid_geo
        self.obj_type = obj_type
        self.owner = owner

        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_release)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)
        # self.kp = self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)

    # To be called after clicking on the plot.
    def on_mouse_release(self, event):
        """
        Called on mouse click release.

        :param event:   Mouse event
        :type event:
        :return:        None
        :rtype:
        """
        event_pos = event.pos
        # event_is_dragging = event.is_dragging
        right_button = 2

        event_pos = self.app.plotcanvas.translate_coords(event_pos)
        if self.app.grid_status():
            curr_pos = self.app.geo_editor.snap(event_pos[0], event_pos[1])
        else:
            curr_pos = (event_pos[0], event_pos[1])

        x1, y1 = curr_pos[0], curr_pos[1]

        # shape_type_button = self.ui.area_shape_radio.get_value()

        # do clear area only for left mouse clicks
        if event.button == 1:
            if self.shape_type_button.get_value() == "square":
                if self.first_click is False:
                    self.first_click = True
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("Click the end point of the area."))

                    # event_pos is already in model coordinates (translated above)
                    self.cursor_pos = (event_pos[0], event_pos[1])
                    if self.app.grid_status():
                        self.cursor_pos = self.app.geo_editor.snap(event_pos[0], event_pos[1])
                else:
                    self.app.inform.emit(_("Zone added. Click to start adding next zone or right click to finish."))
                    self.app.delete_selection_shape()

                    x0, y0 = self.cursor_pos[0], self.cursor_pos[1]

                    pt1 = (x0, y0)
                    pt2 = (x1, y0)
                    pt3 = (x1, y1)
                    pt4 = (x0, y1)

                    new_rectangle = Polygon([pt1, pt2, pt3, pt4])

                    # {
                    #     "obj_type":   string("excellon" or "geometry") < - self.obj_type
                    #     "shape":      Shapely polygon
                    #     "strategy_button":   string("over" or "around") < - self.strategy_button
                    #     "overz":      float < - self.over_z_button
                    # }
                    new_el = {
                        "idx":      self.next_idx(),
                        "obj_type": self.obj_type,
                        "shape":    new_rectangle,
                        "strategy": self.strategy_button.get_value(),
                        "overz":    self.over_z_button.get_value(),
                        "owner":    self.owner
                    }
                    self.exclusion_areas_storage.append(new_el)

                    if self.obj_type == 'excellon':
                        color = "#FF7400"
                        face_color = "#FF7400BF"
                    else:
                        color = "#098a8f"
                        face_color = "#FF7400BF"

                    # add a temporary shape on canvas
                    AppTool.draw_tool_selection_shape(
                        self, old_coords=(x0, y0), coords=(x1, y1),
                        color=color,
                        face_color=face_color,
                        shapes_storage=self.exclusion_shapes)

                    self.first_click = False
                    return
            else:
                self.points.append((x1, y1))

                if len(self.points) > 1:
                    self.poly_drawn = True
                    self.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))

                return ""
        elif event.button == right_button and self.mouse_is_dragging is False:

            shape_type = self.shape_type_button.get_value()

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
                        AppTool.delete_moving_selection_shape(self)
                        pol = Polygon(self.points)
                        # do not add invalid polygons even if they are drawn by utility geometry
                        if pol.is_valid:
                            """
                            {
                                "obj_type":   string("excellon" or "geometry") < - self.obj_type
                                "shape":      Shapely polygon
                                "strategy":   string("over" or "around") < - self.strategy_button
                                "overz":      float < - self.over_z_button
                            }
                            """
                            new_el = {
                                "idx":      self.next_idx(),
                                "obj_type": self.obj_type,
                                "shape":    pol,
                                "strategy": self.strategy_button.get_value(),
                                "overz":    self.over_z_button.get_value(),
                                "owner":    self.owner
                            }
                            self.exclusion_areas_storage.append(new_el)
        
                            if self.obj_type == 'excellon':
                                color = "#FF7400"
                                face_color = "#FF7400BF"
                            else:
                                color = "#098a8f"
                                face_color = "#FF7400BF"

                            AppTool.draw_selection_shape_polygon(
                                self, points=self.points,
                                color=color,
                                face_color=face_color,
                                shapes_storage=self.exclusion_shapes)
                            self.app.inform.emit(
                                _("Zone added. Click to start adding next zone or right click to finish."))

                    self.points = []
                    self.poly_drawn = False
                    return

            # AppTool.delete_tool_selection_shape(self, shapes_storage=self.exclusion_shapes)

            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
            self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            # self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)

            self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
            self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)

            self.app.call_source = 'app'

            if len(self.exclusion_areas_storage) == 0:
                return

            # since the exclusion areas should apply to all objects in the app collection, this check is limited to
            # only the current object therefore it will not guarantee success
            self.app.inform.emit("%s" % _("Exclusion areas added. Checking overlap with the object geometry ..."))

            for el in self.exclusion_areas_storage:
                if el["shape"].intersects(unary_union(self.solid_geometry)):
                    self.on_clear_area_click()
                    self.app.inform.emit(
                        "[ERROR_NOTCL] %s" % _("Failed. Exclusion areas intersects the object geometry ..."))
                    return

            self.app.inform.emit("[success] %s" % _("Exclusion areas added."))
            self.cnc_button.bold = True
            self.cnc_button.color = 'orange'
            self.cnc_button.setToolTip(
                '%s %s' % (_("Generate the CNC Job object."), _("With Exclusion areas."))
            )

            self.e_shape_modified.emit()

    def area_disconnect(self):
        """
        Will do the cleanup (Escape while drawing). Will disconnect the mouse events for the custom handlers in this
        class and drop the area that is being drawn. The areas added before stay: they are drawn on the canvas and
        the CNC jobs use them.

        :return:    None
        :rtype:
        """
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_release)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)

        self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.points = []
        self.poly_drawn = False
        # a rectangle started but not finished: the next area starts with a new first corner
        self.first_click = False

        AppTool.delete_moving_selection_shape(self)
        # AppTool.delete_tool_selection_shape(self, shapes_storage=self.exclusion_shapes)

        self.app.call_source = "app"
        self.app.inform.emit("[WARNING_NOTCL] %s" % _("Cancelled. Area exclusion drawing was interrupted."))
        # the exclusion tables of the plugins show the areas added before Escape
        self.e_shape_modified.emit()

    def on_mouse_move(self, event):
        """
        Called on mouse move

        :param event:   mouse event
        :type event:
        :return:        None
        :rtype:
        """
        shape_type = self.shape_type_button.get_value()

        event_pos = event.pos
        event_is_dragging = event.is_dragging
        # right_button = 2

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
                                         symbol='++', edge_color=self.app.cursor_color_3D,
                                         edge_width=self.app.options.get("global_cursor_width", self.app.defaults.get("global_cursor_width")),
                                         size=self.app.options.get("global_cursor_size", self.app.defaults.get("global_cursor_size")))

        # update the positions on status bar
        if self.cursor_pos is None:
            self.cursor_pos = (0, 0)

        self.app.dx = curr_pos[0] - float(self.cursor_pos[0])
        self.app.dy = curr_pos[1] - float(self.cursor_pos[1])
        # self.app.ui.position_label.setText("&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
        #                                    "<b>Y</b>: %.4f&nbsp;" % (curr_pos[0], curr_pos[1]))
        # self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
        #                                        "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (self.app.dx, self.app.dy))
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])

        units = self.app.app_units.lower()
        # self.app.plotcanvas.text_hud.text = \
        #     'Dx:\t{:<.4f} [{:s}]\nDy:\t{:<.4f} [{:s}]\n\nX:  \t{:<.4f} [{:s}]\nY:  \t{:<.4f} [{:s}]'.format(
        #         self.app.dx, units, self.app.dy, units, curr_pos[0], units, curr_pos[1], units)
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, curr_pos[0], curr_pos[1])

        if self.obj_type == 'excellon':
            color = "#FF7400"
            face_color = "#FF7400BF"
        else:
            color = "#098a8f"
            face_color = "#FF7400BF"

        # draw the utility geometry
        if shape_type == "square":
            if self.first_click:
                self.app.draw_moving_selection_shape(old_coords=(self.cursor_pos[0], self.cursor_pos[1]),
                                                     color=color,
                                                     face_color=face_color,
                                                     coords=(curr_pos[0], curr_pos[1]))
        else:
            AppTool.draw_moving_selection_shape_poly(
                self, points=self.points,
                color=color,
                face_color=face_color,
                data=(curr_pos[0], curr_pos[1]))

    def on_clear_area_click(self):
        """
        Slot for clicking the button for Deleting all the Exclusion areas.

        :return:    None
        :rtype:
        """
        self.clear_shapes()

        # restore the default StyleSheet
        self.cnc_button.setStyleSheet("")
        # update the StyleSheet
        self.cnc_button.bold = True
        self.cnc_button.setToolTip('%s' % _("Generate the CNC Job object."))

    def clear_shapes(self):
        """
        Will delete all the Exclusion areas; will delete on canvas any possible selection box for the Exclusion areas.

        :return:    None
        :rtype:
        """
        if self.exclusion_areas_storage:
            self.app.inform.emit('%s' % _("All exclusion zones deleted."))
        self.exclusion_areas_storage.clear()
        AppTool.delete_moving_selection_shape(self)
        self.app.delete_selection_shape()
        AppTool.delete_tool_selection_shape(self, shapes_storage=self.exclusion_shapes)

    def delete_sel_shapes(self, idxs):
        """

        :param idxs:    list of indexes in self.exclusion_areas_storage list to be deleted
        :type idxs:     list
        :return:        None
        """

        # delete all plotted shapes
        AppTool.delete_tool_selection_shape(self, shapes_storage=self.exclusion_shapes)

        # delete shapes
        for idx in sorted(idxs, reverse=True):
            del self.exclusion_areas_storage[idx]
        self.renumber()

        # re-add what's left after deletion in first step
        if self.obj_type == 'excellon':
            color = "#FF7400"
            face_color = "#FF7400BF"
        else:
            color = "#098a8f"
            face_color = "#FF7400BF"

        face_alpha = 0.3
        color_t = face_color[:-2] + str(hex(int(face_alpha * 255)))[2:]

        for geo_el in self.exclusion_areas_storage:
            if isinstance(geo_el['shape'], Polygon):
                self.exclusion_shapes.add(
                    geo_el['shape'], color=color, face_color=color_t, update=True, layer=0, tolerance=None)
        self.exclusion_shapes.redraw()

        # if there are still some exclusion areas in the storage
        if self.exclusion_areas_storage:
            self.app.inform.emit('[success] %s' % _("Selected exclusion zones deleted."))
        else:
            # restore the default StyleSheet
            self.cnc_button.setStyleSheet("")
            # update the StyleSheet
            self.cnc_button.bold = True
            self.cnc_button.setToolTip('%s' % _("Generate the CNC Job object."))

            # there are no more exclusion areas in the storage, all have been selected and deleted
            self.app.inform.emit('%s' % _("All exclusion zones deleted."))

    def next_idx(self):
        """The idx for a new area: one more than the highest in use, so it is never a duplicate."""
        return max((area.get('idx', 0) for area in self.exclusion_areas_storage), default=0) + 1

    def renumber(self):
        """idx = the row number (1..n) shown in the exclusion table, in storage order."""
        for number, area in enumerate(self.exclusion_areas_storage, start=1):
            area['idx'] = number

    def areas_of(self, owner=None):
        """The stored areas of one owner ('drilling' when drawn in the Drilling plugin); None = all the areas."""
        if owner is None:
            return self.exclusion_areas_storage
        return [area for area in self.exclusion_areas_storage if area.get('owner') == owner]

    def _travel_cache_for(self, tooldia, owner=None):
        """
        The grown shapes, the union of the 'around' ones and the visibility memo of its vertices, for the current areas
        (of this owner) and tool diameter. The key holds the shapes themselves (Shapely shapes are immutable), so any
        add, delete, clear or strategy edit gives a new key and the cache is rebuilt on the next call.
        """
        areas = self.areas_of(owner)
        key = (tooldia, owner, self.app.app_units, tuple((id(area['shape']), area['strategy']) for area in areas))
        cache = getattr(self, '_travel_cache', None)
        if cache is not None and cache['key'] == key:
            return cache
        grown = [self.grown_shape(area['shape'], tooldia) for area in areas]
        around = unary_union([shape for shape, area in zip(grown, areas) if area['strategy'] == 'around'])
        prepare(around)
        for shape in grown:
            prepare(shape)
        cache = {
            'key': key,
            # keeps the shapes alive, so an id() in the key is never reused by another shape
            'shapes': [area['shape'] for area in areas],
            'areas': list(areas),
            'grown': grown,
            'around': around,
            'boundary': list(dict.fromkeys(tuple(xy) for xy in get_coordinates(around.boundary))),
            'visible': {},
        }
        self._travel_cache = cache
        return cache

    def grown_shape(self, shape, tooldia):
        """
        An Exclusion area grown by the half of the tool diameter and a little more. The travel lines go over or around
        the grown area and no drill point is accepted inside it.

        :param shape:           the shape of an Exclusion area
        :type shape:            Polygon
        :param tooldia:         the tool diameter
        :type tooldia:          float
        :return:                the grown shape
        :rtype:                 Polygon
        """
        # add a little something to the half diameter, to make sure that we really don't enter the exclusion zones
        buffered_distance = (tooldia / 2.0) + (0.1 if self.app.app_units == 'MM' else 0.00393701)
        return shape.buffer(buffered_distance, join_style="mitre")

    @staticmethod
    def _crossing_around(cache, line):
        around = cache['around']
        if not around.intersects(line):
            return None
        return line_crossing(line, around)

    def travel_coordinates(self, start_point, end_point, tooldia, owner=None, travel_z=None):
        """
        WIll create a path the go around the exclusion areas on the shortest path when travelling (at a Z above the
        material).

        :param start_point:     X,Y coordinates for the start point of the travel line
        :type start_point:      tuple
        :param end_point:       X,Y coordinates for the destination point of the travel line
        :type end_point:        tuple
        :param tooldia:         THe tool diameter used and which generates the travel lines
        :type tooldia           float
        :param owner:           only the areas of this owner ('drilling'); None: all the areas (Milling)
        :param travel_z:        the travel Z of the job: the tool never goes over an 'over' area below it (an Over Z
                                lower than the travel Z is replaced by the travel Z); None: the Over Z of the areas
        :type travel_z:         float
        :return:                A list of x,y tuples that describe the avoiding path
        :rtype:                 list
        :raises ValueError:     when no path around the grown 'around' areas is possible
        """

        # Travel lines: rapids. Should not pass through Exclusion areas
        travel_line = LineString([start_point, end_point])

        cache = self._travel_cache_for(tooldia, owner)
        areas = cache['areas']
        grown_shapes = cache['grown']
        around = cache['around']

        # Plan XY against all 'around' areas together. The union includes the boundary intersections of overlapping
        # areas, and each candidate leg is checked against the whole union, not only the area being detoured.
        route = [tuple(start_point), tuple(end_point)]
        if self._crossing_around(cache, travel_line) is not None:
            if around.contains(Point(start_point)) or around.contains(Point(end_point)):
                raise ValueError(_("Failed. The tool cannot go around an exclusion area: "
                                   "a travel starts or ends too close to the area. "
                                   "Use the 'Over' strategy or change the area."))

            vertices = list(dict.fromkeys(route + cache['boundary']))
            visible = cache['visible']
            distances = {0: 0.0}
            previous = {}
            pending = [(0.0, 0)]
            visited = set()
            while pending:
                distance, idx = heappop(pending)
                if idx in visited:
                    continue
                visited.add(idx)
                if idx == 1:
                    break
                origin = vertices[idx]
                for next_idx, vertex in enumerate(vertices):
                    if next_idx in visited:
                        continue
                    dx, dy = vertex[0] - origin[0], vertex[1] - origin[1]
                    next_distance = distance + sqrt(dx * dx + dy * dy)
                    if next_distance >= distances.get(next_idx, float('inf')):
                        continue
                    # the visibility of a pair of vertices does not depend on the travel: remember it
                    key = (origin, vertex)
                    clear = visible.get(key)
                    if clear is None:
                        clear = visible[key] = self._crossing_around(cache, LineString([origin, vertex])) is None
                    if clear:
                        distances[next_idx] = next_distance
                        previous[next_idx] = idx
                        heappush(pending, (next_distance, next_idx))
            if 1 not in visited:
                raise ValueError(_("Failed. The tool cannot go around the exclusion areas. Change the areas."))
            route = [vertices[1]]
            idx = 1
            while idx != 0:
                idx = previous[idx]
                route.append(vertices[idx])
            route.reverse()

        # Collect the 'over' intervals along every leg of the complete XY route, including the detour legs.
        intervals = []
        route_points = []
        offset = 0.0
        for first, last in zip(route, route[1:]):
            leg = LineString([first, last])
            for area, grown_shape in zip(areas, grown_shapes):
                if area['strategy'] == 'around' or not grown_shape.intersects(leg):
                    continue
                crossing = line_crossing(leg, grown_shape)
                if crossing is not None:
                    entry_distance, entry_pt, exit_pt = crossing
                    over_z = float(area['overz'])
                    if travel_z is not None:
                        # going 'over' an area never takes the tool down: below the travel Z it stays at the travel Z
                        over_z = max(over_z, float(travel_z))
                    intervals.append([offset + entry_distance, offset + leg.project(exit_pt), over_z,
                                      (entry_pt.x, entry_pt.y), (exit_pt.x, exit_pt.y)])
            offset += leg.length
            route_points.append((offset, last))

        # Stay at the highest Over Z for an entire connected group of intervals. In particular, never lower inside
        # another area or reach a higher area before raising. Disjoint groups still lower to the normal travel Z.
        merged = []
        for interval in sorted(intervals, key=lambda item: item[0]):
            if merged and interval[0] <= merged[-1][1]:
                current = merged[-1]
                current[2] = max(current[2], interval[2])
                if interval[1] > current[1]:
                    current[1], current[4] = interval[1], interval[4]
            else:
                merged.append(interval)

        waypoints = {}
        for distance, point in route_points[:-1]:
            height = next((item[2] for item in merged if item[0] <= distance < item[1]), None)
            waypoints[distance] = [height, point]
        for entry, exit_distance, height, entry_xy, exit_xy in merged:
            waypoints[entry] = [height, entry_xy]
            waypoints[exit_distance] = [None, exit_xy]
        ret_list = [waypoints[distance] for distance in sorted(waypoints)]
        ret_list.append([None, end_point])
        return ret_list


class AppLogging:
    def __init__(self, app, log_level):
        self.app = app

        self._log = logging.getLogger('base')
        self._log.setLevel(logging.DEBUG)
        # log.setLevel(logging.WARNING)
        formatter = logging.Formatter('[%(levelname)s][%(threadName)s] %(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        self._log.addHandler(handler)

        self._log_level = log_level

    @property
    def log_level(self):
        return self._log_level

    @log_level.setter
    def log_level(self, val):
        self._log_level = val if val in [0, 1, 2] else 0

    def info(self, msg):
        if self._log_level == 0:
            return
        # current date now
        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        self._log.info(msg=msg)
        self.app.inform_shell.emit('[log]INFO %s ***\t%s' % (date, msg))

    def debug(self, msg):
        if self._log_level == 0:
            return
        # current date now
        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        self._log.debug(msg=msg)
        self.app.inform_shell.emit('[log]DEBUG %s ***\t%s' % (date, msg))

    def warning(self, msg):
        if self._log_level == 0:
            return
        # current date now
        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        self._log.warning(msg=msg)
        self.app.inform_shell.emit('[log]WARNING %s ***\t%s' % (date, msg))

    def error(self, msg):
        if self._log_level == 0:
            return
        # current date now
        date = str(datetime.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        self._log.error(msg=msg)
        self.app.inform_shell.emit('[log]ERROR %s ***\t%s' % (date, msg))


def farthest_point(origin, points_list):
    """
    Calculate the farthest Point in a list from another Point

    :param origin:      Reference Point
    :type origin:       Point
    :param points_list: List of Points or a MultiPoint
    :type points_list:  list
    :return:            Farthest Point
    :rtype:             Point
    """
    old_dist = 0
    fartherst_pt = None

    for pt in points_list:
        dist = abs(origin.distance(pt))
        if dist >= old_dist:
            fartherst_pt = pt
            old_dist = dist

    return fartherst_pt


# def voronoi_diagram(geom, envelope, edges=False):
#     """
#
#     :param geom:        a collection of Shapely Points from which to build the Voronoi diagram
#     :type geom:          MultiPoint
#     :param envelope:    a bounding box to constrain the diagram (Shapely Polygon)
#     :type envelope:     Polygon
#     :param edges:       If False, return regions as polygons. Else, return only
#                         edges e.g. LineStrings.
#     :type edges:        bool, False
#     :return:
#     :rtype:
#     """
#
#     if not isinstance(geom, MultiPoint):
#         return False
#
#     coords = list(envelope.exterior.coords)
#     v_poly = voronoi_polygon(coords)
#
#     vp = Voronoi(v_poly)
#
#     points = []
#     for pt in geom:
#         points.append((pt.x, pt.y))
#     vp.create_diagram(points=points, vis_steps=False, verbose=False, vis_result=False, vis_tree=False)
#
#     if edges is True:
#         return vp.edges
#     else:
#         voronoi_polygons = []
#         for pt in vp.points:
#             try:
#                 poly_coords = list(pt.get_coordinates())
#                 new_poly_coords = []
#                 for coord in poly_coords:
#                     new_poly_coords.append((coord.x, coord.y))
#
#                 voronoi_polygons.append(Polygon(new_poly_coords))
#             except Exception:
#                 print(traceback.format_exc())
#
#         return voronoi_polygons

def nearest_point(origin, points_list):
    """
    Calculate the nearest Point in a list from another Point

    :param origin:      Reference Point
    :type origin:       Point
    :param points_list: List of Points or a MultiPoint
    :type points_list:  list
    :return:            Nearest Point
    :rtype:             Point
    """
    old_dist = np.inf
    nearest_pt = None

    for pt in points_list:
        dist = abs(origin.distance(pt))
        if dist <= old_dist:
            nearest_pt = pt
            old_dist = dist

    return nearest_pt


def line_crossing(line, shape):
    """
    Find where a line (a travel line) goes through a polygon (a grown Exclusion area)

    :param line:        the line
    :type line:         LineString
    :param shape:       the polygon
    :type shape:        Polygon
    :return:            None if the line does not go through the polygon (it misses it, only touches it or only runs
                        along its outline), else a tuple (entry distance, entry Point, exit Point) with the first and
                        the last point of the line inside the polygon; the entry distance is measured along the line
    :rtype:             tuple
    """
    # the interior of the line meets the interior of the polygon; this is also True when the start or the end point
    # (or both) is inside the polygon and the line meets the outline only once or not at all
    if line.length == 0 or not line.relate_pattern(shape, 'T********'):
        return None

    inside = line.intersection(shape)
    # a line that only touches a corner of the polygon can meet its interior by a rounding error
    if inside.length < 1e-9:
        return None

    # the line can enter the polygon more than once; the result is then a MultiLineString or a GeometryCollection,
    # which are not iterable in Shapely 2, therefore use the coordinates
    inside_points = [Point(xy) for xy in get_coordinates(inside)]
    distances = [line.project(pt) for pt in inside_points]
    entry_idx = int(np.argmin(distances))
    exit_idx = int(np.argmax(distances))
    return distances[entry_idx], inside_points[entry_idx], inside_points[exit_idx]
