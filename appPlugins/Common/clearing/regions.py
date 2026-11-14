# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/14/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure region helpers shared by the NCC and Paint clearing: merge, sweep, complement, offset, margin."""

import logging
import math

import numpy as np
import shapely
from shapely import LineString, LinearRing, MultiLineString, MultiPolygon, Point, Polygon, STRtree, make_valid
from shapely.geometry import base
from shapely.ops import substring, unary_union

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


def physical_domain(polygons, exclusion):
    """
    The physical domain of a clearing with an exclusion: the selection (already shrunk or grown by the offset) minus
    the ORIGINAL exclusion. The offset comes first, so a negative offset cannot grow back into the exclusion. The
    kernels and the cleanup inset this domain by the cutter radius themselves: no second clearance is added.
    """
    if exclusion is None or exclusion.is_empty:
        return list(polygons)
    parts = []
    for polygon in polygons:
        parts.extend(polygon_parts(polygon.difference(exclusion)))
    return parts


# a path nearer than radius - STRICT_TOLERANCE to the original exclusion fails the strict check (float noise only)
STRICT_TOLERANCE = 1e-9


def cutter_expansion(exclusion, radius, quad_segs=16):
    """
    The exclusion grown by the cutter radius, on the safe side. A Shapely round buffer has its arc vertices on the
    true circle, so its chords lie inside it and a center line can be outside the buffer and still nearer than the
    radius. This region is built from the edges (flat rectangles, exact) and from a polygon around each vertex whose
    inscribed circle has the radius (the vertices lie at radius / cos(pi / (4 * quad_segs)), the correction of the
    NCC isolation clip). A point outside it is never nearer to the exclusion than the radius minus half the strict
    tolerance; a straight offset edge at exactly the radius is outside it.
    """
    inner = radius - STRICT_TOLERANCE / 2
    reach = inner / math.cos(math.pi / (4 * quad_segs))
    parts = [exclusion]
    for polygon in polygon_parts(exclusion):
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = np.asarray(ring.coords)
            edges = shapely.linestrings(np.stack([coords[:-1], coords[1:]], axis=1))
            parts.extend(shapely.buffer(edges, inner, quad_segs=quad_segs, cap_style='flat').tolist())
            parts.extend(shapely.buffer(shapely.points(coords[:-1]), reach, quad_segs=quad_segs).tolist())
    return shapely.union_all(parts)


def allowed_centers(domain, exclusion, radius, quad_segs=16):
    """The allowed cutter centers: the physical domain minus the conservative expansion of the exclusion."""
    return domain.difference(cutter_expansion(exclusion, radius, quad_segs))


def too_close(paths, exclusion, radius):
    """For each path: True if its true segment distance to the exclusion is below the radius (strict check)."""
    if not paths:
        return []
    shapely.prepare(exclusion)
    return shapely.dwithin(np.array(paths, dtype=object), exclusion, radius - STRICT_TOLERANCE).tolist()


def _expansion_rings(expansion):
    rings = []
    for polygon in polygon_parts(expansion):
        rings.extend([polygon.exterior, *polygon.interiors])
    return rings


def _along_ring(rings, end, start, limit):
    """The coordinates along the expansion boundary from end to start (the shorter way), None if not possible."""
    for ring in rings:
        if ring.distance(end) > 1e-7 or ring.distance(start) > 1e-7:
            continue
        line = LineString(ring.coords)
        total = line.length
        a, b = line.project(end), line.project(start)
        forward = (b - a) % total
        if min(forward, total - forward) > limit:
            return None
        if forward <= total - forward:
            if a <= b:
                return list(substring(line, a, b).coords)
            return list(substring(line, a, total).coords) + list(substring(line, 0.0, b).coords)[1:]
        if b <= a:
            return list(substring(line, a, b).coords)
        return list(substring(line, a, 0.0).coords) + list(substring(line, total, b).coords)[1:]
    return None


