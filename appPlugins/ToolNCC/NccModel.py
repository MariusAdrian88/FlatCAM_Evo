"""Explicit-data NCC geometry kernels. Application objects stay in the legacy adapter."""

import builtins
import logging
import math
from copy import deepcopy

import numpy as np
import shapely
from shapely import LineString, LinearRing, MultiLineString, MultiPolygon, Polygon
from shapely.geometry import base
from shapely.ops import unary_union

from camlib import clearing_margin, flatten_shapely_geometry, grace, polygon_gap_pairs
from appPlugins.Common.clearing import engine
from appPlugins.Common.clearing.engine import FillKernels, quad_segs  # noqa: F401  re-export until Stage 7
from appPlugins.Common.clearing.methods import NCC_METHODS
from appPlugins.Common.clearing.regions import (  # noqa: F401  re-export until Stage 7
    apply_margin, complement, offset_geometry, swept_area)

log = logging.getLogger('base')


def _(text):
    # translate through the app gettext when it is installed, plain text otherwise (tests, Tcl)
    func = builtins.__dict__.get('_')
    return func(text) if func else text


def copy_tool(tool):
    """Copy of a tool-table entry: own 'data' and own geometry list; the immutable Shapely objects are shared."""
    new = dict(tool)
    if 'data' in tool:
        new['data'] = deepcopy(tool['data'])
    if isinstance(tool.get('solid_geometry'), list):
        new['solid_geometry'] = list(tool['solid_geometry'])
    return new


def copy_tools(tools):
    return {uid: copy_tool(tool) for uid, tool in (tools or {}).items()}


def in_diameters(tooldia, diameters, decimals):
    """True if the tool diameter is one of the diameters, compared by rounding like tool_uid()."""
    wanted = {round(float(dia), decimals) for dia in diameters}
    return round(float(tooldia), decimals) in wanted


def smallest_gerber_gap(apertures, decimals, check_abort=None):
    """
    Smallest copper gap of a Gerber (its aperture storage), the largest tool that still passes between the copper.
    Qt-free. Returns (message or None, gap): the gap is numpy.inf when it can not be measured, then the message
    says why. check_abort is called for every geometry element (it raises to stop).
    """
    total_geo = []
    for aperture in list((apertures or {}).values()):
        for geo_el in aperture.get('geometry', []):
            if check_abort is not None:
                check_abort()
            solid = geo_el.get('solid')
            if solid is not None and solid.is_valid:
                total_geo.append(solid)
    # the parts of a MultiPolygon solid one by one: MultiPolygon() refuses a list that holds one
    total_geo = flatten_shapely_geometry(MultiPolygon(flatten_shapely_geometry(total_geo)).buffer(0)) \
        if total_geo else []
    if len(total_geo) <= 1:
        return _('Too few polygons in the Gerber object to determine distances.'), np.inf
    min_dist = min(polygon_gap_pairs(total_geo, decimals))
    min_dist = round(min_dist - 10 ** -decimals, decimals)
    if min_dist <= 0:
        return _('Could not find a safe tool diameter.'), np.inf
    return None, min_dist


def check_tools_fit(apertures, clear_diameters, iso_diameters, decimals):
    """
    The Check validity test before a run. Returns (level, gap, too_big, note): level 'ok', 'warning' (the run goes
    on) or 'error' (the run stops); too_big lists the diameters larger than the smallest copper gap; note says why
    the gap could not be measured (then the level is 'ok').
    An isolation tool larger than the gap can not isolate completely; if no tool fits at all nothing can be cleared.
    """
    note, gap = smallest_gerber_gap(apertures, decimals)
    if gap == np.inf:
        return 'ok', None, [], note
    big_iso = [dia for dia in iso_diameters if round(float(dia), decimals) > gap]
    big_clear = [dia for dia in clear_diameters if round(float(dia), decimals) > gap]
    fits = [dia for dia in list(clear_diameters) + list(iso_diameters) if round(float(dia), decimals) <= gap]
    if big_iso or not fits:
        return 'error', gap, big_iso + big_clear if not fits else big_iso, None
    return ('warning' if big_clear else 'ok'), gap, big_clear, None


def entire_object(geometry):
    single = (isinstance(geometry, MultiPolygon) and len(geometry.geoms) == 1) or (
        isinstance(geometry, list) and len(geometry) == 1 and isinstance(geometry[0], Polygon))
    if single:
        return unary_union(geometry)
    if isinstance(geometry, MultiPolygon):
        return geometry.convex_hull
    return unary_union(geometry).convex_hull


