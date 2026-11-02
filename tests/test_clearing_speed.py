"""Run: python tests/test_clearing_speed.py

The seed clearing makes its rings in bands and cuts them from the margin without the holes and polygons that the
rings of a band cannot touch; paint_connect() joins the paths as NumPy coordinate arrays. Both must give the paths of
the ring-by-ring cut and of the joining with coordinate tuples, bit for bit.
"""
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    import numpy as np
    import shapely
    from shapely import LineString, MultiPolygon, Point, Polygon, box
    from shapely.ops import unary_union
finally:
    sys.argv = _saved_argv

from test_clearing_paths import make_geometry
from test_paint_geometry import storage_of

PAD_CENTERS = [(x, y) for x in range(3, 29, 3) for y in range(3, 20, 3)]
# (getattr and create=True: on the code before the bands the seed tests run too, and show its ring-by-ring cut)
SEED_BAND_MIN_COORDS = getattr(camlib.Geometry, 'SEED_BAND_MIN_COORDS', 2000)
SEED_BAND_RINGS = getattr(camlib.Geometry, 'SEED_BAND_RINGS', 16)


def band_limit(min_coords):
    """Sets Geometry.SEED_BAND_MIN_COORDS, the number of margin coordinates from which the margin is cut in bands."""
    return patch.object(camlib.Geometry, 'SEED_BAND_MIN_COORDS', min_coords, create=True)


def board(islands=False):
    """
    A board area of 30 x 21 mm with 54 round holes (the copper pads). With islands: the pads are rings and the area
    inside each ring is a separate polygon.
    """
    area = Polygon(box(0, 0, 30, 21).exterior.coords, [Point(c).buffer(0.7, quad_segs=16).exterior.coords
                                                       for c in PAD_CENTERS])
    if islands:
        area = MultiPolygon([area] + [Point(c).buffer(0.35, quad_segs=16) for c in PAD_CENTERS])
    return area


def margin_of(polygon, tooldia, steps=64, simplify_tol=0.0):
    margin = polygon.buffer(-tooldia / 2, quad_segs=camlib.circle_quad_segs(steps))
    return MultiPolygon(camlib.flatten_shapely_geometry(margin, simplify_tolerance=simplify_tol))


def seed_rings_one_by_one(polygon, tooldia, steps, overlap=0.15, simplify_tol=0.0):
    """
    The rings of clear_polygon_seed() cut from the whole margin one at a time (the code before the bands).

    :return:    the paths and the number of rings cut
    """
    quad_segs = camlib.circle_quad_segs(steps)
    radius = tooldia / 2 * (1 - overlap)
    margin = margin_of(polygon, tooldia, steps, simplify_tol)
    seedpoint = margin.representative_point()
    # the rings end past the farthest vertex of the margin, not at the first ring that misses it
    farthest = max(Point(c).distance(seedpoint) for c in shapely.get_coordinates(margin))
    inner_ratio = math.cos(math.pi / (4 * quad_segs))
    paths = []
    rings = 0
    while radius * inner_ratio - simplify_tol - tooldia <= farthest:
        rings += 1
        path = Point(seedpoint).buffer(radius, quad_segs=quad_segs).exterior
        path = path.simplify(simplify_tol)
        path = path.intersection(margin)
        if not path.is_empty:
            paths += camlib.flatten_shapely_geometry(path, simplify_tolerance=simplify_tol)
        radius += tooldia * (1 - overlap)
    return paths, rings


def connect_with_tuples(storage, boundary, tooldia, max_walk=None):
    """paint_connect() with the joined path kept as a list of coordinate tuples (the code before the arrays)."""
    max_walk = max_walk or 10 * tooldia
    result = []
    try:
        pt, geo = storage.nearest((0, 0))
    except StopIteration:
        return result
    storage.remove(geo)
    coords = list(geo.coords)
    boundary_edge = boundary.boundary
    try:
        while True:
            pt, candidate = storage.nearest(coords[-1])
            storage.remove(candidate)
            candidate_coords = list(candidate.coords)
            if pt != candidate_coords[0] and pt == candidate_coords[-1]:
                candidate_coords.reverse()
            walk = LineString([coords[-1], pt]) if coords[-1] != pt else Point(pt)
            if (walk.length < max_walk and boundary.contains(walk)
                    and not shapely.dwithin(walk, boundary_edge, tooldia / 2 - 1e-9)):
                coords.extend(candidate_coords)
            else:
                result.append(LineString(coords))
                coords = candidate_coords
    except StopIteration:
        result.append(LineString(coords))
    return result


