"""Geometry computation for the Invert Gerber plugin. No Qt here."""

import math

from shapely import box, make_valid
from shapely.affinity import scale as _scale
from shapely.ops import polygonize, unary_union

from camlib import gerber_quad_segs
from appPlugins.ToolSub.SubGen import _flat, _obj_units, object_area, units_factor

# value of the join style radio -> Shapely join style
JOIN_STYLES = {'r': 'round', 's': 'mitre', 'b': 'bevel'}
REFERENCE_MODES = ('box', 'exact')


class InvertError(ValueError):
    """The inversion has no area to work in (empty source, no bounds, empty reference)."""


def join_style_name(value):
    """Shapely join style of a radio value ('r' / 's' / 'b') or of a Shapely name; unknown values give 'mitre'."""
    if value in JOIN_STYLES.values():
        return value
    return JOIN_STYLES.get(value, 'mitre')


def _check_abort(abort):
    if abort is not None:
        abort()


def copper_union(geometry):
    """Union of the polygons of geometry (any nesting); invalid polygons are repaired first."""
    polys = []
    for geo in _flat(geometry):
        if not geo.is_valid:
            geo = make_valid(geo)
        polys.extend(g for g in _flat(geo) if g.geom_type == 'Polygon')
    return unary_union(polys) if polys else None


def inversion_area(bounds, margin, options, join_style, area=None, units='MM'):
    """
    The area that the inverted copper fills: the reference area, or the box of bounds, grown by margin.

    :param options: the application options; the Gerber circle preferences set the segments of the rounded corners
    :param units:   the units of the margin
    """
    if area is None:
        if bounds is None or len(bounds) != 4 or not all(math.isfinite(v) for v in bounds) or \
                bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            raise InvertError("no bounds")
        area = box(*bounds)
    if margin > 0:
        area = area.buffer(margin, quad_segs=gerber_quad_segs(options, margin, units), join_style=join_style_name(join_style))
    if area is None or area.is_empty or area.area <= 0:
        raise InvertError("empty area")
    return area


def invert_geometry(geometry, bounds, margin, options, join_style, area=None, abort=None, units='MM'):
    """
    Subtract the source copper from its buffered bounding box (or from the given reference area).

    :return:    list of Polygons; empty when the copper covers the whole area
    """
    region = inversion_area(bounds, margin, options, join_style, area, units)
    _check_abort(abort)
    copper = copper_union(geometry)
    _check_abort(abort)
    result = region if copper is None else region.difference(copper)
    return [g for g in _flat(result) if g.geom_type == 'Polygon' and g.area > 0]


def reference_area(obj, mode, circle_steps, target_units=None):
    """
    The area of a reference object in target_units: its bounding box ('box') or its exact shape ('exact').
    Closed lines (a board outline drawn as lines) give the polygons that they enclose.
    """
    factor = units_factor(target_units, _obj_units(obj)) if target_units else 1.0
    shape = object_area(obj, steps=circle_steps)
    if obj.kind == 'geometry':
        parts = []
        for tool in (getattr(obj, 'tools', None) or {}).values():
            parts.extend(_flat(tool.get('solid_geometry')))
    else:
        parts = _flat(getattr(obj, 'solid_geometry', None))
    if shape.is_empty:
        lines = [g for g in parts if g.geom_type in ('LineString', 'LinearRing')]
        shape = unary_union(list(polygonize(unary_union(lines)))) if lines else shape
    if mode == 'box':
        geoms = [shape] if not shape.is_empty else parts
        shape = box(*unary_union(geoms).bounds) if geoms else shape
    if shape.is_empty:
        raise InvertError("empty reference")
    if factor != 1.0:
        shape = _scale(shape, factor, factor, origin=(0, 0))
    return shape


def apertures_from_polygons(polygons):
    """Gerber tools of the result: one region (aperture 0, 'REG') per polygon."""
    return {0: {'type': 'REG', 'size': 0.0,
                'geometry': [{'solid': poly, 'follow': poly.exterior} for poly in polygons]}}
