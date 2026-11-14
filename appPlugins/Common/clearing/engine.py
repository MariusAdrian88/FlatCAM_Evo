# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Shared clearing engine of the NCC and Paint tools (normal, non-rest path).

Pure: no Qt, no app. The fill kernels come in through FillKernels, the circle steps through callables and the
tool-specific parts of the loop (tool lookup, polygon splitting, output storage) through hooks.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import shapely

from shapely.ops import unary_union

from appPlugins.Common.clearing.methods import MethodMap
from appPlugins.Common.clearing.regions import polygon_parts, swept_area
from camlib import accepted_error, circle_quad_segs, clearing_margin, error_quad_segs, grace, rest_area

log = logging.getLogger('base')

FAIL = 'fail'


def quad_segs(circle_steps):
    """Shapely quadrant segments for an app circle step count (steps count the whole circle)."""
    return max(1, int(circle_steps) // 4)


class FillKernels:
    """
    The fill kernels of the clearing: the adapter over the camlib.Geometry methods of the bound parent tool.
    Each kernel is looked up when it is called, so a caller may supply only the kernels of the selected method.
    """

    def __init__(self, shrink=None, seed=None, lines=None, crosshatch=None, fill_lines=None):
        self.shrink = shrink
        self.seed = seed
        self.lines = lines
        self.crosshatch = crosshatch
        self.fill_lines = fill_lines

    @classmethod
    def from_tool(cls, parent_tool):
        def lazy(name):
            return lambda *args, **kwargs: getattr(parent_tool, name)(*args, **kwargs)

        return cls(shrink=lazy('clear_polygon_shrink'), seed=lazy('clear_polygon_seed'),
                   lines=lazy('clear_polygon_lines'),
                   crosshatch=lazy('clear_polygon_crosshatch') if hasattr(parent_tool, 'clear_polygon_crosshatch')
                   else None,
                   fill_lines=getattr(parent_tool, 'fill_with_lines', None))


def fill_polygon(pol, tooldia, method, overlap, connect, contour, prog_plot, kernels, methods: MethodMap,
                 kernel_steps, angle=0.0, on_attempt=None, laser=None, apertures=None, index=None, tag='clearing'):
    """
    Fill one polygon with the kernel of the method. Returns the kernel storage, None (nothing was cleared) or
    FAIL (the job was aborted). on_attempt(key) is called before each kernel of the combo, with the key 'lines',
    'seed' or 'shrink'. laser is the laser-lines function (Paint), called with the aperture context.
    """
    key = methods.kernel(method) if method in methods else None
    steps = kernel_steps(tooldia, None)
    result = None
    if key == 'laser_lines':
        if laser is None:
            return None
        result = laser(pol, tooldia, overlap, connect, contour, prog_plot, steps,
                       kernel_steps(tooldia, 'geometry'), apertures, kernels.seed, kernels.lines,
                       kernels.fill_lines, index)
        if result == FAIL:
            return result
    elif key == 'crosshatch':
        if kernels.crosshatch is None:
            return None
        try:
            # Simplify only afterwards, where each chord's cutter sweep is checked against the original polygon.
            result = kernels.crosshatch(pol, tooldia, steps_per_circle=steps, overlap=overlap, contour=contour,
                                        connect=connect, prog_plot=prog_plot, simplify_tol=0.0, angle=angle)
        except grace:
            return FAIL
        except Exception as err:
            log.error('%s Crosshatch --> %s' % (tag, str(err)))
            result = None
    elif key is not None:
        fills = {'shrink': kernels.shrink, 'seed': kernels.seed, 'lines': kernels.lines}
        sequence = ('lines', 'seed', 'shrink') if key == 'combo' else (key,)
        for name in sequence:
            if key == 'combo' and on_attempt is not None:
                on_attempt(name)
            try:
                result = fills[name](pol, tooldia, steps_per_circle=steps, overlap=overlap,
                                     contour=contour, connect=connect, prog_plot=prog_plot)
            except grace:
                return FAIL
            except Exception as err:
                log.error('%s %s --> %s' % (tag, name, str(err)))
                result = None
                continue
            if result and result.objects:
                break
    return result if result and result.objects else None


def simplified_inside(paths, simplify, tolerance, polygon, diameter, edge_error=None, quad_segs=16):
    """
    The simplified paths; a simplified path that comes closer to the outline of the polygon than the cutter radius
    minus the edge error (or that leaves the polygon) is replaced by its original path. The simplification moves a
    path by up to the tolerance: around a hole of the polygon (copper) the cutter would cut into the copper.

    :param edge_error:  the cut past the outline that the clearing allows (the Clearing Error of the Preferences),
                        in the units of the geometry; None allows the error of the circle steps of the paths
    :param quad_segs:   the Shapely segments of a quarter circle of the paths (their circle steps)
    """
    paths = list(paths)
    if tolerance <= 0.0 or not paths:
        return simplify(paths, tolerance)
    simple = simplify(paths, tolerance)
    if len(simple) != len(paths):
        return simple
    radius = diameter / 2.0
    edge_error = accepted_error(edge_error, radius, quad_segs)
    # The distance of a path to the outline is measured on its segments: no tessellation of the cutter.
    shapely.prepare(polygon)
    boundary = polygon.boundary
    shapely.prepare(boundary)
    inside = shapely.covers(polygon, simple) & ~shapely.dwithin(boundary, simple, max(radius - edge_error, 0.0))
    return [new if keep else old for old, new, keep in zip(paths, simple, inside.tolist())]


def covered_simplification(paths, tolerance, polygon, tooldia, steps):
    """The paths simplified by the tolerance; a simplified chord that lets the cutter leave the polygon is dropped."""
    out = []
    if tolerance > 0.0:
        shapely.prepare(polygon)
    for path in paths:
        simplified = path.simplify(tolerance) if tolerance > 0.0 else path
        # A simplified chord must not let the cutter cross copper.
        if tolerance > 0.0 and not polygon.covers(simplified.buffer(tooldia / 2, quad_segs(steps))):
            simplified = path
        out.append(simplified)
    return out


def clear_polygon(pol, tooldia, fill, kernel_steps, simplify_rule="covers", tolerance=0.0, simplify=None,
                  edge_error=None):
    """
    The paths that clear one polygon: fill() gives the kernel storage (None or FAIL when nothing was cleared), the
    paths are then simplified by the rule.

    "covers": the cutter swept along a simplified path must stay inside the polygon (NCC), checked at the kernel
              steps of the tool.
    "dwithin": the simplified path must keep the cutter radius minus the edge error from the outline (Paint);
               simplify(paths, tolerance) simplifies the whole list.

    Returns the list of paths, None or FAIL.
    """
    raw = fill()
    if isinstance(raw, str):
        return raw
    if not raw:
        return None
    paths = raw.get_objects()
    if simplify_rule == "dwithin":
        return simplified_inside(paths, simplify, tolerance, pol, tooldia, edge_error,
                                 circle_quad_segs(kernel_steps(tooldia, None)))
    return covered_simplification(paths, tolerance, pol, tooldia, kernel_steps(tooldia, None))


@dataclass(frozen=True)
class ToolRun:
    """One tool of the loop: the uid, the domain to clear (a geometry, or a list of polygons) and its settings."""
    uid: int
    domain: object
    settings: object = None
    polygons: object = None     # rest path: the polygons of the first tool, when the domain is not split by explode


@dataclass
class ToolsResult:
    last: Optional[float] = None
    failed: bool = False
    status: str = 'ok'
    stock: object = None        # rest path: the stock left after the last tool


def run_tools(diameters, resolve: Callable, clear: Callable, store: Callable, check_abort: Callable,
              begin: Optional[Callable] = None, polygon_done: Optional[Callable] = None,
              explode: Callable = list, parts: Optional[Callable] = None, on_fail: Optional[Callable] = None,
              on_error: Optional[Callable] = None):
    """
    The normal (non-rest) loop: each tool clears its complete domain.

    resolve(diameter) -> ToolRun, None (skip the tool) or FAIL (stop the whole run)
    clear(polygon, diameter, run) -> paths, None (the polygon could not be cleared) or FAIL
    store(run, diameter, paths) is called after every tool that ran
    begin(diameter) is called when a tool starts; polygon_done(number, total) after each polygon
    explode(domain) -> the polygons; parts(polygon) -> the polygons to clear, or None to skip it without progress
    on_fail(diameter) handles a FAIL of clear (default: abort with grace); on_error(err, diameter) handles an
    exception of clear (default: raise)
    """
    out = ToolsResult()
    for diameter in diameters:
        check_abort()
        out.last = diameter
        if begin is not None:
            begin(diameter)
        run = resolve(diameter)
        if isinstance(run, str):
            out.status = FAIL
            return out
        if run is None:
            continue
        polygons = explode(run.domain)
        paths = []
        for number, polygon in enumerate(polygons, 1):
            check_abort()
            todo = parts(polygon) if parts is not None else [polygon]
            if todo is None:
                continue
            for part in todo:
                check_abort()
                try:
                    result = clear(part, diameter, run)
                except grace:
                    raise
                except Exception as err:
                    if on_error is None:
                        raise
                    on_error(err, diameter)
                    continue
                check_abort()
                if isinstance(result, str) and result == FAIL:
                    if on_fail is None:
                        raise grace
                    on_fail(diameter)
                elif result is not None:
                    paths.extend(result)
                else:
                    out.failed = True
            if polygon_done is not None:
                polygon_done(number, len(polygons))
        check_abort()
        store(run, diameter, paths)
    return out


def sweep_segments(diameter, edge_error, steps, to_segs=circle_quad_segs):
    """
    The quarter-circle segments of the swept area and of the rest area: their chords stay within the accepted error
    (the Clearing Error, else the error of the circle steps), so the slivers of the sweep are not stock.
    to_segs maps the circle steps to segments (NCC: steps // 4, Paint: rounded up).
    """
    return error_quad_segs(diameter / 2.0, edge_error) if edge_error else to_segs(steps)


def rest_sweep(polygon, diameter, settings, process_polygon, simplify, simplification, keep_stock, edge_error=None,
               quad_segs=16):
    """Paint one rest polygon: (status, elements, swept); status is ok, skipped or failed."""
    left, bottom, right, top = polygon.bounds
    if right - left < diameter or top - bottom < diameter:
        return 'skipped', [], None
    check_buff = clearing_margin(polygon, diameter / 2.0, quad_segs)
    if not check_buff or check_buff.is_empty:
        return 'skipped', [], None
    result = process_polygon(polygon, diameter, settings)
    if result == 'fail':
        # Not swept, so the polygon stays in the stock handed to the next tool.
        return 'failed', [], None
    elements = simplified_inside(result.get_objects(), simplify, simplification, polygon, diameter,
                                 edge_error, quad_segs) if result else []
    swept = (swept_area(elements, diameter, sweep_segments(diameter, edge_error, None, lambda __: quad_segs))
             if keep_stock else None)
    return 'ok', elements, swept


def run_rest_tools(diameters, resolve: Callable, sweep: Callable, store: Callable, check_abort: Callable,
                   sweep_steps: Callable, edge_error=None, to_segs: Callable = circle_quad_segs,
                   begin: Optional[Callable] = None, polygon_done: Optional[Callable] = None,
                   explode: Callable = list, explode_rest: Callable = polygon_parts,
                   initial_stock: Optional[Callable] = None, advance: Optional[Callable] = None,
                   on_fail: Optional[Callable] = None, on_error: Optional[Callable] = None):
    """
    The rest loop: the tools run from the largest diameter to the smallest and each one clears what the earlier ones
    left (the stock), reached through the area they cleared already.

    resolve(diameter) -> ToolRun, None (skip the tool) or FAIL (stop the whole run)
    sweep(polygon, diameter, run, segments, keep_stock) -> (status, elements, swept); status ok, failed, skipped or
        quiet (skipped and not counted in the progress); segments: the margin segments of the sweep steps
    store(run, diameter, cleared) is called after every tool that ran
    sweep_steps(diameter, kind) -> circle steps of the rest area and the swept area (NCC: a job-wide constant)
    to_segs(steps) -> quarter-circle segments; edge_error: the Clearing Error, None for the error of the steps
    explode(domain) splits the first domain; explode_rest(rest area) splits the later ones
    initial_stock(run) -> the stock of the first tool (default: its domain)
    advance(run, stock, cleared, swept_parts, diameter, keep_stock, index, ordered) -> (stock, stop); the default is
        the Paint rule: subtract the swept parts, stop after the last tool or when nothing is left
    on_fail(diameter) after a polygon that failed; on_error(err, diameter) after an exception of sweep (default: raise)
    """
    out = ToolsResult()
    ordered = sorted(diameters, reverse=True)
    stock = None
    for index, diameter in enumerate(ordered):
        has_next_tool = index + 1 < len(ordered)
        check_abort()
        out.last = diameter
        if begin is not None:
            begin(diameter)
        run = resolve(diameter)
        if isinstance(run, str):
            out.status = FAIL
            return out
        if run is None:
            continue
        steps = sweep_steps(diameter, 'sweep')
        if stock is None:
            stock = initial_stock(run) if initial_stock is not None else run.domain
            polygons = run.polygons if run.polygons is not None else explode(stock)
        else:
            # The tool crosses the area that is cut already to reach the stock.
            polygons = explode_rest(rest_area(
                run.domain, stock, diameter, sweep_segments(diameter, edge_error, steps, to_segs), edge_error))
        cleared, swept_parts = [], []
        for number, polygon in enumerate(polygons, 1):
            check_abort()
            try:
                status, elements, swept = sweep(polygon, diameter, run, to_segs(steps), has_next_tool)
            except grace:
                raise
            except Exception as err:
                if on_error is None:
                    raise
                on_error(err, diameter)
                status, elements, swept = 'error', [], None
            if status == 'quiet':
                continue
            if status == 'ok':
                cleared += elements
                if swept is not None and not swept.is_empty:
                    swept_parts.append(swept)
            elif status == 'failed' and on_fail is not None:
                on_fail(diameter)
            if polygon_done is not None:
                polygon_done(number, len(polygons))
        check_abort()
        store(run, diameter, cleared)
        if advance is not None:
            stock, stop = advance(run, stock, cleared, swept_parts, diameter, has_next_tool, index, ordered)
        else:
            # Stock for the next tool: what the swept area of this tool does not cover of the stock, the polygons
            # that were not painted (too narrow, failed) included.
            stop = not has_next_tool
            if not stop:
                if swept_parts:
                    stock = stock.difference(unary_union(swept_parts))
                stock = unary_union(polygon_parts(stock))
                stop = stock.is_empty
        out.stock = stock
        if stop:
            break
    return out


@dataclass(frozen=True)
class CleanupJob:
    """The cleanup of one tool: the domain, the polygon clearing and whether the tool connects its paths."""
    domain: object
    generate: Callable
    connect: bool = False


def final_cleanup(candidates, prepare: Callable, previous: Callable, cleanup: Callable, store: Callable,
                  check_abort: Callable):
    """
    The final cleanup of the residual stock with one of the eligible tools.

    candidates: [(uid, tool diameter)] of the tools that have the cleanup on
    prepare(uid) -> CleanupJob, or None when the tool has nothing to clean
    previous() -> the tools already cut (dicts with solid_geometry and tooldia)
    cleanup(domain, diameter, previous, generate, boundary_first) -> (paths, residual): camlib.residual_cleanup with
        its steps and physical=True bound
    store(uid, paths) is called when the cleanup made paths
    A tool with diameter <= 0 or without a domain is skipped and the next smallest is tried; boundary_first =
    not connect.
    Returns the residual of the cleanup, None when no cleanup ran.
    """
    ordered = [candidate for candidate in sorted(candidates, key=lambda candidate: candidate[1]) if candidate[1] > 0]
    if not ordered:
        return None
    check_abort()
    for uid, diameter in ordered:
        job = prepare(uid)
        if job is None:
            continue
        boundary_first = not job.connect
        paths, residual = cleanup(job.domain, diameter, previous(), job.generate, boundary_first)
        check_abort()
        if paths:
            store(uid, paths)
        return residual
    return None