def selection_areas(geometry):
    return flatten_shapely_geometry(unary_union(geometry))


def reference_area(source_geometry, reference_geometry, reference_kind):
    if reference_kind == 'geometry':
        return flatten_shapely_geometry(reference_geometry)
    if reference_kind == 'gerber':
        return flatten_shapely_geometry(unary_union(source_geometry).convex_hull.intersection(
            unary_union(reference_geometry).convex_hull))
    return None


def poly2rings(poly):
    return [poly.exterior] + [interior for interior in poly.interiors]


def clip_isolation(geometry, boundary, check_abort=None):
    paths = []
    for element in flatten_shapely_geometry(geometry):
        if check_abort is not None:
            check_abort()
        rings = poly2rings(element) if isinstance(element, Polygon) else (
            [element] if isinstance(element, LineString) else [])
        for ring in rings:
            clipped = ring.intersection(boundary)
            if clipped and not clipped.is_empty:
                paths.append(clipped)
    return paths, int(any(type(path) == MultiLineString for path in paths))


def generate_envelope(geometry, offset, invert, envelope_iso_type, isolation_geometry):
    try:
        geom = unary_union(isolation_geometry(offset, geometry=geometry, iso_type=envelope_iso_type))
    except Exception as err:
        log.error('ToolNcc.generate_envelope() --> %s' % str(err))
        return 'fail'
    if invert:
        try:
            polygons = []
            for poly in flatten_shapely_geometry(geom):
                if isinstance(poly, Polygon):
                    polygons.append(Polygon(poly.exterior.coords[::-1], poly.interiors))
                elif isinstance(poly, LinearRing):
                    polygons.append(Polygon(poly.coords[::-1]))
            geom = MultiPolygon(polygons)
        except TypeError:
            if isinstance(geom, Polygon) and geom is not None:
                geom = Polygon(geom.exterior.coords[::-1], geom.interiors)
            elif isinstance(geom, LinearRing) and geom is not None:
                geom = Polygon(geom.coords[::-1])
            else:
                log.debug(f"ToolNcc.generate_envelope() Error --> Unexpected Geometry {type(geom)}")
        except Exception as err:
            log.error(f"ToolNcc.generate_envelope() Error --> {str(err)}")
            return 'fail'
    return geom


def clear_polygon(pol, tooldia, method, overlap, connect, contour, prog_plot, circle_steps,
                  shrink, seed, lines, simplify_tol=0.0, crosshatch=None, angle=0.0):
    """Return paths plus status messages; only Geometry engine callbacks enter this kernel."""
    messages = []
    attempts = {'lines': _('Clearing the polygon with the method: lines.'),
                'seed': _('Failed. Clearing the polygon with the method: seed.'),
                'shrink': _('Failed. Clearing the polygon with the method: standard.')}
    kernels = FillKernels(shrink=shrink, seed=seed, lines=lines, crosshatch=crosshatch)

    def kernel_steps(dia, kind):
        return circle_steps

    paths = engine.clear_polygon(
        pol, tooldia,
        lambda: engine.fill_polygon(pol, tooldia, method, overlap, connect, contour, prog_plot, kernels,
                                    NCC_METHODS, kernel_steps, angle=angle,
                                    on_attempt=lambda key: messages.append(attempts[key]), tag='ToolNcc'),
        kernel_steps, 'covers', simplify_tol)
    return paths, messages


def cleanup_domain(geometry, copper=None):
    domain = unary_union(flatten_shapely_geometry(geometry))
    return domain.difference(copper) if copper is not None else domain


def tool_uid(tools, diameter, decimals):
    return next((int(uid) for uid, tool in tools.items()
                 if round(float(tool['tooldia']), decimals) == round(float(diameter), decimals)), None)


