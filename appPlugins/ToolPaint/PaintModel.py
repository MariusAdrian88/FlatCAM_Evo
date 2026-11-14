"""Explicit-data Paint geometry kernels. Application objects stay in the legacy facade."""

import logging
from copy import deepcopy

import numpy as np
import shapely
from shapely import LineString, LinearRing, MultiLineString, MultiPolygon, Point, Polygon, STRtree, make_valid
from shapely.geometry import base
from shapely.ops import linemerge, unary_union

from appCommon import clipper_ops
from appPlugins.Common.clearing import engine
from appPlugins.Common.clearing.engine import FillKernels, simplified_inside  # noqa: F401  re-export until Stage 7
from appPlugins.Common.clearing.methods import PAINT_METHODS
from appPlugins.Common.clearing.regions import (  # noqa: F401  re-export until Stage 7
    buffer_geometry, merge_overlapping, polygon_parts, swept_area)
from camlib import (AppRTreeStorage, circle_quad_segs, clearing_margin, error_quad_segs,
                    flatten_shapely_geometry, grace)

log = logging.getLogger('base')


def tool_quad_segs(steps_at, diameter):
    """The Shapely segments of a quarter circle of the tool: steps_at(diameter) circle steps, 64 if it is None."""
    return circle_quad_segs(steps_at(diameter) if steps_at is not None else 64)


def sweep_quad_segs(diameter, edge_error, quad_segs):
    """
    The segments of the swept area and of the rest area: their chords stay within the accepted error (the Clearing
    Error, else the error of the circle steps), so the slivers of the sweep are not stock. More would only cost time.
    """
    return error_quad_segs(diameter / 2.0, edge_error) if edge_error else quad_segs


def generate_paint(geometry, tools_storage, diameters, ordering, simplification, decimals,
                   process_polygon, check_abort=None, progress=None, tool_started=None,
                   find_uid=None, get_params=None, buffer=None, simplify=None, edge_error=None, steps_at=None):
    """
    Generate independent tool passes without publishing or changing caller-owned tools.

    :param edge_error:  the Clearing Error in the units of the geometry; None uses the error of the circle steps
    :param steps_at:    callable: the circle steps of a tool diameter; None uses 64
    """
    tools = {uid: {**{key: value for key, value in tool.items() if key != 'paint_cleanup_start'},
                   'data': deepcopy(tool.get('data', {})), 'solid_geometry': []}
             for uid, tool in tools_storage.items()}
    diameters = list(diameters)
    if ordering in (1, 2):
        diameters.sort(reverse=ordering == 2)
    find_uid = find_uid or (lambda dia, storage: find_tool_uid(dia, storage, decimals))
    get_params = get_params or tool_params
    buffer = buffer or buffer_geometry
    simplify = simplify or simplify_geometry
    warnings = []
    source = merge_overlapping(geometry)
    buffered = {}
    state = {'progress': 0}

    def resolve(diameter):
        uid = find_uid(diameter, tools)
        if uid is None:
            return engine.FAIL
        settings = get_params(uid, tools)
        if settings['offset'] not in buffered:
            buffered[settings['offset']] = buffer(source, settings['offset'])
        polygons = buffered[settings['offset']]
        if not polygons:
            warnings.append(('empty', None))
            return None
        return engine.ToolRun(uid, polygons, settings)

    def kernel_steps(diameter, kind):
        return steps_at(diameter) if steps_at is not None else 64

    def clear(polygon, diameter, run):
        return engine.clear_polygon(
            polygon, diameter, lambda: process_polygon(polygon, diameter, run.settings), kernel_steps, 'dwithin',
            simplification, simplify, edge_error)

    def begin(diameter):
        state['progress'] = 0
        if tool_started is not None:
            tool_started(diameter)

    def polygon_done(number, total):
        if progress is not None:
            state['progress'] = progress(number, total, state['progress'])

    def store(run, diameter, paths):
        tools[run.uid]['solid_geometry'] = [path for path in paths if path and not path.is_empty]

    outcome = engine.run_tools(
        diameters, resolve, clear, store, check_abort or (lambda: None), begin=begin, polygon_done=polygon_done,
        on_fail=lambda diameter: warnings.append(('skipped', diameter)),
        on_error=lambda err, diameter: warnings.append(('normal', str(err))))
    last_diameter = outcome.last
    if outcome.status == engine.FAIL:
        return 'fail'
    if check_abort is not None:
        check_abort()
    return {'tools': tools, 'warnings': warnings, 'last_diameter': last_diameter}


