"""Extract plugin core: drills, soldermask and cutout from Gerber data. Qt-free, no app import."""

import math
import os
from dataclasses import dataclass, field

from shapely import GeometryCollection, LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from appHandlers.appJoinConvert import classify_hole, is_closed

KINDS = ('circular', 'oblong', 'square', 'rectangular', 'other')
_SCALAR_TYPES = (int, float, str, bool, type(None))

# result codes (ExtractResult.error)
ERR_NO_APERTURES = 'no_apertures'
ERR_NO_PADS = 'no_pads'
ERR_BAD_DIAMETER = 'bad_diameter'
ERR_BAD_THICKNESS = 'bad_thickness'
ERR_NO_GEOMETRY = 'no_geometry'
ERR_EMPTY_SHAPE = 'empty_shape'
ERR_NO_CIRCLES = 'no_circles'

# skip reasons (ExtractResult.skipped)
SKIP_NON_POSITIVE = 'non_positive'
SKIP_UNKNOWN_SIZE = 'unknown_size'
SKIP_DUPLICATE = 'duplicate'
SKIP_EMPTY = 'empty'


@dataclass
class ExtractResult:
    """Outcome of one extraction; `error` is '' on success, else a code."""
    kind: str
    tools: dict = field(default_factory=dict)
    solid_geometry: list = field(default_factory=list)
    follow_geometry: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    error: str = ''


def _skip(skipped, reason, count=1):
    skipped[reason] = skipped.get(reason, 0) + count


def _flat(geometry):
    """Yield the non-empty shapely geometries of a geometry or a nested list of them."""
    if isinstance(geometry, BaseGeometry):
        if not geometry.is_empty:
            yield geometry
    elif isinstance(geometry, (list, tuple)):
        for item in geometry:
            yield from _flat(item)


def is_square(width, height, decimals):
    """The one square rule: sides equal after rounding."""
    return round(float(width), decimals) == round(float(height), decimals)


def aperture_kind(aperture, decimals):
    """Kind name of an aperture: circular, oblong, square, rectangular, other or region."""
    ap_type = aperture.get('type')
    if ap_type == 'REG':
        return 'region'
    if ap_type == 'C':
        return 'circular'
    if ap_type == 'O':
        return 'oblong'
    if ap_type == 'R':
        try:
            return 'square' if is_square(aperture['width'], aperture['height'], decimals) else 'rectangular'
        except (KeyError, TypeError, ValueError):
            return 'rectangular'
    return 'other'


def pad_size(aperture):
    """Characteristic pad size, or None when it is unknown."""
    try:
        ap_type = aperture.get('type')
        if ap_type in ('O', 'R'):
            return min(float(aperture['width']), float(aperture['height']))
        if ap_type in ('C', 'P'):
            return float(aperture['size'])
        if ap_type == 'AM':
            sizes = []
            for geo_el in aperture.get('geometry') or []:
                solid = geo_el.get('solid')
                if isinstance(solid, BaseGeometry) and not solid.is_empty and not geo_el.get('clear'):
                    sizes.append(solid)
            if not sizes:
                return None
            x0 = min(s.bounds[0] for s in sizes)
            y0 = min(s.bounds[1] for s in sizes)
            x1 = max(s.bounds[2] for s in sizes)
            y1 = max(s.bounds[3] for s in sizes)
            return min(x1 - x0, y1 - y0)
        return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def dark_flashes(aperture):
    """Geometry elements of an aperture that are dark flashes (Point follow, solid, no clear)."""
    out = []
    for geo_el in aperture.get('geometry') or []:
        if (isinstance(geo_el, dict) and isinstance(geo_el.get('follow'), Point) and geo_el.get('solid') is not None
                and not geo_el.get('clear')):
            out.append(geo_el)
    return out


def _selected(gerber_tools, codes, decimals, kinds):
    """Yield (code, aperture, kind) of the apertures that pass the code and kind filters (no regions)."""
    for code, aperture in (gerber_tools or {}).items():
        if codes is not None and code not in codes:
            continue
        if not isinstance(aperture, dict):
            continue
        kind = aperture_kind(aperture, decimals)
        if kind in kinds:
            yield code, aperture, kind


def count_pads(gerber_tools, codes, decimals, kinds):
    """Number of dark flashes the drills would use."""
    kinds = set(kinds)
    return sum(len(dark_flashes(ap)) for _, ap, _ in _selected(gerber_tools, codes, decimals, kinds))


