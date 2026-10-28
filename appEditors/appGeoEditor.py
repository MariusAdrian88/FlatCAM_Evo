# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File Modified: Marius Adrian Stanciu (c)                 #
# Date: 3/10/2019                                          #
# ##########################################################

from appHandlers.appHistory import history_of, HistoryManager, state_size, invert_matrix
from PyQt6 import QtGui, QtCore, QtWidgets
from PyQt6.QtCore import Qt
# import inspect
import math
from contextlib import contextmanager
from functools import wraps
from threading import RLock, get_ident
from weakref import WeakKeyDictionary, WeakSet, ref

from camlib import distance, arc, three_point_circle, Geometry, AppRTreeStorage, flatten_shapely_geometry, \
    translate_geometry, affine_translate_matrix, affine_rotate_matrix, affine_scale_matrix, affine_skew_matrix, \
    affine_mirror_matrix, affine_geometry_many, object_circle_steps, object_quad_segs
from appGUI.GUIElements import FCLabel, GLay, FCDoubleSpinner, FCTree, FCButton, FCFrame, FCCheckBox, FCEntry, \
    FCTextEdit, show_selected_rows
from appGUI.VisPyVisuals import ShapeCollection

from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
from appEditors.geo_plugins.GeoPaintPlugin import PaintOptionsTool
from appEditors.geo_plugins.GeoTextPlugin import TextInputTool
from appEditors.geo_plugins.GeoTransformationPlugin import TransformEditorTool
from appEditors.geo_plugins.GeoPathPlugin import PathEditorTool
from appEditors.geo_plugins.GeoSimplificationPlugin import SimplificationTool
from appEditors.geo_plugins.GeoRectanglePlugin import RectangleEditorTool
from appEditors.geo_plugins.GeoCirclePlugin import CircleEditorTool
from appEditors.geo_plugins.GeoCopyPlugin import CopyEditorTool
from appEditors.editor_corner import corner_point, next_bend_mode, bend_mode_message, BEND_MODE_FREE
from appEditors.vertex_ops import (vertex_address, edit_vertex, check_vertex_result, parts_of, rings_of,
                                   SUPPORTED_TYPES)
from appEditors.vertex_grips import VertexGrips
from appEditors.cursor_text import show_cursor_text, hide_cursor_text

from vispy.geometry import Rect

from shapely import LineString, LinearRing, MultiLineString, Polygon, MultiPolygon, Point, box, STRtree
from shapely.geometry import base
import shapely
from shapely.ops import unary_union, linemerge, substring
from shapely.affinity import translate, scale, skew, rotate
from shapely.geometry.polygon import orient
from shapely.geometry.base import BaseGeometry

import numpy as np
from numpy.linalg import norm as numpy_norm
import logging


from copy import deepcopy
# from vispy.io import read_png

from typing import Union

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# a click picks the shapes nearer than this number of screen pixels (the same value as in the Gerber editor)
PICK_TOLERANCE_PX = 5
CLICK_DRAG_THRESHOLD_PX = 5


class GeometryEditorStorage(AppRTreeStorage):
    """Track mutations at the storage boundary, including direct edits of stored wrappers."""

    def __init__(self):
        super().__init__()
        self.revision = 0

    def insert(self, obj):
        self.revision += 1
        super().insert(obj)
        DrawToolShape._storages.setdefault(obj, WeakSet()).add(self)

    def remove(self, obj):
        self.revision += 1
        super().remove(obj)
        DrawToolShape._storages.get(obj, set()).discard(self)

    def replace(self, removed=(), inserted=(), skip_errors=False):
        removed, inserted = list(removed), list(inserted)
        if not removed and not inserted:
            return
        self.revision += 1
        try:
            super().replace(removed, inserted, skip_errors=skip_errors)
        finally:
            # replace() can either bulk rebuild or call insert/remove. Track both paths, even on failure.
            for shape in removed + inserted:
                index = self.indexes.get(id(shape))
                if index is not None and self.objects[index] is shape:
                    DrawToolShape._storages.setdefault(shape, WeakSet()).add(self)
                else:
                    DrawToolShape._storages.get(shape, set()).discard(self)


def geometry_operation(method):
    @wraps(method)
    def operation(self, *args, **kwargs):
        tool_name = getattr(getattr(self, 'active_tool', None), 'name', '')
        label = tool_name if tool_name and tool_name != 'select' else method.__name__.replace('_', ' ')
        with AppGeoEditor.history_operation(self, label=_(label.capitalize())) as accepted:
            if not accepted:
                return 'fail'
            return method(self, *args, **kwargs)
    return operation


def shape_size(geometry):
    """
    Size of a shape geometry for Select by Size.

    :return: ('area', value) for Polygon/MultiPolygon, ('length', value) for LineString/LinearRing/MultiLineString,
             None for points, empty geometry and mixed collections. A list/tuple of parts (text) counts as the
             sum of its parts when they are all of one kind.
    """
    if isinstance(geometry, (list, tuple)):
        sizes = [shape_size(part) for part in geometry]
        kinds = {size[0] for size in sizes if size}
        if not sizes or None in sizes or len(kinds) != 1:
            return None
        return kinds.pop(), sum(size[1] for size in sizes)
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return 'area', geometry.area
    if isinstance(geometry, (LineString, LinearRing, MultiLineString)):
        return 'length', geometry.length
    return None


def shapes_in_size_range(shapes, area_range=None, length_range=None):
    """
    The shapes whose area (polygons) or length (paths) is inside the given inclusive (min, max) range.
    A range of None disables that filter; shapes of the other kind never match. Needs no GUI.
    """
    ranges = {'area': area_range, 'length': length_range}
    matches = []
    for shape in shapes:
        size = shape_size(shape.geo)
        limits = ranges[size[0]] if size else None
        if limits is not None and limits[0] <= size[1] <= limits[1]:
            matches.append(shape)
    return matches


CIRCLE_MIN_VERTICES = 8
CIRCLE_RADIUS_TOLERANCE = 1e-3


def circle_of(geometry, tolerance=CIRCLE_RADIUS_TOLERANCE):
    """
    Detect a circle drawn as a polygon (Polygon without holes) or as an outline (LinearRing / closed LineString).
    Shapes keep no circle metadata, so it is geometric: at least CIRCLE_MIN_VERTICES vertices, all at the same
    distance from the center within `tolerance` * radius. Squares (too few vertices) and ellipses fail.

    :return: ((x, y), radius), or None if the geometry is not a circle. Needs no GUI.
    """
    if isinstance(geometry, Polygon):
        if geometry.is_empty or len(geometry.interiors):
            return None
        ring = geometry.exterior
    elif isinstance(geometry, LinearRing) or (isinstance(geometry, LineString) and geometry.is_closed):
        ring = geometry
    else:
        return None
    if ring.is_empty or ring.has_z:
        return None
    vertices = np.asarray(ring.coords)[:-1]
    if len(vertices) < CIRCLE_MIN_VERTICES or not np.all(np.isfinite(vertices)):
        return None
    center = Polygon(ring).centroid
    if center.is_empty:
        return None
    center = (center.x, center.y)
    distances = np.hypot(vertices[:, 0] - center[0], vertices[:, 1] - center[1])
    radius = float(distances.mean())
    if radius <= 0 or float(distances.max() - distances.min()) > tolerance * radius:
        return None
    return center, radius


def resized_circle(geometry, new_diameter):
    """
    The circle scaled about its own center to the new diameter. Scaling (not a rebuild) keeps the geometry type,
    the vertex count and the vertex phase exactly. None if the geometry is not a circle. Needs no GUI.
    """
    found = circle_of(geometry)
    if found is None:
        return None
    center, radius = found
    factor = (new_diameter / 2.0) / radius
    return scale(geometry, xfact=factor, yfact=factor, origin=center)


# the kinds of project objects whose shapes the Import tool can copy into the edited Geometry
IMPORT_SOURCE_KINDS = ('geometry', 'gerber', 'excellon')


def import_source_objects(objects, edited_obj):
    """
    The project objects the Import tool takes shapes from: visible Geometry, Gerber and Excellon objects; never the
    edited object itself and never a CNCJob. Needs no GUI.
    """
    sources = []
    for obj in objects:
        if obj is edited_obj or getattr(obj, 'kind', None) not in IMPORT_SOURCE_KINDS:
            continue
        if obj.obj_options.get('plot') is not True:
            continue
        sources.append(obj)
    return sources


def import_source_geometries(obj, app_units=None):
    """
    The shapes of a project object, flat: Shapely polygons and paths. A multi-geo Geometry gives the shapes of all
    its tools. If the object has other units than app_units, the shapes are scaled to app_units. Needs no GUI.

    :param obj:         Geometry, Gerber or Excellon object
    :param app_units:   'MM' or 'IN'; None for no unit check
    :return:            list of Shapely geometries (copies are made on commit)
    """
    if obj.kind == 'geometry' and getattr(obj, 'multigeo', False):
        sources = [tool.get('solid_geometry') for tool in obj.tools.values()]
    else:
        sources = [obj.solid_geometry]
        if obj.kind == 'excellon' and not obj.solid_geometry:
            sources = [tool.get('solid_geometry') for tool in obj.tools.values()]

    geos = [geo for geo in flatten_shapely_geometry([src for src in sources if src is not None])
            if geo.geom_type != 'Point']

    obj_units = str(getattr(obj, 'units', '') or '').upper()
    if app_units and obj_units in ('MM', 'IN') and obj_units != str(app_units).upper():
        factor = 25.4 if obj_units == 'IN' else 1 / 25.4
        geos = [scale(geo, factor, factor, origin=(0, 0)) for geo in geos]
    return geos


def geometries_at_point(geometries, point, tolerance=0.0):
    """
    The geometries under a point: a polygon that contains it, a path or a polygon outline within the tolerance.
    Needs no GUI.
    """
    pt = Point(point[0], point[1])
    return [geo for geo in geometries if geo.distance(pt) <= tolerance]


def geometries_in_area(geometries, start, end, enclosing=True):
    """
    The geometries inside (enclosing=True) or touching (enclosing=False) the rectangle with the corners start and
    end. Needs no GUI.
    """
    area = box(min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1]))
    if enclosing:
        return [geo for geo in geometries if geo.within(area)]
    return [geo for geo in geometries if geo.intersects(area)]


