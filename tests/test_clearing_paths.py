"""Run: python tests/test_clearing_paths.py"""
import os
import sys
import unittest
import math
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    import numpy as np
    from shapely import LineString, MultiLineString, Point, box
    from shapely.ops import unary_union
finally:
    sys.argv = _saved_argv

STEPS = (4, 16, 32)


def make_geometry():
    geometry = camlib.Geometry.__new__(camlib.Geometry)
    geometry.app = SimpleNamespace(abort_flag=False, log=Mock(), inform_no_echo=Mock(), proc_container=Mock())
    return geometry


class TestNarrowChannel(unittest.TestCase):
    """
    A channel between 2 copper pads that is a little wider than the tool must get a tool path.
    Else copper stays between the 2 nets.
    """

    def setUp(self):
        self.geometry = make_geometry()
        self.tooldia = 0.2

    def area_and_channel(self, ratio):
        gap = self.tooldia * ratio
        area = box(0, 0, 5, 5).difference(box(0, 2, 2, 3)).difference(box(2 + gap, 2, 5, 3))
        return area, box(2, 2, 2 + gap, 3).buffer(-1e-6)

    def check(self, run):
        for steps in (16, 32):
            for ratio in (1.01, 1.02, 1.04, 1.06):
                with self.subTest(steps=steps, ratio=ratio):
                    area, channel = self.area_and_channel(ratio)
                    result = run(area, steps)
                    paths = list(result.get_objects()) if result is not None else []
                    self.assertTrue(any(path.intersects(channel) for path in paths))

    def test_every_method_cuts_the_channel(self):
        for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines',
                       'clear_polygon_crosshatch'):
            self.check(lambda area, steps: getattr(camlib.Geometry, method)(
                self.geometry, area, self.tooldia, steps, connect=False))


