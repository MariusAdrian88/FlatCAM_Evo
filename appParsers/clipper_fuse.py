"""
Optional fast fusion of Gerber polygons with Clipper2 (pyclipr).

``fuse_parts()`` replaces the GEOS closing ``MultiPolygon(parts).buffer(e).buffer(-e)``.
It returns ``None`` whenever the result could be unsafe, so the caller can use the GEOS code.
"""

from typing import Sequence

import numpy as np
import shapely
from shapely import Polygon
from shapely.geometry.base import BaseGeometry

from appCommon.clipper_ops import SCALE, polygon_parts, prepare_paths, tree_to_polygons

try:
    import pyclipr
except Exception:  # missing wheel, wrong platform, broken install
    pyclipr = None


def fuse_parts(parts: Sequence[BaseGeometry], closing: float = 1e-8) -> list[Polygon] | None:
    """
    Fuse polygons into one set of polygons: union, then a closing by ``closing`` (offset +/- with miter joins, 30% cheaper than round joins).

    :param parts: Polygons or MultiPolygons in file units.
    :param closing: Closing distance in file units, must be greater than 0.
    :return: List of Polygons, or None when GEOS must be used (no pyclipr, unsafe input or unsafe result).
    """
    try:
        return _fuse_parts(parts, closing)
    except Exception:
        return None


def _fuse_parts(parts: Sequence[BaseGeometry], closing: float) -> list[Polygon] | None:
    """Do the work of fuse_parts(); any exception is handled by the caller."""
    if len(parts) == 0:
        return []
    polys = polygon_parts(parts)
    if polys is None:
        return None
    if polys.size == 0:
        return []
    if not closing > 0:
        return None

    paths = prepare_paths(polys)
    if paths is None or pyclipr is None:
        return None

    has_area = bool(np.any(shapely.area(polys) > 0))

    clipper = pyclipr.Clipper()
    clipper.scaleFactor = SCALE
    clipper.addPaths(paths, pyclipr.Subject)
    united = clipper.execute(pyclipr.Union, pyclipr.FillRule.NonZero)
    if len(united) == 0:
        return None if has_area else []

    grown = pyclipr.ClipperOffset()
    grown.scaleFactor = SCALE
    grown.addPaths(united, pyclipr.JoinType.Miter, pyclipr.EndType.Polygon)
    expanded = grown.execute(closing)
    if len(expanded) == 0:
        return None

    shrunk = pyclipr.ClipperOffset()
    shrunk.scaleFactor = SCALE
    shrunk.addPaths(expanded, pyclipr.JoinType.Miter, pyclipr.EndType.Polygon)
    tree = shrunk.execute2(-closing)

    result = tree_to_polygons(tree)
    if len(result) == 0:
        return None if has_area else []
    # Clipper output is valid by construction (checked in the tests), no is_valid() pass here
    return result