class AppGeoEditor(QtCore.QObject):
    # will emit the name of the object that was just selected

    item_selected = QtCore.pyqtSignal(str)

    transform_complete = QtCore.pyqtSignal()

    build_ui_sig = QtCore.pyqtSignal()
    clear_tree_sig = QtCore.pyqtSignal()
    source_save_done = QtCore.pyqtSignal(object)
    history_changed = QtCore.pyqtSignal()

    draw_shape_idx = -1

    def __init__(self, app, disabled=False):
        # assert isinstance(app, FlatCAMApp.App), \
        #     "Expected the app to be a FlatCAMApp.App, got %s" % type(app)

        super(AppGeoEditor, self).__init__()

        self.app = app
        self.source_save_done.connect(self.on_source_save_done, Qt.ConnectionType.QueuedConnection)
        self._history_lock = RLock()
        self.reset_history()
        self.canvas = app.plotcanvas
        self.decimals = app.decimals
        self.units = self.app.app_units

        # #############################################################################################################
        # Geometry Editor UI
        # #############################################################################################################
        self.ui = AppGeoEditorUI(app=self.app)
        self.history_changed.connect(self.refresh_history, Qt.ConnectionType.QueuedConnection)
        self.ui.history_positions.activated.connect(self.history_go_to)
        self.refresh_history()
        if disabled:
            self.ui.geo_frame.setDisabled(True)

        # when True the Editor can't do selection due of an ongoing process
        self.interdict_selection = False

        # ## Toolbar events and properties
        self.tools = {}

        # # ## Data
        self.active_tool = None

        self.storage = self.make_storage()
        self.utility = []

        # VisPy visuals
        self.fcgeometry = None
        self.shapes = self.app.plotcanvas.new_shape_collection(layers=1)
        self.sel_shapes = self.app.plotcanvas.new_shape_collection(layers=1)
        self.tool_shape = self.app.plotcanvas.new_shape_collection(layers=1)
        # grips to drag the vertices of 1 selected shape in the Select tool
        self.vertex_grips = VertexGrips(self)

        # Remove from scene
        self.shapes.enabled = False
        self.sel_shapes.enabled = False
        self.tool_shape.enabled = False

        # List of selected shapes.
        self.selected = []

        # what plot_all() drew: id(shape) -> [shape, geo, drawable, keys in 'shapes', keys in 'sel_shapes', selected];
        # plot_changes() uses it to plot only the shapes that changed. _render_state is None when the cache is not
        # usable.
        self._rendered = {}
        self._render_state = None

        self.flat_geo = []

        self.move_timer = QtCore.QTimer()
        self.move_timer.setSingleShot(True)

        # this var will store the state of the toolbar before starting the editor
        self.toolbar_old_state = False

        self.key = None  # Currently, pressed key
        self.geo_key_modifiers = None
        self.x = None  # Current mouse cursor pos
        self.y = None

        # if we edit a multigeo geometry store here the tool number
        self.multigeo_tool = None

        # Current snapped mouse pos
        self.snap_x = None
        self.snap_y = None
        self.pos = None

        # signal that there is an action active like polygon or path
        self.in_action = False

        self.units = None

        # this will flag if the Editor "tools" are launched from key shortcuts (True) or from menu toolbar (False)
        self.launched_from_shortcuts = False

        self.editor_options = {
            "global_gridx": 0.1,
            "global_gridy": 0.1,
            "global_snap_max": 0.05,
            "grid_snap": True,
            "corner_snap": False,
            "midpoint_snap": False,
            "segment_snap": False,
            "snap_radius_px": 8.0,
            "grid_gap_link": True
        }
        self.editor_options.update(self.app.options)

        for option in self.editor_options:
            if option in self.app.options:
                self.editor_options[option] = self.app.options.get(
                    option,
                    self.app.defaults.get(option, self.app.defaults.factory_defaults.get(option))
                )

        self.app.ui.grid_gap_x_entry.setText(str(self.editor_options["global_gridx"]))
        self.app.ui.grid_gap_y_entry.setText(str(self.editor_options["global_gridy"]))
        self.app.ui.snap_max_dist_entry.setText(str(self.editor_options["global_snap_max"]))
        self.app.ui.grid_gap_link_cb.setChecked(True)

        self.app.ui.grid_gap_x_entry.setValidator(QtGui.QDoubleValidator())
        self.app.ui.grid_gap_y_entry.setValidator(QtGui.QDoubleValidator())
        self.app.ui.snap_max_dist_entry.setValidator(QtGui.QDoubleValidator())

        # corner mode of the Path and Polygon tools: 1 = 45 deg, 2 = reverse 45 deg, 3 = 90 deg, 4 = reverse 90 deg,
        # 5 = free angle. It is kept for the whole editor session; 5 keeps the legacy behavior.
        self.bend_mode = BEND_MODE_FREE

        # if using Paint store here the tool diameter used
        self.paint_tooldia = None

        self.paint_tool = PaintOptionsTool(self.app, self)
        self.transform_tool = TransformEditorTool(self.app, self)

        # #############################################################################################################
        # ####################### GEOMETRY Editor Signals #############################################################
        # #############################################################################################################
        self.build_ui_sig.connect(self.build_ui)

        self.app.ui.grid_gap_x_entry.textChanged.connect(self.on_gridx_val_changed)
        self.app.ui.grid_gap_y_entry.textChanged.connect(self.on_gridy_val_changed)
        self.app.ui.snap_max_dist_entry.textChanged.connect(
            lambda: self.entry2option("snap_max", self.app.ui.snap_max_dist_entry))

        self.app.ui.grid_snap_btn.triggered.connect(lambda: self.on_grid_toggled())
        self.app.ui.corner_snap_btn.setCheckable(True)
        self.app.ui.corner_snap_btn.triggered.connect(lambda: self.toolbar_tool_toggle("corner_snap"))

        self.ui.midpoint_snap_cb.toggled.connect(
            lambda checked: self.editor_options.update(midpoint_snap=checked))
        self.ui.segment_snap_cb.toggled.connect(
            lambda checked: self.editor_options.update(segment_snap=checked))
        self.ui.snap_radius_entry.valueChanged.connect(
            lambda value: self.editor_options.update(snap_radius_px=value))

        # connect the toolbar signals
        self.connect_geo_toolbar_signals()

        # connect Geometry Editor Menu signals
        self.app.ui.geo_add_circle_menuitem.triggered.connect(lambda: self.select_tool('circle'))
        self.app.ui.geo_add_arc_menuitem.triggered.connect(lambda: self.select_tool('arc'))
        self.app.ui.geo_add_rectangle_menuitem.triggered.connect(lambda: self.select_tool('rectangle'))
        self.app.ui.geo_add_polygon_menuitem.triggered.connect(lambda: self.select_tool('polygon'))
        self.app.ui.geo_add_path_menuitem.triggered.connect(lambda: self.select_tool('path'))
        self.app.ui.geo_add_text_menuitem.triggered.connect(lambda: self.select_tool('text'))
        self.app.ui.geo_paint_menuitem.triggered.connect(lambda: self.select_tool("paint"))
        self.app.ui.geo_buffer_menuitem.triggered.connect(lambda: self.select_tool("buffer"))
        self.app.ui.geo_simplification_menuitem.triggered.connect(lambda: self.select_tool("simplification"))
        self.app.ui.geo_transform_menuitem.triggered.connect(lambda: self.transform_tool.run())

        self.app.ui.geo_delete_menuitem.triggered.connect(self.on_delete_btn)
        self.app.ui.geo_union_menuitem.triggered.connect(self.union)
        self.app.ui.geo_polygonize_menuitem.triggered.connect(self.polygonize)
        self.app.ui.geo_intersection_menuitem.triggered.connect(self.intersection)
        self.app.ui.geo_subtract_menuitem.triggered.connect(self.subtract)
        self.app.ui.geo_subtract_alt_menuitem.triggered.connect(self.subtract_2)

        self.app.ui.geo_cutpath_menuitem.triggered.connect(self.cutpath)
        self.app.ui.geo_copy_menuitem.triggered.connect(lambda: self.select_tool('copy'))
        self.app.ui.geo_import_menuitem.triggered.connect(lambda: self.select_tool('import'))

        self.app.ui.geo_union_btn.triggered.connect(self.union)
        self.app.ui.geo_intersection_btn.triggered.connect(self.intersection)
        self.app.ui.geo_subtract_btn.triggered.connect(self.subtract)
        self.app.ui.geo_alt_subtract_btn.triggered.connect(self.subtract_2)

        self.app.ui.geo_cutpath_btn.triggered.connect(self.cutpath)
        self.app.ui.geo_delete_btn.triggered.connect(self.on_delete_btn)

        self.app.ui.geo_move_menuitem.triggered.connect(self.on_move)
        self.app.ui.geo_cornersnap_menuitem.triggered.connect(self.on_corner_snap)

        self.transform_complete.connect(self.on_transform_complete)

        self.ui.change_orientation_btn.clicked.connect(self.on_change_orientation)
        self.ui.vertex_edit_btn.clicked.connect(self.on_edit_vertices)
        self.ui.join_btn.clicked.connect(self.on_join_paths)
        self.ui.diagnostics_btn.clicked.connect(self.on_diagnostics)
        self.ui.size_select_btn.clicked.connect(self.on_select_by_size)
        self.ui.size_delete_btn.clicked.connect(self.on_delete_by_size)
        self.ui.resize_circles_btn.clicked.connect(self.on_resize_circles)

        self.ui.tw.customContextMenuRequested.connect(self.on_menu_request)

        self.clear_tree_sig.connect(self.on_clear_tree)

        self.app.log.debug("Initialization of the Geometry Editor is finished ...")

    def make_callback(self, thetool):
        def f():
            self.on_tool_select(thetool)

        return f

    def connect_geo_toolbar_signals(self):
        self.app.ui.geo_join_btn.triggered.connect(self.on_join_paths)
        self.app.ui.geo_polygonize_btn.triggered.connect(lambda: self.polygonize())
        self.tools.update({
            "select": {"button": self.app.ui.geo_select_btn, "constructor": FCSelect},
            "arc": {"button": self.app.ui.geo_add_arc_btn, "constructor": FCArc},
            "circle": {"button": self.app.ui.geo_add_circle_btn, "constructor": FCCircle},
            "path": {"button": self.app.ui.geo_add_path_btn, "constructor": FCPath},
            "rectangle": {"button": self.app.ui.geo_add_rectangle_btn, "constructor": FCRectangle},
            "polygon": {"button": self.app.ui.geo_add_polygon_btn, "constructor": FCPolygon},
            "text": {"button": self.app.ui.geo_add_text_btn, "constructor": FCText},
            "buffer": {"button": self.app.ui.geo_add_buffer_btn, "constructor": FCBuffer},
            "simplification": {"button": self.app.ui.geo_add_simplification_btn, "constructor": FCSimplification},
            "paint": {"button": self.app.ui.geo_add_paint_btn, "constructor": FCPaint},
            "eraser": {"button": self.app.ui.geo_eraser_btn, "constructor": FCEraser},
            "move": {"button": self.app.ui.geo_move_btn, "constructor": FCMove},
            "transform": {"button": self.app.ui.geo_transform_btn, "constructor": FCTransform},
            "copy": {"button": self.app.ui.geo_copy_btn, "constructor": FCCopy},
            "import": {"button": self.app.ui.geo_import_btn, "constructor": FCImport},
            "explode": {"button": self.app.ui.geo_explode_btn, "constructor": FCExplode},
            "split": {"button": self.ui.split_action, "constructor": FCSplit}
        })

        for tool in self.tools:
            self.tools[tool]["button"].triggered.connect(self.make_callback(tool))  # Events
            self.tools[tool]["button"].setCheckable(True)  # Checkable

    def on_transform_complete(self):
        self.delete_selected()
        self.plot_all()

    def entry2option(self, opt, entry):
        """

        :param opt:     An option from the self.editor_options dictionary
        :param entry:   A GUI element which text value is used
        :return:
        """
        try:
            text_value = entry.text()
            if ',' in text_value:
                text_value = text_value.replace(',', '.')
            self.editor_options[opt] = float(text_value)
        except Exception as e:
            entry.set_value(self.app.options.get(
                opt,
                self.app.defaults.get(opt, self.app.defaults.factory_defaults.get(opt))
            ))
            self.app.log.error("AppGeoEditor.__init__().entry2option() --> %s" % str(e))
            return

    def grid_changed(self, goption, gentry):
        """

        :param goption:     String. Can be either 'global_gridx' or 'global_gridy'
        :param gentry:      A GUI element which text value is read and used
        :return:
        """
        if goption not in ['global_gridx', 'global_gridy']:
            return

        self.entry2option(opt=goption, entry=gentry)
        # if the grid link is checked copy the value in the GridX field to GridY
        try:
            text_value = gentry.text()
            if ',' in text_value:
                text_value = text_value.replace(',', '.')
            val = float(text_value)
        except ValueError:
            return

        if self.app.ui.grid_gap_link_cb.isChecked():
            self.app.ui.grid_gap_y_entry.set_value(val, decimals=self.decimals)

    def on_gridx_val_changed(self):
        self.grid_changed("global_gridx", self.app.ui.grid_gap_x_entry)
        # try:
        #     self.app.options["global_gridx"] =  float(self.app.ui.grid_gap_x_entry.get_value())
        # except ValueError:
        #     return

    def on_gridy_val_changed(self):
        self.entry2option("global_gridy", self.app.ui.grid_gap_y_entry)

    def set_editor_ui(self):
        # updated units
        self.units = self.app.app_units.upper()
        self.decimals = self.app.decimals

        self.ui.geo_coords_entry.setText('')
        self.ui.is_ccw_entry.set_value('None')
        self.ui.is_ring_entry.set_value('None')
        self.ui.is_simple_entry.set_value('None')
        self.ui.is_empty_entry.set_value('None')
        self.ui.is_valid_entry.set_value('None')
        self.ui.geo_vertex_entry.set_value(0.0)
        self.ui.geo_zoom.set_value(False)

        self.ui.param_button.setChecked(self.app.options.get(
            'geometry_editor_parameters',
            self.app.defaults.get(
                'geometry_editor_parameters',
                self.app.defaults.factory_defaults.get('geometry_editor_parameters')
            )
        ))

        # Remove anything else in the GUI Selected Tab
        self.app.ui.properties_scroll_area.takeWidget()
        # Put ourselves in the appGUI Properties Tab
        self.app.ui.properties_scroll_area.setWidget(self.ui.geo_edit_widget)
        # Switch notebook to Properties page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            self.app.defaults.get(
                "global_app_level",
                self.app.defaults.factory_defaults.get("global_app_level")
            )
        )
        self.ui.change_level(app_mode)

    def build_ui(self):
        """
        Build the appGUI in the Properties Tab for this editor

        :return:
        """

        # the rows are removed and added in 1 batch each, with the tree signals blocked: a removed selected row does
        # not run the tree handlers (and their plots) for each row
        tw = self.ui.tw
        tw.blockSignals(True)
        tw.setUpdatesEnabled(False)
        try:
            self.ui.geo_parent.takeChildren()

            items = []
            for elem in self.storage.get_objects():
                geo_type = type(elem.geo)

                if geo_type is MultiLineString:
                    el_type = _('Multi-Line')
                elif geo_type is MultiPolygon:
                    el_type = _('Multi-Polygon')
                else:
                    el_type = elem.data['type']

                items.append(tw.addParentEditable(
                    None,
                    [
                        str(id(elem)),
                        '%s' % el_type,
                        _("Geo Elem")
                    ],
                    font=self.ui.geo_font,
                    font_items=2,
                    # color=QtGui.QColor("#FF0000"),
                    editable=True
                ))
            self.ui.geo_parent.addChildren(items)
        finally:
            tw.setUpdatesEnabled(True)
            tw.blockSignals(False)

        # the new rows show the current selection
        self.sync_tree_selection()

        self.ui.tw.resize_sig.emit()

    def on_geo_elem_selected(self):
        pass

    def get_pick_index(self):
        """
        :return: (tree, refs): tree is a STRtree on the geometries of the storage (None if there is none), refs is the
                 list of the shapes in the same order. The index is made again only when the storage changed.
        """
        state = (self.storage, self.storage.revision)
        cache = getattr(self, '_pick_cache', None)
        if cache is not None and cache[0] == state:
            return cache[1], cache[2]

        refs = [shape for shape in self.storage.get_objects() if isinstance(shape.geo, BaseGeometry)]
        tree = STRtree([shape.geo for shape in refs]) if refs else None
        self._pick_cache = (state, tree, refs)
        self._pick_cycle = None
        return tree, refs

    def pick_at(self, point):
        """
        The shape under (or near) a click: the nearest shape in PICK_TOLERANCE_PX screen pixels. When more shapes
        are at the same distance (a click inside of more polygons), the one with the smaller area is picked.

        :param point:   (x, y) in plot units
        :return:        DrawToolShape or None
        """
        candidates = AppGeoEditor.pick_candidates(self, point)
        return candidates[0] if candidates else None

    def pick_candidates(self, point):
        """Nearest contour ties, ordered by distance, area, then stable storage order."""
        tree, refs = self.get_pick_index()
        if tree is None:
            return []

        try:
            tol = PICK_TOLERANCE_PX * self.canvas.pixel_size()
        except Exception:
            tol = None

        if tol:
            found, dists = tree.query_nearest(Point(point[0], point[1]), max_distance=tol, return_distance=True,
                                              all_matches=True)
        else:
            found, dists = tree.query_nearest(Point(point[0], point[1]), return_distance=True, all_matches=True)
        ordered = sorted(zip(found, dists),
                         key=lambda item: (round(float(item[1]), 9), refs[int(item[0])].geo.area, int(item[0])))
        return [refs[int(index)] for index, _distance in ordered]

    def pick_cycle_at(self, point):
        candidates = AppGeoEditor.pick_candidates(self, point)
        if not candidates:
            self._pick_cycle = None
            return None, None, 0, 0
        ids = tuple(id(shape) for shape in candidates)
        state = (self.storage, self.storage.revision)
        last = getattr(self, '_pick_cycle', None)
        position, previous = 0, None
        if last is not None and len(candidates) > 1 and last['ids'] == ids and last['state'] == state:
            pixel_size = 0
            try:
                pixel_size = self.canvas.pixel_size()
                moved = math.hypot(point[0] - last['point'][0], point[1] - last['point'][1]) / pixel_size
            except (AttributeError, TypeError, ZeroDivisionError):
                moved = float('inf')
            if pixel_size and pixel_size > 0 and moved < CLICK_DRAG_THRESHOLD_PX:
                position = (last['position'] + 1) % len(candidates)
                previous = candidates[last['position']]
        self._pick_cycle = {'point': tuple(point), 'ids': ids, 'state': state, 'position': position}
        return candidates[position], previous, position, len(candidates)

    def click_select(self, point):
        """
        Selects the shape under a click. With the multiple selection key, the click adds the shape to the
        selection or removes it from the selection; without the key, the shape is the new selection. A click where
        there is no shape clears the selection. The tree rows and the info fields follow the selection.

        :param point:   (x, y) in plot units
        :return:        False if a selection is not allowed now, else True
        """
        if self.interdict_selection is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selection not allowed. Wait ..."))
            return False

        obj_to_add, previous, position, count = AppGeoEditor.pick_cycle_at(self, point)
        if obj_to_add is None:
            self.selected = []
        else:
            key_modifier = QtWidgets.QApplication.keyboardModifiers()
            if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
                mod_key = 'Shift'
            elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
                mod_key = 'Control'
            else:
                mod_key = None

            if mod_key == self.app.options.get(
                    "global_mselect_key",
                    self.app.defaults.get(
                        "global_mselect_key",
                        self.app.defaults.factory_defaults.get("global_mselect_key")
                    )):
                if previous is not None:
                    self.selected = [shape for shape in self.selected if shape is not previous]
                    if not any(shape is obj_to_add for shape in self.selected):
                        self.selected.append(obj_to_add)
                # A single candidate still toggles with the multiple-selection key.
                elif any(shape is obj_to_add for shape in self.selected):
                    self.selected = [shape for shape in self.selected if shape is not obj_to_add]
                else:
                    self.selected.append(obj_to_add)
            else:
                self.selected = [obj_to_add]
            if count > 1:
                self.app.inform.emit('%s %d / %d. %s' % (
                    _("Shape"), position + 1, count, _("Click again at the same place to select the next one.")))

        self.sync_tree_selection()
        self.update_ui()
        return True

    def shapes_by_tree_id(self):
        """
        :return: dict of the tree row ID (the str of id(shape)) to the shape, for all the shapes of the storage
        """
        return {str(id(obj_shape)): obj_shape for obj_shape in self.storage.get_objects()}

    def update_ui(self, current_item: QtWidgets.QTreeWidgetItem = None):
        # this only fills the info fields; it does not change the selection
        last_obj_shape = None
        last_id = None

        shapes_by_id = self.shapes_by_tree_id()
        if current_item:
            last_id = current_item.text(0)
            last_obj_shape = shapes_by_id.get(last_id)
        else:
            for sel in self.ui.tw.selectedItems():
                if sel.text(0) in shapes_by_id:
                    last_id = sel.text(0)
                    last_obj_shape = shapes_by_id[last_id]

        if last_obj_shape:
            last_sel_geo = last_obj_shape.geo

            self.ui.is_valid_entry.set_value(last_sel_geo.is_valid)
            self.ui.is_empty_entry.set_value(last_sel_geo.is_empty)

            if last_sel_geo.geom_type == 'MultiLineString':
                length = last_sel_geo.length
                self.ui.is_simple_entry.set_value(last_sel_geo.is_simple)
                self.ui.is_ring_entry.set_value(last_sel_geo.is_ring)
                self.ui.is_ccw_entry.set_value('None')

                coords = ''
                vertex_nr = 0
                for idx, line in enumerate(last_sel_geo.geoms):
                    line_coords = list(line.coords)
                    vertex_nr += len(line_coords)
                    coords += 'Line %s\n' % str(idx)
                    coords += str(line_coords) + '\n'
            elif last_sel_geo.geom_type == 'MultiPolygon':
                length = 0.0
                self.ui.is_simple_entry.set_value('None')
                self.ui.is_ring_entry.set_value('None')
                self.ui.is_ccw_entry.set_value('None')

                coords = ''
                vertex_nr = 0
                for idx, poly in enumerate(last_sel_geo.geoms):
                    poly_coords = list(poly.exterior.coords) + [list(i.coords) for i in poly.interiors]
                    vertex_nr += len(poly_coords)

                    coords += 'Polygon %s\n' % str(idx)
                    coords += str(poly_coords) + '\n'
            elif last_sel_geo.geom_type in ['LinearRing', 'LineString']:
                length = last_sel_geo.length
                coords = list(last_sel_geo.coords)
                vertex_nr = len(coords)
                self.ui.is_simple_entry.set_value(last_sel_geo.is_simple)
                self.ui.is_ring_entry.set_value(last_sel_geo.is_ring)
                if last_sel_geo.geom_type == 'LinearRing':
                    self.ui.is_ccw_entry.set_value(last_sel_geo.is_ccw)
            elif last_sel_geo.geom_type == 'Polygon':
                length = last_sel_geo.exterior.length
                coords = list(last_sel_geo.exterior.coords)
                vertex_nr = len(coords)
                self.ui.is_simple_entry.set_value(last_sel_geo.is_simple)
                self.ui.is_ring_entry.set_value(last_sel_geo.is_ring)
                if last_sel_geo.exterior.geom_type == 'LinearRing':
                    self.ui.is_ccw_entry.set_value(last_sel_geo.exterior.is_ccw)
            else:
                length = 0.0
                coords = 'None'
                vertex_nr = 0

            if self.ui.geo_zoom.get_value():
                xmin, ymin, xmax, ymax = last_sel_geo.bounds
                if xmin == xmax and ymin != ymax:
                    xmin = ymin
                    xmax = ymax
                elif xmin != xmax and ymin == ymax:
                    ymin = xmin
                    ymax = xmax

                rect = Rect(xmin, ymin, xmax, ymax)
                rect.left, rect.right = xmin, xmax
                rect.bottom, rect.top = ymin, ymax

                # Lock updates in other threads
                assert isinstance(self.shapes, ShapeCollection)
                self.shapes.lock_updates()

                assert isinstance(self.sel_shapes, ShapeCollection)
                self.sel_shapes.lock_updates()

                # adjust the view camera to be slightly bigger than the bounds so the shape collection can be
                # seen clearly otherwise the shape collection boundary will have no border
                dx = rect.right - rect.left
                dy = rect.top - rect.bottom
                x_factor = dx * 0.02
                y_factor = dy * 0.02

                rect.left -= x_factor
                rect.bottom -= y_factor
                rect.right += x_factor
                rect.top += y_factor

                self.app.plotcanvas.view.camera.rect = rect
                self.shapes.unlock_updates()
                self.sel_shapes.unlock_updates()

            self.ui.geo_len_entry.set_value(length, decimals=self.decimals)
            self.ui.geo_coords_entry.setText(str(coords))
            self.ui.geo_vertex_entry.set_value(vertex_nr)

            self.app.inform.emit('%s: %s' % (_("Last selected shape ID"), str(last_id)))

    def on_tree_geo_click(self, current_item, prev_item):
        try:
            self.update_ui(current_item=current_item)
            # self.plot_all()
        except Exception as e:
            self.app.log.error("APpGeoEditor.on_tree_selection_change() -> %s" % str(e))

    def on_tree_selection(self):
        if getattr(self, 'interdict_selection', False):
            return
        selected_items = self.ui.tw.selectedItems()

        if len(selected_items) == 0:
            self.ui.is_valid_entry.set_value("None")
            self.ui.is_empty_entry.set_value("None")
            self.ui.is_simple_entry.set_value("None")
            self.ui.is_ring_entry.set_value("None")
            self.ui.is_ccw_entry.set_value("None")
            self.ui.geo_len_entry.set_value("None")
            self.ui.geo_coords_entry.setText("None")
            self.ui.geo_vertex_entry.set_value("")

        if len(selected_items) >= 1:
            shapes_by_id = self.shapes_by_tree_id()
            self.selected = [shapes_by_id[sel.text(0)] for sel in selected_items if sel.text(0) in shapes_by_id]
            self.plot_changes()
            self.update_all_vertex_count()

    def update_all_vertex_count(self):
        """
        Shows the number of vertices of all the selected shapes. The polygons count the vertices of all their rings.
        """
        total_geos = flatten_shapely_geometry([s.geo for s in self.selected])
        total_vtx = int(shapely.get_num_coordinates(total_geos).sum()) if total_geos else 0
        self.ui.geo_all_vertex_entry.set_value(str(total_vtx))

    def sync_tree_selection(self):
        """
        Selects in the tree the rows of the selected shapes, in 1 pass over the rows. The tree signals are blocked,
        so the tree handlers (and their replots) do not run for each row.
        """
        tw = self.ui.tw
        sel_ids = {str(id(sel_shape)) for sel_shape in self.selected}

        tw.blockSignals(True)
        try:
            tw.selectionModel().clearSelection()
            if sel_ids:
                iterator = QtWidgets.QTreeWidgetItemIterator(tw)
                while iterator.value():
                    item = iterator.value()
                    if item.text(0) in sel_ids:
                        item.setSelected(True)
                    iterator += 1
        finally:
            tw.blockSignals(False)
        show_selected_rows(tw)

    def vertex_edit_target(self, target=None):
        """Vertex editing never guesses a selected contour: exactly 1 selected shape."""
        if len(self.selected) != 1:
            raise ValueError(_("Select exactly one shape to edit vertices."))
        shape = self.selected[0]
        if (target is not None and shape is not target) or id(shape) not in self.storage.indexes:
            raise ValueError(_("The selected shape changed. Reopen the vertex editor."))
        if not isinstance(shape.geo, SUPPORTED_TYPES) or shape.geo.is_empty:
            raise ValueError(_("Vertex editing supports LineString, LinearRing, Polygon and their multipart shapes.\n"
                               "Collections are not supported."))
        if shape.geo.has_z:
            raise ValueError(_("Vertex editing supports two-dimensional coordinates only."))
        return shape

    @geometry_operation
    def apply_vertex_edit(self, action, index, point=None, target=None, on_segment=True):
        """
        Validate a complete replacement before touching storage or recording history.

        :param action:      'move', 'insert', 'remove' or an arc action of vertex_ops.ARC_ACTIONS
        :param index:       a vertex index of a single contour, or a (part, ring, index) / (part, ring, start, end)
                            address
        :param on_segment:  True (the dialog): an inserted vertex must lie inside the chosen segment
        """
        try:
            shape = AppGeoEditor.vertex_edit_target(self, target)
            geometry = shape.geo
            address = vertex_address(geometry, index)
            # the steps of an arc follow the preference "Circles" and the radius of the arc
            result_geo = edit_vertex(geometry, action, address, point, on_segment=on_segment,
                                     steps=lambda radius: object_circle_steps(self.app.options, 'geometry', radius,
                                                                              self.app.app_units))
            check_vertex_result(result_geo, address)
        except (ValueError, TypeError, OverflowError, shapely.errors.GEOSException) as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return 'fail'

        if result_geo.equals_exact(geometry, 0.0):
            return
        result = DrawToolShape(result_geo)
        result.data = shape.data.copy()
        result.data['bounds'] = result.bounds()
        if self.replace_shapes(removed=[shape], inserted=[result], selected=[result]) == 'fail':
            return 'fail'
        self.build_ui_sig.emit()
        self.plot_changes()

    @geometry_operation
    def apply_selection_drag(self, geometries, offset=None):
        """Commit all segment groups (or a whole-object translation) as one validated replacement."""
        try:
            if any(id(shape) not in self.storage.indexes for shape in geometries):
                raise ValueError(_("Geometry changed during the drag. Select the shapes again."))
            if offset is not None and offset == (0, 0):
                return
            shapes = list(geometries)
            values = translate_geometry([shape.geo for shape in shapes], *offset) if offset is not None else \
                list(geometries.values())
            replacements = {}
            for shape, geometry in zip(shapes, values):
                if geometry.equals_exact(shape.geo, 0.0):
                    continue
                if geometry.is_empty or not geometry.is_valid:
                    raise ValueError(_("The drag would create invalid geometry."))
                result = DrawToolShape(geometry)
                result.data = deepcopy(shape.data)
                result.data['bounds'] = result.bounds()
                replacements[shape] = result
        except (ValueError, TypeError, OverflowError, shapely.errors.GEOSException) as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return 'fail'
        if not replacements:
            return
        selected = [replacements.get(shape, shape) for shape in self.selected]
        if self.replace_shapes(removed=replacements, inserted=replacements.values(), selected=selected) == 'fail':
            return 'fail'
        self.build_ui_sig.emit()
        self.plot_changes()

    def on_edit_vertices(self):
        if AppGeoEditor.reject_busy(self):
            return 'fail'
        try:
            target = AppGeoEditor.vertex_edit_target(self)
        except ValueError as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return 'fail'

        dialog = QtWidgets.QDialog(self.ui.geo_edit_widget)
        dialog.setWindowTitle(_("Edit vertices"))
        grid = GLay(v_spacing=5, h_spacing=3)
        dialog.setLayout(grid)
        hint = FCLabel(_("Indexes start at 0. Ring 0 is the line or the polygon exterior; rings 1+ are the holes.\n"
                         "Insert uses the segment from index to index + 1.\n"
                         "The closing coordinate is an alias of vertex 0."))
        grid.addWidget(hint, 0, 0, 1, 2)
        part_entry, ring_entry, index_entry = QtWidgets.QSpinBox(), QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        part_entry.setObjectName('vertex_part')
        ring_entry.setObjectName('vertex_ring')
        index_entry.setObjectName('vertex_index')
        x_entry, y_entry = FCEntry(), FCEntry()
        x_entry.setObjectName('vertex_x')
        y_entry.setObjectName('vertex_y')
        action_entry = QtWidgets.QComboBox()
        for label, action in [(_("Move"), 'move'), (_("Insert"), 'insert'), (_("Remove"), 'remove')]:
            action_entry.addItem(label, action)
        for row, (label, widget) in enumerate([(_("Part"), part_entry), (_("Ring"), ring_entry),
                                                (_("Vertex / segment index"), index_entry),
                                                ('X', x_entry), ('Y', y_entry),
                                                (_("Operation"), action_entry)], 1):
            grid.addWidget(FCLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
        apply_btn, close_btn = FCButton(_("Apply")), FCButton(_("Close"))
        apply_btn.setObjectName('vertex_apply')
        grid.addWidget(apply_btn, 7, 0)
        grid.addWidget(close_btn, 7, 1)

        def load_vertex():
            parts = parts_of(target.geo)
            part_entry.setMaximum(len(parts) - 1)
            rings = rings_of(parts[part_entry.value()])
            ring_entry.setMaximum(len(rings) - 1)
            coords = list(rings[ring_entry.value()].coords)
            index_entry.setMaximum(len(coords) - 1)
            x_entry.setText(str(coords[index_entry.value()][0]))
            y_entry.setText(str(coords[index_entry.value()][1]))

        def apply():
            nonlocal target
            address = (part_entry.value(), ring_entry.value(), index_entry.value())
            if self.apply_vertex_edit(action_entry.currentData(), address,
                                      (x_entry.text(), y_entry.text()), target=target) == 'fail':
                return
            target = self.selected[0]
            load_vertex()

        def change_action():
            for entry in (x_entry, y_entry):
                entry.setEnabled(action_entry.currentData() != 'remove')

        index_entry.valueChanged.connect(load_vertex)
        part_entry.valueChanged.connect(load_vertex)
        ring_entry.valueChanged.connect(load_vertex)
        action_entry.currentIndexChanged.connect(change_action)
        apply_btn.clicked.connect(apply)
        close_btn.clicked.connect(dialog.reject)
        load_vertex()
        dialog.exec()
        dialog.deleteLater()

    @geometry_operation
    def apply_path_split(self, point, tolerance, target=None, session=None):
        """Project a click and replace one open path with two directed substrings."""
        try:
            if len(self.selected) != 1:
                raise ValueError(_("Select exactly one open LineString to split."))
            shape = self.selected[0]
            stored_index = self.storage.indexes.get(id(shape))
            if ((target is not None and shape is not target) or stored_index is None or
                    self.storage.objects[stored_index] is not shape or
                    (session is not None and session != self._history_session)):
                raise ValueError(_("The selected path or editing session changed. Reopen Split path."))
            geometry = shape.geo
            if (type(geometry) is not LineString or geometry.is_empty or geometry.is_closed or
                    geometry.has_z or not geometry.is_valid or not math.isfinite(geometry.length)):
                raise ValueError(_("Split supports one two-dimensional open LineString only.\n"
                                   "Rings, polygons and multipart shapes are not supported."))
            tolerance = float(tolerance)
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError(_("Split tolerance must be a finite, nonnegative distance in current units."))
            if point is None or len(point) != 2:
                raise ValueError(_("Choose a finite X and Y split point."))
            point = tuple(float(value) for value in point)
            if not all(math.isfinite(value) for value in point):
                raise ValueError(_("Choose a finite X and Y split point."))
            candidate = Point(point)
            if geometry.distance(candidate) > tolerance:
                raise ValueError(_("The split point is outside the path tolerance."))
            position = geometry.project(candidate)
            if position <= tolerance or geometry.length - position <= tolerance:
                raise ValueError(_("Choose a split point farther than the tolerance from both path endpoints."))
            pieces = [substring(geometry, 0, position), substring(geometry, position, geometry.length)]
            if any(type(piece) is not LineString or piece.length <= 0 or not piece.is_valid for piece in pieces):
                raise ValueError(_("The split must produce two nonzero-length paths."))
        except (ValueError, TypeError, OverflowError, shapely.errors.GEOSException) as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return 'fail'

        results = []
        for piece in pieces:
            result = DrawToolShape(piece)
            result.data = shape.data.copy()
            result.data['bounds'] = result.bounds()
            results.append(result)
        if self.replace_shapes(removed=[shape], inserted=results, selected=results) == 'fail':
            return 'fail'
        self.build_ui_sig.emit()
        self.plot_changes()

    def on_join_paths(self):
        return self.apply_path_join(self.ui.join_tolerance_entry.text())

    @geometry_operation
    def apply_path_join(self, tolerance):
        """Merge unique endpoint pairs; near pairs gain a bridge, not moved vertices."""
        try:
            tolerance = float(tolerance)
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError(_("Join tolerance must be a finite, nonnegative distance in current units."))
            shapes = list(self.selected)
            if len(shapes) < 2 or len({id(shape) for shape in shapes}) != len(shapes):
                raise ValueError(_("Select at least two distinct open LineStrings to join."))
            endpoints = []
            for shape in shapes:
                stored_index = self.storage.indexes.get(id(shape))
                if stored_index is None or self.storage.objects[stored_index] is not shape:
                    raise ValueError(_("The selected paths changed. Select stored paths again."))
                geometry = shape.geo
                if (type(geometry) is not LineString or geometry.is_empty or geometry.is_closed or
                        geometry.has_z or not geometry.is_valid or not geometry.is_simple or
                        not math.isfinite(geometry.length) or geometry.length <= 0 or
                        not all(math.isfinite(v) for coord in geometry.coords for v in coord)):
                    raise ValueError(_("Join supports two-dimensional, simple open LineStrings only."))
                endpoints.extend([geometry.coords[0], geometry.coords[-1]])

            # Only selected endpoints participate. No transitive welding or nearest-choice guessing.
            points = [Point(coord) for coord in endpoints]
            tree = STRtree(points)
            pairs = []
            adjacency = [set() for shape in shapes]
            for i, point in enumerate(points):
                candidates = [int(j) for j in tree.query(point, predicate='dwithin', distance=tolerance)
                              if int(j) // 2 != i // 2]
                if len(candidates) > 1:
                    raise ValueError(_("Ambiguous or branching endpoints. Reduce tolerance or selection."))
                if candidates and i < candidates[0]:
                    j = candidates[0]
                    pairs.append((i, j))
                    adjacency[i // 2].add(j // 2)
                    adjacency[j // 2].add(i // 2)
            if not pairs:
                raise ValueError(_("No selected endpoints are within the join tolerance."))

            removed, inserted, selected = [], [], []
            visited = set()
            bridge_length = 0.0
            for start in range(len(shapes)):
                if start in visited:
                    continue
                component, pending = set(), [start]
                while pending:
                    index = pending.pop()
                    if index in component:
                        continue
                    component.add(index)
                    pending.extend(adjacency[index] - component)
                visited.update(component)
                members = [shapes[index] for index in sorted(component)]
                if len(members) == 1:
                    selected.extend(members)
                    continue
                metadata = {key: value for key, value in members[0].data.items() if key != 'bounds'}
                if any({key: value for key, value in member.data.items() if key != 'bounds'} != metadata
                       for member in members[1:]):
                    raise ValueError(_("Joined paths must have compatible metadata and tool ownership."))
                lines = [member.geo for member in members]
                added_length = 0.0
                for i, j in pairs:
                    if i // 2 in component and endpoints[i] != endpoints[j]:
                        bridge = LineString([endpoints[i], endpoints[j]])
                        lines.append(bridge)
                        added_length += bridge.length
                geometry = linemerge(lines)
                expected_length = sum(member.geo.length for member in members) + added_length
                if (type(geometry) is not LineString or geometry.is_empty or not geometry.is_valid or
                        not geometry.is_simple or not math.isfinite(geometry.length) or
                        not math.isclose(geometry.length, expected_length, rel_tol=1e-12, abs_tol=1e-12)):
                    raise ValueError(_("Join would create branching, overlapping or invalid geometry."))
                result = DrawToolShape(geometry)
                result.data = members[0].data.copy()
                result.data['bounds'] = result.bounds()
                removed.extend(members)
                inserted.append(result)
                selected.append(result)
                bridge_length += added_length
            if not MultiLineString([shape.geo for shape in selected]).is_simple:
                raise ValueError(_("Joined components would intersect or branch. Refine the selection."))
        except (ValueError, TypeError, OverflowError, shapely.errors.GEOSException) as err:
            self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
            return 'fail'

        if self.replace_shapes(removed=removed, inserted=inserted, selected=selected) == 'fail':
            return 'fail'
        self.build_ui_sig.emit()
        self.plot_changes()
        self.app.inform.emit(_("Join complete: %d separate components; straight bridges added %s %s of length.") %
                             (len(selected), bridge_length, self.app.app_units))

    def diagnose_selected(self, tolerance):
        """Inspect selected contours only; never enter a geometry/history operation."""
        with AppGeoEditor.history_access(self) as allowed:
            if not allowed:
                return 'fail'
            try:
                tolerance = float(tolerance)
                if not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                self.app.inform.emit(_('[WARNING_NOTCL] Endpoint tolerance must be finite and non-negative.'))
                return 'fail'
            if not self.selected:
                self.app.inform.emit(_('[WARNING_NOTCL] Select geometry to inspect.'))
                return 'fail'

            report = {key: [] for key in ('empty', 'zero_length', 'duplicate', 'invalid',
                                          'open', 'endpoints', 'near_endpoints')}
            paths = {}

            def mark(key, shape):
                if shape not in report[key]:
                    report[key].append(shape)

            def inspect(geometry, shape):
                if isinstance(geometry, (list, tuple)):
                    if not geometry:
                        mark('empty', shape)
                    for part in geometry:
                        inspect(part, shape)
                    return
                if geometry.is_empty:
                    mark('empty', shape)
                    return
                if not geometry.is_valid or not geometry.is_simple:
                    mark('invalid', shape)
                if isinstance(geometry, Polygon):
                    inspect(geometry.exterior, shape)
                    for ring in geometry.interiors:
                        inspect(ring, shape)
                elif hasattr(geometry, 'geoms'):
                    for part in geometry.geoms:
                        inspect(part, shape)
                elif isinstance(geometry, (LineString, LinearRing)):
                    if geometry.length == 0:
                        mark('zero_length', shape)
                        return
                    # Normalize direction without modifying the stored path.
                    key = shapely.normalize(LineString(geometry.coords)).wkb
                    if key in paths:
                        mark('duplicate', paths[key])
                        mark('duplicate', shape)
                    else:
                        paths[key] = shape
                    if not geometry.is_closed:
                        mark('open', shape)
                        report['endpoints'].extend((shape, tuple(geometry.coords[index])) for index in (0, -1))

            for shape in self.selected:
                inspect(shape.geo, shape)
            endpoints = report['endpoints']
            # Selected-only explicit action, not a mouse-move scan. STRtree bounds endpoint comparisons.
            if endpoints:
                points = [Point(coords[:2]) for shape, coords in endpoints]
                tree = STRtree(points)
                for index, point in enumerate(points):
                    for other in tree.query(point, predicate='dwithin', distance=tolerance):
                        if other > index:
                            report['near_endpoints'].append((endpoints[index], endpoints[other]))
            return report

    def on_diagnostics(self):
        report = AppGeoEditor.diagnose_selected(self, self.ui.diagnostics_tolerance_entry.text())
        if report == 'fail':
            return 'fail'
        labels = [('empty', _('Empty geometry')), ('zero_length', _('Zero-length paths')),
                  ('duplicate', _('Duplicate paths')), ('invalid', _('Invalid / self-intersecting')),
                  ('open', _('Open paths (information)'))]
        lines = [_('Selected geometry snapshot; rerun after edits or selection changes.')]
        by_shape = {}
        for key, label in labels:
            ids = ', '.join(str(id(shape)) for shape in report[key])
            lines.append('%s: %d%s' % (label, len(report[key]), ' — ' + ids if ids else ''))
            for shape in report[key]:
                by_shape.setdefault(str(id(shape)), []).append(label)
        lines.append(_('Open endpoints (information): %d') % len(report['endpoints']))
        for shape, coords in report['endpoints']:
            lines.append('%s: %s' % (id(shape), coords))
        lines.append(_('Near endpoint pairs: %d') % len(report['near_endpoints']))
        for (first, a), (second, b) in report['near_endpoints']:
            lines.append('%s %s — %s %s' % (id(first), a, id(second), b))
        self.ui.diagnostics_result.setText('\n'.join(lines))
        self.ui.diagnostics_result.show()
        iterator = QtWidgets.QTreeWidgetItemIterator(self.ui.tw)
        while iterator.value():
            item = iterator.value()
            item.setToolTip(1, '\n'.join(by_shape.get(item.text(0), [])))
            iterator += 1
        self.app.inform.emit(_('[INFO_NOTCL] Selected geometry diagnostics updated (read-only).'))
        return report

    def size_filter_matches(self, whole_board=False):
        """
        Shapes matching the enabled Select by Size filters. Read-only: no selection or history change.
        Select searches the whole board (whole_board=True), so repeating it never only narrows the selection.
        Delete acts on the selected shapes if any, else on all the shapes of the edited object.

        :return: list of DrawToolShape, or 'fail' (a warning was shown) for bad input
        """
        with AppGeoEditor.history_access(self) as allowed:
            if not allowed:
                return 'fail'
            ranges = {}
            for key, check, low_entry, high_entry, name in (
                    ('area', self.ui.size_area_chk, self.ui.size_min_area_entry, self.ui.size_max_area_entry,
                     _("area")),
                    ('length', self.ui.size_length_chk, self.ui.size_min_length_entry,
                     self.ui.size_max_length_entry, _("length"))):
                if not check.isChecked():
                    continue
                try:
                    low = float(low_entry.text().strip().replace(',', '.'))
                    high = float(high_entry.text().strip().replace(',', '.'))
                    if not (math.isfinite(low) and math.isfinite(high)) or low < 0 or high < 0:
                        raise ValueError
                except (ValueError, TypeError, OverflowError):
                    self.app.inform.emit('[WARNING_NOTCL] %s %s' % (
                        _("Invalid limits. Enter finite, non-negative numbers for the"), name))
                    return 'fail'
                if low > high:
                    self.app.inform.emit('[WARNING_NOTCL] %s %s' % (
                        _("The minimum is larger than the maximum for the"), name))
                    return 'fail'
                ranges[key] = (low, high)
            if not ranges:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Enable the area or the length filter."))
                return 'fail'
            if self.selected and not whole_board:
                scope = list(self.selected)
            else:
                scope = list(self.storage.get_objects())
            return shapes_in_size_range(scope, ranges.get('area'), ranges.get('length'))

    def on_select_by_size(self):
        matches = AppGeoEditor.size_filter_matches(self, whole_board=True)
        if matches == 'fail':
            return 'fail'
        self.selected = matches
        AppGeoEditor.sync_tree_selection(self)
        AppGeoEditor.update_all_vertex_count(self)
        self.plot_changes()
        self.app.inform.emit(_("{n} shapes match").format(n=len(matches)))
        return matches

    def on_delete_by_size(self):
        matches = AppGeoEditor.size_filter_matches(self)
        if matches == 'fail':
            return 'fail'
        if not matches:
            self.app.inform.emit(_("{n} shapes match").format(n=0))
            return matches
        previous = self.selected
        self.selected = list(matches)
        # one history step: delete_selected is a single geometry operation
        if AppGeoEditor.delete_selected(self) == 'fail':
            self.selected = previous
            return 'fail'
        self.app.inform.emit(_("{n} shapes deleted").format(n=len(matches)))
        return matches

    def on_resize_circles(self):
        return AppGeoEditor.apply_resize_circles(self, self.ui.resize_circles_entry.get_value())

    @geometry_operation
    def apply_resize_circles(self, diameter):
        """Set the diameter of the selected circles, each about its own center, as one history step."""
        try:
            diameter = float(diameter)
        except (ValueError, TypeError, OverflowError):
            diameter = float('nan')
        if not math.isfinite(diameter) or diameter <= 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The new diameter must be a positive number."))
            return 'fail'
        removed, inserted, replacement = [], [], {}
        skipped = 0
        for shape in self.selected:
            geometry = resized_circle(shape.geo, diameter) if isinstance(shape.geo, BaseGeometry) else None
            if geometry is None:
                skipped += 1
                continue
            result = DrawToolShape(geometry)
            result.data = shape.data.copy()
            result.data['bounds'] = result.bounds()
            removed.append(shape)
            inserted.append(result)
            replacement[id(shape)] = result
        if not removed:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No circle selected."))
            return 'fail'
        selected = [replacement.get(id(shape), shape) for shape in self.selected]
        if self.replace_shapes(removed=removed, inserted=inserted, selected=selected) == 'fail':
            return 'fail'
        AppGeoEditor.update_all_vertex_count(self)
        self.build_ui_sig.emit()
        self.plot_changes()
        self.app.inform.emit(_("{n} circles resized, {m} shapes skipped").format(n=len(removed), m=skipped))
        return inserted

    def on_change_orientation(self):
        self.app.log.debug("AppGeoEditor.on_change_orientation()")

        shapes_by_id = self.shapes_by_tree_id()
        selected_shapes = [shapes_by_id.get(item.text(0)) for item in self.ui.tw.selectedItems()]

        def task_job():
            with self.app.proc_container.new('%s...' % _("Working")):
                processed_shapes = []
                new_shapes = []
                for obj_shape in selected_shapes:
                    if obj_shape is None:
                        continue
                    old_geo = obj_shape.geo
                    if isinstance(old_geo, (LinearRing, LineString)):
                        geometry = type(old_geo)(old_geo.coords[::-1])
                    elif isinstance(old_geo, Polygon):
                        geometry = orient(old_geo, -1 if old_geo.exterior.is_ccw else 1)
                    else:
                        continue
                    result = DrawToolShape(geometry)
                    result.data = obj_shape.data.copy()
                    processed_shapes.append(obj_shape)
                    new_shapes.append(result)

                self.delete_shape(processed_shapes, inserted=new_shapes)
                self.build_ui_sig.emit()
                self.plot_changes()

        AppGeoEditor.submit_worker(self, task_job, [])

    def on_menu_request(self, pos):
        menu = QtWidgets.QMenu()

        delete_action = menu.addAction(QtGui.QIcon(self.app.resource_location + '/delete32.png'), _("Delete"))
        delete_action.triggered.connect(self.delete_selected)

        menu.addSeparator()

        orientation_change = menu.addAction(QtGui.QIcon(self.app.resource_location + '/orientation32.png'),
                                            _("Change"))
        orientation_change.triggered.connect(self.on_change_orientation)

        if not self.ui.tw.selectedItems():
            delete_action.setDisabled(True)
            orientation_change.setDisabled(True)

        menu.exec(self.ui.tw.viewport().mapToGlobal(pos))

    def activate(self):
        if AppGeoEditor.reset_history(self) == 'fail':
            return 'fail'
        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(True)
        self.app.ui.menueditok.setDisabled(False)
        self.app.ui.on_history_changed('', '')

        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(False)
        self.app.ui.popmenu_save.setVisible(True)

        self.connect_canvas_event_handlers()

        # initialize working objects
        self.storage = self.make_storage()
        self.utility = []
        self.selected = []

        self.shapes.enabled = True
        self.sel_shapes.enabled = True
        self.tool_shape.enabled = True
        self.app.app_cursor.enabled = True

        self.app.ui.corner_snap_btn.setVisible(True)
        self.app.ui.snap_magnet.setVisible(True)

        self.app.ui.geo_editor_menu.setDisabled(False)
        self.app.ui.geo_editor_menu.menuAction().setVisible(True)

        self.app.ui.editor_exit_btn_ret_action.setVisible(True)
        self.app.ui.editor_start_btn.setVisible(False)
        self.app.ui.g_editor_cmenu.setEnabled(True)

        self.app.ui.geo_edit_toolbar.setDisabled(False)
        self.app.ui.geo_edit_toolbar.setVisible(True)

        self.app.ui.status_toolbar.setDisabled(False)

        self.app.ui.pop_menucolor.menuAction().setVisible(False)
        self.app.ui.popmenu_numeric_move.setVisible(False)
        self.app.ui.popmenu_move2origin.setVisible(False)

        self.app.ui.popmenu_disable.setVisible(False)
        self.app.ui.cmenu_newmenu.menuAction().setVisible(False)
        self.app.ui.popmenu_properties.setVisible(False)
        self.app.ui.g_editor_cmenu.menuAction().setVisible(True)

        # prevent the user to change anything in the Properties Tab while the Geo Editor is active
        # sel_tab_widget_list = self.app.ui.properties_tab.findChildren(QtWidgets.QWidget)
        # for w in sel_tab_widget_list:
        #     w.setEnabled(False)

        self.item_selected.connect(self.on_geo_elem_selected)

        # ## appGUI Events
        self.ui.tw.currentItemChanged.connect(self.on_tree_geo_click)
        self.ui.tw.itemSelectionChanged.connect(self.on_tree_selection)

        # self.ui.tw.keyPressed.connect(self.app.ui.keyPressEvent)
        # self.ui.tw.customContextMenuRequested.connect(self.on_menu_request)

        self.ui.geo_frame.show()

        self.app.log.debug("Finished activating the Geometry Editor...")

    def deactivate(self):
        if AppGeoEditor.reset_history(self) == 'fail':
            return 'fail'
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # adjust the status of the menu entries related to the editor
        self.app.ui.menueditedit.setDisabled(False)
        self.app.ui.menueditok.setDisabled(True)
        history_of(self.app).notify()

        # adjust the visibility of some of the canvas context menu
        self.app.ui.popmenu_edit.setVisible(True)
        self.app.ui.popmenu_save.setVisible(False)

        self.disconnect_canvas_event_handlers()
        self.storage = self.make_storage()

        grips = getattr(self, 'vertex_grips', None)
        if grips is not None:
            grips.hide()
            grips.segment_drag.clear()
        self.clear()
        self.app.ui.geo_edit_toolbar.setDisabled(True)

        self.app.ui.corner_snap_btn.setVisible(False)
        self.app.ui.snap_magnet.setVisible(False)

        # set the Editor Toolbar visibility to what was before entering the Editor
        self.app.ui.geo_edit_toolbar.setVisible(False) if self.toolbar_old_state is False \
            else self.app.ui.geo_edit_toolbar.setVisible(True)

        # Disable visuals
        self.shapes.enabled = False
        self.sel_shapes.enabled = False
        self.tool_shape.enabled = False

        # disable text cursor (for FCPath)
        self.app.plotcanvas.text_cursor.parent = None
        self.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        self.app.ui.geo_editor_menu.setDisabled(True)
        self.app.ui.geo_editor_menu.menuAction().setVisible(False)

        self.app.ui.editor_exit_btn_ret_action.setVisible(False)
        self.app.ui.editor_start_btn.setVisible(True)

        self.app.ui.g_editor_cmenu.setEnabled(False)
        self.app.ui.e_editor_cmenu.setEnabled(False)

        self.app.ui.pop_menucolor.menuAction().setVisible(True)
        self.app.ui.popmenu_numeric_move.setVisible(True)
        self.app.ui.popmenu_move2origin.setVisible(True)

        self.app.ui.popmenu_disable.setVisible(True)
        self.app.ui.cmenu_newmenu.menuAction().setVisible(True)
        self.app.ui.popmenu_properties.setVisible(True)
        self.app.ui.grb_editor_cmenu.menuAction().setVisible(False)
        self.app.ui.e_editor_cmenu.menuAction().setVisible(False)
        self.app.ui.g_editor_cmenu.menuAction().setVisible(False)

        try:
            self.item_selected.disconnect()
        except (AttributeError, TypeError, RuntimeError):
            pass

        try:
            # ## appGUI Events
            self.ui.tw.currentItemChanged.disconnect(self.on_tree_geo_click)
            # self.ui.tw.keyPressed.connect(self.app.ui.keyPressEvent)
            # self.ui.tw.customContextMenuRequested.connect(self.on_menu_request)
        except (AttributeError, TypeError, RuntimeError):
            pass

        try:
            self.ui.tw.itemSelectionChanged.disconnect(self.on_tree_selection)
        except (AttributeError, TypeError, RuntimeError):
            pass

        # try:
        #     # re-enable all the widgets in the Selected Tab that were disabled after entering in Edit Geometry Mode
        #     sel_tab_widget_list = self.app.ui.properties_tab.findChildren(QtWidgets.QWidget)
        #     for w in sel_tab_widget_list:
        #         w.setEnabled(True)
        # except Exception as e:
        #     self.app.log.error("AppGeoEditor.deactivate() --> %s" % str(e))

        # Show original geometry
        try:
            if self.fcgeometry:
                self.fcgeometry.visible = True

            # clear the Tree
            self.clear_tree_sig.emit()
        except Exception as err:
            self.app.log.error("AppGeoEditor.deactivate() --> %s" % str(err))

        # hide the UI
        self.ui.geo_frame.hide()

        self.app.log.debug("Finished deactivating the Geometry Editor...")

    def connect_canvas_event_handlers(self):
        # Canvas events

        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.canvas.graph_event_connect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_connect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_connect('mouse_release', self.on_canvas_click_release)

        # make sure that the shortcuts key and mouse events will no longer be linked to the methods from FlatCAMApp
        # but those from AppGeoEditor
        self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_disconnect('mouse_double_click', self.app.on_mouse_double_click_over_plot)

        # self.app.collection.view.clicked.disconnect()
        self.app.ui.popmenu_copy.triggered.disconnect()
        self.app.ui.popmenu_delete.triggered.disconnect()
        self.app.ui.popmenu_move.triggered.disconnect()

        self.app.ui.popmenu_copy.triggered.connect(lambda: self.select_tool('copy'))
        self.app.ui.popmenu_delete.triggered.connect(self.on_delete_btn)
        self.app.ui.popmenu_move.triggered.connect(lambda: self.select_tool('move'))

        # Geometry Editor
        self.app.ui.draw_line.triggered.connect(self.draw_tool_path)
        self.app.ui.draw_rect.triggered.connect(self.draw_tool_rectangle)

        self.app.ui.draw_circle.triggered.connect(lambda: self.select_tool('circle'))
        self.app.ui.draw_poly.triggered.connect(lambda: self.select_tool('polygon'))
        self.app.ui.draw_arc.triggered.connect(lambda: self.select_tool('arc'))

        self.app.ui.draw_text.triggered.connect(lambda: self.select_tool('text'))
        self.app.ui.draw_simplification.triggered.connect(lambda: self.select_tool('simplification'))
        self.app.ui.draw_buffer.triggered.connect(lambda: self.select_tool('buffer'))
        self.app.ui.draw_paint.triggered.connect(lambda: self.select_tool('paint'))
        self.app.ui.draw_eraser.triggered.connect(lambda: self.select_tool('eraser'))

        self.app.ui.draw_union.triggered.connect(self.union)
        self.app.ui.draw_polygonize.triggered.connect(self.polygonize)
        self.app.ui.draw_intersect.triggered.connect(self.intersection)
        self.app.ui.draw_substract.triggered.connect(self.subtract)
        self.app.ui.draw_substract_alt.triggered.connect(self.subtract_2)

        self.app.ui.draw_cut.triggered.connect(self.cutpath)
        self.app.ui.draw_transform.triggered.connect(lambda: self.select_tool('transform'))

        self.app.ui.draw_move.triggered.connect(self.on_move)

    def disconnect_canvas_event_handlers(self):
        # we restore the key and mouse control to FlatCAMApp method
        # first connect to new, then disconnect the old handlers
        # don't ask why but if there is nothing connected I've seen issues
        self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.graph_event_connect('mouse_double_click', self.app.on_mouse_double_click_over_plot)
        # self.app.collection.view.clicked.connect(self.app.collection.on_mouse_down)

        self.canvas.graph_event_disconnect('mouse_press', self.on_canvas_click)
        self.canvas.graph_event_disconnect('mouse_move', self.on_canvas_move)
        self.canvas.graph_event_disconnect('mouse_release', self.on_canvas_click_release)

        try:
            self.app.ui.popmenu_copy.triggered.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.popmenu_delete.triggered.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.app.ui.popmenu_move.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        self.app.ui.popmenu_copy.triggered.connect(self.app.on_copy_command)
        self.app.ui.popmenu_delete.triggered.connect(self.app.on_delete)
        self.app.ui.popmenu_move.triggered.connect(self.app.obj_move)

        # Geometry Editor
        try:
            self.app.ui.draw_line.triggered.disconnect(self.draw_tool_path)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_rect.triggered.disconnect(self.draw_tool_rectangle)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_cut.triggered.disconnect(self.cutpath)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_move.triggered.disconnect(self.on_move)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_circle.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_poly.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_arc.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_text.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_simplification.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_buffer.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_paint.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_eraser.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_union.triggered.disconnect(self.union)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_polygonize.triggered.disconnect(self.polygonize)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_intersect.triggered.disconnect(self.intersection)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_substract.triggered.disconnect(self.subtract)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_substract_alt.triggered.disconnect(self.subtract_2)
        except (TypeError, AttributeError):
            pass

        try:
            self.app.ui.draw_transform.triggered.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def on_clear_tree(self):
        self.ui.tw.clearSelection()
        self.ui.tw.clear()
        self.ui.geo_parent = self.ui.tw.invisibleRootItem()

    def reject_busy(self):
        busy = getattr(self, '_source_save_pending', False) is True or (
            getattr(self, '_history_worker_pending', False) is True and
            getattr(self, '_history_worker_owner', None) != get_ident())
        if busy:
            AppGeoEditor.history_busy_message(self)
        return busy

    def history_busy_message(self):
        self.app.inform.emit('[WARNING_NOTCL] %s' %
                             _("Geometry operation in progress. Wait for it to finish, then retry."))

    @contextmanager
    def history_access(self, cancel_pending=False):
        """Never wait for worker compute on the GUI thread. Only queued jobs can be invalidated."""
        if getattr(self, '_source_save_pending', False) is True:
            AppGeoEditor.history_busy_message(self)
            yield False
            return
        if not hasattr(self, '_history_lock'):
            self._history_lock = RLock()
        pending = getattr(self, '_history_worker_pending', False) is True
        owner = getattr(self, '_history_worker_owner', None)
        if (pending and (owner != get_ident() or cancel_pending) and
                not (cancel_pending and owner is None)) or not self._history_lock.acquire(blocking=False):
            AppGeoEditor.history_busy_message(self)
            yield False
            return
        try:
            yield True
        finally:
            self._history_lock.release()

    @contextmanager
    def history_operation(self, record=True, label=None):
        """Group committed deltas, never copy the scene or record utility geometry."""
        if not hasattr(self, '_history_undo'):
            AppGeoEditor.reset_history(self)
        with AppGeoEditor.history_access(self) as accepted:
            if not accepted:
                yield False
                return
            nested = self._history_pending is not None
            if not nested:
                self._history_pending = []
                before = list(self.selected)
                tool_name = getattr(getattr(self, 'active_tool', None), 'name', '')
                label = label or tool_name or _("Geometry")
            try:
                yield True
            except Exception:
                if not nested:
                    for removed, inserted in reversed(self._history_pending):
                        self.storage.replace(removed=inserted, inserted=removed)
                    self._snap_cache = None
                    self.selected[:] = before
                raise
            else:
                if not nested and self._history_pending:
                    if record:
                        self._history_undo.append((self._history_pending, before, list(self.selected), label))
                        self._history_redo.clear()
                        AppGeoEditor.enforce_history_limits(self)
                    self._history_revision += 1
            finally:
                if not nested:
                    self._history_pending = None
                    AppGeoEditor.notify_history(self)

    def history_limits(self):
        # Only the limit values are shared; the editor owns its deltas and navigation state.
        return HistoryManager(self.app, None).limits()

    def enforce_history_limits(self):
        max_steps, max_bytes = AppGeoEditor.history_limits(self)
        if len(self._history_undo) > max_steps:
            self._history_baseline_evicted = True
        del self._history_undo[:-max_steps]

        def retained_size():
            shapes = {}
            for deltas, before, after, label in self._history_undo + self._history_redo:
                for shape in before + after:
                    shapes[id(shape)] = shape
                for removed, inserted in deltas:
                    for shape in removed + inserted:
                        shapes[id(shape)] = shape
            return state_size({'geometry': [shape.geo for shape in shapes.values()]})

        # The memory budget is soft for one adjacent operation, on either stack.
        while self._history_redo and len(self._history_undo) + len(self._history_redo) > 1 and \
                retained_size() > max_bytes:
            del self._history_redo[0]
        while len(self._history_undo) > 1 and retained_size() > max_bytes:
            del self._history_undo[0]
            self._history_baseline_evicted = True

    def history_labels(self):
        return [entry[3] for entry in self._history_undo + list(reversed(self._history_redo))]

    def notify_history(self):
        signal = getattr(self, 'history_changed', None)
        if signal is not None:
            signal.emit()

    def refresh_history(self):
        if not self._history_lock.acquire(blocking=False):
            return
        try:
            AppGeoEditor.enforce_history_limits(self)
            labels = AppGeoEditor.history_labels(self)
            position = len(self._history_undo)
            combo = self.ui.history_positions
            combo.clear()
            baseline = _('Oldest retained state') if self._history_baseline_evicted else _('Initial state')
            combo.addItems([baseline] + labels)
            combo.setCurrentIndex(position)
            undo_label = self._history_undo[-1][3] if self._history_undo else ''
            redo_label = self._history_redo[-1][3] if self._history_redo else ''
            if self.app.call_source == 'geo_editor':
                self.app.ui.on_history_changed(undo_label, redo_label)
            elif self.app.call_source == 'app':
                history_of(self.app).notify()
        finally:
            self._history_lock.release()

    def history_go_to(self, position):
        with AppGeoEditor.history_access(self) as accepted:
            if not accepted:
                AppGeoEditor.notify_history(self)
                return False
            # Positions refer to the retained timeline, just as in refresh_history().
            AppGeoEditor.enforce_history_limits(self)
            if not 0 <= position <= len(self._history_undo) + len(self._history_redo):
                AppGeoEditor.notify_history(self)
                return False
            distance = position - len(self._history_undo)
            for _step in range(abs(distance)):
                if not AppGeoEditor._replay_history(self, distance > 0):
                    return False
            return True

    def reset_history(self):
        if not hasattr(self, '_history_lock'):
            self._history_lock = RLock()
        with AppGeoEditor.history_access(self, cancel_pending=True) as accepted:
            if not accepted:
                return 'fail'
            self._history_session = getattr(self, '_history_session', 0) + 1
            self._history_revision = 0
            self._snap_cache = None
            self._pick_cache = None
            self._pick_cycle = None
            self.press_pos_raw = None
            self.press_pos_screen = None
            self._history_undo = []
            self._history_redo = []
            self._history_baseline_evicted = False
            self._history_pending = None
            self._history_worker_pending = False
            self._history_worker_owner = None
            self._history_worker_token = None
            AppGeoEditor.notify_history(self)

    @geometry_operation
    def replace_shapes(self, removed=(), inserted=(), selected=None):
        """The committed storage boundary. Selection and wrappers belong to the same delta."""
        removed = [shape for shape in removed if id(shape) in self.storage.indexes]
        inserted = list(inserted)
        if not removed and not inserted:
            return
        self.storage.replace(removed=removed, inserted=inserted)
        self._snap_cache = None
        self._history_pending.append((removed, inserted))
        removed_ids = {id(shape) for shape in removed}
        self.selected[:] = [shape for shape in self.selected if id(shape) not in removed_ids]
        if selected is not None:
            self.selected[:] = selected

    def undo(self):
        return AppGeoEditor._replay_history(self, False)

    def redo(self):
        return AppGeoEditor._replay_history(self, True)

    def _replay_history(self, redo):
        if not hasattr(self, '_history_lock'):
            return False
        with AppGeoEditor.history_access(self) as accepted:
            if not accepted:
                return False
            AppGeoEditor.enforce_history_limits(self)
            source = self._history_redo if redo else self._history_undo
            destination = self._history_undo if redo else self._history_redo
            if not source:
                return False
            deltas, before, after, label = source[-1]
            for removed, inserted in deltas if redo else reversed(deltas):
                self.storage.replace(removed=removed if redo else inserted,
                                     inserted=inserted if redo else removed)
            self.selected[:] = after if redo else before
            destination.append(source.pop())
            AppGeoEditor.enforce_history_limits(self)
            self._history_revision += 1
            self._snap_cache = None
            self.build_ui_sig.emit()
            self.plot_changes()
            AppGeoEditor.notify_history(self)
            return True

    def submit_worker(self, fcn, params, record=True):
        """Reserve one operation; GUI callers reject busy work instead of waiting for computation."""
        if not hasattr(self, '_history_undo'):
            AppGeoEditor.reset_history(self)
        with AppGeoEditor.history_access(self) as accepted:
            if not accepted:
                return 'fail'
            session, revision = self._history_session, self._history_revision
            selection = tuple(self.selected)
            token = object()
            label = getattr(getattr(self, 'active_tool', None), 'name', '') or \
                getattr(fcn, '__qualname__', _('Geometry')).split('.<locals>')[0].split('.')[-1].replace('_', ' ').capitalize()
            self._history_worker_token = token
            self._history_worker_pending = True

        def work():
            try:
                # Only the worker may wait here. Every GUI entry uses nonblocking history_access().
                with self._history_lock:
                    if token is not self._history_worker_token or \
                            (session, revision) != (self._history_session, self._history_revision) or \
                            selection != tuple(self.selected):
                        return
                    self._history_worker_owner = get_ident()
                    interdict_selection = getattr(self, 'interdict_selection', False)
                    self.interdict_selection = True
                    try:
                        with AppGeoEditor.history_operation(self, record=record, label=label) as accepted:
                            if accepted:
                                return fcn(*params)
                    finally:
                        self.interdict_selection = interdict_selection
            finally:
                with self._history_lock:
                    if token is self._history_worker_token:
                        self._history_worker_token = None
                        self._history_worker_owner = None
                        self._history_worker_pending = False
                        AppGeoEditor.notify_history(self)

        try:
            self.app.worker_task.emit({'fcn': work, 'params': []})
        except Exception:
            if token is self._history_worker_token:
                self._history_worker_token = None
                self._history_worker_pending = False
            raise

    @geometry_operation
    def add_shape(self, shape, build_ui=True):
        """
        Adds a shape to the shape storage.

        :param shape:       Shape to be added.
        :type shape:        DrawToolShape, list
        :param build_ui:    If to trigger a build of the UI
        :type build_ui:     bool
        :return:            None
        """
        # the shapes are collected first: the storage gets them in 1 batch and the UI is built 1 time
        to_insert = []
        ret = self._collect_shape(shape, to_insert)

        if to_insert:
            try:
                AppGeoEditor.replace_shapes(self, inserted=to_insert)
            except Exception as err:
                self.app.inform_shell.emit('%s\n%s' % (_("Error on inserting shapes into storage."), str(err)))
            if build_ui is True:
                self.build_ui_sig.emit()  # Build UI

        return ret

    def _collect_shape(self, shape, to_insert):
        """
        Checks a shape for add_shape(). A utility shape goes to self.utility; a shape for the storage goes to
        to_insert.

        :param shape:       DrawToolShape, Shapely geometry or an iterable of them
        :param to_insert:   list that collects the shapes for the storage
        :return:            list with the DrawToolShape made for a Shapely geometry; None for an iterable
        """
        ret = []

        if shape is None:
            return

        try:
            w_geo = shape.geoms if isinstance(shape, base.BaseMultipartGeometry) else shape
            for subshape in w_geo:
                self._collect_shape(subshape, to_insert)
            return
        except TypeError:
            pass

        if not isinstance(shape, DrawToolShape):
            shape = DrawToolShape(shape)
            ret.append(shape)

        # assert isinstance(shape, DrawToolShape), "Expected a DrawToolShape, got %s" % type(shape)
        assert shape.geo is not None, "Shape object has empty geometry (None)"
        assert (isinstance(shape.geo, list) and len(shape.geo) > 0) or not isinstance(shape.geo, list), \
            "Shape objects has empty geometry ([])"

        if isinstance(shape, DrawToolUtilityShape):
            self.utility.append(shape)
        else:
            geometry = shape.geo
            if geometry and geometry.is_valid and not geometry.is_empty and geometry.geom_type != 'Point':
                to_insert.append(shape)

        return ret

    def delete_utility_geometry(self):
        """
        Will delete the shapes in the utility shapes storage.

        :return:    None
        """

        # for_deletion = [shape for shape in self.shape_buffer if shape.utility]
        # for_deletion = [shape for shape in self.storage.get_objects() if shape.utility]
        for_deletion = [shape for shape in self.utility]
        for shape in for_deletion:
            self.delete_shape(shape)

        self.tool_shape.clear(update=True)
        self.tool_shape.redraw()

    def toolbar_tool_toggle(self, key):
        """
        It is used as a slot by the Snap buttons.

        :param key:     Key in the self.editor_options dictionary that is to be updated
        :return:        Boolean. Status of the checkbox that toggled the Editor Tool
        """
        cb_widget = self.sender()
        assert isinstance(cb_widget, QtGui.QAction), "Expected a QAction got %s" % type(cb_widget)
        self.editor_options[key] = cb_widget.isChecked()

        return 1 if self.editor_options[key] is True else 0

    def clear(self):
        """
        Will clear the storage for the Editor shapes, the selected shapes storage and plot_all. Clean up method.

        :return:    None
        """
        if AppGeoEditor.reset_history(self) == 'fail':
            return 'fail'
        self.active_tool = None
        # self.shape_buffer = []
        self.selected = []
        self.shapes.clear(update=True)
        self.sel_shapes.clear(update=True)
        self.tool_shape.clear(update=True)

        # self.storage = AppGeoEditor.make_storage()
        self.plot_all()

    def on_tool_select(self, tool):
        """
        Behavior of the toolbar. Tool initialization.

        :rtype : None
        """
        self.app.log.debug("on_tool_select('%s')" % tool)
        grips = getattr(self, 'vertex_grips', None)
        if grips is not None:
            grips.cancel()

        # This is to make the group behave as radio group
        if tool in self.tools:
            if isinstance(self.active_tool, (FCPath, FCCircle, FCRectangle, FCMove, FCCopy, FCImport)):
                self.delete_utility_geometry()
                self.active_tool.clean_up()
                self.active_tool.points = []
                self.in_action = False
                QtGui.QGuiApplication.restoreOverrideCursor()
            if self.tools[tool]["button"].isChecked():
                self.app.log.debug("%s is checked." % tool)
                for t in self.tools:
                    if t != tool:
                        self.tools[t]["button"].setChecked(False)

                self.active_tool = self.tools[tool]["constructor"](self)
            else:
                self.app.log.debug("%s is NOT checked." % tool)
                for t in self.tools:
                    self.tools[t]["button"].setChecked(False)

                self.select_tool('select')
                self.active_tool = FCSelect(self)

        if grips is not None:
            grips.sync()

    def draw_tool_path(self):
        self.select_tool('path')
        return

    def draw_tool_rectangle(self):
        self.select_tool('rectangle')
        return

    def on_grid_toggled(self):
        self.toolbar_tool_toggle("grid_snap")

        # make sure that the cursor shape is enabled/disabled, too
        if self.editor_options['grid_snap'] is True:
            self.app.options['global_grid_snap'] = True
            self.app.inform[str, bool].emit(_("Grid Snap enabled."), False)
            self.app.app_cursor.enabled = True
        else:
            self.app.options['global_grid_snap'] = False
            self.app.inform[str, bool].emit(_("Grid Snap disabled."), False)
            self.app.app_cursor.enabled = False

    def _tool_wants_raw_pick(self):
        tool = self.active_tool
        name = getattr(tool, 'name', None)
        if name in ('select', 'simplification', 'import'):
            return True
        if name in ('move', 'copy'):
            return getattr(tool, 'has_selection', True) is False
        if name == 'eraser':
            return not self.selected
        return name in ('circle', 'rectangle') and \
            QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.ShiftModifier

    def _is_click_not_drag(self, screen_pos):
        press = getattr(self, 'press_pos_screen', None)
        if press is None:
            return None
        return math.hypot(screen_pos[0] - press[0], screen_pos[1] - press[1]) < CLICK_DRAG_THRESHOLD_PX

    def on_canvas_click(self, event):
        """
        event.pos has canvas screen coordinates.

        :param event: Event object dispatched by VisPy SceneCanvas
        :return: None
        """
        event_pos = event.pos

        self.pos = self.canvas.translate_coords(event_pos)
        raw_pick = AppGeoEditor._tool_wants_raw_pick(self)
        grips = getattr(self, 'vertex_grips', None)
        if event.button == 1:
            self.press_pos_raw = (self.pos[0], self.pos[1])
            self.press_pos_screen = (event_pos[0], event_pos[1])
            origin = self.snap(*self.press_pos_raw) if self.app.grid_status() else self.press_pos_raw
            if self.active_tool is not None and self.active_tool.name == 'path' and \
                    QtWidgets.QApplication.keyboardModifiers() == Qt.KeyboardModifier.ShiftModifier:
                origin = self.active_tool.close_x, self.active_tool.close_y
            self._delta_origin = tuple(origin)
            self.app.dx = self.app.dy = 0
            self.app.mouse_pos = list(origin)
            self.app.ui.update_location_labels(0, 0, *origin)
            self.app.plotcanvas.on_update_text_hud(0, 0, *origin)
            if grips is not None and grips.press(event_pos, self.pos, QtWidgets.QApplication.keyboardModifiers()):
                return
        elif grips is not None:
            grips.cancel()

        if not raw_pick and self.app.grid_status():
            self.pos = self.app.geo_editor.snap(self.pos[0], self.pos[1])
        else:
            self.pos = (self.pos[0], self.pos[1])

        if event.button == 1:
            # update mouse position with the clicked position
            self.snap_x = self.pos[0]
            self.snap_y = self.pos[1]

            modifiers = QtWidgets.QApplication.keyboardModifiers()
            # If the SHIFT key is pressed when LMB is clicked then the coordinates are copied to clipboard
            if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
                if self.active_tool is not None \
                        and self.active_tool.name != 'rectangle' \
                        and self.active_tool.name != 'circle' \
                        and self.active_tool.name != 'path':
                    self.app.clipboard.setText(
                        self.app.options.get(
                            "global_point_clipboard_format",
                            self.app.defaults.get(
                                "global_point_clipboard_format",
                                self.app.defaults.factory_defaults.get("global_point_clipboard_format")
                            )
                        ) %
                        (self.decimals, self.pos[0], self.decimals, self.pos[1])
                    )
                    return

            # Selection with left mouse button
            if self.active_tool is not None:

                # Dispatch event to active_tool
                if AppGeoEditor.reject_busy(self):
                    return 'fail'
                point = self.pos if raw_pick else self.snap(self.pos[0], self.pos[1])
                if self.active_tool.click(point) == 'fail':
                    return 'fail'

                # If it is a shape generating tool
                if isinstance(self.active_tool, FCShapeTool) and self.active_tool.complete:
                    if self.on_shape_complete() == 'fail':
                        return 'fail'

                    if isinstance(self.active_tool, (FCText, FCMove)):
                        self.select_tool("select")
                    else:
                        self.select_tool(self.active_tool.name)
            else:
                self.app.log.debug("No active tool to respond to click!")

    def on_canvas_click_release(self, event):
        if AppGeoEditor.reject_busy(self):
            return 'fail'
        event_pos = event.pos
        right_button = 2

        pos_canvas = self.canvas.translate_coords(event_pos)

        if not AppGeoEditor._tool_wants_raw_pick(self) and self.app.grid_status():
            pos = self.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = (pos_canvas[0], pos_canvas[1])

        grips = getattr(self, 'vertex_grips', None)
        if event.button == 1 and grips is not None and grips.release(event_pos, pos_canvas):
            self.press_pos_raw = self.press_pos_screen = None
            return

        # if the released mouse button was RMB then test if it was a panning motion or not, if not it was a context
        # canvas menu
        try:
            # if the released mouse button was LMB then test if we had a right-to-left selection or a left-to-right
            # selection and then select a type of selection ("enclosing" or "touching")
            if event.button == 1:  # left click
                is_click = AppGeoEditor._is_click_not_drag(self, event_pos) if isinstance(self.active_tool, FCSelect) \
                    else None
                if isinstance(self.active_tool, FCImport):
                    # a click picks (or unpicks) the shapes under it; a drag picks the shapes in the area
                    start = getattr(self, 'press_pos_raw', None) or (self.pos[0], self.pos[1])
                    self.app.delete_selection_shape()
                    if AppGeoEditor._is_click_not_drag(self, event_pos) is False:
                        self.active_tool.area_select(start, (pos_canvas[0], pos_canvas[1]), pos_canvas[0] >= start[0])
                    else:
                        self.active_tool.click_release(start)
                    self.app.selection_type = None
                    self.press_pos_raw = self.press_pos_screen = None
                elif isinstance(self.active_tool, FCSelect) and is_click is not None:
                    start = self.press_pos_raw
                    self.app.delete_selection_shape()
                    if is_click:
                        self.active_tool.click_release(start)
                        self.plot_changes()
                    else:
                        self._pick_cycle = None
                        self.draw_selection_area_handler(start, (pos_canvas[0], pos_canvas[1]), pos_canvas[0] >= start[0])
                    self.app.selection_type = None
                    self.press_pos_raw = self.press_pos_screen = None
                elif self.app.selection_type is not None:
                    self.draw_selection_area_handler(self.pos, pos, self.app.selection_type)
                    self.app.selection_type = None
                elif isinstance(self.active_tool, FCSelect):
                    # Dispatch event to active_tool
                    self.active_tool.click_release((self.pos[0], self.pos[1]))
                    # self.app.inform.emit(msg)
                    self.plot_changes()
            elif event.button == right_button:  # right click
                if self.app.ui.popMenu.mouse_is_panning is False:
                    if self.in_action is False:
                        try:
                            QtGui.QGuiApplication.restoreOverrideCursor()
                        except Exception:
                            pass

                        if self.active_tool.complete is False and not isinstance(self.active_tool, FCSelect):
                            self.active_tool.complete = True
                            self.in_action = False
                            self.delete_utility_geometry()
                            if isinstance(self.active_tool, FCImport) and self.active_tool.commit() == 'fail':
                                self.active_tool.complete = False
                                return 'fail'
                            self.active_tool.clean_up()
                            self.app.inform.emit('[success] %s' % _("Done."))
                            self.select_tool('select')
                        else:
                            self.app.cursor = QtGui.QCursor()
                            self.app.populate_cmenu_grids()
                            self.app.ui.popMenu.popup(self.app.cursor.pos())
                    else:
                        # if right click on canvas and the active tool need to be finished (like Path or Polygon)
                        # right mouse click will finish the action
                        if isinstance(self.active_tool, FCShapeTool):
                            if self.active_tool.click(self.snap(self.x, self.y)) == 'fail':
                                return 'fail'
                            if self.active_tool.make() == 'fail':
                                return 'fail'
                            if self.active_tool.complete:
                                if self.on_shape_complete() == 'fail':
                                    return 'fail'
                                self.app.inform.emit('[success] %s' % _("Done."))
                                self.select_tool(self.active_tool.name)
        except Exception as e:
            self.app.log.error("FLatCAMGeoEditor.on_canvas_click_release() --> Error: %s" % str(e))
            return

    def on_canvas_move(self, event):
        """
        Called on 'mouse_move' event.
        "event.pos" have canvas screen coordinates

        :param event: Event object dispatched by VisPy SceneCavas
        :return: None
        """
        event_pos = event.pos
        event_is_dragging = event.is_dragging
        right_button = 2

        pos = self.canvas.translate_coords(event_pos)
        self.x = pos[0]
        self.y = pos[1]

        self.app.ui.popMenu.mouse_is_panning = False

        # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
        if event.button == right_button:
            if event_is_dragging:
                self.app.ui.popMenu.mouse_is_panning = True
                # return
            else:
                self.app.ui.popMenu.mouse_is_panning = False

        if self.active_tool is None:
            return

        try:
            x = float(pos[0])
            y = float(pos[1])
        except TypeError:
            return

        # ### Snap coordinates ###
        if self.app.grid_status():
            x, y = self.snap(x, y)

            # Update cursor
            self.app.app_cursor.set_data(np.asarray([(x, y)]), symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                         edge_width=self.app.options.get(
                                             "global_cursor_width",
                                             self.app.defaults.get(
                                                 "global_cursor_width",
                                                 self.app.defaults.factory_defaults.get("global_cursor_width")
                                             )
                                         ),
                                         size=self.app.options.get(
                                             "global_cursor_size",
                                             self.app.defaults.get(
                                                 "global_cursor_size",
                                                 self.app.defaults.factory_defaults.get("global_cursor_size")
                                             )
                                         ))

        self.snap_x = x
        self.snap_y = y
        if not self.app.grid_status():
            AppGeoEditor.snap_feedback(self, None)
        self.app.mouse_pos = [x, y]

        if self.pos is None:
            self.pos = (0, 0)

        if event.button == 1 and event_is_dragging and isinstance(self.active_tool, FCEraser):
            pass
        else:
            self.update_utility_geometry(data=(x, y))
            if self.active_tool.name in ['path', 'polygon', 'move', 'circle', 'arc', 'rectangle', 'copy']:
                try:
                    self.active_tool.draw_cursor_data(pos=(x, y))
                except AttributeError:
                    # this can happen if the method is not implemented yet for the active_tool
                    pass

        display_x, display_y = x, y
        if self.active_tool.name == 'path' and \
                QtWidgets.QApplication.keyboardModifiers() == Qt.KeyboardModifier.ShiftModifier:
            display_x, display_y = self.active_tool.close_x, self.active_tool.close_y
        origin = getattr(self, '_delta_origin', (0, 0))
        self.app.dx = display_x - origin[0]
        self.app.dy = display_y - origin[1]
        self.app.ui.update_location_labels(self.app.dx, self.app.dy, display_x, display_y)
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, display_x, display_y)

        grips = getattr(self, 'vertex_grips', None)
        if grips is not None and grips.move(event_pos, pos):
            return

        # ### Selection area on canvas section ###
        dx = pos[0] - self.pos[0]
        selecting = isinstance(self.active_tool, FCSelect)
        if selecting:
            start = getattr(self, 'press_pos_raw', None) or (self.pos[0], self.pos[1])
            dx = pos[0] - start[0]
            selection_end = (pos[0], pos[1])
        else:
            start, selection_end = self.pos, (x, y)
        if event_is_dragging and event.button == 1 and \
                (not selecting or AppGeoEditor._is_click_not_drag(self, event_pos) is not True):
            if dx < 0:
                self.app.draw_moving_selection_shape(tuple(start), selection_end,
                                                     color=self.app.options.get(
                                                         "global_alt_sel_line",
                                                         self.app.defaults.get(
                                                             "global_alt_sel_line",
                                                             self.app.defaults.factory_defaults.get("global_alt_sel_line")
                                                         )
                                                     ),
                                                     face_color=self.app.options.get(
                                                         'global_alt_sel_fill',
                                                         self.app.defaults.get(
                                                             'global_alt_sel_fill',
                                                             self.app.defaults.factory_defaults.get('global_alt_sel_fill')
                                                         )
                                                     ))
                self.app.selection_type = False
            else:
                self.app.draw_moving_selection_shape(tuple(start), selection_end)
                self.app.selection_type = True
        else:
            if selecting:
                self.app.delete_selection_shape()
            self.app.selection_type = None

    def update_utility_geometry(self, data):
        # ### Utility geometry (animated) ###
        geo = self.active_tool.utility_geometry(data=data)
        if isinstance(geo, DrawToolShape) and geo.geo is not None:
            # Remove any previous utility shape
            self.tool_shape.clear(update=False)
            self.draw_utility_geometry(geo=geo)

    def draw_selection_area_handler(self, start_pos, end_pos, sel_type):
        """

        :param start_pos: mouse position when the selection LMB click was done
        :param end_pos: mouse position when the left mouse button is released
        :param sel_type: if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :return:
        """
        if getattr(self, 'interdict_selection', False):
            return
        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])

        key_modifier = QtWidgets.QApplication.keyboardModifiers()

        if key_modifier == QtCore.Qt.KeyboardModifier.ShiftModifier:
            mod_key = 'Shift'
        elif key_modifier == QtCore.Qt.KeyboardModifier.ControlModifier:
            mod_key = 'Control'
        else:
            mod_key = None

        self.app.delete_selection_shape()

        # 1 vectorized test of all the shapes against the prepared selection polygon
        objects = list(self.storage.get_objects())
        shapely.prepare(poly_selection)
        test = shapely.contains if sel_type is True else shapely.intersects
        hits = test(poly_selection, [obj.geo for obj in objects]) if objects else []
        sel_objects_list = [obj for obj, hit in zip(objects, hits) if hit]

        if mod_key == self.app.options.get(
                "global_mselect_key",
                self.app.defaults.get(
                    "global_mselect_key",
                    self.app.defaults.factory_defaults.get("global_mselect_key")
                )):
            # a shape in the area is deselected if it was selected, else it is added to the selected shapes
            old_ids = {id(obj) for obj in self.selected}
            hit_ids = {id(obj) for obj in sel_objects_list}
            self.selected[:] = [obj for obj in self.selected if id(obj) not in hit_ids] + \
                               [obj for obj in sel_objects_list if id(obj) not in old_ids]
        else:
            self.selected = sel_objects_list

        # #############################################################################################################
        # #########  if selection is done on canvas update the Tree in Selected Tab with the selection  ###############
        # #############################################################################################################
        self.sync_tree_selection()
        if self.selected:
            self.update_all_vertex_count()
        else:
            self.on_tree_selection()

        # #############################################################################################################
        # ###################  calculate vertex numbers for all selected shapes  ######################################
        # #############################################################################################################
        vertex_nr = 0
        for sha in sel_objects_list:
            sha_geo_solid = sha.geo
            if sha_geo_solid.geom_type == 'Polygon':
                sha_geo_solid_coords = list(sha_geo_solid.exterior.coords)
            elif sha_geo_solid.geom_type in ['LinearRing', 'LineString']:
                sha_geo_solid_coords = list(sha_geo_solid.coords)
            else:
                sha_geo_solid_coords = []

            vertex_nr += len(sha_geo_solid_coords)

        self.ui.geo_vertex_entry.set_value(vertex_nr)

        self.plot_changes()

        # a tool that shows data of the selection (like Simplify) updates it
        selection_changed = getattr(self.active_tool, 'selection_changed', None)
        if selection_changed is not None:
            selection_changed()

    def draw_utility_geometry(self, geo):
        # Add the new utility shape
        try:
            # this case is for the Font Parse
            w_geo = list(geo.geo.geoms) if isinstance(geo.geo, (MultiPolygon, MultiLineString)) else list(geo.geo)
            for el in w_geo:
                if type(el) == MultiPolygon:
                    for poly in el.geoms:
                        self.tool_shape.add(
                            shape=poly,
                            color=self.get_draw_color(),
                            update=False,
                            layer=0,
                            tolerance=None
                        )
                elif type(el) == MultiLineString:
                    for linestring in el.geoms:
                        self.tool_shape.add(
                            shape=linestring,
                            color=self.get_draw_color(),
                            update=False,
                            layer=0,
                            tolerance=None
                        )
                else:
                    self.tool_shape.add(
                        shape=el,
                        color=(self.get_draw_color()),
                        update=False,
                        layer=0,
                        tolerance=None
                    )
        except TypeError:
            self.tool_shape.add(
                shape=geo.geo, color=self.get_draw_color(),
                update=False, layer=0, tolerance=None)
        except AttributeError:
            pass

        self.tool_shape.redraw()

    def get_draw_color(self):
        orig_color = self.app.options.get(
            "global_draw_color",
            self.app.defaults.get(
                "global_draw_color",
                self.app.defaults.factory_defaults.get("global_draw_color")
            )
        )

        if self.app.options.get(
                'global_theme',
                self.app.defaults.get(
                    'global_theme',
                    self.app.defaults.factory_defaults.get('global_theme')
                )) in ['default', 'light']:
            return orig_color

        # in the "dark" theme we invert the color
        lowered_color = orig_color.lower()
        group1 = "#0123456789abcdef"
        group2 = "#fedcba9876543210"
        # create color dict
        color_dict = {group1[i]: group2[i] for i in range(len(group1))}
        new_color = ''.join([color_dict[j] for j in lowered_color])
        return new_color

    def get_sel_color(self):
        return self.app.options.get(
            'global_sel_draw_color',
            self.app.defaults.get(
                'global_sel_draw_color',
                self.app.defaults.factory_defaults.get('global_sel_draw_color')
            )
        )

    def on_delete_btn(self):
        self.delete_selected()
        # self.plot_all()

    @geometry_operation
    def delete_selected(self):
        self.delete_shape(self.selected)
        self.selected.clear()

        self.build_ui()
        # only the deleted shapes are removed from the canvas
        self.plot_changes()

    @geometry_operation
    def delete_shape(self, shapes, inserted=()):
        """
        Deletes shape(shapes) from the storage, selection and utility

        :param shapes:      DrawToolShape or list of them
        :param inserted:    DrawToolShape objects to insert in the storage in the same index update
        """
        # copy a list: 'selected' is passed in and shrinks while shapes are removed from it
        w_shapes = list(shapes) if isinstance(shapes, list) else [shapes]

        # 1 pass on each list instead of a search and a remove() for each shape; DrawToolShape has no __eq__,
        # so the identity test is the same test as 'in'. The slice assignment keeps the list objects.
        shape_ids = {id(shape) for shape in w_shapes}
        self.utility[:] = [shape for shape in self.utility if id(shape) not in shape_ids]

        # remove from Storage (and insert), in 1 batch
        AppGeoEditor.replace_shapes(self, removed=w_shapes, inserted=inserted)

    def on_move(self):
        # if not self.selected:
        #     self.app.inform.emit(_("[WARNING_NOTCL] Move cancelled. No shape selected."))
        #     return
        self.app.ui.geo_move_btn.setChecked(True)
        self.on_tool_select('move')

    def on_move_click(self):
        try:
            x, y = self.snap(self.x, self.y)
        except TypeError:
            return
        self.on_move()
        self.active_tool.set_origin((x, y))

    def on_copy_click(self):
        if not self.selected:
            self.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No shape selected.")))
            return

        self.app.ui.geo_copy_btn.setChecked(True)
        self.app.geo_editor.on_tool_select('copy')
        self.app.geo_editor.active_tool.set_origin(self.app.geo_editor.snap(
            self.app.geo_editor.x, self.app.geo_editor.y))
        self.app.inform.emit(_("Click on target point."))

    def on_corner_snap(self):
        self.app.ui.corner_snap_btn.trigger()

    def get_selected(self):
        """
        Returns list of shapes that are selected in the editor.

        :return: List of shapes.
        """
        # return [shape for shape in self.shape_buffer if shape["selected"]]
        return self.selected

    @geometry_operation
    def transform_shapes(self, transform, *args, **kwargs):
        """Transform the selection, then update storage, tree and canvas once."""
        selected = list(self.get_selected())
        transformed = []
        parameters = list(args) + list(kwargs.values())
        while parameters:
            value = parameters.pop()
            if isinstance(value, (tuple, list)):
                parameters.extend(value)
            elif value is not None and not isinstance(value, str) and not math.isfinite(value):
                raise ValueError(_('Transformation parameters must be finite.'))
        if transform == 'skew':
            angles = [args[i] if len(args) > i else kwargs[name]
                      for i, name in enumerate(('angle_x', 'angle_y'))]
            if any(math.isclose(float(angle) % 180, 90, abs_tol=1e-12) for angle in angles):
                raise ValueError(_('Invalid skew angle.'))
        if transform == 'buffer':
            value = args[0] if args else kwargs['value']
            factor = args[2] if len(args) > 2 else kwargs.get('factor')
            if not math.isfinite(float(value)) or (factor and float(value) <= 0):
                raise ValueError(_('Invalid buffer value.'))
        matrix = AppGeoEditor.transform_matrix(transform, *args, **kwargs)
        if matrix is not None:
            if not all(math.isfinite(value) for value in matrix):
                raise ValueError(_('Transformation matrix must be finite.'))
            a, b, d, e = matrix[:4]
            if transform == 'scale':
                if a == 0 or e == 0:
                    raise ValueError(_('Transformation matrix is singular.'))
            else:
                magnitude = max(abs(a), abs(b), abs(d), abs(e))
                if magnitude == 0 or abs((a / magnitude) * (e / magnitude) -
                                         (b / magnitude) * (d / magnitude)) <= 1e-12:
                    raise ValueError(_('Transformation matrix is singular.'))
            with np.errstate(over='ignore', invalid='ignore'):
                geometries = affine_geometry_many([shape.geo for shape in selected], matrix)
            if (not shapely.is_valid(geometries).all() or shapely.is_empty(geometries).any() or
                    not np.isfinite(shapely.get_coordinates(geometries)).all()):
                raise ValueError(_('Invalid transformed geometry.'))
        else:
            geometries = [shape.geo for shape in selected]
        for shape, geometry in zip(selected, geometries):
            # Keep the indexed geometry unchanged until replace() removes its old points.
            result = DrawToolShape(geometry)
            result.data = deepcopy(shape.data)
            if matrix is None:
                getattr(result, transform)(*args, **kwargs)
                if np.any(shapely.is_empty(result.geo)):
                    # an erosion larger than the shape: the empty result was dropped and the shape deleted
                    raise ValueError(_('The buffer would leave an empty shape.'))
            result.data['bounds'] = result.bounds()
            self._collect_shape(result, transformed)
            if matrix is not None:
                DrawToolShape._affine_sources[result] = (ref(shape), shape.geo, result.geo, matrix)

        self.delete_shape(selected, inserted=transformed)
        self.selected.extend(transformed)
        self.build_ui_sig.emit()
        self.plot_changes()

    @staticmethod
    def transform_matrix(transform, *args, **kwargs):
        """Use the same model-space matrices as the other editors; buffers are only a rendering optimization."""
        factories = {
            'offset': lambda vect: affine_translate_matrix(*vect),
            'rotate': lambda angle, point: affine_rotate_matrix(angle, point),
            'mirror': lambda axis, point: affine_mirror_matrix(axis, point),
            'skew': lambda angle_x, angle_y, point: affine_skew_matrix(angle_x, angle_y, point),
            'scale': lambda xfactor, yfactor=None, point=None: affine_scale_matrix(
                float(xfactor), float(xfactor if yfactor is None else yfactor), (0, 0) if point is None else point)
        }
        factory = factories.get(transform)
        return factory(*args, **kwargs) if factory is not None else None

    def reuse_affine_entries(self, objects, rendered):
        """Transfer proven replacement buffers, or discard a partial transfer and use the normal replot path."""
        live_ids = {id(shape) for shape in objects}
        reverse = {}
        for entry in rendered.values():
            provenance = DrawToolShape._affine_sources.get(entry[0])
            if provenance is not None:
                source = provenance[0]()
                if source is not None:
                    reverse[id(source)] = (entry[0], provenance)
        changed = False
        for shape in objects:
            if id(shape) in rendered:
                continue
            provenance = DrawToolShape._affine_sources.get(shape)
            if provenance is not None and provenance[0]() is not None and id(provenance[0]()) in rendered:
                source = provenance[0]()
                source_geo, target_geo, matrix = provenance[1:]
            elif id(shape) in reverse:
                source, provenance = reverse[id(shape)]
                target_geo, source_geo, matrix = provenance[1:]
                try:
                    matrix = invert_matrix(matrix)
                except Exception:
                    continue
            else:
                continue
            entry = rendered.get(id(source))
            if (id(source) in live_ids or entry is None or entry[0] is not source or
                    entry[1] is not source_geo or shape.geo is not target_geo or not entry[2]):
                continue
            a, b, d, e, _xoff, _yoff = matrix
            if not all(math.isfinite(value) for value in matrix) or abs(a * e - b * d) <= 1e-12:
                continue
            collections = ((self.shapes, entry[3]), (self.sel_shapes, entry[4]))
            if not entry[3] or any(not callable(getattr(collection, 'transform_keys', None)) or
                                   any(key not in collection.data for key in keys)
                                   for collection, keys in collections):
                continue
            try:
                reused = all(not keys or collection.transform_keys(keys, matrix, update=False)
                             for collection, keys in collections)
            except Exception as err:
                self.app.log.debug('AppGeoEditor.reuse_affine_entries() --> %s' % str(err))
                reused = False
            rendered.pop(id(source))
            changed = True
            if reused:
                entry[0], entry[1] = shape, shape.geo
                rendered[id(shape)] = entry
            else:
                # The base collection may have succeeded before the selection copy failed. Never reuse half a pair.
                self.unplot_entry(entry)
        return changed

    def plot_shape(self, storage, geometry=None, color='#000000FF', linewidth=1, layer=0):
        """
        Plots a geometric object or list of objects without rendering. Plotted objects
        are returned as a list. This allows for efficient/animated rendering.

        :param geometry:    Geometry to be plotted (Any "Shapely.geom" kind or list of such)
        :param color:       Shape color
        :param linewidth:   Width of lines in # of pixels.
        :return:            List of plotted elements.
        """
        plot_elements = []
        if geometry is None:
            geometry = self.active_tool.geometry

        try:
            w_geo = geometry.geoms if isinstance(geometry, base.BaseMultipartGeometry) else geometry
            for geo in w_geo:
                plot_elements += self.plot_shape(storage=storage, geometry=geo, color=color, linewidth=linewidth,
                                                 layer=layer)
        # Non-iterable
        except TypeError:

            # DrawToolShape
            if isinstance(geometry, DrawToolShape):
                plot_elements += self.plot_shape(storage=storage, geometry=geometry.geo, color=color,
                                                 linewidth=linewidth, layer=layer)

            # Polygon: Descend into exterior and each interior.
            # if isinstance(geometry, Polygon):
            #     plot_elements += self.plot_shape(geometry=geometry.exterior, color=color, linewidth=linewidth)
            #     plot_elements += self.plot_shape(geometry=geometry.interiors, color=color, linewidth=linewidth)

            if isinstance(geometry, Polygon):
                plot_elements.append(storage.add(shape=geometry, color=color, face_color=color[:-2] + '50',
                                                 layer=layer, tolerance=self.fcgeometry.drawing_tolerance,
                                                 linewidth=linewidth))
            if isinstance(geometry, (LineString, LinearRing)):
                plot_elements.append(storage.add(shape=geometry, color=color, layer=layer,
                                                 tolerance=self.fcgeometry.drawing_tolerance, linewidth=linewidth))

            if type(geometry) == Point:
                pass

        return plot_elements

    def plot_all(self):
        """
        Plots all shapes in the editor.

        :return: None
        """
        # self.app.log.debug(str(inspect.stack()[1][3]) + " --> AppGeoEditor.plot_all()")

        draw_color = self.color_with_alpha(self.get_draw_color(), 'FF')
        sel_color = self.color_with_alpha(self.get_sel_color(), 'FF')

        drawn = []

        sel_ids = {id(shape) for shape in self.get_selected()}
        rendered = {}
        for shape in self.storage.get_objects():
            geo = shape.geo
            drawable = bool(geo and not geo.is_empty and geo.is_valid)
            rendered[id(shape)] = entry = [shape, geo, drawable, [], [], id(shape) in sel_ids]
            if drawable:
                drawn.append(entry)

        # always clear, otherwise the last shape deleted or deselected stays on the canvas
        self.shapes.clear(update=False)
        self.sel_shapes.clear(update=False)

        # every shape is drawn in 'shapes'. A selected shape is hidden there and 'sel_shapes' gets a copy of its
        # buffers with the selection colors, so a change of the selection does not triangulate the shapes again.
        for entry in drawn:
            entry[3] = self.plot_shape(storage=self.shapes, geometry=entry[1], color=draw_color, linewidth=1)

        for shape in self.utility:
            self.plot_shape(storage=self.shapes, geometry=shape.geo, linewidth=1)

        hidden = set()
        for entry in drawn:
            if entry[5]:
                hidden.update(entry[3])
                entry[4] = self.copy_to_selection(entry, sel_color)
        if hidden:
            self.shapes.update_visibility(False, hidden)

        self.shapes.redraw()
        self.sel_shapes.redraw()

        self._rendered = rendered
        self._render_state = self.get_render_state()
        grips = getattr(self, 'vertex_grips', None)
        if grips is not None:
            grips.sync()

    def get_render_state(self):
        """
        :return: what must stay the same for the cache of plot_all() to match the canvas: the utility shapes and the
                 keys and sizes of the 2 shape collections (a clear() made elsewhere changes them)
        """
        return (tuple(id(shape) for shape in self.utility),
                getattr(self.shapes, 'last_key', None), len(getattr(self.shapes, 'data', ())),
                getattr(self.sel_shapes, 'last_key', None), len(getattr(self.sel_shapes, 'data', ())))

    def copy_to_selection(self, entry, sel_color):
        """
        Draws a shape of the 'shapes' collection in the 'sel_shapes' collection with the selection colors. It copies
        the buffers of the shape; it triangulates the shape again only if a buffer is not ready.

        :param entry:       the plot_all() cache entry of the shape
        :param sel_color:   selection color, '#RRGGBBAA'
        :return:            the keys of the shape in 'sel_shapes'
        """
        sel_keys = []
        for key in entry[3]:
            sel_key = self.sel_shapes.add_copy(self.shapes, key, color=sel_color, face_color=sel_color[:-2] + '50',
                                               linewidth=3)
            if sel_key is None:
                for done_key in sel_keys:
                    self.sel_shapes.remove(done_key)
                return self.plot_shape(storage=self.sel_shapes, geometry=entry[1], color=sel_color, linewidth=3)
            sel_keys.append(sel_key)
        return sel_keys

    def plot_changes(self):
        """
        Plots the changes since the last plot: new, changed and removed shapes, and the shapes whose selection
        changed. Only a new shape (or a shape with a new geometry) is triangulated; a change of the selection hides or
        shows a shape in the 'shapes' collection and adds or removes its copy in the 'sel_shapes' collection. If the
        utility shapes or the collections changed outside of the plot functions, it does a plot_all().

        :return: None
        """
        rendered = getattr(self, '_rendered', None)
        if not rendered or getattr(self, '_render_state', None) != self.get_render_state():
            self.plot_all()
            return

        draw_color = self.color_with_alpha(self.get_draw_color(), 'FF')
        sel_color = self.color_with_alpha(self.get_sel_color(), 'FF')
        sel_ids = {id(shape) for shape in self.get_selected()}

        to_hide = set()
        to_show = set()
        objects = list(self.storage.get_objects())
        changed = AppGeoEditor.reuse_affine_entries(self, objects, rendered)
        seen = set()
        for shape in objects:
            shape_id = id(shape)
            seen.add(shape_id)
            is_sel = shape_id in sel_ids
            entry = rendered.get(shape_id)

            if entry is None or entry[0] is not shape or entry[1] is not shape.geo:
                # a new shape, or a shape with a new geometry
                if entry is not None:
                    self.unplot_entry(entry)
                geo = shape.geo
                drawable = bool(geo and not geo.is_empty and geo.is_valid)
                rendered[shape_id] = entry = [shape, geo, drawable, [], [], is_sel]
                if drawable:
                    changed = True
                    entry[3] = self.plot_shape(storage=self.shapes, geometry=geo, color=draw_color, linewidth=1)
                    if is_sel:
                        to_hide.update(entry[3])
                        entry[4] = self.copy_to_selection(entry, sel_color)
                continue

            if is_sel == entry[5]:
                continue
            entry[5] = is_sel
            if not entry[2]:
                continue

            changed = True
            if is_sel:
                to_hide.update(entry[3])
                entry[4] = self.copy_to_selection(entry, sel_color)
            else:
                for key in entry[4]:
                    self.sel_shapes.remove(key)
                entry[4] = []
                to_show.update(entry[3])

        # the removed shapes
        for shape_id in [shape_id for shape_id in rendered if shape_id not in seen]:
            changed = self.unplot_entry(rendered.pop(shape_id)) or changed

        # 1 pass over the collection for each change of visibility
        if to_hide:
            self.shapes.update_visibility(False, to_hide)
        if to_show:
            self.shapes.update_visibility(True, to_show)

        if changed:
            self.shapes.redraw()
            self.sel_shapes.redraw()
        self._render_state = self.get_render_state()
        grips = getattr(self, 'vertex_grips', None)
        if grips is not None:
            grips.sync()

    def unplot_entry(self, entry):
        """
        Removes from the 2 collections the canvas shapes of a plot_all() cache entry.

        :return: True if a canvas shape was removed
        """
        for key in entry[3]:
            self.shapes.remove(key)
        for key in entry[4]:
            self.sel_shapes.remove(key)
        removed = bool(entry[3] or entry[4])
        entry[3] = []
        entry[4] = []
        return removed

    @staticmethod
    def color_with_alpha(color, alpha):
        """
        Returns the color as '#RRGGBB' + alpha, whether it came as '#RRGGBB' or '#RRGGBBAA'.
        """
        return color[:7] + alpha

    @geometry_operation
    def on_shape_complete(self):
        self.app.log.debug("on_shape_complete()")

        geom_list = []
        try:
            for shape in self.active_tool.geometry:
                geom_list.append(shape)
        except TypeError:
            geom_list = [self.active_tool.geometry]

        if self.app.options.get(
                'geometry_editor_milling_type',
                self.app.defaults.get(
                    'geometry_editor_milling_type',
                    self.app.defaults.factory_defaults.get('geometry_editor_milling_type')
                )) == 'cl' and not getattr(self.active_tool, 'keeps_direction', False):
            # reverse the geometry coordinates direction to allow creation of Gcode for climb milling
            try:
                for shp in geom_list:
                    p = shp.geo
                    if p is not None:
                        if isinstance(p, Polygon):
                            shp.geo = Polygon(p.exterior.coords[::-1], p.interiors)
                        elif isinstance(p, LinearRing):
                            shp.geo = LinearRing(p.coords[::-1])
                        elif isinstance(p, LineString):
                            shp.geo = LineString(p.coords[::-1])
                        elif isinstance(p, MultiLineString):
                            new_line = []
                            for line in p.geoms:
                                new_line.append(LineString(line.coords[::-1]))
                            shp.geo = MultiLineString(new_line)
                        elif isinstance(p, MultiPolygon):
                            new_poly = []
                            for poly in p.geoms:
                                new_poly.append(Polygon(poly.exterior.coords[::-1], poly.interiors))
                            shp.geo = MultiPolygon(new_poly)
                        else:
                            self.app.log.debug("AppGeoEditor.on_shape_complete() Error --> Unexpected Geometry %s" %
                                               type(p))
            except Exception as e:
                self.app.log.error("AppGeoEditor.on_shape_complete() Error --> %s" % str(e))
                return 'fail'

        # Add shape

        removed = getattr(self.active_tool, 'removed_shapes', ())
        if removed:
            inserted = []
            self._collect_shape(geom_list, inserted)
            if AppGeoEditor.replace_shapes(self, removed=removed, inserted=inserted, selected=inserted) == 'fail':
                return 'fail'
            self.active_tool.removed_shapes = []
            self.build_ui_sig.emit()
        else:
            if self.add_shape(geom_list) == 'fail':
                return 'fail'

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        # Re-plot and reset tool.
        self.plot_changes()
        if removed and isinstance(self.active_tool, FCMove):
            # Move preparation must remain retryable until its replacement has actually committed.
            self.active_tool.origin = None
            self.active_tool.draw_cursor_data(delete=True)
            self.app.inform.emit('[success] %s' % _("Done."))
            try:
                self.app.jump_signal.disconnect()
            except TypeError:
                pass
        # self.active_tool = type(self.active_tool)(self)

    @staticmethod
    def make_storage():

        # Shape storage.
        storage = GeometryEditorStorage()
        storage.get_points = DrawToolShape.get_pts

        return storage

    def select_tool(self, pluginName):
        """
        Selects a drawing tool. Impacts the object and appGUI.

        :param pluginName: Name of the tool.
        :return: None
        """
        self.tools[pluginName]["button"].setChecked(True)
        self.on_tool_select(pluginName)

    def set_selected(self, shape):

        # Remove and add to the end.
        if shape in self.selected:
            self.selected.remove(shape)

        self.selected.append(shape)

    def set_unselected(self, shape):
        if shape in self.selected:
            self.selected.remove(shape)

    def get_snap_index(self):
        """Cache straight segments once per mutation, never scan the scene on each mouse move."""
        if not self._history_lock.acquire(blocking=False):
            return None, ()
        try:
            cache = getattr(self, '_snap_cache', None)
            state = (self.storage, self.storage.revision)
            if cache is not None and cache[0] == state:
                return cache[1], cache[2]
            segments = []
            for shape in self.storage.get_objects():
                geo = shape.geo
                parts = geo.geoms if isinstance(geo, MultiLineString) else (geo,)
                for part in parts:
                    if not isinstance(part, (LineString, LinearRing)):
                        continue
                    coords = list(part.coords)
                    segments.extend(LineString((a[:2], b[:2])) for a, b in zip(coords, coords[1:]) if a[:2] != b[:2])
            tree = STRtree(segments) if segments else None
            self._snap_cache = (state, tree, segments)
            return tree, segments
        finally:
            self._history_lock.release()

    def snap_feedback(self, kind):
        self.active_snap_type = kind
        ui = getattr(self, 'ui', None)
        label = getattr(ui, 'snap_type_label', None)
        if label is not None:
            names = {'corner': _("Corner"), 'midpoint': _("Midpoint"), 'segment': _("Nearest segment"),
                     'grid': _("Grid"), None: _("None")}
            text = '%s: %s' % (_("Snap"), names[kind])
            if getattr(self, 'snap_acquisition', None) == 'world':
                text += ' — ' + _("Compatibility: model units (global_snap_max)")
            if label.text() != text:
                label.setText(text)

    def snap(self, x, y):
        """
        Adjusts coordinates to snap settings.

        :param x: Input coordinate X
        :param y: Input coordinate Y
        :return: Snapped (x, y)
        """
        if getattr(self.app, 'call_source', None) == 'grb_editor':
            return self.app.grb_editor.snap(x, y)
        if getattr(self.app, 'call_source', None) == 'exc_editor':
            return self.app.exc_editor.snap(x, y)

        snap_x, snap_y = (x, y)
        snap_distance = np.inf
        snap_kind = None

        # # ## Object (corner?) snap
        # # ## No need for the objects, just the coordinates
        # # ## in the index.
        if self.editor_options["corner_snap"]:
            try:
                nearest_pt, shape = self.storage.nearest((x, y))

                nearest_pt_distance = distance((x, y), nearest_pt)
                if nearest_pt_distance <= float(self.editor_options["global_snap_max"]):
                    snap_distance = nearest_pt_distance
                    snap_x, snap_y = nearest_pt
                    snap_kind = 'corner'
            except (StopIteration, AssertionError):
                pass

        # New modes are editor-local. Global callers keep the legacy grid/corner contract.
        self.snap_acquisition = None
        if getattr(self.app, 'call_source', None) == 'geo_editor' and (
                self.editor_options.get('midpoint_snap', False) or self.editor_options.get('segment_snap', False)) and \
                self.app.grid_status():
            radius = float(self.editor_options.get('snap_radius_px', 8.0))
            try:
                canvas = self.canvas
                screen = np.asarray(canvas.translate_coords_2((x, y))[:2], dtype=float)
                corners = [canvas.translate_coords((screen[0] + dx, screen[1] + dy))[:2]
                           for dx, dy in ((-radius, -radius), (-radius, radius),
                                          (radius, -radius), (radius, radius))]
                bounds = np.asarray(corners, dtype=float)
                if not np.isfinite(screen).all() or not np.isfinite(bounds).all():
                    raise ValueError('Non-finite canvas conversion')
                query = box(*bounds.min(axis=0), *bounds.max(axis=0))
                self.snap_acquisition = 'pixels'
            except (AttributeError, TypeError, ValueError, RuntimeError):
                # Preserve the existing preference's units; never reinterpret it as a pixel count.
                radius = float(self.editor_options['global_snap_max'])
                query = box(x - radius, y - radius, x + radius, y + radius)
                self.snap_acquisition = 'world'

            tree, segments = AppGeoEditor.get_snap_index(self)
            candidates = []
            for idx in tree.query(query) if tree is not None else ():
                segment = segments[int(idx)]
                if self.editor_options.get('midpoint_snap', False):
                    candidates.append((segment.interpolate(0.5, normalized=True), 1, 'midpoint'))
                if self.editor_options.get('segment_snap', False):
                    candidates.append((segment.interpolate(segment.project(Point(x, y))), 2, 'segment'))
            ranked = []
            for point, priority, kind in candidates:
                pt = (point.x, point.y)
                model_distance = distance((x, y), pt)
                acquisition_distance = model_distance
                if self.snap_acquisition == 'pixels':
                    acquisition_distance = distance(screen, canvas.translate_coords_2(pt)[:2])
                if acquisition_distance <= radius:
                    ranked.append((model_distance, priority, pt, kind))
            if ranked:
                candidate_distance, __, pt, kind = min(ranked)
                if candidate_distance < snap_distance:
                    snap_distance, (snap_x, snap_y), snap_kind = candidate_distance, pt, kind

        # # ## Grid snap
        if self.editor_options["grid_snap"]:
            if self.editor_options["global_gridx"] != 0:
                try:
                    snap_x_ = round(
                        x / float(self.editor_options["global_gridx"])) * float(self.editor_options['global_gridx'])
                except TypeError:
                    snap_x_ = x
            else:
                snap_x_ = x

            # If the Grid_gap_linked on Grid Toolbar is checked then the snap distance on GridY entry will be ignored,
            # and it will use the snap distance from GridX entry
            if self.app.ui.grid_gap_link_cb.isChecked():
                if self.editor_options["global_gridx"] != 0:
                    try:
                        snap_y_ = round(
                            y / float(self.editor_options["global_gridx"])) * float(self.editor_options['global_gridx'])
                    except TypeError:
                        snap_y_ = y
                else:
                    snap_y_ = y
            else:
                if self.editor_options["global_gridy"] != 0:
                    try:
                        snap_y_ = round(
                            y / float(self.editor_options["global_gridy"])) * float(self.editor_options['global_gridy'])
                    except TypeError:
                        snap_y_ = y
                else:
                    snap_y_ = y
            nearest_grid_distance = distance((x, y), (snap_x_, snap_y_))
            if nearest_grid_distance < snap_distance:
                snap_x, snap_y = (snap_x_, snap_y_)
                snap_kind = 'grid'

        AppGeoEditor.snap_feedback(self, snap_kind)
        return snap_x, snap_y

    def edit_geometry(self, fcgeometry, multigeo_tool=None):
        """
        Imports the geometry from the given FlatCAM Geometry object
        into the editor.

        :param fcgeometry:      GeometryObject
        :param multigeo_tool:   A tool for the case of the edited geometry being of type 'multigeo'
        :return:                None
        """
        assert isinstance(fcgeometry, Geometry), "Expected a Geometry, got %s" % type(fcgeometry)

        if self.deactivate() == 'fail':
            return 'fail'
        if self.activate() == 'fail':
            return 'fail'

        self.set_editor_ui()

        self.units = self.app.app_units

        # Hide original geometry
        self.fcgeometry = fcgeometry
        fcgeometry.visible = False

        # Set selection tolerance
        DrawToolShape.tolerance = fcgeometry.drawing_tolerance * 10

        self.select_tool("select")

        if self.app.options.get(
                'tools_mill_spindledir',
                self.app.defaults.get(
                    'tools_mill_spindledir',
                    self.app.defaults.factory_defaults.get('tools_mill_spindledir')
                )) == 'CW':
            if self.app.options.get(
                    'geometry_editor_milling_type',
                    self.app.defaults.get(
                        'geometry_editor_milling_type',
                        self.app.defaults.factory_defaults.get('geometry_editor_milling_type')
                    )) == 'cl':
                milling_type = 1  # CCW motion = climb milling (spindle is rotating CW)
            else:
                milling_type = -1  # CW motion = conventional milling (spindle is rotating CW)
        else:
            if self.app.options.get(
                    'geometry_editor_milling_type',
                    self.app.defaults.get(
                        'geometry_editor_milling_type',
                        self.app.defaults.factory_defaults.get('geometry_editor_milling_type')
                    )) == 'cl':
                milling_type = -1  # CCW motion = climb milling (spindle is rotating CCW)
            else:
                milling_type = 1  # CW motion = conventional milling (spindle is rotating CCW)

        self.multigeo_tool = multigeo_tool

        def worker_job(editor_obj):
            # Link shapes into editor.
            with editor_obj.app.proc_container.new(_("Working...")):
                editor_obj.app.inform.emit(_("Loading the Geometry into the Editor..."))

                if self.multigeo_tool:
                    editor_obj.multigeo_tool = self.multigeo_tool
                    geo_to_edit = editor_obj.flatten(geometry=fcgeometry.tools[self.multigeo_tool]['solid_geometry'],
                                                     orient_val=milling_type)
                else:
                    geo_to_edit = editor_obj.flatten(geometry=fcgeometry.solid_geometry, orient_val=milling_type)

                # ####################################################################################################
                # remove the invalid geometry and also the Points as those are not relevant for the Editor
                # ####################################################################################################
                geo_to_edit = flatten_shapely_geometry(geo_to_edit)
                cleaned_geo = [g for g in geo_to_edit if g and not g.is_empty and g.is_valid and g.geom_type != 'Point']

                # all the shapes go in the storage in 1 batch (1 index load instead of 1 index update for each shape)
                new_shapes = []
                for shape in cleaned_geo:
                    if shape.geom_type == 'Polygon':
                        new_shapes.append(DrawToolShape(shape.exterior))
                        for inter in shape.interiors:
                            new_shapes.append(DrawToolShape(inter))
                    else:
                        new_shapes.append(DrawToolShape(shape))
                editor_obj.add_shape(new_shapes, build_ui=False)

                editor_obj.plot_all()

                # updated units
                editor_obj.units = self.app.app_units.upper()
                editor_obj.decimals = self.app.decimals

                # start with GRID toolbar activated
                if editor_obj.app.ui.grid_snap_btn.isChecked() is False:
                    editor_obj.app.ui.grid_snap_btn.trigger()

                # trigger a build of the UI
                self.build_ui_sig.emit()

                if multigeo_tool:
                    editor_obj.app.inform.emit(
                        '[WARNING_NOTCL] %s: %s %s: %s' % (
                            _("Editing MultiGeo Geometry, tool"),
                            str(self.multigeo_tool),
                            _("with diameter"),
                            str(fcgeometry.tools[self.multigeo_tool]['tooldia'])
                        )
                    )
                    self.ui.tooldia_entry.set_value(
                        float(fcgeometry.tools[self.multigeo_tool]['data']['tools_mill_tooldia']))
                else:
                    self.ui.tooldia_entry.set_value(float(fcgeometry.obj_options['tools_mill_tooldia']))

        AppGeoEditor.submit_worker(self, worker_job, [self], record=False)

    def update_editor_geometry(self, fcgeometry, on_complete=None):
        """
        Transfers the geometry tool shape buffer to the selected geometry
        object. The geometry already in the object are removed.

        Geometry commits in the caller thread. The GUI caller supplies on_complete: DXF generation then runs
        in the existing worker queue and queued GUI completion owns source assignment and editor teardown.
        Direct non-GUI callers without a continuation retain synchronous completion.

        :param fcgeometry:  GeometryObject
        :param on_complete: GUI continuation, called once after source generation and deactivation
        :return:            'fail' if busy; 'pending' for deferred source generation; None on completion
        """
        # Reserve the idle editor BEFORE reading storage. A rejected save must not change the object.
        with AppGeoEditor.history_access(self) as accepted:
            if not accepted:
                return 'fail'
            if getattr(self.app, 'save_in_progress', False) is True or \
                    getattr(self.app, 'project_save_pending', False) is True:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "A save of the project is running. Wait until it ends."))
                return 'fail'
            with self.app.proc_container.new(_("Working...")):
                new_dia = self.ui.tooldia_entry.get_value()

                # the shapes of the editor, with the MultiLineString shapes merged
                new_geometry = []
                for shape in self.storage.get_objects():
                    new_geo = shape.geo
                    if isinstance(new_geo, MultiLineString):
                        new_geo = linemerge(new_geo)
                    new_geometry.append(new_geo)
                new_geometry = flatten_shapely_geometry(new_geometry)

                if self.multigeo_tool:
                    tool = fcgeometry.tools[self.multigeo_tool]
                    if new_dia != float(tool['tooldia']):
                        tool['tooldia'] = new_dia
                        tool['data']['tools_mill_tooldia'] = new_dia
                    tool['solid_geometry'] = list(new_geometry)
                    self.multigeo_tool = None
                elif new_dia != float(fcgeometry.obj_options['tools_mill_tooldia']):
                    fcgeometry.obj_options['tools_mill_tooldia'] = new_dia

                fcgeometry.solid_geometry = new_geometry

                try:
                    bounds = fcgeometry.bounds()
                    fcgeometry.obj_options['xmin'] = bounds[0]
                    fcgeometry.obj_options['ymin'] = bounds[1]
                    fcgeometry.obj_options['xmax'] = bounds[2]
                    fcgeometry.obj_options['ymax'] = bounds[3]
                except Exception:
                    pass

                # Geometry is committed. A failed interchange snapshot must not turn Save into Discard.
                if on_complete is not None:
                    return AppGeoEditor.queue_source_save(self, fcgeometry, on_complete)
                try:
                    source_updated = fcgeometry.regenerate_source()
                except Exception as e:
                    self.app.log.error("AppGeoEditor.update_editor_geometry() --> %s" % str(e))
                    source_updated = False
                if not source_updated:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                        "Geometry saved, but the DXF source snapshot is stale. The previous source was kept."))

            self.deactivate()
            self.app.inform.emit(_("Editor Exit. Geometry object was updated ..."))

    def queue_source_save(self, fcgeometry, on_complete):
        token = object()
        self._source_save = dict(token=token, obj=fcgeometry, complete=on_complete,
                                 session=getattr(self, '_history_session', 0),
                                 selection_blocked=getattr(self, 'interdict_selection', False),
                                 proc=self.app.proc_container.new(_("Working...")))
        self._source_save_pending = True
        self.interdict_selection = True

        def work():
            state = getattr(self, '_source_save', None)
            if state is None or state['token'] is not token:
                return
            text = False
            try:
                # No source or UI mutation here. The committed geometry is reserved until GUI completion.
                text = fcgeometry.regenerate_source(store=False)
            except Exception as err:
                self.app.log.error("AppGeoEditor.queue_source_save() --> %s" % str(err))
            finally:
                self.source_save_done.emit((token, text))

        try:
            self.app.worker_task.emit({'fcn': work, 'params': []})
        except Exception as err:
            self.app.log.error("AppGeoEditor.queue_source_save() --> %s" % str(err))
            AppGeoEditor.on_source_save_done(self, (token, False))
            return
        return 'pending'

    @QtCore.pyqtSlot(object)
    def on_source_save_done(self, result):
        token, text = result
        state = getattr(self, '_source_save', None)
        if state is None or token is not state['token']:
            return
        self._source_save = None
        self._source_save_pending = False
        self.interdict_selection = state['selection_blocked']
        try:
            if self.app.geo_editor is not self or self.app.call_source != 'geo_editor' or \
                    getattr(self, '_history_session', 0) != state['session']:
                return
            if isinstance(text, str) and text and text != 'fail':
                state['obj'].source_file = text
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "Geometry saved, but the DXF source snapshot is stale. The previous source was kept."))
            self.deactivate()
            self.app.inform.emit(_("Editor Exit. Geometry object was updated ..."))
            state['complete']()
        finally:
            state['proc'].done()

    def update_options(self, obj):
        if self.paint_tooldia:
            obj.obj_options['tools_mill_tooldia'] = deepcopy(str(self.paint_tooldia))
            self.paint_tooldia = None
            return True
        else:
            return False

    def polygonize(self):
        """Replace enclosing linework with polygon faces, retaining unused geometry."""
        def work_task(editor_self):
            with editor_self.app.proc_container.new(_("Working...")):
                selected = list(editor_self.get_selected())
                try:
                    parts = {shape: flatten_shapely_geometry(shape.geo) for shape in selected}
                    lines = [geo for geometries in parts.values() for geo in geometries
                             if isinstance(geo, (LineString, LinearRing))]
                    if any(not geo.is_valid for geo in lines):
                        raise ValueError(_("Select valid paths to polygonize."))
                    polygons = list(shapely.polygonize(shapely.get_parts(unary_union(lines))).geoms)
                    if not polygons:
                        editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                    _("Select paths that enclose an area. Nothing was changed."))
                        return

                    boundaries = unary_union([polygon.boundary for polygon in polygons])
                    inserted, removed = [], set()
                    for polygon in polygons:
                        shape = DrawToolShape(polygon)
                        shape.data['type'] = _('Polygon')
                        inserted.append(shape)
                    for shape, geometries in parts.items():
                        remaining = [geo.difference(boundaries) if isinstance(geo, (LineString, LinearRing)) else geo
                                     for geo in geometries]
                        if all(before.equals(after) for before, after in zip(geometries, remaining)):
                            continue
                        removed.add(shape)
                        for geo in remaining:
                            if not geo.is_empty:
                                replacement = DrawToolShape(geo)
                                replacement.data = deepcopy(shape.data)
                                replacement.data['bounds'] = replacement.bounds()
                                inserted.append(replacement)
                    if any(not shape.geo.is_valid for shape in inserted):
                        raise ValueError(_("Polygonize produced invalid geometry. Nothing was changed."))
                except (ValueError, TypeError, NotImplementedError, shapely.errors.GEOSException) as err:
                    editor_self.app.log.error("AppGeoEditor.polygonize() --> %s" % str(err))
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
                    return 'fail'

                if AppGeoEditor.replace_shapes(editor_self, removed=removed, inserted=inserted,
                                               selected=[shape for shape in selected if shape not in removed] +
                                               inserted) == 'fail':
                    return 'fail'
                editor_self.plot_changes()
                editor_self.build_ui_sig.emit()
                editor_self.app.inform.emit('[success] %s' % _("Done."))

        return AppGeoEditor.submit_worker(self, work_task, [self])

    def union(self):
        """
        Makes union of selected polygons. Original polygons
        are deleted.

        :return: None.
        """

        def work_task(editor_self):
            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()

                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                results = unary_union([t.geo for t in selected])
                if results.geom_type == 'MultiLineString':
                    results = linemerge(results)

                inserted = []
                editor_self._collect_shape(DrawToolShape(results), inserted)
                editor_self.delete_shape(selected, inserted=inserted)
                editor_self.plot_changes()
                editor_self.build_ui_sig.emit()
                editor_self.app.inform.emit('[success] %s' % _("Done."))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def intersection_2(self):
        """
        Makes intersection of selected polygons. Original polygons are deleted.

        :return: None
        """

        def work_task(editor_self):
            editor_self.app.log.debug("AppGeoEditor.intersection_2()")

            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()

                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                target = selected[0].geo
                if target.is_ring:
                    target = Polygon(target)
                tools = selected[1:]
                # toolgeo = unary_union([deepcopy(shp.geo) for shp in tools]).buffer(0.0000001)
                # result = DrawToolShape(target.difference(toolgeo))
                toolgeo = unary_union([Polygon(tool.geo) if tool.geo.is_ring else tool.geo for tool in tools])
                target = target.difference(toolgeo)

                if target.geom_type == 'MultiLineString':
                    target = linemerge(target)

                if target.geom_type == 'Polygon':
                    target = target.exterior

                inserted = []
                editor_self._collect_shape(DrawToolShape(target), inserted)
                editor_self.delete_shape(selected, inserted=inserted)
                editor_self.plot_changes()
                editor_self.build_ui_sig.emit()
                editor_self.app.inform.emit('[success] %s' % _("Done."))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def intersection(self):
        """
        Makes intersection of selected polygons. Original polygons are deleted.

        :return: None
        """

        def work_task(editor_self):
            editor_self.app.log.debug("AppGeoEditor.intersection()")

            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()
                results = []
                intact = []

                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                intersector = selected[0].geo
                if intersector.is_ring:
                    intersector = Polygon(intersector)
                tools = selected[1:]
                for tool in tools:
                    if tool.geo.is_ring:
                        intersected = Polygon(tool.geo)
                    else:
                        intersected = tool.geo
                    if intersector.intersects(intersected):
                        results.append(intersector.intersection(intersected))
                    else:
                        intact.append(tool)

                if results:
                    intact_ids = {id(shape) for shape in intact}
                    removed = [shape for shape in selected if id(shape) not in intact_ids]
                    inserted = []
                    for geo in results:
                        if geo.geom_type == 'MultiPolygon':
                            for poly in geo.geoms:
                                p_geo = [poly.exterior] + [ints for ints in poly.interiors]
                                for g in p_geo:
                                    editor_self._collect_shape(DrawToolShape(g), inserted)
                        elif geo.geom_type == 'Polygon':
                            p_geo = [geo.exterior] + [ints for ints in geo.interiors]
                            for g in p_geo:
                                editor_self._collect_shape(DrawToolShape(g), inserted)
                        else:
                            editor_self._collect_shape(geo, inserted)
                    editor_self.delete_shape(removed, inserted=inserted)

                # Selected geometry is now gone!
                editor_self.selected = []
                editor_self.plot_changes()
                editor_self.build_ui_sig.emit()
                editor_self.app.inform.emit('[success] %s' % _("Done."))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def subtract(self):
        def work_task(editor_self):
            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()
                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                try:
                    target = selected[0].geo
                    if target.is_ring:
                        target = Polygon(target)
                    tools = selected[1:]
                    # toolgeo = unary_union([deepcopy(shp.geo) for shp in tools]).buffer(0.0000001)
                    # result = DrawToolShape(target.difference(toolgeo))
                    toolgeo = unary_union([Polygon(tool.geo) if tool.geo.is_ring else tool.geo for tool in tools])
                    target = target.difference(toolgeo)
                    inserted = []
                    editor_self._collect_shape(DrawToolShape(target), inserted)
                    editor_self.delete_shape(selected, inserted=inserted)
                    editor_self.plot_changes()
                    editor_self.build_ui_sig.emit()
                    editor_self.app.inform.emit('[success] %s' % _("Done."))
                except Exception as e:
                    editor_self.app.log.error(str(e))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def subtract_2(self):
        def work_task(editor_self):
            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()
                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                try:
                    target = selected[0].geo
                    if target.is_ring:
                        target = Polygon(target)
                    tools = selected[1:]
                    # toolgeo = unary_union([shp.geo for shp in tools]).buffer(0.0000001)
                    toolgeo = unary_union([Polygon(tool.geo) if tool.geo.is_ring else tool.geo for tool in tools])
                    target = target.difference(toolgeo)
                    inserted = []
                    editor_self._collect_shape(DrawToolShape(target), inserted)
                    editor_self.delete_shape(selected[0], inserted=inserted)
                    editor_self.plot_changes()
                    editor_self.build_ui_sig.emit()
                    editor_self.app.inform.emit('[success] %s' % _("Done."))
                except Exception as e:
                    editor_self.app.log.error(str(e))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def cutpath(self):
        def work_task(editor_self):
            with editor_self.app.proc_container.new(_("Working...")):
                selected = editor_self.get_selected()
                if len(selected) < 2:
                    editor_self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                _("A selection of minimum two items is required."))
                    editor_self.select_tool('select')
                    return

                tools = selected[1:]
                toolgeo = unary_union([
                    Polygon(geo) if isinstance(geo, (LineString, LinearRing)) and geo.is_ring else geo
                    for shp in tools for geo in editor_self.flatten(shp.geo)
                ])

                target = selected[0]
                if type(target.geo) == Polygon:
                    paths = poly2rings(target.geo)
                elif type(target.geo) == LineString or type(target.geo) == LinearRing:
                    paths = [target.geo]
                elif type(target.geo) == MultiLineString:
                    paths = target.geo.geoms
                else:
                    editor_self.app.log.warning("Not implemented. Object type: %s" % str(type(target.geo)))
                    return

                inserted = []
                for path in paths:
                    for result in editor_self.flatten(path.difference(toolgeo)):
                        if not result.is_empty:
                            editor_self._collect_shape(result, inserted)

                editor_self.delete_shape(target, inserted=inserted)
                editor_self.plot_changes()
                editor_self.build_ui_sig.emit()
                editor_self.app.inform.emit('[success] %s' % _("Done."))

        AppGeoEditor.submit_worker(self, work_task, [self])

    def flatten(self, geometry, orient_val=1, reset=True, pathonly=False):
        """
        Creates a list of non-iterable linear geometry objects.
        Polygons are expanded into its exterior and interiors if specified.

        Results are placed in self.flat_geometry

        :param geometry: Shapely type or a list or a list of lists of such.
        :param orient_val: will orient the exterior coordinates CW if 1 and CCW for else (whatever else means ...)
        https://shapely.readthedocs.io/en/stable/manual.html#polygons
        :param reset: Clears the contents of self.flat_geometry.
        :param pathonly: Expands polygons into linear elements.
        """

        if reset:
            self.flat_geo = []

        # ## If iterable, expand recursively.
        try:
            if isinstance(geometry, (MultiPolygon, MultiLineString)):
                work_geo = geometry.geoms
            else:
                work_geo = geometry

            for geo in work_geo:
                if geo is not None:
                    self.flatten(geometry=geo,
                                 orient_val=orient_val,
                                 reset=False,
                                 pathonly=pathonly)

        # ## Not iterable, do the actual indexing and add.
        except TypeError:
            if type(geometry) == Polygon:
                geometry = orient(geometry, orient_val)

            if pathonly and type(geometry) == Polygon:
                self.flat_geo.append(geometry.exterior)
                self.flatten(geometry=geometry.interiors,
                             reset=False,
                             pathonly=True)
            else:
                self.flat_geo.append(geometry)

        return self.flat_geo


