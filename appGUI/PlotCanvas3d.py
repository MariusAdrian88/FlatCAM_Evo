# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# Author: Dennis Hayrullin (c)                             #
# Date: 2016                                               #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtGui import QFont, QFontMetricsF, QPalette
# from PyQt6.QtCore import QSettings

import time
from itertools import product

import vispy.scene as scene
from vispy.scene.cameras.base_camera import BaseCamera
from vispy.util import keys
from vispy.util.quaternion import Quaternion
from vispy.color import Color
from vispy.visuals.markers import MarkersVisual
from appGUI.VisPyVisuals import ShapeGroup, ShapeCollection, TextCollection, TextGroup, Cursor, GridLines
from appGUI.ViewCube import ViewCube
from vispy.scene.visuals import InfiniteLine, Line, Rectangle, Text, XYZAxis
from appDatabase import _database_option

import gettext
import appTranslation as fcTranslate
import builtins

import numpy as np
from vispy.geometry import Rect

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

white = Color("#ffffff")
black = Color("#000000")


class PlotCanvas3d(QtCore.QObject, scene.SceneCanvas):
    """
    Class handling the plotting area in the application.
    """

    def __init__(self, container, fcapp, camera_reference='center_rotate'):
        """
        The constructor configures the VisPy figure that
        will contain all plots, creates the base axes and connects
        events to the plotting area.

        :param container: The parent container in which to draw plots.
        :param camera_reference: 'lmb' rotates about the press point; 'center' uses all loaded objects;
                                 'center_rotate' adds trackball rotation about that center.
        :rtype: PlotCanvas
        """

        # super(PlotCanvas, self).__init__()
        # QtCore.QObject.__init__(self)
        # VisPyCanvas.__init__(self)
        super().__init__()

        # VisPyCanvas does not allow new attributes. Override.
        self.unfreeze()

        # Text uses logical pixels; Qt already handles the device pixel ratio.
        self.dpi = self.native.logicalDpiY()

        self.fcapp = fcapp

        # Parent container
        self.container = container

        settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("theme"):
            theme = settings.value('theme', type=str)
        else:
            theme = 'default'

        if settings.contains("dark_canvas"):
            dark_canvas = settings.value('dark_canvas', type=bool)
        else:
            dark_canvas = False

        if settings.contains("axis_font_size"):
            a_fsize = settings.value('axis_font_size', type=int)
        else:
            a_fsize = 8

        if (theme == 'default' or theme == 'light') and not dark_canvas:
            theme_color = Color('#FFFFFF')
            tick_color = Color('#000000')
            window_color = QPalette().color(QPalette.ColorRole.Window)
            back_color = str(window_color.name())
            if window_color.lightness() < 128:
                # a dark window (Qt could not follow the theme): black ticks and numbers would not be readable
                tick_color = Color('gray')
            grid_color = 'dimgray'
        else:
            if theme not in ['default', 'light']:
                theme_color = Color('#202124')
                back_color = Color('#202124')
            else:
                theme_color = Color('#000000')
                back_color = Color('#000000')
            tick_color = Color('gray')
            grid_color = '#dededeff'

        self.central_widget.bgcolor = back_color
        self.central_widget.border_color = back_color
        self.line_color = tick_color.rgba

        self.grid_widget = self.central_widget.add_grid()
        self.grid_widget.spacing = 0

        self.view = self.grid_widget.add_view(row=0, col=0, border_color=tick_color, bgcolor=theme_color)
        self.view.camera = Camera_3D(fov=0)
        self.view.camera.set_state(_quaternion=Quaternion.create_from_axis_angle(-90, 1, 0, 0, degrees=True))
        self.axis_padding = max(100, 8 * a_fsize)

        units_per_mm = 1 / 25.4 if self.fcapp.app_units.upper() == 'IN' else 1
        self.workspace_size = (400 * units_per_mm, 400 * units_per_mm)
        self.view.camera.minimum_scene_size = 10 * units_per_mm / 3
        size = self.workspace_size[0]

        self.grid = GridLines(
            parent=self.view.scene, color=grid_color, grid_bounds=(0, size, 0, size), border_width=0
        )
        # The grid's transparent screen quad must not occlude the toolpath.
        self.grid.set_gl_state('translucent', depth_test=False, cull_face=False)
        self.workspace_line = Line(
            pos=np.array([(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0), (0, 0, 0)]),
            color=tick_color, width=1.5, parent=self.view.scene
        )

        self.xaxis = scene.Axis(
            pos=[[0, 0], [size, 0]], tick_direction=(0, -1), domain=(0, size),
            font_size=a_fsize, axis_color='red', tick_color=tick_color, text_color=tick_color,
            axis_label='X (%s)' % self.fcapp.app_units.lower(), parent=self.view.scene
        )
        self.yaxis = scene.Axis(
            pos=[[0, 0], [0, size]], tick_direction=(-1, 0), domain=(0, size),
            font_size=a_fsize, axis_color='green', tick_color=tick_color, text_color=tick_color,
            axis_label='Y (%s)' % self.fcapp.app_units.lower(), parent=self.view.scene
        )
        self._update_axis_label_margins()
        self.xyz_axis = XYZAxis(parent=self.view.scene)
        self.xyz_axis.transform = scene.STTransform(scale=(size / 10,) * 3)

        # <VisPyCanvas>
        self.create_native()
        self.native.setParent(self.fcapp.ui)
        # the canvas is never mirrored: X grows to the right in a right-to-left application too
        self.native.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)

        # <QtCore.QObject>
        self.container.addWidget(self.native)

        self.line_parent = None
        if _database_option(self.fcapp, "global_cursor_color_enabled"):
            c_color = Color(_database_option(self.fcapp, "global_cursor_color")).rgba
        else:
            c_color = self.line_color

        # font size
        q_settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("hud_font_size"):
            fsize = q_settings.value('hud_font_size', type=int)
        else:
            fsize = 8

        # units
        # units = self.fcapp.app_units.upper()

        # coordinates and anchors
        height = fsize * 11     # 90. Constant 11 is something that works
        width = height * 2      # width is double the height = it is something that works
        # center_x = (width / 2) + 5
        # center_y = (height / 2) + 5

        # enable Grid lines
        self.grid_lines_enabled = True

        self.shape_collections = []
        # A material preview can fit its own bounds instead of the machine workspace.
        self.content_bounds = None

        # deferred: the redraws of a burst of object plots are joined into one rebuild
        self.shape_collection = self.new_shape_collection(
            deferred=_database_option(self.fcapp, 'global_joined_redraws') is not False)

        self.text_collection = self.new_text_collection()
        self.text_collection.enabled = True

        self.c = None
        self.view_cube = ViewCube(self.view.camera, self.native)
        self.view_cube.fit_requested.connect(self.fit_view)
        self.view_cube.rotation_requested.connect(lambda dx, dy: self.view.camera.rotate_about(
            (dx, dy), self.geometry_center() if self.camera_reference != 'lmb' else
            (np.mean(self.content_bounds, axis=0) if self.content_bounds is not None else
                       (self.workspace_size[0] / 2, self.workspace_size[1] / 2, 0))
        ))
        self.view.events.resize.connect(self.on_view_resize)
        self.context_menu = self.create_context_menu()
        self.graph_event_connect('mouse_release', self.on_mouse_release)
        self._camera_reference = 'lmb'
        self.camera_reference = camera_reference

        # Keep VisPy canvas happy by letting it be "frozen" again.
        self.freeze()
        self.fit_view()

        # self.graph_event_connect('mouse_wheel', self.on_mouse_scroll)

    def create_context_menu(self):
        """Build the default menu; override to supply different actions."""
        menu = QtWidgets.QMenu(self.native)
        menu.addAction(_("Toggle Grid"), self.toggle_grid)
        menu.addAction(_("Fit View"), self.on_fit_view)
        return menu

    def on_fit_view(self):
        sender = getattr(self.fcapp, 'grbl_sender_tool', None)
        view = getattr(sender, 'view3d', None)
        if self.content_bounds is None and view is not None and view.canvas is self:
            sender.on_fit_view()
        else:
            self.view.camera.set_view('iso')
            self.fit_view()

    def set_context_menu(self, menu):
        """Replace the current QMenu."""
        self.context_menu = menu

    def add_context_menu_action(self, text, callback, icon=None):
        """Add an action with an optional QIcon and return its QAction."""
        action = self.context_menu.addAction(text)
        if icon is not None:
            action.setIcon(icon)
        action.triggered.connect(callback)
        return action

    def show_context_menu(self, pos):
        """Show the menu at a QPoint in native widget coordinates."""
        self.context_menu.popup(self.native.mapToGlobal(pos))

    def on_mouse_release(self, event):
        if event.button != 2 or event.handled:
            return
        if event.press_event is not None and any(
                np.linalg.norm(item.pos - event.press_event.pos) >= QtWidgets.QApplication.startDragDistance()
                for item in event.drag_events()):
            return
        self.show_context_menu(QtCore.QPoint(int(event.pos[0]), int(event.pos[1])))

    def toggle_grid(self):
        """Toggle grid visibility independently of grid snapping."""
        self.grid.visible = not self.grid.visible
        self.grid_lines_enabled = self.grid.visible
        self.update()

    def graph_event_connect(self, event_name, callback):
        return getattr(self.events, event_name).connect(callback)

    def graph_event_disconnect(self, event_name, callback=None):
        if callback is None:
            getattr(self.events, event_name).disconnect()
        else:
            getattr(self.events, event_name).disconnect(callback)

    def translate_coords(self, pos):
        """
        Translate pixels to FlatCAM units.

        """
        tr = self.grid.get_transform('canvas', 'visual')
        return tr.map(pos)

    def pixel_size(self):
        """
        Plot units per screen pixel.
        """
        a = self.translate_coords((0, 0))
        b = self.translate_coords((1, 0))
        return float(np.hypot(b[0] - a[0], b[1] - a[1]))

    def translate_coords_2(self, pos):
        """
        Translate FlatCAM units to pixels.
        """
        tr = self.grid.get_transform('visual', 'document')
        return tr.map(pos)

    def zoom(self, factor, center=None):
        """
        Zooms the plot by factor around a given
        center point. Takes care of re-drawing.

        :param factor: Number by which to scale the plot.
        :type factor: float
        :param center: Coordinates [x, y] of the point around which to scale the plot.
        :type center: list
        :return: None
        """
        self.view.camera.zoom(factor, center)

    def new_shape_group(self, shape_collection=None):
        if shape_collection:
            return ShapeGroup(shape_collection)
        return ShapeGroup(self.shape_collection)

    def new_shape_collection(self, **kwargs):
        # sc = ShapeCollection(parent=self.view.scene, pool=self.app.pool, **kwargs)
        # self.shape_collections.append(sc)
        # return sc
        return ShapeCollection(parent=self.view.scene, pool=self.fcapp.pool, **kwargs)

    def new_cursor(self):
        """
        Will create a mouse cursor pointer on canvas

        :return: the mouse cursor object
        """
        self.c = Cursor(pos=np.empty((0, 2)), parent=self.view.scene)
        self.c.antialias = 0
        return self.c

    def on_mouse_scroll(self, event):
        # key modifiers
        modifiers = event.modifiers

        pan_delta_x = _database_option(self.fcapp, "global_gridx")
        pan_delta_y = _database_option(self.fcapp, "global_gridy")
        curr_pos = event.pos

        # Controlled pan by mouse wheel
        if 'Shift' in modifiers:
            p1 = np.array(curr_pos)[:2]

            if event.delta[1] > 0:
                curr_pos[0] -= pan_delta_x
            else:
                curr_pos[0] += pan_delta_x
            p2 = np.array(curr_pos)[:2]
            self.view.camera.pan(p2 - p1)
        elif 'Control' in modifiers:
            p1 = np.array(curr_pos)[:2]

            if event.delta[1] > 0:
                curr_pos[1] += pan_delta_y
            else:
                curr_pos[1] -= pan_delta_y
            p2 = np.array(curr_pos)[:2]
            self.view.camera.pan(p2 - p1)

        # if self.fcapp.grid_status():
        #     pos_canvas = self.translate_coords(curr_pos)
        #     pos = self.fcapp.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        #
        #     # Update cursor
        #     self.fcapp.app_cursor.set_data(np.asarray([(pos[0], pos[1])]),
        #                                    symbol='++', edge_color=self.fcapp.cursor_color_3D,
        #                                    edge_width=self.fcapp.options["global_cursor_width"],
        #                                    size=self.fcapp.options["global_cursor_size"])

    def new_text_group(self, collection=None):
        if collection:
            return TextGroup(collection)
        else:
            return TextGroup(self.text_collection)

    def new_text_collection(self, **kwargs):
        return TextCollection(parent=self.view.scene, **kwargs)

    def get_grid_size(self):
        """Return the grid's (X, Y) dimensions in millimetres."""
        factor = 25.4 if self.fcapp.app_units.upper() == 'IN' else 1
        return tuple(size * factor for size in self.workspace_size)

    def _update_axis_label_margins(self):
        """Keep axis titles just beyond the numeric labels, in logical pixels."""
        for axis in (self.xaxis, self.yaxis):
            font = QFont(axis._text.face)
            font.setPointSizeF(max(1, axis.tick_font_size))
            metrics = QFontMetricsF(font, self.native)
            labels = axis.ticker._get_tick_frac_labels()[2]
            bounds = [metrics.tightBoundingRect(label) for label in labels]
            if axis is self.xaxis:
                extent = max((rect.height() for rect in bounds), default=0)
            else:
                extent = max((rect.width() for rect in bounds), default=0)
            title_height = metrics.tightBoundingRect(axis.axis_label).height()
            gap = max(4, axis.tick_font_size * self.dpi / 72 * 0.25)
            axis.axis_label_margin = axis.tick_label_margin + extent + title_height / 2 + gap

    def set_grid_size(self, x_mm, y_mm=None):
        """Resize immediately in millimetres; omitted Y defaults to X."""
        x_mm = float(x_mm)
        y_mm = x_mm if y_mm is None else float(y_mm)
        if not np.isfinite((x_mm, y_mm)).all() or min(x_mm, y_mm) <= 0:
            raise ValueError('Grid dimensions must be positive, finite numbers of millimetres.')
        factor = 1 / 25.4 if self.fcapp.app_units.upper() == 'IN' else 1
        x, y = x_mm * factor, y_mm * factor
        self.workspace_size = (x, y)
        self.grid.grid_bounds = (0, x, 0, y)
        self.workspace_line.set_data(pos=np.array([
            (0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, 0)
        ]))
        self.xaxis.pos = np.array([(0, 0), (x, 0)])
        self.yaxis.pos = np.array([(0, 0), (0, y)])
        self.xaxis.domain = (0, x)
        self.yaxis.domain = (0, y)
        self._update_axis_label_margins()
        self.xyz_axis.transform = scene.STTransform(scale=(min(x, y) / 10,) * 3)
        self.fit_view()
        self.update()

    @property
    def camera_reference(self):
        return self._camera_reference

    @camera_reference.setter
    def camera_reference(self, value):
        value = value.lower()
        if value not in ('lmb', 'center', 'center_rotate'):
            raise ValueError('Camera reference must be lmb, center or center_rotate.')
        self._camera_reference = value
        self.view.camera.rotation_center = self.geometry_center if value != 'lmb' else None
        self.view.camera._trackball_rotation = value == 'center_rotate'
        self.view.camera._rotation_anchor = None
        self.view.camera._event_value = None

    def geometry_center(self):
        center = (np.mean(self.content_bounds, axis=0) if self.content_bounds is not None else
                  np.array(self.view.camera.center, dtype=float))
        collection = getattr(self.fcapp, 'collection', None)
        get_bounds = getattr(collection, 'get_bounds', None)
        if get_bounds is not None:
            bounds = np.asarray(get_bounds(), dtype=float)
            if bounds.shape == (4,) and np.isfinite(bounds).all():
                center[:2] = (bounds[:2] + bounds[2:]) / 2
                if self.content_bounds is None:
                    center[2] = 0
        return center

    def _scene_corners(self, rect=None):
        if rect is not None:
            return np.array(list(product((rect.left, rect.right), (rect.bottom, rect.top), (0,))))
        if self.content_bounds is not None:
            return np.array(list(product(*zip(*self.content_bounds))))
        bounds = np.array([(0, 0, 0), (*self.workspace_size, 0)], dtype=float)
        # the bounds need the buffers of the deferred redraws
        self.shape_collection.flush()
        nodes = list(self.view.scene.children)
        while nodes:
            node = nodes.pop()
            if not node.visible:
                continue
            nodes.extend(node.children)
            if not hasattr(node, 'bounds'):
                continue
            # A VisPy Markers visual without data has no bounds and raises in bounds().
            if isinstance(node, MarkersVisual) and node._data is None:
                continue
            # VisPy Line bounds reduce positions without checking for an empty array.
            if isinstance(node, Line) and node.pos is not None and len(node.pos) == 0:
                continue
            # VisPy caches bounds separately from live visual data.
            node._bounds_changed()
            limits = [node.bounds(axis) for axis in range(3)]
            if all(limit is None for limit in limits):
                continue
            corners = np.array(list(product(*(limit if limit is not None else (0, 0) for limit in limits))))
            points = node.node_transform(self.view.scene).map(corners)
            points = points[:, :3] / points[:, 3:]
            points = points[np.isfinite(points).all(axis=1)]
            if len(points):
                bounds[0] = np.minimum(bounds[0], points.min(axis=0))
                bounds[1] = np.maximum(bounds[1], points.max(axis=0))
        return np.array(list(product(*bounds.T)))

    def fit_view(self, rect=None, points=None, margin=None):
        fit_bounds = rect is not None or points is not None
        width, height = self.view.size
        padding = self.axis_padding
        left, bottom = padding, padding
        right, top = 20, 20
        if margin is not None:
            left = right = width * margin
            bottom = top = height * margin
        if width <= left + right or height <= bottom + top:
            return
        camera = self.view.camera
        if points is None:
            points = self._scene_corners(rect)
        else:
            points = np.asarray(points)
            points = points[np.isfinite(points).all(axis=1)]
            if not len(points):
                return
            points = np.array(list(product(*zip(points.min(axis=0), points.max(axis=0)))))
        points = camera.transform.imap(points)
        points = points[:, :3] / points[:, 3:]
        lower, upper = points.min(axis=0), points.max(axis=0)
        units_per_pixel = max(
            (upper[0] - lower[0]) / (width - left - right),
            (upper[1] - lower[1]) / (height - bottom - top),
            camera.minimum_scene_size / min(width, height)
        )
        center = (lower + upper) / 2
        center[:2] += np.array((right - left, top - bottom)) * units_per_pixel / 2
        center = camera.transform.map(center)
        camera.center = center[:3] / center[3]
        camera.fitted_scene_size = units_per_pixel * min(width, height)
        camera.scale_factor = camera.fitted_scene_size
        camera.maximum_scene_size = camera.fitted_scene_size
        if fit_bounds:
            # Fitting a toolpath must still allow zooming out to the full workspace.
            scene_points = camera.transform.imap(self._scene_corners())
            scene_points = scene_points[:, :3] / scene_points[:, 3:]
            scene_size = np.ptp(scene_points, axis=0)
            camera.maximum_scene_size = max(
                camera.scale_factor,
                scene_size[0] / max(1, width - padding - 20) * min(width, height),
                scene_size[1] / max(1, height - padding - 20) * min(width, height)
            )
        camera.maximum_scene_size *= 10
        camera._view_modified = False

    def on_view_resize(self, event):
        camera = self.view.camera
        center = camera.center
        zoom = camera.scale_factor / camera.fitted_scene_size
        modified = camera._view_modified
        self.fit_view()
        if modified:
            camera.zoom(zoom)
            camera.center = center
            camera._view_modified = True

    def fit_center(self, loc, rect=None):
        if rect is not None:
            self.fit_view(rect)
        self.view.camera.center = (loc[0], loc[1], 0)

    def clear(self):
        pass

    def redraw(self):
        self.shape_collection.redraw([])
        self.shape_collection.flush()
        self.text_collection.redraw()


