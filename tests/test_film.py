"""
Tests for the Film plugin: the transformations, the SVG documents and the export of a film file.
No Qt application is necessary: the plugin is made without its __init__().
"""
import os
import struct
import sys
import tempfile
import unittest
import logging
import xml.etree.ElementTree as ElementTree
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from reportlab.lib.units import mm
from shapely import box, LineString, Point, Polygon, GeometryCollection, MultiPolygon
from shapely.ops import unary_union

from appPlugins.ToolFilm import Film, FilmGen

SVG_NS = '{http://www.w3.org/2000/svg}'


class Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class FakeObject:
    def __init__(self, name, geometry):
        self.kind = 'gerber'
        self.multigeo = False
        self.units = 'MM'
        self.solid_geometry = geometry
        self.obj_options = {'name': name, 'tools_film_color': '#000000'}

    def flatten(self, geometry=None):
        geometry = self.solid_geometry if geometry is None else geometry
        return list(getattr(geometry, 'geoms', [geometry]))

    def bounds(self):
        return self.solid_geometry.bounds


class Value:
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value


def make_film(objects, boundary=0.0, screen_dpi=96.0):
    film = Film.__new__(Film)
    screen = SimpleNamespace(logicalDotsPerInch=lambda: screen_dpi)
    film.app = SimpleNamespace(
        log=logging.getLogger('test_film'),
        options={'global_open_style': True},
        defaults=SimpleNamespace(report_usage=lambda *a: None, get=lambda *a: None),
        inform=Signal(), file_saved=Signal(), file_opened=Signal(),
        qapp=SimpleNamespace(screens=lambda: [screen]),
        collection=SimpleNamespace(get_by_name=lambda name: objects.get(name)),
    )
    film.units = 'MM'
    film.ui = SimpleNamespace(pagesize_combo=Value('A4'), orientation_radio=Value('p'),
                              pagesize={'Bounds': None, 'A4': (210 * mm, 297 * mm)},
                              png_dpi_spinner=Value(96), boundary_entry=Value(boundary))
    return film


def colored_elements(document):
    root = ElementTree.fromstring(document)
    return [e for e in root.iter() if e.tag.split('}')[-1] in ('path', 'polyline', 'circle')]


class TestTransformGeometry(unittest.TestCase):

    def test_object_keeps_its_place_in_the_box(self):
        frame = box(0, 0, 100, 50)
        part = box(10, 10, 20, 20)
        new_frame, new_part = Film.transform_geometry([frame, part], scale_factor_x=2.0, scale_factor_y=2.0,
                                                      scale_type=1, mirror='y')
        self.assertEqual(new_frame.bounds, (-50.0, -25.0, 150.0, 75.0))
        # the part is scaled and mirrored around the center of the frame, not around its own center
        self.assertEqual(new_part.bounds, (110.0, -5.0, 130.0, 15.0))

    def test_length_scale_of_a_flat_box_does_not_divide_by_zero(self):
        line = LineString([(0, 0), (0, 10)])
        new_line, = Film.transform_geometry([line], scale_factor_x=5.0, scale_factor_y=5.0, scale_type=0)
        self.assertEqual(new_line.bounds, (0.0, -2.5, 0.0, 12.5))

    def test_skew_by_length_from_bottomleft(self):
        frame = box(0, 0, 10, 10)
        new_frame, = Film.transform_geometry([frame], skew_factor_x=10.0, skew_reference='bottomleft', skew_type=0)
        self.assertAlmostEqual(new_frame.bounds[2], 20.0)


