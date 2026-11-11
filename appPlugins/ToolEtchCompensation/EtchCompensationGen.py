"""Copper and aperture compensation. No Qt or application objects here."""

import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry, gerber_quad_segs, deepcopy_keep_geometry

ETCHANTS = {   # etch factor F = depth etch / lateral undercut
    'cucl2': {'label': 'CuCl2', 'factor': 3.0},
    'fecl3': {'label': 'FeCl3', 'factor': 2.5},
    'alkaline': {'label': 'Alkaline baths', 'factor': 4.0},   # the UI translates the label
}


def lateral_offset_um(thickness_um, mode, factor=None, etchant=None, offset_um=None):
    """Lateral undercut in micrometers for the given mode: 'factor', 'etch_list' or 'manual'."""
    if mode == 'manual':
        if offset_um is None:
            raise ValueError("Manual mode needs an offset.")
        return float(offset_um)
    if mode not in ('factor', 'etch_list'):
        raise ValueError("Unknown mode: %s" % mode)
    if thickness_um is None or thickness_um < 0:
        raise ValueError("The copper thickness must be zero or positive.")
    if mode == 'factor':
        if factor is None or factor <= 0:
            raise ValueError("The etch factor must be greater than zero.")
        return float(thickness_um) / factor
    if etchant not in ETCHANTS:
        raise ValueError("Unknown etchant: %s" % etchant)
    return float(thickness_um) / ETCHANTS[etchant]['factor']


def um_to_units(value_um, units):
    """Convert micrometers to application units ('MM' or 'IN')."""
    key = str(units).upper()
    if key == 'MM':
        return value_um / 1000.0
    if key == 'IN':
        return value_um / 25400.0
    raise ValueError("Unknown units: %s" % units)


def _polygons(geometry):
    """The non-empty Polygons of a list, Polygon, MultiPolygon, GeometryCollection or None."""
    if geometry is None:
        return []
    return [g for g in flatten_shapely_geometry(geometry) if isinstance(g, Polygon) and not g.is_empty]


def compensate_geometry(geometry, apertures, offset, options, units='MM'):
    """
    Return buffered copper (flat Polygon list) and copied, compensated aperture data.

    :param options: the application options; the Gerber circle preferences set the segments of the rounded corners
    :param units:   the units of the geometry and of the offset
    """
    qs = gerber_quad_segs(options, offset, units)

    polys = _polygons(geometry)
    new_solid_geometry = []
    if polys:
        buffered = unary_union(polys).buffer(offset, qs)
        new_solid_geometry = _polygons(buffered)

    new_apertures = deepcopy_keep_geometry(apertures)
    for ap in new_apertures.values():
        ap_type = ap.get('type')
        if ap_type in ('R', 'O'):
            for k in ('width', 'height'):
                if k in ap:
                    ap[k] = max(0.0, ap[k] + 2 * offset)
            if 'width' in ap and 'height' in ap:
                ap['size'] = math.hypot(ap['width'], ap['height'])
        elif ap_type in ('C', 'P') and 'size' in ap:
            ap['size'] = max(0.0, ap['size'] + 2 * offset)

        records = ap.get('geometry')
        if not records:
            continue
        kept = []
        for rec in records:
            if 'solid' in rec:
                rec['solid'] = rec['solid'].buffer(offset, qs)
                if rec['solid'].is_empty:
                    continue
            if 'clear' in rec:
                rec['clear'] = rec['clear'].buffer(-offset, qs)
                if rec['clear'].is_empty:
                    # every record keeps 'follow', which the Gerber writer exports as copper: no clear, no record
                    continue
            kept.append(rec)
        ap['geometry'] = kept

    return new_solid_geometry, new_apertures


def compensate_gerber(geometry, apertures, thickness_um, mode, units, options,
                      factor=None, etchant=None, offset_um=None):
    """Compute the offset for the mode and compensate the copper and apertures with it."""
    off_um = lateral_offset_um(thickness_um, mode, factor=factor, etchant=etchant, offset_um=offset_um)
    off = um_to_units(off_um, units)
    solid, aps = compensate_geometry(geometry, apertures, off, options, units)
    return {'offset_um': off_um, 'offset': off, 'solid_geometry': solid, 'apertures': aps}