class Camera_3D(scene.ArcballCamera):

    def __init__(self, **kwargs):
        super(Camera_3D, self).__init__(**kwargs)

        self.minimum_scene_size = 0.01
        self.maximum_scene_size = 10000
        self.fitted_scene_size = self.maximum_scene_size

        self.last_event = None
        self.last_time = 0
        self._view_modified = False
        self._rotation_anchor = None
        self.rotation_center = None
        self._trackball_rotation = False

        # Default mouse button for panning is RMB
        self.pan_button_setting = "2"

    def set_view(self, name):
        views = {
            'top': (-90, 1, 0, 0), 'bottom': (90, 1, 0, 0),
            'left': (-90, 0, 1, 0), 'right': (90, 0, 1, 0),
            'front': (0, 1, 0, 0), 'back': (180, 0, 1, 0),
            'iso': (-np.degrees(np.arctan(1 / np.sqrt(2))), 1, 0, 0),
        }
        if name not in views:
            raise ValueError('Unknown camera view: %s' % name)
        self._event_value = None
        self._rotation_anchor = None
        self._view_modified = True
        rotation = Quaternion.create_from_axis_angle(*views[name], degrees=True)
        if name == 'iso':
            rotation = rotation * Quaternion.create_from_axis_angle(45, 0, 1, 0, degrees=True)
        self.set_state(fov=0, _quaternion=rotation)

    def zoom(self, factor, center=None):
        factor = float(factor)
        if not np.isfinite(factor) or factor <= 0:
            return
        scale = np.clip(self.scale_factor * factor, self.minimum_scene_size, self.maximum_scene_size)
        if scale == self.scale_factor:
            return
        factor = scale / self.scale_factor
        if center is not None:
            anchor = np.zeros(3)
            anchor[:min(len(center), 3)] = center[:3]
            self.center = anchor + (np.asarray(self.center) - anchor) * factor
        if self._distance is not None:
            self.distance *= factor
        self.scale_factor = scale
        self._view_modified = True

    def _screen_to_world(self, pos, reference=None):
        reference = self.center if reference is None else reference
        depth = self._scene_transform.map((*reference, 1))
        point = self._scene_transform.imap((*pos[:2], depth[2] / depth[3], 1))
        return point[:3] / point[3]

    def _update_rotation(self, event):
        pos = np.asarray(event.pos[:2], dtype=float)
        start = event.mouse_event.press_event.pos
        previous = start if self._event_value is None else self._event_value
        delta = (pos - previous) * np.pi / max(1, min(self._viewbox.size))
        self._event_value = pos
        if not np.any(delta):
            return
        if self._rotation_anchor is None:
            if self.rotation_center is not None:
                self._rotation_anchor = self.rotation_center()
            else:
                ray = self._scene_transform.imap([(start[0], start[1], 0, 1), (start[0], start[1], 1, 1)])
                ray = ray[:, :3] / ray[:, 3:]
                direction = ray[1] - ray[0]
                if abs(direction[2]) > np.linalg.norm(direction) * 1e-8:
                    self._rotation_anchor = ray[0] - direction * ray[0, 2] / direction[2]
                else:
                    # Edge-on views have no unique intersection with the grid plane.
                    self._rotation_anchor = self._screen_to_world(start)
        if self._trackball_rotation:
            anchor = self._rotation_anchor
            projected = self._scene_transform.map((*anchor, 1))
            screen = projected[:2] / projected[3]
            self._event_value = previous
            super()._update_rotation(event)
            self._view_modified = True
            self.center = np.asarray(self.center) + anchor - self._screen_to_world(screen, anchor)
        else:
            self.rotate_about(delta, self._rotation_anchor)

    def rotate_about(self, delta, anchor):
        """Rotate by screen-space angles while keeping a world-space pivot fixed on screen."""
        projected = self._scene_transform.map((*anchor, 1))
        pos = projected[:2] / projected[3]
        horizontal = Quaternion.create_from_axis_angle(-delta[0], 0, 1, 0)
        vertical = Quaternion.create_from_axis_angle(-delta[1], 1, 0, 0)
        self._quaternion = vertical * horizontal * self._quaternion
        self._view_modified = True
        self.view_changed()
        self.center = np.asarray(self.center) + anchor - self._screen_to_world(pos, anchor)

    # def viewbox_mouse_event(self, event):
    #     """
    #     The SubScene received a mouse event; update transform
    #     accordingly.
    #
    #     Parameters
    #     ----------
    #     event : instance of Event
    #         The event.
    #     """
    #     if event.handled or not self.interactive:
    #         return
    #
    #     # key modifiers
    #     modifiers = event.mouse_event.modifiers
    #
    #     # Limit mouse move events
    #     last_event = event.last_event
    #     t = time.time()
    #     if t - self.last_time > 0.015:
    #         self.last_time = t
    #         if self.last_event:
    #             last_event = self.last_event
    #             self.last_event = None
    #     else:
    #         if not self.last_event:
    #             self.last_event = last_event
    #         event.handled = True
    #         return
    #
    #     # ################### Scrolling ##########################
    #     BaseCamera.viewbox_mouse_event(self, event)
    #
    #     if event.type == 'mouse_wheel':
    #         if not modifiers:
    #             center = self._scene_transform.imap(event.pos)
    #             scale = (1 + self.zoom_factor) ** (-event.delta[1] * 30)
    #             self.limited_zoom(scale, center)
    #         event.handled = True
    #
    #     elif event.type == 'mouse_move':
    #         if event.press_event is None:
    #             return
    #
    #         # ################ Panning ############################
    #         # self.pan_button_setting is actually self.FlatCAM.APP.defaults['global_pan_button']
    #         if event.button == int(self.pan_button_setting) and not modifiers:
    #             # Translate
    #             p1 = np.array(last_event.pos)[:2]
    #             p2 = np.array(event.pos)[:2]
    #             p1s = self._transform.imap(p1)
    #             p2s = self._transform.imap(p2)
    #             self.pan(p1s-p2s)
    #             event.handled = True
    #         elif event.button in [2, 3] and 'Shift' in modifiers:
    #             # Zoom
    #             p1c = np.array(last_event.pos)[:2]
    #             p2c = np.array(event.pos)[:2]
    #             scale = ((1 + self.zoom_factor) **
    #                      ((p1c-p2c) * np.array([1, -1])))
    #             center = self._transform.imap(event.press_event.pos[:2])
    #             self.limited_zoom(scale, center)
    #             event.handled = True
    #         else:
    #             event.handled = False
    #     elif event.type == 'mouse_press':
    #         # accept the event if it is button 1 or 2.
    #         # This is required in order to receive future events
    #         event.handled = event.button in [1, 2, 3]
    #     else:
    #         event.handled = False

    def viewbox_mouse_event(self, event):
        """
        The viewbox received a mouse event; update transform
        accordingly.

        Parameters
        ----------
        event : instance of Event
            The event.
        """
        if event.handled or not self.interactive:
            return

        BaseCamera.viewbox_mouse_event(self, event)

        if event.type == 'mouse_wheel':
            modifiers = event.mouse_event.modifiers
            fine = tuple(modifiers) == (keys.SHIFT,)
            if not modifiers or fine:
                # Shift: 10 times finer steps; some platforms send the Shift wheel as a horizontal delta
                step = event.delta[1] if event.delta[1] or not fine else event.delta[0]
                center = self._screen_to_world(event.pos)
                scale = (1 + self.zoom_factor) ** (-step * (3 if fine else 30))
                self.limited_zoom(scale, center)
            event.handled = True
            return
        elif event.type == 'gesture_zoom':
            self.zoom(1 - event.scale)
            event.handled = True
            return

        if event.type == 'mouse_release':
            self._event_value = None  # Reset
            self._rotation_anchor = None
        elif event.type == 'mouse_press':
            self._event_value = None
            self._rotation_anchor = None
            event.handled = True
        elif event.type == 'mouse_move':
            if event.press_event is None:
                return

            modifiers = event.mouse_event.modifiers
            p1 = event.mouse_event.press_event.pos
            p2 = event.mouse_event.pos
            d = p2 - p1

            if 1 in event.buttons and not modifiers:
                # Rotate
                self._update_rotation(event)

            elif (1 in event.buttons or int(self.pan_button_setting) in event.buttons) and keys.SHIFT in modifiers:
                # Zoom
                if self._event_value is None:
                    self._event_value = (self._scale_factor, self._distance)
                zoomy = (1 + self.zoom_factor) ** d[1]

                self.zoom(self._event_value[0] * zoomy / self.scale_factor)

            elif int(self.pan_button_setting) in event.buttons and not modifiers:
                # Translate
                norm = np.mean(self._viewbox.size)
                if self._event_value is None or len(self._event_value) == 2:
                    self._event_value = self.center
                dist = (p1 - p2) / norm * self._scale_factor
                dist[1] *= -1
                # Black magic part 1: turn 2D into 3D translations
                dx, dy, dz = self._dist_to_trans(dist)
                # Black magic part 2: take up-vector and flipping into account
                ff = self._flip_factors
                up, forward, right = self._get_dim_vectors()
                dx, dy, dz = right * dx + forward * dy + up * dz
                dx, dy, dz = ff[0] * dx, ff[1] * dy, dz * ff[2]
                c = self._event_value
                self.center = c[0] + dx, c[1] + dy, c[2] + dz
                self._view_modified = True

    def limited_zoom(self, scale, center):
        self.zoom(scale, center)