class TestScanLinesCoverTheEdgeStrip(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()
        self.tooldia = 1.0
        self.overlap = 0.15

    def run_lines(self, polygon, steps):
        quad_segs = camlib.circle_quad_segs(steps)
        margin = polygon.buffer(-self.tooldia / 1.99999999, quad_segs=quad_segs)
        paths = list(camlib.Geometry.clear_polygon_lines(
            self.geometry, polygon, self.tooldia, steps, overlap=self.overlap, connect=False,
            contour=False).get_objects())
        return margin, paths

    def check_levels(self, levels, low, high):
        levels = sorted(set(round(v, 9) for v in levels))
        # the first and the last scan line lie on the margin
        self.assertAlmostEqual(levels[0], low, delta=1e-6)
        self.assertAlmostEqual(levels[-1], high, delta=1e-6)
        # no gap between 2 scan lines is bigger than the step-over
        gaps = [b - a for a, b in zip(levels, levels[1:])]
        self.assertLessEqual(max(gaps), self.tooldia * (1 - self.overlap) + 1e-6)

    def test_horizontal_lines(self):
        for steps in STEPS:
            with self.subTest(steps=steps):
                margin, paths = self.run_lines(box(0, 0, 20, 10), steps)
                self.check_levels([c[1] for path in paths for c in path.coords], margin.bounds[1], margin.bounds[3])

    def test_automatic_axis_unchanged(self):
        # Without an explicit angle the lines still follow the longest side of the bounding box.
        for steps in STEPS:
            with self.subTest(steps=steps):
                margin, paths = self.run_lines(box(0, 0, 10, 20), steps)
                self.check_levels([c[0] for path in paths for c in path.coords], margin.bounds[0], margin.bounds[2])


class TestCrosshatch(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()
        self.geometry.app.units = 'MM'
        self.geometry.app.options = {'global_tolerance': 0}
        self.tooldia = 1.0
        self.area = box(0, 0, 12, 3)

    def directions(self, paths):
        angles = set()
        for path in paths:
            coords = list(path.coords)
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                if math.hypot(x1 - x0, y1 - y0) > 1e-6:
                    angles.add(round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0, 6))
        return angles

    def run_crosshatch(self, angle):
        return list(self.geometry.clear_polygon_crosshatch(
            self.area, self.tooldia, 64, overlap=0.2, connect=False, contour=False, angle=angle).get_objects())

    def test_direction_families(self):
        # A non-square polygon would turn a single automatic family by 90 degrees; both families must be there.
        for angle in (0.0, 45.0, 90.0):
            with self.subTest(angle=angle):
                expected = {round(angle % 180.0, 6), round((angle + 90.0) % 180.0, 6)}
                self.assertEqual(self.directions(self.run_crosshatch(angle)), expected)

    def test_angle_is_normalized(self):
        for angle, expected in ((180.0, 0.0), (-45.0, 135.0), (float('nan'), 0.0)):
            with self.subTest(angle=angle):
                families = {round(expected, 6), round((expected + 90.0) % 180.0, 6)}
                self.assertEqual(self.directions(self.run_crosshatch(angle)), families)

    def test_contour_is_added_once(self):
        area = box(0, 0, 10.6, 10).difference(box(4, 4, 6, 6))
        paths = list(self.geometry.clear_polygon_crosshatch(
            area, self.tooldia, 64, overlap=0.2, connect=False, contour=True, angle=45).get_objects())
        rings = [path for path in paths if path.is_ring and not path.is_empty]
        self.assertEqual(len(rings), 2)  # the outer boundary and the hole
        coords = [tuple((round(x, 9), round(y, 9)) for x, y in path.coords) for path in rings]
        self.assertEqual(len(coords), len(set(coords)))

    def test_crosshatch_keeps_each_scan_line_whole(self):
        # Cutting every line at every crossing grew the path count with the square of the line count.
        box_10 = box(0, 0, 10, 10)
        counts = {}
        for method in ('clear_polygon_lines', 'clear_polygon_crosshatch'):
            result = getattr(self.geometry, method)(box_10, 0.2, 16, overlap=0.4, connect=False, contour=True)
            counts[method] = len(list(result.get_objects()))
        self.assertLessEqual(counts['clear_polygon_crosshatch'], 3 * counts['clear_polygon_lines'])

    def test_invalid_scan_spacing_is_rejected(self):
        for method in ('clear_polygon_lines', 'clear_polygon_crosshatch'):
            for diameter, overlap in ((1.0, float('nan')), (1.0, 1.0), (1.0, 1.1), (0.0, 0.2), (-1.0, 0.2)):
                with self.subTest(method=method, diameter=diameter, overlap=overlap):
                    with self.assertRaises(ValueError):
                        getattr(self.geometry, method)(self.area, diameter, 64, overlap=overlap, connect=False)

def sweep_of(paths, tooldia):
    return unary_union([path.buffer(tooldia / 2, quad_segs=32) for path in paths])


class TestClearingCoverage(unittest.TestCase):
    """
    The cutter clears all the material that it can reach (the opening of the area by the cutter radius), and does
    not cut outside the area. Without contour the edge band can stay, but nothing inside the margin.
    """
    SHAPES = {
        # the shrink center vanishes between 2 insets
        'square': (box(0, 0, 2.6, 2.6), 1.0),
        # the insets meet around a hole and split
        'hole': (box(0, 0, 10.6, 10).difference(box(4, 4, 6, 6)), 1.0),
        # the margin has 2 parts: a neck narrower than the tool
        'neck': (unary_union([box(0, 0, 4, 4), box(6, 0, 10, 4), box(3.9, 1.8, 6.1, 2.2)]), 1.0),
        # a branch and an edge parallel to the scan lines
        'branch': (unary_union([box(0, 0, 10, 4), box(4, 4, 5.25, 9)]), 1.0),
    }

    def setUp(self):
        self.geometry = make_geometry()
        self.geometry.app.units = 'MM'
        self.geometry.app.options = {'global_tolerance': 0}

    def check(self, area, tooldia, paths, contour):
        radius = tooldia / 2
        left = area.buffer(-radius, quad_segs=32).buffer(radius, quad_segs=32).difference(sweep_of(paths, tooldia))
        if not contour:
            left = left.intersection(area.buffer(-radius, quad_segs=32))
        # the chord error of the tessellated arcs leaves slivers; a pass that is missed leaves percents of the area
        self.assertLess(left.area, 1e-3 * area.area)
        self.assertLess(sweep_of(paths, tooldia).difference(area).area, 1e-4)

    def test_area_methods(self):
        for name, (area, tooldia) in self.SHAPES.items():
            for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines',
                           'clear_polygon_crosshatch'):
                for contour in (True, False):
                    if method == 'clear_polygon_shrink' and not contour:
                        continue
                    with self.subTest(shape=name, method=method, contour=contour):
                        result = getattr(self.geometry, method)(area, tooldia, 64, overlap=0.2, connect=False,
                                                                contour=contour)
                        self.check(area, tooldia, list(result.get_objects()), contour)

    def test_rotated_crosshatch_coverage_and_connections(self):
        for name in ('hole', 'neck', 'branch'):
            area, tooldia = self.SHAPES[name]
            for angle in (-45.0, 17.0, 90.0):
                for contour in (False, True):
                    for connect in (False, True):
                        with self.subTest(shape=name, angle=angle, contour=contour, connect=connect):
                            result = self.geometry.clear_polygon_crosshatch(
                                area, tooldia, 64, overlap=0.2, connect=connect, contour=contour, angle=angle)
                            self.check(area, tooldia, list(result.get_objects()), contour)

    def test_exact_fit_touches_the_outline(self):
        # An L slot and a trace exactly as wide as the tool: the cutter touches both walls, it cuts past them only by
        # the back-down of the margin (0.1 um here).
        slot = unary_union([box(0, 0, 8, 1), box(7, 0, 8, 6)])
        trace = LineString([(0, 0), (6, 0), (6, 4)])
        runs = {method: lambda method=method: getattr(self.geometry, method)(slot, 1.0, 64, overlap=0.2, connect=False)
                for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines',
                               'clear_polygon_crosshatch')}
        runs['fill_with_lines'] = lambda: self.geometry.fill_with_lines(trace, 1.0, 1.0, 64, connect=False)
        for name, run in runs.items():
            with self.subTest(method=name):
                area = trace.buffer(0.5, quad_segs=32) if name == 'fill_with_lines' else slot
                swept = sweep_of(list(run().get_objects()), 1.0)
                self.assertGreater(swept.intersection(area).area, 0.95 * area.area)
                self.assertLess(swept.difference(area).area, 2 * area.length * camlib.exact_fit(0.5))

    def test_clearing_error_and_edge_preferences(self):
        # Touch: the contour cuts past the outline by at most the error. Strict: never past it, and it leaves a thin
        # layer of material.
        area = box(0, 0, 6, 6).difference(box(2, 2, 4, 4))
        reach = area.buffer(-0.5, quad_segs=256).buffer(0.5, quad_segs=256)
        for edge in ('touch', 'strict'):
            for error_um in (0.5, 10.0):
                for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines',
                               'clear_polygon_crosshatch'):
                    with self.subTest(edge=edge, error=error_um, method=method):
                        self.geometry.app.options = {'global_tolerance': 0, 'tools_clearing_error': error_um,
                                                     'tools_clearing_edge': edge}
                        paths = list(getattr(self.geometry, method)(area, 1.0, 64, overlap=0.2,
                                                                     connect=False).get_objects())
                        past = max(0.5 - area.boundary.distance(path) for path in paths)
                        left = reach.difference(unary_union([path.buffer(0.5, quad_segs=256) for path in paths]))
                        if edge == 'touch':
                            self.assertLessEqual(past, error_um / 1000 + 1e-9)
                            self.assertLess(left.area, 1e-6)
                        else:
                            self.assertLessEqual(past, 1e-9)
                            # the walls keep at most the error; a right-angled corner of the area keeps sqrt(2) times it
                            self.assertTrue(left.buffer(-0.75 * error_um / 1000 - 1e-6).is_empty)

    def test_strict_edge_stays_strict_next_to_an_exact_fit_branch(self):
        # A branch exactly as wide as the tool backs down only in the branch: around the copper the contour stays
        # strict, and the branch is cleared. A branch 2 um wider fits the cutter too: Strict must not drop it.
        copper = box(4, 4, 6, 6)
        self.geometry.app.options = {'global_tolerance': 0, 'tools_clearing_error': 2.0, 'tools_clearing_edge': 'strict'}
        for width in (1.0, 1.002):
            area = unary_union([box(0, 0, 10, 10).difference(copper), box(10, 5 - width / 2, 14, 5 + width / 2)])
            for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines',
                           'clear_polygon_crosshatch'):
                with self.subTest(width=width, method=method):
                    paths = list(getattr(self.geometry, method)(area, 1.0, 64, overlap=0.2,
                                                                 connect=False).get_objects())
                    self.assertGreaterEqual(min(copper.exterior.distance(path) for path in paths), 0.5 - 1e-9)
                    self.assertGreater(sweep_of(paths, 1.0).intersection(box(10.5, 4.5, 13.5, 5.5)).area, 2.9)
                    if width > 1.0:
                        self.assertGreaterEqual(min(area.boundary.distance(path) for path in paths), 0.5 - 1e-9)

    def test_clearing_error_follows_the_units_of_the_project(self):
        # An inch project: app.app_units is the project, app.units can be stale. 2 um is 0.0000787 in.
        inch = 25.4
        area = box(0, 0, 6 / inch, 6 / inch).difference(box(2 / inch, 2 / inch, 4 / inch, 4 / inch))
        radius = 0.5 / inch
        self.geometry.app.units, self.geometry.app.app_units = 'MM', 'IN'
        self.geometry.app.options = {'global_tolerance': 0, 'tools_clearing_error': 2.0, 'tools_clearing_edge': 'touch'}
        paths = list(self.geometry.clear_polygon_shrink(area, 2 * radius, 64, overlap=0.2, connect=False).get_objects())
        self.assertLessEqual(max(radius - area.boundary.distance(path) for path in paths), 2.0 / 25400 + 1e-12)

    def test_laser_lines(self):
        traces = {
            'straight': (LineString([(0, 0), (10, 0)]), 3.0, 0.5),
            'sharp bends': (LineString([(0, 0), (5, 0), (2, 4), (8, 4)]), 2.0, 0.3),
            'closed': (LineString([(0, 0), (6, 0), (6, 6), (0, 6), (0, 0)]), 2.0, 0.3),
            'multipart': (MultiLineString([[(0, 0), (5, 0)], [(5, 0), (5, 5)]]), 2.0, 0.3),
        }
        for name, (follow, aperture, tooldia) in traces.items():
            for contour in (True, False):
                with self.subTest(trace=name, contour=contour):
                    result = self.geometry.fill_with_lines(follow, aperture, tooldia, 64, overlap=0.15,
                                                           connect=False, contour=contour)
                    self.check(follow.buffer(aperture / 2, quad_segs=32), tooldia, list(result.get_objects()),
                               contour)