def prepare_area(geometry, boundary, iso_diameters, tools, name, decimals, has_offset, offset,
                 margin, milling_type, envelope, area_complement, check_abort, exclusion=None, circle_steps=64):
    """
    Prepare stock and isolation tool data without publishing into an output object.
    Returns (area, isolation tools, broken count, warnings). The area is 'fail' (no usable geometry) or 'empty'
    (nothing to clear: empty region, or the region is all copper / excluded).
    """
    generated = {}
    warnings = []
    broken = 0
    if boundary is not None and boundary.is_empty:
        return 'empty', generated, broken, warnings
    envelopes = []
    for diameter in iso_diameters:
        check_abort()
        isolated = envelope(diameter / 2, int(milling_type == 'cl'))
        if isinstance(isolated, str) or isolated is None or isolated.is_empty:
            warnings.append(_('Isolation geometry could not be generated.'))
            continue
        envelopes.append(isolated)
        if margin < diameter:
            warnings.append(_('Isolation geometry is broken. Margin is less than isolation tool diameter.'))
        # The isolation path is clipped by the boundary minus the cutter sweep of the excluded zones. The buffer
        # polygon inscribes the circle, so grow its radius until it contains the sweep at the given circle steps.
        path_boundary = boundary
        if exclusion is not None and not exclusion.is_empty:
            segments = quad_segs(circle_steps)
            forbidden = exclusion.buffer((diameter / 2) / math.cos(math.pi / (4 * segments)), segments)
            path_boundary = boundary.difference(forbidden)
        paths, count = clip_isolation(isolated, path_boundary, check_abort)
        broken += count
        uid = tool_uid(tools, diameter, decimals)
        if uid is not None:
            generated[uid] = copy_tool(tools[uid])
            generated[uid].pop('ncc_cleanup_start', None)
            generated[uid]['solid_geometry'] = flatten_shapely_geometry(paths)
            generated[uid]['data']['name'] = name
    if iso_diameters:
        if not envelopes:
            return 'fail', generated, broken, warnings
        # The copper to keep is the union of every isolation tool's envelope.
        isolated = unary_union(envelopes)
    else:
        isolated = geometry
    target = offset_geometry(unary_union(isolated), has_offset, offset)
    area = area_complement(target=target, boundary=boundary)
    check_abort()
    if isinstance(area, str):
        return 'fail', generated, broken, warnings
    if exclusion is not None and not exclusion.is_empty:
        area = area.difference(exclusion)
    if area.is_empty:
        return 'empty', generated, broken, warnings
    if isinstance(area, Polygon):
        area = MultiPolygon([area])
    return area, generated, broken, warnings


def _generate_normal(tool_settings, tools_storage, diameters, areas, name, decimals, clear_paths,
                     check_abort, progress, simplify_tol, prog_plot, initial_tools, tool_progress):
    """The normal modes of generate_tools through the shared engine: each tool clears its complete domain."""
    storage = copy_tools(tools_storage)
    # Geometry left in the table by an earlier run is not part of this run.
    for value in storage.values():
        value['solid_geometry'] = []
        value.pop('ncc_cleanup_start', None)
    generated = copy_tools(initial_tools)
    domains = {}
    ordered = list(diameters)

    def resolve(diameter):
        uid = tool_uid(tool_settings, diameter, decimals)
        if uid is None:
            return None
        domain = areas.get(uid, 'fail')
        if domain == 'fail' or domain is None:
            return None
        domains[uid] = domain
        data = tool_settings[uid]['data']
        angle = 0.0
        tool_method = data['tools_ncc_method']
        if tool_method == 4:
            try:
                angle = float(data.get('tools_ncc_crosshatch_angle', 0.0) or 0.0)
            except (TypeError, ValueError):
                angle = 0.0
        settings = dict(overlap=float(data['tools_ncc_overlap']) / 100, method=tool_method,
                        connect=data['tools_ncc_connect'], contour=data['tools_ncc_contour'], angle=angle)
        return engine.ToolRun(uid, domain, settings)

    def clear(pol, diameter, run):
        values = run.settings
        clear_kwargs = dict(
            pol=pol, tooldia=diameter, ncc_method=values['method'], ncc_overlap=values['overlap'],
            ncc_connect=values['connect'], ncc_contour=values['contour'], simplify_tol=simplify_tol,
            prog_plot=prog_plot)
        if values['method'] == 4:
            clear_kwargs['angle'] = values['angle']
        return clear_paths(**clear_kwargs)

    def parts(polygon):
        if polygon is None or polygon.is_empty:
            return None
        return [pol for pol in flatten_shapely_geometry(polygon.buffer(0))
                if isinstance(pol, Polygon) and pol.is_valid and not pol.is_empty]

    def store(run, diameter, paths):
        if not paths:
            return
        target_uid = tool_uid(storage, diameter, decimals)
        if target_uid is not None:
            value = storage[target_uid]
            value['solid_geometry'] = flatten_shapely_geometry(paths)
            value.pop('ncc_cleanup_start', None)
            value['data']['output_object_name'] = name
            generated[target_uid] = copy_tool(value)

    def begin(diameter):
        if tool_progress is not None:
            tool_progress(diameter)
        progress(' %d%%' % 0)

    result = engine.run_tools(
        ordered, resolve, clear, store, check_abort, begin=begin,
        polygon_done=lambda number, total: progress(' %d%%' % int(number * 100 / total)),
        explode=flatten_shapely_geometry, parts=parts)
    check_abort()
    # Only what this run generated (and the isolation tools) is published.
    return generated, domains, None, result.failed, result.last


