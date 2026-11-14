"""
Tests for Film geometry, retained artifacts and the GUI Generate/preview/Save workflow.
Geometry checks use lightweight sources; controller checks use a Qt event loop.
"""
import contextlib
import os
import sys
import tempfile
import unittest
import logging
from io import BytesIO
from unittest.mock import patch
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
        self.history_uid = name
        self.kind = 'gerber'
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
        artifact = FilmGen.generate_film(self.snapshot(
            objects[obj_name].solid_geometry, objects[box_name].solid_geometry, negative=negative))
        return ElementTree.fromstring(artifact['data'])

    @staticmethod
    def snapshot(geometry, frame, negative=False, file_type='svg', margin=0.5, dpi=96):
        return {'geometry': geometry, 'box': frame, 'transform': {}, 'margin': margin,
                'color': '#000000', 'stroke': 0.01, 'negative': negative, 'rounded': False,
                'convex': False, 'units': 'MM', 'format': file_type, 'page_size': None,
                'dpi': dpi, 'name': 'part'}

    def test_positive_film_with_the_object_as_box(self):
        part = FakeObject('part', unary_union([box(0, 0, 10, 10), box(20, 0, 30, 10)]))
        root = self.export({'part': part}, 'part', 'part', negative=False)
        self.assertEqual(root.get('width'), '31.0mm')
        self.assertEqual(len(colored_elements(ElementTree.tostring(root, encoding='unicode'))), 2)


    def test_png_renders_the_requested_dpi_with_the_border(self):
        from PIL import Image
        part = box(0, 0, 100, 100)
        for dpi, expected in ((96, 454), (192, 907)):
            artifact = FilmGen.generate_film(self.snapshot(part, part, file_type='png', margin=10, dpi=dpi))
            with Image.open(BytesIO(artifact['data'])) as image:
                width, height = image.size
                file_dpi = image.info.get('dpi')
            self.assertAlmostEqual(width, expected, delta=2, msg='%s DPI width' % dpi)
            self.assertEqual(width, height, '%s DPI is square' % dpi)
            # the file records its DPI: a viewer or a printer shows the 120 mm film at 120 mm
            self.assertIsNotNone(file_dpi, '%s DPI is in the file' % dpi)
            self.assertAlmostEqual(width / file_dpi[0] * 25.4, 120.0, delta=0.5, msg='%s DPI size' % dpi)


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