def out_name(source_name, suffix):
    """Output object name: the last extension of the source is dropped."""
    base = source_name
    if os.path.splitext(source_name)[1]:
        base = os.path.splitext(source_name)[0]
    return f"{base}_{suffix}"


def extract_drills(sources, mode, kinds, decimals, fixed_dia=0.0, rings=None, factor=1.0, oblong_slots=False):
    """Drill/slot tools from Gerber flashes; sources = [(gerber_tools, codes or None)]."""
    res = ExtractResult('excellon')
    kinds = set(kinds)
    rings = rings or {}
    if mode == 'fixed' and not fixed_dia > 0:
        res.error = ERR_BAD_DIAMETER
        return res

    found_aperture = False
    entries = {}        # rounded (x, y) -> [dia, start, stop, drill_point]
    for gerber_tools, codes in sources:
        for _, aperture, kind in _selected(gerber_tools, codes, decimals, kinds):
            found_aperture = True
            flashes = dark_flashes(aperture)
            if not flashes:
                continue
            size = pad_size(aperture)
            if mode == 'fixed':
                dia = float(fixed_dia)
            elif size is None:
                _skip(res.skipped, SKIP_UNKNOWN_SIZE, len(flashes))
                continue
            elif mode == 'ring':
                dia = size - 2 * rings.get(kind, 0.0)
            else:
                dia = size * factor
            dia = round(dia, decimals)
            if dia <= 0:
                _skip(res.skipped, SKIP_NON_POSITIVE, len(flashes))
                continue
            half = 0.0
            if oblong_slots and aperture.get('type') == 'O':
                width, height = float(aperture['width']), float(aperture['height'])
                if round(max(width, height), decimals) > round(min(width, height), decimals):
                    half = abs(width - height) / 2.0
                    along_x = width > height
            for geo_el in flashes:
                point = geo_el['follow']
                key = (round(point.x, decimals), round(point.y, decimals))
                old = entries.get(key)
                if old is not None:
                    _skip(res.skipped, SKIP_DUPLICATE)
                    if old[0] >= dia:
                        continue
                if half:
                    if along_x:
                        start, stop = Point(point.x - half, point.y), Point(point.x + half, point.y)
                    else:
                        start, stop = Point(point.x, point.y - half), Point(point.x, point.y + half)
                    entries[key] = [dia, start, stop, None]
                else:
                    entries[key] = [dia, None, None, point]

    if not entries:
        res.error = ERR_NO_PADS if found_aperture else ERR_NO_APERTURES
        return res
    by_dia = {}
    for dia, start, stop, point in entries.values():
        tool = by_dia.setdefault(dia, {'tooldia': dia, 'drills': [], 'slots': []})
        if point is not None:
            tool['drills'].append(point)
        else:
            tool['slots'].append((start, stop))
    res.tools = {num: by_dia[dia] for num, dia in enumerate(sorted(by_dia), 1)}
    return res


def geometry_circles(shapes):
    """[(center Point, diameter)] of the circular shapes: a closed path or the exterior of a polygon."""
    circles = []
    for shape in shapes:
        if isinstance(shape, Polygon):
            ring = shape.exterior
        elif isinstance(shape, LineString) and is_closed(shape):
            ring = shape
        else:
            continue
        if len(ring.coords) < 4:
            continue
        hole = classify_hole(Polygon(ring))
        if hole is not None and hole[0] == 'drill':
            circles.append((hole[1], hole[2]))
    return circles


def extract_drills_geometry(shapes, mode, decimals, fixed_dia=0.0, ring=0.0, factor=1.0):
    """
    Drill tools from the circular shapes of a Geometry object. The measured diameter of a circle is the pad:
    fixed -> fixed_dia, ring -> diameter - 2 * ring, prop -> diameter * factor.
    """
    res = ExtractResult('excellon')
    if mode == 'fixed' and not fixed_dia > 0:
        res.error = ERR_BAD_DIAMETER
        return res

    circles = geometry_circles(shapes)
    if not circles:
        res.error = ERR_NO_CIRCLES
        return res

    entries = {}        # rounded (x, y) -> [dia, drill_point]
    for center, measured in circles:
        if mode == 'fixed':
            dia = float(fixed_dia)
        elif mode == 'ring':
            dia = measured - 2 * ring
        else:
            dia = measured * factor
        dia = round(dia, decimals)
        if dia <= 0:
            _skip(res.skipped, SKIP_NON_POSITIVE)
            continue
        key = (round(center.x, decimals), round(center.y, decimals))
        old = entries.get(key)
        if old is not None:
            _skip(res.skipped, SKIP_DUPLICATE)
            if old[0] >= dia:
                continue
        entries[key] = [dia, Point(center.x, center.y)]

    if not entries:
        res.error = ERR_NO_PADS
        return res
    by_dia = {}
    for dia, point in entries.values():
        by_dia.setdefault(dia, {'tooldia': dia, 'drills': [], 'slots': []})['drills'].append(point)
    res.tools = {num: by_dia[dia] for num, dia in enumerate(sorted(by_dia), 1)}
    return res