class TestSvgDocument(unittest.TestCase):

    def test_single_polygon_gets_the_film_color(self):
        film = make_film({})
        svg_geo = Film.create_svg_geometry(box(0, 0, 10, 10), 0.01)
        doc = film.create_positive_svg(svg_geo, (0, 0, 10, 10), 1.0, '#123456', 1.0, 'mm')
        elements = colored_elements(doc)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].get('fill'), '#123456')
        self.assertEqual(elements[0].get('opacity'), '1.0')
        self.assertNotIn("b'", doc)

    def test_nested_collection_is_colored_and_lines_stay_unfilled(self):
        film = make_film({})
        geo = GeometryCollection([MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]), LineString([(0, 5), (5, 5)])])
        doc = film.create_positive_svg(Film.create_svg_geometry(geo, 0.01), geo.bounds, 0.0, '#000000', 1.0, 'mm')
        elements = colored_elements(doc)
        self.assertEqual(len(elements), 3)
        for elem in elements:
            self.assertEqual(elem.get('stroke'), '#000000')
            self.assertIn(elem.get('fill'), ('#000000', 'none'))
        self.assertEqual([e.get('fill') for e in elements if e.tag.endswith('polyline')], ['none'])

    def test_view_box_of_negative_coordinates(self):
        film = make_film({})
        doc = film.create_positive_svg(Film.create_svg_geometry(box(-5, -8, -1, -2), 0.01), (-5, -8, -1, -2),
                                       1.0, '#000000', 1.0, 'mm')
        root = ElementTree.fromstring(doc)
        self.assertEqual([float(v) for v in root.get('viewBox').split()], [-6.0, 1.0, 6.0, 8.0])

    def test_negative_box_polygon_gets_the_margin(self):
        film = make_film({})
        frame = box(0, 0, 10, 10)
        doc = film.create_negative_svg(Film.create_svg_geometry(box(2, 2, 4, 4), 0.01), frame.bounds, False, frame,
                                       False, 1.0, '#000000', 1.0, 'mm')
        root = ElementTree.fromstring(doc)
        background = next(e for e in root.iter() if e.get('id') == 'neg_rect')
        points = [float(v) for v in background.get('points').replace(',', ' ').split()]
        self.assertEqual((min(points), max(points)), (-1.0, 11.0))
        self.assertEqual(colored_elements(doc)[0].get('fill'), '#FFFFFF')


class TestExportFilm(unittest.TestCase):

    def export(self, objects, obj_name, box_name, negative):
        film = make_film(objects, boundary=0.5)
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, 'film.svg')
            if negative:
                ret = film.export_negative_handler(obj_name, box_name, filename, 0.5, use_thread=False)
            else:
                ret = film.export_positive_handler(obj_name, box_name, filename, use_thread=False)
            with open(filename) as f:
                document = f.read()
        self.assertIsNone(ret)
        self.assertEqual(film.app.file_saved.calls, [('SVG', filename)])
        return ElementTree.fromstring(document)

    def test_positive_film_with_the_object_as_box(self):
        part = FakeObject('part', unary_union([box(0, 0, 10, 10), box(20, 0, 30, 10)]))
        root = self.export({'part': part}, 'part', 'part', negative=False)
        self.assertEqual(root.get('width'), '31.0mm')
        self.assertEqual(len(colored_elements(ElementTree.tostring(root, encoding='unicode'))), 2)

    def test_negative_film_with_an_other_box(self):
        part = FakeObject('part', Polygon([(10, 10), (20, 10), (20, 20)]))
        frame = FakeObject('frame', box(0, 0, 40, 40))
        root = self.export({'part': part, 'frame': frame}, 'part', 'frame', negative=True)
        self.assertEqual(root.get('width'), '41.0mm')
        self.assertTrue(any(e.get('id') == 'neg_rect' for e in root.iter()))

    def test_png_and_pdf_files_are_written(self):
        part = FakeObject('part', box(1, 1, 10, 10))
        film = make_film({'part': part})
        film.ui.pagesize_combo = Value('Bounds')
        with tempfile.TemporaryDirectory() as tmp:
            for ftype in ('png', 'pdf'):
                filename = os.path.join(tmp, 'film.' + ftype)
                film.export_negative_handler('part', 'part', filename, 1.0, use_thread=False, ftype=ftype)
                self.assertGreater(os.path.getsize(filename), 0, ftype)

    def png_size(self, filename):
        with open(filename, 'rb') as f:
            header = f.read(24)
        return struct.unpack('>II', header[16:24])

    def test_png_renders_the_requested_dpi_with_the_border(self):
        part = FakeObject('part', box(0, 0, 100, 100))
        for dpi, expected in ((96, 454), (192, 907)):
            film = make_film({'part': part}, boundary=10.0)
            film.ui.png_dpi_spinner = Value(dpi)
            with tempfile.TemporaryDirectory() as tmp:
                filename = os.path.join(tmp, 'film.png')
                film.export_positive_handler('part', 'part', filename, use_thread=False, ftype='png')
                width, height = self.png_size(filename)
            self.assertAlmostEqual(width, expected, delta=2, msg='%s DPI width' % dpi)
            self.assertEqual(width, height, '%s DPI is square' % dpi)

    def test_png_rejects_an_invalid_dpi(self):
        part = FakeObject('part', box(0, 0, 10, 10))
        film = make_film({'part': part}, boundary=1.0)
        film.ui.png_dpi_spinner = Value(0)
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, 'film.png')
            film.export_positive_handler('part', 'part', filename, use_thread=False, ftype='png')
            self.assertFalse(os.path.exists(filename))
        self.assertEqual(film.app.file_saved.calls, [])
        self.assertTrue(any('ERROR' in call[0] for call in film.app.inform.calls))

    def test_pdf_validation_uses_the_transformed_artwork(self):
        part = FakeObject('part', box(10, 10, 20, 20))
        frame = FakeObject('frame', box(0, 0, 100, 100))
        film = make_film({'part': part, 'frame': frame})
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, 'film.pdf')
            film.export_positive_handler('part', 'frame', filename, use_thread=False, ftype='pdf',
                                         scale_factor_x=2.0, scale_factor_y=2.0, scale_type=1)
            self.assertFalse(os.path.exists(filename))
        self.assertEqual(film.app.file_saved.calls, [])
        self.assertTrue(any('ERROR' in call[0] for call in film.app.inform.calls))

    def test_pdf_accepts_artwork_transformed_onto_the_page(self):
        part = FakeObject('part', box(230, 10, 240, 20))
        frame = FakeObject('frame', box(0, 0, 100, 100))
        film = make_film({'part': part, 'frame': frame})
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, 'film.pdf')
            film.export_positive_handler('part', 'frame', filename, use_thread=False, ftype='pdf',
                                         scale_factor_x=0.5, scale_factor_y=0.5, scale_type=1)
            self.assertGreater(os.path.getsize(filename), 0)
        self.assertEqual(film.app.file_saved.calls, [('SVG', filename)])


    def test_missing_object_returns_a_message(self):
        film = make_film({})
        self.assertIn('Could not retrieve', film.export_positive_handler('none', 'none', 'x.svg', use_thread=False))