class TestRetainedFilm(unittest.TestCase):
    def test_svg_project_paint_keeps_holes_strokes_colors_and_outside_artwork(self):
        source = GeometryCollection([
            Polygon([(2, 2), (8, 2), (8, 8), (2, 8)], [[(4, 4), (6, 4), (6, 6), (4, 6)]]),
            LineString([(-3, 5), (1, 5)]), Point(12, 5)])
        snapshot = TestExportFilm.snapshot(source, box(0, 0, 10, 10), negative=True, margin=0)
        snapshot.update(color='#123456', stroke=0.2)
        artifact = FilmGen.generate_film(snapshot)
        background, artwork = artifact['geometry']
        self.assertTrue(background.contains(Point(5, 5)))
        self.assertTrue(artwork.contains(Point(2.1, 5)))
        self.assertFalse(artwork.contains(Point(5, 5)))
        self.assertTrue(artwork.contains(Point(-2, 5.19)))
        self.assertFalse(artwork.contains(Point(-2, 5.21)))
        self.assertFalse(artwork.contains(Point(-3.01, 5)))
        self.assertTrue(artwork.contains(Point(12.69, 5)))
        self.assertEqual(background.intersection(artwork).area, 0)
        self.assertEqual([layer['color'] for layer in artifact['film']['layers']], ['#123456', '#EDCBA9'])
        self.assertEqual(artifact['film'], {'layers': [{'color': '#123456'}, {'color': '#EDCBA9'}]})
        sharp = TestExportFilm.snapshot(LineString([(0, -1), (10, 0), (0, 1)]), box(0, -3, 15, 3))
        sharp['stroke'] = 0.5
        face, = FilmGen.generate_film(sharp)['geometry']
        self.assertFalse(face.contains(Point(11, 0)), 'SVG bevels joins exceeding its miter limit')

    def test_svg_project_faces_match_transformed_and_punched_svg_pixels(self):
        from PIL import Image
        import numpy as np
        ElementTree.register_namespace('', 'http://www.w3.org/2000/svg')
        source = GeometryCollection([box(1, 1, 9, 9), LineString([(-2, 5), (12, 5)])])
        for negative, punch in ((False, True), (True, False)):
            with self.subTest(negative=negative, punch=punch):
                snapshot = TestExportFilm.snapshot(source, box(0, 0, 10, 10), negative, margin=1, dpi=508)
                snapshot.update(color='#123456', stroke=0.15, rounded=True,
                                transform={'scale_factor_x': 1.1, 'scale_type': 1, 'mirror': 'y'},
                                punch=punch, holes=Point(5, 5).buffer(1), tools={})
                artifact = FilmGen.generate_film(snapshot)
                root = ElementTree.fromstring(artifact['svg'])
                group = root.find(SVG_NS + 'g')
                group.clear()
                group.set('transform', 'scale(1,-1)')
                for geometry, layer in zip(artifact['geometry'], artifact['film']['layers']):
                    element = ElementTree.fromstring(geometry.svg())
                    for leaf in element.iter():
                        leaf.set('fill', layer['color'])
                        leaf.set('stroke', 'none')
                        leaf.set('opacity', '1')
                    group.append(element)
                project_svg = ElementTree.tostring(root, encoding='unicode')
                images = []
                for svg in (artifact['svg'], project_svg):
                    data = FilmGen.render_svg_png(svg, None, snapshot['dpi'])
                    with Image.open(BytesIO(data)) as image:
                        images.append(np.asarray(image.convert('RGB')).astype(int))
                # Independent SVG paint vs expanded faces; only antialiased shared edges can differ.
                different = np.max(np.abs(images[0] - images[1]), axis=2) > 12
                self.assertLess(np.mean(different), 0.01)

    def test_positive_negative_and_punched_pdf_and_png_keep_artwork_pixels(self):
        from PIL import Image
        import pypdfium2
        frame, source = box(0, 0, 10, 10), box(2, 2, 8, 8)
        for file_type in ('png', 'pdf'):
            for negative, punched in ((False, False), (True, False), (False, True)):
                with self.subTest(format=file_type, negative=negative, punched=punched):
                    snapshot = TestExportFilm.snapshot(source, frame, negative, file_type, margin=1, dpi=254)
                    if punched:
                        snapshot.update(punch=True, holes=Point(5, 5).buffer(1), tools={})
                    data = FilmGen.generate_film(snapshot)['data']
                    if file_type == 'png':
                        with Image.open(BytesIO(data)) as image:
                            result = image.convert('RGB')
                    else:
                        with pypdfium2.PdfDocument(data) as document:
                            page = document[0]
                            bitmap = page.render(scale=254 / 72)
                            result = bitmap.to_pil().convert('RGB')
                            bitmap.close()
                            page.close()
                    self.assertEqual(result.getpixel((50, 70)), (255, 255, 255) if negative else (0, 0, 0))
                    self.assertEqual(result.getpixel((15, 105)), (0, 0, 0) if negative else (255, 255, 255))
                    self.assertEqual(result.getpixel((60, 60)),
                                     (255, 255, 255) if negative or punched else (0, 0, 0))

    def test_negative_film_box_and_rounded_controls_determine_background_corners(self):
        import pypdfium2
        frame = box(0, 0, 10, 10).buffer(1)
        for convex, rounded, square in ((False, False, True), (False, True, False), (True, False, False)):
            with self.subTest(convex=convex, rounded=rounded):
                snapshot = TestExportFilm.snapshot(box(2, 2, 8, 8), frame, negative=True,
                                                   file_type='pdf', margin=1, dpi=254)
                snapshot.update(convex=convex, rounded=rounded)
                artifact = FilmGen.generate_film(snapshot)
                with pypdfium2.PdfDocument(artifact['data']) as document:
                    page = document[0]
                    bitmap = page.render(scale=254 / 72)
                    result = bitmap.to_pil().convert('RGB')
                    bitmap.close()
                    page.close()
                self.assertEqual(result.getpixel((1, 1)), (0, 0, 0) if square else (255, 255, 255))
                self.assertEqual(result.getpixel((70, 2)), (0, 0, 0))
                snapshot['format'] = 'svg'
                background = FilmGen.generate_film(snapshot)['geometry'][0]
                self.assertEqual(background.contains(Point(-1.75, 11.75)), square)

    def test_finished_pdf_keeps_physical_size_without_a_destination(self):
        import pikepdf
        svg = FilmGen.create_positive_svg(
            FilmGen.create_svg_geometry(box(0, 0, 100, 100), 0.01),
            (0, 0, 100, 100), 10, '#123456', 1, 'mm')
        self.assertEqual(FilmGen.render_film(svg, 'svg', None, 'MM', (0, 0, 100, 100)), svg)
        data = FilmGen.render_film(svg, 'pdf', None, 'MM', (0, 0, 100, 100))
        with pikepdf.Pdf.open(BytesIO(data)) as document:
            width = float(document.pages[0].MediaBox[2])
            self.assertAlmostEqual(width / mm, 120, places=3)

    def test_png_resource_limit_rejects_excessive_and_invalid_dimensions(self):
        svg = '<svg width="100mm" height="100mm"/>'
        self.assertEqual(FilmGen.png_dimensions(svg, 254), (1000, 1000))
        for dpi in (0, float('nan'), 100000):
            with self.subTest(dpi=dpi), self.assertRaises(ValueError):
                FilmGen.png_dimensions(svg, dpi)


class TestFilmGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from PyQt6 import QtCore, QtGui, QtWidgets
        from appGUI.GUIElements import FCDetachableTab2
        from appPlugins.ToolPDFViewer.PDFViewer import PDFViewer
        from defaults import AppDefaults

        class App(QtCore.QObject):
            cleanup = QtCore.pyqtSignal()
            object_status_changed = QtCore.pyqtSignal(object, str, str)
            object_changed = QtCore.pyqtSignal(object)
            proj_selection_changed = QtCore.pyqtSignal(object, object)
            history_changed = QtCore.pyqtSignal(str, str)
            worker_task = QtCore.pyqtSignal(object)
            inform = QtCore.pyqtSignal([str], [str, bool])
            file_saved = QtCore.pyqtSignal(str, str)

        class Defaults(dict):
            def report_usage(self, *args):
                pass

        self.app = App()
        self.app.options = dict(AppDefaults.factory_defaults)
        self.app.defaults = Defaults(AppDefaults.factory_defaults)
        self.app.decimals, self.app.app_units = 4, 'MM'
        self.app.resource_location = 'assets/resources'
        self.app.log = logging.getLogger('test_film')
        self.part = FakeObject('part', box(0, 0, 20, 10))
        self.part.tools = {}
        self.objects = {'part': self.part}
        self.app.collection = QtGui.QStandardItemModel()
        for name in ('Gerber', 'Excellon', 'Geometry'):
            self.app.collection.appendRow(QtGui.QStandardItem(name))
        self.app.collection.item(0).appendRow(QtGui.QStandardItem('part'))
        self.app.collection.get_by_name = self.objects.get
        self.app.collection.get_active = lambda: self.part
        self.app.collection.get_list = lambda: list(self.objects.values())
        self.app.app_obj = SimpleNamespace(object_changed=self.app.object_changed)
        self.app.ui = SimpleNamespace(plot_tab_area=FCDetachableTab2(), notebook=QtWidgets.QTabWidget(),
                                      plugin_tab=QtWidgets.QWidget(), plugin_scroll_area=QtWidgets.QScrollArea(),
                                      properties_scroll_area=QtWidgets.QScrollArea())
        for name in ('Project', 'Properties'):
            self.app.ui.notebook.addTab(QtWidgets.QWidget(), name)
        self.app.ui.notebook.addTab(self.app.ui.plugin_tab, 'Film')
        self.app.pdf_viewer = PDFViewer(self.app)
        self.jobs = []
        self.app.worker_task.connect(self.jobs.append)
        self.app.proc_container = SimpleNamespace(new=lambda text: contextlib.nullcontext())
        self.messages = []
        self.app.inform.connect(self.messages.append)
        self.film = Film(self.app)
        self.app.ui.plugin_scroll_area.setWidget(self.film)
        self.film.set_tool_ui()
        self.film._active = True
        self.svg_project()
        self.film.ui.file_type_radio.set_value('pdf')
        self.film.ui.film_type.set_value('pos')
        self.film.ui.pagesize_combo.set_value('Bounds')

    def tearDown(self):
        self.film.on_plugin_cleanup()
        self.app.ui.plugin_scroll_area.takeWidget()
        self.film.deleteLater()
        self.app.ui.plot_tab_area.deleteLater()
        self.app.ui.notebook.deleteLater()
        self.app.ui.plugin_scroll_area.deleteLater()
        self.app.ui.properties_scroll_area.deleteLater()
        self.qt_app.processEvents()

    def test_opening_selects_only_supported_film_inputs(self):
        from PyQt6 import QtGui

        geometry = FakeObject('outline', box(0, 0, 25, 15))
        geometry.kind = 'geometry'
        self.objects['outline'] = geometry
        self.app.collection.item(2).appendRow(QtGui.QStandardItem('outline'))
        unsupported = SimpleNamespace(kind='excellon', obj_options={'name': 'drills'})
        for active, expected_type, expected_name in (
                (unsupported, 'grb', 'part'), (None, 'grb', 'part'),
                (geometry, 'geo', 'outline'), (self.part, 'grb', 'part')):
            with self.subTest(active=active):
                self.app.collection.get_active = lambda: active
                self.film.set_tool_ui()
                self.assertEqual(self.film.ui.tf_type_obj_combo.get_value(), expected_type)
                self.assertEqual(self.film.ui.tf_type_box_combo.get_value(), expected_type)
                self.assertEqual(self.film.ui.tf_object_combo.get_value(), expected_name)
                self.assertEqual(self.film.ui.tf_box_combo.get_value(), expected_name)

        self.app.collection.item(0).removeRows(0, self.app.collection.item(0).rowCount())
        self.app.collection.item(2).removeRows(0, self.app.collection.item(2).rowCount())
        self.app.collection.get_active = lambda: unsupported
        self.film.set_tool_ui()
        self.assertEqual(self.film.ui.tf_type_obj_combo.get_value(), 'grb')
        self.assertEqual(self.film.ui.tf_type_box_combo.get_value(), 'grb')
        self.assertEqual(self.film.ui.tf_object_combo.get_value(), '')
        self.assertEqual(self.film.ui.tf_box_combo.get_value(), '')

    def finish(self, threaded=False):
        job = self.jobs.pop(0)
        if threaded:
            import threading
            worker = threading.Thread(target=job['fcn'], args=job['params'])
            worker.start()
            worker.join(30)
            self.assertFalse(worker.is_alive())
        else:
            job['fcn'](*job['params'])
        self.qt_app.processEvents()

    def test_preview_save_cancel_retry_and_preview_close(self):
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_film_creation()
        self.assertFalse(self.film.ui.film_object_button.isEnabled())
        self.film.on_film_creation()
        self.assertEqual(len(self.jobs), 1)
        self.finish(threaded=True)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        preview = self.app.ui.plot_tab_area.widget(0)
        original = preview.data
        self.app.ui.plot_tab_area.closeTab(0)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        with tempfile.TemporaryDirectory() as folder:
            self.app.get_last_save_folder = lambda: folder
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename', return_value=('', '')):
                self.assertFalse(self.film.save_film())
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename',
                       return_value=(os.path.join(folder, 'missing', 'film.pdf'), 'PDF Files (*.pdf)')):
                self.assertFalse(self.film.save_film())
            self.assertTrue(self.film.ui.save_button.isEnabled())
            filename = os.path.join(folder, 'film.pdf')
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename',
                       return_value=(filename, 'PDF Files (*.pdf)')):
                self.assertTrue(self.film.save_film())
            with open(filename, 'rb') as saved:
                self.assertEqual(saved.read(), original)
        self.film.ui.boundary_entry.set_value(2)
        self.assertTrue(self.film.ui.save_button.isEnabled())

    def test_save_without_preview_generates_current_settings_and_reuses_the_result(self):
        import pikepdf
        from PIL import Image
        for suffix in ('pdf', 'png', 'svg'):
            with self.subTest(format=suffix), tempfile.TemporaryDirectory() as folder:
                if suffix == 'svg':
                    self.svg_project()
                self.film.ui.file_type_radio.set_value(suffix)
                self.film.ui.png_dpi_spinner.set_value(254)
                self.film.ui.boundary_entry.set_value(1)
                self.app.get_last_save_folder = lambda: folder
                filename = os.path.join(folder, 'film.' + suffix)
                with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename',
                           return_value=(filename, '')):
                    self.film.ui.save_button.click()
                    self.assertEqual(len(self.jobs), 1)
                    self.assertFalse(self.film.ui.save_button.isEnabled())
                    self.film.save_film()
                    self.assertEqual(len(self.jobs), 1)
                    self.finish(threaded=True)
                    with open(filename, 'rb') as saved:
                        original = saved.read()
                    self.assertTrue(self.film.save_film())
                    self.assertEqual(len(self.jobs), 0)
                    with open(filename, 'rb') as saved:
                        self.assertEqual(saved.read(), original)
                    self.film.ui.boundary_entry.set_value(3)
                    self.film.ui.save_button.click()
                    self.assertEqual(len(self.jobs), 1)
                    self.finish()
                if suffix == 'pdf':
                    with pikepdf.Pdf.open(filename) as document:
                        self.assertAlmostEqual(float(document.pages[0].MediaBox[2]) / mm, 26, places=3)
                elif suffix == 'png':
                    with Image.open(filename) as image:
                        self.assertEqual(image.width, 260)
                else:
                    self.assertEqual(ElementTree.parse(filename).getroot().get('width'), '26.0mm')

    def test_pending_save_is_discarded_on_changes_cleanup_or_generation_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            self.app.get_last_save_folder = lambda: folder
            filename = os.path.join(folder, 'film.pdf')
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename',
                       return_value=(filename, '')):
                self.film.save_film()
                self.assertEqual(len(self.jobs), 1)
                self.film.ui.boundary_entry.set_value(2)
                self.finish()
                self.assertFalse(os.path.exists(filename))
                self.film.save_film()
                with patch.object(FilmGen, 'generate_film', side_effect=FilmGen.FilmError('render_failed', 'failed')):
                    self.finish()
                self.assertFalse(os.path.exists(filename))
                self.assertTrue(self.film.ui.save_button.isEnabled())
                self.film.save_film()
                self.film.on_plugin_cleanup()
                self.finish()
                self.assertFalse(os.path.exists(filename))

    def test_inflight_modification_and_cleanup_reject_late_results_without_closing_independent_pdf(self):
        from tests.test_pdf_viewer import pdf_bytes
        independent = self.app.pdf_viewer.open_bytes(pdf_bytes(), 'independent.pdf')
        self.film.on_film_creation()
        self.part.solid_geometry = box(1, 1, 3, 3)
        self.app.object_changed.emit(self.part)
        self.finish()
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.assertEqual(self.app.ui.plot_tab_area.count(), 1)
        self.film.on_film_creation()
        del self.objects['part']
        self.app.object_status_changed.emit(self.part, 'delete', 'part')
        self.finish()
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.assertEqual(self.app.ui.plot_tab_area.count(), 1)
        self.objects['part'] = self.part
        self.film.on_film_creation()
        self.app.cleanup.emit()
        self.finish()
        self.assertFalse(self.film.ui.save_button.isEnabled())
        self.assertEqual(self.app.ui.plot_tab_area.count(), 1)
        self.assertEqual(independent.document.pageCount(), 1)
        self.app.ui.plot_tab_area.closeTab(0)

    def test_format_switch_replaces_only_owned_preview(self):
        self.film.on_film_creation()
        self.finish(threaded=True)
        self.film.ui.file_type_radio.set_value('png')
        self.film.ui.png_dpi_spinner.set_value(96)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_film_creation()
        self.finish(threaded=True)
        self.assertEqual(self.app.ui.plot_tab_area.count(), 2)
        self.assertEqual(self.app.ui.plot_tab_area.widget(0).owner.kind, 'document')
        preview = self.app.ui.plot_tab_area.widget(1)
        self.assertEqual(preview.objectName(), 'film_png_preview')
        self.assertEqual(preview.dpi, 96)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_plugin_cleanup()
        self.assertEqual(self.app.ui.plot_tab_area.count(), 0)
        self.assertEqual(self.objects['part_film'].document_type, 'pdf')

    def svg_project(self):
        from appHandlers.appHistory import HistoryManager
        from appObjects.GeometryObject import GeometryObject
        from appObjects.DocumentObject import DocumentObject
        from tests.helpers_objects import make_app

        self.app.history = HistoryManager(self.app, self.app.collection)

        def new_object(kind, name, initialize, **kwargs):
            obj = (DocumentObject if kind == 'document' else GeometryObject)(name, app=make_app())
            if kind == 'document':
                obj.app = self.app
            initialize(obj, self.app)
            candidate, number = name, 1
            while candidate in self.objects:
                candidate = '%s_%s' % (name, number)
                number += 1
            obj.obj_options['name'] = candidate
            obj.plot = lambda: None
            self.objects[candidate] = obj
            if kwargs.get('record_history', True):
                self.app.history.record_create('Film', [obj])
            return obj

        self.app.app_obj.new_object = new_object

        def delete(objects):
            for obj in objects:
                if obj.kind == 'document':
                    obj.close_editor()
                self.objects.pop(obj.obj_options['name'])

        self.app.object_ops = SimpleNamespace(delete_objects_now=delete)
        self.film.ui.file_type_radio.set_value('svg')

    def test_clearing_generated_svg_tools_refuses_the_stale_save(self):
        self.svg_project()
        self.film.on_film_creation()
        self.finish()
        output = self.objects['part_film']
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, 'film.svg')
            self.app.get_last_save_folder = lambda: folder

            def choose_file(**kwargs):
                output.tools.clear()
                return filename, ''

            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename', side_effect=choose_file):
                self.assertFalse(self.film.save_film())
            self.assertFalse(os.path.exists(filename))
            self.assertEqual(self.jobs, [])

    def test_pdf_generate_keeps_project_document_and_regenerates_with_undo(self):
        import json
        from appObjects.DocumentObject import DocumentObject
        from tests.helpers_objects import make_app

        self.film.on_film_creation()
        self.finish(threaded=True)
        output = self.objects['part_film']
        self.assertEqual(output.kind, 'document')
        self.assertEqual(output.document_type, 'pdf')
        before = output.pdf_data
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, before)
        self.objects.pop('part_film')
        output.obj_options['name'] = 'renamed'
        self.objects['renamed'] = output
        self.film.ui.boundary_entry.set_value(3)
        self.film.on_film_creation()
        self.finish()
        self.assertIs(self.objects['renamed'], output)
        after = output.pdf_data
        self.assertNotEqual(before, after)
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, after)
        self.assertEqual(len(self.app.history.undo_stack), 2)
        self.assertTrue(self.app.history.undo())
        self.assertEqual(output.pdf_data, before)
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, before)
        self.assertTrue(self.app.history.redo())
        self.assertEqual(output.pdf_data, after)
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, after)
        loaded = DocumentObject('loaded', make_app())
        loaded.from_dict(json.loads(json.dumps(output.to_dict())))
        self.assertEqual(loaded.pdf_data, after)
        self.film.on_plugin_cleanup()
        self.assertIs(self.objects['renamed'], output)
        self.assertEqual(self.app.ui.plot_tab_area.count(), 0)
        self.film.on_plugin_cleanup()
        output.build_ui()
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, after)
        self.film.on_plugin_cleanup()
        self.assertEqual(self.app.ui.plot_tab_area.currentWidget().data, after)

    def test_pdf_stale_jobs_and_failed_regeneration_preserve_project_documents(self):
        self.film.on_film_creation()
        self.film.ui.boundary_entry.set_value(3)
        self.finish()
        self.assertEqual(list(self.objects), ['part'])
        self.film.on_film_creation()
        self.finish()
        output = self.objects['part_film']
        before = output.pdf_data
        self.film.ui.boundary_entry.set_value(5)
        self.film.on_film_creation()
        with patch.object(self.app.pdf_viewer, 'open_document', side_effect=[RuntimeError('view failed'), None]):
            self.finish()
        self.assertEqual(output.pdf_data, before)
        self.assertEqual(len(self.app.history.undo_stack), 1)
        self.film.on_film_creation()
        self.film.on_plugin_cleanup()
        self.finish()
        self.assertEqual(output.pdf_data, before)
        self.assertEqual(list(self.objects), ['part', 'part_film'])

    def test_svg_regenerates_renamed_object_with_one_step_and_restores_complete_appearance(self):
        from appHandlers.appHistory import object_state, states_equal
        self.svg_project()
        self.objects['part_film'] = FakeObject('part_film', box(30, 30, 40, 40))
        source = self.part.solid_geometry
        self.film.on_film_creation()
        self.finish(threaded=True)
        self.assertTrue(self.film.ui.save_button.isEnabled(), self.messages)
        output = self.objects.pop('part_film_1')
        output.obj_options['name'] = 'renamed'
        self.objects['renamed'] = output
        before = object_state(output)
        self.assertEqual(len(self.app.history.undo_stack), 1)
        self.film.ui.film_type.set_value('neg')
        self.film.on_film_creation()
        self.finish()
        after = object_state(output)
        self.assertIs(self.objects['renamed'], output)
        self.assertEqual(len(self.app.history.undo_stack), 2)
        self.assertFalse(states_equal(before, after))
        self.assertTrue(self.app.history.undo())
        self.assertTrue(states_equal(object_state(output), before))
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.assertTrue(self.app.history.redo())
        self.assertTrue(states_equal(object_state(output), after))
        self.assertIs(self.part.solid_geometry, source)
        self.film.on_film_creation()
        self.finish()
        with tempfile.TemporaryDirectory() as folder:
            self.app.get_last_save_folder = lambda: folder
            filename = os.path.join(folder, 'film.svg')
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename', return_value=(filename, '')):
                self.assertTrue(self.film.save_film())
            with open(filename, encoding='utf-8') as saved:
                self.assertEqual(ElementTree.fromstring(saved.read()).tag, SVG_NS + 'svg')
        del self.objects['renamed']
        self.app.object_status_changed.emit(output, 'delete', 'renamed')
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_film_creation()
        self.finish()
        replacement = self.objects['part_film_1']
        self.assertIsNot(replacement, output)
        self.film.on_plugin_cleanup()
        self.assertIs(self.objects['part_film_1'], replacement)
        self.film._active = True
        self.film.on_film_creation()
        self.finish()
        self.assertIsNot(self.objects['part_film_2'], replacement)
        latest = self.objects['part_film_2']
        latest.obj_options['film']['layers'][0]['color'] = '#ABCDEF'
        self.app.object_changed.emit(latest)
        self.assertTrue(self.film.ui.save_button.isEnabled())

    def test_svg_stale_and_deactivated_jobs_do_not_create_project_objects(self):
        self.svg_project()
        self.film.on_film_creation()
        self.film.ui.boundary_entry.set_value(3)
        self.finish()
        self.assertEqual(list(self.objects), ['part'])

        self.film.on_film_creation()
        self.film.on_plugin_cleanup()
        self.finish()
        self.assertEqual(list(self.objects), ['part'])

    def test_svg_save_rebuilds_after_other_tool_changes(self):
        self.svg_project()
        with tempfile.TemporaryDirectory() as folder:
            self.app.get_last_save_folder = lambda: folder
            filename = os.path.join(folder, 'film.svg')
            with patch('appPlugins.ToolFilm.Film.FCFileSaveDialog.get_saved_filename', return_value=(filename, '')):
                for change in ('add', 'edit', 'clear', 'remove'):
                    with self.subTest(change=change):
                        self.film.on_film_creation()
                        self.finish()
                        output = self.objects['part_film']
                        if change == 'add':
                            output.tools[2] = dict(next(iter(output.tools.values())))
                        else:
                            output.tools[2] = dict(next(iter(output.tools.values())))
                            self.film._film_state = self.film.film_state(output)
                            if change == 'remove':
                                del output.tools[2]
                            else:
                                output.tools[2]['solid_geometry'] = [box(0, 0, 1, 1)] if change == 'edit' else []
                        self.film.save_film()
                        self.assertEqual(len(self.jobs), 1)
                        self.finish()
                        self.assertTrue(os.path.isfile(filename))

    def test_svg_failed_regeneration_rolls_back_and_reload_drops_identity_tracking(self):
        import json
        from camlib import dict2obj, to_dict
        from appHandlers.appHistory import object_state, states_equal
        from appObjects.GeometryObject import GeometryObject
        from tests.helpers_objects import make_app

        self.svg_project()
        self.film.on_film_creation()
        self.finish()
        output = self.objects['part_film']
        before = object_state(output)
        self.film.ui.film_type.set_value('neg')
        self.film.on_film_creation()
        with patch.object(output, 'plot', side_effect=[RuntimeError('plot failed'), None]):
            self.finish()
        self.assertTrue(states_equal(before, object_state(output)))
        self.assertEqual(len(self.app.history.undo_stack), 1)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_film_creation()
        self.finish()
        restored = GeometryObject('loaded', make_app())
        restored.from_dict(json.loads(json.dumps(output.to_dict(), default=to_dict), object_hook=dict2obj))
        self.objects['part_film'] = restored
        self.app.history_changed.emit('', '')
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.film.on_film_creation()
        self.finish()
        self.assertIsNot(self.objects['part_film_1'], restored)
        self.assertIs(self.objects['part_film'], restored)
        created = self.objects['part_film_1']
        created_state = object_state(created)
        self.assertTrue(self.app.history.undo())
        self.assertNotIn('part_film_1', self.objects)
        self.assertTrue(self.film.ui.save_button.isEnabled())
        self.assertTrue(self.app.history.redo())
        replayed = self.objects['part_film_1']
        self.assertIsNot(replayed, created)
        self.assertTrue(states_equal(object_state(replayed), created_state))
        self.film.on_film_creation()
        self.finish()
        self.assertIs(self.objects['part_film_1'], replayed)
        self.assertIsNot(self.objects['part_film_2'], replayed)

    def test_replacing_plugin_rejects_inflight_completion(self):
        from PyQt6 import QtWidgets
        self.film.on_film_creation()
        job = self.jobs.pop(0)
        job['fcn'](*job['params'])
        observed_tabs = []
        area = self.app.ui.plot_tab_area
        area.currentChanged.connect(lambda index: observed_tabs.append(area.count()))
        self.app.ui.plugin_scroll_area.takeWidget()
        self.app.ui.plugin_scroll_area.setWidget(QtWidgets.QWidget())
        self.qt_app.processEvents()
        self.assertFalse(self.film.ui.save_button.isEnabled())
        self.assertEqual(self.app.ui.plot_tab_area.count(), 0)
        self.assertFalse(any(count > 0 for count in observed_tabs))

    def test_toggle_close_preserves_other_notebook_tabs_after_reordering(self):
        from PyQt6 import QtCore
        from tests.test_pdf_viewer import pdf_bytes
        independent = self.app.pdf_viewer.open_bytes(pdf_bytes(), 'independent.pdf')
        self.film.on_film_creation()
        self.finish()
        ui = self.app.ui
        output = self.objects['part_film']
        preview = ui.plot_tab_area.currentWidget()
        ui.plot_tab_area.detachTab(ui.plot_tab_area.indexOf(preview), QtCore.QPoint(0, 0))
        window = preview.window()
        ui.properties_tab = ui.notebook.widget(1)
        ui.plugin_tab.setObjectName('plugin_tab')
        ui.splitter = SimpleNamespace(sizes=lambda: [1, 1], setSizes=lambda sizes: None)
        self.film.setObjectName(self.film.pluginName)
        ui.notebook.tabBar().moveTab(2, 1)
        ui.notebook.setCurrentWidget(ui.plugin_tab)
        self.film.run(toggle=True)
        self.assertGreaterEqual(ui.notebook.indexOf(ui.properties_tab), 0)
        self.assertEqual(ui.notebook.indexOf(ui.plugin_tab), -1)
        self.assertFalse(window.isVisible())
        self.assertEqual(ui.plot_tab_area.count(), 1)
        self.assertIs(ui.plot_tab_area.widget(0), independent)
        self.assertIs(self.objects['part_film'], output)
        self.film.on_plugin_cleanup()
        self.assertFalse(self.film.ui.save_button.isEnabled())
        self.assertEqual(independent.document.pageCount(), 1)
        ui.plot_tab_area.closeTab(0)

if __name__ == '__main__':
    unittest.main()