def turn(coords):
    """+1 for a CCW closed run, -1 for CW (the sign of the shoelace area)."""
    xy = np.asarray(coords, dtype=float)
    return 1 if 0.5 * float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])) > 0 else -1


def swept_around(path, center):
    """+1 if the path turns CCW around the center, else -1."""
    xy = np.asarray(path.coords) - center
    return 1 if float(np.sum(xy[:-1, 0] * xy[1:, 1] - xy[1:, 0] * xy[:-1, 1])) > 0 else -1


class TestClearingDirection(unittest.TestCase):
    """
    The wall on the right (climb): the rings of the outer boundary of the domain are CCW, the rings around an island
    (a hole) are CW; conventional is the opposite. Also when the rings are joined, and without Clipper.
    """
    CENTER = (10.0, 10.0)

    def setUp(self):
        self.geometry = make_geometry()
        self.geometry.app.units = 'MM'
        self.geometry.app.options = {'global_tolerance': 0}
        self.area = box(0, 0, 20, 20).difference(Point(self.CENTER).buffer(4, quad_segs=16))

    def role(self, ring):
        """'outer' for a ring of the outer boundary, 'hole' for a ring around the island, by the distance to the hole."""
        dist = np.hypot(*(np.asarray(ring) - self.CENTER).T)
        if dist.max() < 7.0:
            return 'hole'
        if dist.min() > 7.0:
            return 'outer'
        return None

    def kernel(self, name, direction, connect):
        if name == 'shrink':
            result = camlib.Geometry.clear_polygon_shrink(self.geometry, self.area, 1.0, 32, overlap=0.5,
                                                          connect=connect, direction=direction)
        else:
            result = camlib.Geometry.clear_polygon_seed(self.geometry, self.area, 1.0, 32, overlap=0.5,
                                                        connect=connect, contour=True, direction=direction)
        return list(result.get_objects())

    @staticmethod
    def rings_of(paths):
        return [list(path.coords) for path in paths if len(path.coords) > 3 and path.coords[0] == path.coords[-1]]

    @staticmethod
    def expect(direction):
        outer = 1 if direction == 'climb' else -1
        return {'outer': outer, 'hole': -outer}

    def check_rings(self, direction):
        seen = set()
        for ring in self.rings_of(self.kernel('shrink', direction, False)):
            role = self.role(ring)
            self.assertIsNotNone(role)
            seen.add(role)
            self.assertEqual(turn(ring), self.expect(direction)[role])
        self.assertEqual(seen, {'outer', 'hole'})

    def test_rings_by_role_without_connect(self):
        for direction in ('climb', 'conv'):
            with self.subTest(direction=direction, clipper=True):
                self.check_rings(direction)
            # Clipper and GEOS give opposite rings: without Clipper the result is the same
            with self.subTest(direction=direction, clipper=False), mock.patch.object(
                    camlib.clipper_ops.PolygonOffsetter, 'offset', return_value=None):
                self.check_rings(direction)

    def test_seed_arcs_turn_the_same_way_around_the_seed(self):
        for direction, sign in (('climb', 1), ('conv', -1)):
            with self.subTest(direction=direction):
                paths = list(camlib.Geometry.clear_polygon_seed(
                    self.geometry, box(0, 0, 20, 20), 1.0, 32, seedpoint=Point(3, 3), overlap=0.5, connect=False,
                    contour=False, direction=direction).get_objects())
                arcs = [path for path in paths if path.coords[0] != path.coords[-1]]
                self.assertGreater(len(arcs), 5)
                for arc in arcs:
                    self.assertEqual(swept_around(arc, (3, 3)), sign)

    def test_lines_keep_the_zigzag_and_orient_only_the_rings(self):
        for name in ('clear_polygon_lines', 'clear_polygon_crosshatch'):
            with self.subTest(kernel=name):
                kernel = getattr(camlib.Geometry, name)
                plain = list(kernel(self.geometry, self.area, 1.0, 32, overlap=0.2, connect=True).get_objects())
                climb = list(kernel(self.geometry, self.area, 1.0, 32, overlap=0.2, connect=True,
                                    direction='climb').get_objects())
                # the scan lines are not oriented and keep the zig-zag of the join
                self.assertEqual(len(plain), len(climb))
                self.assertEqual(sum(len(p.coords) for p in plain), sum(len(p.coords) for p in climb))
                rings = self.rings_of(kernel(self.geometry, self.area, 1.0, 32, overlap=0.2, connect=False,
                                             direction='climb').get_objects())
                self.assertEqual({turn(ring) for ring in rings if self.role(ring) == 'outer'}, {1})
                self.assertEqual({turn(ring) for ring in rings if self.role(ring) == 'hole'}, {-1})

    def test_paint_connect_keep_direction_never_reverses_a_path(self):
        paths = [LineString([(x, 0), (x, 5)]) for x in range(0, 12)]
        paths += [LineString([(30, 30), (31, 30)]), LineString([(31, 31), (30, 31)])]
        results = {}
        for keep in (True, False):
            storage = camlib.AppRTreeStorage()
            storage.get_points = lambda o: [o.coords[0], o.coords[-1]]
            storage.insert_many(paths)
            joined = camlib.Geometry.paint_connect(storage, box(-5, -5, 40, 40), 0.1, 32, keep_direction=keep)
            results[keep] = [np.asarray(path.coords) for path in joined.get_objects()]

        def steps(runs, start, end):
            """How many times the path start -> end is walked in these runs as consecutive points."""
            return sum(1 for run in runs for i in range(len(run) - 1)
                       if np.allclose(run[i], start) and np.allclose(run[i + 1], end))

        for path in paths:
            start, end = path.coords[0], path.coords[-1]
            self.assertEqual(steps(results[True], start, end), 1)
            self.assertEqual(steps(results[True], end, start), 0)
        # the old join reverses an open path when its last point is the nearest
        self.assertTrue(any(steps(results[False], path.coords[-1], path.coords[0]) for path in paths))

    def test_clip_pieces_of_a_ring_keep_its_direction(self):
        ring = Point(0, 0).buffer(10, quad_segs=16)
        clip = box(-20, -3, 20, 3)
        for direction, sign in (('climb', 1), ('conv', -1)):
            with self.subTest(direction=direction):
                boundary = camlib.oriented_boundary(ring, direction)
                pieces = camlib.flatten_shapely_geometry(boundary.intersection(clip))
                self.assertEqual(len(pieces), 3)
                for piece in pieces:
                    self.assertEqual(swept_around(piece, (0, 0)), sign)


if __name__ == '__main__':
    unittest.main()
