# ##########################################################
# FlatCAM EVO: 2D Post-processing for Manufacturing        #
# Panelize core shared by ToolPanelize and the Tcl command #
# MIT Licence                                              #
# ##########################################################

"""
The copy-and-translate part of "panelize", without Qt and without the app object (Stage 5 item 3).

Before this module the Panelize plugin (3 ``job_init_*`` functions) and ``TclCommandPanelize`` (3 more) each held
their own copy of the same loops, with 6 copies of ``translate_recursion``. Both now compute the grid offsets, call
one of the ``panelize_*`` functions here and assign the result to the new object.

Contract (all functions)
------------------------
* The source data is never modified. Geometry is immutable, so translated geometries are new objects and the
  untouched parts of a tool dict are deep-copied.
* ``offsets`` is a list of ``(dx, dy)``, one per copy, row after row (see ``grid_offsets``). The result lists the
  copies in that order.
* Geometry output is FLAT: nested source lists are flattened, the result is a list of shapely geometries (or, for a
  Gerber record, a geometry or a flat list, as the source field was).
* ``abort`` is an optional callable returning True when the user asked for an abort: the function then raises
  ``camlib.grace`` (``GracefulException``), like the old loops did.
* ``progress`` is an optional callable ``progress(element, percent, tag)``: ``element`` is the 1-based copy number,
  ``percent`` an int 0..100 within that copy, ``tag`` '' or 'D:' (drills) / 'S:' (slots).
"""

from copy import deepcopy
from math import isfinite
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, snap, unary_union

from camlib import grace, new_rtree_index, translate_geometry
from appParsers.ParseExcellon import hole_ids

Offset = Tuple[float, float]

# Tolerance of the snapping of the panel paths, and the gap that keeps 2 copies apart.
SNAP_TOLERANCE = 1e-7


# ---------------------------------------------------------------------------------------------------------------
# grid
# ---------------------------------------------------------------------------------------------------------------

def grid_offsets(bounds, rows: int, cols: int, spacing_x: float, spacing_y: float) -> List[Offset]:
    """
    Offsets of the copies of a ``rows`` x ``cols`` grid, row by row, the column index running fastest.
    The pitch is the size of ``bounds`` plus the spacing: copy (row, col) is moved by
    ``(col * (width + spacing_x), row * (height + spacing_y))``.

    :param bounds:    (xmin, ymin, xmax, ymax) of the box that defines the pitch
    :param rows:      number of rows
    :param cols:      number of columns
    :param spacing_x: gap between columns
    :param spacing_y: gap between rows
    :return:          list of (dx, dy), length rows * cols (empty if one of them is not positive)
    """
    xmin, ymin, xmax, ymax = bounds
    if not all(isfinite(v) for v in (*bounds, spacing_x, spacing_y)) or xmax < xmin or ymax < ymin:
        raise ValueError("Panelization requires finite, non-empty reference bounds and spacing.")
    step_x = xmax - xmin + spacing_x
    step_y = ymax - ymin + spacing_y
    return [(col * step_x, row * step_y) for row in range(rows) for col in range(cols)]


def constrain_grid(bounds, rows: int, cols: int, spacing_x: float, spacing_y: float,
                   max_width: float, max_height: float) -> Tuple[int, int, bool]:
    """
    Reduces the number of columns and rows until the panel fits in ``max_width`` x ``max_height``.
    The panel size is ``n * box + (n - 1) * spacing`` per axis.

    :return: (rows, cols, constrained) where ``constrained`` is True if at least one of them was reduced
    """
    xmin, ymin, xmax, ymax = bounds
    if not all(isfinite(v) for v in (*bounds, spacing_x, spacing_y, max_width, max_height)) \
            or xmax < xmin or ymax < ymin:
        raise ValueError("Panelization requires finite, non-empty reference bounds and constraints.")
    width = xmax - xmin
    height = ymax - ymin

    def panel_x(n):
        return width * n + spacing_x * (n - 1)

    def panel_y(n):
        return height * n + spacing_y * (n - 1)

    if panel_x(cols) <= max_width and panel_y(rows) <= max_height:
        return rows, cols, False

    while cols > 0 and panel_x(cols) > max_width:
        cols -= 1
    while rows > 0 and panel_y(rows) > max_height:
        rows -= 1
    return rows, cols, True


