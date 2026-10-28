"""Alignment drill geometry independent of the plugin and its widgets."""

from shapely import Point
from shapely.affinity import scale
from copy import deepcopy
import math
from concurrent.futures import ThreadPoolExecutor

from appParsers.ParseExcellon import Excellon
from appPlugins.ToolAlignObjects.AlignGen import AlignError, bounded_align, is_ambiguous, invert

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


GENERATED = '_2sided_generated'


def alignment_matrices(indices, gerbers, drill, parallel=True):
    """Solve every move without touching objects. First Gerber is the fixed reference.

    Use the shared matcher's geometric support and ambiguity rules. Coverage is informative: mounting holes
    needn't have copper pads, just as SMD pads needn't have holes.
    """
    if len(indices) < 2 or not gerbers:
        return {}

    def solve(src, dst):
        results = bounded_align(indices[src], indices[dst], allow_mirror=True)
        if is_ambiguous(results):
            raise AlignError('Alignment is ambiguous. Use Align Objects to inspect candidates.')
        return results[0].matrix

    matrices = {}
    if drill is not None:
        threaded = parallel and len(gerbers) > 1
        if threaded:
            # These are original immutable snapshots; never nest jobs in the shared Qt worker pool.
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(solve, drill, target) for target in gerbers]
                solved = [future.result() for future in futures]
        else:
            solved = [solve(drill, target) for target in gerbers]
        matrices[drill] = solved[0]
        for target, matrix in zip(gerbers, solved):
            if target != gerbers[0]:
                matrices[target] = compose(matrices[drill], invert(matrix))
    else:
        for other in gerbers[1:]:
            matrices[other] = solve(other, gerbers[0])
    return matrices


def compose(left, right):
    """Affine composition: apply right, then left."""
    a, b, d, e, x, y = left
    A, B, D, E, X, Y = right
    return (a*A + b*D, a*B + b*E, d*A + e*D, d*B + e*E,
            a*X + b*Y + x, d*X + e*Y + y)


def valid_diameter(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Drill diameter must be positive and finite.')
    return value


def validate_holes(holes):
    if not isinstance(holes, (list, tuple)) or not holes:
        raise ValueError('Enter drill coordinates as (x, y), (x, y), ...')
    for hole in holes:
        if not isinstance(hole, (list, tuple)) or len(hole) != 2 or \
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in hole):
            raise ValueError('Each drill needs two finite coordinates (x, y).')
    return holes


def valid_drill_offset(diameter, offset):
    diameter = valid_diameter(diameter)
    if not math.isfinite(offset) or offset < 0:
        raise ValueError(_('Offset must be finite and non-negative.'))
    if offset < diameter / 2:
        raise ValueError(_('The offset must be at least half the drill diameter, else a hole cuts the board.'))
    return diameter


def bounds_drills(bounds, diameter, offset, turn='L-R', failsafe=True):
    """Alignment holes for a board that is turned over left/right ('L-R') or top/bottom ('T-B').

    A and B sit on the center line of the turn, one on each side of the sources. The failsafe hole C sits on the
    other axis, beyond the sources, on the mirror line of the turn. Only the turn in the Type direction maps this
    isosceles triangle onto itself, so the board cannot be fitted on the pins any other way. A near-equilateral
    triangle would also fit other placements: then A and B move away from C (never C outward, the bed is small)
    until the legs differ from the base by 2 drill diameters. Without failsafe, only A and B are made.
    """
    if turn not in ('L-R', 'T-B'):
        raise ValueError(_('Unknown turn direction.'))
    xmin, ymin, xmax, ymax = bounds
    if not all(math.isfinite(v) for v in bounds) or xmin > xmax or ymin > ymax:
        raise ValueError(_('Source objects have no finite bounds.'))
    diameter = valid_drill_offset(diameter, offset)
    if turn == 'T-B':
        # Same layout with x and y exchanged.
        return [Point(p.y, p.x) for p in bounds_drills((ymin, xmin, ymax, xmax), diameter, offset, 'L-R', failsafe)]
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    width = xmax - xmin
    base = width + 2 * offset
    margin = 2 * diameter
    half = width / 2 + offset
    y_ab = cy
    if failsafe and abs(math.hypot(half, ymax + offset - y_ab) - base) < margin:
        y_ab = ymax + offset - math.sqrt((base + margin) ** 2 - half ** 2)
        if y_ab < ymin:
            raise ValueError(_('There is no room for keyed alignment holes on this board. '
                               'Use a smaller drill diameter or a larger offset.'))
    points = [Point(xmin - offset, y_ab), Point(xmax + offset, y_ab)]
    return points + [Point(cx, ymax + offset)] if failsafe else points