def rest_sweep(polygon, diameter, settings, process_polygon, simplify, simplification, keep_stock, edge_error=None,
               quad_segs=16):
    """Paint one rest polygon: (status, elements, swept); status is ok, skipped or failed."""
    return engine.rest_sweep(polygon, diameter, settings, process_polygon, simplify, simplification, keep_stock,
                             edge_error, quad_segs)


def generate_rest_paint(geometry, tools_storage, diameters, simplification, decimals,
                        process_polygon, check_abort=None, progress=None, tool_started=None,
                        rest_offset=0, find_uid=None, get_params=None, buffer=None, simplify=None, edge_error=None,
                        steps_at=None):
    """Rest passes: the stock left for the next tool is what the swept paths of this tool did not cover.

    Tools always run from the largest diameter to the smallest. edge_error and steps_at: see generate_paint().
    """
    tools = {uid: {**{key: value for key, value in tool.items() if key != 'paint_cleanup_start'},
                   'data': deepcopy(tool.get('data', {})), 'solid_geometry': []}
             for uid, tool in tools_storage.items()}
    find_uid = find_uid or (lambda dia, storage: find_tool_uid(dia, storage, decimals))
    get_params = get_params or tool_params
    buffer = buffer or buffer_geometry
    simplify = simplify or simplify_geometry
    if check_abort is not None:
        check_abort()
    polygons = polygon_parts(unary_union(buffer(merge_overlapping(geometry), rest_offset)))
    warnings = []
    output_tools = {}
    if not polygons:
        return {'tools': tools, 'output_tools': output_tools, 'warnings': [('empty', None)]}
    domain = unary_union(polygons)
    state = {'progress': 0}

    def resolve(diameter):
        uid = find_uid(diameter, tools)
        if uid is None:
            return engine.FAIL
        return engine.ToolRun(uid, domain, get_params(uid, tools), polygons)

    def sweep(polygon, diameter, run, segments, keep_stock):
        return engine.rest_sweep(polygon, diameter, run.settings, process_polygon, simplify, simplification,
                                 keep_stock, edge_error, segments)

    def begin(diameter):
        state['progress'] = 0
        if tool_started is not None:
            tool_started(diameter)

    def polygon_done(number, total):
        if progress is not None:
            state['progress'] = progress(number, total, state['progress'])

    def store(run, diameter, cleared):
        if cleared:
            tools[run.uid]['solid_geometry'] = list(cleared)
            output_tools[run.uid] = dict(tools[run.uid])

    outcome = engine.run_rest_tools(
        diameters, resolve, sweep, store, check_abort or (lambda: None),
        lambda diameter, kind: steps_at(diameter) if steps_at is not None else 64, edge_error,
        begin=begin, polygon_done=polygon_done,
        on_fail=lambda diameter: warnings.append(('skipped', diameter)),
        on_error=lambda err, diameter: warnings.append(('rest', str(err))))
    if outcome.status == engine.FAIL:
        return 'fail'
    if check_abort is not None:
        check_abort()
    return {'tools': tools, 'output_tools': output_tools, 'warnings': warnings}