class AppGeoEditorUI:
    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals
        self.units = self.app.app_units.upper()

        self.geo_edit_widget = QtWidgets.QWidget()
        # ## Box for custom widgets
        # This gets populated in offspring implementations.
        layout = QtWidgets.QVBoxLayout()
        self.geo_edit_widget.setLayout(layout)

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add all the Drills widgets
        # this way I can hide/show the frame
        self.geo_frame = QtWidgets.QFrame()
        self.geo_frame.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.geo_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.geo_frame.setLayout(self.tools_box)

        # ## Page Title box (spacing between children)
        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)

        # ## Page Title icon
        pixmap = QtGui.QPixmap(self.app.resource_location + '/app32.png')
        self.icon = FCLabel()
        self.icon.setPixmap(pixmap)
        self.title_box.addWidget(self.icon, stretch=0)

        # ## Title label
        self.title_label = FCLabel("<font size=5><b>%s</b></font>" % _('Geometry Editor'))
        self.title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.title_box.addWidget(self.title_label, stretch=1)

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        # self.level.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        def add_section(title, color, tooltip, parent_layout):
            # bold colored title followed by a frame with its own grid; returns the grid
            title_lbl = FCLabel('%s' % title, bold=True, color=color)
            title_lbl.setToolTip(tooltip)
            parent_layout.addWidget(title_lbl)
            frame = FCFrame()
            parent_layout.addWidget(frame)
            section_grid = GLay(v_spacing=5, h_spacing=3)
            frame.setLayout(section_grid)
            return section_grid

        # #############################################################################################################
        # History Frame
        # #############################################################################################################
        history_grid = add_section(_("History"), 'purple',
                                   _("The state timeline of the geometry operations."), self.tools_box)
        self.history_positions = QtWidgets.QComboBox()
        self.history_positions.setToolTip(_('History: select a state to undo or redo geometry operations.'))
        history_grid.addWidget(self.history_positions, 0, 0, 1, 3)

        units = self.app.app_units

        dia_grid = GLay(v_spacing=5, h_spacing=3)
        self.tools_box.addLayout(dia_grid)

        # Tool diameter
        tooldia_lbl = FCLabel('%s:' % _("Tool dia"), bold=True)
        tooldia_lbl.setToolTip(
            _("Edited tool diameter.")
        )
        self.tooldia_entry = FCDoubleSpinner()
        self.tooldia_entry.set_precision(self.decimals)
        self.tooldia_entry.setSingleStep(10 ** -self.decimals)
        self.tooldia_entry.set_range(-10000.0000, 10000.0000)

        dia_grid.addWidget(tooldia_lbl, 0, 0)
        dia_grid.addWidget(self.tooldia_entry, 0, 1)
        dia_grid.addWidget(FCLabel(units), 0, 2)

        # #############################################################################################################
        # Snapping Frame
        # #############################################################################################################
        snap_grid = add_section(_("Snapping"), 'orange',
                                _("Extra snap candidates, used when grid snapping is enabled."), self.tools_box)
        self.midpoint_snap_cb = FCCheckBox(_("Midpoint snap"))
        self.segment_snap_cb = FCCheckBox(_("Nearest segment snap"))
        self.midpoint_snap_cb.setToolTip(_("Snap to the midpoint of each straight line/polyline segment."))
        self.segment_snap_cb.setToolTip(_("Snap to the closest point on a straight segment, not its extension.\n"
                                          "Polygonized curves remain straight segments, not analytic circles or tangents."))
        snap_grid.addWidget(self.midpoint_snap_cb, 0, 0, 1, 3)
        snap_grid.addWidget(self.segment_snap_cb, 1, 0, 1, 3)
        snap_grid.addWidget(FCLabel('%s:' % _("Acquisition")), 2, 0)
        self.snap_radius_entry = FCDoubleSpinner()
        self.snap_radius_entry.set_precision(1)
        self.snap_radius_entry.set_range(0.0, 100.0)
        self.snap_radius_entry.set_value(8.0)
        self.snap_radius_entry.setToolTip(_("Pixel radius for midpoint/segment snapping. Requires grid snapping enabled.\n"
                                           "Without a live canvas, uses global_snap_max in model units.\n"
                                           "Corner snapping keeps its existing model-unit distance preference."))
        snap_grid.addWidget(self.snap_radius_entry, 2, 1)
        snap_grid.addWidget(FCLabel("px"), 2, 2)
        self.snap_type_label = FCLabel('%s: %s' % (_("Snap"), _("None")))
        self.snap_type_label.setToolTip(_("Closest candidate wins. Equal distances: corner, midpoint, segment, grid.\n"
                                         "Equal segment candidates use coordinate order."))
        snap_grid.addWidget(self.snap_type_label, 3, 0, 1, 3)

        # #############################################################################################################
        # Tree Widget Frame
        # #############################################################################################################
        # Tree Widget Title
        tw_label = FCLabel('%s' % _("Geometry Table"), bold=True, color='green')
        tw_label.setToolTip(
            _("The list of geometry elements inside the edited object.")
        )
        self.tools_box.addWidget(tw_label)

        tw_frame = FCFrame()
        self.tools_box.addWidget(tw_frame)

        # Grid Layout
        tw_grid = GLay(v_spacing=5, h_spacing=3)
        tw_frame.setLayout(tw_grid)

        # Tree Widget
        self.tw = FCTree(columns=3, header_hidden=False, protected_column=[0, 1], extended_sel=True)
        self.tw.setHeaderLabels(["ID", _("Type"), _("Name")])
        self.tw.setIndentation(0)
        self.tw.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tw.header().setStretchLastSection(True)
        self.tw.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        tw_grid.addWidget(self.tw, 0, 0, 1, 3)

        self.geo_font = QtGui.QFont()
        self.geo_font.setBold(True)
        self.geo_parent = self.tw.invisibleRootItem()

        # #############################################################################################################
        # ############################################ Advanced Editor ################################################
        # #############################################################################################################
        self.adv_frame = QtWidgets.QFrame()
        self.adv_frame.setContentsMargins(0, 0, 0, 0)
        self.tools_box.addWidget(self.adv_frame)

        grid0 = GLay(v_spacing=5, h_spacing=3)
        grid0.setContentsMargins(0, 0, 0, 0)
        self.adv_frame.setLayout(grid0)

        # Zoom Selection
        self.geo_zoom = FCCheckBox(_("Zoom on selection"))
        grid0.addWidget(self.geo_zoom, 0, 0, 1, 3)

        # Parameters Title
        self.param_button = FCButton('%s' % _("Parameters"), checkable=True, color='blue', bold=True,
                                     click_callback=self.on_param_click)
        self.param_button.setToolTip(
            _("Geometry parameters.")
        )
        grid0.addWidget(self.param_button, 1, 0, 1, 3)

        # #############################################################################################################
        # ############################################ Parameter Frame ################################################
        # #############################################################################################################
        self.par_frame = FCFrame()
        grid0.addWidget(self.par_frame, 2, 0, 1, 3)

        par_grid = GLay(v_spacing=5, h_spacing=3)
        self.par_frame.setLayout(par_grid)

        # Is Valid
        is_valid_lbl = FCLabel('%s:' % _("Is Valid"), bold=True)
        self.is_valid_entry = FCLabel('None')

        par_grid.addWidget(is_valid_lbl, 0, 0)
        par_grid.addWidget(self.is_valid_entry, 0, 1)

        # Is Empty
        is_empty_lbl = FCLabel('%s:' % _("Is Empty"), bold=True)
        self.is_empty_entry = FCLabel('None')

        par_grid.addWidget(is_empty_lbl, 1, 0)
        par_grid.addWidget(self.is_empty_entry, 1, 1)

        # Is Ring
        is_ring_lbl = FCLabel('%s:' % _("Is Ring"), bold=True)
        self.is_ring_entry = FCLabel('None')

        par_grid.addWidget(is_ring_lbl, 2, 0)
        par_grid.addWidget(self.is_ring_entry, 2, 1)

        # Is CCW
        is_ccw_lbl = FCLabel('%s:' % _("Is CCW"), bold=True)
        self.is_ccw_entry = FCLabel('None')
        self.change_orientation_btn = FCButton(_("Change"))
        self.change_orientation_btn.setIcon(QtGui.QIcon(self.app.resource_location + '/orientation32.png'))
        self.change_orientation_btn.setToolTip(
            _("Change the orientation of the geometric element.\n"
              "Works for LinearRing and Polygons.")
        )
        par_grid.addWidget(is_ccw_lbl, 3, 0)
        par_grid.addWidget(self.is_ccw_entry, 3, 1)
        par_grid.addWidget(self.change_orientation_btn, 3, 2)

        # Is Simple
        is_simple_lbl = FCLabel('%s:' % _("Is Simple"), bold=True)
        self.is_simple_entry = FCLabel('None')

        par_grid.addWidget(is_simple_lbl, 4, 0)
        par_grid.addWidget(self.is_simple_entry, 4, 1)

        # Length
        len_lbl = FCLabel('%s:' % _("Length"), bold=True)
        len_lbl.setToolTip(
            _("The length of the geometry element.")
        )
        self.geo_len_entry = FCEntry(decimals=self.decimals)
        self.geo_len_entry.setReadOnly(True)

        par_grid.addWidget(len_lbl, 5, 0)
        par_grid.addWidget(self.geo_len_entry, 5, 1)
        par_grid.addWidget(FCLabel(units), 5, 2)

        # #############################################################################################################
        # Coordinates Frame
        # #############################################################################################################
        coords_grid = add_section(_("Coordinates"), 'red',
                                  _("The coordinates of the selected geometry element."), self.tools_box)

        self.geo_coords_entry = FCTextEdit()
        self.geo_coords_entry.setPlaceholderText(
            _("The coordinates of the selected geometry element.")
        )
        coords_grid.addWidget(self.geo_coords_entry, 0, 0, 1, 3)

        # Vertex Points Number
        vertex_lbl = FCLabel('%s:' % _("Last Vertexes"), bold=True)
        vertex_lbl.setToolTip(
            _("The number of vertex points in the last selected geometry element.")
        )
        self.geo_vertex_entry = FCEntry(decimals=self.decimals)
        self.geo_vertex_entry.setReadOnly(True)

        coords_grid.addWidget(vertex_lbl, 1, 0)
        coords_grid.addWidget(self.geo_vertex_entry, 1, 1)

        # All selected Vertex Points Number
        vertex_all_lbl = FCLabel('%s:' % _("Selected Vertexes"), bold=True)
        vertex_all_lbl.setToolTip(
            _("The number of vertex points in all selected geometry elements.")
        )
        self.geo_all_vertex_entry = FCEntry(decimals=self.decimals)
        self.geo_all_vertex_entry.setReadOnly(True)

        coords_grid.addWidget(vertex_all_lbl, 2, 0)
        coords_grid.addWidget(self.geo_all_vertex_entry, 2, 1)

        self.vertex_edit_btn = FCButton(_("Edit vertices"))
        self.vertex_edit_btn.setToolTip(_("Numerically move, insert or remove a vertex of one selected contour."))
        coords_grid.addWidget(self.vertex_edit_btn, 3, 0, 1, 3)

        # #############################################################################################################
        # Advanced sections: hidden in Beginner mode
        # #############################################################################################################
        self.adv_sections = QtWidgets.QWidget()
        adv_box = QtWidgets.QVBoxLayout()
        adv_box.setContentsMargins(0, 0, 0, 0)
        self.adv_sections.setLayout(adv_box)
        self.tools_box.addWidget(self.adv_sections)

        # Paths
        paths_grid = add_section(_("Paths"), 'green', _("Split and join open paths."), adv_box)

        self.split_action = QtGui.QAction(_("Split path"), self.geo_edit_widget)
        self.split_action.setToolTip(_("Select one open LineString, then click its interior to split it."))
        self.split_btn = QtWidgets.QToolButton()
        self.split_btn.setDefaultAction(self.split_action)
        self.split_btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.split_tolerance_entry = FCEntry()
        self.split_tolerance_entry.setText('0.01')
        self.split_tolerance_entry.setToolTip(
            _("Maximum click distance from the path, and excluded distance along the path from each endpoint.\n"
              "In current units, independent of grid and pixel snap radius. Zero requires an exact interior click."))
        paths_grid.addWidget(FCLabel('%s:' % _("Split tolerance")), 0, 0)
        paths_grid.addWidget(self.split_tolerance_entry, 0, 1)
        paths_grid.addWidget(FCLabel(units), 0, 2)
        paths_grid.addWidget(self.split_btn, 1, 0, 1, 3)

        self.join_btn = FCButton(_("Join paths"))
        self.join_btn.setToolTip(_("Join selected open LineStrings. Ambiguous branches are rejected."))
        self.join_tolerance_entry = FCEntry()
        self.join_tolerance_entry.setText('0.01')
        self.join_tolerance_entry.setToolTip(
            _("Maximum endpoint gap in current units. Zero joins exact endpoints only.\n"
              "Near endpoints get a straight bridge: no vertices move; length increases by the gap.\n"
              "Each endpoint must have only one candidate. Disjoint components stay separate."))
        paths_grid.addWidget(FCLabel('%s:' % _("Join tolerance")), 2, 0)
        paths_grid.addWidget(self.join_tolerance_entry, 2, 1)
        paths_grid.addWidget(FCLabel(units), 2, 2)
        paths_grid.addWidget(self.join_btn, 3, 0, 1, 3)

        # Inspect
        inspect_grid = add_section(_("Inspect"), 'blue', _("Read-only diagnostics of the selected geometry."), adv_box)

        self.diagnostics_btn = FCButton(_("Inspect selected geometry"))
        self.diagnostics_btn.setToolTip(_("Read-only snapshot. Open paths are informational; no repair is performed."))
        self.diagnostics_tolerance_entry = FCEntry()
        self.diagnostics_tolerance_entry.setText('0.01')
        self.diagnostics_tolerance_entry.setToolTip(
            _("Near endpoint distance in current units, independent of grid and snapping.\n"
              "Zero reports exactly coincident endpoints only. Paths remain open and unchanged."))
        inspect_grid.addWidget(FCLabel('%s:' % _("Endpoint tolerance")), 0, 0)
        inspect_grid.addWidget(self.diagnostics_tolerance_entry, 0, 1)
        inspect_grid.addWidget(FCLabel(units), 0, 2)
        inspect_grid.addWidget(self.diagnostics_btn, 1, 0, 1, 3)
        self.diagnostics_result = FCLabel('')
        self.diagnostics_result.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.diagnostics_result.setWordWrap(True)
        self.diagnostics_result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        inspect_grid.addWidget(self.diagnostics_result, 2, 0, 1, 3)
        # empty until the first inspection; hidden so it does not leave a blank row
        self.diagnostics_result.hide()

        # Select by Size
        size_grid = add_section(_("Select by Size"), 'teal',
                                _("Select or delete shapes by area or length."), adv_box)
        self.size_area_chk = FCCheckBox(_("Filter polygons by area"))
        self.size_area_chk.setToolTip(
            _("Applies to Polygon and MultiPolygon shapes (text counts as polygons), area in current units squared.\n"
              "Limits are inclusive. Points and mixed collections never match."))
        self.size_min_area_entry = FCEntry()
        self.size_min_area_entry.setText('0')
        self.size_max_area_entry = FCEntry()
        self.size_max_area_entry.setText('1')
        size_grid.addWidget(self.size_area_chk, 0, 0, 1, 3)
        size_grid.addWidget(FCLabel('%s:' % _("Min area")), 1, 0)
        size_grid.addWidget(self.size_min_area_entry, 1, 1)
        size_grid.addWidget(FCLabel('%s²' % units), 1, 2)
        size_grid.addWidget(FCLabel('%s:' % _("Max area")), 2, 0)
        size_grid.addWidget(self.size_max_area_entry, 2, 1)
        size_grid.addWidget(FCLabel('%s²' % units), 2, 2)

        self.size_length_chk = FCCheckBox(_("Filter paths by length"))
        self.size_length_chk.setToolTip(
            _("Applies to LineString, LinearRing and MultiLineString shapes, length in current units.\n"
              "Limits are inclusive. Points and mixed collections never match."))
        self.size_min_length_entry = FCEntry()
        self.size_min_length_entry.setText('0')
        self.size_max_length_entry = FCEntry()
        self.size_max_length_entry.setText('1')
        size_grid.addWidget(self.size_length_chk, 3, 0, 1, 3)
        size_grid.addWidget(FCLabel('%s:' % _("Min length")), 4, 0)
        size_grid.addWidget(self.size_min_length_entry, 4, 1)
        size_grid.addWidget(FCLabel(units), 4, 2)
        size_grid.addWidget(FCLabel('%s:' % _("Max length")), 5, 0)
        size_grid.addWidget(self.size_max_length_entry, 5, 1)
        size_grid.addWidget(FCLabel(units), 5, 2)

        self.size_select_btn = FCButton(_("Select"))
        self.size_select_btn.setToolTip(
            _("Select the shapes matching the enabled filters. Searches the selected shapes if any,\n"
              "else all the shapes. Replaces the current selection."))
        self.size_delete_btn = FCButton(_("Delete"))
        self.size_delete_btn.setToolTip(
            _("Delete the shapes matching the enabled filters, as one Undo/Redo step.\n"
              "Searches the selected shapes if any, else all the shapes."))
        size_btn_box = QtWidgets.QHBoxLayout()
        size_btn_box.setContentsMargins(0, 0, 0, 0)
        size_btn_box.addWidget(self.size_select_btn)
        size_btn_box.addWidget(self.size_delete_btn)
        size_grid.addLayout(size_btn_box, 6, 0, 1, 3)

        # Resize Circles
        resize_grid = add_section(_("Resize Circles"), 'brown',
                                  _("Resize the selected circles to a new diameter."), adv_box)
        self.resize_circles_entry = FCDoubleSpinner(policy=False)
        self.resize_circles_entry.set_precision(self.decimals)
        self.resize_circles_entry.set_range(0.0000, 10000.0000)
        self.resize_circles_entry.setSingleStep(10 ** -self.decimals)
        self.resize_circles_entry.setToolTip(
            _("Diameter to resize the selected circles to. Each circle keeps its own center.\n"
              "Circles are recognized by their shape (polygon or outline with at least 8 vertices,\n"
              "all at the same distance from the center). Other shapes are skipped."))
        resize_grid.addWidget(FCLabel('%s:' % _("New Diameter")), 0, 0)
        resize_grid.addWidget(self.resize_circles_entry, 0, 1)
        resize_grid.addWidget(FCLabel(units), 0, 2)
        self.resize_circles_btn = FCButton(_("Resize"))
        self.resize_circles_btn.setToolTip(
            _("Resize the selected circles to the new diameter, as one Undo/Redo step."))
        resize_grid.addWidget(self.resize_circles_btn, 1, 0, 1, 3)

        GLay.set_common_column_size([grid0, tw_grid, coords_grid, dia_grid, par_grid, history_grid, snap_grid,
                                     paths_grid, inspect_grid, size_grid, resize_grid], 0)

        layout.addStretch(1)

        # Editor
        self.exit_editor_button = FCButton(_('Exit Editor'), bold=True)
        self.exit_editor_button.setIcon(QtGui.QIcon(self.app.resource_location + '/power16.png'))
        self.exit_editor_button.setToolTip(
            _("Exit from Editor.")
        )
        layout.addWidget(self.exit_editor_button)

        # Signals
        self.level.toggled.connect(self.on_level_changed)
        self.exit_editor_button.clicked.connect(lambda: self.app.on_editing_finished())

    def on_param_click(self):
        if self.param_button.get_value():
            self.par_frame.show()
        else:
            self.par_frame.hide()

    def change_level(self, level):
        """

        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """
        if level == 'a':
            self.level.setChecked(True)
        else:
            self.level.setChecked(False)
        self.on_level_changed(self.level.isChecked())

    def on_level_changed(self, checked):
        if not checked:
            self.level.setText('%s' % _('Beginner'))
            self.level.setStyleSheet("""
                                    QToolButton
                                    {
                                        color: green;
                                    }
                                    """)

            self.adv_frame.hide()
            self.adv_sections.hide()

            # Context Menu section
            # self.tw.removeContextMenu()
        else:
            self.level.setText('%s' % _('Advanced'))
            self.level.setStyleSheet("""
                                    QToolButton
                                    {
                                        color: red;
                                    }
                                    """)

            self.adv_frame.show()
            self.adv_sections.show()

            # Context Menu section
            # self.tw.setupContextMenu()


