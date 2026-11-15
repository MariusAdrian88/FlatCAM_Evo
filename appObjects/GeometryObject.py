# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File modified by: Marius Stanciu                         #
# ##########################################################

from PyQt6 import QtWidgets, QtCore
from appObjects.AppObjectTemplate import FlatCAMObj, OffsetUiMixin, PropertiesPanelMixin, ObjectDeleted
from appGUI.GUIElements import FCCheckBox, checkbox_column_width, opposite_direction
from appGUI.ObjectUI import GeometryObjectUI

from shapely import MultiLineString, LinearRing, Polygon, MultiPolygon, LineString, Point, MultiPoint, GeometryCollection
from shapely.affinity import scale, translate
from shapely.ops import unary_union

from camlib import (voronoi_path_signature, Geometry, flatten_shapely_geometry, translate_geometry, affine_geometry, affine_geometry_many,
                    affine_scale_matrix, affine_translate_matrix, merge_zones, deepcopy_keep_geometry, first_tool_dia, list_total_bounds,
                    buffer_geometry_many, scale_geometries_by_center)
from appCommon.SafeEval import safe_eval
from defaults import AppDefaults

import shapely

import re
import ezdxf
import numpy as np
import traceback
from copy import deepcopy
from collections import defaultdict
from functools import reduce

from appHandlers.appHistory import history_of
import gettext
from appParsers.DXF.writer import build_dxf
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


_SINGLE_TYPES = frozenset((Point, LineString, LinearRing, Polygon))
_SHAPE_TYPES = _SINGLE_TYPES | {MultiPoint, MultiLineString, MultiPolygon, GeometryCollection}


def _collect_leaves(item, leaves):
    """
    Append the shape leaves of ``item`` (lists and tuples walked recursively, a multi-part shape is 1 leaf) to
    ``leaves``. Return False for anything else (a generator, a set, ...): the caller must not cache then.
    """
    kind = type(item)
    if kind is list or kind is tuple:
        for child in item:
            if type(child) in _SHAPE_TYPES:
                leaves.append(child)
            elif not _collect_leaves(child, leaves):
                return False
        return True
    if kind in _SHAPE_TYPES:
        leaves.append(item)
        return True
    return False