def final_cleanup(geometry, tools_storage, output_tools, diameters, decimals, process_polygon,
                  cleanup, rest_offset=None, check_abort=None, find_uid=None, get_params=None,
                  buffer=None):
    """Select one final tool and return physical cleanup paths and residual stock."""
    tools = {uid: dict(tool) for uid, tool in tools_storage.items()}
    output_tools = {uid: dict(tool) for uid, tool in output_tools.items()}
    find_uid = find_uid or (lambda dia, storage: find_tool_uid(dia, storage, decimals))
    get_params = get_params or tool_params
    buffer = buffer or buffer_geometry
    selected = {find_uid(diameter, tools) for diameter in diameters}
    candidates = [(uid, float(tool['tooldia'])) for uid, tool in tools.items()
                  if uid in selected and float(tool['tooldia']) > 0
                  and tool['data'].get('tools_paint_final_cleanup', False)]
    warnings = []
    state = {}

    def prepare(uid):
        if 'original' not in state:
            state['original'] = unary_union(flatten_shapely_geometry(geometry))
        tool = tools[uid]
        offset = float(tool['data']['tools_paint_offset']) if rest_offset is None else rest_offset
        # Negative offsets never authorize cutting outside the selection or in its holes.
        domain = state['original'].intersection(unary_union(buffer(geometry, offset)))
        if domain.is_empty:
            return None
        settings = get_params(uid, tools)

        def generate_paths(polygon, diameter):
            try:
                result = process_polygon(polygon, diameter, settings)
            except grace:
                raise
            except Exception as err:
                # One polygon that cannot be painted is left to the residual.
                warnings.append(('normal', str(err)))
                return 'fail'
            if result == 'fail':
                return 'fail'
            return list(result.get_objects()) if result else []

        return engine.CleanupJob(domain, generate_paths, settings['conn'])

    def store(uid, paths):
        tool = tools[uid]
        standard = list(output_tools.get(uid, {}).get('solid_geometry', []))
        tool['solid_geometry'] = standard + paths
        tool['paint_cleanup_start'] = len(standard)
        tools[uid] = tools.pop(uid)
        output_tools.pop(uid, None)
        output_tools[uid] = dict(tool)

    residual = engine.final_cleanup(
        candidates, prepare, lambda: [entry for entry in output_tools.values() if entry['solid_geometry']], cleanup,
        store, check_abort or (lambda: None), cleanup_rules="paint")
    if residual is not None and not residual.is_empty:
        warnings.append(('cleanup', None))
    return {'tools': tools, 'output_tools': output_tools, 'residual': residual, 'warnings': warnings,
            'paths': [path for tool in output_tools.values() for path in tool['solid_geometry']]}


def find_tool_uid(tool_dia, tools_storage, decimals):
    wanted = float('%.*f' % (decimals, float(tool_dia)))
    for uid, tool in tools_storage.items():
        if float('%.*f' % (decimals, float(tool['tooldia']))) == wanted:
            return int(uid)
    return None


def tool_params(tool_uid, tools_storage):
    data = tools_storage[tool_uid]['data']
    try:
        angle = float(data.get('tools_paint_crosshatch_angle', 0.0) or 0.0)
    except (TypeError, ValueError):
        angle = 0.0
    return {
        'over': float(data['tools_paint_overlap']) / 100.0,
        'conn': data['tools_paint_connect'],
        'cont': data['tools_paint_contour'],
        'offset': float(data['tools_paint_offset']),
        'angle': angle
    }


def check_tools_fit(geometry, diameters, offsets=None):
    """
    The Check validity test of Paint: a tool paints inside a polygon only if it fits inside it (after the offset).
    Returns (level, narrow, unused): level 'ok', 'warning' (the run goes on) or 'error' (no tool fits inside any
    polygon); narrow is the number of polygons that no tool fits inside; unused lists the tools that fit nowhere.
    """
    # Generation paints the merged geometry, so the check measures that same geometry.
    parts = polygon_parts(merge_overlapping(geometry))
    if not parts or not diameters:
        return 'ok', 0, []
    offsets = offsets or {}
    fits = {}
    for dia in diameters:
        reach = float(dia) / 2.0 + float(offsets.get(dia, 0.0))
        row = []
        for part in parts:
            left, bottom, right, top = part.bounds
            # the bounds check avoids the buffer for the polygons that are clearly too narrow
            # a tool exactly as wide as the polygon fits: it touches the outline, it does not cut past it
            row.append(min(right - left, top - bottom) >= 2 * reach and not clearing_margin(part, reach, 16).is_empty)
        fits[dia] = row
    narrow = sum(1 for index in range(len(parts)) if not any(fits[dia][index] for dia in diameters))
    unused = [dia for dia in diameters if not any(fits[dia])]
    if narrow == len(parts):
        return 'error', narrow, unused
    return ('warning' if narrow or unused else 'ok'), narrow, unused


def simplify_geometry(geometry, tolerance):
    if tolerance > 0.0:
        return [element.simplify(tolerance) for element in geometry]
    return list(geometry)