class TestPadCenterPunch(unittest.TestCase):
    """FilmGen.punch_geometry() on the apertures as the Gerber parser stores them."""

    @staticmethod
    def board():
        track = LineString([(5, 0), (15, 0)])
        tools = {
            # a KiCad rounded rectangle pad, 1.0 x 1.5: an AM macro aperture has no width and height
            10: {'type': 'AM', 'macro': None, 'modifiers': [],
                 'geometry': [{'solid': box(-0.35, -0.6, 0.35, 0.6).buffer(0.15), 'follow': Point(0, 0)}]},
            11: {'type': 'C', 'size': 1.7, 'geometry': [{'solid': Point(20, 0).buffer(0.85), 'follow': Point(20, 0)}]},
            # an aperture used only for a track, thinner than the punch
            12: {'type': 'C', 'size': 0.25, 'geometry': [{'solid': track.buffer(0.125), 'follow': track}]},
        }
        return unary_union([elem['solid'] for tool in tools.values() for elem in tool['geometry']]), tools

    def test_macro_pads_are_punched_and_track_apertures_are_not_checked(self):
        solid, tools = self.board()
        punched = FilmGen.punch_geometry(solid, tools, punch_size=0.6)
        self.assertAlmostEqual(solid.area - punched.area, 2 * Point(0, 0).buffer(0.3).area, places=6)
        self.assertFalse(punched.contains(Point(0, 0)) or punched.contains(Point(20, 0)))
        self.assertTrue(punched.contains(Point(10, 0)))

    def test_a_punch_as_wide_as_a_pad_is_refused(self):
        solid, tools = self.board()
        with self.assertRaises(FilmGen.PunchTooBigError):
            FilmGen.punch_geometry(solid, tools, punch_size=1.0)


if __name__ == '__main__':
    unittest.main()