class DrawToolShape(object):
    """
    Encapsulates "shapes" under a common class.
    """

    # External weak maps keep storage/render provenance out of deepcopy and worker serialization.
    _storages = WeakKeyDictionary()
    _affine_sources = WeakKeyDictionary()

    @property
    def geo(self):
        return self._geo

    @geo.setter
    def geo(self, geometry):
        self._geo = geometry
        for storage in tuple(DrawToolShape._storages.get(self, ())):
            storage.revision += 1

    tolerance = None

    @staticmethod
    def get_pts(o):
        """
        Returns a list of all points in the object, where
        the object can be a Polygon, Not a polygon, or a list
        of such. Search is done recursively.

        :param o:   geometric object
        :return:    List of points
        :rtype:     list
        """
        pts = []

        # Iterable: descend into each item.
        try:
            if isinstance(o, base.BaseMultipartGeometry):
                for subo in o.geoms:
                    pts += DrawToolShape.get_pts(subo)
            else:
                for subo in o:
                    pts += DrawToolShape.get_pts(subo)
        # Non-iterable
        except TypeError:
            if o is None:
                return

            # DrawToolShape: descend into .geo.
            if isinstance(o, DrawToolShape):
                pts += DrawToolShape.get_pts(o.geo)

            # Descend into .exterior and .interiors
            elif isinstance(o, Polygon):
                pts += DrawToolShape.get_pts(o.exterior)
                for i in o.interiors:
                    pts += DrawToolShape.get_pts(i)
            elif isinstance(o, (MultiLineString, MultiPolygon)):
                for geo_pol_line in o.geoms:
                    pts += DrawToolShape.get_pts(geo_pol_line)
            # Has .coords: list them.
            else:
                if DrawToolShape.tolerance is not None:
                    pts += list(o.simplify(DrawToolShape.tolerance).coords)
                else:
                    pts += list(o.coords)
        return pts

    def __init__(self, geo: (BaseGeometry, list)):

        # Shapely type or list of such
        self.geo = geo
        self.utility = False
        self.data = {
            'name': _("Geo Elem"),
            'type': _('Path'),  # 'Path', 'Arc', 'Rectangle', 'Polygon', 'Circle',
            'origin': 'center',  # 'center', 'tl', 'tr', 'bl', 'br'
            'bounds': self.bounds()  # xmin, ymin, xmax, ymax
        }

    def get_all_points(self):
        return DrawToolShape.get_pts(self)

    def bounds(self):
        """
                Returns coordinates of rectangular bounds
                of geometry: (xmin, ymin, xmax, ymax).
                """

        # fixed issue of getting bounds only for one level lists of objects
        # now it can get bounds for nested lists of objects

        if self.geo is None:
            return 0, 0, 0, 0

        def bounds_rec(shape_el):
            if type(shape_el) is list:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in shape_el:
                    minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return shape_el.bounds

        bounds_coords = bounds_rec(self.geo)
        return bounds_coords

    def mirror(self, axis, point):
        """
        Mirrors the shape around a specified axis passing through
        the given point.

        :param axis:    "X" or "Y" indicates around which axis to mirror.
        :type axis:     str
        :param point:   [x, y] point belonging to the mirror axis.
        :type point:    list
        :return:        None
        """

        px, py = point
        xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[axis]

        def mirror_geom(shape_el):
            if type(shape_el) is list:
                new_obj = []
                for g in shape_el:
                    new_obj.append(mirror_geom(g))
                return new_obj
            else:
                return scale(shape_el, xscale, yscale, origin=(px, py))

        try:
            self.geo = mirror_geom(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.mirror() --> Failed to mirror. No shape selected")

    def rotate(self, angle, point):
        """
        Rotate a shape by an angle (in degrees) around the provided coordinates.


        The angle of rotation are specified in degrees (default). Positive angles are
        counter-clockwise and negative are clockwise rotations.

        The point of origin can be a keyword 'center' for the bounding box
        center (default), 'centroid' for the geometry's centroid, a Point object
        or a coordinate tuple (x0, y0).

        See shapely manual for more information: http://toblerity.org/shapely/manual.html#affine-transformations

        :param angle:   The angle of rotation
        :param point:   The point of origin
        :return:        None
        """

        px, py = point

        def rotate_geom(shape_el):
            if type(shape_el) is list:
                new_obj = []
                for g in shape_el:
                    new_obj.append(rotate_geom(g))
                return new_obj
            else:
                return rotate(shape_el, angle, origin=(px, py))

        try:
            self.geo = rotate_geom(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.rotate() --> Failed to rotate. No shape selected")

    def skew(self, angle_x, angle_y, point):
        """
        Shear/Skew a shape by angles along x and y dimensions.

        angle_x, angle_y : float, float
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.

        See shapely manual for more information: http://toblerity.org/shapely/manual.html#affine-transformations

        :param angle_x:
        :param angle_y:
        :param point:       tuple of coordinates (x,y)
        :return:
        """
        px, py = point

        def skew_geom(shape_el):
            if type(shape_el) is list:
                new_obj = []
                for g in shape_el:
                    new_obj.append(skew_geom(g))
                return new_obj
            else:
                return skew(shape_el, angle_x, angle_y, origin=(px, py))

        try:
            self.geo = skew_geom(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.skew() --> Failed to skew. No shape selected")

    def offset(self, vect):
        """
        Offsets all shapes by a given vector

        :param vect:    (x, y) vector by which to offset the shape geometry
        :type vect:     tuple
        :return:        None
        """

        try:
            dx, dy = vect
        except TypeError:
            log.debug("DrawToolShape.offset() --> An (x,y) pair of values are needed. "
                      "Probable you entered only one value in the Offset field.")
            return

        def translate_recursion(geom):
            if type(geom) == list:
                geoms = []
                for local_geom in geom:
                    geoms.append(translate_recursion(local_geom))
                return geoms
            else:
                return translate(geom, xoff=dx, yoff=dy)

        try:
            self.geo = translate_recursion(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.offset() --> Failed to offset. No shape selected")

    def scale(self, xfactor, yfactor=None, point=None):
        """
        Scales all shape geometry by a given factor.

        :param xfactor:     Factor by which to scale the shape's geometry/
        :type xfactor:      float
        :param yfactor:     Factor by which to scale the shape's geometry/
        :type yfactor:      float
        :param point:       Point of origin; tuple
        :return: None
        """

        try:
            xfactor = float(xfactor)
        except Exception:
            log.debug("DrawToolShape.offset() --> Scale factor has to be a number: integer or float.")
            return

        if yfactor is None:
            yfactor = xfactor
        else:
            try:
                yfactor = float(yfactor)
            except Exception:
                log.debug("DrawToolShape.offset() --> Scale factor has to be a number: integer or float.")
                return

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        def scale_recursion(geom):
            if type(geom) == list:
                geoms = []
                for local_geom in geom:
                    geoms.append(scale_recursion(local_geom))
                return geoms
            else:
                return scale(geom, xfactor, yfactor, origin=(px, py))

        try:
            self.geo = scale_recursion(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.scale() --> Failed to scale. No shape selected")

    def buffer(self, value, join=None, factor=None, quad_segs=32):
        """
        Create a buffered geometry

        :param value:   the distance to which to buffer
        :param join:    the type of connections between nearby buffer lines
        :param factor:  a scaling factor which will do a "sort of" buffer
        :param quad_segs:   the segments of a quarter circle of a round join
        :return:        None
        """

        def buffer_recursion(geom):
            if type(geom) == list:
                geoms = []
                for local_geom in geom:
                    geoms.append(buffer_recursion(local_geom))
                return geoms
            else:
                if factor:
                    return scale(geom, xfact=value, yfact=value, origin='center')
                elif geom.geom_type in ('LineString', 'LinearRing') and geom.is_ring:
                    # the editor keeps a polygon as its outline: buffer the area inside it, as the Buffer tool does,
                    # and keep the outlines (the buffer of the line was a band with 2 outlines, or empty for erosion)
                    contours = BufferSelectionTool.buffer_contours(geom, value, quad_segs, join)
                    return contours[0] if len(contours) == 1 else MultiLineString(contours)
                else:
                    return geom.buffer(value, quad_segs=quad_segs, join_style=join)

        try:
            self.geo = buffer_recursion(self.geo)
        except AttributeError:
            log.debug("DrawToolShape.buffer() --> Failed to buffer. No shape selected")


class DrawToolUtilityShape(DrawToolShape):
    """
    Utility shapes are temporary geometry in the editor
    to assist in the creation of shapes. For example, it
    will show the outline of a rectangle from the first
    point to the current mouse pointer before the second
    point is clicked and the final geometry is created.
    """

    def __init__(self, geo: (BaseGeometry, list)):
        super(DrawToolUtilityShape, self).__init__(geo=geo)
        self.utility = True


def bend_corner_point(draw_app, old, new):
    """
    Corner point inserted between two consecutive points of a Path/Polygon, according to draw_app.bend_mode.

    The corner math is the one shared with the Gerber Editor (corner_point()).

    :param draw_app:    the Geometry Editor; its bend_mode attribute holds the corner mode (5 = free angle)
    :param old:         (x, y) of the previous point
    :param new:         (x, y) of the new point
    :return:            (x, y) of the corner or None if no corner point is needed
    """
    mode = getattr(draw_app, 'bend_mode', BEND_MODE_FREE)
    return corner_point(mode, (float(old[0]), float(old[1])), (float(new[0]), float(new[1])))


def corner_points(draw_app, points, point):
    """
    Points to append to a Path/Polygon when the user clicks 'point': the corner (if any) and the point.
    """
    corner = bend_corner_point(draw_app, points[-1], point) if points else None
    return [corner, point] if corner is not None else [point]


def backtrack_points(tool):
    """
    Remove from tool.points the points added by the last click (the corner included).
    The number of points added by each click is stored in tool.click_sizes.
    """
    sizes = getattr(tool, 'click_sizes', None)
    count = sizes.pop() if sizes else 1
    tool.points = tool.points[0:-count] if count else tool.points[:]


def cycle_bend_mode(tool, forward):
    """
    Change the corner mode of the Geometry Editor (T = forward, R = backward) and refresh the utility shape.

    :param tool:    the active Path or Polygon tool
    :param forward: True to go to the next mode, False to go to the previous one
    :return:        the status bar message
    """
    draw_app = tool.draw_app
    draw_app.bend_mode = next_bend_mode(getattr(draw_app, 'bend_mode', BEND_MODE_FREE), forward)

    if tool.points:
        # Remove any previous utility shape
        draw_app.tool_shape.clear(update=False)
        geo = tool.utility_geometry(data=(draw_app.snap_x, draw_app.snap_y))
        draw_app.draw_utility_geometry(geo=geo)

    return bend_mode_message(draw_app.bend_mode)


class DrawTool(object):
    """
    Abstract Class representing a tool in the drawing
    program. Can generate geometry, including temporary
    utility geometry that is updated on user clicks
    and mouse motion.
    """

    def __init__(self, draw_app):
        self.draw_app = draw_app
        self.complete = False
        self.points = []
        self.geometry = None  # DrawToolShape or None

    def click(self, point: Union[list[float, float], tuple[float, float]]):
        """
        :param point: [x, y] Coordinate pair.
        """
        return ""

    def click_release(self, point):
        """
        :param point: [x, y] Coordinate pair.
        """
        return ""

    def on_key(self, key):
        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()
        return

    def utility_geometry(self, data=None):
        return None

    @staticmethod
    def bounds(obj):
        def bounds_rec(o):
            if type(o) is list:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in o:
                    try:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                    except Exception as e:
                        log.error("camlib.Gerber.bounds() --> %s" % str(e))
                        return

                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return o.geo.bounds

        bounds_coords = bounds_rec(obj)
        return bounds_coords


class FCSplit(DrawTool):
    """One click commits the split directly; it isn't newly drawn geometry."""

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'split'
        self.target = draw_app.selected[0] if len(draw_app.selected) == 1 else None
        self.session = draw_app._history_session
        draw_app.in_action = False
        draw_app.app.inform.emit(_("Select one open LineString before starting Split path. Click its interior; "
                                   "the split tolerance is in current units."))

    def click(self, point):
        if self.target is None:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Select exactly one open LineString and reopen Split path."))
            return 'fail'
        if self.draw_app.apply_path_split(point, self.draw_app.ui.split_tolerance_entry.text(),
                                          target=self.target, session=self.session) == 'fail':
            return 'fail'
        self.draw_app.select_tool('select')

    def clean_up(self):
        pass


class FCShapeTool(DrawTool):
    """
    Abstract class for tools that create a shape.
    """

    # True for a tool that makes its shapes from shapes of the storage (Move, Copy, Explode): those shapes already
    # have the milling direction, so on_shape_complete() must not reverse them again for climb milling
    keeps_direction = False

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)

        self.name = None

    def steps(self, radius):
        """The segments of a full circle of this radius, as the preference "Circles" of Geometry sets them."""
        return object_circle_steps(self.draw_app.app.options, 'geometry', radius, self.draw_app.app.app_units)

    def quad_segs(self, radius):
        """The Shapely buffer quad_segs of a circle of this radius."""
        return object_quad_segs(self.draw_app.app.options, 'geometry', radius, self.draw_app.app.app_units)

    def make(self):
        pass


class FCCircle(FCShapeTool):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'circle'

        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.plugin_name = _("Circle")
        self.storage = self.draw_app.storage
        self.util_geo = None

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_circle_geo.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.circle_tool = CircleEditorTool(self.app, self.draw_app, plugin_name=self.plugin_name)
        self.ui = self.circle_tool.ui
        self.circle_tool.run()

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.draw_app.app.inform.emit(_("Click on Center point ..."))

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        modifiers = QtWidgets.QApplication.keyboardModifiers()
        if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            # deselect all shapes
            self.draw_app.selected = []
            try:
                __, closest_shape = self.storage.nearest(point)
                self.draw_app.selected.append(closest_shape)
            except StopIteration:
                return ""

            if self.draw_app.selected:
                self.draw_app.plot_changes()
                self.circle_tool.mode = 'change'
                sel_shape_geo = self.draw_app.selected[-1].geo
                geo_bounds = sel_shape_geo.bounds
                # assuming that the default setting for anchor is center
                origin_x_sel_geo = geo_bounds[0] + ((geo_bounds[2] - geo_bounds[0]) / 2)
                self.circle_tool.ui.x_entry.set_value(origin_x_sel_geo)
                origin_y_sel_geo = geo_bounds[1] + ((geo_bounds[3] - geo_bounds[1]) / 2)
                self.circle_tool.ui.y_entry.set_value(origin_y_sel_geo)
                self.draw_app.app.inform.emit(
                    _("Click on Center point to add a new circle or Apply to change the selection."))
            return

        if not all(math.isfinite(v) for v in point) or (self.points and point == self.points[0]):
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Choose a finite perimeter point different from the center."))
            return 'fail'
        if self.points:
            self.utility_geometry(data=point)
        self.circle_tool.mode = 'add'
        self.points.append(point)
        self.circle_tool.ui.x_entry.set_value(self.points[0][0])
        self.circle_tool.ui.y_entry.set_value(self.points[0][1])

        if len(self.points) == 1:
            if self.ui.radius_link_btn.isChecked():
                self.draw_app.app.inform.emit(_("Click on Perimeter point to complete ..."))
                return "Click on perimeter to complete ..."
            else:
                self.draw_app.app.inform.emit(_("Click on Perimeter point to set axis major ..."))
                return "Click on perimeter to complete ..."

        if len(self.points) == 2:
            if self.ui.radius_link_btn.isChecked():
                if self.make() == 'fail':
                    return 'fail'
                return "Done."
            else:
                self.draw_app.app.inform.emit(_("Click on Perimeter point to set axis minor ..."))
                return "Click on perimeter to complete ..."

        if len(self.points) == 3:
            self.make()
            return "Done."

        return ""

    def utility_geometry(self, data=None):
        if self.ui.radius_link_btn.isChecked():  # circle
            if len(self.points) == 1:
                p1 = self.points[0]
                p2 = data

                radius = np.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
                self.ui.radius_x_entry.set_value(radius)
                util_geo = Point(p1).buffer(radius, self.quad_segs(radius))
                self.util_geo = util_geo
                return DrawToolUtilityShape(util_geo)
        else:  # ellipse
            if len(self.points) == 1:
                p1 = self.points[0]
                p2 = data
                radius = np.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
                angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0]) * 180 / math.pi

                axis_min_radius = radius * 0.66
                if axis_min_radius > 3:
                    axis_min_radius = 3
                self.ui.angle_entry.set_value(angle)
                self.ui.radius_x_entry.set_value(radius)
                self.ui.radius_y_entry.set_value(axis_min_radius)
            elif len(self.points) == 2:
                p1 = self.points[0]
                p2 = data

                angle = self.ui.angle_entry.get_value()
                radius = self.ui.radius_x_entry.get_value()
                axis_min_radius = np.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)
                self.ui.radius_y_entry.set_value(axis_min_radius)
            else:
                return

            circle_geo = Point((p1[0], p1[1])).buffer(1, self.quad_segs(max(radius, axis_min_radius)))
            util_geo = scale(circle_geo, radius, axis_min_radius)
            if angle != 0:
                util_geo = rotate(util_geo, angle)

            self.util_geo = util_geo
            return DrawToolUtilityShape(util_geo)

        return None

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key in ('Enter', QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if AppGeoEditor.reject_busy(self.draw_app):
                return 'fail'
            if not self.points:
                return _("Click on the center point before entering a radius.")
        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:

            if not self.points:
                return _("Click on the center point before entering a radius.")
            if self.draw_app.app.mouse_pos[0] != self.points[-1][0] or (
                    self.draw_app.app.mouse_pos[1] != self.points[-1][1] and
                    self.circle_tool.ui.radius_link_btn.isChecked()):
                try:
                    # VisPy keys
                    if self.circle_tool.radius_x == 0.0:
                        self.circle_tool.radius_x = str(key.name)
                    else:
                        self.circle_tool.radius_x = str(self.circle_tool.radius_x) + str(key.name)
                except AttributeError:
                    # Qt keys
                    if self.circle_tool.radius_x == 0.0:
                        self.circle_tool.radius_x = chr(key)
                    else:
                        self.circle_tool.radius_x = str(self.circle_tool.radius_x) + chr(key)

            if self.draw_app.app.mouse_pos[1] != self.points[-1][1] or (
                    self.draw_app.app.mouse_pos[0] != self.points[-1][0] and
                    self.circle_tool.ui.radius_link_btn.isChecked()):
                try:
                    # VisPy keys
                    if self.circle_tool.radius_y == 0.0:
                        self.circle_tool.radius_y = str(key.name)
                    else:
                        self.circle_tool.radius_y = str(self.circle_tool.radius_y) + str(key.name)
                except AttributeError:
                    # Qt keys
                    if self.circle_tool.radius_y == 0.0:
                        self.circle_tool.radius_y = chr(key)
                    else:
                        self.circle_tool.radius_y = str(self.circle_tool.radius_y) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            new_radius_x, new_radius_y = self.circle_tool.radius_x, self.circle_tool.radius_y
            linked = self.circle_tool.ui.radius_link_btn.isChecked()
            if linked:
                new_radius_y = new_radius_x
            x = self.circle_tool.ui.x_entry.get_value()
            y = self.circle_tool.ui.y_entry.get_value()
            angle = self.circle_tool.ui.angle_entry.get_value()
            try:
                valid = all(math.isfinite(v) for v in (x, y, new_radius_x, new_radius_y, angle))
                valid = valid and new_radius_x > 0 and new_radius_y > 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                return _("Enter positive finite radii and finite center coordinates and angle.")
            self.util_geo = Point((x, y)).buffer(new_radius_x, self.quad_segs(new_radius_x)) if linked else \
                rotate(scale(Point((x, y)).buffer(1, self.quad_segs(max(new_radius_x, new_radius_y))),
                             new_radius_x, new_radius_y), angle)
            if self.make() == 'fail':
                return 'fail'
            if self.draw_app.on_shape_complete() == 'fail':
                return 'fail'
            self.draw_app.select_tool("select")
            return "Done."

        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

    def make(self):
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # p1 = self.points[0]
        # p2 = self.points[1]
        # radius = distance(p1, p2)

        if self.util_geo is None or self.util_geo.is_empty or not self.util_geo.is_valid or \
                not math.isfinite(self.util_geo.area) or self.util_geo.area == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Choose a center and a positive finite radius before completing the circle."))
            return 'fail'
        self.geometry = DrawToolShape(self.util_geo.exterior)
        self.geometry.data['type'] = _('Circle')

        self.complete = True
        self.draw_cursor_data(delete=True)

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def clean_up(self):
        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.circle_tool.ui.add_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.circle_tool.ui.circle_frame.hide()


class FCArc(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'arc'

        self.draw_app = draw_app

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_arc.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.inform.emit(_("Click on Center point ..."))

        # Direction of rotation between point 1 and 2.
        # 'cw' or 'ccw'. Switch direction by hitting the
        # 'o' key.
        self.direction = "cw"

        # Mode
        # C12 = Center, p1, p2
        # 12C = p1, p2, Center
        # 132 = p1, p3, p2
        self.mode = "c12"  # Center, p1, p2

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))


    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.points.append(point)

        if len(self.points) == 1:
            if self.mode == 'c12':
                self.draw_app.app.inform.emit(_("Click on Start point ..."))
            elif self.mode == '132':
                self.draw_app.app.inform.emit(_("Click on Point3 ..."))
            else:
                self.draw_app.app.inform.emit(_("Click on Stop point ..."))
            return "Click on 1st point ..."

        if len(self.points) == 2:
            if self.mode == 'c12':
                self.draw_app.app.inform.emit(_("Click on Stop point to complete ..."))
            elif self.mode == '132':
                self.draw_app.app.inform.emit(_("Click on Point2 to complete ..."))
            else:
                self.draw_app.app.inform.emit(_("Click on Center point to complete ..."))
            return "Click on 2nd point to complete ..."

        if len(self.points) == 3:
            self.make()
            return "Done."

        return ""

    def on_key(self, key):
        if key == 'D' or key == QtCore.Qt.Key.Key_D:
            self.direction = 'cw' if self.direction == 'ccw' else 'ccw'
            return '%s: %s' % (_('Direction'), self.direction.upper())

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'M' or key == QtCore.Qt.Key.Key_M:
            # delete the possible points made before this action; we want to start anew
            self.points[:] = []
            # and delete the utility geometry made up until this point
            self.draw_app.delete_utility_geometry()

            if self.mode == 'c12':
                self.mode = '12c'
                return _('Mode: Start -> Stop -> Center. Click on Start point ...')
            elif self.mode == '12c':
                self.mode = '132'
                return _('Mode: Point1 -> Point3 -> Point2. Click on Point1 ...')
            else:
                self.mode = 'c12'
                return _('Mode: Center -> Start -> Stop. Click on Center point ...')

    def utility_geometry(self, data=None):
        if len(self.points) == 1:  # Show the radius
            center = self.points[0]
            p1 = data

            return DrawToolUtilityShape(LineString([center, p1]))

        if len(self.points) == 2:  # Show the arc

            if self.mode == 'c12':
                center = self.points[0]
                p1 = self.points[1]
                p2 = data

                radius = np.sqrt((center[0] - p1[0]) ** 2 + (center[1] - p1[1]) ** 2)
                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

                return DrawToolUtilityShape([LineString(arc(center, radius, startangle, stopangle,
                                                            self.direction, self.steps(radius))),
                                             Point(center)])

            elif self.mode == '132':
                p1 = np.array(self.points[0])
                p3 = np.array(self.points[1])
                p2 = np.array(data)

                try:
                    center, radius, t = three_point_circle(p1, p2, p3)
                except TypeError:
                    return

                direction = 'cw' if np.sign(t) > 0 else 'ccw'

                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p3[1] - center[1], p3[0] - center[0])

                return DrawToolUtilityShape([LineString(arc(center, radius, startangle, stopangle,
                                                            direction, self.steps(radius))),
                                             Point(center), Point(p1), Point(p3)])

            else:  # '12c'
                p1 = np.array(self.points[0])
                p2 = np.array(self.points[1])

                # Midpoint
                a = (p1 + p2) / 2.0

                # Parallel vector
                c = p2 - p1

                # Perpendicular vector
                b = np.dot(c, np.array([[0, -1], [1, 0]], dtype=np.float32))
                b /= numpy_norm(b)

                # Distance
                t = distance(data, a)

                # Which side? Cross product with c.
                # cross(M-A, B-A), where line is AB and M is the test point.
                side = (data[0] - p1[0]) * c[1] - (data[1] - p1[1]) * c[0]
                t *= np.sign(side)

                # Center = a + bt
                center = a + b * t

                radius = numpy_norm(center - p1)
                startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
                stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

                return DrawToolUtilityShape([LineString(arc(center, radius, startangle, stopangle,
                                                            self.direction, self.steps(radius))),
                                             Point(center)])

        return None

    def make(self):

        if self.mode == 'c12':
            center = self.points[0]
            p1 = self.points[1]
            p2 = self.points[2]

            radius = distance(center, p1)
            startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])
            self.geometry = DrawToolShape(LineString(arc(center, radius, startangle, stopangle,
                                                         self.direction, self.steps(radius))))

        elif self.mode == '132':
            p1 = np.array(self.points[0])
            p3 = np.array(self.points[1])
            p2 = np.array(self.points[2])

            center, radius, t = three_point_circle(p1, p2, p3)
            direction = 'cw' if np.sign(t) > 0 else 'ccw'

            startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stopangle = np.arctan2(p3[1] - center[1], p3[0] - center[0])

            self.geometry = DrawToolShape(LineString(arc(center, radius, startangle, stopangle,
                                                         direction, self.steps(radius))))

        else:  # self.mode == '12c'
            p1 = np.array(self.points[0])
            p2 = np.array(self.points[1])
            pc = np.array(self.points[2])

            # Midpoint
            a = (p1 + p2) / 2.0

            # Parallel vector
            c = p2 - p1

            # Perpendicular vector
            b = np.dot(c, np.array([[0, -1], [1, 0]], dtype=np.float32))
            b /= numpy_norm(b)

            # Distance
            t = distance(pc, a)

            # Which side? Cross product with c.
            # cross(M-A, B-A), where line is AB and M is the test point.
            side = (pc[0] - p1[0]) * c[1] - (pc[1] - p1[1]) * c[0]
            t *= np.sign(side)

            # Center = a + bt
            center = a + b * t

            radius = numpy_norm(center - p1)
            startangle = np.arctan2(p1[1] - center[1], p1[0] - center[0])
            stopangle = np.arctan2(p2[1] - center[1], p2[0] - center[0])

            self.geometry = DrawToolShape(LineString(arc(center, radius, startangle, stopangle,
                                                         self.direction, self.steps(radius))))
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()

        self.geometry.data['type'] = _('Arc')
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class FCRectangle(FCShapeTool):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'rectangle'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.plugin_name = _("Rectangle")
        self.storage = self.draw_app.storage

        self.util_geo = None

        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.rect_tool = RectangleEditorTool(self.app, self.draw_app, plugin_name=self.plugin_name)
        self.rect_tool.run()

        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.draw_app.app.inform.emit(_("Click on 1st corner ..."))

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        modifiers = QtWidgets.QApplication.keyboardModifiers()
        if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            # deselect all shapes
            self.draw_app.selected = []
            try:
                __, closest_shape = self.storage.nearest(point)
                self.draw_app.selected.append(closest_shape)
            except StopIteration:
                return ""

            if self.draw_app.selected:
                self.draw_app.plot_changes()
                self.rect_tool.mode = 'change'
                sel_shape_geo = self.draw_app.selected[-1].geo
                geo_bounds = sel_shape_geo.bounds
                # assuming that the default setting for anchor is center
                origin_x_sel_geo = geo_bounds[0] + ((geo_bounds[2] - geo_bounds[0]) / 2)
                self.rect_tool.ui.x_entry.set_value(origin_x_sel_geo)
                origin_y_sel_geo = geo_bounds[1] + ((geo_bounds[3] - geo_bounds[1]) / 2)
                self.rect_tool.ui.y_entry.set_value(origin_y_sel_geo)
                self.draw_app.app.inform.emit(
                    _("Click on 1st corner to add a new rectangle or Apply to change the selection."))
            return

        if not all(math.isfinite(v) for v in point) or (self.points and
                (point[0] == self.points[0][0] or point[1] == self.points[0][1])):
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Choose a finite opposite corner with nonzero length and width."))
            return 'fail'
        if self.points:
            self.utility_geometry(data=point)
        self.rect_tool.mode = 'add'
        self.points.append(point)
        if len(self.points) == 1:
            self.draw_app.app.inform.emit(_("Click on opposite corner to complete ..."))
            return "Click on opposite corner to complete ..."
        if len(self.points) == 2:
            if self.make() == 'fail':
                return 'fail'
            return "Done."

        return ""

    def utility_geometry(self, data=None):
        if len(self.points) == 1:
            p1 = self.points[0]
            p2 = data

            corner_type = self.rect_tool.ui.corner_radio.get_value()
            corner_radius = self.rect_tool.ui.radius_entry.get_value()
            length = abs(p1[0] - p2[0])
            width = abs(p1[1] - p2[1])

            if corner_radius == 0.0:
                corner_type = 's'
            if corner_type in ['r', 'b']:
                length -= 2 * corner_radius
                width -= 2 * corner_radius

            base_util_geo = Polygon([p1, (p2[0], p1[1]), p2, (p1[0], p2[1])])
            center_pt = base_util_geo.centroid
            cx = center_pt.x
            cy = center_pt.y
            minx = cx - (length / 2)
            miny = cy - (width / 2)
            maxx = cx + (length / 2)
            maxy = cy + (width / 2)

            if length < 0 or width < 0:
                corner_type = 's'

            if corner_type == 'r':
                util_geo = box(minx, miny, maxx, maxy).buffer(
                    corner_radius, join_style=base.JOIN_STYLE.round,
                    quad_segs=self.quad_segs(corner_radius)).exterior
            elif corner_type == 'b':
                util_geo = box(minx, miny, maxx, maxy).buffer(
                    corner_radius, join_style=base.JOIN_STYLE.bevel,
                    quad_segs=self.quad_segs(corner_radius)).exterior
            else:  # 's' - square
                util_geo = base_util_geo.exterior

            self.util_geo = util_geo
            return DrawToolUtilityShape(util_geo)

        return None

    def make(self):
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        # p1 = self.points[0]
        # p2 = self.points[1]
        # # self.geometry = LinearRing([p1, (p2[0], p1[1]), p2, (p1[0], p2[1])])
        # geo = LinearRing([p1, (p2[0], p1[1]), p2, (p1[0], p2[1])])

        if self.util_geo is None or self.util_geo.is_empty or not self.util_geo.is_valid or \
                not math.isfinite(Polygon(self.util_geo).area) or Polygon(self.util_geo).area == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Choose two finite opposite corners with nonzero length and width."))
            return 'fail'
        self.geometry = DrawToolShape(self.util_geo)
        self.geometry.data['type'] = _('Rectangle')

        self.complete = True
        self.draw_cursor_data(delete=True)

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key in ('Enter', QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            if AppGeoEditor.reject_busy(self.draw_app):
                return 'fail'
            if not self.points:
                return _("Click on the first corner point before entering dimensions.")
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:

            if not self.points:
                return _("Click on the first corner point before entering dimensions.")
            if self.draw_app.app.mouse_pos[0] != self.points[-1][0]:
                try:
                    # VisPy keys
                    if self.rect_tool.length == 0:
                        self.rect_tool.length = str(key.name)
                    else:
                        self.rect_tool.length = str(self.rect_tool.length) + str(key.name)
                except AttributeError:
                    # Qt keys
                    if self.rect_tool.length == 0:
                        self.rect_tool.length = chr(key)
                    else:
                        self.rect_tool.length = str(self.rect_tool.length) + chr(key)
            if self.draw_app.app.mouse_pos[1] != self.points[-1][1]:
                try:
                    # VisPy keys
                    if self.rect_tool.width == 0:
                        self.rect_tool.width = str(key.name)
                    else:
                        self.rect_tool.width = str(self.rect_tool.width) + str(key.name)
                except AttributeError:
                    # Qt keys
                    if self.rect_tool.width == 0:
                        self.rect_tool.width = chr(key)
                    else:
                        self.rect_tool.width = str(self.rect_tool.width) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            new_x, new_y = self.points[-1][0], self.points[-1][1]
            target_length, target_width = self.rect_tool.length, self.rect_tool.width
            try:
                valid = all(math.isfinite(v) for v in (new_x, new_y, target_length, target_width))
                valid = valid and target_length != 0 and target_width != 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                return _("Enter finite nonzero length and width values.")
            new_x += target_length
            new_y += target_width
            if not all(math.isfinite(v) for v in (new_x, new_y)):
                return _("Enter smaller dimensions so the projected coordinates remain finite.")

            if self.points[-1] != (new_x, new_y):
                self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)

            if len(self.points) > 0:
                msg = '%s: (%s, %s). %s' % (
                    _("Projected"), str(self.rect_tool.length), str(self.rect_tool.width),
                    _("Click to complete ..."))
                self.draw_app.app.inform.emit(msg)

    def clean_up(self):
        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        try:
            self.rect_tool.ui.add_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass
        self.rect_tool.ui.rect_frame.hide()