def selection_geometry(geometry, check_abort=None, expand_multi=False):
    """Flatten selected geometry, retaining the legacy all/area multipart distinction."""
    if check_abort is not None:
        check_abort()
    if geometry is None:
        return None
    result = []
    try:
        parts = geometry.geoms if expand_multi and isinstance(geometry, base.BaseMultipartGeometry) else geometry
        for part in parts:
            if part and (isinstance(part, list) or not part.is_empty):
                result.extend(selection_geometry(part, check_abort, expand_multi))
    except TypeError:
        result.append(Polygon(geometry) if isinstance(geometry, LinearRing) else geometry)
    return result


def _selectable_parts(geometry, out):
    if isinstance(geometry, (list, tuple)):
        for part in geometry:
            _selectable_parts(part, out)
    elif isinstance(geometry, LinearRing):
        out.append(Polygon(geometry))
    elif isinstance(geometry, base.BaseGeometry):
        out.append(geometry)


def select_polygon(geometry, point):
    parts = []
    _selectable_parts(geometry, parts)
    if not parts:
        return None
    x, y = point[0], point[1]
    # Bounds prefilter: only the few candidates around the click get the exact test.
    bounds = _bounds_of(parts)
    candidates = np.nonzero((bounds[:, 0] <= x) & (x <= bounds[:, 2]) &
                            (bounds[:, 1] <= y) & (y <= bounds[:, 3]))[0]
    click = Point(x, y)
    for index in candidates.tolist():
        if parts[index].contains(click):
            return parts[index]
    return None


def _bounds_of(geometries):
    array = np.empty(len(geometries), dtype=object)
    array[:] = geometries
    return shapely.bounds(array)


def reference_area(geometry):
    """Exact union of the reference polygons, grown by a hair so edge-touching shapes stay inside."""
    polygons, rings = [], []
    for part in selection_geometry(geometry, expand_multi=True) or []:
        if isinstance(part, Polygon) and not part.is_empty:
            polygons.append(part)
        elif isinstance(part, (LinearRing, LineString)) and not part.is_empty:
            rings.append(part)
    if rings:
        # A closed outline encloses an area; an open path does not, so it is never turned into a hull.
        try:
            merged = linemerge(rings)
            for line in (merged.geoms if isinstance(merged, MultiLineString) else [merged]):
                if line.is_closed and len(line.coords) >= 4:
                    polygon = Polygon(line)
                    if not polygon.is_empty and polygon.area > 0:
                        polygons.append(polygon if polygon.is_valid else make_valid(polygon))
        except Exception as err:
            log.error('ToolPaint reference_area --> %s' % str(err))
    return unary_union(polygons).buffer(distance=0.0000001, join_style=base.JOIN_STYLE.mitre)


def area_intersection(target, areas, check_abort=None):
    """Polygons of target inside areas; touching points/lines are not areas and are dropped."""
    if isinstance(areas, (list, tuple)):
        areas = unary_union(polygon_parts(areas))
    try:
        common = target.intersection(areas)
    except Exception:
        try:
            common = make_valid(target).intersection(make_valid(areas))
        except Exception as err:
            log.error('ToolPaint area_intersection --> %s' % str(err))
            return []
    polygons, rings = [], []
    for part in selection_geometry(common, check_abort, expand_multi=True) or []:
        if isinstance(part, Polygon):
            if not part.is_empty and part.area > 0:
                polygons.append(part)
        elif isinstance(part, (LinearRing, LineString)) and not part.is_empty:
            rings.append(part)
    if rings:
        # Pieces of a closed outline may arrive split; only rings that really close enclose an area.
        try:
            merged = linemerge(rings)
            for line in (merged.geoms if isinstance(merged, MultiLineString) else [merged]):
                if line.is_closed and len(line.coords) >= 4:
                    polygon = Polygon(line)
                    if not polygon.is_empty and polygon.area > 0:
                        polygons.append(polygon if polygon.is_valid else make_valid(polygon))
        except Exception as err:
            log.error('ToolPaint area_intersection --> %s' % str(err))
    return polygons


class ApertureIndex:
    """Gerber aperture elements indexed by their 'follow' geometry (built once per job)."""

    def __init__(self, apertures):
        self.entries = []
        follows = []
        for aperture in (apertures or {}).values():
            for element in aperture.get('geometry', ()):
                follow = element.get('follow') if hasattr(element, 'get') else None
                if isinstance(follow, base.BaseGeometry) and not follow.is_empty:
                    self.entries.append((aperture, element))
                    follows.append(follow)
        self.tree = STRtree(follows)

    def inside(self, poly_g):
        """(aperture, element) pairs whose follow lies within poly_g, in aperture order, each once."""
        return [self.entries[i] for i in sorted(self.tree.query(poly_g, predicate='contains').tolist())]