def _pieces_in_order(path, cut):
    """The pieces of the clipped path in path order; the 2 pieces of a closed path at its start are joined."""
    pieces = [piece for piece in flatten_shapely_geometry(cut) if isinstance(piece, LineString) and not piece.is_empty]
    pieces.sort(key=lambda piece: path.project(piece.interpolate(0.5, normalized=True)))
    if len(pieces) > 1 and path.is_closed and pieces[-1].coords[-1] == pieces[0].coords[0]:
        pieces[0] = LineString(list(pieces.pop().coords) + list(pieces[0].coords)[1:])
    return pieces


def _bridge_inside(coords, physical, radius, tolerance):
    """True if a bridge keeps the cutter inside the physical domain: it is covered and nearer than r - tolerance to no edge."""
    if physical is None:
        return True
    bridge = LineString(coords)
    return bool(shapely.covers(physical, bridge)) and not shapely.dwithin(
        physical.boundary, bridge, radius - max(tolerance, STRICT_TOLERANCE))


def clip_clear(paths, exclusion, radius, expansion, domain=None, min_length=0.0, physical=None, tolerance=0.0):
    """
    Keep the cutter out of the exclusion with center lines.

    Without a domain (the strict boundary) only the paths that fail the true distance check are changed: the part
    inside the expansion is cut out and the path is joined again along the boundary of the expansion where that is a
    short detour, so the stock around a convex corner of the zone is still cleared. A bridge is only used where the
    cutter stays inside the physical domain (the selection with the offset, before the zone was subtracted): its
    distance to the edge of that domain is at least r - tolerance (the normal edge error); else the pieces stay apart.
    A piece keeps the order of the coordinates of its path: the cut direction survives. A piece that still fails the
    check is dropped.

    With a domain (laser lines) every path is clipped to the allowed cutter centers: the domain minus the expansion.

    :return: (paths, failed): the new list in the old order, and the number of pieces dropped by the strict check
    """
    paths = list(paths)
    near = too_close(paths, exclusion, radius) if domain is None else [True] * len(paths)
    allowed = None if domain is None else domain.difference(expansion)
    rings = _expansion_rings(expansion) if domain is None and any(near) else []
    out, failed = [], 0
    for path, hit in zip(paths, near):
        if not hit or not isinstance(path, LineString):
            out.append(path)
            continue
        if allowed is not None:
            pieces = [piece for piece in flatten_shapely_geometry(path.intersection(allowed))
                      if isinstance(piece, LineString) and not piece.is_empty]
        else:
            pieces = _pieces_in_order(path, path.difference(expansion))
            joined = pieces[:1]
            for piece in pieces[1:]:
                bridge = _along_ring(rings, Point(joined[-1].coords[-1]), Point(piece.coords[0]), 6 * radius)
                if bridge is None or not _bridge_inside(bridge, physical, radius, tolerance):
                    joined.append(piece)
                else:
                    joined[-1] = LineString(list(joined[-1].coords) + bridge[1:] + list(piece.coords)[1:])
            if path.is_closed and pieces:
                # a closed path is cyclic: the end of the last piece goes to the start of the first one
                bridge = _along_ring(rings, Point(joined[-1].coords[-1]), Point(joined[0].coords[0]), 6 * radius)
                if bridge is not None and _bridge_inside(bridge, physical, radius, tolerance):
                    closing = list(joined[-1].coords) + bridge[1:]
                    if len(joined) == 1:
                        joined[0] = LineString(closing)
                    else:
                        joined = [LineString(closing + list(joined[0].coords)[1:])] + joined[1:-1]
            pieces = joined
        pieces = [piece for piece in pieces if piece.length > min_length]
        bad = too_close(pieces, exclusion, radius)
        failed += sum(bad)
        out.extend(piece for piece, wrong in zip(pieces, bad) if not wrong)
    return out, failed


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