def _generate_rest(tool_settings, tools_storage, diameters, areas, name, decimals, circle_steps, clear_paths,
                   check_abort, progress, simplify_tol, prog_plot,
                   initial_tools, tool_progress, clearing_error):
    """The rest modes of generate_tools through the shared engine: each tool clears the stock the larger ones left."""
    storage = copy_tools(tools_storage)
    # Geometry left in the table by an earlier run is not part of this run.
    for value in storage.values():
        value['solid_geometry'] = []
        value.pop('ncc_cleanup_start', None)
    generated = copy_tools(initial_tools)
    domains = {}

    def resolve(diameter):
        uid = tool_uid(tool_settings, diameter, decimals)
        if uid is None:
            return None
        domain = areas.get(uid, 'fail')
        if domain == 'fail' or domain is None:
            return None
        domains[uid] = domain
        data = tool_settings[uid]['data']
        tool_overlap = float(data['tools_ncc_overlap']) / 100
        tool_method = data['tools_ncc_method']
        angle = 0.0
        if tool_method == 4:
            try:
                angle = float(data.get('tools_ncc_crosshatch_angle', 0.0) or 0.0)
            except (TypeError, ValueError):
                angle = 0.0
        # each tool follows the Connect and Contour of its own row
        return engine.ToolRun(uid, domain, dict(
            overlap=tool_overlap, method=tool_method, connect=data.get('tools_ncc_connect', False),
            contour=data.get('tools_ncc_contour', True), angle=angle))

    def sweep(polygon, diameter, run, segments, keep_stock):
        if polygon is None or polygon.is_empty or not polygon.is_valid:
            return 'quiet', [], None
        if clearing_margin(polygon, diameter / 2, segments).is_empty:
            # A sliver that the tool cannot enter stays in the residual for a smaller tool.
            return 'quiet', [], None
        values = run.settings
        paths = []
        for pol in [polygon]:
            check_abort()
            if not isinstance(pol, Polygon) or not pol.is_valid or pol.is_empty:
                continue
            clear_kwargs = dict(
                pol=pol, tooldia=diameter, ncc_method=values['method'],
                ncc_overlap=values['overlap'], ncc_connect=values['connect'], ncc_contour=values['contour'],
                simplify_tol=simplify_tol, prog_plot=prog_plot)
            if values['method'] == 4:
                clear_kwargs['angle'] = values['angle']
            result = clear_paths(**clear_kwargs)
            check_abort()
            if result == 'fail':
                raise grace
            if result is not None:
                paths.extend(result)
        return 'ok', paths, None

    def store(run, diameter, paths):
        if not paths:
            return
        target_uid = run.uid
        if target_uid is not None:
            value = storage[target_uid]
            value['solid_geometry'] = flatten_shapely_geometry(paths)
            value.pop('ncc_cleanup_start', None)
            value['data']['output_object_name'] = name + '_' + str(diameter)
            generated[target_uid] = copy_tool(value)

    def advance(run, residual, paths, swept_parts, diameter, keep_stock, index, ordered):
        if paths:
            # the chords of the swept area stay within the accepted error: its slivers are not residual
            segments = engine.sweep_segments(diameter, clearing_error, circle_steps, quad_segs)
            swept = swept_area(flatten_shapely_geometry(paths), diameter, segments)
            residual = residual.difference(swept)
            residual = MultiPolygon([pol for pol in flatten_shapely_geometry(residual)
                                     if isinstance(pol, Polygon) and pol.is_valid and not pol.is_empty])
        if residual is not None and residual.is_empty:
            # Cleanup still needs the original domain of every selected tool.
            for pending in ordered[index + 1:]:
                pending_uid = tool_uid(tool_settings, pending, decimals)
                if pending_uid is not None and pending_uid in areas:
                    domains[pending_uid] = areas[pending_uid]
            return residual, True
        return residual, False

    def begin(diameter):
        if tool_progress is not None:
            tool_progress(diameter)
        progress(' %d%%' % 0)

    result = engine.run_rest_tools(
        diameters, resolve, sweep, store, check_abort,
        # the rest area, the swept area and the margin use the job-wide steps of the nominal radius
        lambda diameter, kind: circle_steps, clearing_error, to_segs=quad_segs, begin=begin,
        polygon_done=lambda number, total: progress(' %d%%' % int(number * 100 / total)),
        explode=flatten_shapely_geometry,
        explode_rest=lambda parts: flatten_shapely_geometry(MultiPolygon(parts)),
        initial_stock=lambda run: run.domain, advance=advance)
    check_abort()
    residual = result.stock
    # Failed means copper-free area remains that the tools could not clear (slivers under 1% of a tool ignored).
    failed = residual is None or not residual.buffer(-0.005 * (result.last or 0.0)).is_empty
    # Only what this run generated (and the isolation tools) is published.
    return generated, domains, residual, failed, result.last


