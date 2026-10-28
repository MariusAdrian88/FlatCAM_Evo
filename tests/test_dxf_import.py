"""Tests for the appParsers.DXF importer; run directly: python tests/test_dxf_import.py"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from shapely.ops import unary_union

from appParsers.DXF import dxf_to_shapes, text_entity_to_shapes
from appParsers.DXF import text as dxf_text


def new_doc():
    doc = ezdxf.new()
    return doc, doc.modelspace()


class DxfImportTest(unittest.TestCase):

    def test_point_line(self):
        doc, msp = new_doc()
        msp.add_point((3, 4))
        msp.add_line((0, 0), (10, 0))
        msp.add_line((1, 1), (1, 1))  # zero length: skipped
        shapes = dxf_to_shapes(doc)
        self.assertEqual(sorted(s.geom_type for s in shapes), ['LineString', 'Polygon'])
        poly = [s for s in shapes if s.geom_type == 'Polygon'][0]
        self.assertAlmostEqual(poly.centroid.x, 3, places=3)
        self.assertAlmostEqual(poly.area, math.pi * 0.01 ** 2, places=4)

    def test_circle(self):
        doc, msp = new_doc()
        msp.add_circle((1, 2), 5)
        (poly,) = dxf_to_shapes(doc)
        self.assertEqual(poly.geom_type, 'Polygon')
        self.assertAlmostEqual(poly.area, 78.54, delta=0.3)
        self.assertGreaterEqual(len(poly.exterior.coords) - 1, 100)
        self.assertAlmostEqual(poly.centroid.x, 1, places=3)

    def test_arc(self):
        doc, msp = new_doc()
        msp.add_arc((0, 0), 10, 0, 90)
        (line,) = dxf_to_shapes(doc)
        self.assertEqual(line.geom_type, 'LineString')
        self.assertAlmostEqual(line.length, 15.708, delta=0.02)
        b = line.bounds
        self.assertAlmostEqual(b[2], 10, places=3)
        self.assertAlmostEqual(b[3], 10, places=3)

    def test_lwpolyline_bulge_and_closed(self):
        doc, msp = new_doc()
        # half circle of diameter 10 (bulge 1): length = pi * 5
        msp.add_lwpolyline([(0, 0, 0, 0, 1), (10, 0)])
        msp.add_lwpolyline([(0, 0), (4, 0), (4, 3)], close=True)
        half, tri = dxf_to_shapes(doc)
        self.assertEqual(half.geom_type, 'LineString')
        self.assertAlmostEqual(half.length, math.pi * 5, delta=0.03)
        self.assertEqual(tri.geom_type, 'LineString')
        self.assertTrue(tri.is_ring)
        self.assertAlmostEqual(tri.length, 12, places=6)

    def test_ellipse(self):
        doc, msp = new_doc()
        msp.add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5)
        msp.add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5, start_param=0, end_param=math.pi)
        full, half = dxf_to_shapes(doc)
        self.assertEqual(full.geom_type, 'LineString')
        self.assertTrue(full.is_ring)
        # Ramanujan perimeter for a=10, b=5
        a, b = 10, 5
        h = ((a - b) / (a + b)) ** 2
        perimeter = math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
        self.assertAlmostEqual(full.length, perimeter, delta=0.05)
        self.assertAlmostEqual(half.length, perimeter / 2, delta=0.05)
        self.assertFalse(half.is_ring)
        self.assertAlmostEqual(half.bounds[1], 0, places=3)

    def test_spline(self):
        doc, msp = new_doc()
        msp.add_spline(fit_points=[(0, 0), (5, 5), (10, 0), (15, 5)])
        (line,) = dxf_to_shapes(doc)
        self.assertEqual(line.geom_type, 'LineString')
        self.assertAlmostEqual(line.coords[0][0], 0, places=6)
        self.assertAlmostEqual(line.coords[0][1], 0, places=6)
        self.assertAlmostEqual(line.coords[-1][0], 15, places=6)
        self.assertAlmostEqual(line.coords[-1][1], 5, places=6)

    def test_insert_transform(self):
        doc, msp = new_doc()
        blk = doc.blocks.new('B')
        blk.add_line((0, 0), (1, 0))
        msp.add_blockref('B', (10, 5), dxfattribs={'xscale': -2, 'yscale': 2, 'rotation': 90})
        (line,) = dxf_to_shapes(doc)
        (x0, y0), (x1, y1) = line.coords
        self.assertAlmostEqual(x0, 10, places=6)
        self.assertAlmostEqual(y0, 5, places=6)
        self.assertAlmostEqual(x1, 10, places=6)
        self.assertAlmostEqual(y1, 3, places=6)

    def test_bad_entity_does_not_stop(self):
        doc, msp = new_doc()
        msp.add_line((0, 0), (1, 0))
        bad = msp.add_circle((0, 0), 1)
        bad.dxf.radius = 0  # degenerate
        msp.add_polyline2d([(0, 0)])  # single vertex
        msp.add_blockref('MISSING', (0, 0))
        msp.add_line((0, 5), (1, 5))
        shapes = dxf_to_shapes(doc)
        self.assertEqual(len([s for s in shapes if s.geom_type == 'LineString']), 2)

    @unittest.skipUnless(dxf_text.TEXT_RENDERING_AVAILABLE, 'text rendering unavailable')
    def test_text_modes(self):
        doc, msp = new_doc()
        msp.add_text('ABC', dxfattribs={'height': 2.5})
        stroke = dxf_to_shapes(doc, 'stroke')
        outline = dxf_to_shapes(doc, 'outline')
        self.assertTrue(stroke and all(s.geom_type == 'LineString' for s in stroke))
        self.assertTrue(outline and all(s.geom_type == 'Polygon' for s in outline))
        self.assertEqual(dxf_to_shapes(doc, 'none'), [])
        self.assertEqual(text_entity_to_shapes(list(msp)[0], 'none'), [])
        self.assertTrue(all(math.isfinite(v) for g in outline for v in g.bounds))

    @staticmethod
    def _square(x, y, size):
        return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]

    def _hatch(self, msp, *paths):
        hatch = msp.add_hatch()
        for pts in paths:
            hatch.paths.add_polyline_path(pts, is_closed=True)
        return hatch

    def test_hatch_hole(self):
        doc, msp = new_doc()
        self._hatch(msp, self._square(0, 0, 10), self._square(2, 2, 4))
        shapes = dxf_to_shapes(doc)
        self.assertEqual(len(shapes), 1)
        self.assertEqual(len(shapes[0].interiors), 1)
        self.assertAlmostEqual(shapes[0].area, 100 - 16, places=6)

    def test_invisible_entity_skipped(self):
        doc, msp = new_doc()
        msp.add_line((0, 0), (1, 0), dxfattribs={'invisible': 1})
        msp.add_line((0, 1), (1, 1))
        shapes = dxf_to_shapes(doc)
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0].bounds[1], 1)

    def test_off_and_frozen_layers_skipped(self):
        doc, msp = new_doc()
        doc.layers.add('OFF').off()
        doc.layers.add('FROZEN').freeze()
        doc.layers.add('ON')
        for name in ('OFF', 'FROZEN', 'ON'):
            msp.add_line((0, 0), (1, 0), dxfattribs={'layer': name})
        doc.blocks.new('B').add_line((0, 5), (1, 5))  # layer '0': takes the layer of the INSERT
        msp.add_blockref('B', (0, 0), dxfattribs={'layer': 'FROZEN'})
        msp.add_blockref('B', (0, 10), dxfattribs={'layer': 'ON'})
        shapes = dxf_to_shapes(doc)
        self.assertEqual(sorted(s.bounds[1] for s in shapes), [0, 15])

    @unittest.skipUnless(dxf_text.TEXT_RENDERING_AVAILABLE, 'text rendering unavailable')
    def test_mtext_modes_and_lines(self):
        doc, msp = new_doc()
        one = msp.add_mtext('Hello', dxfattribs={'insert': (10, 20), 'char_height': 2.5})
        two = msp.add_mtext('Hello\\PWorld', dxfattribs={'insert': (10, 20), 'char_height': 2.5})
        for mode, kind in (('stroke', 'LineString'), ('outline', 'Polygon')):
            boxes = []
            for entity in (one, two):
                shapes = text_entity_to_shapes(entity, mode)
                self.assertTrue(shapes)
                self.assertTrue(all(s.geom_type == kind for s in shapes))
                boxes.append(unary_union(shapes).bounds)
            self.assertGreaterEqual(boxes[0][0], 9.9)
            self.assertGreater(boxes[1][3] - boxes[1][1], 1.5 * (boxes[0][3] - boxes[0][1]))
            self.assertLess(boxes[1][3] - boxes[1][1], 12)
            self.assertTrue(dxf_to_shapes(doc, mode))
        self.assertEqual(dxf_to_shapes(doc, 'none'), [])
    def test_circles_and_points_follow_the_circle_preferences(self):
        doc, msp = new_doc()
        doc.header['$INSUNITS'] = 4
        msp.add_point((0, 0))
        msp.add_circle((10, 0), 0.5)
        msp.add_circle((50, 0), 20)
        fixed = {'geometry_circle_steps_mode': 'fixed', 'geometry_circle_steps': 64}
        point, small, big = dxf_to_shapes(doc, options=fixed)
        self.assertEqual([len(g.exterior.coords) - 1 for g in (point, small)], [64, 64])
        self.assertGreaterEqual(len(big.exterior.coords) - 1, 64)  # the flattening tolerance may add more
        point, small, big = dxf_to_shapes(doc)
        self.assertLess(len(point.exterior.coords), len(big.exterior.coords))
        self.assertAlmostEqual(big.area, math.pi * 400, delta=0.5)
        self.assertAlmostEqual(point.area, math.pi * 0.01 ** 2, places=4)

    def test_geometry_import_takes_the_first_of_several_tool_diameters(self):
        # the Milling "Tools Dia" preference is a list of diameters ("0.3, 1.0"); float() of it failed the import
        import tempfile
        from camlib import Geometry
        from tests.helpers_objects import make_app_0b
        doc, msp = new_doc()
        msp.add_line((0, 0), (10, 0))
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'a.dxf')
            doc.saveas(path)
            for value, dia in (('0.3, 1.0', 0.3), ('1.0', 1.0), (2.4, 2.4)):
                geo = Geometry(make_app_0b(tools_mill_tooldia=value))
                geo.obj_options = {'name': 'a.dxf'}
                geo.import_dxf_as_geo(path, units='MM')
                self.assertEqual(geo.tools[1]['tooldia'], dia)
                self.assertEqual([g.length for g in geo.tools[1]['solid_geometry']], [10])


if __name__ == '__main__':
    unittest.main()
