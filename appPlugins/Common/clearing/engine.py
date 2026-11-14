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
from shapely import MultiPolygon

from appPlugins.Common.clearing.methods import MethodMap
from camlib import accepted_error, circle_quad_segs, grace

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


@dataclass(frozen=True)
class LegacyTclInset:
    """The historical Tcl NCC normal mode: each tool clears the domain inset by the diameters still to come."""
    total: float
    overlap: float


@dataclass
class ToolsResult:
    last: Optional[float] = None
    failed: bool = False
    status: str = 'ok'


def run_tools(diameters, resolve: Callable, clear: Callable, store: Callable, check_abort: Callable,
              begin: Optional[Callable] = None, polygon_done: Optional[Callable] = None,
              explode: Callable = list, parts: Optional[Callable] = None, on_fail: Optional[Callable] = None,
              on_error: Optional[Callable] = None, legacy_tcl_inset: Optional[LegacyTclInset] = None):
    """
    The normal (non-rest) loop: each tool clears its complete domain.

    resolve(diameter) -> ToolRun, None (skip the tool) or FAIL (stop the whole run)
    clear(polygon, diameter, run) -> paths, None (the polygon could not be cleared) or FAIL
    store(run, diameter, paths) is called after every tool that ran
    begin(diameter) is called when a tool starts; polygon_done(number, total) after each polygon
    explode(domain) -> the polygons; parts(polygon) -> the polygons to clear, or None to skip it without progress
    on_fail(diameter) handles a FAIL of clear (default: abort with grace); on_error(err, diameter) handles an
    exception of clear (default: raise)
    legacy_tcl_inset: the Tcl NCC normal inset and morphological cleared-area estimate (temporary flag)
    """
    out = ToolsResult()
    offset_a = legacy_tcl_inset.total if legacy_tcl_inset else 0.0
    cleared = MultiPolygon()
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
        area = run.domain
        if legacy_tcl_inset:
            offset_a -= diameter - 1e-12
            try:
                area = run.domain.buffer(-offset_a).difference(cleared)
            except Exception as err:
                log.error('NCC normal area difference failed: %s' % str(err))
                continue
        polygons = explode(area)
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
        if legacy_tcl_inset and paths:
            cleared = run.domain.buffer(-offset_a * (1 + legacy_tcl_inset.overlap)).buffer(
                -diameter / 1.999999).buffer(diameter / 1.999999).buffer(0)
    return out
