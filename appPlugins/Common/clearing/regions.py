# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure region helpers shared by the NCC and Paint clearing: merge, sweep, complement, offset, margin."""

import logging

import numpy as np
import shapely
from shapely import LineString, LinearRing, MultiLineString, MultiPolygon, Polygon, STRtree, make_valid
from shapely.geometry import base
from shapely.ops import unary_union

from appCommon import clipper_ops
from camlib import flatten_shapely_geometry

log = logging.getLogger('base')


def polygon_parts(geometry):
    """Polygonal parts of any geometry; invalid parts are repaired instead of dropped."""
    parts = []
    for part in flatten_shapely_geometry(geometry):
        if not part.is_valid:
            part = make_valid(part)
        parts.extend(sub for sub in flatten_shapely_geometry(part)
                     if isinstance(sub, Polygon) and not sub.is_empty)
    return parts


def merge_overlapping(geometry):
    """Union the source polygons whose interiors overlap; disjoint polygons are returned untouched.

    Overlapping sources (DXF/SVG) would otherwise be painted twice, giving duplicate toolpaths.
    """
    items = []
    for part in flatten_shapely_geometry(geometry):
        if isinstance(part, Polygon) and not part.is_valid:
            items.extend(polygon_parts(part))
        else:
            items.append(part)
    polygons = [(index, item) for index, item in enumerate(items) if isinstance(item, Polygon)]
    if len(polygons) < 2:
        return items
    geoms = [item for _index, item in polygons]
    root = list(range(len(geoms)))

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    first, second = STRtree(geoms).query(geoms, predicate='intersects')
    for i, j in zip(first.tolist(), second.tolist()):
        if i < j and find(i) != find(j) and geoms[i].relate_pattern(geoms[j], '2********'):
            root[find(j)] = find(i)
    groups = {}
    for i in range(len(geoms)):
        groups.setdefault(find(i), []).append(i)
    if all(len(members) == 1 for members in groups.values()):
        return items
    merged, dropped = {}, set()
    for members in groups.values():
        if len(members) > 1:
            merged[polygons[members[0]][0]] = polygon_parts(unary_union([geoms[i] for i in members]))
            dropped.update(polygons[i][0] for i in members[1:])
    result = []
    for index, item in enumerate(items):
        if index in merged:
            result.extend(merged[index])
        elif index not in dropped:
            result.append(item)
    return result


def swept_area(paths, diameter, quad_segs=16):
    """The area that the cutter of this diameter sweeps along the paths."""
    paths = [path for path in paths if path is not None and not path.is_empty]
    if not paths:
        return Polygon()
    # Clipper2 offsets all open paths in one pass (2 to 5x faster than GEOS); like GEOS its round ends stay inside the
    # true circle sweep by the arc tolerance of the quad_segs. None (no pyclipr, not lines): the GEOS code below.
    parts = clipper_ops.offset_lines(paths, diameter / 2.0, clipper_ops.arc_tolerance(diameter / 2.0, quad_segs))
    if parts is not None:
        return Polygon() if not parts else parts[0] if len(parts) == 1 else MultiPolygon(parts)
    # A buffer for each path, then 1 union: the same area, about 4x faster than the buffer of the union.
    return shapely.union_all(shapely.buffer(paths, diameter / 2.0, quad_segs=quad_segs))


def buffer_geometry(geometry, offset):
    """Buffer every polygon by -offset; a neck or dumbbell that splits in two yields two polygons."""
    buffered = []
    for polygon in flatten_shapely_geometry(geometry):
        result = polygon.buffer(-offset)
        if result and not result.is_empty:
            buffered.extend(part for part in flatten_shapely_geometry(result)
                            if isinstance(part, Polygon) and not part.is_empty)
    return buffered


def apply_margin(region, reference_kind, clipping_type, margin, check_abort=None):
    if clipping_type == 0 or (clipping_type == 2 and reference_kind == 'gerber'):
        # reference_area() returns a flattened list for a Gerber reference
        return unary_union(region).buffer(distance=margin, join_style=base.JOIN_STYLE.mitre)
    if clipping_type == 1 or (clipping_type == 2 and reference_kind == 'geometry'):
        buffered = []
        for poly in region:
            if check_abort is not None:
                check_abort()
            buffered.append(poly.buffer(distance=margin, join_style=base.JOIN_STYLE.mitre))
        return unary_union(buffered)
    return None


def offset_geometry(geometry, has_offset, offset):
    if has_offset is True:
        if isinstance(geometry, list):
            geometry = MultiPolygon(geometry)
        return geometry.buffer(distance=offset)
    return geometry


def empty_faces(extent, copper):
    """The copper-free faces: the separate regions of the extent that the copper leaves empty."""
    return polygon_parts(extent.difference(unary_union(polygon_parts(copper))))


def face_at(faces, point):
    """The face that contains the point, or None."""
    click = shapely.Point(point[0], point[1])
    for face in faces:
        if face.contains(click):
            return face
    return None


def complement(target, boundary=None, check_abort=None, progress=None):
    if isinstance(target, list):
        target = MultiPolygon(target)
    if isinstance(target, (LineString, LinearRing, Polygon)):
        geo_len = 1
    elif isinstance(target, (MultiPolygon, MultiLineString)):
        geo_len = len(target.geoms)
    else:
        geo_len = len(target)
    if boundary is None:
        boundary = target.envelope
    try:
        return boundary.difference(target)
    except Exception as err:
        log.error("NCCGen.get_ncc_empty_area() --> %s" % str(err))
        try:
            old_disp_number = 0
            target_geoms = target.geoms if isinstance(target, MultiPolygon) else target
            for pol_nr, element in enumerate(target_geoms, 1):
                if check_abort is not None:
                    check_abort()
                boundary = boundary.difference(element)
                disp_number = int(np.interp(pol_nr, [0, geo_len], [0, 100]))
                if old_disp_number < disp_number <= 100:
                    if progress is not None:
                        progress(' %d%%' % disp_number)
                    old_disp_number = disp_number
            return boundary
        except Exception as err:
            log.error("NCCGen.get_ncc_empty_area() --> %s" % str(err))
            return 'fail'