def _grown(aperture, clearance):
    """New aperture dict with the scalar keys of the source, sizes grown by 2*clearance."""
    new = {k: v for k, v in aperture.items() if isinstance(v, _SCALAR_TYPES)}
    if clearance:
        for key in ('size', 'width', 'height'):
            val = new.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                new[key] = val + 2 * clearance
    return new


def _region_follow(solid):
    if isinstance(solid, Polygon):
        return LineString(solid.exterior.coords)
    return solid.boundary


def extract_soldermask(sources, kinds, clearance, decimals, include_regions=False):
    """Soldermask apertures: dark flashes (and optionally regions) grown by clearance."""
    res = ExtractResult('gerber')
    kinds = set(kinds)
    next_code = 10
    for gerber_tools, codes in sources:
        for code, aperture in (gerber_tools or {}).items():
            if codes is not None and code not in codes:
                continue
            if not isinstance(aperture, dict):
                continue
            kind = aperture_kind(aperture, decimals)
            if kind == 'region':
                if not include_regions:
                    continue
                elements = [e for e in (aperture.get('geometry') or [])
                            if isinstance(e, dict) and e.get('solid') is not None and not e.get('clear')]
            elif kind in kinds:
                elements = dark_flashes(aperture)
            else:
                continue
            records = []
            for geo_el in elements:
                solid = geo_el['solid']
                if clearance:
                    solid = solid.buffer(clearance)
                if solid.is_empty:
                    _skip(res.skipped, SKIP_EMPTY)
                    continue
                follow = geo_el.get('follow')
                if follow is None:
                    follow = _region_follow(geo_el['solid'])
                records.append({'solid': solid, 'follow': follow})
                res.solid_geometry.append(solid)
                res.follow_geometry.append(follow)
            if not records:
                continue
            if kind == 'region':
                out_code = 0
            else:
                out_code = next_code
                next_code += 1
            target = res.tools.get(out_code)
            if target is None:
                target = _grown(aperture, clearance)
                target['geometry'] = []
                res.tools[out_code] = target
            target['geometry'].extend(records)
    if not res.tools:
        res.error = ERR_NO_PADS
    return res


def extract_cutout(geometry, margin, thickness, shape='rect'):
    """Cutout outline around the geometry: its bounding box ('rect') or its convex hull ('convex'), grown by margin."""
    res = ExtractResult('gerber')
    if not thickness > 0:
        res.error = ERR_BAD_THICKNESS
        return res
    parts = list(_flat(geometry))
    if not parts:
        res.error = ERR_NO_GEOMETRY
        return res
    if shape == 'convex':
        base = GeometryCollection(parts).convex_hull
    else:
        all_bounds = [p.bounds for p in parts]
        xs0, ys0, xs1, ys1 = zip(*all_bounds)
        vals = (min(xs0), min(ys0), max(xs1), max(ys1))
        if not all(math.isfinite(v) for v in vals):
            res.error = ERR_NO_GEOMETRY
            return res
        base = box(*vals)
    poly = base.buffer(margin, join_style='mitre')
    if poly.is_empty or not isinstance(poly, Polygon):
        res.error = ERR_EMPTY_SHAPE
        return res
    follow = LineString(poly.exterior.coords)
    solid = follow.buffer(thickness / 2.0)
    res.tools = {10: {'type': 'C', 'size': thickness, 'geometry': [{'solid': solid, 'follow': follow}]}}
    res.solid_geometry = [solid]
    res.follow_geometry = [follow]
    return res


def apply_result(obj, res):
    """Fill a new Gerber object from a non-excellon ExtractResult."""
    obj.multitool = False
    obj.multigeo = False
    obj.follow = False
    obj.tools = res.tools
    obj.solid_geometry = res.solid_geometry
    obj.follow_geometry = res.follow_geometry