# Memory of a panel, per coordinate and per geometry of the source, for one copy. The numbers are an estimate: the
# coordinates in GEOS (16 bytes), the plot buffers, the source text and the temporary arrays of the translation.
BYTES_PER_COORDINATE = 100
BYTES_PER_GEOMETRY = 500


def _geometries_of(data, out: list) -> None:
    """Collects the shapely geometries found in nested dicts, lists and tuples."""
    if isinstance(data, BaseGeometry):
        out.append(data)
    elif isinstance(data, dict):
        for value in data.values():
            _geometries_of(value, out)
    elif isinstance(data, (list, tuple)):
        for value in data:
            _geometries_of(value, out)


def panel_memory_estimate(src_tools, src_solid, copies: int) -> int:
    """
    Estimate of the memory that a panel of ``copies`` copies needs, from the size of the source: the number of
    coordinates and of geometries in the tools (apertures) and in the solid_geometry.

    :param src_tools: tools / apertures of the source object
    :param src_solid: solid_geometry of the source object
    :param copies:    number of copies (rows * columns)
    :return:          bytes (an estimate, not a limit)
    """
    geometries = []
    _geometries_of(src_tools, geometries)
    _geometries_of(src_solid, geometries)
    if not geometries:
        return 0
    coordinates = int(shapely.get_num_coordinates(np.array(geometries, dtype=object)).sum())
    return int(copies) * (coordinates * BYTES_PER_COORDINATE + len(geometries) * BYTES_PER_GEOMETRY)


# ---------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------

def _check_abort(abort: Optional[Callable[[], bool]]) -> None:
    if abort is not None and abort():
        raise grace


def _report(progress, element: int, done: int, total: int, tag: str = '') -> None:
    if progress is not None and total > 0:
        progress(element, min(100, int(100 * done / total)), tag)


def _flatten(geom) -> List[Any]:
    """Leaves of a geometry, a list or nested lists of them (None is dropped)."""
    if geom is None:
        return []
    if isinstance(geom, (list, tuple)):
        out = []
        for item in geom:
            out.extend(_flatten(item))
        return out
    return [geom]


def translate_flat(geom, dx: float, dy: float):
    """
    Translates a geometry or a (nested) list of geometries. A list gives a FLAT list, a single geometry (including
    Multi* and GeometryCollection) stays a single geometry. This is the only translate helper of the module (it
    replaces the 6 ``translate_recursion`` copies); the coordinates move with ``camlib.translate_geometry``.
    """
    if isinstance(geom, (list, tuple)):
        return translate_geometry(_flatten(geom), dx, dy)
    return translate_geometry(geom, dx, dy)


def _split_multi(geom) -> List[Any]:
    """Leaves of a geometry list; a MultiPolygon / MultiLineString is split into its parts."""
    out = []
    for leaf in _flatten(geom):
        if isinstance(leaf, (MultiPolygon, MultiLineString)):
            out.extend(leaf.geoms)
        else:
            out.append(leaf)
    return out


def _translate_solid(src_solid, offset: Offset, split_multi: bool) -> List[Any]:
    """One copy of the source solid_geometry as a flat list."""
    dx, dy = offset
    if split_multi:
        return translate_geometry(_split_multi(src_solid), dx, dy)
    moved = translate_flat(src_solid, dx, dy)
    return moved if isinstance(moved, list) else [moved]


# ---------------------------------------------------------------------------------------------------------------
# Excellon
# ---------------------------------------------------------------------------------------------------------------