def _remove_generated(tools):
    for tool in tools.values():
        indices = set(tool.pop(GENERATED, []))
        tool['drills'] = [p for i, p in enumerate(tool.get('drills', [])) if i not in indices]


def has_generated(obj):
    """True for an Excellon object that holds drills this plugin generated and therefore owns."""
    return obj.kind == 'excellon' and any(t.get(GENERATED) for t in obj.tools.values())


def unowned_features(obj):
    """The drills and slots of an object that this plugin did not generate."""
    count = 0
    for tool in obj.tools.values():
        generated = set(tool.get(GENERATED, ()))
        count += sum(1 for index in range(len(tool.get('drills') or ())) if index not in generated)
        count += len(tool.get('slots') or ())
    return count


def source_view(obj):
    """A drill-only working copy without our holes; never edit the live source for a search/bounds read."""
    if not has_generated(obj):
        return obj
    view = Excellon(obj.app)
    view.kind = 'excellon'
    view.tools = deepcopy(obj.tools)
    _remove_generated(view.tools)
    view.create_geometry()
    return view


def combined_bounds(objects):
    bounds = [source_view(obj).bounds() for obj in objects]
    if not bounds or any(not all(math.isfinite(v) for v in b) for b in bounds):
        raise ValueError('Select source objects with non-empty, finite geometry.')
    return min(b[0] for b in bounds), min(b[1] for b in bounds), \
        max(b[2] for b in bounds), max(b[3] for b in bounds)


def _refresh_source(obj):
    xmin, ymin, xmax, ymax = obj.bounds()
    obj.obj_options.update({'xmin': xmin, 'ymin': ymin, 'xmax': xmax, 'ymax': ymax})
    if obj.regenerate_source() is False:
        raise ValueError('Could not regenerate the Excellon source.')


def replace_generated(obj, diameter, drills, decimals):
    """Ownership is by list position, not coordinates. Rigid transforms preserve positions, including duplicates.

    The marker lives in the serialized tools dict, so project save and history snapshots keep it with the drills.
    """
    diameter = valid_diameter(diameter)
    _remove_generated(obj.tools)
    tool_id = next((key for key, tool in obj.tools.items()
                    if round(tool['tooldia'], decimals) == round(diameter, decimals)), None)
    if tool_id is None:
        tool_id = obj.add_tool(diameter)
    tool = obj.tools[tool_id]
    start = len(tool['drills'])
    tool['drills'].extend(drills)
    tool[GENERATED] = list(range(start, len(tool['drills'])))
    # Empty tools left by changing our diameter needn't leak into the export.
    obj.tools = {key: t for key, t in obj.tools.items() if t.get('drills') or t.get('slots')}
    if obj.create_geometry() == 'fail':
        raise ValueError('Could not create alignment drill geometry.')
    _refresh_source(obj)


def retire_generated(obj):
    """Drop our holes from an object we generated into before; every other feature stays."""
    if not has_generated(obj):
        return False
    _remove_generated(obj.tools)
    obj.tools = {key: t for key, t in obj.tools.items() if t.get('drills') or t.get('slots')}
    if obj.create_geometry() == 'fail':
        raise ValueError('Could not create alignment drill geometry.')
    if not obj.tools:
        # An empty Excellon has no export; regenerate_source() would keep the stale text.
        obj.source_file = ''
    else:
        _refresh_source(obj)
    return True


def alignment_drills(holes, align_type, origin):
    drills = []
    if align_type in ["X", "Y"]:
        xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[align_type]
        for hole in holes:
            point = Point(hole)
            drills += [point, scale(point, xscale, yscale, origin=origin)]
    elif align_type == "manual":
        drills = [Point(hole) for hole in holes]
    return drills


def drills_fit_after_flip(drills, axis, origin, tol):
    """True if every drill has a drill at its mirror image about the axis ('X' or 'Y') through origin."""
    xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[axis]
    return all(any(scale(drill, xscale, yscale, origin=origin).distance(other) <= tol for other in drills)
               for drill in drills)