def generate_tools(mode, tool_settings, tools_storage, diameters, areas, name, decimals, circle_steps,
                   clear_paths, check_abort, progress, simplify_tol=0.0, prog_plot=False, initial_tools=None, tool_progress=None,
                   clearing_error=None):
    """Generate the tools of a GUI or Tcl job from resolved data (both resolvers fill the same tool rows).

    mode 'normal': each tool clears its complete domain with the settings of its row.
    mode 'rest': each tool clears the stock the larger tools left (actual cutter sweeps), with the connect and
    contour of its own row.
    """
    if mode != 'rest':
        return _generate_normal(tool_settings, tools_storage, diameters, areas, name, decimals, clear_paths,
                                check_abort, progress, simplify_tol, prog_plot, initial_tools, tool_progress)
    return _generate_rest(tool_settings, tools_storage, diameters, areas, name, decimals, circle_steps,
                          clear_paths, check_abort, progress, simplify_tol,
                          prog_plot, initial_tools, tool_progress, clearing_error)


def finish_cleanup(tools_storage, generated, domains, name, circle_steps, cleanup_paths, clear_paths,
                   check_abort, settings=None, copper=None):
    """Select the final tool and return safe cleanup geometry, metadata and residuals."""
    tools = copy_tools(generated)
    candidates = [(uid, float(tools_storage[uid]['tooldia'])) for uid in domains
                  if tools_storage[uid]['data'].get('tools_ncc_final_cleanup', False)]
    state = {}

    def prepare(uid):
        tool = copy_tool(tools_storage[uid])
        domain = cleanup_domain(domains[uid], copper)
        if domain.is_empty:
            return None
        state['tool'] = tool
        data = dict(tool['data'], **(settings or {}))

        def generate_paths(pol, dia):
            method = data.get('tools_ncc_method', 0)
            kwargs = dict(
                pol=pol, tooldia=dia, ncc_method=method,
                ncc_overlap=float(data.get('tools_ncc_overlap', 40)) / 100,
                ncc_connect=data.get('tools_ncc_connect', False), ncc_contour=data.get('tools_ncc_contour', True),
                prog_plot=False)
            if method == 4:
                kwargs['angle'] = data.get('tools_ncc_crosshatch_angle', 0.0)
            return clear_paths(**kwargs)

        return engine.CleanupJob(domain, generate_paths)

    def store(uid, paths):
        tool = state['tool']
        standard = list(tools.get(uid, {}).get('solid_geometry', []))
        tool['solid_geometry'] = standard + paths
        tool['data']['output_object_name'] = name
        tool['ncc_cleanup_start'] = len(standard)
        tools.pop(uid, None)
        tools[uid] = tool

    residual = engine.final_cleanup(
        candidates, prepare, lambda: tools.values(),
        # the NCC sweep uses the job-wide circle steps; its residual_cleanup keeps the boundary first
        lambda domain, dia, previous, generate, boundary_first: cleanup_paths(
            domain, dia, circle_steps, previous, generate),
        store, check_abort, cleanup_rules="ncc")
    return tools, residual
