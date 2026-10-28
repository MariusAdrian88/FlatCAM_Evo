"""Tests of the clean-room SVG importer (appParsers/SVG). Run: python tests/test_svg_import.py"""
import io
import math
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shapely
from lxml import etree

from appParsers.SVG import SvgImportError, svg_to_shapes

DOC = 'width="100mm" height="50mm" viewBox="0 0 200 100"'


def svg(body, attrs=DOC):
    return '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" %s>%s</svg>' % (
        attrs, body)


def run(body, attrs=DOC, **kw):
    return svg_to_shapes(svg(body, attrs), **kw)


def raw(body, **kw):
    """User-unit import without flip."""
    kw.setdefault('user_units', True)
    kw.setdefault('flip', False)
    attrs = 'viewBox="0 0 20 20"' if kw.pop('attrs_vb', False) else ''
    return svg_to_shapes(svg(body, attrs), **kw)


def total_area(geoms):
    return sum(g.area for g in geoms)


def bounds(geoms):
    return shapely.union_all(geoms).bounds


class TestShapes(unittest.TestCase):
    def test_rect_in_mm(self):
        g = run('<rect x="10" y="10" width="40" height="20"/>')
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0].area, 200.0, places=6)
        for a, b in zip(g[0].bounds, (5.0, 35.0, 25.0, 45.0)):
            self.assertAlmostEqual(a, b, places=6)

    def test_flip_off_and_on(self):
        on = bounds(run('<rect x="10" y="10" width="40" height="20"/>'))
        off = bounds(run('<rect x="10" y="10" width="40" height="20"/>', flip=False))
        self.assertAlmostEqual(on[1], 35.0)
        self.assertAlmostEqual(off[1], 5.0)

    def test_circle_ellipse(self):
        c = run('<circle cx="100" cy="50" r="20"/>')
        self.assertAlmostEqual(c[0].area, math.pi * 400 * 0.25, delta=0.01 * c[0].area)
        e = run('<ellipse cx="100" cy="50" rx="30" ry="10"/>')
        self.assertAlmostEqual(e[0].area, math.pi * 300 * 0.25, delta=0.01 * e[0].area)
        self.assertAlmostEqual(e[0].centroid.x, 50.0, places=4)

    def test_units(self):
        for attrs in ('width="96px" height="96px"', 'width="1in" height="1in" viewBox="0 0 96 96"',
                      'width="72pt" height="72pt" viewBox="0 0 10 10"'):
            vb = 10.0 if 'pt' in attrs else 96.0
            g = run('<rect width="%s" height="%s"/>' % (vb, vb), attrs)
            self.assertAlmostEqual(g[0].area, 25.4 ** 2, places=4, msg=attrs)

    def test_missing_size_uses_viewbox(self):
        g = run('<rect width="96" height="96"/>', 'viewBox="0 0 96 96"')
        self.assertAlmostEqual(g[0].area, 25.4 ** 2, places=4)
        g = run('<rect width="96" height="96"/>', 'width="100%" height="100%" viewBox="0 0 96 96"')
        self.assertAlmostEqual(g[0].area, 25.4 ** 2, places=4)

    def test_preserve_aspect_ratio(self):
        body = '<rect width="100" height="50"/>'
        meet = bounds(run(body, 'width="100mm" height="100mm" viewBox="0 0 100 50"', flip=False))
        self.assertAlmostEqual(meet[3] - meet[1], 50.0)
        self.assertAlmostEqual(meet[1], 25.0)
        none = bounds(run(body, 'width="100mm" height="100mm" viewBox="0 0 100 50" '
                                'preserveAspectRatio="none"', flip=False))
        self.assertAlmostEqual(none[3] - none[1], 100.0)
        sl = bounds(run(body, 'width="100mm" height="100mm" viewBox="0 0 100 50" '
                              'preserveAspectRatio="xMinYMin slice"', flip=False))
        self.assertAlmostEqual(sl[2] - sl[0], 200.0)
        self.assertAlmostEqual(sl[0], 0.0)
        mx = bounds(run('<rect width="50" height="50"/>', 'width="100mm" height="50mm" viewBox="0 0 50 50" '
                                                          'preserveAspectRatio="xMaxYMid meet"', flip=False))
        self.assertAlmostEqual(mx[0], 50.0)

