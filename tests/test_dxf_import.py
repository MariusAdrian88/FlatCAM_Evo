"""Tests for the appParsers.DXF importer; run directly: python tests/test_dxf_import.py"""

import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from shapely.ops import unary_union

from appParsers.DXF import dxf_to_shapes, text_entity_to_shapes
from appParsers.DXF import text as dxf_text
from appParsers.ParseGerber import Gerber
from camlib import Geometry


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

    # --- units scaling: the import scales DXF geometry from $INSUNITS to the app units ---

    @staticmethod
    def _units_doc(insunits):
        doc, msp = new_doc()
        doc.header['$INSUNITS'] = insunits
        return doc, msp

    def _save(self, doc):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, 'in.dxf')
        doc.saveas(path)
        return path

    @staticmethod
    def _fake_app():
        return SimpleNamespace(log=Mock(), app_units='MM', decimals=4)

    def _import_geo(self, path, units):
        obj = Geometry.__new__(Geometry)
        obj.app = self._fake_app()
        obj._app_option = lambda key, *a, **kw: '0.1'
        obj.decimals = 4
        obj.obj_options = {'name': 'g'}
        obj.tools = {}
        obj.solid_geometry = None
        obj.import_dxf_as_geo(path, units=units)
        return unary_union(obj.solid_geometry)

    def _import_gerber(self, path, units):
        obj = Gerber.__new__(Gerber)
        obj.app = self._fake_app()
        obj.multigeo = False
        obj.solid_geometry = None
        obj.follow_geometry = None
        obj.tools = {}
        obj.import_dxf_as_gerber(path, units=units)
        return unary_union([e['solid'] for e in obj.tools[0]['geometry']])

    def test_geometry_import_inch_dxf_into_mm_app(self):
        doc, msp = self._units_doc(1)
        msp.add_line((0, 0), (1, 0))
        geo = self._import_geo(self._save(doc), 'MM')
        self.assertAlmostEqual(geo.bounds[0], 0, places=9)
        self.assertAlmostEqual(geo.bounds[2], 25.4, places=9)

    def test_gerber_import_inch_dxf_into_mm_app(self):
        doc, msp = self._units_doc(1)
        msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
        minx, _, maxx, _ = self._import_gerber(self._save(doc), 'MM').bounds
        self.assertAlmostEqual(maxx - minx, 254, places=6)

    def test_geometry_import_ignores_units_that_make_the_drawing_huge(self):
        doc, msp = self._units_doc(6)
        msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
        minx, _, maxx, _ = self._import_geo(self._save(doc), 'MM').bounds
        self.assertAlmostEqual(maxx - minx, 10, places=9)


if __name__ == '__main__':
    unittest.main()