class FCPolygon(FCShapeTool):
    """
    Resulting type: Polygon
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'polygon'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.plugin_name = _("Polygon")

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.polygon_tool = PathEditorTool(self.app, self.draw_app, plugin_name=self.plugin_name)
        self.polygon_tool.run()
        self.new_segment = True

        # number of points added by each click (the corner point + the clicked point), used by Backspace
        self.click_sizes = []

        self.cursor_data_control = True

        self.app.ui.notebook.setTabText(2, self.plugin_name)
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

        self.draw_app.app.inform.emit("%s %s" % (_("Click on 1st corner ..."),
                                                 bend_mode_message(self.draw_app.bend_mode)))

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.draw_app.in_action = True
        if point != self.points[-1:]:
            added = corner_points(self.draw_app, self.points, point)
            self.points.extend(added)
            self.__dict__.setdefault('click_sizes', []).append(len(added))

        if len(self.points) > 0:
            self.draw_app.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))
            return "Click on next point or hit ENTER to complete ..."

        return ""

    def make(self):
        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        if self.points[-1] == self.points[-2]:
            self.points.pop(-1)

        # self.geometry = LinearRing(self.points)
        self.geometry = DrawToolShape(LinearRing(self.points))
        self.draw_app.in_action = False
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()
        self.geometry.data['type'] = self.plugin_name
        self.draw_cursor_data(delete=True)
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def utility_geometry(self, data=None):
        if len(self.points) == 1:
            temp_points = [x for x in self.points]
            temp_points.extend(corner_points(self.draw_app, temp_points, data))
            return DrawToolUtilityShape(LineString(temp_points))
        elif len(self.points) > 1:
            temp_points = [x for x in self.points]
            temp_points.extend(corner_points(self.draw_app, temp_points, data))
            return DrawToolUtilityShape(LinearRing(temp_points))
        else:
            return None

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'Backspace' or key == QtCore.Qt.Key.Key_Backspace:
            if len(self.points) > 0:
                backtrack_points(self)
                # Remove any previous utility shape
                self.draw_app.tool_shape.clear(update=False)
                geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
                self.draw_app.draw_utility_geometry(geo=geo)
                if geo:
                    return _("Backtracked one point ...")
                else:
                    self.draw_app.app.inform.emit(_("Click on 1st corner ..."))

        # Corner Mode: T = next mode, R = previous mode
        if key == 'T' or key == QtCore.Qt.Key.Key_T:
            return cycle_bend_mode(self, forward=True)

        if key == 'R' or key == QtCore.Qt.Key.Key_R:
            return cycle_bend_mode(self, forward=False)

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.polygon_tool.length == 0 or self.new_segment is True:
                    self.polygon_tool.length = str(key.name)
                    self.new_segment = False
                else:
                    self.polygon_tool.length = str(self.polygon_tool.length) + str(key.name)
            except AttributeError:
                # Qt keys
                if self.polygon_tool.length == 0 or self.new_segment is True:
                    self.polygon_tool.length = chr(key)
                    self.new_segment = False
                else:
                    self.polygon_tool.length = str(self.polygon_tool.length) + chr(key)

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if self.polygon_tool.length != 0:
                target_length = self.polygon_tool.length
                if target_length is None:
                    self.polygon_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.points[-1]
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.sqrt((last_pt[0] - first_pt[0])**2 + (last_pt[1] - first_pt[1])**2)
                if seg_length == 0.0:
                    return
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.points = []
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if self.points[-1] != (new_x, new_y):
                    self.points.append((new_x, new_y))
                    self.__dict__.setdefault('click_sizes', []).append(1)
                    self.new_segment = True
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    if len(self.points) > 0:
                        msg = '%s: %s. %s' % (
                            _("Projected"), str(self.polygon_tool.length),
                            _("Click on next Point or click right mouse button to complete ..."))
                        self.draw_app.app.inform.emit(msg)

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()

        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.polygon_tool.on_tab_close()


class FCPath(FCShapeTool):
    """
    Resulting type: LineString
    """

    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.draw_app = draw_app
        self.name = 'path'
        self.app = self.draw_app.app

        # show the cursor data
        self.cursor_data_control = True

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.set_bend_cursor(restore=False)

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.path_tool = PathEditorTool(self.app, self.draw_app, plugin_name=_("Path"))
        self.path_tool.run()

        self.new_segment = True
        self.close_x = 0.0
        self.close_y = 0.0

        # number of points added by each click (the corner point + the clicked point), used by Backspace
        self.click_sizes = []

        self.app.ui.notebook.setTabText(2, _("Path"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

        self.draw_app.app.inform.emit("%s %s" % (_("Click on 1st point ..."),
                                                 bend_mode_message(self.draw_app.bend_mode)))

    def set_bend_cursor(self, restore=True):
        """
        Set the cursor icon that matches the current corner mode.

        :param restore: True to first remove the cursor set previously by this tool
        """
        if restore:
            try:
                QtGui.QGuiApplication.restoreOverrideCursor()
            except Exception:
                pass
        try:
            self.cursor = QtGui.QCursor(QtGui.QPixmap(
                self.draw_app.app.resource_location + '/aero_path%s.png' % self.draw_app.bend_mode))
            QtGui.QGuiApplication.setOverrideCursor(self.cursor)
        except Exception as e:
            self.app.log.error("FCPath.set_bend_cursor() -> %s" % str(e))

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.draw_app.in_action = True
        modifier = QtWidgets.QApplication.keyboardModifiers()
        if modifier == Qt.KeyboardModifier.ShiftModifier:
            new_point = self.close_x, self.close_y
            if new_point != self.points[-1:]:
                self.points.append(new_point)
                self.__dict__.setdefault('click_sizes', []).append(1)
        else:
            if point != self.points[-1:]:
                added = corner_points(self.draw_app, self.points, point)
                self.points.extend(added)
                self.__dict__.setdefault('click_sizes', []).append(len(added))

        if len(self.points) > 0:
            self.draw_app.app.inform.emit(_("Click on next Point or click right mouse button to complete ..."))
            return "Click on next point or hit ENTER to complete ..."

        return ""

    def make(self):
        if len(self.points) < 2 or not all(math.isfinite(v) for p in self.points for v in p) or \
                not any(p != self.points[0] for p in self.points[1:]):
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                          _("Choose at least two distinct finite points to complete the path."))
            return 'fail'
        self.geometry = DrawToolShape(LineString(self.points))
        self.name = 'path'

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.draw_app.in_action = False
        self.complete = True

        self.draw_app.app.jump_signal.disconnect()
        self.geometry.data['type'] = _('Path')
        self.draw_cursor_data(delete=True)
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def utility_geometry(self, data=None):
        if len(self.points) > 0:
            modifier = QtWidgets.QApplication.keyboardModifiers()
            if modifier == Qt.KeyboardModifier.ShiftModifier:
                temp_points = [x for x in self.points]
                if not temp_points:
                    return DrawToolUtilityShape(None)

                x_start = temp_points[-1][0]
                y_start = temp_points[-1][1]
                x_end = data[0]
                y_end = data[1]
                dx = x_end - x_start
                dy = y_end - y_start
                det_angle = self.update_angle(dx, dy)

                new_x, new_y = data

                if 0 <= det_angle <= 10:
                    new_x = data[0]
                    new_y = y_start

                if 80 <= det_angle <= 90:
                    new_x = x_start
                    new_y = data[1]

                if 35 <= det_angle <= 55:
                    new_x, new_y = self.closest_point_to_45_degrees(origin=temp_points[-1], current=data)

                self.close_x, self.close_y = new_x, new_y

                temp_points.append([new_x, new_y])
            else:
                temp_points = [x for x in self.points]
                temp_points.extend(corner_points(self.draw_app, temp_points, data))

            return DrawToolUtilityShape(LineString(temp_points))

        return None

    @staticmethod
    def closest_point_to_45_degrees(origin, current):
        # Calculate vector from origin to current point
        vector_x = current[0] - origin[0]
        vector_y = current[1] - origin[1]

        # Calculate the angle between the vector and the x-axis
        angle = math.atan2(vector_y, vector_x)

        # Calculate the angle of the 45-degree line
        angle_45 = math.radians(45)

        # Determine the angle to the closest point on the 45-degree line
        closest_angle = round(angle / angle_45) * angle_45

        # Calculate the coordinates of the closest point
        closest_distance = math.sqrt(vector_x ** 2 + vector_y ** 2)
        closest_point_x = origin[0] + closest_distance * math.cos(closest_angle)
        closest_point_y = origin[1] + closest_distance * math.sin(closest_angle)

        return closest_point_x, closest_point_y

    def update_angle(self, dx, dy):
        try:
            angle = math.degrees(math.atan2(abs(dy), abs(dx)))
            # if angle < 0:
            #     angle += 360
        except Exception as e:
            self.app.log.error("FCPath.update_angle() -> %s" % str(e))
            return None
        return angle

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key == 'Backspace' or key == QtCore.Qt.Key.Key_Backspace:
            if len(self.points) > 0:
                backtrack_points(self)
                self.draw_app.in_action = bool(self.points)
                self.new_segment = True
                # Remove any previous utility shape
                self.draw_app.tool_shape.clear(update=False)
                geo = self.utility_geometry(data=(self.draw_app.snap_x, self.draw_app.snap_y))
                self.draw_app.draw_utility_geometry(geo=geo)
                if geo:
                    return _("Backtracked one point ...")
                else:
                    return _("Click on 1st point ...")

        # Corner Mode: T = next mode, R = previous mode
        if key == 'T' or key == QtCore.Qt.Key.Key_T or key == 'R' or key == QtCore.Qt.Key.Key_R:
            msg = cycle_bend_mode(self, forward=(key == 'T' or key == QtCore.Qt.Key.Key_T))
            self.set_bend_cursor()
            return msg

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.new_segment is True:
                    self.path_tool.length = str(key.name)
                    self.new_segment = False
                else:
                    self.path_tool.length = self.path_tool.ui.project_line_entry.text() + str(key.name)
            except AttributeError:
                # Qt keys
                if self.new_segment is True:
                    self.path_tool.length = key if isinstance(key, str) else chr(key)
                    self.new_segment = False
                else:
                    self.path_tool.length = self.path_tool.ui.project_line_entry.text() + \
                        (key if isinstance(key, str) else chr(key))

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if AppGeoEditor.reject_busy(self.draw_app):
                return 'fail'
            if not self.points:
                return _("Click on the first point before entering a length.")
            try:
                valid = math.isfinite(self.path_tool.length) and self.path_tool.length != 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                return _("Enter a finite nonzero length.")
            if self.path_tool.length != 0:
                # target_length = self.interpolate_length.replace(',', '.')
                # try:
                #     target_length = eval(target_length)
                # except SyntaxError as err:
                #     ret = '%s: %s' % (str(err).capitalize(), self.interpolate_length)
                #     self.interpolate_length = ''
                #     return ret

                target_length = self.path_tool.length
                if target_length is None:
                    self.path_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.points[-1]
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.hypot(last_pt[0] - first_pt[0], last_pt[1] - first_pt[1])
                if not math.isfinite(seg_length) or seg_length == 0.0:
                    return _("Move the cursor away from the last point to set a finite direction.")
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.points = []
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if not all(math.isfinite(v) for v in (new_x, new_y)):
                    return _("Enter a smaller length so the projected coordinates remain finite.")
                if self.points[-1] != (new_x, new_y):
                    self.points.append((new_x, new_y))
                    self.__dict__.setdefault('click_sizes', []).append(1)
                    self.new_segment = True
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    if len(self.points) > 0:
                        msg = '%s: %s. %s' % (
                            _("Projected"), str(self.path_tool.length),
                            _("Click on next Point or click right mouse button to complete ..."))
                        self.draw_app.app.inform.emit(msg)
                        # self.interpolate_length = ''
                        # return "Click on next point or hit ENTER to complete ..."

    def clean_up(self):
        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.path_tool.disconnect_signals()
        self.path_tool.ui.path_tool_frame.hide()


class FCSelect(DrawTool):
    def __init__(self, draw_app: AppGeoEditor):
        DrawTool.__init__(self, draw_app)
        self.name = 'select'
        self.draw_app: AppGeoEditor = draw_app

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.storage = self.draw_app.storage
        # self.shape_buffer = self.draw_app.shape_buffer
        # self.selected = self.draw_app.selected

        # make sure that the cursor text from the FCPath is deleted
        if self.draw_app.app.plotcanvas.text_cursor.parent:
            self.draw_app.app.plotcanvas.text_cursor.parent = None
            self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        # make sure that the Tools tab is removed
        try:
            self.draw_app.app.ui.notebook.removeTab(2)
        except Exception:
            pass

    def click_release(self, point):
        """
        Selects the shape under the click; see AppGeoEditor.click_select().

        :param point:   (x, y) of the click
        :return:
        """
        self.draw_app.click_select(point)
        return ""

    def clean_up(self):
        pass


class FCExplode(FCShapeTool):
    keeps_direction = True

    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'explode'
        self.draw_app = draw_app

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.storage = self.draw_app.storage
        self.origin = (0, 0)
        self.destination = None

        self.draw_app.active_tool = self
        if len(self.draw_app.get_selected()) == 0:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("No shape selected."))
        else:
            self.make()

    def make(self):
        to_be_deleted_list = []
        lines = []

        for shape in self.draw_app.get_selected():
            geo = shape.geo
            # the lines that replace this shape; a shape is removed only when it is replaced (an open path or a
            # polygon was removed with nothing in its place, and a Multi* shape threw away the lines collected before)
            shape_lines = []

            if geo.geom_type == 'MultiLineString':
                shape_lines = [line for line in geo.geoms]
            elif geo.geom_type == 'MultiPolygon':
                for poly in geo.geoms:
                    shape_lines.append(poly.exterior)
                    shape_lines += list(poly.interiors)
            elif geo.is_ring or geo.geom_type == 'Polygon':
                if geo.is_ring:
                    geo = Polygon(geo)
                ext_coords = list(geo.exterior.coords)

                for c in range(len(ext_coords)):
                    if c < len(ext_coords) - 1:
                        shape_lines.append(LineString([ext_coords[c], ext_coords[c + 1]]))

                for int_geo in geo.interiors:
                    int_coords = list(int_geo.coords)
                    for c in range(len(int_coords)):
                        if c < len(int_coords) - 1:
                            shape_lines.append(LineString([int_coords[c], int_coords[c + 1]]))
            elif geo.geom_type == 'LineString' and len(geo.coords) > 2:
                # an open path (or an arc) is exploded in its segments; a single segment stays as it is
                coords = list(geo.coords)
                for c in range(len(coords) - 1):
                    shape_lines.append(LineString([coords[c], coords[c + 1]]))

            if shape_lines:
                to_be_deleted_list.append(shape)
                lines += shape_lines

        self.removed_shapes = to_be_deleted_list

        geo_list = []
        for line in lines:
            line_geo = DrawToolShape(line)
            line_geo.data['type'] = _('Path')
            geo_list.append(line_geo)

        self.geometry = geo_list
        self.draw_app.on_shape_complete()
        self.draw_app.app.inform.emit('[success] %s...' % _("Done."))

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class FCMove(FCShapeTool):
    keeps_direction = True

    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'move'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.storage = self.draw_app.storage

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.origin = None
        self.destination = None
        self.sel_limit = self.draw_app.app.options.get(
            "geometry_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "geometry_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("geometry_editor_sel_limit")
            )
        )
        self.selection_shape = self.selection_bbox()

        self.cursor_data_control = True

        if len(self.draw_app.get_selected()) == 0:
            self.has_selection = False
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                          (_("No shape selected."), _("Select some shapes or cancel.")))
        else:
            self.has_selection = True
            self.draw_app.app.inform.emit(_("Click on reference location ..."))

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.move_tool = PathEditorTool(self.app, self.draw_app, plugin_name=_("Move"))
        self.move_tool.run()
        self.new_segment = True

        self.app.ui.notebook.setTabText(2, _("Move"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

    def set_origin(self, origin):
        self.draw_app.app.inform.emit(_("Click on destination point ..."))
        self.origin = origin

    def click(self, point):
        if AppGeoEditor.reject_busy(self.draw_app):
            return 'fail'
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if self.has_selection is False:
            self.draw_app.selected = []
            try:
                __, closest_shape = self.storage.nearest(point)
                self.draw_app.selected.append(closest_shape)
            except StopIteration:
                return ""

            if not self.draw_app.selected:
                self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                              (_("No shape selected."), _("Select some shapes or cancel.")))
                return

            self.has_selection = True
            self.draw_app.plot_changes()
            self.selection_shape = self.selection_bbox()
            # self.draw_app.plot_all()
            self.draw_app.app.inform.emit(_("Click on reference location ..."))
            # This click picks with raw coordinates, but it also establishes the move reference.
            origin = self.draw_app.snap(*point) if getattr(self.draw_app.app, 'grid_status', lambda: False)() else point
            self.set_origin(origin)
            return

        if self.origin is None:
            self.points.append(point)
            self.set_origin(point)
            self.selection_shape = self.selection_bbox()
            return "Click on final location."
        else:
            self.destination = point
            if self.make() == 'fail':
                return 'fail'
            # self.draw_app.app.worker_task.emit(({'fcn': self.make,
            #                                      'params': []}))
            return "Done."

    def make(self):
        if AppGeoEditor.reject_busy(self.draw_app):
            return 'fail'
        with self.draw_app.app.proc_container.new('%s...' % _("Moving")):
            # Create new geometry
            dx = self.destination[0] - self.origin[0]
            dy = self.destination[1] - self.origin[1]
            # 1 vectorized batch; Shapely geometry is immutable, so the source needs no deepcopy
            selected = self.draw_app.get_selected()
            self.geometry = [DrawToolShape(geo) for geo in translate_geometry([geom.geo for geom in selected], dx, dy)]
            for source, result in zip(selected, self.geometry):
                result.data = deepcopy(source.data)
                result.data['bounds'] = result.bounds()
                DrawToolShape._affine_sources[result] = (
                    ref(source), source.geo, result.geo, affine_translate_matrix(dx, dy))

            # Completion removes the originals and inserts the replacements in one committed operation.
            self.removed_shapes = list(selected)
            self.complete = True

    def selection_bbox(self):
        geo_list = []
        for select_shape in self.draw_app.get_selected():
            if select_shape:
                geometric_data = select_shape.geo
            else:
                continue
            try:
                w_geo = geometric_data.geoms if \
                    isinstance(geometric_data, (MultiPolygon, MultiLineString)) else geometric_data
                geo_list += [g for g in w_geo]
            except TypeError:
                geo_list.append(geometric_data)

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)

        pt1 = (xmin, ymin)
        pt2 = (xmax, ymin)
        pt3 = (xmax, ymax)
        pt4 = (xmin, ymax)

        return Polygon([pt1, pt2, pt3, pt4])

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        geo_list = []

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        if len(self.draw_app.get_selected()) <= self.sel_limit:
            try:
                geo_list = translate_geometry([geom.geo for geom in self.draw_app.get_selected()], dx, dy)
            except AttributeError:
                self.draw_app.select_tool('select')
                self.draw_app.selected = []
                return
            return DrawToolUtilityShape(geo_list)
        else:
            try:
                ss_el = translate(self.selection_shape, xoff=dx, yoff=dy)
            except ValueError:
                ss_el = None
            return DrawToolUtilityShape(ss_el)

    def select_shapes(self, pos):
        # list where we store the overlapped shapes under our mouse left click position
        over_shape_list = []

        try:
            _, closest_shape = self.storage.nearest(pos)
        except StopIteration:
            return ""

        over_shape_list.append(closest_shape)

        try:
            # if there is no shape under our click then deselect all shapes
            # it will not work for 3rd method of click selection
            if not over_shape_list:
                self.draw_app.selected = []
                self.draw_app.draw_shape_idx = -1
            else:
                # if there are shapes under our click then advance through the list of them, one at the time in a
                # circular way
                self.draw_app.draw_shape_idx = (AppGeoEditor.draw_shape_idx + 1) % len(over_shape_list)
                try:
                    obj_to_add = over_shape_list[int(AppGeoEditor.draw_shape_idx)]
                except IndexError:
                    return

                key_modifier = QtWidgets.QApplication.keyboardModifiers()
                if self.draw_app.app.options.get(
                        "global_mselect_key",
                        self.draw_app.app.defaults.get(
                            "global_mselect_key",
                            self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                        )) == 'Control':
                    # if CONTROL key is pressed then we add to the selected list the current shape but if it's
                    # already in the selected list, we removed it. Therefore, first click selects, second deselects.
                    if key_modifier == Qt.KeyboardModifier.ControlModifier:
                        if obj_to_add in self.draw_app.selected:
                            self.draw_app.selected.remove(obj_to_add)
                        else:
                            self.draw_app.selected.append(obj_to_add)
                    else:
                        self.draw_app.selected = []
                        self.draw_app.selected.append(obj_to_add)
                else:
                    if key_modifier == Qt.KeyboardModifier.ShiftModifier:
                        if obj_to_add in self.draw_app.selected:
                            self.draw_app.selected.remove(obj_to_add)
                        else:
                            self.draw_app.selected.append(obj_to_add)
                    else:
                        self.draw_app.selected = []
                        self.draw_app.selected.append(obj_to_add)

        except Exception as e:
            log.error("[ERROR] Something went bad. %s" % str(e))
            raise

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.new_segment is True:
                    self.move_tool.length = str(key.name)
                    self.new_segment = False
                else:
                    self.move_tool.length = self.move_tool.ui.project_line_entry.text() + str(key.name)
            except AttributeError:
                # Qt keys
                if self.new_segment is True:
                    self.move_tool.length = key if isinstance(key, str) else chr(key)
                    self.new_segment = False
                else:
                    self.move_tool.length = self.move_tool.ui.project_line_entry.text() + \
                        (key if isinstance(key, str) else chr(key))

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if AppGeoEditor.reject_busy(self.draw_app):
                return 'fail'
            if self.origin is None:
                return _("Click on a reference point before entering a length.")
            try:
                valid = math.isfinite(self.move_tool.length) and self.move_tool.length != 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                return _("Enter a finite nonzero length.")
            if self.move_tool.length != 0:
                target_length = self.move_tool.length
                if target_length is None:
                    self.move_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.origin
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.hypot(last_pt[0] - first_pt[0], last_pt[1] - first_pt[1])
                if not math.isfinite(seg_length) or seg_length == 0.0:
                    return _("Move the cursor away from the reference point to set a finite direction.")
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.points = []
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if not all(math.isfinite(v) for v in (new_x, new_y)):
                    return _("Enter a smaller length so the projected coordinates remain finite.")
                if first_pt != (new_x, new_y):
                    self.points.append((new_x, new_y))
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    self.destination = (new_x, new_y)
                    if self.make() == 'fail':
                        return 'fail'
                    if self.draw_app.on_shape_complete() == 'fail':
                        return 'fail'
                    self.draw_app.select_tool("select")
                    return "Done."

    def clean_up(self):
        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.move_tool.disconnect_signals()
        self.move_tool.ui.path_tool_frame.hide()


class FCCopy(FCShapeTool):
    keeps_direction = True

    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'copy'
        self.draw_app = draw_app
        self.app = self.draw_app.app
        self.storage = self.draw_app.storage

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.origin = None
        self.destination = None
        self.sel_limit = self.draw_app.app.options.get(
            "geometry_editor_sel_limit",
            self.draw_app.app.defaults.get(
                "geometry_editor_sel_limit",
                self.draw_app.app.defaults.factory_defaults.get("geometry_editor_sel_limit")
            )
        )
        self.selection_shape = self.selection_bbox()

        # store here the utility geometry, so we can use it on the last step
        self.util_geo = None

        self.clicked_postion = None

        self.cursor_data_control = True

        if len(self.draw_app.get_selected()) == 0:
            self.has_selection = False
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                          (_("No shape selected."), _("Select some shapes or cancel.")))
        else:
            self.has_selection = True
            self.draw_app.app.inform.emit(_("Click on reference location ..."))

        self.draw_app.app.plotcanvas.view.camera.zoom_callback = self.draw_cursor_data
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.copy_tool = CopyEditorTool(self.app, self.draw_app, plugin_name=_("Copy"))
        self.copy_tool.run()
        self.new_segment = True

        self.app.ui.notebook.setTabText(2, _("Copy"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

    def set_origin(self, origin):
        self.draw_app.app.inform.emit(_("Click on destination point ..."))
        self.origin = origin

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        self.clicked_postion = point

        if self.has_selection is False:
            self.draw_app.selected = []
            try:
                __, closest_shape = self.storage.nearest(point)
                self.draw_app.selected.append(closest_shape)
            except StopIteration:
                return ""

            if not self.draw_app.selected:
                self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' %
                                              (_("No shape selected."), _("Select some shapes or cancel.")))
                return

            self.has_selection = True
            self.draw_app.plot_changes()
            self.selection_shape = self.selection_bbox()
            # self.draw_app.plot_all()
            self.draw_app.app.inform.emit(_("Click on reference location ..."))
            return

        if self.origin is None:
            self.points.append(point)
            self.set_origin(point)
            self.selection_shape = self.selection_bbox()
            return "Click on final location."
        else:
            self.destination = point
            self.make()
            # self.draw_app.app.worker_task.emit(({'fcn': self.make,
            #                                      'params': []}))
            return "Done."

    def make(self):
        # Always generate at the destination; the last preview may be stale or only a bounding box.
        dx = self.destination[0] - self.origin[0]
        dy = self.destination[1] - self.origin[1]
        if self.copy_tool.ui.mode_radio.get_value() == 'n':
            geometry = translate_geometry([shape.geo for shape in self.draw_app.get_selected()], dx, dy)
        else:
            self.util_geo = self.array_util_geometry((dx, dy))
            if self.util_geo is None:
                return
            geometry = self.util_geo.geo
            # The last circular preview shape is the radius guide, not a copy.
            if self.copy_tool.ui.array_type_radio.get_value() == 'circular':
                geometry = geometry[:-1]
        self.geometry = [DrawToolShape(shp) for shp in geometry]
        selected = list(self.draw_app.get_selected())
        for i, result in enumerate(self.geometry):
            result.data = deepcopy(selected[i % len(selected)].data)
            result.data['bounds'] = result.bounds()

        self.complete = True
        self.origin = None
        self.draw_cursor_data(delete=True)
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def selection_bbox(self):
        geo_list = []
        for select_shape in self.draw_app.get_selected():
            geometric_data = select_shape.geo
            try:
                for g in geometric_data:
                    geo_list.append(g)
            except TypeError:
                geo_list.append(geometric_data)

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_list)

        pt1 = (xmin, ymin)
        pt2 = (xmax, ymin)
        pt3 = (xmax, ymax)
        pt4 = (xmin, ymax)

        return Polygon([pt1, pt2, pt3, pt4])

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        selected = self.draw_app.get_selected()
        copy_mode = self.copy_tool.ui.mode_radio.get_value()
        copy_count = 1
        if copy_mode == 'a':
            if self.copy_tool.ui.array_type_radio.get_value() == '2D':
                copy_count = self.copy_tool.ui.rows.get_value() * self.copy_tool.ui.columns.get_value()
            else:
                copy_count = self.copy_tool.ui.array_size_entry.get_value()

        if len(selected) * copy_count <= self.sel_limit:
            if copy_mode == 'n':
                try:
                    geo_list = translate_geometry([geom.geo for geom in selected], dx, dy)
                except AttributeError:
                    self.draw_app.select_tool('select')
                    self.draw_app.selected = []
                    return
                self.util_geo = DrawToolUtilityShape(geo_list)
            else:
                self.util_geo = self.array_util_geometry((dx, dy))
        else:
            try:
                ss_el = translate(self.selection_shape, xoff=dx, yoff=dy)
            except ValueError:
                ss_el = None
            self.util_geo = DrawToolUtilityShape(ss_el)

        return self.util_geo

    def array_util_geometry(self, pos, static=None):
        array_type = self.copy_tool.ui.array_type_radio.get_value()      # 'linear', '2D', 'circular'

        if array_type == 'linear':  # 'Linear'
            return self.linear_geo(pos, static)
        elif array_type == '2D':
            return self.dd_geo(pos)
        elif array_type == 'circular':  # 'Circular'
            return self.circular_geo(pos)

    def linear_geo(self, pos, static):
        axis = self.copy_tool.ui.axis_radio.get_value()  # X, Y or A
        pitch = float(self.copy_tool.ui.pitch_entry.get_value())
        linear_angle = float(self.copy_tool.ui.linear_angle_spinner.get_value())
        array_size = int(self.copy_tool.ui.array_size_entry.get_value())

        if pos[0] is None and pos[1] is None:
            dx = self.draw_app.x
            dy = self.draw_app.y
        else:
            dx = pos[0]
            dy = pos[1]

        geo_list = []
        self.points = [(dx, dy)]
        geo_source = [shape.geo for shape in self.draw_app.get_selected()]
        x_adj = pitch * math.cos(math.radians(linear_angle))
        y_adj = pitch * math.sin(math.radians(linear_angle))

        for item in range(array_size):
            if axis == 'X':
                new_pos = ((dx + (pitch * item)), dy)
            elif axis == 'Y':
                new_pos = (dx, (dy + (pitch * item)))
            else:  # 'A'
                new_pos = ((dx + (x_adj * item)), (dy + (y_adj * item)))

            if static is None or static is False:
                geo_list.extend(translate_geometry(geo_source, new_pos[0], new_pos[1]))
            else:
                geo_list.extend(geo_source)

        return DrawToolUtilityShape(geo_list)

    def dd_geo(self, pos):
        trans_geo = []
        array_2d_type = self.copy_tool.ui.placement_radio.get_value()

        rows = self.copy_tool.ui.rows.get_value()
        columns = self.copy_tool.ui.columns.get_value()

        spacing_rows = self.copy_tool.ui.spacing_rows.get_value()
        spacing_columns = self.copy_tool.ui.spacing_columns.get_value()

        off_x = self.copy_tool.ui.offsetx_entry.get_value()
        off_y = self.copy_tool.ui.offsety_entry.get_value()

        geo_source = [s.geo for s in self.draw_app.get_selected()]

        xmin, ymin, xmax, ymax = get_shapely_list_bounds(geo_source)

        currentx = pos[0]
        currenty = pos[1]

        for row in range(rows):
            currentx = pos[0]

            for col in range(columns):
                trans_geo.extend(translate_geometry(geo_source, currentx, currenty))
                if array_2d_type == 's':  # 'spacing'
                    currentx += (xmax - xmin + spacing_columns)
                else:   # 'offset'
                    currentx = pos[0] + off_x * (col + 1)    # because 'col' starts from 0 we increment by 1

            if array_2d_type == 's':  # 'spacing'
                currenty += (ymax - ymin + spacing_rows)
            else:   # 'offset;
                currenty = pos[1] + off_y * (row + 1)    # because 'row' starts from 0 we increment by 1

        return DrawToolUtilityShape(trans_geo)

    def circular_geo(self, pos):
        if pos[0] is None and pos[1] is None:
            cdx = self.draw_app.x
            cdy = self.draw_app.y
        else:
            cdx = pos[0] + self.origin[0]
            cdy = pos[1] + self.origin[1]

        utility_list = []

        try:
            radius = distance((cdx, cdy), self.origin)
        except Exception:
            radius = 0

        if radius == 0:
            self.draw_app.delete_utility_geometry()

        if len(self.points) >= 1 and radius > 0:
            try:
                if cdx < self.origin[0]:
                    radius = -radius

                # draw the temp geometry
                initial_angle = math.asin((cdy - self.origin[1]) / radius)
                temp_circular_geo = self.circular_util_shape(radius, initial_angle)

                temp_points = [
                    (self.origin[0], self.origin[1]),
                    (self.origin[0] + pos[0], self.origin[1] + pos[1])
                ]
                temp_line = LineString(temp_points)

                for geo_shape in temp_circular_geo:
                    utility_list.append(geo_shape.geo)
                utility_list.append(temp_line)

                return DrawToolUtilityShape(utility_list)
            except Exception as e:
                log.error("DrillArray.utility_geometry -- circular -> %s" % str(e))

    def circular_util_shape(self, radius, ini_angle):
        direction = self.copy_tool.ui.array_dir_radio.get_value()      # CW or CCW
        angle = self.copy_tool.ui.angle_entry.get_value()
        array_size = int(self.copy_tool.ui.array_size_entry.get_value())

        circular_geo = []
        geo_source = [shape.geo for shape in self.draw_app.get_selected()]
        for i in range(array_size):
            angle_radians = math.radians(angle * i)
            if direction == 'CW':
                x = radius * math.cos(-angle_radians + ini_angle)
                y = radius * math.sin(-angle_radians + ini_angle)
            else:
                x = radius * math.cos(angle_radians + ini_angle)
                y = radius * math.sin(angle_radians + ini_angle)

            rotation = -angle * i if direction == 'CW' else angle * i
            rotated = affine_geometry_many(geo_source, affine_rotate_matrix(rotation, self.origin))
            for geo_sol in translate_geometry(rotated, x, y):
                circular_geo.append(DrawToolShape(geo_sol))

        return circular_geo

    def select_shapes(self, pos):
        # list where we store the overlapped shapes under our mouse left click position
        over_shape_list = []

        try:
            _, closest_shape = self.storage.nearest(pos)
        except StopIteration:
            return ""

        over_shape_list.append(closest_shape)

        try:
            # if there is no shape under our click then deselect all shapes
            # it will not work for 3rd method of click selection
            if not over_shape_list:
                self.draw_app.selected = []
                self.draw_app.draw_shape_idx = -1
            else:
                # if there are shapes under our click then advance through the list of them, one at the time in a
                # circular way
                self.draw_app.draw_shape_idx = (AppGeoEditor.draw_shape_idx + 1) % len(over_shape_list)
                try:
                    obj_to_add = over_shape_list[int(AppGeoEditor.draw_shape_idx)]
                except IndexError:
                    return

                key_modifier = QtWidgets.QApplication.keyboardModifiers()
                if self.draw_app.app.options.get(
                        "global_mselect_key",
                        self.draw_app.app.defaults.get(
                            "global_mselect_key",
                            self.draw_app.app.defaults.factory_defaults.get("global_mselect_key")
                        )) == 'Control':
                    # if CONTROL key is pressed then we add to the selected list the current shape but if it's
                    # already in the selected list, we removed it. Therefore, first click selects, second deselects.
                    if key_modifier == Qt.KeyboardModifier.ControlModifier:
                        if obj_to_add in self.draw_app.selected:
                            self.draw_app.selected.remove(obj_to_add)
                        else:
                            self.draw_app.selected.append(obj_to_add)
                    else:
                        self.draw_app.selected = []
                        self.draw_app.selected.append(obj_to_add)
                else:
                    if key_modifier == Qt.KeyboardModifier.ShiftModifier:
                        if obj_to_add in self.draw_app.selected:
                            self.draw_app.selected.remove(obj_to_add)
                        else:
                            self.draw_app.selected.append(obj_to_add)
                    else:
                        self.draw_app.selected = []
                        self.draw_app.selected.append(obj_to_add)

        except Exception as e:
            log.error("[ERROR] Something went bad. %s" % str(e))
            raise

    def draw_cursor_data(self, pos=None, delete=False):
        if self.cursor_data_control is False:
            self.draw_app.app.plotcanvas.text_cursor.text = ""
            return

        if pos is None:
            pos = self.draw_app.snap_x, self.draw_app.snap_y

        if delete:
            hide_cursor_text(self.draw_app.app)
            return

        show_cursor_text(self.draw_app, pos, ref=self.points[-1] if self.points else None)

    def on_key(self, key):
        if key == 'C' or key == QtCore.Qt.Key.Key_C:
            self.cursor_data_control = not self.cursor_data_control

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.draw_app.app.on_jump_to()

        if key in [str(i) for i in range(10)] + ['.', ',', '+', '-', '/', '*'] or \
                key in [QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_0, QtCore.Qt.Key.Key_1, QtCore.Qt.Key.Key_2,
                        QtCore.Qt.Key.Key_3, QtCore.Qt.Key.Key_4, QtCore.Qt.Key.Key_5, QtCore.Qt.Key.Key_6,
                        QtCore.Qt.Key.Key_7, QtCore.Qt.Key.Key_8, QtCore.Qt.Key.Key_9, QtCore.Qt.Key.Key_Minus,
                        QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Comma, QtCore.Qt.Key.Key_Period,
                        QtCore.Qt.Key.Key_Slash, QtCore.Qt.Key.Key_Asterisk]:
            try:
                # VisPy keys
                if self.new_segment is True:
                    self.copy_tool.length = str(key.name)
                    self.new_segment = False
                else:
                    self.copy_tool.length = self.copy_tool.ui.project_line_entry.text() + str(key.name)
            except AttributeError:
                # Qt keys
                if self.new_segment is True:
                    self.copy_tool.length = key if isinstance(key, str) else chr(key)
                    self.new_segment = False
                else:
                    self.copy_tool.length = self.copy_tool.ui.project_line_entry.text() + \
                        (key if isinstance(key, str) else chr(key))

        if key == 'Enter' or key == QtCore.Qt.Key.Key_Return or key == QtCore.Qt.Key.Key_Enter:
            if AppGeoEditor.reject_busy(self.draw_app):
                return 'fail'
            if self.origin is None:
                return _("Click on a reference point before entering a length.")
            try:
                valid = math.isfinite(self.copy_tool.length) and self.copy_tool.length != 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                return _("Enter a finite nonzero length.")
            if self.copy_tool.length != 0:
                target_length = self.copy_tool.length
                if target_length is None:
                    self.copy_tool.length = 0.0
                    return _("Failed.")

                first_pt = self.origin
                last_pt = self.draw_app.app.mouse_pos

                seg_length = math.hypot(last_pt[0] - first_pt[0], last_pt[1] - first_pt[1])
                if not math.isfinite(seg_length) or seg_length == 0.0:
                    return _("Move the cursor away from the reference point to set a finite direction.")
                try:
                    new_x = first_pt[0] + (last_pt[0] - first_pt[0]) / seg_length * target_length
                    new_y = first_pt[1] + (last_pt[1] - first_pt[1]) / seg_length * target_length
                except ZeroDivisionError as err:
                    self.points = []
                    self.clean_up()
                    return '[ERROR_NOTCL] %s %s' % (_("Failed."), str(err).capitalize())

                if not all(math.isfinite(v) for v in (new_x, new_y)):
                    return _("Enter a smaller length so the projected coordinates remain finite.")
                if first_pt != (new_x, new_y):
                    self.points.append((new_x, new_y))
                    self.draw_app.app.on_jump_to(custom_location=(new_x, new_y), fit_center=False)
                    self.destination = (new_x, new_y)
                    if self.make() == 'fail':
                        return 'fail'
                    if self.draw_app.on_shape_complete() == 'fail':
                        return 'fail'
                    self.draw_app.select_tool("select")
                    return "Done."

    def clean_up(self):
        self.draw_app.app.plotcanvas.text_cursor.parent = None
        self.draw_app.app.plotcanvas.view.camera.zoom_callback = lambda *args: None

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.copy_tool.disconnect_signals()
        self.copy_tool.ui.copy_frame.hide()


class FCText(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'text'
        self.draw_app = draw_app

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.cursor = QtGui.QCursor(QtGui.QPixmap(self.draw_app.app.resource_location + '/aero_text.png'))
        QtGui.QGuiApplication.setOverrideCursor(self.cursor)

        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Click on 1st point ..."))
        self.origin = (0, 0)

        self.text_gui = TextInputTool(app=self.app, draw_app=self.draw_app)
        self.text_gui.run()
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        # Create new geometry
        dx = point[0]
        dy = point[1]

        if self.text_gui.text_path:
            try:
                self.geometry = DrawToolShape(translate(self.text_gui.text_path, xoff=dx, yoff=dy))
            except Exception as e:
                log.error("Font geometry is empty or incorrect: %s" % str(e))
                self.draw_app.app.inform.emit('[ERROR] %s: %s' %
                                              (_("Font not supported. Only Regular, Bold, Italic and BoldItalic are "
                                                 "supported. Error"), str(e)))
                self.text_gui.text_path = []
                # self.text_gui.hide_tool()
                self.draw_app.select_tool('select')
                self.draw_app.app.jump_signal.disconnect()
                return
        else:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' % _("No text to add."))
            try:
                self.draw_app.app.jump_signal.disconnect()
            except (TypeError, AttributeError):
                pass
            return

        self.text_gui.text_path = []
        self.text_gui.hide_tool()
        self.complete = True
        self.draw_app.app.inform.emit('[success]%s' % _("Done."))

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data: mouse position coords
        :return:
        """

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        try:
            return DrawToolUtilityShape(translate(self.text_gui.text_path, xoff=dx, yoff=dy))
        except Exception:
            return

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class FCBuffer(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'buffer'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Create buffer geometry ..."))
        self.origin = (0, 0)
        self.buff_tool = BufferSelectionTool(self.app, self.draw_app)
        self.buff_tool.run()
        self.app.ui.notebook.setTabText(2, _("Buffer"))
        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])
        self.activate()

    def on_buffer(self):
        if not self.draw_app.selected:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No shape selected.")))
            return

        try:
            buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value().replace(',', '.'))
                self.buff_tool.ui.buffer_distance_entry.set_value(buffer_distance)
            except ValueError:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Buffer distance value is missing or wrong format. Add it and retry."))
                return
        # the cb index start from 0 but the join styles for the buffer start from 1 therefore the adjustment
        # I populated the combobox such that the index coincide with the join styles value
        join_style = {1: 'round', 2: 'mitre', 3: 'bevel'}.get(self.buff_tool.ui.buffer_corner_cb.currentIndex() + 1)
        ret_val = self.buff_tool.buffer(buffer_distance, join_style)

        self.deactivate()
        if ret_val == 'fail':
            return
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def on_buffer_int(self):
        if not self.draw_app.selected:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No shape selected.")))
            return

        try:
            buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value().replace(',', '.'))
                self.buff_tool.ui.buffer_distance_entry.set_value(buffer_distance)
            except ValueError:
                self.app.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Buffer distance value is missing or wrong format. Add it and retry."))
                return
        # the cb index start from 0 but the join styles for the buffer start from 1 therefore the adjustment
        # I populated the combobox such that the index coincide with the join styles value
        join_style = {1: 'round', 2: 'mitre', 3: 'bevel'}.get(self.buff_tool.ui.buffer_corner_cb.currentIndex() + 1)
        ret_val = self.buff_tool.buffer_int(buffer_distance, join_style)

        self.deactivate()
        if ret_val == 'fail':
            return
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def on_buffer_ext(self):
        if not self.draw_app.selected:
            self.draw_app.app.inform.emit('[WARNING_NOTCL] %s %s' % (_("Cancelled."), _("No shape selected.")))
            return

        try:
            buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value())
        except ValueError:
            # try to convert comma to decimal point. if it's still not working error message and return
            try:
                buffer_distance = float(self.buff_tool.ui.buffer_distance_entry.get_value().replace(',', '.'))
                self.buff_tool.ui.buffer_distance_entry.set_value(buffer_distance)
            except ValueError:
                self.draw_app.app.inform.emit('[WARNING_NOTCL] %s' %
                                              _("Buffer distance value is missing or wrong format. Add it and retry."))
                return
        # the cb index start from 0 but the join styles for the buffer start from 1 therefore the adjustment
        # I populated the combobox such that the index coincide with the join styles value
        join_style = {1: 'round', 2: 'mitre', 3: 'bevel'}.get(self.buff_tool.ui.buffer_corner_cb.currentIndex() + 1)
        ret_val = self.buff_tool.buffer_ext(buffer_distance, join_style)
        # self.app.ui.notebook.setTabText(2, _("Tools"))
        # self.draw_app.app.ui.splitter.setSizes([0, 1])

        self.deactivate()
        if ret_val == 'fail':
            return
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))

    def activate(self):
        self.buff_tool.ui.buffer_button.clicked.disconnect()
        self.buff_tool.ui.buffer_int_button.clicked.disconnect()
        self.buff_tool.ui.buffer_ext_button.clicked.disconnect()

        self.buff_tool.ui.buffer_button.clicked.connect(self.on_buffer)
        self.buff_tool.ui.buffer_int_button.clicked.connect(self.on_buffer_int)
        self.buff_tool.ui.buffer_ext_button.clicked.connect(self.on_buffer_ext)

    def deactivate(self):
        self.buff_tool.ui.buffer_button.clicked.disconnect()
        self.buff_tool.ui.buffer_int_button.clicked.disconnect()
        self.buff_tool.ui.buffer_ext_button.clicked.disconnect()

        self.buff_tool.ui.buffer_button.clicked.connect(self.buff_tool.on_buffer)
        self.buff_tool.ui.buffer_int_button.clicked.connect(self.buff_tool.on_buffer_int)
        self.buff_tool.ui.buffer_ext_button.clicked.connect(self.buff_tool.on_buffer_ext)
        self.complete = True
        self.draw_app.select_tool("select")

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()


