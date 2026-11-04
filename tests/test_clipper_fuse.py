#!/usr/bin/env python
"""
Tests for appParsers/clipper_fuse.py (optional Clipper2 fusion of Gerber polygons).

Group A needs pyclipr: the tests are skipped when pyclipr is missing, unless the environment variable
FLATCAM_TEST_CLIPPER=1 is set (then they fail).
Group B tests the safe fallback (the function returns None) and needs no pyclipr, except where noted.

Run: python tests/test_clipper_fuse.py
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
import shapely
import shapely.affinity as affinity
from shapely import MultiPolygon, Point, Polygon, STRtree, box

from appParsers import clipper_fuse
from appParsers.clipper_fuse import fuse_parts

try:
    import pyclipr
except Exception:
    pyclipr = None

HAVE_CLIPPER = pyclipr is not None
STRICT_CLIPPER = os.environ.get('FLATCAM_TEST_CLIPPER') == '1'

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'eagle')
HAVE_EAGLE = all(os.path.isfile(os.path.join(FIXTURES, f)) for f in ('eagle.GTL', 'eagle.GBL'))
CLOSING = 1e-8


# ----------------------------------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------------------------------

def geos_closing(parts):
    """Reference: the GEOS closing used by the Gerber parser. Returns a list of Polygons."""
    flat = shapely.get_parts(np.asarray(parts, dtype=object))
    result = MultiPolygon(list(flat)).buffer(CLOSING).buffer(-CLOSING)
    return list(result.geoms) if hasattr(result, 'geoms') else [result]


def count_holes(polys):
    return sum(len(p.interiors) for p in polys)


def boundary_segments(polys):
    """All ring segments of the polygons as an array of LineStrings."""
    rings, _ = shapely.get_rings(np.asarray(polys, dtype=object), return_index=True)
    coords, idx = shapely.get_coordinates(rings, return_index=True)
    same_ring = idx[1:] == idx[:-1]
    pairs = np.stack([coords[:-1][same_ring], coords[1:][same_ring]], axis=1)
    return shapely.linestrings(pairs)


def max_boundary_distance(polys_a, polys_b, max_segment=None, skip_box=None, within=None):
    """
    Largest distance from a sample point of the boundary of A to the boundary of B and the other way round.

    :param max_segment: Boundary segments are split to this length before sampling (None: vertices only).
    :param skip_box: Sample points inside this shapely box are ignored.
    :param within: When set, the points that are closer than this distance to the other boundary are found with
                   1 'dwithin' query (cheap, few candidates) and only the other points get a nearest search; the
                   result is then the largest distance of the other points (0.0 when every point is within).
    """
    worst = 0.0
    for first, second in ((polys_a, polys_b), (polys_b, polys_a)):
        geom = MultiPolygon(first)
        if max_segment:
            geom = shapely.segmentize(geom, max_segment)
        points = shapely.points(shapely.get_coordinates(geom))
        if skip_box is not None:
            points = points[~shapely.contains(skip_box, points)]
        if points.size == 0:
            continue
        tree = STRtree(boundary_segments(second))
        if within is not None:
            near = np.zeros(len(points), dtype=bool)
            near[tree.query(points, predicate='dwithin', distance=within)[0]] = True
            points = points[~near]
            if points.size == 0:
                continue
        _, dist = tree.query_nearest(points, return_distance=True, all_matches=False)
        worst = max(worst, float(dist.max()))
    return worst


def parts_of(geom):
    return list(geom.geoms) if hasattr(geom, 'geoms') else [geom]


def sq(x0, y0, x1, y1):
    return box(x0, y0, x1, y1)


# ----------------------------------------------------------------------------------------------------------------------
# Eagle fixtures: the polygons that the Gerber parser fuses at the end of parse_lines()
# ----------------------------------------------------------------------------------------------------------------------

_PARTS_CACHE = {}


def collect_eagle_parts(filename):
    """Parse an Eagle fixture and return the list of polygons passed to the final fusion."""
    if filename in _PARTS_CACHE:
        return _PARTS_CACHE[filename]

    # importing appMain parses sys.argv (for example "-v" of unittest makes it exit)
    with mock.patch.object(sys, 'argv', sys.argv[:1]):
        import appParsers.ParseGerber as parse_gerber
        from appMain import App  # noqa: F401  (imported inside Gerber.__init__)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_gerber_parser import MockApp

    app = MockApp()
    app.options.update({
        'gerber_circle_steps': 16,
        'gerber_buffering': 'no',
        'gerber_simplification': False,
        'gerber_use_buffer_for_union': True,
        'gerber_extra_buffering': False,
    })

    recorded = []
    original_prepare = parse_gerber.prepare

    def recording_prepare(geom, *args, **kwargs):
        if isinstance(geom, np.ndarray) and geom.dtype == object:
            recorded.append(list(geom))
        return original_prepare(geom, *args, **kwargs)

    with mock.patch.object(parse_gerber, 'prepare', recording_prepare):
        gerber = parse_gerber.Gerber(app)
        gerber.parse_file(os.path.join(FIXTURES, filename))

    if not recorded:
        raise RuntimeError("No polygons recorded for %s" % filename)
    _PARTS_CACHE[filename] = max(recorded, key=len)
    return _PARTS_CACHE[filename]


# ----------------------------------------------------------------------------------------------------------------------
# Group A: needs pyclipr
# ----------------------------------------------------------------------------------------------------------------------

class ClipperTestCase(unittest.TestCase):
    """Base class: skip (or fail in strict mode) when pyclipr is missing."""

    def setUp(self):
        if not HAVE_CLIPPER:
            if STRICT_CLIPPER:
                self.fail("pyclipr is missing but FLATCAM_TEST_CLIPPER=1")
            self.skipTest("pyclipr is not installed")

    def fuse(self, parts, **kwargs):
        result = fuse_parts(parts, **kwargs)
        self.assertIsNotNone(result, "fuse_parts() returned None")
        return result

    def assert_same_as_geos(self, parts, tol=1e-8, expected=None):
        """Same number of polygons and holes as the GEOS closing, boundary distance <= tol."""
        result = self.fuse(parts)
        reference = geos_closing(parts)
        self.assertEqual(len(result), len(reference), "polygon count")
        self.assertEqual(count_holes(result), count_holes(reference), "hole count")
        if expected is not None:
            self.assertEqual((len(result), count_holes(result)), expected)
        self.assertTrue(MultiPolygon(result).is_valid)
        if result:
            dist = max_boundary_distance(result, reference, max_segment=0.05)
            self.assertLessEqual(dist, tol)
        return result


class TestSmallCases(ClipperTestCase):

    def test_gap_1e8_between_squares_is_closed(self):
        self.assert_same_as_geos([sq(0, 0, 1, 1), sq(1 + 1e-8, 0, 2 + 1e-8, 1)], expected=(1, 0))

    def test_hole_with_1e8_neck_is_split(self):
        hole = [(2, 2), (4, 2), (4, 3), (6, 3), (6, 2), (8, 2), (8, 4), (6, 4), (6, 3 + 1e-8), (4, 3 + 1e-8), (4, 4),
                (2, 4)]
        part = Polygon(sq(0, 0, 10, 10).exterior.coords, [hole])
        self.assertTrue(part.is_valid)
        self.assert_same_as_geos([part], expected=(1, 2))

class TestStructure(ClipperTestCase):

    def test_nested_three_levels(self):
        base = sq(0, 0, 30, 30).difference(sq(3, 3, 12, 12)).difference(sq(18, 18, 27, 27))
        island_in_hole = sq(5, 5, 10, 10).difference(sq(7, 7, 8, 8))
        result = self.assert_same_as_geos([base, island_in_hole], expected=(2, 3))
        # the hole in the island must not be filled
        self.assertFalse(MultiPolygon(result).intersects(Point(7.5, 7.5)))
        self.assertTrue(MultiPolygon(result).intersects(Point(5.5, 5.5)))

    def test_multipart_input(self):
        multi = MultiPolygon([sq(0, 0, 1, 1), sq(1 + 1e-8, 0, 2, 1), sq(5, 5, 6, 6)])
        result = self.assert_same_as_geos([multi, sq(5.5, 5.5, 7, 7)], expected=(2, 0))
        self.assertAlmostEqual(sum(p.area for p in result), 2 + 1 + 2.25 - 0.25, places=6)


class TestClipperOps(ClipperTestCase):
    """appCommon/clipper_ops.py: the results match GEOS within the arc tolerance."""

    TOL = 1e-4

    def assert_close(self, result, reference, tol):
        self.assertIsNotNone(result)
        shape = MultiPolygon(result)
        self.assertTrue(shape.is_valid)
        self.assertEqual(len(result), len(getattr(reference, 'geoms', [reference])))
        self.assertLess(shape.symmetric_difference(reference).area, reference.length * tol * 2)

    def test_offset_polygons_in_and_out_keep_the_holes(self):
        from appCommon import clipper_ops
        part = sq(0, 0, 10, 10).difference(sq(4, 4, 6, 6))
        worker = clipper_ops.PolygonOffsetter([part])
        for distance in (-0.5, -1.5, 0.3):
            reference = part.buffer(distance, quad_segs=64)
            self.assert_close(worker.offset(distance, self.TOL), reference, self.TOL)
        self.assertEqual(worker.offset(-6, self.TOL), [])

    def test_offset_lines_is_the_union_of_the_line_buffers(self):
        from appCommon import clipper_ops
        lines = [shapely.LineString([(0, 0), (10, 0)]), shapely.LineString([(5, -5), (5, 5)]),
                 shapely.LinearRing([(20, 0), (24, 0), (24, 4), (20, 4)])]
        reference = shapely.union_all([line.buffer(0.5, quad_segs=64) for line in lines])
        self.assert_close(clipper_ops.offset_lines(lines, 0.5, self.TOL), reference, self.TOL)

    def test_boolean_difference_and_union(self):
        from appCommon import clipper_ops
        board = sq(0, 0, 20, 20)
        copper = [sq(2, 2, 5, 5), sq(4, 4, 8, 8), Point(15, 15).buffer(2, quad_segs=16)]
        self.assert_close(clipper_ops.boolean(board, copper, 'difference'),
                          board.difference(shapely.union_all(copper)), 1e-9)
        self.assert_close(clipper_ops.union(copper), shapely.union_all(copper), 1e-9)

    def test_no_pyclipr_or_bad_input_returns_none(self):
        from appCommon import clipper_ops
        with mock.patch.object(clipper_ops, 'pyclipr', None):
            self.assertIsNone(clipper_ops.offset_polygons([sq(0, 0, 1, 1)], -0.1, self.TOL))
            self.assertIsNone(clipper_ops.offset_lines([shapely.LineString([(0, 0), (1, 0)])], 0.1, self.TOL))
            self.assertIsNone(clipper_ops.union([sq(0, 0, 1, 1)]))
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
        self.assertIsNone(clipper_ops.union([bowtie]))
        self.assertIsNone(clipper_ops.offset_lines([sq(0, 0, 1, 1)], 0.1, self.TOL))

# ----------------------------------------------------------------------------------------------------------------------
# Group B: safe fallback
# ----------------------------------------------------------------------------------------------------------------------

class TestFallbackWithoutClipper(unittest.TestCase):
    """The input checks must reject the data before Clipper is used: these tests need no pyclipr."""

    def assert_rejected(self, parts, **kwargs):
        fake = mock.MagicMock()
        with mock.patch.object(clipper_fuse, 'pyclipr', fake):
            self.assertIsNone(fuse_parts(parts, **kwargs))
        fake.Clipper.assert_not_called()

class TestFallbackWithClipper(ClipperTestCase):
    """Failures inside or after Clipper: these tests need pyclipr."""

    PARTS = [sq(0, 0, 1, 1), sq(3, 0, 4, 1)]

# ----------------------------------------------------------------------------------------------------------------------
# Parse level: Gerber.parse_lines() / parse_file() with the Clipper fusion
# ----------------------------------------------------------------------------------------------------------------------

def parse_gerber_data(lines=None, path=None, geos=False, fuse_result=None, patches=()):
    """
    Parse Gerber lines or a file with a parser in the default mode (full buffering, union by buffer).

    :param geos: Reference parse: pyclipr is patched to None, so the GEOS code runs.
    :param fuse_result: A list with 1 value: fuse_parts() of the parser is patched to return this value.
    :param patches: Other patches (mock.patch objects) that are active during the parse.
    :return: (Gerber object, list of the values returned by the fuse_parts() calls)
    """
    from contextlib import ExitStack

    with mock.patch.object(sys, 'argv', sys.argv[:1]):  # importing appMain parses sys.argv
        import appParsers.ParseGerber as parse_gerber
        from appMain import App  # noqa: F401  (imported inside Gerber.__init__)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_gerber_parser import MockApp

    app = MockApp()
    app.options.update({
        'gerber_buffering': 'full',
        'gerber_use_buffer_for_union': True,
        'gerber_circle_steps': 16,
        'gerber_simplification': False,
        'gerber_extra_buffering': False,
    })
    gbr = parse_gerber.Gerber(app)

    results = []
    real_fuse = parse_gerber.fuse_parts

    def recording_fuse(parts, *args, **kwargs):
        value = fuse_result[0] if fuse_result is not None else real_fuse(parts, *args, **kwargs)
        results.append(value)
        return value

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(parse_gerber, 'fuse_parts', recording_fuse))
        if geos:
            stack.enter_context(mock.patch.object(clipper_fuse, 'pyclipr', None))
        for patch in patches:
            stack.enter_context(patch)
        if path is not None:
            gbr.parse_file(path)
        else:
            gbr.parse_lines(list(lines))
    return gbr, results


def as_polygons(geo):
    """The Polygons of a solid geometry (list, Multi-geometry or single geometry)."""
    items = list(geo) if isinstance(geo, (list, tuple)) else [geo]
    polys = []
    for item in items:
        polys.extend(p for p in shapely.get_parts(item) if isinstance(p, Polygon) and not p.is_empty)
    return polys


def geometry_wkb(geo):
    items = list(geo) if isinstance(geo, (list, tuple)) else [geo]
    return [shapely.to_wkb(g) for g in items]


def tools_wkb(gbr):
    """WKB of every geometry stored per aperture."""
    result = {}
    for code, tool in sorted(gbr.tools.items()):
        result[code] = [(key, shapely.to_wkb(geo)) for item in tool['geometry'] for key, geo in sorted(item.items())]
    return result


def outside_box_counts(polys, region):
    """Number of polygons and holes whose bounds do not touch the region."""
    polygons = [p for p in polys if not region.intersects(box(*p.bounds))]
    holes = [r for p in polys for r in p.interiors if not region.intersects(box(*r.bounds))]
    return len(polygons), len(holes)


def square_region(x0, y0, x1, y1, scale=1):
    """Gerber lines of a rectangular region (format 4.6: 1 unit = 1e-6 file units, multiplied by scale)."""
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    lines = ["G36*", "X%dY%dD02*" % (pts[0][0] * scale, pts[0][1] * scale)]
    lines += ["X%dY%dD01*" % (x * scale, y * scale) for x, y in pts[1:]]
    return lines + ["G37*"]


class ParseTestCase(ClipperTestCase):
    """Parse-level tests that need pyclipr: a silent fallback to GEOS must not hide a problem."""

    def assert_clipper_used(self, results):
        self.assertTrue(any(r is not None for r in results), "fuse_parts() never returned a result: %r" % (results,))


@pytest.mark.slow
@unittest.skipUnless(HAVE_EAGLE, "the Eagle fixtures are missing")
class TestParseEagle(ParseTestCase):
    POINT = (2.5614, 2.6473)
    BOX = box(2.5582, 2.6437, 2.5646, 2.6519)

    def parse_both(self, filename):
        path = os.path.join(FIXTURES, filename)
        fast, results = parse_gerber_data(path=path)
        self.assert_clipper_used(results)
        reference, _ = parse_gerber_data(path=path, geos=True)
        return fast, reference

    def test_top_layer(self):
        fast, reference = self.parse_both('eagle.GTL')
        polys, ref_polys = as_polygons(fast.solid_geometry), as_polygons(reference.solid_geometry)
        self.assertTrue(MultiPolygon(polys).covers(Point(*self.POINT)), "copper at the GEOS problem point")
        self.assertEqual(outside_box_counts(polys, self.BOX), outside_box_counts(ref_polys, self.BOX))
        self.assertEqual(tools_wkb(fast), tools_wkb(reference))


def small_board(inch):
    """
    A small board as Gerber lines, in mm or in inches. The coordinates are integer multiples of 127e-6 mm (5e-6
    inch), so both files describe the same geometry without rounding.
    """
    scale = 5 if inch else 127
    factor = 1 / 25.4 if inch else 1.0
    header = ["%FSLAX46Y46*%", "%MOIN*%" if inch else "%MOMM*%",
              "%ADD10C,{:.12f}*%".format(0.5 * factor), "%ADD11R,{:.12f}X{:.12f}*%".format(2 * factor, 1 * factor),
              "G01*", "D10*"]
    body = square_region(0, 0, 200000, 100000, scale)                           # 25.4 x 12.7 mm
    body += ["%LPC*%"] + square_region(40000, 20000, 80000, 60000, scale) + ["%LPD*%"]  # a hole
    body += square_region(50000, 30000, 60000, 40000, scale)                    # an island in the hole
    body += square_region(210000, 0, 250000, 50000, scale)                      # a separate part
    body += square_region(250400, 0, 300000, 50000, scale)                      # next to it, gap of 0.05 mm
    body += ["X0Y%dD02*" % (150000 * scale), "X%dY%dD01*" % (100000 * scale, 150000 * scale)]  # a track
    body += ["X%dY%dD03*" % (50000 * scale, 150000 * scale), "X%dY%dD03*" % (60000 * scale, 150000 * scale)]
    body += ["D11*", "X%dY%dD03*" % (150000 * scale, 150000 * scale),
             "X%dY%dD03*" % (150100 * scale, 150000 * scale)]                   # 2 overlapping rectangles
    return header + body + ["M02*"]


class TestParseUnits(ParseTestCase):

    def test_mm_and_inch_boards(self):
        gbr_mm, results_mm = parse_gerber_data(lines=small_board(inch=False))
        gbr_in, results_in = parse_gerber_data(lines=small_board(inch=True))
        self.assert_clipper_used(results_mm)
        self.assert_clipper_used(results_in)
        self.assertEqual((gbr_mm.units, gbr_in.units), ('MM', 'IN'))
        polys_mm = as_polygons(gbr_mm.solid_geometry)
        polys_in = [affinity.scale(p, 25.4, 25.4, origin=(0, 0)) for p in as_polygons(gbr_in.solid_geometry)]
        self.assertEqual(len(polys_mm), len(polys_in))
        self.assertEqual(count_holes(polys_mm), count_holes(polys_in))
        self.assertGreater(count_holes(polys_mm), 0)
        # The parser makes regions and flashes 1e-7 file units larger, which is 2.54e-6 mm in inches. Because of this
        # the 2 files can differ by this value, with or without the Clipper fusion.
        self.assertLessEqual(max_boundary_distance(polys_mm, polys_in, max_segment=0.1), 3e-6)
        # the Clipper result against the GEOS result of the same file: 1e-6 mm
        for inch, gbr in ((False, gbr_mm), (True, gbr_in)):
            reference, _ = parse_gerber_data(lines=small_board(inch=inch), geos=True)
            factor = 25.4 if inch else 1.0
            polys = as_polygons(gbr.solid_geometry)
            ref_polys = as_polygons(reference.solid_geometry)
            self.assertEqual(len(polys), len(ref_polys))
            self.assertEqual(count_holes(polys), count_holes(ref_polys))
            self.assertLessEqual(max_boundary_distance(polys, ref_polys, max_segment=0.1 / factor), 1e-6 / factor)


class TestParseFallback(unittest.TestCase):
    """The parser must give the GEOS result when the Clipper fusion is not used. These tests need no pyclipr."""

    # ADD12 is a macro aperture with a self-intersecting outline (a bowtie), ADD13 a circle of 4e-10 mm
    HEADER = ["%FSLAX46Y46*%", "%MOMM*%", "%AMBOW*4,1,4,0,0,2,2,2,0,0,2,0,0,0*%", "%ADD10C,0.5*%", "%ADD12BOW*%",
              "%ADD13C,0.0000000004*%", "G01*"]

    def assert_geos_result(self, lines, geos=False, expect_fuse_results=None):
        """Parse; the result must equal the parse with fuse_parts() returning None."""
        gbr, results = parse_gerber_data(lines=lines, geos=geos)
        reference, _ = parse_gerber_data(lines=lines, fuse_result=[None])
        self.assertEqual(geometry_wkb(gbr.solid_geometry), geometry_wkb(reference.solid_geometry))
        self.assertEqual(tools_wkb(gbr), tools_wkb(reference))
        if expect_fuse_results is not None:
            self.assertEqual(results, expect_fuse_results)
        return gbr, results

    def test_pyclipr_missing(self):
        # 2 fusions: the composition of the polarity layers, then the last layer
        gbr, results = self.assert_geos_result(small_board(inch=False), geos=True, expect_fuse_results=[None, None])
        self.assertGreater(len(as_polygons(gbr.solid_geometry)), 3)

    def test_invalid_polygon_from_macro(self):
        # The parser repairs a bowtie region, but a macro with a self-intersecting outline gives an invalid polygon.
        lines = self.HEADER + ["D12*", "X20000000Y20000000D03*", "D10*"] + square_region(0, 0, 100000, 100000, 127) +             ["M02*"]
        gbr, results = self.assert_geos_result(lines, expect_fuse_results=[None])
        self.assertEqual(len(as_polygons(gbr.solid_geometry)), 2)

if __name__ == '__main__':
    unittest.main()