class GeometryObject(OffsetUiMixin, PropertiesPanelMixin, FlatCAMObj, Geometry):
    """
    Geometric object not associated with a specific
    format.
    """
    # Dispatch metadata, read by AppObject, ObjectCollection and appPlotManager instead of kind chains.
    # status_color: color of the object name in the status bar message (None: no message).
    # top of the 3 canvas layers: Geometry is drawn above the filled Gerber (layer 1)
    PLOT_LAYER = 2

    status_color = 'red'
    source_exporter = 'export_dxf'
    # tool_option_prefix: extra application options (with this prefix) copied into obj_options on creation.
    tool_option_prefix = None
    # inherits_mill_options: the tools_mill_* and tools_* options are copied into obj_options on creation.
    inherits_mill_options = True
    # has_plot_colors: the object has user settable fill / outline plot colors.
    has_plot_colors = False
    # plot_default_fill_option / plot_default_line_option: option keys of the colors restored by "Default".
    plot_default_fill_option = 'geometry_plot_line'
    plot_default_line_option = 'geometry_plot_line'
    # alpha_fill_option: option key whose color alpha is the default alpha level.
    # default_alpha_level: fixed default alpha level when alpha_fill_option is None.
    alpha_fill_option = None
    default_alpha_level = 'FF'
    build_ui_sig = QtCore.pyqtSignal()
    launch_job = QtCore.pyqtSignal()

    ui_type = GeometryObjectUI

    @property
    def solid_geometry(self):
        """
        All tool shapes, flattened, without fusing them. A shape that more than 1 tool mills (an old single-geo
        object gave its shapes to every tool) is in the list 1 time.
        """
        # No invalidation calls: the writers of ``tools`` are too many. The cache key is a fingerprint taken at each
        # access: per tool, its id and the id() of every shape leaf. The cache holds the leaves (strong references),
        # so a freed shape can not give its id to a new one while the entry lives. Key, leaves and result are 1
        # tuple in 1 assignment: a thread never pairs a key with a wrong result.
        snapshot = []
        leaves = []
        key = []
        for uid, tool in list(self.tools.items()):
            shapes = tool.get('solid_geometry')
            if shapes is None:
                continue
            start = len(leaves)
            if not _collect_leaves(shapes, leaves):
                # a kind of container that is not fingerprinted: no cache
                return self._build_solid_geometry(self.tools.values())
            snapshot.append(leaves[start:])
            key.append((uid, tuple(map(id, snapshot[-1]))))
        key = tuple(key)
        cached = self.__dict__.get('_solid_geometry_cache')
        if cached is not None and cached[0] == key:
            return list(cached[2])
        flat = []
        for tool_leaves in snapshot:
            for leaf in tool_leaves:
                if type(leaf) in _SINGLE_TYPES:
                    if not leaf.is_empty:
                        flat.append(leaf)
                else:
                    flat.extend(flatten_shapely_geometry(leaf))
        result = list(dict.fromkeys(flat))
        self._solid_geometry_cache = (key, snapshot, result)
        return list(result)

    @staticmethod
    def _build_solid_geometry(tools):
        return list(dict.fromkeys(
            g for tool in tools if tool.get('solid_geometry') is not None
            for g in flatten_shapely_geometry(tool['solid_geometry'])))

    def _first_tool(self):
        """The id of the first tool; an object without tools gets the default tool."""
        if not self.tools:
            self.tools[1] = self.default_tool()
        return next(iter(self.tools))

    def tool_shapes(self, tool_id):
        """The flat list of the shapes of 1 tool."""
        shapes = self.tools[tool_id].get('solid_geometry')
        return [] if shapes is None else flatten_shapely_geometry(shapes)

    def shared_tools(self, tool_id):
        """
        The ids of the tools that hold the same shapes as the tool ``tool_id``, that tool included. Tools share
        their shapes in an object that had 1 geometry for all its tools (an old single-geo project, Gerber to
        Geometry with more than 1 diameter): there a change of the shapes is a change for all these tools.
        """
        reference = self.tool_shapes(tool_id)
        same = []
        for uid in self.tools:
            shapes = reference if uid == tool_id else self.tool_shapes(uid)
            if len(shapes) == len(reference) and all(a is b or a == b for a, b in zip(shapes, reference)):
                same.append(uid)
        return same

    def _store_shapes(self, shapes):
        self.tools[self._first_tool()]['solid_geometry'] = shapes

    def _add_shape(self, shape, tool=None):
        if tool is None or tool not in self.tools:
            tool = self._first_tool()
        record = self.tools[tool]
        shapes = record.get('solid_geometry')
        if not isinstance(shapes, list):
            shapes = [] if shapes is None else [shapes]
        shapes.append(shape)
        record['solid_geometry'] = shapes

    def _per_tool(self, fn):
        for tool in self.tools.values():
            tool['solid_geometry'] = fn(tool.get('solid_geometry', []))

    def bounds(self, flatten=False):
        shapes = [g for g in self.solid_geometry if g is not None and not g.is_empty]
        if not shapes:
            return 0, 0, 0, 0
        return tuple(float(v) for v in shapely.total_bounds(shapes))

    def _affine_solid_geometry(self, matrix, any_iterable=False):
        keys = list(self.tools)
        results = affine_geometry_many([self.tools[k].get('solid_geometry', []) for k in keys],
                                       matrix, any_iterable)
        for key, result in zip(keys, results):
            self.tools[key]['solid_geometry'] = result
        self._affine_zones(matrix)

    def union(self):
        self._per_tool(lambda shapes: [unary_union(self.flatten(shapes))])
        self.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), self.bounds()))

    def subtract_polygon(self, points):
        polygon = points if isinstance(points, Polygon) else Polygon(points)
        if not polygon.is_valid or polygon.is_empty:
            return 'fail'
        updated = {uid: [unary_union([path.difference(polygon)
                                     for path in self.flatten(tool.get('solid_geometry', []), pathonly=True)
                                     if isinstance(path, (LineString, LinearRing, MultiLineString))])]
                   for uid, tool in self.tools.items()}
        for uid, shapes in updated.items():
            self.tools[uid]['solid_geometry'] = shapes
        self.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), self.bounds()))

    def _buffer_geometry(self, distance, quad_segs, join, factor, only_exterior):
        keys = list(self.tools)
        shapes = [self.tools[k].get('solid_geometry', []) for k in keys]
        results = ([scale_geometries_by_center(s, distance) for s in shapes] if factor else
                   buffer_geometry_many(shapes, distance, quad_segs, join, only_exterior=only_exterior))
        return {key: flatten_shapely_geometry(result) for key, result in zip(keys, results)}

    def _store_buffer(self, result):
        for key, shapes in result.items():
            self.tools[key]['solid_geometry'] = shapes

    def __init__(self, name, app):
        self.app = app
        self.decimals = self.app.decimals

        # no fixed steps: the circles follow the preferences "Circles" of Geometry (Geometry.circle_quad_segs_for())
        Geometry.__init__(self, app=app)
        FlatCAMObj.__init__(self, name, app=app)

        self.kind = "geometry"

        self.obj_options.update({
            "plot": True,
            "multicolored": False,

            "tools_mill_cutz": -0.002,
            "tools_mill_vtipdia": 0.1,
            "tools_mill_vtipangle": 30,
            "tools_mill_travelz": 0.1,
            "tools_mill_feedrate": 5.0,
            "tools_mill_feedrate_z": 5.0,
            "tools_mill_feedrate_rapid": 5.0,
            "tools_mill_spindlespeed": 0,
            "tools_mill_dwell": True,
            "tools_mill_dwelltime": 1000,
            "tools_mill_multidepth": False,
            "tools_mill_depthperpass": 0.002,
            "tools_mill_extracut": False,
            "tools_mill_extracut_length": 0.1,
            "tools_mill_endz": 2.0,
            "tools_mill_endxy": '',
            "tools_mill_area_exclusion": False,
            "tools_mill_area_shape": "polygon",
            "tools_mill_area_strategy": "over",
            "tools_mill_area_overz": 1.0,

            "tools_mill_startz": None,
            "tools_mill_toolchange": False,
            "tools_mill_toolchangez": 1.0,
            "tools_mill_toolchangexy": "0.0, 0.0",
            "tools_mill_ppname_g": 'default',
            "tools_mill_z_p_depth": -0.02,
            "tools_mill_probe_plate": 0.0,
            "tools_mill_feedrate_probe": 3.0,
        })

        if "tools_mill_tooldia" not in self.obj_options:
            if isinstance(self._app_option("tools_mill_tooldia"), float):
                self.obj_options["tools_mill_tooldia"] = self._app_option("tools_mill_tooldia")
            else:
                try:
                    tools_string = self._app_option("tools_mill_tooldia").split(",")
                    tools_diameters = [safe_eval(a) for a in tools_string if a != '']
                    self.obj_options["tools_mill_tooldia"] = tools_diameters[0] if tools_diameters else 0.0
                except Exception as e:
                    self.app.log.error("FlatCAMObj.GeometryObject.init() --> %s" % str(e))

        self.obj_options["tools_mill_startz"] = self._app_option("tools_mill_startz")

        # this will hold the tool unique ID that is useful when having multiple tools with same diameter
        self.tooluid = 0

        '''
            self.tools = {}
            This is a dictionary. Each dict key is associated with a tool used in geo_tools_table. The key is the 
            tool_id of the tools and the value is another dict that will hold the data under the following form:
                {tooluid:   {
                            'tooldia': 1,
                            'data': self.default_tool_data,
                            'solid_geometry': []
                            }
                }
        '''
        self.tools = {1: self.default_tool()}

        # this dict is to store those elements (tools) of self.tools that are selected in the self.geo_tools_table
        # those elements are the ones used for generating GCode
        self.sel_tools = {}

        self.offset_item_options = ["Path", "In", "Out", "Custom"]
        self.job_item_options = [_('Roughing'), _('Finishing'), _('Isolation'), _('Polishing')]
        self.tool_type_item_options = ["C1", "C2", "C3", "C4", "B", "V", "L"]

        # flag to store if the V-Shape tool is selected in self.ui.geo_tools_table
        self.v_tool_type = None

        # flag to store if the geometry is part of a special group of geometries that can't be processed by the default
        # engine of FlatCAM. Most likely are generated by some tools and are special cases of geometries.
        self.special_group = None

        # self.old_pp_state = self.app.options["tools_mill_multidepth"]
        # self.old_toolchangeg_state = self.app.options["tools_mill_toolchange"]
        self.units_found = self.app.app_units

        # this variable can be updated by the Object that generates the geometry
        self.tool_type = 'C1'

        # save here the old value for the Cut Z before it is changed by selecting a V-shape type tool in the tool table
        self.old_cutz = self._app_option("tools_mill_cutz")

        self.fill_color = self._app_option('geometry_plot_line')
        self.outline_color = self._app_option('geometry_plot_line')
        self.alpha_level = 'FF'

        self.param_fields = {}

        # store here the state of the exclusion checkbox state to be restored after building the UI
        self.exclusion_area_cb_is_checked = self._app_option("tools_mill_area_exclusion")

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from predecessors.
        self.source_file = ''
        self.ser_attrs += ['obj_options', 'kind', 'fill_color', 'outline_color', 'alpha_level', 'source_file',
                           'exclusion_zones', 'zones_always_drawn']
        self.ser_attrs.remove('solid_geometry')

    def to_dict(self):
        d = super().to_dict()
        # constant for the previous release: it reads the shapes of the tools only when it finds this key True
        d['multigeo'] = True
        return d

    def from_dict(self, d):
        # Older geometry project objects did not persist the interchange snapshot.
        d = dict(d, source_file=d.get('source_file', ''))
        single_geo = d.get('multigeo') is False
        if single_geo or (
                d.get('multigeo') is None and d.get('solid_geometry') and
                not any(tool.get('solid_geometry') for tool in (d.get('tools') or {}).values())):
            shared = d.get('solid_geometry') or []
            shared = list(shared) if isinstance(shared, (list, tuple)) else [shared]
            tools = {tid: dict(tool) for tid, tool in (d.get('tools') or {}).items()}
            if not tools:
                tools = {1: self.default_tool()}
            # Keep legacy setting names: Milling resolves them without overwriting saved values with defaults.
            # An old single-geo object milled its shared shapes with every tool; the copies in the tools could be
            # older than the shared shapes (scale, offset and the editor changed only the shared shapes).
            for tool in tools.values():
                if (single_geo and shared) or not tool.get('solid_geometry'):
                    tool['solid_geometry'] = list(shared)
            d['tools'] = tools
        tools = d.get('tools') or {}
        if not tools:
            # an old object without tools: its shapes (if it has some) go into a default tool
            shapes = d.get('solid_geometry')
            tool = self.default_tool()
            tool['solid_geometry'] = [] if shapes is None else (
                list(shapes) if isinstance(shapes, (list, tuple)) else [shapes])
            d['tools'] = {1: tool}
        elif any(tool.get('solid_geometry') is None for tool in tools.values()):
            d['tools'] = {tid: dict(tool, solid_geometry=[]) if tool.get('solid_geometry') is None else tool
                          for tid, tool in tools.items()}
        super().from_dict(d)

    def default_tool(self):
        """The Milling defaults for a new Geometry tool, without shapes."""
        data = {}
        for key in self.app.options:
            if key.startswith('geometry_'):
                data[key[len('geometry_'):]] = deepcopy(self._app_option(key))
            elif key.startswith('tools_'):
                data[key] = deepcopy(self._app_option(key))
        try:
            tooldia = first_tool_dia(self._app_option('tools_mill_tooldia'))
        except Exception:
            # the preference is free text: an empty or bad text gives the factory diameter
            tooldia = first_tool_dia(AppDefaults.factory_defaults['tools_mill_tooldia'])
        return {'tooldia': tooldia, 'offset': 'Path',
                'offset_value': 0.0, 'type': 'Rough', 'tool_type': 'C1', 'data': data, 'solid_geometry': []}

    def copy_from(self, src):
        """
        Make this (new, empty) Geometry a copy of ``src``. Used by both copy paths of AppObjectOps. Everything is
        deep-copied, nothing mutable is shared with ``src``: the geometry fields go through
        deepcopy_keep_geometry() (new containers, the immutable Shapely geometries are shared).

        Copied: follow_geometry, tools (with the per-tool data and shapes),
        tooluid (the counter of the tool ids: a copy that restarts at 0 hands out ids that already exist in
        its tools), units and source_file (only if the source has one, an imported SVG / DXF has).

        Not copied, on purpose: obj_options (the new object takes the current app defaults), sel_tools and the
        UI / plot state.

        :param src: the GeometryObject to copy
        :return:    None
        """
        self.follow_geometry = deepcopy_keep_geometry(getattr(src, 'follow_geometry', self.follow_geometry))
        self.tools = deepcopy_keep_geometry(src.tools)
        self.tooluid = getattr(src, 'tooluid', self.tooluid)
        self.units = getattr(src, 'units', self.units)
        self.exclusion_zones = list(getattr(src, 'exclusion_zones', ()))
        self.zones_always_drawn = bool(getattr(src, 'zones_always_drawn', False))
        if 'photocarve' in src.obj_options:
            self.obj_options['photocarve'] = deepcopy(src.obj_options['photocarve'])
        if hasattr(src, 'source_file'):
            self.source_file = deepcopy(src.source_file)

    def build_ui(self):
        try:
            self.ui_disconnect()
        except RuntimeError:
            # Widget C++ object was deleted; recreate UI
            self.set_ui(self.ui_type(app=self.app))

        FlatCAMObj.build_ui(self)

        self.units = self.app.app_units

        row_idx = 0

        n = len(self.tools)
        self.ui.geo_tools_table.setRowCount(n)

        for tooluid_key, tooluid_value in self.tools.items():

            # -------------------- ID ------------------------------------------ #
            tool_id = QtWidgets.QTableWidgetItem('%d' % int(row_idx + 1))
            tool_id.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 0, tool_id)  # Tool name/id

            # Make sure that the tool diameter when in MM is with no more than 2 decimals.
            # There are no tool bits in MM with more than 3 decimals diameter.
            # For INCH the decimals should be no more than 3. There are no tools under 10mils.

            # -------------------- DIAMETER ------------------------------------- #
            dia_item = QtWidgets.QTableWidgetItem('%.*f' % (self.decimals, float(tooluid_value['tooldia'])))
            dia_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 1, dia_item)  # Diameter

            # -------------------- OFFSET   ------------------------------------- #
            try:
                offset_item_txt = self.offset_item_options[tooluid_value['data']['tools_mill_offset_type']]
            except TypeError:
                offset_item_txt = tooluid_value['data']['tools_mill_offset_type']
            except KeyError:
                # for older loaded projects
                offset_item_txt = self._app_option('tools_mill_offset_type')
            offset_item = QtWidgets.QTableWidgetItem(offset_item_txt)
            offset_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 2, offset_item)  # Offset Type

            # -------------------- JOB     ------------------------------------- #
            try:
                job_item_txt = self.job_item_options[tooluid_value['data']['tools_mill_job_type']]
            except TypeError:
                job_item_txt = tooluid_value['data']['tools_mill_job_type']
            except KeyError:
                # for older loaded projects
                job_item_txt = self._app_option('tools_mill_job_type')
            job_item = QtWidgets.QTableWidgetItem(job_item_txt)
            job_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 3, job_item)  # Job Type

            # -------------------- TOOL SHAPE ------------------------------------- #
            try:
                tool_shape_item_txt = self.tool_type_item_options[tooluid_value['data']['tools_mill_tool_shape']]
            except TypeError:
                tool_shape_item_txt = tooluid_value['data']['tools_mill_tool_shape']
            except KeyError:
                # for older loaded projects
                tool_shape_item_txt = self._app_option('tools_mill_tool_shape')
            tool_shape_item = QtWidgets.QTableWidgetItem(tool_shape_item_txt)
            tool_shape_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 4, tool_shape_item)  # Tool Shape

            # -------------------- TOOL UID   ------------------------------------- #
            tool_uid_item = QtWidgets.QTableWidgetItem(str(tooluid_key))
            # ## REMEMBER: THIS COLUMN IS HIDDEN IN OBJECTUI.PY ###
            self.ui.geo_tools_table.setItem(row_idx, 5, tool_uid_item)  # Tool unique ID

            # -------------------- PLOT       ------------------------------------- #
            empty_plot_item = QtWidgets.QTableWidgetItem('')
            empty_plot_item.setFlags(~QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.ui.geo_tools_table.setItem(row_idx, 6, empty_plot_item)
            plot_item = FCCheckBox()
            plot_item.setLayoutDirection(opposite_direction())
            if self.ui.plot_cb.isChecked():
                plot_item.setChecked(True)
            self.ui.geo_tools_table.setCellWidget(row_idx, 6, plot_item)

            row_idx += 1

        for row in range(row_idx):
            self.ui.geo_tools_table.item(row, 0).setFlags(
                self.ui.geo_tools_table.item(row, 0).flags() ^ QtCore.Qt.ItemFlag.ItemIsSelectable)

        self.ui.geo_tools_table.resizeColumnsToContents()
        self.ui.geo_tools_table.resizeRowsToContents()

        vertical_header = self.ui.geo_tools_table.verticalHeader()
        # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        vertical_header.hide()
        self.ui.geo_tools_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        horizontal_header = self.ui.geo_tools_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(4, 40)
        horizontal_header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Fixed)
        horizontal_header.resizeSection(6, checkbox_column_width())
        # horizontal_header.setStretchLastSection(True)
        self.ui.geo_tools_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.ui.geo_tools_table.setColumnWidth(0, 20)
        self.ui.geo_tools_table.setColumnWidth(4, 40)
        self.ui.geo_tools_table.setColumnWidth(6, checkbox_column_width())

        # self.ui.geo_tools_table.setSortingEnabled(True)

        self.ui.geo_tools_table.setMinimumHeight(self.ui.geo_tools_table.getHeight())
        self.ui.geo_tools_table.setMaximumHeight(self.ui.geo_tools_table.getHeight())

        # select only the first tool / row
        selected_row = 0
        try:
            self.select_tools_table_row(selected_row, clearsel=True)
        except Exception as e:
            # when the tools table is empty there will be this error but once the table is populated it will go away
            self.app.log.error('GeometryObject.build_ui() -> %s' % str(e))

        self.ui.geo_tools_table.setColumnHidden(6, False)

        self.ui_connect()

    def set_ui(self, ui):
        # this one adds the 'name' key and the self.ui.name_entry widget in the self.form_fields dict
        FlatCAMObj.set_ui(self, ui)

        self.app.log.debug("GeometryObject.set_ui()")

        assert isinstance(self.ui, GeometryObjectUI), \
            "Expected a GeometryObjectUI, got %s" % type(self.ui)

        self.units = self.app.app_units.upper()
        self.units_found = self.app.app_units

        self.form_fields.update({
            "plot": self.ui.plot_cb,
            "multicolored": self.ui.multicolored_cb,
        })

        # Fill form fields only on object create
        self.to_form()

        # store here the default data for Geometry Data
        self.default_data = {}

        # fill in self.default_data values from self.obj_options
        self.default_data.update(self.obj_options)

        self.tooluid += 1

        # the tool ids are numbers: a project file and some importers give them as text
        self._first_tool()
        self.tools = {int(uid): deepcopy_keep_geometry(tool) for uid, tool in self.tools.items()}

        if not isinstance(self.ui, GeometryObjectUI):
            self.app.log.debug("Expected a GeometryObjectUI, got %s" % type(self.ui))
            return

        # #############################################################################################################
        # ##################################### Setting Values#########################################################
        # #############################################################################################################
        self.ui.vertex_points_entry.set_value(0)
        self.ui.geo_tol_entry.set_value(10 ** -self.decimals)

        # #############################################################################################################
        # ################################ Signals Connection #########################################################
        # #############################################################################################################
        self.ui.level.toggled.connect(self.on_level_changed)

        # Plot state signals
        # self.ui.plot_cb.stateChanged.connect(self.on_plot_cb_click)
        self.ui.multicolored_cb.stateChanged.connect(self.on_multicolored_cb_click)

        # Editor Signal
        self.ui.editor_button.clicked.connect(self.app.on_editing_start)

        # Properties
        self.ui.info_button.toggled.connect(self.on_properties)
        self.calculations_finished.connect(self.update_area_chull)
        self.ui.treeWidget.itemExpanded.connect(self.on_properties_expanded)
        self.ui.treeWidget.itemCollapsed.connect(self.on_properties_expanded)

        # # Buttons Signals
        self.ui.paint_tool_button.clicked.connect(lambda: self.app.paint_tool.run(toggle=True))
        self.ui.generate_ncc_button.clicked.connect(lambda: self.app.ncclear_tool.run(toggle=True))
        self.ui.milling_button.clicked.connect(self.on_milling_button_clicked)

        self.ui.util_button.clicked.connect(lambda st: self.ui.util_frame.show() if st else self.ui.util_frame.hide())
        self.ui.vertex_points_btn.clicked.connect(self.on_calculate_vertex_points)
        self.ui.simplification_btn.clicked.connect(self.on_simplify_geometry)

        self.set_offset_values()

        self.ui.geo_tools_table.itemSelectionChanged.connect(self.on_row_changed)
        self.ui.geo_tools_table.horizontalHeader().sectionClicked.connect(self.table_toggle_all)
        # Show/Hide Advanced Options
        app_mode = self._app_option("global_app_level")
        self.change_level(app_mode)

    def on_row_changed(self):
        pass

    def table_toggle_all(self):
        """
        Will toggle the selection of all rows in the table

        :return:
        """
        sel_model = self.ui.geo_tools_table.selectionModel()
        sel_indexes = sel_model.selectedIndexes()

        # it will iterate over all indexes which means all items in all columns too, but I'm interested only on rows
        sel_rows = set()
        for idx in sel_indexes:
            sel_rows.add(idx.row())

        if sel_rows:
            self.ui.geo_tools_table.clearSelection()
        else:
            self.ui.geo_tools_table.selectAll()

    def on_level_changed(self, checked):
        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: green;
                                                }
                                                """)

        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                                QToolButton
                                                {
                                                    color: red;
                                                }
                                                """)

    def on_milling_button_clicked(self):
        self.app.milling_tool.run(toggle=True)

    def on_calculate_vertex_points(self):
        self.app.log.debug("GeometryObject.on_calculate_vertex_points()")

        vertex_points = 0

        for tool in self.tools:
            geometry = self.tools[tool]['solid_geometry']
            flattened_geo = self.flatten_list(obj_list=geometry)
            for geo in flattened_geo:
                if geo.geom_type == 'Polygon':
                    vertex_points += len(list(geo.exterior.coords))
                    for inter in geo.interiors:
                        vertex_points += len(list(inter.coords))
                if geo.geom_type in ['LineString', 'LinearRing']:
                    vertex_points += len(list(geo.coords))

        self.ui.vertex_points_entry.set_value(vertex_points)
        self.app.inform.emit('[success] %s' % _("Vertex points calculated."))

    def on_simplify_geometry(self):
        self.app.log.debug("GeometryObject.on_simplify_geometry()")

        tol = self.ui.geo_tol_entry.get_value()

        def task_job():
            with self.app.proc_container.new('%s...' % _("Simplify")):
                with history_of(self.app).snapshot(_("Simplify"), [self]):
                    for tool in self.tools:
                        new_tool_geo = []
                        geometry = self.tools[tool]['solid_geometry']
                        flattened_geo = self.flatten_list(obj_list=geometry)
                        for geo in flattened_geo:
                            new_tool_geo.append(geo.simplify(tolerance=tol))
                        self.tools[tool]['solid_geometry'] = deepcopy_keep_geometry(new_tool_geo)

                # plot the new geometry
                self.app.plot_all()

                # update the vertex points number
                self.on_calculate_vertex_points()

                self.app.inform.emit('[success] %s' % _("Done."))

        self.app.worker_task.emit({'fcn': task_job, 'params': []})

    def ui_connect(self):
        for row in range(self.ui.geo_tools_table.rowCount()):
            self.ui.geo_tools_table.cellWidget(row, 6).clicked.connect(self.on_plot_cb_click_table)

        self.ui.plot_cb.stateChanged.connect(self.on_plot_cb_click)

    def ui_disconnect(self):
        for row in range(self.ui.geo_tools_table.rowCount()):
            try:
                self.ui.geo_tools_table.cellWidget(row, 6).clicked.disconnect()
            except (TypeError, AttributeError):
                pass

        try:
            self.ui.plot_cb.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    def select_tools_table_row(self, row, clearsel=None):
        if clearsel:
            self.ui.geo_tools_table.clearSelection()

        if self.ui.geo_tools_table.rowCount() > 0:
            # self.ui.geo_tools_table.item(row, 0).setSelected(True)
            self.ui.geo_tools_table.setCurrentItem(self.ui.geo_tools_table.item(row, 0))

    def dxf_layers(self):
        """DXF layers: one for the object, or one per tool ("<name>_T<id>") in tool mode."""
        name = self.obj_options.get('name') or 'geometry'
        color = self._app_option(self.plot_default_line_option or 'geometry_plot_line', None)
        if self._app_option('geometry_dxf_layers', 'tool') != 'object':
            layers = []
            done = set()
            for tool_id, tool in self.tools.items():
                geometry = tool.get('solid_geometry')
                if geometry is None or (hasattr(geometry, 'is_empty') and geometry.is_empty) or \
                        (isinstance(geometry, (list, tuple)) and not geometry):
                    continue
                if tool_id in done:
                    # the same shapes as an earlier tool: a second layer would hold every shape a second time
                    continue
                done.update(self.shared_tools(tool_id))
                try:
                    weight = float(tool.get('tooldia') or 0.0) or None
                except (TypeError, ValueError):
                    weight = None
                layers.append({'name': "%s_T%s" % (name, tool_id), 'geometry': geometry, 'color': color,
                               'lineweight': weight})
            if len(layers) == 1:
                layers[0]['name'] = name
            return layers
        geometry = self.solid_geometry
        return [{'name': name, 'geometry': geometry, 'color': color}]

    def export_dxf(self):
        """Return a complete DXF drawing of the object on layers, or None on failure."""
        try:
            return build_dxf(self.dxf_layers(), self.app.app_units, self._app_option('geometry_dxf_format'),
                             fit_arcs=bool(self._app_option('geometry_dxf_arcs', True)),
                             panel_offsets=getattr(self, 'dxf_panel_offsets_once', None))
        except Exception as e:
            self.app.log.error("%s.export_dxf() -> %s" % (type(self).__name__, str(e)))
            return None

    def scale(self, xfactor, yfactor=None, point=None):
        """
        Scales all geometry by a given factor.

        :param xfactor:     Factor by which to scale the object's geometry/
        :type xfactor:      float
        :param yfactor:     Factor by which to scale the object's geometry/
        :type yfactor:      float
        :param point:       Point around which to scale
        :return: None
        """
        self.app.log.debug("FlatCAMObj.GeometryObject.scale()")

        if getattr(self, 'obj_options', {}).get('photocarve') and not getattr(self, '_photocarve_unit_change', False):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Regenerate photo engraving to change its scale or skew."))
            return 'fail'

        try:
            xfactor = float(xfactor)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Scale factor has to be a number: integer or float."))
            return

        if yfactor is None:
            yfactor = xfactor
        else:
            try:
                yfactor = float(yfactor)
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Scale factor has to be a number: integer or float."))
                return

        if xfactor == 1 and yfactor == 1:
            return

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        matrix = affine_scale_matrix(xfactor, yfactor, (px, py))

        self._affine_solid_geometry(matrix)

        self.app.proc_container.new_text = ''
        self.app.inform.emit('[success] %s' % _("Done."))

    def skew(self, angle_x, angle_y, point):
        if self.obj_options.get('photocarve'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Regenerate photo engraving to change its scale or skew."))
            return 'fail'
        return super().skew(angle_x, angle_y, point)

    def offset(self, vect):
        """
        Offsets all geometry by a given vector/

        :param vect: (x, y) vector by which to offset the object's geometry.
        :type vect: tuple
        :return: None
        """
        self.app.log.debug("FlatCAMObj.GeometryObject.offset()")

        try:
            dx, dy = vect
        except TypeError:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("An (x,y) pair of values are needed. "
                                   "Probable you entered only one value in the Offset field.")
                                 )
            return

        if dx == 0 and dy == 0:
            return

        tool_count = len(self.tools)
        old_disp_number = 0
        for idx, tool in enumerate(self.tools):
            self.tools[tool]['solid_geometry'] = translate_geometry(self.tools[tool]['solid_geometry'], dx, dy)
            disp_number = int(np.interp(idx + 1, [0, tool_count], [0, 100]))
            if old_disp_number < disp_number <= 100:
                self.app.proc_container.update_view_text(' %d%%' % disp_number)
                old_disp_number = disp_number

        self._affine_zones(affine_translate_matrix(dx, dy))
        self.app.proc_container.new_text = ''
        self.app.inform.emit('[success] %s' % _("Done."))

    def convert_units(self, units):
        self.app.log.debug("FlatCAMObj.GeometryObject.convert_units()")
        self.ui_disconnect()
        if units.upper() not in ('MM', 'IN') or units.upper() == self.units.upper():
            return Geometry.convert_units(self, units)
        factor = 25.4 if units.upper() == 'MM' else 1 / 25.4
        length_keys = ('cutz', 'depthperpass', 'travelz', 'feedrate', 'feedrate_z', 'feedrate_rapid',
                       'endz', 'toolchangez', 'startz', 'tooldia', 'offset_value', 'extracut_length',
                       'vtipdia', 'z_p_depth', 'probe_plate', 'feedrate_probe', 'area_overz',
                       'polish_margin', 'milling_dia', 'tools_iso_voronoi_spacing', 'tools_iso_voronoi_tooldia')

        def convert_length(value):
            return float(value) * 25.4 if units.upper() == 'MM' else float(value) / 25.4

        def convert_values(storage):
            for key in tuple(storage):
                suffix = key.removeprefix('tools_mill_')
                if suffix not in length_keys and suffix not in ('toolchangexy', 'endxy'):
                    continue
                value = storage[key]
                if value is None or (isinstance(value, str) and value == ''):
                    continue
                if suffix in length_keys:
                    storage[key] = convert_length(value)
                elif suffix in ('toolchangexy', 'endxy'):
                    if isinstance(value, str):
                        coords = re.sub(r'[()\[\]]', '', value).split(',')
                        coords = [convert_length(safe_eval(coord)) for coord in coords]
                        if len(coords) != 2:
                            raise ValueError('XY coordinates require two values')
                        storage[key] = ', '.join(str(coord) for coord in coords)
                    else:
                        if len(value) != 2:
                            raise ValueError('XY coordinates require two values')
                        storage[key] = type(value)(convert_length(coord) for coord in value)

        options = deepcopy(dict(self.obj_options))
        tools = deepcopy_keep_geometry(self.tools)
        mark = options.get('photocarve')
        try:
            convert_values(options)
            for tool in tools.values():
                convert_values(tool)
                convert_values(tool.get('data', {}))
                if not mark and tool.get('tooldia') is not None:
                    tool['tooldia'] = float('%.*f' % (self.decimals, tool['tooldia']))
        except (TypeError, ValueError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Cannot convert geometry units"), str(err)))
            return 'fail'

        # Voronoi paths that are still the generated ones stay valid in the new units: the scaled paths are signed
        # again. Edited paths keep their old signature and are refused by the CNC job.
        generated = {uid for uid, tool in self.tools.items() if tool.get('data', {}).get('tools_iso_validated_paths')
                     and tool['data'].get('tools_iso_voronoi_path_signature') ==
                     voronoi_path_signature(tool.get('solid_geometry', []))}
        self._photocarve_unit_change = True
        try:
            Geometry.convert_units(self, units)
        finally:
            self._photocarve_unit_change = False

        def convert_z(geometry):
            if isinstance(geometry, (list, tuple)):
                return type(geometry)(convert_z(item) for item in geometry)
            return scale(geometry, xfact=1, yfact=1, zfact=factor, origin=(0, 0, 0)) if geometry is not None else None

        # Geometry.convert_units() already scaled XY; keep every record and its converted geometry.
        for uid, tool in tools.items():
            if 'solid_geometry' in self.tools[uid]:
                geometry = self.tools[uid]['solid_geometry']
                tool['solid_geometry'] = convert_z(geometry) if mark and not mark['z_is_power'] else deepcopy_keep_geometry(geometry)
        for uid in generated:
            data = tools[uid]['data']
            data['tools_iso_voronoi_units'] = units.upper()
            # the output grid of the validated paths: a coarser unit needs fewer decimals, a finer one more
            decimals = int(data.get('tools_iso_voronoi_coord_decimals', 0))
            data['tools_iso_voronoi_coord_decimals'] = decimals + 2 if units.upper() == 'IN' else max(decimals - 1, 0)
            data['tools_iso_voronoi_path_signature'] = voronoi_path_signature(tools[uid]['solid_geometry'])
        if mark:
            mark['xy_err'] = convert_length(mark['xy_err'])
            mark['units'] = units.upper()
            for cutter in mark['tools'].values():
                cutter['diameter'] = convert_length(cutter['diameter'])
                cutter['tip_diameter'] = convert_length(cutter['tip_diameter'])
        self.obj_options.update(options)
        self.tools = tools
        return factor

    def _collect_elements(self, element, color=None, collected=None):
        """
        Recursively collect leaf geometries into a flat list for batch submission.

        :param element: geometry or collection of geometries
        :param color: color for these geometries
        :param collected: list to append to (created if None)
        :return: list of dicts suitable for add_shapes_batch()
        """
        if collected is None:
            collected = []

        if color is None:
            color = '#FF0000FF'

        try:
            if isinstance(element, (MultiPolygon, MultiLineString)):
                for sub_el in element.geoms:
                    self._collect_elements(sub_el, color=color, collected=collected)
            else:
                for sub_el in element:
                    self._collect_elements(sub_el, color=color, collected=collected)
        except TypeError:
            # Leaf element - not iterable
            if getattr(self, 'obj_options', {}).get('photocarve') and getattr(element, 'has_z', False):
                from shapely import force_2d
                element = force_2d(element)
            collected.append({'shape': element, 'color': color, 'layer': GeometryObject.PLOT_LAYER})

        return collected

    def plot_element(self, element, color=None, visible=None):

        if color is None:
            color = '#FF0000FF'

        visible = self.obj_options['plot'] if visible is None else visible
        try:
            if isinstance(element, (MultiPolygon, MultiLineString)):
                for sub_el in element.geoms:
                    self.plot_element(sub_el, color=color, visible=visible)
            else:
                for sub_el in element:
                    self.plot_element(sub_el, color=color, visible=visible)
        except TypeError:  # Element is not iterable...
            self.add_shape(shape=element, color=color, visible=visible, layer=GeometryObject.PLOT_LAYER)

    def plot(self, visible=None, kind=None, plot_tool=None):
        """
        Plot the object.

        :param visible:     Controls if the added shape is visible of not
        :param kind:        added so there is no error when a project is loaded, and it has both geometry and CNCJob,
                            because CNCJob require the 'kind' parameter. Perhaps the FlatCAMObj.plot()
                            has to be rewritten
        :param plot_tool:   plot a specific tool
        :return:
        """

        # Does all the required setup and returns False
        # if the 'ptint' option is set to False.
        if not FlatCAMObj.plot(self):
            return

        random_color = self.random_color

        try:
            batch = []
            visible = self.obj_options['plot'] if visible is None else visible

            # plot solid geometries found as members of self.tools attribute dict
            film_layers = (self.obj_options.get('film') or {}).get('layers')
            film_geometry = []
            if film_layers:
                for tool in self.tools.values():
                    shapes = tool.get('solid_geometry')
                    film_geometry += [] if shapes is None else (
                        list(shapes) if isinstance(shapes, (list, tuple)) else [shapes])
            if film_layers and len(film_geometry) == len(film_layers):
                # Film faces already include world-unit SVG strokes and opaque overpainting.
                # An edited geometry (a flat list of other length) plots normally.
                for geometry, layer in zip(film_geometry, film_layers):
                    for face in flatten_shapely_geometry(geometry):
                        batch.append({'shape': face, 'color': None, 'face_color': layer['color'],
                                      'layer': self.PLOT_LAYER, 'tolerance': 0})
            else:
                if plot_tool is None:
                    for tooluid_key in self.tools:
                        solid_geometry = self.tools[tooluid_key]['solid_geometry']
                        if 'override_color' in self.tools[tooluid_key]['data']:
                            color = self.tools[tooluid_key]['data']['override_color']
                        else:
                            color = random_color() if self.obj_options['multicolored'] else \
                                self._app_option("geometry_plot_line")

                        batch.extend(self._collect_elements(solid_geometry, color=color))
                else:
                    solid_geometry = self.tools[plot_tool]['solid_geometry']
                    if 'override_color' in self.tools[plot_tool]['data']:
                        color = self.tools[plot_tool]['data']['override_color']
                    else:
                        color = random_color() if self.obj_options['multicolored'] else \
                            self._app_option("geometry_plot_line")

                    batch.extend(self._collect_elements(solid_geometry, color=color))
            if batch:
                self.add_shapes_batch(batch, visible=visible)

            self.shapes.redraw()
            if plot_tool is None:
                self.mark_full_plot()

        except (ObjectDeleted, AttributeError):
            self.shapes.clear(update=True)

    def on_plot_cb_click(self):
        if self.muted_ui:
            return

        self.read_form_item('plot')
        # a toggle only changes the visibility; a new plot of a big object blocks the UI
        if not self.show_plotted_shapes(self.obj_options['plot']):
            self.plot()

        self.ui_disconnect()
        cb_flag = self.ui.plot_cb.isChecked()
        for row in range(self.ui.geo_tools_table.rowCount()):
            table_cb = self.ui.geo_tools_table.cellWidget(row, 6)
            if cb_flag:
                table_cb.setChecked(True)
            else:
                table_cb.setChecked(False)
        self.ui_connect()

    def on_plot_cb_click_table(self):
        # self.ui.cnc_tools_table.cellWidget(row, 2).widget().setCheckState(QtCore.Qt.Unchecked)
        self.ui_disconnect()
        # cw = self.sender()
        # cw_index = self.ui.geo_tools_table.indexAt(cw.pos())
        # cw_row = cw_index.row()
        check_row = 0

        self.shapes.clear(update=True)

        for tooluid_key in self.tools:
            solid_geometry = self.tools[tooluid_key]['solid_geometry']

            # find the geo_plugin_table row associated with the tooluid_key
            for row in range(self.ui.geo_tools_table.rowCount()):
                tooluid_item = int(self.ui.geo_tools_table.item(row, 5).text())
                if tooluid_item == int(tooluid_key):
                    check_row = row
                    break

            if self.ui.geo_tools_table.cellWidget(check_row, 6).isChecked():
                try:
                    color = self.tools[tooluid_key]['data']['override_color']
                    self.plot_element(element=solid_geometry, visible=True, color=color)
                except KeyError:
                    self.plot_element(element=solid_geometry, visible=True)
        self.shapes.redraw()

        # make sure that the general plot is disabled if one of the row plots are disabled and
        # if all the row plots are enabled also enable the general plot checkbox
        cb_cnt = 0
        total_row = self.ui.geo_tools_table.rowCount()
        for row in range(total_row):
            if self.ui.geo_tools_table.cellWidget(row, 6).isChecked():
                cb_cnt += 1
            else:
                cb_cnt -= 1
        if cb_cnt < total_row:
            self.ui.plot_cb.setChecked(False)
        else:
            self.ui.plot_cb.setChecked(True)
        self.ui_connect()

    def on_multicolored_cb_click(self):
        if self.muted_ui:
            return
        self.read_form_item('multicolored')
        self.plot()

    @staticmethod
    def merge(geo_list, geo_final, fuse_tools=None, log=None):
        """
        Merges the geometry of objects in grb_list into the geometry of geo_final.

        :param geo_list:    List of GerberObject Objects to join.
        :param geo_final:   Destination GerberObject object.
        :param fuse_tools:  If True will try to fuse tools of the same type and with equal tool parameters
        :param log:         A logging object
        :return: None
        """

        def marked(objects):
            return any(marked(obj) if isinstance(obj, list) else obj.obj_options.get('photocarve')
                       for obj in objects)

        if marked(geo_list):
            geo_final.app.inform.emit('[ERROR_NOTCL] %s' % _("Certified photo engraving cannot be joined."))
            return 'fail'

        new_options = {}
        new_tools = {}

        for geo_obj in geo_list:
            for option in geo_obj.obj_options:
                if option != 'name':
                    try:
                        new_options[option] = deepcopy(geo_obj.obj_options[option])
                    except Exception as e:
                        if log:
                            log.error("Failed to copy option %s. Error: %s" % (str(option), str(e)))

            # Expand lists
            if type(geo_obj) is list:
                GeometryObject.merge(geo_list=geo_obj, geo_final=geo_final, log=log)
            # If not list, just append
            else:
                # find the tool_uid maximum value in the geo_final
                try:
                    max_uid = max([int(i) for i in new_tools.keys()])
                except ValueError:
                    max_uid = 0

                # add and merge tools. If what we try to merge as Geometry is Excellon's and/or Gerber's then don't try
                # to merge the obj.tools as it is likely there is none to merge.
                if geo_obj.kind != 'gerber' and geo_obj.kind != 'excellon':
                    for tool_uid in geo_obj.tools:
                        new_uid = int(tool_uid)
                        if new_uid in new_tools:
                            new_uid = max_uid + 1
                        max_uid = max(max_uid, new_uid)
                        new_tools[new_uid] = deepcopy_keep_geometry(geo_obj.tools[tool_uid])
                else:
                    new_tools[max_uid + 1] = geo_final.default_tool()
                    new_tools[max_uid + 1]['solid_geometry'] = deepcopy_keep_geometry(geo_obj.solid_geometry)

        geo_final.obj_options.update(new_options)

        if new_tools and fuse_tools is True:
            # merge the geometries of the tools that share the same tool diameter and the same tool_type
            # and the same type, but keep different machining parameters separate
            final_tools = {}
            same_dia = defaultdict(list)
            same_type = defaultdict(list)
            same_tool_type = defaultdict(list)

            # find tools that have the same diameter and group them by diameter
            for k, v in new_tools.items():
                same_dia[v['tooldia']].append(k)

            # find tools that have the same type (job) and group them by type
            for k, v in new_tools.items():
                same_type[v['data'].get('tools_mill_job_type')].append(k)

            # find tools that have the same tool_type and group them by tool_type
            for k, v in new_tools.items():
                same_tool_type[v['data'].get('tools_mill_tool_shape')].append(k)

            # find the intersections in the above groups
            intersect_list = []
            for dia, dia_list in same_dia.items():
                for ty, type_list in same_type.items():
                    for t_ty, tool_type_list in same_tool_type.items():
                        intersection = reduce(np.intersect1d, (dia_list, type_list, tool_type_list)).tolist()
                        # intersection = list(set(dia_list) & set(type_list) & set(tool_type_list))
                        if intersection:
                            parameter_groups = []
                            for tool_uid in intersection:
                                parameters = {
                                    k: v for k, v in new_tools[tool_uid]['data'].items()
                                    if str(k).startswith('tools_')
                                }
                                for group_parameters, tool_group in parameter_groups:
                                    try:
                                        equal = bool(parameters == group_parameters)
                                    except Exception:
                                        equal = False
                                    if equal:
                                        tool_group.append(tool_uid)
                                        break
                                else:
                                    parameter_groups.append((parameters, [tool_uid]))
                            intersect_list.extend(group for _, group in parameter_groups)

            new_tool_nr = 1
            for i_lst in intersect_list:
                new_solid_geo = []
                last_tool = None
                for old_tool in i_lst:
                    # the tool geometry can be a list, a multi-geometry (e.g. the cutout) or a single geometry
                    new_solid_geo += flatten_shapely_geometry(new_tools[old_tool]['solid_geometry'])
                    last_tool = old_tool

                if last_tool is not None:
                    final_tools[new_tool_nr] = \
                        {
                            k: deepcopy_keep_geometry(new_tools[last_tool][k]) for k in new_tools[last_tool] if k != 'solid_geometry'
                        }
                    final_tools[new_tool_nr]['solid_geometry'] = deepcopy_keep_geometry(new_solid_geo)
                    new_tool_nr += 1
        else:
            final_tools = new_tools

        # every Geometry keeps at least 1 tool, also when the sources had no shapes
        geo_final.tools = final_tools or {1: geo_final.default_tool()}
        merge_zones(geo_final, geo_list)

    @staticmethod
    def get_pts(o):
        """
        Returns a list of all points in the object, where
        the object can be a MultiPolygon, Polygon, Not a polygon, or a list
        of such. Search is done recursively.

        :param: geometric object
        :return: List of points
        :rtype: list
        """
        pts = []

        # Iterable: descend into each item.
        try:
            for sub_o in o:
                pts += GeometryObject.get_pts(sub_o)

        # Non-iterable
        except TypeError:
            if o is not None:
                if isinstance(o, MultiPolygon):
                    for poly in o.geoms:
                        pts += GeometryObject.get_pts(poly)
                # ## Descend into .exterior and .interiors
                elif isinstance(o, Polygon):
                    pts += GeometryObject.get_pts(o.exterior)
                    for i in o.interiors:
                        pts += GeometryObject.get_pts(i)
                elif isinstance(o, MultiLineString):
                    for line in o.geoms:
                        pts += GeometryObject.get_pts(line)
                # ## Has .coords: list them.
                else:
                    pts += list(o.coords)
            else:
                return
        return pts
