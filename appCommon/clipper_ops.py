"""
Optional fast polygon operations with Clipper2 (pyclipr).

Every function returns ``None`` when Clipper2 cannot be used (no pyclipr, unsafe input, error). The caller then runs
its Shapely / GEOS code. Results are lists of valid Shapely Polygons in the units of the input.

Clipper2 works with integers: coordinates are multiplied by SCALE and rounded. Distances are in input units; the arc
tolerance (the largest chord error of a round join or end) is in input units too and is scaled here.
"""

import math
from typing import Iterable, Sequence

import numpy as np
import shapely
from shapely import Polygon
from shapely.geometry.base import BaseGeometry

try:
    import pyclipr
except Exception:  # missing wheel, wrong platform, broken install
    pyclipr = None

SCALE: int = 10 ** 9

# Largest scaled coordinate that is safe for the 64-bit integer arithmetic of Clipper2.
_MAX_SCALED: float = 1e14

# Below this many polygons a boolean operation of GEOS is as fast as Clipper2 (measured in the editors, 2026-11-03)
MIN_SHAPES: int = 64

_POLYGON_ID = 3
_MULTIPOLYGON_ID = 6
_LINE_IDS = (1, 2, 5)  # LineString, LinearRing, MultiLineString

_JOINS = {'round': 'Round', 'miter': 'Miter', 'square': 'Square'}
_OPS = {'union': 'Union', 'difference': 'Difference', 'intersection': 'Intersection', 'xor': 'Xor'}


def available() -> bool:
    """True if pyclipr is installed and usable."""
    return pyclipr is not None


def arc_tolerance(radius: float, quad_segs: int) -> float:
    """The chord error of a circle of this radius drawn with quad_segs segments per quarter (Shapely convention)."""
    return abs(radius) * (1.0 - math.cos(math.pi / (4 * max(1, int(quad_segs)))))


# ---------------------------------------------------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------------------------------------------------

def _object_array(geoms) -> np.ndarray:
    if isinstance(geoms, BaseGeometry):
        geoms = [geoms]
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = list(geoms)
    return arr


def polygon_parts(geoms) -> np.ndarray | None:
    """
    The non-empty Polygons of the geometries, as an object array.

    :return: None if a geometry is not a valid Polygon / MultiPolygon.
    """
    arr = _object_array(geoms)
    if arr.size == 0:
        return arr
    type_ids = shapely.get_type_id(arr)
    if not np.all((type_ids == _POLYGON_ID) | (type_ids == _MULTIPOLYGON_ID)):
        return None
    if not shapely.is_valid(arr).all():
        return None
    polys = shapely.get_parts(arr)
    return polys[~shapely.is_empty(polys)]


def prepare_paths(polys: np.ndarray) -> list[np.ndarray] | None:
    """
    Make the Clipper paths of the polygons: exterior CCW, holes CW, closing point removed.

    :param polys: Object array of non-empty, valid Polygons.
    :return: List of (n, 2) arrays, or None if a coordinate is unsafe or a ring collapses at scale SCALE.
    """
    if len(polys) == 0:
        return []
    rings, poly_idx = shapely.get_rings(polys, return_index=True)
    coords, ring_idx = shapely.get_coordinates(rings, return_index=True)

    if not _safe_coords(coords):
        return None

    counts = np.bincount(ring_idx, minlength=len(rings))
    ends = np.cumsum(counts)
    starts = ends - counts

    # the first ring of each polygon is the exterior, the others are holes
    is_exterior = np.ones(len(rings), dtype=bool)
    is_exterior[1:] = poly_idx[1:] != poly_idx[:-1]
    flip = shapely.is_ccw(rings) != is_exterior

    if _has_collapsed_ring(coords, counts, starts, ends):
        return None

    paths = []
    for s, e, f in zip(starts.tolist(), (ends - 1).tolist(), flip.tolist()):
        path = coords[s:e]
        paths.append(np.ascontiguousarray(path[::-1]) if f else path)
    return paths