class TestSeedBands(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()

    def seed_paths(self, polygon, tooldia, steps, simplify_tol=0.0):
        return list(self.geometry.clear_polygon_seed(
            polygon, tooldia, steps, connect=False, contour=False, simplify_tol=simplify_tol).get_objects())

    def test_banded_rings_are_the_rings_cut_one_by_one(self):
        for islands in (False, True):
            polygon = board(islands)
            for steps, simplify_tol in ((8, 0.0), (64, 0.01)):
                expected, __ = seed_rings_one_by_one(polygon, 0.4, steps, simplify_tol=simplify_tol)
                # the margin cut whole (fewer coordinates than SEED_BAND_MIN_COORDS) and cut in bands
                for min_coords in (SEED_BAND_MIN_COORDS, 0):
                    with self.subTest(islands=islands, steps=steps, simplify_tol=simplify_tol,
                                      min_coords=min_coords), band_limit(min_coords):
                        paths = self.seed_paths(polygon, 0.4, steps, simplify_tol)
                        # the rings come first; the rings around the gaps of the margin follow them
                        self.assertEqual([p.wkb for p in paths[:len(expected)]], [p.wkb for p in expected])

    def test_rings_go_on_past_a_ring_that_misses_the_margin(self):
        # 2 areas 3 mm apart, and 2 pockets joined by a neck narrower than the tool: a ring in the gap misses the
        # margin, the later rings clear the other part.
        for polygon in (MultiPolygon([box(0, 0, 4, 4), box(7, 0, 11, 4)]).buffer(0),
                        unary_union([box(0, 0, 4, 4), box(6, 0, 10, 4), box(3.9, 1.8, 6.1, 2.2)])):
            for min_coords in (SEED_BAND_MIN_COORDS, 0):
                with self.subTest(parts=len(polygon.geoms) if hasattr(polygon, 'geoms') else 1,
                                  min_coords=min_coords), band_limit(min_coords):
                    paths = list(self.geometry.clear_polygon_seed(polygon, 1.0, 64, connect=False).get_objects())
                    reach = polygon.buffer(-0.5, quad_segs=32).buffer(0.5, quad_segs=32)
                    swept = unary_union([p.buffer(0.5, quad_segs=32) for p in paths])
                    self.assertLess(reach.difference(swept).area, 1e-4)

    def test_a_large_margin_is_cut_without_the_holes_out_of_the_band(self):
        polygon = board()
        tooldia = 0.4
        margin_coords = shapely.get_num_coordinates(margin_of(polygon, tooldia))
        original = shapely.intersection
        cuts = []

        def recorded(a, b, *args, **kwargs):
            cuts.append(shapely.get_num_coordinates(b))
            return original(a, b, *args, **kwargs)

        with band_limit(0), patch.object(shapely, 'intersection', recorded):
            paths = self.seed_paths(polygon, tooldia, 64)
        __, rings = seed_rings_one_by_one(polygon, tooldia, 64)
        self.assertGreater(rings, 3 * SEED_BAND_RINGS)
        self.assertGreater(len(paths), 0)
        # one cut for each band of SEED_BAND_RINGS rings, not one for each ring
        self.assertLessEqual(len(cuts), math.ceil(rings / SEED_BAND_RINGS) + 1)
        # each band holds a part of the margin
        self.assertLess(sum(cuts), len(cuts) * margin_coords / 2)

class TestConnectArrays(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()

    def test_joins_the_paths_of_the_coordinate_tuples(self):
        polygon = board()
        for method in ('clear_polygon_seed', 'clear_polygon_lines', 'clear_polygon_shrink'):
            with self.subTest(method=method):
                paths = list(getattr(self.geometry, method)(polygon, 0.4, 64, connect=False).get_objects())
                expected = connect_with_tuples(storage_of(paths), polygon, 0.4)
                joined = list(camlib.Geometry.paint_connect(storage_of(paths), polygon, 0.4, 64).get_objects())
                self.assertLess(len(joined), len(paths))
                self.assertEqual([p.wkb for p in joined], [p.wkb for p in expected])

    def test_closed_rings_are_joined_as_paths(self):
        rings = [box(1, 1, 2, 2).exterior, box(1, 2.5, 2, 3.5).exterior]
        joined = list(camlib.Geometry.paint_connect(storage_of(rings), box(0, 0, 3, 5), 0.2, 64).get_objects())
        self.assertEqual(len(joined), 1)
        self.assertIsInstance(joined[0], LineString)
        self.assertEqual(list(joined[0].coords), list(rings[0].coords) + list(rings[1].coords))


if __name__ == '__main__':
    unittest.main()