def panelize_excellon(src_tools: Dict[Any, dict], offsets: Iterable[Offset],
                      abort: Optional[Callable[[], bool]] = None,
                      progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[Any, dict]:
    """
    Copies the drills and the slots of every tool to every offset.

    The new tools keep the tool data (``tooldia``, ``data``, ...; deep-copied). 'drills' are new Points and 'slots'
    are new ``(start Point, stop Point)`` tuples (also when the source slots are lists). 'solid_geometry' is reset to
    an empty list: the caller runs ``create_geometry()`` on the new object.

    :param src_tools: Excellon tools ({tid: {'tooldia', 'drills', 'slots', 'data', ...}})
    :param offsets:   list of (dx, dy)
    :return:          the new tools dict, same keys in the same order
    """
    tools = {}
    for tid, tool in src_tools.items():
        new_tool = {k: deepcopy(v) for k, v in tool.items()
                    if k not in ('drills', 'slots', 'solid_geometry', 'drills_to', 'slots_to')}
        new_tool['solid_geometry'] = []
        new_tool['drills'] = []
        new_tool['slots'] = []
        # the TO ids follow the holes: each copy of a hole has the id of its source (ids out of step are dropped)
        for kind in ('drills', 'slots'):
            if hole_ids(tool, kind) is not None:
                new_tool[kind + '_to'] = []
        tools[tid] = new_tool

    total_drills = sum(len(t.get('drills') or ()) for t in src_tools.values())
    total_slots = sum(len(t.get('slots') or ()) for t in src_tools.values())

    for element, (dx, dy) in enumerate(offsets, start=1):
        done_drills = 0
        done_slots = 0
        for tid, tool in src_tools.items():
            _check_abort(abort)
            drills = tool.get('drills') or []
            if drills:
                tools[tid]['drills'].extend(translate_geometry(list(drills), dx, dy))
                if 'drills_to' in tools[tid]:
                    tools[tid]['drills_to'].extend(tool['drills_to'])
                done_drills += len(drills)
                _report(progress, element, done_drills, total_drills, 'D:')

            slots = tool.get('slots') or []
            if slots:
                starts = translate_geometry([slot[0] for slot in slots], dx, dy)
                stops = translate_geometry([slot[1] for slot in slots], dx, dy)
                tools[tid]['slots'].extend(zip(starts, stops))
                if 'slots_to' in tools[tid]:
                    tools[tid]['slots_to'].extend(tool['slots_to'])
                done_slots += len(slots)
                _report(progress, element, done_slots, total_slots, 'S:')
    return tools


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

_RECORD_GEOMETRY_KEYS = ('solid', 'clear', 'follow')

# the abort is checked each time this number of records was rebuilt
ABORT_CHECK_RECORDS = 256


def panelize_gerber(src_tools: Dict[Any, dict], src_solid, offsets: Iterable[Offset],
                    abort: Optional[Callable[[], bool]] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None) -> Tuple[Dict[Any, dict], list]:
    """
    Copies the aperture records and the composed solid_geometry of a Gerber to every offset.

    Every aperture keeps its key and its metadata (type, size, macro, modifiers, ... deep-copied); its 'geometry'
    list holds the translated records of copy 1, then of copy 2, ... Each record keeps its keys: 'solid', 'clear'
    and 'follow' are translated, anything else is deep-copied. The records are NOT composed here: a clear record
    stays a clear record, the composed copper of each copy is the translated ``src_solid``.

    The solid_geometry of the result is a flat list of simple geometries (Multi* parts of the source are split).

    All the geometry of the source is read once into one array; each copy moves the coordinates of that array in a
    single call and the records are rebuilt from a plan.

    :param src_tools:  Gerber apertures ({apid: {'type', 'size', 'geometry': [records], ...}})
    :param src_solid:  composed solid_geometry of the source (geometry or (nested) list)
    :param offsets:    list of (dx, dy)
    :return:           (new apertures, new flat solid_geometry list)
    """
    tools = {}
    for apid, tool in src_tools.items():
        new_tool = {k: deepcopy(v) for k, v in tool.items() if k != 'geometry'}
        new_tool['geometry'] = []
        tools[apid] = new_tool

    # Read the geometry once. The plan of a record is a list of (key, kind, first, last): kind 'g' is one geometry,
    # 'l' a list of geometries (flat), 'c' any other value, copied again for every copy.
    flat = []
    plan = {}
    total = 0
    sequence = []
    offsets = list(offsets)
    for apid, tool in src_tools.items():
        records_plan = []
        for record in tool.get('geometry') or ():
            record_plan = []
            for key, value in record.items():
                if key in _RECORD_GEOMETRY_KEYS and isinstance(value, (list, tuple)):
                    leaves = _flatten(value)
                    record_plan.append((key, 'l', len(flat), len(flat) + len(leaves)))
                    flat.extend(leaves)
                elif key in _RECORD_GEOMETRY_KEYS and isinstance(value, BaseGeometry):
                    record_plan.append((key, 'g', len(flat), len(flat) + 1))
                    flat.append(value)
                elif key == 'seq':
                    record_plan.append((key, 's', value, None))
                    sequence.append(value)
                else:
                    record_plan.append((key, 'c', value, None))
            records_plan.append(record_plan)
        plan[apid] = records_plan
        total += len(records_plan)
    solid_start = len(flat)
    flat.extend(_split_multi(src_solid))

    geometries = np.empty(len(flat), dtype=object)
    geometries[:] = flat
    has_z = bool(shapely.has_z(geometries).any()) if len(flat) else False
    coordinates = shapely.get_coordinates(geometries, include_z=has_z)

    # 'seq' is the place of a record in the file (a clear object erases the objects before it). The records of
    # copy 2 get the numbers after those of copy 1, and so on: copy after copy. That order gives the copper of the
    # panel only if the copies do not touch (a clear object of a copy must not erase another copy). If they can
    # touch, or the numbers of the source are not complete, the panel records get no 'seq': its export then
    # writes regions.
    seq_span = 0
    if sequence and len(sequence) == total and len(flat) \
            and all(isinstance(s, int) and not isinstance(s, bool) and s >= 0 for s in sequence):
        xmin, ymin, xmax, ymax = shapely.total_bounds(geometries).tolist()
        if all(isfinite(v) for v in (xmin, ymin, xmax, ymax)) \
                and _copies_are_apart(offsets, xmax - xmin, ymax - ymin):
            seq_span = max(sequence) + 1

    solid = []
    for element, (dx, dy) in enumerate(offsets, start=1):
        _check_abort(abort)
        moved_coordinates = coordinates.copy()
        moved_coordinates[:, 0] += dx
        moved_coordinates[:, 1] += dy
        moved = shapely.set_coordinates(geometries.copy(), moved_coordinates).tolist()

        done = 0
        for apid, records_plan in plan.items():
            _check_abort(abort)
            target = tools[apid]['geometry']
            for number, record_plan in enumerate(records_plan):
                if number % ABORT_CHECK_RECORDS == 0:
                    _check_abort(abort)
                new_record = {}
                for key, kind, first, last in record_plan:
                    if kind == 'g':
                        new_record[key] = moved[first]
                    elif kind == 'l':
                        new_record[key] = moved[first:last]
                    elif kind == 's':
                        if seq_span:
                            new_record[key] = first + (element - 1) * seq_span
                    else:
                        new_record[key] = deepcopy(first)
                target.append(new_record)
            done += len(records_plan)
            _report(progress, element, done, total)

        solid.extend(moved[solid_start:])
        _report(progress, element, 1, 1)
    # an abort during the records of the last copy must not give a finished panel
    _check_abort(abort)
    return tools, solid


def source_bounds(src_tools, src_solid) -> Optional[Tuple[float, float, float, float]]:
    """
    Bounds of all the geometry of a source object: the solid_geometry and every record of the tools / apertures
    (a clear object can be larger than the copper). None if there is no geometry or the bounds are not finite.
    """
    geometries = []
    _geometries_of(src_tools, geometries)
    _geometries_of(src_solid, geometries)
    if not geometries:
        return None
    bounds = tuple(shapely.total_bounds(np.array(geometries, dtype=object)).tolist())
    return bounds if all(isfinite(v) for v in bounds) else None


def step_repeat_grid(bounds, offsets: Iterable[Offset], rows: int, cols: int) \
        -> Optional[Tuple[int, int, float, float]]:
    """
    The values of a Gerber Step and Repeat block that gives the copies of ``offsets``: (columns, rows, step in X,
    step in Y), or None if such a block can not be used:

    * the offsets are not the regular grid of ``grid_offsets`` (column index fastest, steps not negative);
    * there is 1 copy only;
    * the copies can touch (``bounds``, from ``source_bounds``, against the steps): in a Step and Repeat block a
      clear object of a copy erases what is under it, also the copper of another copy.

    :param bounds:  (xmin, ymin, xmax, ymax) of all the geometry of the source
    :param offsets: list of (dx, dy)
    :param rows:    number of rows of the grid
    :param cols:    number of columns of the grid
    """
    offsets = list(offsets)
    if bounds is None or rows < 1 or cols < 1 or len(offsets) != rows * cols or len(offsets) < 2:
        return None
    step_x = offsets[1][0] if cols > 1 else 0.0
    step_y = offsets[cols][1] if rows > 1 else 0.0
    if not (isfinite(step_x) and isfinite(step_y)) or step_x < 0 or step_y < 0:
        return None
    if any(offset != (col * step_x, row * step_y)
           for (row, col), offset in zip(((r, c) for r in range(rows) for c in range(cols)), offsets)):
        return None
    xmin, ymin, xmax, ymax = bounds
    if not _copies_are_apart(offsets, xmax - xmin, ymax - ymin):
        return None
    return cols, rows, float(step_x), float(step_y)


def panel_region_polygons(source_polygons: list, offsets: Iterable[Offset],
                          abort: Optional[Callable[[], bool]] = None) -> Optional[list]:
    """
    The polygons of the region export of a Gerber panel, made from the polygons of the region export of its source
    (``Gerber.region_export_polygons(source)``: valid and disjoint): the source polygons moved to every offset.

    This is the union of the copper of the panel only if the copies do not touch each other. That is checked on the
    real bounds of the source polygons (see ``_copies_are_apart``). If the copies can touch, or 2 offsets are equal,
    the result is None: the caller must let the export make the union of the whole panel.

    :param source_polygons: disjoint polygons of the source
    :param offsets:         list of (dx, dy)
    :return:                flat list of polygons (copy by copy), or None
    """
    offsets = list(offsets)
    if not source_polygons or not offsets:
        return None
    xmin, ymin, xmax, ymax = shapely.total_bounds(np.array(source_polygons, dtype=object)).tolist()
    if not all(isfinite(v) for v in (xmin, ymin, xmax, ymax)) \
            or not _copies_are_apart(offsets, xmax - xmin, ymax - ymin):
        return None
    moved = []
    for dx, dy in offsets:
        _check_abort(abort)
        moved.extend(translate_geometry(list(source_polygons), dx, dy))
    return moved


def copy_gerber_attributes(dst, src) -> None:
    """
    The Gerber-level state a panel inherits from its source: ``export_as_regions`` (Stage 0C: the apertures of a
    region-exported object do not describe its geometry, so the panel must export regions too) and the
    ``aperture_macros`` that the 'AM' apertures refer to. The X2 / X3 attributes are copied too: the records of the
    panel keep their 'to' ids, so the table of the ids must go with them.
    """
    dst.export_as_regions = bool(getattr(src, 'export_as_regions', False))
    # The panel records have 'seq' numbers only if the copies do not touch (see panelize_gerber); without the
    # numbers the order is not used.
    dst.records_in_order = getattr(src, 'records_in_order', False) is True
    dst.aperture_macros = deepcopy(getattr(src, 'aperture_macros', {}))
    for name, default in (('attributes', {'TF': {}, 'TA': {}, 'TO': {}}), ('to_attributes', {}),
                          ('file_attributes', {})):
        setattr(dst, name, deepcopy(getattr(src, name, default)))


# ---------------------------------------------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------------------------------------------

def panelize_geometry(src_tools: Dict[Any, dict], src_solid, multigeo: bool, offsets: Iterable[Offset],
                      abort: Optional[Callable[[], bool]] = None,
                      progress: Optional[Callable[[int, int, str], None]] = None) -> Tuple[Dict[Any, dict], list]:
    """
    Copies the solid_geometry of a Geometry (and, if ``multigeo``, the 'solid_geometry' of every tool) to every
    offset. Multi* parts are split, so the result is a flat list of simple geometries. Empty geometries are dropped
    from the tools, kept in the composed solid_geometry (as before).

    A non-multigeo Geometry keeps its tools unchanged (deep copy).

    :return: (new tools, new flat solid_geometry list)
    """
    tools = {}
    for tid, tool in src_tools.items():
        if multigeo:
            new_tool = {k: deepcopy(v) for k, v in tool.items() if k != 'solid_geometry'}
            new_tool['solid_geometry'] = []
        else:
            new_tool = deepcopy(tool)
        tools[tid] = new_tool

    # Flatten and split the immutable source once, rather than for every copy.
    source_solid = _split_multi(src_solid)
    source_tools = {tid: _split_multi(tool.get('solid_geometry')) for tid, tool in src_tools.items()} if multigeo else {}
    solid = []
    for element, (dx, dy) in enumerate(offsets, start=1):
        if multigeo:
            for tid, geometry in source_tools.items():
                _check_abort(abort)
                moved = translate_geometry(geometry, dx, dy)
                tools[tid]['solid_geometry'].extend(g for g in moved if not g.is_empty)

        _check_abort(abort)
        solid.extend(translate_geometry(source_solid, dx, dy))
        _report(progress, element, 1, 1)
    return tools, solid


def gerber_tools_from_geometry(src_tools: Dict[Any, dict], src_solid, multigeo: bool) -> Tuple[Dict[Any, dict], list]:
    """
    Builds the Gerber apertures of a Geometry, to panelize it as a Gerber. The result is the input of
    ``panelize_gerber``.

    Everything goes to the region 'aperture' 0 (type 'REG', size 0.0). Each element becomes 1 record:

    * a polygon: ``{'solid': polygon, 'follow': polygon.exterior}``;
    * anything else (line, point, ...): ``{'solid': geo, 'follow': geo}``.

    The elements are:

    * ``multigeo``: the parts of the 'solid_geometry' of every tool in ``src_tools`` (Multi* split, empty ones
      dropped). A line is merged with ``linemerge`` first, if that works, then turned into a filled polygon if
      ``Polygon(line)`` accepts it (an open line of 3 or more points too: the former panel code did this). The
      returned solid is the union of the record solids, so it shows the same copper as the records.
    * not ``multigeo``: the parts of ``src_solid`` (Multi* split). A line stays a line: the returned solid is that
      flat list, and a filled record would hold copper that the solid does not have.

    The inputs are not changed: geometry is immutable, so the records hold the same geometry objects.

    :param src_tools:  tools of the Geometry ({tid: {'solid_geometry': [...], ...}})
    :param src_solid:  solid_geometry of the Geometry (geometry or (nested) list)
    :param multigeo:   True if the geometry is in the tools
    :return:           (apertures {0: {'type': 'REG', 'size': 0.0, 'geometry': [records]}}, composed solid_geometry)
    """
    if multigeo:
        elements = []
        for tool in src_tools.values():
            elements.extend(g for g in _split_multi(tool.get('solid_geometry')) if not g.is_empty)
    else:
        elements = _split_multi(src_solid)

    records = []
    for geo in elements:
        if multigeo:
            try:
                geo = linemerge(geo)
            except Exception:
                pass
            try:
                geo = Polygon(geo)
            except Exception:
                pass
        if isinstance(geo, Polygon):
            record = {'solid': geo, 'follow': geo.exterior}
        else:
            record = {'solid': geo, 'follow': geo}
        records.append(record)

    if multigeo:
        solid = unary_union([r['solid'] for r in records])
    else:
        solid = list(elements)
    tools = {0: {'type': 'REG', 'size': 0.0, 'geometry': records}}
    return tools, solid


def _split_lines(geometries) -> Tuple[List[LineString], list]:
    """The non-empty lines of a list of geometries (a MultiLineString is split) and the other geometries."""
    lines = []
    other_geo = []
    for geo in geometries or []:
        if isinstance(geo, LineString):
            if not geo.is_empty:
                lines.append(geo)
        elif isinstance(geo, MultiLineString):
            lines.extend(geo.geoms)
        else:
            other_geo.append(geo)
    return lines, other_geo


def _snap_lines(lines: List[LineString], abort: Optional[Callable[[], bool]]) -> None:
    """Snaps the lines to each other (tolerance 1e-7), in place, in the order of the original pairwise loop."""
    tolerance = SNAP_TOLERANCE
    bounds = []
    for line in lines:
        _check_abort(abort)
        bounds.append(line.bounds)
    # the lines as an array, kept equal to the list: the distance check reads a slice of it
    current = np.empty(len(lines), dtype=object)
    current[:] = lines
    # the id of a line is its position (bulk load from an array)
    index = new_rtree_index(boxes=bounds)
    try:

        pairwise = False
        for idx in range(len(lines)):
            line = lines[idx]
            _check_abort(abort)
            if pairwise:
                candidates = range(idx + 1, len(lines))
            else:
                xmin, ymin, xmax, ymax = bounds[idx]
                nearby = index.intersection((xmin - tolerance, ymin - tolerance,
                                             xmax + tolerance, ymax + tolerance))
                candidates = sorted(candidate for candidate in nearby if candidate > idx)
                # ponytail: use the original loop for a dense remainder; its worst case stays quadratic.
                pairwise = len(candidates) == len(lines) - idx - 1
            if not candidates:
                continue
            # A snap changes a line only if it is within the tolerance of the other line. The bounds of nested
            # rings (isolation paths) overlap almost always, so the real distance is checked first, in one call.
            others = current[idx + 1:] if pairwise else current[candidates]
            first = idx + 1 if pairwise else None
            # The prepared geometry must be the first argument. The line can be an object of the source: its
            # prepared state is removed again, the source stays as it was.
            was_prepared = shapely.is_prepared(line)
            shapely.prepare(line)
            try:
                near = np.flatnonzero(shapely.dwithin(line, others, tolerance))
            finally:
                if not was_prepared:
                    shapely.destroy_prepared(line)
            for position in near.tolist():
                idx_s = first + position if pairwise else candidates[position]
                _check_abort(abort)
                res = snap(lines[idx_s], line, tolerance=tolerance)
                if res and not res.is_empty:
                    lines[idx_s] = res
                    current[idx_s] = res
                    if not pairwise:
                        new_bounds = res.bounds
                        if new_bounds != bounds[idx_s]:
                            index.delete(idx_s, bounds[idx_s])
                            index.insert(idx_s, new_bounds)
                            bounds[idx_s] = new_bounds
    finally:
        index.close()


def merge_tool_lines(tools: Dict[Any, dict], snap_overlaps: bool = False,
                     abort: Optional[Callable[[], bool]] = None) -> None:
    """
    Path optimization of the multigeo tools of a panel (the cutout fix): in every tool the lines are merged with
    ``linemerge`` and fused with ``unary_union`` so the border shared by 2 neighbour copies is cut once. With
    ``snap_overlaps`` the lines are first snapped to each other (tolerance 1e-7) so almost-touching paths fuse.
    A dynamic R-tree skips distant pairs while preserving the original snapping order. Dense overlapping bounds
    still require quadratic work. The tools are changed in place.

    This works on the lines of all the copies. ``optimize_panel_lines`` is faster when the copies are apart.
    """
    for tool in tools.values():
        _check_abort(abort)
        lines, other_geo = _split_lines(tool.get('solid_geometry'))

        if snap_overlaps and len(lines) > 1:
            _snap_lines(lines, abort)

        _check_abort(abort)
        fused_lines = linemerge(lines)
        fused_lines = [unary_union(fused_lines)] if not fused_lines.is_empty else []
        tool['solid_geometry'] = fused_lines + other_geo


def _copies_are_apart(offsets: List[Offset], width: float, height: float) -> bool:
    """
    True if copies of a shape of ``width`` x ``height`` moved by ``offsets`` stay farther apart than the snap
    tolerance: on each axis there is one distinct offset, or the smallest gap between distinct offsets is more than
    the size of the shape plus the tolerance. Then the bounding boxes of 2 copies never touch.

    2 copies at the same offset are NOT apart: they are on top of each other, and their paths must be fused.
    """
    if len(set(offsets)) != len(offsets):
        return False
    for axis, size in ((0, width), (1, height)):
        values = sorted({offset[axis] for offset in offsets})
        if any(not isfinite(value) for value in values):
            return False
        if len(values) > 1 and min(b - a for a, b in zip(values, values[1:])) <= size + SNAP_TOLERANCE:
            return False
    return True


def optimize_panel_lines(src_tools: Dict[Any, dict], tools: Dict[Any, dict], offsets: Iterable[Offset],
                         snap_overlaps: bool = False, abort: Optional[Callable[[], bool]] = None) -> None:
    """
    Same result as ``merge_tool_lines`` on the tools of a panel, made faster when the copies cannot interact.

    ``tools`` are the multigeo tools made by ``panelize_geometry(src_tools, ..., offsets)``. For a tool whose copies
    stay apart (see ``_copies_are_apart``, measured on the real bounds of its lines) the lines of the SOURCE tool
    are snapped (if asked) and merged once and the merged parts are moved to every offset; the parts of all the
    copies make one MultiLineString (copy by copy). Any other tool, or a tool that does not look as expected, goes
    through ``merge_tool_lines``. The geometries that are not lines are kept as they are. ``tools`` are changed in
    place.
    """
    offsets = list(offsets)
    for tid, tool in tools.items():
        _check_abort(abort)
        src_tool = src_tools.get(tid)
        fast = None
        if src_tool is not None and offsets:
            src_lines, _ = _split_lines(_split_multi(src_tool.get('solid_geometry')))
            lines, other_geo = _split_lines(tool.get('solid_geometry'))
            if src_lines and len(lines) == len(src_lines) * len(offsets):
                xmin, ymin, xmax, ymax = MultiLineString(src_lines).bounds
                if all(isfinite(v) for v in (xmin, ymin, xmax, ymax)) \
                        and _copies_are_apart(offsets, xmax - xmin, ymax - ymin):
                    fast = (src_lines, other_geo)
        if fast is None:
            merge_tool_lines({tid: tool}, snap_overlaps=snap_overlaps, abort=abort)
            continue

        src_lines, other_geo = fast
        if snap_overlaps and len(src_lines) > 1:
            _snap_lines(src_lines, abort)
        _check_abort(abort)
        merged = linemerge(src_lines)
        if merged.is_empty:
            tool['solid_geometry'] = other_geo
            continue
        fused = unary_union(merged)
        parts = list(getattr(fused, 'geoms', [fused]))
        moved = []
        for dx, dy in offsets:
            _check_abort(abort)
            moved.extend(translate_geometry(parts, dx, dy))
        fused_lines = moved[0] if len(moved) == 1 else MultiLineString(moved)
        tool['solid_geometry'] = [fused_lines] + other_geo