def line_paths(geoms) -> list[np.ndarray] | None:
    """
    The open Clipper paths of LineStrings, LinearRings and MultiLineStrings (a ring keeps its closing point).

    :return: None if a geometry is not a line or a coordinate is unsafe.
    """
    arr = _object_array(geoms)
    if arr.size == 0:
        return []
    type_ids = shapely.get_type_id(arr)
    if not np.all(np.isin(type_ids, _LINE_IDS)):
        return None
    lines = shapely.get_parts(arr)
    lines = lines[~shapely.is_empty(lines)]
    coords, idx = shapely.get_coordinates(lines, return_index=True)
    if not _safe_coords(coords):
        return None
    counts = np.bincount(idx, minlength=len(lines))
    ends = np.cumsum(counts)
    return [coords[s:e] for s, e in zip((ends - counts).tolist(), ends.tolist()) if e - s >= 1]


def _safe_coords(coords: np.ndarray) -> bool:
    if not np.isfinite(coords).all():
        return False
    return not (coords.size and np.abs(coords).max() * SCALE > _MAX_SCALED)


def _quantize(coords: np.ndarray) -> np.ndarray:
    """Round scaled coordinates like pyclipr does: half away from zero."""
    scaled = coords * SCALE
    return np.copysign(np.floor(np.abs(scaled) + 0.5), scaled)


