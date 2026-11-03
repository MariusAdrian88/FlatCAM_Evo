"""Run: python tests/test_clearing_paths.py"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    from shapely import LineString, MultiLineString, box
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
        for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
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
            for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
                for contour in (True, False):
                    if method == 'clear_polygon_shrink' and not contour:
                        continue
                    with self.subTest(shape=name, method=method, contour=contour):
                        result = getattr(self.geometry, method)(area, tooldia, 64, overlap=0.2, connect=False,
                                                                contour=contour)
                        self.check(area, tooldia, list(result.get_objects()), contour)

    def test_exact_fit_touches_the_outline(self):
        # An L slot and a trace exactly as wide as the tool: the cutter touches both walls, it cuts past them only by
        # the back-down of the margin (0.1 um here).
        slot = unary_union([box(0, 0, 8, 1), box(7, 0, 8, 6)])
        trace = LineString([(0, 0), (6, 0), (6, 4)])
        runs = {method: lambda method=method: getattr(self.geometry, method)(slot, 1.0, 64, overlap=0.2, connect=False)
                for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines')}
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
                for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
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
            for method in ('clear_polygon_shrink', 'clear_polygon_seed', 'clear_polygon_lines'):
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


if __name__ == '__main__':
    unittest.main()