class FCSimplification(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'simplify'

        self.draw_app = draw_app
        self.app = draw_app.app
        self.storage = self.draw_app.storage

        self.draw_app.app.inform.emit(_("Simplify geometry ..."))
        self.origin = (0, 0)
        self.simp_tool = SimplificationTool(self.app, self.draw_app)
        self.simp_tool.run()

        if self.draw_app.app.ui.splitter.sizes()[0] == 0:
            self.draw_app.app.ui.splitter.setSizes([1, 1])

    def click(self, point):
        # the same selection as the Select tool: a click selects, deselects (with the key) or clears the selection
        if self.draw_app.click_select(point):
            self.draw_app.plot_changes()
            self.selection_changed()

    def selection_changed(self):
        self.simp_tool.calculate_coordinates_vertex([shape.geo for shape in self.draw_app.selected])

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.sync_tree_selection()
        self.draw_app.plot_changes()


class FCImport(DrawTool):
    """
    Copies shapes of the other visible project objects (Geometry, Gerber, Excellon) into the edited Geometry.

    A click picks or unpicks the shapes under it, a drag picks the shapes in the area (left to right: enclosed,
    right to left: touched). The picked shapes are highlighted. Right click or Enter copies them into the editor
    as one Undo step; Escape cancels.
    """

    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'import'
        self.draw_app = draw_app
        self.app = draw_app.app

        # the picked shapes: dicts with the source geometry ('geo'), its WKB ('wkb') and the highlight key
        self.picked = []

        try:
            QtGui.QGuiApplication.restoreOverrideCursor()
        except Exception:
            pass

        self.app.inform.emit(_("Select shapes to import them into the edited object."))

    def source_geometries(self):
        geos = []
        for obj in import_source_objects(self.app.collection.get_list(), self.draw_app.fcgeometry):
            geos += import_source_geometries(obj, self.app.app_units)
        return geos

    def pick_tolerance(self):
        try:
            return PICK_TOLERANCE_PX * self.draw_app.canvas.pixel_size()
        except Exception:
            return 0.0

    def click_release(self, point):
        """
        Picks, or unpicks if already picked, the shapes under the click.

        :param point:   (x, y) of the click
        """
        self.toggle(geometries_at_point(self.source_geometries(), point, self.pick_tolerance()))
        return ""

    def area_select(self, start, end, enclosing):
        self.toggle(geometries_in_area(self.source_geometries(), start, end, enclosing))

    def toggle(self, geometries):
        if not geometries:
            self.app.inform.emit(_("No shape in selection."))
            return
        for geo in geometries:
            wkb = geo.wkb
            existing = [item for item in self.picked if item['wkb'] == wkb]
            if existing:
                self.picked.remove(existing[0])
            else:
                self.picked.append({'geo': geo, 'wkb': wkb})
        self.highlight()
        self.app.inform.emit('%s: %d. %s' % (_("Picked shapes"), len(self.picked),
                                             _("Click to pick more, right click or Enter to import.")))

    def highlight(self):
        color = self.draw_app.get_sel_color()[:7] + 'AF'
        self.draw_app.tool_shape.clear(update=False)
        for item in self.picked:
            self.draw_app.tool_shape.add(shape=item['geo'], color=color, face_color=color, update=False, layer=0,
                                         tolerance=None)
        self.draw_app.tool_shape.redraw()

    def commit(self):
        """
        Copies the picked shapes into the edited object: one Undo step.

        :return:    'fail' if the editor is busy, else None
        """
        if not self.picked:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No shape picked."))
            return
        shapes = [DrawToolShape(deepcopy(item['geo'])) for item in self.picked]
        if self.draw_app.add_shape(shapes) == 'fail':
            return 'fail'
        self.picked = []
        self.draw_app.plot_changes()

    def on_key(self, key):
        if key in (QtCore.Qt.Key.Key_Enter, QtCore.Qt.Key.Key_Return, 'Enter', 'Return'):
            if self.commit() != 'fail':
                self.app.inform.emit('[success] %s' % _("Done."))
                self.draw_app.select_tool('select')
            return
        return DrawTool.on_key(self, key)

    def clean_up(self):
        self.picked = []
        self.draw_app.tool_shape.clear(update=True)


class FCEraser(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'eraser'
        self.draw_app = draw_app

        self.origin = None
        self.destination = None

        if len(self.draw_app.get_selected()) == 0:
            if self.draw_app.launched_from_shortcuts is True:
                self.draw_app.launched_from_shortcuts = False
            self.draw_app.app.inform.emit(_("Select a shape to act as deletion area ..."))
        else:
            self.draw_app.app.inform.emit(_("Click to pick-up the erase shape..."))

        self.geometry = []
        self.storage = self.draw_app.storage

        # Switch notebook to Properties page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.properties_tab)
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

    def set_origin(self, origin):
        self.origin = origin

    def click(self, point):
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass
        self.draw_app.app.jump_signal.connect(lambda x: self.draw_app.update_utility_geometry(data=x))

        if len(self.draw_app.get_selected()) == 0:
            for ____ in self.storage.get_objects():
                try:
                    __, closest_shape = self.storage.nearest(point)
                    self.draw_app.selected.append(closest_shape)
                except StopIteration:
                    if len(self.draw_app.selected) > 0:
                        self.draw_app.app.inform.emit(_("Click to pick-up the erase shape..."))
                    return ""

        if len(self.draw_app.get_selected()) == 0:
            return _("Nothing to erase.")
        else:
            self.draw_app.app.inform.emit(_("Click to pick-up the erase shape..."))

        if self.origin is None:
            origin = self.draw_app.snap(*point) if getattr(self.draw_app.app, 'grid_status', lambda: False)() else point
            self.set_origin(origin)
            self.draw_app.app.inform.emit(_("Click to erase ..."))
            return
        else:
            self.destination = point
            self.make()

            # self.draw_app.select_tool("select")
            return

    def make(self):
        eraser_sel_shapes = []

        # create the eraser shape from selection
        for eraser_shape in self.utility_geometry(data=self.destination).geo:
            for temp_shape in flatten_shapely_geometry(eraser_shape.buffer(0.0000001)):
                eraser_sel_shapes.append(Polygon(temp_shape.exterior))
        eraser_sel_shapes = unary_union(eraser_sel_shapes)

        tree, refs = self.draw_app.get_pick_index()
        removed = [refs[idx] for idx in sorted(tree.query(eraser_sel_shapes, predicate='intersects'))] \
            if tree is not None else []
        if removed:
            selected_ids = {id(shape) for shape in self.draw_app.get_selected()}
            inserted = []
            selected = []
            geometries = shapely.difference([shape.geo for shape in removed], eraser_sel_shapes)
            for shape, geometry in zip(removed, geometries):
                result = DrawToolShape(geometry)
                result.data = shape.data.copy()
                result.data['bounds'] = result.bounds()
                self.draw_app._collect_shape(result, inserted)
                if id(shape) in selected_ids and inserted and inserted[-1] is result:
                    selected.append(result)
            with AppGeoEditor.history_operation(self.draw_app) as accepted:
                if not accepted:
                    return 'fail'
                self.draw_app.delete_shape(removed, inserted=inserted)
                self.draw_app.selected.extend(selected)
            self.draw_app.build_ui_sig.emit()

        self.draw_app.delete_utility_geometry()
        self.draw_app.plot_changes()
        self.draw_app.app.inform.emit('[success] %s' % _("Done."))
        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        geo_list = []

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]

        try:
            for geom in self.draw_app.get_selected():
                geo_list.append(translate(geom.geo, xoff=dx, yoff=dy))
        except AttributeError:
            self.draw_app.select_tool('select')
            self.draw_app.selected = []
            return
        return DrawToolUtilityShape(geo_list)

    def clean_up(self):
        self.draw_app.selected = []
        self.draw_app.plot_changes()

        try:
            self.draw_app.app.jump_signal.disconnect()
        except (TypeError, AttributeError):
            pass


class FCPaint(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'paint'
        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Create Paint geometry ..."))
        self.origin = (0, 0)
        self.draw_app.paint_tool.run()

    def clean_up(self):
        pass


class FCTransform(FCShapeTool):
    def __init__(self, draw_app):
        FCShapeTool.__init__(self, draw_app)
        self.name = 'transformation'

        self.draw_app = draw_app
        self.app = draw_app.app

        self.draw_app.app.inform.emit(_("Shape transformations ..."))
        self.origin = (0, 0)
        self.draw_app.transform_tool.run()

    def clean_up(self):
        pass


def mag(vec):
    return np.sqrt(vec[0] ** 2 + vec[1] ** 2)


def poly2rings(poly):
    return [poly.exterior] + [interior for interior in poly.interiors]


def get_shapely_list_bounds(geometry_list):
    xmin = np.inf
    ymin = np.inf
    xmax = -np.inf
    ymax = -np.inf

    for gs in geometry_list:
        try:
            gxmin, gymin, gxmax, gymax = gs.bounds
            xmin = min([xmin, gxmin])
            ymin = min([ymin, gymin])
            xmax = max([xmax, gxmax])
            ymax = max([ymax, gymax])
        except Exception as e:
            log.error("Tried to get bounds of empty geometry. --> %s" % str(e))

    return [xmin, ymin, xmax, ymax]