def _has_collapsed_ring(coords: np.ndarray, counts: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> bool:
    """
    Check if a ring has zero area after rounding to the integer grid of Clipper.

    The area is computed in float64 on coordinates relative to the first point of each ring. A ring whose area is
    not clearly above the float rounding error is checked again with Python integers (exact).
    """
    q = _quantize(coords)
    rel = q - np.repeat(q[starts], counts, axis=0)

    x = rel[:, 0]
    y = rel[:, 1]
    x_next = np.roll(x, -1)
    y_next = np.roll(y, -1)
    cross = x * y_next - x_next * y
    magnitude = np.abs(x * y_next) + np.abs(x_next * y)
    # the pair (last point of a ring, first point of the next ring) is not an edge
    cross[ends - 1] = 0.0
    magnitude[ends - 1] = 0.0

    area2 = np.add.reduceat(cross, starts)
    bound = np.add.reduceat(magnitude, starts)
    uncertain = np.nonzero(np.abs(area2) <= 1e-6 * bound)[0]

    for k in uncertain.tolist():
        ring = q[starts[k]:ends[k]].astype(np.int64).tolist()
        x0, y0 = ring[0]
        total = 0
        for (xa, ya), (xb, yb) in zip(ring[:-1], ring[1:]):
            total += (xa - x0) * (yb - y0) - (xb - x0) * (ya - y0)
        if total == 0:
            return True
    return False


def tree_to_polygons(tree) -> list[Polygon]:
    """Convert a Clipper PolyTree into Shapely Polygons: holes are the children, islands in holes are new polygons."""
    result = []
    stack = list(tree.children)
    while stack:
        node = stack.pop()
        holes = []
        for hole in node.children:
            holes.append(hole.polygon)
            stack.extend(hole.children)
        result.append(Polygon(node.polygon, holes))
    return result


# ---------------------------------------------------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------------------------------------------------

class PolygonOffsetter:
    """
    Offset the same polygons by many distances: the conversion to Clipper paths is done once.

    ``ok`` is False when Clipper2 cannot be used for these polygons; ``offset()`` then returns None.
    """

    def __init__(self, geoms, join: str = 'round', simplify: float = 0.0):
        self.ok = False
        self._paths = None
        self._join = None
        if pyclipr is None or join not in _JOINS:
            return
        try:
            polys = polygon_parts(geoms)
            if polys is None:
                return
            paths = prepare_paths(polys)
            if paths is None:
                return
            if simplify > 0 and paths:
                paths = [np.asarray(p) for p in pyclipr.simplifyPaths(paths, simplify * SCALE, scaleFactor=SCALE)]
                paths = [p for p in paths if len(p) >= 3]
            self._paths = paths
            self._join = getattr(pyclipr.JoinType, _JOINS[join])
            self.ok = True
        except Exception:
            self.ok = False

    def offset(self, distance: float, tolerance: float) -> list[Polygon] | None:
        """
        :param distance: Offset distance in input units; negative shrinks.
        :param tolerance: Largest chord error of the round joins, in input units (> 0).
        :return: Polygons (an empty list if nothing is left), or None on error.
        """
        if not self.ok:
            return None
        if not self._paths:
            return []
        try:
            worker = pyclipr.ClipperOffset()
            worker.scaleFactor = SCALE
            worker.arcTolerance = max(float(tolerance), 1.0 / SCALE) * SCALE
            worker.addPaths(self._paths, self._join, pyclipr.EndType.Polygon)
            return tree_to_polygons(worker.execute2(float(distance)))
        except Exception:
            return None


def offset_polygons(geoms, distance: float, tolerance: float, join: str = 'round') -> list[Polygon] | None:
    """One offset of polygons. See PolygonOffsetter.offset()."""
    return PolygonOffsetter(geoms, join=join).offset(distance, tolerance)


def offset_lines(geoms, radius: float, tolerance: float, end: str = 'round') -> list[Polygon] | None:
    """
    The union of the buffers of lines (open paths) by radius: the swept area of a round tool on its paths.

    :param geoms: LineStrings / LinearRings / MultiLineStrings.
    :param radius: Buffer radius in input units (> 0).
    :param tolerance: Largest chord error of the round ends and joins, in input units.
    :param end: 'round' or 'square' ends.
    """
    if pyclipr is None or not radius > 0:
        return None
    try:
        paths = line_paths(geoms)
        if paths is None:
            return None
        if not paths:
            return []
        worker = pyclipr.ClipperOffset()
        worker.scaleFactor = SCALE
        worker.arcTolerance = max(float(tolerance), 1.0 / SCALE) * SCALE
        end_type = pyclipr.EndType.Round if end == 'round' else pyclipr.EndType.Square
        join_type = pyclipr.JoinType.Round if end == 'round' else pyclipr.JoinType.Miter
        worker.addPaths(paths, join_type, end_type)
        return tree_to_polygons(worker.execute2(float(radius)))
    except Exception:
        return None


def boolean(subject, clip, op: str) -> list[Polygon] | None:
    """
    Boolean operation of polygons: 'union', 'difference', 'intersection' or 'xor' (subject OP clip).

    :param subject: Polygons / MultiPolygons (one geometry or a sequence).
    :param clip: Polygons / MultiPolygons, or None / empty for a union of the subject only.
    """
    if pyclipr is None or op not in _OPS:
        return None
    try:
        subj = polygon_parts(subject)
        clp = polygon_parts(clip if clip is not None else [])
        if subj is None or clp is None:
            return None
        subj_paths = prepare_paths(subj)
        clip_paths = prepare_paths(clp)
        if subj_paths is None or clip_paths is None:
            return None
        if not subj_paths and op != 'union' and op != 'xor':
            return []
        worker = pyclipr.Clipper()
        worker.scaleFactor = SCALE
        if subj_paths:
            worker.addPaths(subj_paths, pyclipr.Subject)
        if clip_paths:
            worker.addPaths(clip_paths, pyclipr.Clip)
        tree = worker.execute2(getattr(pyclipr, _OPS[op]), pyclipr.FillRule.NonZero)
        return tree_to_polygons(tree)
    except Exception:
        return None


def boolean_geometry(subject, clip, op: str, min_shapes: int = MIN_SHAPES):
    """
    Boolean operation of many polygons (the editor operations on a whole board), as one Shapely geometry.

    :param subject: list of Polygons / MultiPolygons
    :param clip: list of Polygons / MultiPolygons (may be empty)
    :param min_shapes: below this many polygons GEOS is as fast (the conversion costs as much as it saves)
    :return: a Polygon or MultiPolygon, or None when the caller must use its GEOS code (few shapes, no pyclipr,
        unsafe input, empty or invalid result: a Clipper difference can return a ring that touches itself)
    """
    if len(subject) + len(clip) < min_shapes:
        return None
    polygons = boolean(subject, clip, op)
    if not polygons or not all(polygon.is_valid for polygon in polygons):
        return None
    return polygons[0] if len(polygons) == 1 else shapely.MultiPolygon(polygons)


def union(geoms: Iterable[BaseGeometry] | Sequence[BaseGeometry]) -> list[Polygon] | None:
    """Union of polygons. See boolean()."""
    return boolean(list(geoms) if not isinstance(geoms, BaseGeometry) else geoms, None, 'union')