class TestPath(unittest.TestCase):
    def one(self, d, **kw):
        g = raw('<path d="%s"/>' % d, **kw)
        self.assertEqual(len(g), 1, d)
        return g[0]

    def test_lines_absolute_relative(self):
        self.assertAlmostEqual(self.one('M10 10 H30 V30 H10 Z').area, 400.0)
        self.assertAlmostEqual(self.one('m10 10 h20 v20 h-20 z').area, 400.0)
        self.assertAlmostEqual(self.one('M0 0 L10 0 20 0 20 10 0 10z').area, 200.0)
        self.assertAlmostEqual(self.one('m0 0 l10 0 10 0 0 10 -20 0z').area, 200.0)

    def test_arc_semicircle_and_flags(self):
        a = self.one('M0 0 A10 10 0 0 1 20 0 Z', resolution=360)
        self.assertAlmostEqual(a.area, math.pi * 100 / 2, delta=0.1)
        b = self.one('M0 0 a10 10 0 0120 0 z', resolution=360)
        self.assertAlmostEqual(b.area, math.pi * 100 / 2, delta=0.1)
        big = self.one('M0 0 A10 10 0 1 1 10 10 Z', resolution=360)
        small = self.one('M0 0 A10 10 0 0 1 10 10 Z', resolution=360)
        self.assertGreater(big.area, small.area)
        self.assertAlmostEqual(big.area + small.area, math.pi * 100 + 0.0, delta=0.5)

    def test_multiple_subpaths_and_implicit_lineto(self):
        g = raw('<path d="M0 0 10 0 10 10 0 10z M20 0 30 0 30 10 20 10z"/>')
        self.assertEqual(len(g), 2)
        self.assertAlmostEqual(total_area(g), 200.0)

class TestFillRules(unittest.TestCase):
    SAME = 'M0 0H20V20H0Z M5 5H15V15H5Z'
    OPPOSITE = 'M0 0H20V20H0Z M5 5V15H15V5Z'

    def area(self, d, rule):
        return total_area(raw('<path d="%s" fill-rule="%s"/>' % (d, rule)))

    def test_nonzero_same_direction_fills(self):
        self.assertAlmostEqual(self.area(self.SAME, 'nonzero'), 400.0)

    def test_evenodd_makes_hole(self):
        self.assertAlmostEqual(self.area(self.SAME, 'evenodd'), 300.0)

    def test_figure_eight(self):
        for rule in ('nonzero', 'evenodd'):
            self.assertAlmostEqual(self.area('M0 0 L10 10 L10 0 L0 10 Z', rule), 50.0)

class TestPaint(unittest.TestCase):
    def test_gerber_only_polygons(self):
        body = ('<circle cx="10" cy="10" r="5"/><path d="M0 0 H10" stroke="black" stroke-width="2"/>'
                '<path d="M0 0 H10 V10" fill="none"/><line x1="0" y1="0" x2="5" y2="5" stroke="red"/>')
        g = raw(body, object_type='gerber')
        self.assertTrue(g)
        self.assertTrue(all(p.geom_type == 'Polygon' for p in g))

    def test_stroke_only_buffer(self):
        g = raw('<path d="M0 0 H10" stroke="black" stroke-width="2" fill="none"/>', object_type='gerber')
        self.assertAlmostEqual(g[0].area, 20.0, places=6)

    def test_fill_and_stroke_union(self):
        g = raw('<rect width="10" height="10" stroke="black" stroke-width="2"/>', object_type='gerber')
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0].bounds[0], -1.0)
        self.assertGreater(g[0].area, 140.0)

    def test_open_path_without_stroke_gives_nothing_in_gerber(self):
        self.assertEqual(raw('<path d="M0 0 H10" fill="none"/>', object_type='gerber'), [])

class TestStructure(unittest.TestCase):
    def test_nested_group_rotate_translate(self):
        g = raw('<g transform="translate(10 0)"><g transform="rotate(90)"><rect width="10" height="5"/></g></g>')
        for a, b in zip(g[0].bounds, (5.0, 0.0, 10.0, 10.0)):
            self.assertAlmostEqual(a, b, places=6)

    def test_defs_not_drawn_and_use(self):
        body = ('<defs><rect id="r" width="10" height="10"/></defs>'
                '<use xlink:href="#r" x="20" y="5"/><use href="#r" x="40"/>')
        g = raw(body)
        self.assertEqual(len(g), 2)
        self.assertEqual(sorted(p.bounds[0] for p in g), [20.0, 40.0])

class TestStyle(unittest.TestCase):
    def test_css_class_id_tag_rules(self):
        body = ('<style>rect{fill:none;stroke:#000;stroke-width:2} .thick{stroke-width:4} #one{stroke-width:6}'
                '</style><rect class="thick" width="10" height="10"/><rect id="one" width="10" height="10"/>'
                '<rect width="10" height="10"/>')
        g = raw(body, object_type='gerber')
        self.assertEqual(len(g), 3)
        areas = sorted(p.area for p in g)
        self.assertTrue(areas[0] < areas[1] < areas[2])
        self.assertAlmostEqual(areas[0], 12 * 12 - 8 * 8, places=3)

    def test_inline_style_beats_attribute_and_css(self):
        body = ('<style>rect{fill:none}</style>'
                '<rect width="10" height="10" fill="red" style="fill:blue"/>')
        self.assertEqual(len(raw(body, object_type='gerber')), 1)
        body = ('<style>rect{fill:none !important}</style><rect width="10" height="10" style="fill:blue"/>')
        self.assertEqual(raw(body, object_type='gerber'), [])

