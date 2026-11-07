# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The probe points, the height map and the levelling of G-code, shared by the
Levelling Plugin (MACH3, MACH4, LinuxCNC) and the GRBL Sender Plugin.

The class that uses LevellingCoreMixin must have: self.app, self.ui (with the
widgets made by build_probe_points_ui()), self.units, self.decimals,
self.drawing_tolerance (from CNCjob), and the 4 hooks at the end of the class.
"""

import contextlib
import math
import random
import sys
from copy import deepcopy

from PyQt6 import QtWidgets, QtCore, QtGui
from shapely import Point, MultiPoint, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.affinity import translate
import numpy as np

import gettext
import appTranslation as fcTranslate
import builtins

from appObjects.AppObjectTemplate import ObjectDeleted
from appGUI.VisPyVisuals import ShapeCollection
from appGUI.GUIElements import FCFileSaveDialog
from defaults import AppDefaults

from appPlugins.Common.levelling_interp import (
    build_bilinear_grid,
    bilinear_offset,
    bicubic_offset,
    bicubic_supported,
    nearest_offset,
    conservative_ceiling,
    match_nearest_point,
    match_tolerance,
    new_levelling_state,
    level_gcode_line,
    parse_height_map_line,
)

try:
    from shapely.ops import voronoi_diagram
    VORONOI_ENABLED = True
except Exception:
    VORONOI_ENABLED = False

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# maximum number of steps per direction when moving a probe point away from an Excellon hole
MAX_AVOID_TRIES = 10000


def _iter_polygons(geometry):
    """
    Return a flat list of the Polygons in `geometry`. `geometry` can be None,
    a Polygon, a Multi* geometry, a GeometryCollection or a list of these.
    Other geometry types (lines, points) are ignored.
    """
    if geometry is None:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (list, tuple)):
        parts = geometry
    elif hasattr(geometry, 'geoms'):
        parts = geometry.geoms
    else:
        return []
    result = []
    for part in parts:
        result.extend(_iter_polygons(part))
    return result


class _AlContext:
    """
    What a probing or import result belongs to: the generation of the probe
    points/replacement attempt, the owner object (name and identity) and the
    data units. Matching tolerance is captured before scheduling the worker.
    """

    def __init__(self, generation, target_name, target_obj, units, tolerance):
        self.generation = generation
        self.target_name = target_name
        self.target_obj = target_obj
        self.units = units
        self.tolerance = tolerance


class LevellingCoreMixin:

    def _al_init_state(self):
        """Set the probe-point state. Call it from __init__ of the tool."""
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        # updated in the self.set_tool_ui()
        self.form_fields = {}

        self.first_click = False
        self.cursor_pos = None

        # if mouse is dragging set the object True
        self.mouse_is_dragging = False

        # if mouse events are bound to local methods
        self.mouse_events_connected = False

        self.probing_gcode_text = ''

        # True only right after a successful probing pass (_on_al_result_ready())
        # or a successful height-map import; False whenever probe points are
        # (re)created or cleared, since their 'height' is then just a placeholder.
        self.al_heights_valid = False

        # busy guard for apply_autolevel() (creates the new '_levelled' CNCJob object)
        self._al_apply_running = False

        # changes each time the probe points change or a replacement attempt
        # starts; a result of an older generation is discarded
        self._al_generation = 0
        # coordinate/height units of the point set; None when no data exists
        self._al_data_units = None
        # the object that the probe points belong to (name and the object itself)
        self._al_target_name = None
        self._al_target_obj = None

        '''
        dictionary of dictionaries to store the information's for the autolevelling
        format when using Voronoi diagram:
        {
            id: {
                    'point': Shapely Point
                    'geo': Shapely Polygon from Voronoi diagram,
                    'height': float
                }
        }
        '''
        self.al_voronoi_geo_storage = {}

        '''
        list of (x, y, x) tuples to store the information's for the autolevelling
        format when using bilinear interpolation:
        [(x0, y0, z0), (x1, y1, z1), ...]
        '''
        self.al_bilinear_geo_storage = []

        # working state used by autolevell_gcode() / autolevell_gcode_line()
        # while levelling a CNCJob's G-code: the (x, y, height) points used
        # for interpolation, the bilinear grid built out of them (if any),
        # the offset function currently selected, and the coordinate
        # decimals used to format the new Z values
        self._al_points = []
        self._al_grid = None
        self._al_offset_fn = None
        self._al_decimals = 4

        self.solid_geo = None

        self.probing_shapes = None

        # store the current selection shape status to be restored after manual adding test points
        self.old_selection_state = self.app.options.get(
            'global_selection_shape',
            app_defaults.get('global_selection_shape', AppDefaults.factory_defaults.get('global_selection_shape'))
        )

    def _new_probing_shapes(self, name):
        """
        Make a new container for the probe shapes. The old container is
        cleared first, so its shapes do not stay on the canvas.
        """
        old_shapes = getattr(self, 'probing_shapes', None)
        if old_shapes is not None:
            try:
                old_shapes.clear(update=True)
            except Exception as err:
                self.app.log.debug("ToolLevelling._new_probing_shapes() -> %s" % str(err))

        self.probing_shapes = self.app.plotcanvas.new_shape_collection(layers=1)

    def _clear_probe_data(self):
        """Forget the probe points, the heights, the bilinear grid, the probing G-code and the owner."""
        # first: a pending Start request must be old before the map is cleared (no-op in the tools
        # that have no such request)
        self._invalidate_start_requests()
        self._release_manual_mouse_events()
        self.al_voronoi_geo_storage.clear()
        self._al_data_units = None
        self.al_bilinear_geo_storage = []
        self.al_heights_valid = False
        self.probing_gcode_text = ''
        self.solid_geo = None
        self._al_target_name = None
        self._al_target_obj = None
        # the GRBL frame that the map is bound to (used by the GRBL Sender only)
        self._al_map_frame = None
        self._al_map_imported = False
        self._al_bump_generation()
        self.ui.al_probe_points_table.setRowCount(0)
        shapes = getattr(self, 'probing_shapes', None)
        if shapes is not None:
            shapes.clear(update=True)
        # the 3D view holds a cached mesh; clear it when the data is gone (Sender only)
        update_3d = getattr(type(self), '_update_3d_height_map', None)
        if callable(update_3d):
            update_3d(self)

    def _height_map_is_complete(self):
        return bool(self.al_voronoi_geo_storage) and all(
            value.get('height') is not None for value in self.al_voronoi_geo_storage.values()
        )

    def on_object_selection_changed(self, current, previous):
        if getattr(self, 'ui', None) is None:
            return  # the GCode sender builds its UI at its first run
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

                if kind == 'cncjob':
                    self.ui.object_combo.set_value(name)
            except IndexError:
                pass

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

        # target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())

        # if 'Roland' in target_obj.pp_excellon_name or 'Roland' in target_obj.pp_geometry_name or 'hpgl' in \
        #         target_obj.pp_geometry_name:
        #     # TODO DO NOT AUTOLEVELL
        #     pass

        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            self.ui.al_title.hide()
            self.ui.show_al_table.hide()
            self.ui.al_probe_points_table.hide()

            # Context Menu section
            # self.ui.al_probe_points_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            self.ui.al_title.show()
            self.ui.show_al_table.show()
            if self.ui.show_al_table.get_value():
                self.ui.al_probe_points_table.show()

            # Context Menu section
            # self.ui.al_probe_points_table.setupContextMenu()

    def build_tool_ui(self):
        self.ui_disconnect()

        self.build_al_table()

        self.ui_connect()

    def build_al_table(self):
        tool_idx = 0

        n = len(self.al_voronoi_geo_storage)
        self.ui.al_probe_points_table.setRowCount(n)

        for id_key, value in self.al_voronoi_geo_storage.items():
            tool_idx += 1
            row_no = tool_idx - 1

            t_id = QtWidgets.QTableWidgetItem('%d' % int(tool_idx))
            x = value['point'].x
            y = value['point'].y
            xy_coords = self.app.dec_format(x, dec=self.app.decimals), self.app.dec_format(y, dec=self.app.decimals)
            coords_item = QtWidgets.QTableWidgetItem(str(xy_coords))
            height = self.app.dec_format(value['height'], dec=self.app.decimals)
            height_item = QtWidgets.QTableWidgetItem(str(height))

            t_id.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            coords_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            height_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            self.ui.al_probe_points_table.setItem(row_no, 0, t_id)  # Tool name/id
            self.ui.al_probe_points_table.setItem(row_no, 1, coords_item)  # X-Y coords
            self.ui.al_probe_points_table.setItem(row_no, 2, height_item)  # Determined Height

        self.ui.al_probe_points_table.resizeColumnsToContents()
        self.ui.al_probe_points_table.resizeRowsToContents()

        h_header = self.ui.al_probe_points_table.horizontalHeader()
        h_header.setMinimumSectionSize(10)
        h_header.setDefaultSectionSize(70)
        h_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h_header.resizeSection(0, 20)
        h_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.al_probe_points_table.setMinimumHeight(self.ui.al_probe_points_table.getHeight())
        self.ui.al_probe_points_table.setMaximumHeight(self.ui.al_probe_points_table.getHeight())

        self._al_set_buttons_enabled(bool(self.ui.al_probe_points_table.model().rowCount()))

    def on_add_al_probepoints(self):
        self._release_manual_mouse_events()
        if self.ui.avoid_exc_holes_cb.get_value() is True:
            try:
                self._avoidance_excellon_objects()
            except ValueError as e:
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
                return 'fail'
        # create the solid_geo

        loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if loaded_obj is None:
            self.app.log.error("ToolLevelling.on_add_al_probepoints() -> No object loaded.")
            return 'fail'

        try:
            self.solid_geo = unary_union([geo['geom'] for geo in loaded_obj.gcode_parsed if geo['kind'][0] == 'C'])
        except (TypeError, KeyError, IndexError):
            self.solid_geo = None

        if self.solid_geo is None or self.solid_geo.is_empty:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The selected object has no cut geometry. Can not add probe points."))
            return 'fail'

        # reset al table
        self.ui.al_probe_points_table.setRowCount(0)

        # reset the al dict
        self.al_voronoi_geo_storage.clear()
        self.al_heights_valid = False
        # the old bilinear grid and probing G-code belong to the old points
        self.al_bilinear_geo_storage = []
        self.probing_gcode_text = ''
        # the new points belong to this object
        self._al_target_name = self.ui.object_combo.get_value()
        self._al_target_obj = loaded_obj
        self._al_data_units = self.app.app_units.upper()
        self._al_bump_generation()

        if self.ui.al_mode_radio.get_value() == 'grid':
            self.on_add_grid_points()
        else:
            self.on_add_manual_points()

    def _avoidance_excellon_objects(self):
        return [obj for obj in self.app.collection.get_list()
                if obj.kind == 'excellon' and obj.obj_options['plot']]

    def check_point_over_excellon(self, pol: Polygon, check: bool) -> BaseGeometry:
        if not check:
            return MultiPolygon()

        fused_geometries: list[Polygon] = [
            exc_geo
            for obj_in_collection in self._avoidance_excellon_objects()
            for exc_geo in _iter_polygons(obj_in_collection.solid_geometry)
            if isinstance(exc_geo, Polygon) and pol.intersects(exc_geo)
        ]
        return unary_union(fused_geometries)

    def on_add_grid_points(self):
        self.al_heights_valid = False

        check_overlap = self.ui.avoid_exc_holes_cb.get_value()
        avoid_step = self.ui.avoid_exc_holes_size_entry.get_value()
        radius = self.ui.probe_tip_dia_entry.get_value() / 2

        if check_overlap and avoid_step <= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The Avoid Step must be greater than zero."))
            return

        xmin, ymin, xmax, ymax = self.solid_geo.bounds

        width = abs(xmax - xmin)
        height = abs(ymax - ymin)
        cols = self.ui.al_columns_entry.get_value()
        rows = self.ui.al_rows_entry.get_value()

        dx = 0 if cols == 1 else width / (cols - 1)
        dy = 0 if rows == 1 else height / (rows - 1)

        points = []
        new_y = ymin
        for x in range(rows):
            new_x = xmin
            for y in range(cols):
                formatted_point = (
                    self.app.dec_format(new_x, self.app.decimals),
                    self.app.dec_format(new_y, self.app.decimals)
                )

                point_buffered = Point(formatted_point).buffer(radius)
                if self.check_point_over_excellon(pol=point_buffered, check=check_overlap).is_empty:
                    # do not add the point if is already added
                    if formatted_point not in points:
                        points.append(formatted_point)
                        new_x += dx
                        continue

                box_poly: Polygon = box(
                    new_x - dx if (new_x - dx) > xmin else xmin,
                    new_y - dy if (new_y - dy) > ymin else ymin,
                    new_x + dx if (new_x + dx) < xmax else xmax,
                    new_y + dy if (new_y + dy) < ymax else ymax
                )

                increments = [
                    (avoid_step, 0),
                    (avoid_step * -1, 0),
                    (0, avoid_step),
                    (0, avoid_step * -1)
                ]

                for increment in increments:
                    break_for_loop = False
                    # finite limit: a blocked direction keeps the point out instead of looping forever
                    for _try in range(MAX_AVOID_TRIES):
                        # check if the point is within the box
                        formatted_point = (
                            formatted_point[0] + increment[0],
                            formatted_point[1] + increment[1]
                        )
                        point_buffered = Point(formatted_point).buffer(radius)

                        if not box_poly.contains(point_buffered):
                            break

                        # check if the point overlaps an excellon hole
                        if self.check_point_over_excellon(pol=point_buffered, check=check_overlap).is_empty:
                            # do not add the point if it is already added
                            if formatted_point not in points:
                                points.append(formatted_point)
                                break_for_loop = True
                                break
                    if break_for_loop:
                        break
                new_x += dx
            new_y += dy

        # snake order: reverse every 2nd row, so the probe moves to the next point
        # of the row and does not go back across the board
        row_values = sorted({p[1] for p in points})
        snake = []
        for row_idx, y_val in enumerate(row_values):
            row_pts = [p for p in points if p[1] == y_val]
            row_pts.sort(key=lambda p: p[0], reverse=bool(row_idx % 2))
            snake.extend(row_pts)
        points = snake

        pt_id = 0
        vor_pts_list = []
        bl_pts_list = []
        for point in points:
            pt_id += 1
            pt = Point(point)
            vor_pts_list.append(pt)
            bl_pts_list.append((point[0], point[1], 0.0))
            new_dict = {
                'point': pt,
                'geo': None,
                'height': 0.0
            }
            self.al_voronoi_geo_storage[pt_id] = deepcopy(new_dict)

        al_method = self.ui.al_method_radio.get_value()
        if al_method == 'v':
            if VORONOI_ENABLED is True:
                self.generate_voronoi_geometry(pts=vor_pts_list)
                # generate Probing GCode
                self.probing_gcode_text = self.probing_gcode(storage=self.al_voronoi_geo_storage)
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi function can not be loaded.\n"
                                                            "Shapely >= 1.8 is required"))
        else:
            self.generate_bilinear_geometry(pts=bl_pts_list)
            # generate Probing GCode
            self.probing_gcode_text = self.probing_gcode(storage=self.al_bilinear_geo_storage)

        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo(state=True, reset=True)
        else:
            # clear probe shapes
            self.plot_probing_geo(None, False)

    def on_add_manual_points(self):
        self.al_heights_valid = False

        # a 2nd click on Add must not connect the handlers 2 times
        self._release_manual_mouse_events()

        self.app.inform.emit(_("Click on canvas to add a Probe Point..."))
        # keep the current selection shape setting; it is restored when the adding ends
        self.old_selection_state = self.app.options.get('global_selection_shape', True)
        self.app.options['global_selection_shape'] = False

        self.app.plotcanvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)
        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)

        self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_click_release)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)

        self.mouse_events_connected = True

        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo(state=True, reset=True)
        else:
            # clear probe shapes
            self.plot_probing_geo(None, False)

    def _on_show_probing_geo_sig(self, state, reset):
        """
        Slot of show_probing_geo_sig. It runs in the GUI thread, so it can read
        the 'Plot probing points' checkbox. Worker threads only emit the signal.
        """
        if state and not self.ui.plot_probing_pts_cb.get_value():
            return
        self.show_probing_geo(state=state, reset=reset)

    def show_probing_geo(self, state, reset=False):
        self.app.log.debug("ToolLevelling.show_probing_geo() -> %s" % ('cleared' if state is False else 'displayed'))
        if reset or not state:
            self.probing_shapes.clear(update=True)
        if not state:
            return

        points_geo = []
        poly_geo = []

        al_method = self.ui.al_method_radio.get_value()
        radius = self.ui.probe_tip_dia_entry.get_value() / 2

        # voronoi diagram
        if al_method == 'v':
            # create the geometry
            for pt in self.al_voronoi_geo_storage:
                if not self.al_voronoi_geo_storage[pt]['geo']:
                    continue

                p_geo = self.al_voronoi_geo_storage[pt]['point'].buffer(radius)
                s_geo = self.al_voronoi_geo_storage[pt]['geo'].buffer(0.0000001)

                points_geo.append(p_geo)
                poly_geo.append(s_geo)

            if not points_geo and not poly_geo:
                return

            self.plot_probing_geo(geometry=points_geo, visibility=state, custom_color='#000000FF')
            self.plot_probing_geo(geometry=poly_geo, visibility=state)
        # bilinear interpolation
        elif al_method in ('b', 'c'):
            bilinear_points = self.al_bilinear_geo_storage
            if not bilinear_points:
                bilinear_points = [
                    (value['point'].x, value['point'].y)
                    for value in self.al_voronoi_geo_storage.values()
                ]

            for pt in bilinear_points:

                x_pt = pt[0]
                y_pt = pt[1]
                p_geo = Point([x_pt, y_pt]).buffer(radius)

                if p_geo.is_valid:
                    points_geo.append(p_geo)

            if not points_geo:
                return

            # the area allocated to each probe point (grid cell); drawn with a
            # filled random color while no heights exist, and as an outline
            # only once the heatmap carries the height information
            heatmap = self._generate_bilinear_heatmap()
            for value in self.al_voronoi_geo_storage.values():
                cell_geo = value.get('geo')
                if cell_geo is None or cell_geo.is_empty:
                    continue
                poly_geo.append(cell_geo.buffer(0.0000001))

            if poly_geo:
                if heatmap:
                    for cell_geo in poly_geo:
                        self.add_probing_shape(
                            shape=cell_geo, color='#000000FF', face_color='#00000000', visible=True
                        )
                else:
                    self.plot_probing_geo(geometry=poly_geo, visibility=state)

            for heatmap_geo, color in heatmap:
                self.add_probing_shape(
                    shape=heatmap_geo, color=color, face_color=color, visible=True
                )

            self.plot_probing_geo(geometry=points_geo, visibility=state, custom_color='#000000FF')

    def _generate_bilinear_heatmap(self):
        if not self.al_heights_valid or not self.al_voronoi_geo_storage:
            return []

        points = [
            (value['point'].x, value['point'].y, value.get('height'))
            for value in self.al_voronoi_geo_storage.values()
        ]
        if any(
                value is None or not math.isfinite(value)
                for point in points for value in point
        ):
            return []

        grid_data = build_bilinear_grid(points, self._al_grid_tolerance())
        if grid_data is None:
            return []

        xs, ys, _grid = grid_data
        z_min = min(point[2] for point in points)
        z_max = max(point[2] for point in points)
        band_colors = (
            '#2146A659', '#167CBE59', '#00A6B259', '#31B87959',
            '#A7C63659', '#F4D44D59', '#F28E2B59', '#D7302759',
        )

        def sample_axis(values):
            interval_count = len(values) - 1
            sample_count = min(40, interval_count * 4)
            if sample_count >= interval_count:
                subdivisions = max(1, min(4, sample_count // interval_count))
                axis = [values[0]]
                for start, end in zip(values, values[1:]):
                    for subdivision in range(1, subdivisions + 1):
                        axis.append(start + (end - start) * subdivision / subdivisions)
                return axis

            span = values[-1] - values[0]
            axis = [values[0] + span * index / sample_count for index in range(sample_count + 1)]
            axis[-1] = values[-1]
            return axis

        sampled_xs = sample_axis(xs)
        sampled_ys = sample_axis(ys)
        band_tiles = {}
        for x0, x1 in zip(sampled_xs, sampled_xs[1:]):
            for y0, y1 in zip(sampled_ys, sampled_ys[1:]):
                tile = box(x0, y0, x1, y1)
                if tile.is_empty:
                    continue
                height = bilinear_offset(grid_data, (x0 + x1) / 2, (y0 + y1) / 2)
                if z_max == z_min:
                    band = len(band_colors) // 2
                else:
                    band = int((height - z_min) / (z_max - z_min) * len(band_colors))
                    band = min(max(band, 0), len(band_colors) - 1)
                band_tiles.setdefault(band, []).append(tile)

        result = []
        for band, tiles in sorted(band_tiles.items()):
            geometry = unary_union(tiles)
            if not geometry.is_empty:
                result.append((geometry, band_colors[band]))
        return result

    def plot_probing_geo(self, geometry, visibility, custom_color=None):
        if visibility:
            def random_color():
                r_color = np.random.rand(4)
                r_color[3] = 0.5
                return r_color

            try:
                # for sh in points_geo:
                #     self.add_probing_shape(shape=sh, color=color, face_color=color, visible=True)

                edge_color = "#000000FF"

                try:
                    for sh in geometry:
                        if custom_color is None:
                            k = self.add_probing_shape(
                                shape=sh,
                                color=edge_color,
                                face_color=random_color(),
                                visible=True
                            )
                        else:
                            k = self.add_probing_shape(
                                shape=sh,
                                color=custom_color,
                                face_color=custom_color,
                                visible=True
                            )
                except TypeError:
                    if custom_color is None:
                        self.add_probing_shape(
                            shape=geometry, color=edge_color, face_color=random_color(), visible=True)
                    else:
                        self.add_probing_shape(
                            shape=geometry, color=custom_color, face_color=custom_color, visible=True)

                self.probing_shapes.redraw()
            except (ObjectDeleted, AttributeError) as e:
                self.app.log.error("ToolLevelling.plot_probing_geo() -> %s" % str(e))
                self.probing_shapes.clear(update=True)
            except Exception as e:
                self.app.log.error("CNCJobObject.plot_probing_geo() --> %s" % str(e))
        else:
            self.probing_shapes.clear(update=True)

    def add_probing_shape(self, **kwargs):
        key = self.probing_shapes.add(tolerance=self.drawing_tolerance, layer=0, **kwargs)
        return key

    def generate_voronoi_geometry(self, pts):
        env = self.solid_geo.envelope
        fact = 1 if self.units == 'MM' else 0.039
        env = env.buffer(fact)

        new_pts = deepcopy(pts)
        try:
            pts_union = MultiPoint(pts)
            voronoi_union = voronoi_diagram(geom=pts_union, envelope=env)
        except Exception as e:
            self.app.log.error("CNCJobObject.generate_voronoi_geometry() --> %s" % str(e))
            for pt_index in range(len(pts)):
                new_pts[pt_index] = translate(
                    new_pts[pt_index], random.random() * 1e-09, random.random() * 1e-09)

            pts_union = MultiPoint(new_pts)
            try:
                voronoi_union = voronoi_diagram(geom=pts_union, envelope=env)
            except Exception:
                return

        new_voronoi = []
        for p in voronoi_union.geoms:
            new_voronoi.append(p.intersection(env))

        for pt_key in list(self.al_voronoi_geo_storage.keys()):
            for poly in new_voronoi:
                if self.al_voronoi_geo_storage[pt_key]['point'].within(poly):
                    self.al_voronoi_geo_storage[pt_key]['geo'] = poly

    # def generate_voronoi_geometry_2(self, pts):
    #     env = self.solid_geo.envelope
    #     fact = 1 if self.units == 'MM' else 0.039
    #     env = env.buffer(fact)
    #     env_poly = voronoi_poly(tuple(env.exterior.coords))
    #
    #     new_pts = [[pt.x, pt.y] for pt in pts]
    #     print(new_pts)
    #     print(env_poly)
    #
    #     # Initialize the algorithm
    #     v = Voronoi(env_poly)
    #
    #     # calculate the Voronoi diagram
    #     try:
    #         v.create_diagram(new_pts)
    #     except AttributeError as e:
    #         self.app.log.error("CNCJobObject.generate_voronoi_geometry_2() --> %s" % str(e))
    #         new_pts_2 = []
    #         for pt_index in range(len(new_pts)):
    #             new_pts_2.append([
    #                 new_pts[pt_index][0] + random.random() * 1e-03,
    #                 new_pts[pt_index][1] + random.random() * 1e-03
    #             ])
    #
    #         try:
    #             v.create_diagram(new_pts_2)
    #         except Exception:
    #             print("Didn't work.")
    #             return
    #
    #     new_voronoi = []
    #     for p in v.sites:
    #         # p_coords = [(coord.x, coord.y) for coord in p.get_coordinates()]
    #         p_coords = [(p.x, p.y)]
    #         new_pol = Polygon(p_coords)
    #         new_voronoi.append(new_pol)
    #
    #     new_voronoi = MultiPolygon(new_voronoi)
    #
    #     # new_voronoi = []
    #     # for p in voronoi_union:
    #     #     new_voronoi.append(p.intersection(env))
    #     #
    #     for pt_key in list(self.al_voronoi_geo_storage.keys()):
    #         for poly in new_voronoi:
    #             if self.al_voronoi_geo_storage[pt_key]['point'].within(poly) or \
    #                     self.al_voronoi_geo_storage[pt_key]['point'].intersects(poly):
    #                 self.al_voronoi_geo_storage[pt_key]['geo'] = poly

    def generate_bilinear_geometry(self, pts):
        self.al_bilinear_geo_storage = pts

        # allocate an area (cell) to each probe point so it can be plotted;
        # for a regular grid the Voronoi cells are the rectangles halfway
        # between neighbouring probe points
        if VORONOI_ENABLED is True:
            self.generate_voronoi_geometry(pts=[Point((pt[0], pt[1])) for pt in pts])

    def _release_manual_mouse_events(self):
        """
        Give the canvas mouse and key events back to the app, and restore the
        selection shape setting. Does nothing if manual adding is not active.
        """
        if not getattr(self, 'mouse_events_connected', False):
            return
        self.mouse_events_connected = False

        self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)

        self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
        self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_release',
                                                              self.app.on_mouse_click_release_over_plot)

        # restore selection
        self.app.options['global_selection_shape'] = self.old_selection_state

    def _finish_manual_points(self):
        """
        Build the cells and the probing G-code for the manual probe points,
        then refresh the table and the plot. Right click and Escape use it.
        """
        pts_list = [value['point'] for value in self.al_voronoi_geo_storage.values()]
        if not pts_list:
            self.probing_gcode_text = ''
            self.build_al_table_sig.emit()
            return

        al_method = self.ui.al_method_radio.get_value()
        if al_method == 'v':
            if VORONOI_ENABLED is True:
                self.generate_voronoi_geometry(pts=pts_list)
                self.probing_gcode_text = self.probing_gcode(self.al_voronoi_geo_storage)
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi function can not be loaded.\n"
                                                            "Shapely >= 1.8 is required"))
        else:
            self.generate_bilinear_geometry(pts=[(pt.x, pt.y, 0.0) for pt in pts_list])
            self.probing_gcode_text = self.probing_gcode(storage=self.al_bilinear_geo_storage)

        # rebuild the al table
        self.build_al_table_sig.emit()

        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo(state=True, reset=True)
        else:
            # clear probe shapes
            self.plot_probing_geo(None, False)

    def on_mouse_move(self, event):
        """
        Callback for the mouse motion event over the plot.

        :param event: Contains information about the event.
        :return: None
        """

        self.mouse_is_dragging = event.is_dragging

        # So it can receive key presses but not when the Tcl Shell is active
        if not self.app.ui.shell_dock.isVisible():
            if not self.app.plotcanvas.native.hasFocus():
                self.app.plotcanvas.native.setFocus()

    # To be called after clicking on the plot.
    def on_mouse_click_release(self, event):
        if not self.mouse_events_connected:
            return

        event_pos = event.pos
        right_button = 2

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return
        event_pos = (x, y)

        # do paint single only for left mouse clicks
        if event.button == 1:
            if self.solid_geo is None:
                return
            check_for_exc_hole = self.ui.avoid_exc_holes_cb.get_value()

            pos = self.app.plotcanvas.translate_coords(event_pos)
            # use the snapped position as reference
            snapped_pos = self.app.geo_editor.snap(pos[0], pos[1])

            # do not add the point if is already added
            old_points_coords = [(pt['point'].x, pt['point'].y) for pt in self.al_voronoi_geo_storage.values()]
            if (snapped_pos[0], snapped_pos[1]) in old_points_coords:
                return

            # Clicked Point
            probe_pt = Point(snapped_pos)

            xxmin, yymin, xxmax, yymax = self.solid_geo.bounds
            box_geo = box(xxmin, yymin, xxmax, yymax)
            if not probe_pt.within(box_geo):
                self.app.inform.emit(_("Point is not within the object area. Choose another point."))
                return

            # check if chosen point is within an Excellon drill hole geometry
            if check_for_exc_hole is True:
                try:
                    objects = self._avoidance_excellon_objects()
                except ValueError as e:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % str(e))
                    return
                for obj_in_collection in objects:
                    for exc_geo in _iter_polygons(obj_in_collection.solid_geometry):
                        if probe_pt.within(exc_geo):
                            self.app.inform.emit(_("Point on an Excellon drill hole. Choose another point."))
                            return

            int_keys = [int(k) for k in self.al_voronoi_geo_storage.keys()]
            new_id = max(int_keys) + 1 if int_keys else 1
            new_dict = {
                'point': probe_pt,
                'geo': None,
                'height': 0.0
            }
            self.al_voronoi_geo_storage[new_id] = deepcopy(new_dict)
            self.al_heights_valid = False
            self._al_bump_generation()

            # rebuild the al table
            self.build_al_table_sig.emit()

            radius = self.ui.probe_tip_dia_entry.get_value() / 2
            probe_pt_buff = probe_pt.buffer(radius)

            self.plot_probing_geo(geometry=probe_pt_buff, visibility=True, custom_color="#0000FFFA")

            self.app.inform.emit(_("Added a Probe Point... Click again to add another or right click to finish ..."))

        # if RMB then we exit
        elif event.button == right_button and self.mouse_is_dragging is False:
            self._release_manual_mouse_events()
            self.app.inform.emit(_("Finished adding Probe Points..."))
            self._finish_manual_points()

    def on_key_press(self, event):
        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if isinstance(event, int):
            key = event
        # events from the GUI are of type QKeyEvent
        elif isinstance(event, QtGui.QKeyEvent):
            key = event.key()
        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        # Escape = finish adding probe points (same as a right click)
        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':
            if self.mouse_events_connected is True:
                self._release_manual_mouse_events()
                self.app.inform.emit(_("Finished adding Probe Points..."))
                self._finish_manual_points()

        # Grid toggle
        if key == QtCore.Qt.Key.Key_G or key == 'G':
            self.app.ui.grid_snap_btn.trigger()

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.app.on_jump_to()

    def _al_grid_tolerance(self):
        """
        Tolerance used to snap probe X/Y values into a bilinear grid
        (build_bilinear_grid()), i.e. to decide which probe points share
        an X column / Y row.

        This is based on `self.app.decimals` (not `target_obj.coords_decimals`,
        which is only used later to format the levelled Z output) because
        the probe point coordinates stored in `self.al_voronoi_geo_storage`
        were themselves generated/rounded with `self.app.dec_format(...,
        self.app.decimals)` (see on_add_grid_points()); 10x the smallest
        unit at that resolution absorbs the resulting float noise without
        risking merging 2 genuinely different grid rows/columns.

        :return: tolerance (float), in the units of `self.units`.
        """
        return 10 ** -self.app.decimals * 10

    def _al_match_tolerance(self, points_xy, units=None):
        """
        Tolerance used to match a GRBL probe result back to the storage
        point it was probed for (_al_check_probe_reply()).

        Unlike the grid tolerance above, a probe result's X/Y is echoed
        back by the controller itself and can carry real-world noise
        (step quantization, backlash, ...), so the tolerance is derived
        from the actual point layout: half of the minimum distance
        between any 2 distinct stored points (so 2 different points can
        never both match the same probe result), capped at a fixed
        absolute distance appropriate for the machine's units so that a
        sparse point layout doesn't produce an overly generous tolerance.

        :param points_xy: iterable of (x, y) tuples - the stored probe
            point coordinates.
        :param units: captured data units; None preserves the direct-call default.
        :return: tolerance (float), in the requested units.
        """
        cap = 0.5 if (self.units if units is None else units) == 'MM' else 0.02
        return match_tolerance(points_xy, cap)

    def autolevell_gcode_line(self, gcode_line, state):
        return level_gcode_line(gcode_line, state, self._al_offset_fn, self._al_decimals)

    def _al_capture_apply_input(self, target_obj, units=None, require_segmented=True):
        """Validate the source/map and freeze points, units and grid tolerance."""
        # an imported G-code file is levelled as it is: its moves were not made by the application
        imported = getattr(target_obj, 'gcode_imported', False) is True
        if target_obj is None or (require_segmented and not imported and target_obj.is_segmented_gcode is not True):
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("The CNCJob object must be made from segmented G-code.")
            )
            return None
        # the words of a program with macros or subprograms run with other values or more than once: the levelled
        # Z of a word would be right for one run only
        blocked = target_obj.gcode_flow_blocked('level') \
            if imported and callable(getattr(target_obj, 'gcode_flow_blocked', None)) else None
        if blocked:
            self.app.inform.emit(blocked)
            return None

        # level_gcode_line() only understands standard G-code motion; Roland,
        # HPGL and laser preprocessors emit a completely different dialect
        # (see camlib.CNCjob.codes_split()), and solder-paste jobs are XY-only
        # dispense moves, not height-compensable milling. Refuse rather than
        # silently mis-level.
        pp_geometry_name = str(getattr(target_obj, 'pp_geometry_name', '') or '')
        pp_excellon_name = str(getattr(target_obj, 'pp_excellon_name', '') or '')
        pp_solderpaste_name = getattr(target_obj, 'pp_solderpaste_name', None)

        unsupported_markers = ('roland', 'hpgl', 'laser')
        bad_pp_name = None
        if pp_solderpaste_name is not None:
            bad_pp_name = pp_solderpaste_name
        elif any(marker in pp_geometry_name.lower() for marker in unsupported_markers):
            bad_pp_name = pp_geometry_name
        elif any(marker in pp_excellon_name.lower() for marker in unsupported_markers):
            bad_pp_name = pp_excellon_name

        if bad_pp_name is not None:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s: %s.' %
                (_("Autolevelling is not supported for this preprocessor"), bad_pp_name)
            )
            return None

        storage = self.al_voronoi_geo_storage
        if not storage or not self.al_heights_valid:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' %
                _("No height map. Probe the points or import a height map first.")
            )
            return None

        units = self._al_data_units if units is None else units
        if units not in ('MM', 'IN') or str(target_obj.units).upper() != units:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("The units of the CNCJob object and of the height map do not match.")
            )
            return None

        points = tuple((v['point'].x, v['point'].y, v['height']) for v in storage.values())
        if any(value is None or not math.isfinite(value) for point in points for value in point):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The height map contains invalid values."))
            return None
        return points, units, self._al_grid_tolerance()

    def _al_prepare_interp(self, target_obj, al_method, snapshot=None):
        """Set up per-call interpolation from validated immutable input.

        Direct callers capture the current map; scheduled apply passes the
        GUI-thread snapshot without reading mutable map storage or tool units.
        """
        if snapshot is None:
            snapshot = self._al_capture_apply_input(target_obj)
        if snapshot is None:
            return False
        points, units, tol = snapshot
        if str(target_obj.units).upper() != units:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("The units of the CNCJob object and of the height map do not match.")
            )
            return False
        self._al_points = points
        self._al_decimals = target_obj.coords_decimals

        if al_method in ('b', 'c'):
            self._al_grid = build_bilinear_grid(points, tol)
            if self._al_grid is None:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s' %
                    _("Probe points do not form a regular grid. Using nearest-point levelling.")
                )
                self._al_offset_fn = self.autolevell_voronoi
            elif al_method == 'c' and not bicubic_supported(self._al_grid):
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s' %
                    _("Bicubic needs a grid of at least 4 x 4 points with equal spacing. "
                      "Using bilinear levelling.")
                )
                self._al_offset_fn = self.autolevell_bilinear
            elif al_method == 'c':
                self._al_offset_fn = self.autolevell_bicubic
            else:
                self._al_offset_fn = self.autolevell_bilinear
        else:
            self._al_offset_fn = self.autolevell_voronoi

        return True

    def autolevell_gcode(self, target_obj, al_method, snapshot=None, diagnostics=None,
                         home_wcs=None, strict=False):
        """
        Apply the probed height map (self.al_voronoi_geo_storage) to each
        of a CNCJob object's per-tool G-code texts
        (`target_obj.tools[k]['gcode']`). A single modal G-code state is
        shared across all tools, processed in `target_obj.tools`'s
        (dict/insertion) order - the same order export_gcode() uses to
        concatenate them into source_file - so a Z/G-mode set by 1
        tool's trailing lines carries into the next tool's leading
        lines exactly as it would in the assembled G-code.

        Not reentrant: it stores per-call interpolation state on `self`
        (`self._al_points`, `self._al_grid`, `self._al_offset_fn`,
        `self._al_decimals`). The caller must not start a 2nd call before
        the 1st one returns (e.g. from another thread).

        :param target_obj:  the CNCJobObject whose tools' G-code is to be
                             height-compensated. Not modified.
        :param al_method:   'v' (Voronoi/nearest-point), 'b' (bilinear) or 'c' (bicubic).
        :param snapshot:    optional validated (points, units, grid tolerance).
        :param diagnostics: optional list receiving the coverage warning for later
                             GUI-thread publication; otherwise emit it directly.
        :param home_wcs:    the WCS of the height map as a G number (54.0 for G54), or
                             None: the first WCS word of the program declares it.
        :param strict:      if True, a program with any move that was not compensated
                             (arcs, relative, unparsed, unknown, frame, cutter, cycles)
                             gives 1 error and None.
        :return:             {tooluid: levelled G-code text} for every tool
                              in target_obj.tools, or None on error.
        :rtype:              dict | None
        """
        if not self._al_prepare_interp(target_obj, al_method, snapshot=snapshot):
            return None

        # the bounds of the probe points: a Z zero after a contact probe inside them
        # re-references the map to the probed point
        map_points = getattr(self, '_al_points', None) or []
        # the height map and the job are in the app units; the text of an imported file may be in the other ones:
        # the probe X / Y / Z are converted to the units of the text before they are written into it
        text_units, text_factor = self._al_text_scale(target_obj)
        map_bounds = None
        if map_points:
            map_bounds = (min(p[0] for p in map_points) / text_factor, min(p[1] for p in map_points) / text_factor,
                          max(p[0] for p in map_points) / text_factor, max(p[1] for p in map_points) / text_factor)
        state = new_levelling_state(arc_segment=self._al_arc_segment(target_obj) / text_factor,
                                    units=text_units if text_factor != 1.0 else getattr(target_obj, 'units', None),
                                    map_bounds=map_bounds, home_wcs=home_wcs)
        state['outside'] = 0
        if text_factor != 1.0:
            map_offset_fn = self._al_offset_fn
            self._al_offset_fn = lambda x, y: map_offset_fn(x * text_factor, y * text_factor) / text_factor
        if map_points:
            tol = snapshot[2] if snapshot is not None else self._al_grid_tolerance()
            if al_method in ('b', 'c') and self._al_grid is not None:
                xs, ys, _grid = self._al_grid
                footprint = box(xs[0], ys[0], xs[-1], ys[-1])
            else:
                # A degenerate hull is a measured line or point, not a filled rectangle.
                footprint = MultiPoint([(p[0], p[1]) for p in map_points]).convex_hull
            # A provable ceiling of the map, for the XY plane of a canned cycle:
            # the segment between 2 holes must be cleared, not only its ends.
            grid_data = getattr(self, '_al_grid', None) if al_method in ('b', 'c') else None
            ceiling = conservative_ceiling(
                points=map_points, grid_data=grid_data,
                bicubic=(al_method == 'c' and grid_data is not None
                         and bicubic_supported(grid_data)))
            if ceiling is not None:
                state['map_ceiling'] = ceiling / text_factor
            offset_fn = self._al_offset_fn

            def coverage_offset(x, y):
                if footprint.distance(Point(x * text_factor, y * text_factor)) > tol:
                    state['outside'] += 1
                return offset_fn(x, y)

            state['coverage_offset'] = coverage_offset
        sources = {tooluid: target_obj.tools[tooluid].get('gcode') or '' for tooluid in target_obj.tools.keys()}
        if getattr(target_obj, 'gcode_imported', False) is True:
            # the moves of an imported file are not segmented: split the long G1 moves so that the height map
            # levels the cut between their ends too
            sources = self._al_split_imported(sources, state['arc_segment'], self._al_decimals)
        result = {}
        for tooluid, tool_gcode in sources.items():
            source_lines = tool_gcode.splitlines()
            trailing_newline = tool_gcode.endswith('\n')

            new_lines = [self.autolevell_gcode_line(line, state) for line in source_lines]

            text = '\n'.join(new_lines)
            if trailing_newline:
                text += '\n'
            result[tooluid] = text

        if strict:
            counts = [(_("arcs"), state['arcs']), (_("relative moves"), state['relative']),
                      (_("unparsed lines"), state['unparsed']), (_("unknown position or mode"), state['unknown']),
                      (_("other frame or work origin"), state['frame']),
                      (_("cutter compensation"), state['cutter']), (_("canned cycles"), state['cycles'])]
            if any(count > 0 for _name, count in counts):
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                    _("The G-code has moves that can not be height-compensated"),
                    ', '.join('%s %d' % (name, count) for name, count in counts if count > 0)))
                return None

        if state['arcs'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' % (_("Arc moves were not height-compensated"), state['arcs'])
            )

        if state['relative'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Relative (G91) moves were not height-compensated"), state['relative'])
            )

        if state['unparsed'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Lines with expressions or unknown words were not height-compensated"), state['unparsed'])
            )

        if state['unknown'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Moves at an unknown position or mode were not height-compensated"), state['unknown'])
            )

        if state['frame'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Moves in another coordinate system or units, or after a change of the work origin "
                   "or of the Z zero, a subprogram call or a G word that can not be read, "
                   "were not height-compensated"), state['frame'])
            )

        if state['cycles'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Canned cycle moves or parameters that are not supported (G73, G76, G84..G89, "
                   "another plane, missing or invalid R, Z, Q, P or feed, a repetition) "
                   "were not height-compensated"), state['cycles'])
            )

        if state['cutter'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Moves with cutter radius compensation (G41/G42) were not height-compensated"),
                 state['cutter'])
            )

        if state['arcs_split'] > 0:
            self.app.inform.emit("%s: %d" % (_("Arc moves split into line moves"), state['arcs_split']))

        if state['outside'] > 0:
            warning = '[WARNING_NOTCL] %s: %d. %s' % (
                _("Compensation samples outside the measured height map"), state['outside'],
                _("Extend and reprobe the height map over the cutting path. "
                  "Existing height compensation was retained."))
            if diagnostics is None:
                self.app.inform.emit(warning)
            else:
                diagnostics.append(warning)

        return result

    def _al_split_imported(self, sources, max_len, decimals):
        """
        Split the long G1 moves of the tool blocks of an imported job. The blocks are slices of 1 program: they are
        split together (the modal state goes on from a block to the next) and partitioned again by tool.

        :param sources:  {tooluid: G-code text of the block}, in the order of the program
        :param max_len:  the longest XY length of 1 piece, in the units of the text
        :param decimals: decimals of the new X/Y/Z words
        :return:         {tooluid: G-code text}; the sources unchanged if nothing is split
        """
        from appParsers.ParseGCode import split_tool_blocks
        from appPlugins.Common.gcode_segment import split_long_moves

        text = ''.join(sources.values())
        new_lines, count = split_long_moves(text.splitlines(), max_len, decimals)
        if count == 0:
            return sources
        new_line = '\r\n' if '\r\n' in text else '\n'
        new_text = new_line.join(new_lines) + (new_line if text.endswith('\n') else '')
        blocks = split_tool_blocks(new_text)
        if len(blocks) != len(sources):
            # a split line can not change the tool words; keep the program as it is if the blocks do not match
            return sources
        self.app.inform.emit("%s: %d" % (_("Long moves split into shorter line moves"), count))
        return {tooluid: block['gcode'] for tooluid, block in zip(sources.keys(), blocks)}

    @staticmethod
    def _al_text_scale(target_obj):
        """
        The units of the G-code text of the job and the factor from them to the units of the job (and of the height
        map). They differ only for a job imported from a G-code file in the other units than the application's.

        :return: (units of the text, factor); the factor is 1.0 when the text is in the units of the job
        """
        units = str(getattr(target_obj, 'units', '') or '').upper()
        if getattr(target_obj, 'gcode_imported', False) is True:
            file_units = str((getattr(target_obj, 'obj_options', None) or {}).get('gcode_units') or '').upper()
            if file_units in ('MM', 'IN') and units in ('MM', 'IN') and file_units != units:
                return file_units, (25.4 if file_units == 'IN' else 1 / 25.4)
        return units, 1.0

    @staticmethod
    def _al_arc_segment(target_obj):
        """
        Longest line segment that a split arc may have: the smallest positive
        of the CNCJob's seg_x / seg_y, else 1.0 for MM or 0.04 for IN.

        :param target_obj: the CNCJobObject being levelled.
        :return: segment length in the units of the object.
        :rtype: float
        """
        segments = [value for value in (getattr(target_obj, 'seg_x', 0) or 0,
                                        getattr(target_obj, 'seg_y', 0) or 0) if value > 0]
        if segments:
            return min(segments)
        return 0.04 if str(getattr(target_obj, 'units', 'MM')).upper() == 'IN' else 1.0

    def autolevell_bilinear(self, x, y):
        return bilinear_offset(self._al_grid, x, y)

    def autolevell_bicubic(self, x, y):
        return bicubic_offset(self._al_grid, x, y)

    def autolevell_voronoi(self, x, y):
        return nearest_offset(self._al_points, x, y)

    def _al_bump_generation(self):
        """Retire older results when points change or a replacement attempt starts."""
        self._al_generation = getattr(self, '_al_generation', 0) + 1

    def _al_capture_context(self, replacement=False):
        """Capture data identity/tolerance; a replacement retires prior results first.

        :return: an _AlContext, or None if a replacement would reinterpret old points.
        """
        units = self._al_data_units if self.al_voronoi_geo_storage else self.app.app_units.upper()
        if replacement:
            # first: a pending Start request is old when a probe or an import starts
            self._invalidate_start_requests()
            self._al_bump_generation()
            self.al_heights_valid = False
            # the 3D view holds a cached mesh; clear it when the map is invalidated (Sender only)
            update_3d = getattr(type(self), '_update_3d_height_map', None)
            if callable(update_3d):
                update_3d(self)
            if units != self.app.app_units.upper():
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The units of the probe points and of the project do not match. Add probe points again."))
                return None
        points = [(value['point'].x, value['point'].y) for value in self.al_voronoi_geo_storage.values()]
        return _AlContext(
            generation=getattr(self, '_al_generation', 0),
            target_name=getattr(self, '_al_target_name', None),
            target_obj=getattr(self, '_al_target_obj', None),
            units=units,
            tolerance=self._al_match_tolerance(points, units=units)
        )

    def _al_context_current(self, ctx):
        """:return: True if the probe points, the owner object and the units did not change."""
        if ctx.generation != getattr(self, '_al_generation', 0):
            return False
        if ctx.target_name != getattr(self, '_al_target_name', None):
            return False
        if ctx.target_obj is not getattr(self, '_al_target_obj', None):
            return False
        if ctx.target_obj is not None and self.app.collection.get_by_name(ctx.target_name) is not ctx.target_obj:
            return False
        if self.al_voronoi_geo_storage and ctx.units != self._al_data_units:
            return False
        return ctx.units == self.app.app_units.upper()

    def _on_al_result_ready(self, result):
        """
        GUI-thread slot of al_result_ready_sig. Publishes a probing or import
        result only if its context is still current, and only as a whole.

        :param result: dict with 'kind' ('coverage', 'probe', 'import', 'import_points' or
            'failed'), 'context' (an _AlContext), and 'heights' ({key: z}) or
            'points' ([(x, y, z), ...]).
        """
        with self._al_result_lock():
            if not self._al_context_current(result['context']):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The probe points, the target object or the units changed while the "
                    "operation ran. The result was discarded."))
                return
            if self._al_result_retired(result):
                return

            kind = result['kind']
            if kind == 'coverage':
                ctx = result['context']
                if (ctx.target_obj.obj_options['name'] == ctx.target_name and
                        str(ctx.target_obj.units).upper() == ctx.units):
                    for warning in result['warnings']:
                        self.app.inform.emit(warning)
                return
            storage = self.al_voronoi_geo_storage
            if kind == 'failed':
                self.al_heights_valid = False
                return

            # the new heights are published in this locked section: a pending Start request is old
            self._al_on_publish_locked(result)

            if kind == 'import_points':
                storage.clear()
                for idx, (x, y, z) in enumerate(result['points'], start=1):
                    storage[idx] = {'point': Point((x, y)), 'geo': None, 'height': z}
                self._al_data_units = result['context'].units
                self._al_bump_generation()
            else:
                heights = result['heights']
                if set(heights) != set(storage):
                    self.al_heights_valid = False
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "Some probe points did not receive a height value. The result was discarded."))
                    return
                for key, z in heights.items():
                    storage[key]['height'] = z

            self.al_heights_valid = True
        self.build_al_table_sig.emit()
        self.show_probing_geo_sig.emit(True, True)
        # probe and both import kinds reach this point only with a complete, valid height map
        if kind in ('probe', 'import', 'import_points'):
            self._al_on_height_map_ready(result['context'].target_obj, result)

    def on_show_al_table(self, state):
        self.ui.al_probe_points_table.show() if state else self.ui.al_probe_points_table.hide()

    def on_mode_radio(self, val):
        if val not in ('grid', 'manual'):
            return
        self._clear_probe_data()

        # build AL table
        self.build_al_table()

        self._update_mode_radio_ui(val)
        method = self.ui.al_method_radio.get_value()
        self._update_method_radio_ui(method)
        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if self._is_supported_al_target(target_obj) and getattr(target_obj, 'kind', None) == 'cncjob':
            target_obj.obj_options['tools_al_mode'] = val
            if method in ('v', 'b', 'c'):
                target_obj.obj_options['tools_al_method'] = method

    def _restore_al_radios(self, target_obj):
        mode = target_obj.obj_options.get('tools_al_mode', 'grid')
        method = target_obj.obj_options.get('tools_al_method', 'v')
        mode = mode if mode in ('grid', 'manual') else 'grid'
        method = method if method in ('v', 'b', 'c') and mode != 'manual' else 'v'
        mode_blocked = self.ui.al_mode_radio.blockSignals(True)
        method_blocked = self.ui.al_method_radio.blockSignals(True)
        try:
            self.ui.al_mode_radio.set_value(mode)
            self.ui.al_method_radio.set_value(method)
            self._update_mode_radio_ui(val=mode)
            self._update_method_radio_ui(val=method)
        finally:
            self.ui.al_method_radio.blockSignals(method_blocked)
            self.ui.al_mode_radio.blockSignals(mode_blocked)

    def _update_mode_radio_ui(self, val):
        # enable/disable the widgets relevant to the selected AL mode, without
        # touching the al_voronoi_geo_storage / al_heights_valid / probing_shapes
        # state - that reset must only happen when the user changes the mode
        # radio through the UI (see on_mode_radio(), the signal-connected
        # handler); set_tool_ui() calls this helper directly so that a
        # retained (probed/imported) height map survives re-opening the tool.
        if val == "manual":
            method_blocked = self.ui.al_method_radio.blockSignals(True)
            try:
                self.ui.al_method_radio.set_value('v')
            finally:
                self.ui.al_method_radio.blockSignals(method_blocked)
            self.ui.al_rows_entry.setDisabled(True)
            self.ui.al_rows_label.setDisabled(True)
            self.ui.al_columns_entry.setDisabled(True)
            self.ui.al_columns_label.setDisabled(True)
            self.ui.al_method_lbl.setDisabled(True)
            self.ui.al_method_radio.setDisabled(True)
            # self.ui.avoid_exc_holes_cb.setDisabled(False)
        else:
            self.ui.al_rows_entry.setDisabled(False)
            self.ui.al_rows_label.setDisabled(False)
            self.ui.al_columns_entry.setDisabled(False)
            self.ui.al_columns_label.setDisabled(False)
            self.ui.al_method_lbl.setDisabled(False)
            self.ui.al_method_radio.setDisabled(False)
            # self.ui.avoid_exc_holes_cb.setDisabled(True)

    def on_avoid_exc_holes(self, state):
        self.ui.avoid_exc_holes_size_label.show() if state else self.ui.avoid_exc_holes_size_label.hide()
        self.ui.avoid_exc_holes_size_entry.show() if state else self.ui.avoid_exc_holes_size_entry.hide()

    def on_method_radio(self, val):
        if val not in ('v', 'b', 'c'):
            return
        if self.ui.al_mode_radio.get_value() == 'manual':
            val = 'v'
            self._update_mode_radio_ui('manual')
        self._update_method_radio_ui(val)
        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if self._is_supported_al_target(target_obj) and getattr(target_obj, 'kind', None) == 'cncjob':
            target_obj.obj_options['tools_al_method'] = val

    def _update_method_radio_ui(self, val):
        if val in ('b', 'c'):
            self.ui.al_columns_entry.setMinimum(2)
            self.ui.al_rows_entry.setMinimum(2)
        else:
            self.ui.al_columns_entry.setMinimum(1)
            self.ui.al_rows_entry.setMinimum(1)

    def on_import_height_map(self):
        """
        Import the height map file into the app
        :return:
        :rtype:
        """

        _filter_ = "Text File .txt (*.txt);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import Height Map"),
                                                                 directory=self.app.get_last_folder(),
                                                                 filter=_filter_)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import Height Map"),
                                                                 filter=_filter_)

        filename = str(filename)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            # the imported map belongs to the object selected now
            current_name = self.ui.object_combo.get_value()
            current_obj = self.app.collection.get_by_name(current_name)
            if current_name != getattr(self, '_al_target_name', None) or \
                    current_obj is not getattr(self, '_al_target_obj', None):
                self._clear_probe_data()
                self._al_target_name = current_name
                self._al_target_obj = current_obj
            context = self._al_capture_context(replacement=True)
            if context is None:
                return
            points = {key: (v['point'].x, v['point'].y) for key, v in self.al_voronoi_geo_storage.items()}
            self.app.worker_task.emit({'fcn': self.import_height_map, 'params': [filename, context, points]})

    def import_height_map(self, filename, context=None, points=None):
        """
        Read a height map file (worker thread). The result goes to the GUI thread
        through al_result_ready_sig, as a whole; nothing is written here.

        :param filename: path of the height map file.
        :param context:  _AlContext captured by the caller; None starts a replacement attempt.
        :param points:   {key: (x, y)} of the probe points captured by the caller; None means 'now'.
        """
        if context is None:
            context = self._al_capture_context(replacement=True)
            if context is None:
                return
        if points is None:
            points = {key: (v['point'].x, v['point'].y) for key, v in self.al_voronoi_geo_storage.items()}
        failed = {'kind': 'failed', 'context': context}

        try:
            if not filename:
                return
            with open(filename, 'r') as f:
                stream = f.readlines()
        except IOError:
            self.app.log.error("Failed to open height map file: %s" % filename)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open height map file"), filename))
            self.al_result_ready_sig.emit(failed)
            return

        rows = []
        for line_no, line in enumerate(stream, start=1):
            parsed = parse_height_map_line(line)
            if parsed is None:
                if line.strip() != '':
                    self.app.log.debug(
                        "ToolLevelling.import_height_map() -> could not parse line %d: %r" % (line_no, line)
                    )
                continue
            rows.append(parsed)

        if not rows:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The height map file has no valid rows."))
            self.al_result_ready_sig.emit(failed)
            return

        if not points:
            self.al_result_ready_sig.emit({'kind': 'import_points', 'context': context, 'points': rows})
            return

        tol = context.tolerance
        heights = {}
        unmatched_rows = 0
        for x, y, z in rows:
            key = match_nearest_point(points, x, y, tol)
            if key is None:
                unmatched_rows += 1
                continue
            heights[key] = z

        if unmatched_rows:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Height map rows could not be matched to a probe point"), unmatched_rows)
            )

        if set(heights) != set(points):
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' %
                _("Some probe points have no matching row in the height map file.")
            )
            self.al_result_ready_sig.emit(failed)
            return

        self.al_result_ready_sig.emit({'kind': 'import', 'context': context, 'heights': heights})

    def on_grbl_heightmap_save(self):
        if not self.al_heights_valid or not self._height_map_is_complete():
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Empty GRBL heightmap."))
            return

        _filter_ = "Text File .txt (*.txt);;All Files (*.*)"
        name = "probing_gcode"
        try:
            dir_file_to_save = self.app.get_last_save_folder() + '/' + str(name)
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=dir_file_to_save,
                ext_filter=_filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return
        else:
            try:
                app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
                force_windows_line_endings = self.app.options.get(
                    'cncjob_line_ending',
                    app_defaults.get('cncjob_line_ending', AppDefaults.factory_defaults.get('cncjob_line_ending'))
                )
                open_kwargs = {'newline': '\r\n'} if force_windows_line_endings and sys.platform != 'win32' else {}
                with open(filename, 'w', **open_kwargs) as f:
                    for value in self.al_voronoi_geo_storage.values():
                        f.write('%s,%s,%s\n' % (value['point'].x, value['point'].y, value['height']))
            except FileNotFoundError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit(
                    '[WARNING] %s' % _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible.")
                )
                return 'fail'

    # ------------------------------------------------------------------
    # hooks: each tool that uses this mixin overrides these 4 methods
    # ------------------------------------------------------------------
    def _al_set_buttons_enabled(self, enabled):
        """Enable the buttons that need probe points. `enabled` is True if the table has rows."""
        pass

    def _al_result_lock(self):
        """The lock that _on_al_result_ready() holds while it publishes a result."""
        return contextlib.nullcontext()

    def _al_result_retired(self, result):
        """:return: True if the operation that made `result` was stopped or replaced."""
        return False

    def _al_on_publish_locked(self, result):
        """Called with the result lock held, just before a probe or import result is published."""
        pass

    def _invalidate_start_requests(self):
        """Make the pending Start requests old. Call it where no lock is held. The GRBL Sender overrides it."""
        pass

    def _al_on_height_map_ready(self, target_obj, result=None):
        """
        Called after a probe or an import gave a complete and valid height map.

        :param result: the result dict of _on_al_result_ready(); a probe result has 'frame'.
        """
        pass
