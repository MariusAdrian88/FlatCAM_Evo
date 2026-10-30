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

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import linemerge, snap, unary_union

from camlib import grace, new_rtree_index, translate_geometry

Offset = Tuple[float, float]


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
        new_tool = {k: deepcopy(v) for k, v in tool.items() if k not in ('drills', 'slots', 'solid_geometry')}
        new_tool['solid_geometry'] = []
        new_tool['drills'] = []
        new_tool['slots'] = []
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
                done_drills += len(drills)
                _report(progress, element, done_drills, total_drills, 'D:')

            slots = tool.get('slots') or []
            if slots:
                starts = translate_geometry([slot[0] for slot in slots], dx, dy)
                stops = translate_geometry([slot[1] for slot in slots], dx, dy)
                tools[tid]['slots'].extend(zip(starts, stops))
                done_slots += len(slots)
                _report(progress, element, done_slots, total_slots, 'S:')
    return tools


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

_RECORD_GEOMETRY_KEYS = ('solid', 'clear', 'follow')


def panelize_gerber(src_tools: Dict[Any, dict], src_solid, offsets: Iterable[Offset],
                    abort: Optional[Callable[[], bool]] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None) -> Tuple[Dict[Any, dict], list]:
    """
    Copies the aperture records and the composed solid_geometry of a Gerber to every offset.

    Every aperture keeps its key and its metadata (type, size, macro, modifiers, ... deep-copied); its 'geometry'
    list holds the translated records of copy 1, then of copy 2, ... Each record keeps its keys: 'solid', 'clear'
    and 'follow' are translated, anything else is deep-copied. The records are NOT composed here: a clear record
    stays a clear record, the composed copper of each copy is the translated ``src_solid``.

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

    total = sum(len(t.get('geometry') or ()) for t in src_tools.values())
    solid = []

    for element, (dx, dy) in enumerate(offsets, start=1):
        done = 0
        for apid, tool in src_tools.items():
            _check_abort(abort)
            target = tools[apid]['geometry']
            for record in tool.get('geometry') or ():
                _check_abort(abort)
                new_record = {}
                for key, value in record.items():
                    if key in _RECORD_GEOMETRY_KEYS:
                        new_record[key] = translate_flat(value, dx, dy)
                    else:
                        new_record[key] = deepcopy(value)
                target.append(new_record)
                done += 1
                _report(progress, element, done, total)

        _check_abort(abort)
        solid.extend(_translate_solid(src_solid, (dx, dy), split_multi=False))
    return tools, solid


def copy_gerber_attributes(dst, src) -> None:
    """
    The Gerber-level state a panel inherits from its source: ``export_as_regions`` (Stage 0C: the apertures of a
    region-exported object do not describe its geometry, so the panel must export regions too) and the
    ``aperture_macros`` that the 'AM' apertures refer to. The X2 / X3 attributes are copied too: the records of the
    panel keep their 'to' ids, so the table of the ids must go with them.
    """
    dst.export_as_regions = bool(getattr(src, 'export_as_regions', False))
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

    The inputs are not changed; the records hold copies.

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
        records.append(deepcopy(record))

    if multigeo:
        solid = unary_union([r['solid'] for r in records])
    else:
        solid = deepcopy(elements)
    tools = {0: {'type': 'REG', 'size': 0.0, 'geometry': records}}
    return tools, solid


def merge_tool_lines(tools: Dict[Any, dict], snap_overlaps: bool = False,
                     abort: Optional[Callable[[], bool]] = None) -> None:
    """
    Path optimization of the multigeo tools of a panel (the cutout fix): in every tool the lines are merged with
    ``linemerge`` and fused with ``unary_union`` so the border shared by 2 neighbour copies is cut once. With
    ``snap_overlaps`` the lines are first snapped to each other (tolerance 1e-7) so almost-touching paths fuse.
    A dynamic R-tree skips distant pairs while preserving the original snapping order. Dense overlapping bounds
    still require quadratic work. The tools are changed in place.
    """
    for tool in tools.values():
        _check_abort(abort)
        lines = []
        other_geo = []
        for geo in tool.get('solid_geometry') or []:
            if isinstance(geo, LineString):
                if not geo.is_empty:
                    lines.append(geo)
            elif isinstance(geo, MultiLineString):
                lines.extend(geo.geoms)
            else:
                other_geo.append(geo)

        if snap_overlaps and len(lines) > 1:
            tolerance = 1e-7
            bounds = []
            for line in lines:
                _check_abort(abort)
                bounds.append(line.bounds)
            # the id of a line is its position (bulk load from an array)
            index = new_rtree_index(boxes=bounds)
            try:

                pairwise = False
                for idx, line in enumerate(lines):
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
                    for idx_s in candidates:
                        _check_abort(abort)
                        res = snap(lines[idx_s], line, tolerance=tolerance)
                        if res and not res.is_empty:
                            lines[idx_s] = res
                            if not pairwise:
                                new_bounds = res.bounds
                                if new_bounds != bounds[idx_s]:
                                    index.delete(idx_s, bounds[idx_s])
                                    index.insert(idx_s, new_bounds)
                                    bounds[idx_s] = new_bounds
            finally:
                index.close()

        _check_abort(abort)
        fused_lines = linemerge(lines)
        fused_lines = [unary_union(fused_lines)] if not fused_lines.is_empty else []
        tool['solid_geometry'] = fused_lines + other_geo