class TestModern(unittest.TestCase):
    def test_inputs(self):
        text = svg('<rect width="10" height="10"/>', '')
        self.assertEqual(len(svg_to_shapes(io.StringIO(text), user_units=True)), 1)
        self.assertEqual(len(svg_to_shapes(io.BytesIO(text.encode()), user_units=True)), 1)
        self.assertEqual(len(svg_to_shapes(text.encode(), user_units=True)), 1)
        self.assertEqual(len(svg_to_shapes('<?xml version="1.0" encoding="UTF-8"?>\n' + text, user_units=True)), 1)
        root = etree.fromstring(text.encode())
        self.assertEqual(len(svg_to_shapes(root, user_units=True)), 1)
        self.assertEqual(len(svg_to_shapes(etree.ElementTree(root), user_units=True)), 1)
        import xml.etree.ElementTree as ET
        self.assertEqual(len(svg_to_shapes(ET.fromstring(text), user_units=True)), 1)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'a.svg')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(text)
            self.assertEqual(len(svg_to_shapes(p, user_units=True)), 1)
            self.assertEqual(len(svg_to_shapes(__import__('pathlib').Path(p), user_units=True)), 1)

    def test_errors(self):
        for bad in ('<svg><rect></svg>', '<html/>', '', 'not a file at all.svg', b'\x00\x01'):
            with self.assertRaises(SvgImportError, msg=repr(bad)):
                svg_to_shapes(bad)
        with self.assertRaises(SvgImportError):
            svg_to_shapes(12345)

    def test_geometry_import_takes_the_first_of_several_tool_diameters(self):
        # the Milling "Tools Dia" preference is a list of diameters ("0.3, 1.0"); float() of it failed the import
        from camlib import Geometry
        from tests.helpers_objects import make_app_0b
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'a.svg')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(svg('<rect x="10" y="10" width="40" height="20" fill="none" stroke="black"/>'))
            for value, dia in (('0.3, 1.0', 0.3), ('1.0', 1.0), (2.4, 2.4)):
                geo = Geometry(make_app_0b(tools_mill_tooldia=value))
                geo.obj_options = {'name': 'a.svg'}
                geo.import_svg(p, 'geometry', units='MM')
                self.assertEqual(geo.tools[1]['tooldia'], dia)
                self.assertTrue(geo.tools[1]['solid_geometry'])


def _fonts_available():
    try:
        g = svg_to_shapes(svg('<text x="0" y="20" font-size="20">H</text>', ''), user_units=True)
        return bool(g)
    except Exception:
        return False


class TestText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _fonts_available():
            raise unittest.SkipTest('no usable font for ParseFont')

    def text(self, body, **kw):
        kw.setdefault('user_units', True)
        kw.setdefault('flip', False)
        return svg_to_shapes(svg(body, ''), **kw)

    def test_text_renders_and_height_follows_font_size(self):
        h10 = bounds(self.text('<text x="0" y="50" font-size="10">H</text>'))
        h20 = bounds(self.text('<text x="0" y="50" font-size="20">H</text>'))
        height10, height20 = h10[3] - h10[1], h20[3] - h20[1]
        self.assertTrue(6.0 < height10 < 8.5, height10)
        self.assertAlmostEqual(height20 / height10, 2.0, delta=0.05)
        # baseline at y=50, glyph grows upwards (smaller y) without flip
        self.assertAlmostEqual(h10[3], 50.0, delta=0.3)

def bench_doc():
    """20 000 elements: rect, circle, bezier path and rotated polygon, 5000 each."""
    parts = []
    for i in range(20000):
        x, y = (i % 200) * 5 + 1, (i // 200) * 10 + 1
        kind = i % 4
        if kind == 0:
            parts.append('<rect x="%d" y="%d" width="4" height="3"/>' % (x, y))
        elif kind == 1:
            parts.append('<circle cx="%d" cy="%d" r="1.5"/>' % (x, y))
        elif kind == 2:
            parts.append('<path d="M%d %d c 3 -6 9 -6 12 0 s 9 6 12 0 z"/>' % (x, y))
        else:
            parts.append('<polygon points="%d,%d %d,%d %d,%d" transform="rotate(15 %d %d)"/>' % (
                x, y, x + 3, y, x, y + 3, x, y))
    return svg(''.join(parts), 'width="1000mm" height="1000mm" viewBox="0 0 1000 1000"')


    def test_steps_function_follows_the_radius(self):
        from camlib import object_circle_steps
        steps = lambda r: object_circle_steps(None, 'geometry', r, 'MM')
        small, big = run('<circle cx="20" cy="20" r="0.5"/><circle cx="100" cy="50" r="20"/>', resolution=steps)
        # viewBox 200 wide on 100 mm: 1 user unit = 0.5 mm
        self.assertEqual(len(small.exterior.coords) - 1, steps(0.25))
        self.assertEqual(len(big.exterior.coords) - 1, steps(10.0))
        self.assertLess(len(small.exterior.coords), len(big.exterior.coords))
        fixed, = run('<circle cx="20" cy="20" r="0.5"/>', resolution=64)
        self.assertEqual(len(fixed.exterior.coords) - 1, 64)


if __name__ == '__main__':
    unittest.main()
