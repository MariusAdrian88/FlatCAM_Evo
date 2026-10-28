"""Geometry preparation and distance calculations for Find Optimal. No Qt."""

from shapely import MultiPolygon

from camlib import flatten_shapely_geometry, polygon_gap_pairs


def prepare_geometry(solids):
    # a solid can be a MultiPolygon (e.g. a shape split by a negative Etch Compensation): MultiPolygon() refuses a
    # list that holds one, so the parts go in one by one
    return flatten_shapely_geometry(MultiPolygon(flatten_shapely_geometry(solids)).buffer(0))


def find_distances(polygons, decimals):
    # Each polygon has at least its nearest neighbor; minimum and frequency are exact.
    return polygon_gap_pairs(polygons, decimals, neighbors=True)