def aperture_index(apertures):
    return ApertureIndex(apertures)


def classify_apertures(poly_g, apertures, flashes, traces, index=None):
    if index is None:
        index = aperture_index(apertures)
    for aperture, element in index.inside(poly_g):
        follow = element['follow']
        if isinstance(follow, Point):
            if aperture.get('type') in ('C', 'O', 'R'):
                flashes.setdefault(aperture['type'], []).append(element)
        else:
            traces.setdefault(aperture.get('size', 0.0), []).append(element)


def laser_lines(poly_g, tool_diameter, over, conn, cont, prog_plot, circle_steps,
                geometry_circle_steps, apertures, clear_seed, clear_lines, fill_lines, index=None):
    flashes, traces = {}, {}
    try:
        classify_apertures(poly_g, apertures, flashes, traces, index)
    except grace:
        return 'fail'
    except Exception as err:
        log.error('ToolPaint.paint_polygon_worker() Laser Lines -> Identify flashes/traces--> %s' % str(err))
    result = AppRTreeStorage()
    pads = []
    try:
        for aperture_type, elements in flashes.items():
            for element in elements:
                if 'solid' in element:
                    clear = clear_lines if aperture_type == 'R' else clear_seed
                    steps = circle_steps if aperture_type == 'C' else geometry_circle_steps
                    paths = clear(element['solid'], tooldia=tool_diameter, steps_per_circle=steps,
                                  overlap=over, contour=True, connect=conn, prog_plot=prog_plot)
                    if paths:
                        pads += [path for path in paths.get_objects() if path]
    except grace:
        return 'fail'
    except Exception as err:
        log.error('ToolPaint.paint_polygon_worker() Laser Lines -> Process flashes--> %s' % str(err))
    try:
        result.insert_many([path for path in pads if path])
    except TypeError:
        result.insert(pads)
    copper = []
    try:
        for size, elements in traces.items():
            for element in elements:
                line = element['follow']
                if line and isinstance(line, (LineString, MultiLineString)):
                    paths = fill_lines(line, size, tooldia=tool_diameter,
                                       steps_per_circle=geometry_circle_steps, overlap=over,
                                       contour=cont, connect=conn, prog_plot=prog_plot)
                    # A trace that gives no paths (too narrow) must not stop the other traces.
                    if paths:
                        copper += [path for path in paths.get_objects() if path]
    except grace:
        return 'fail'
    except Exception as err:
        log.error('ToolPaint.paint_polygon_worker() Laser Lines -> Process traces--> %s' % str(err))
    lines = []
    if copper:
        try:
            lines = flatten_shapely_geometry(linemerge(unary_union(copper)))
        except Exception as err:
            log.error('ToolPaint.paint_polygon_worker() Laser Lines -> Merge traces--> %s' % str(err))
            lines = list(copper)
    lines = [line for line in lines if line and isinstance(line, LineString)]
    try:
        result.insert_many(lines)
    except TypeError:
        result.insert(lines)
    return result


def paint_polygon(poly_g, tool_diameter, paint_method, over, conn, cont, prog_plot, circle_steps,
                  clear_shrink, clear_seed, clear_lines, apertures=None, geometry_circle_steps=64,
                  fill_lines=None, progress=None, index=None, crosshatch_angle=0.0, clear_crosshatch=None):
    kernels = FillKernels(shrink=clear_shrink, seed=clear_seed, lines=clear_lines, crosshatch=clear_crosshatch,
                          fill_lines=fill_lines)
    order = {'lines': 2, 'seed': 1, 'shrink': 0}

    def kernel_steps(diameter, kind):
        return geometry_circle_steps if kind == 'geometry' else circle_steps

    return engine.fill_polygon(
        poly_g, tool_diameter, paint_method, over, conn, cont, prog_plot, kernels, PAINT_METHODS, kernel_steps,
        angle=crosshatch_angle, on_attempt=(lambda key: progress(order[key])) if progress is not None else None,
        laser=laser_lines, apertures=apertures, index=index, tag='ToolPaint.paint_polygon_worker')
