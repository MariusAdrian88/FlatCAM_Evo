import functools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtWidgets
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, Point, Polygon
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from appParsers.ParseKiCadPcb import KiCadPcbParser
from defaults import AppDefaults

import pytest

pytestmark = pytest.mark.slow


@functools.lru_cache(maxsize=None)
def _parsed_board():
    # 1 parse for both test classes: the import copies the geometry and does not change the parsed board
    return KiCadPcbParser(app=_app('MM')).parse_file(TestKiCadPcbFixture.FIXTURE)


class NativeGerberObject(GerberObject):
    def __init__(self, name, app):
        self.app = app
        self.obj_options = {'name': name, 'pcbdoc_import': False}
        self.units = app.app_units
        self.tools = {}
        self.solid_geometry = []
        self.follow_geometry = []
        self.source_file = ''
        self.export_as_regions = False


class NativeExcellonObject(ExcellonObject):
    def __init__(self, name, app):
        self.app = app
        self.obj_options = {'name': name, 'pcbdoc_import': False}
        self.units = app.app_units
        self.tools = {}
        self.solid_geometry = []
        self.source_file = ''
        self.excellon_circle_steps = 64
        self._fixed_steps = 64
        self.default_data = {}


def _app(units):
    defaults = AppDefaults()
    options = dict(defaults.defaults)
    plotcanvas = Mock()
    plotcanvas.new_shape_collection.return_value = Mock()
    options.update({
        'gerber_exp_units': units,
        'gerber_exp_integer': 2,
        'gerber_exp_decimals': 4,
        'gerber_exp_zeros': 'L',
        'excellon_exp_units': 'METRIC' if units == 'MM' else 'INCH',
        'excellon_exp_integer': 2,
        'excellon_exp_decimals': 4,
        'excellon_exp_format': 'dec',
        'excellon_exp_slot_type': 'routing',
    })
    return SimpleNamespace(
        log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
        options=options, app_units=units, new_project_signal=Mock(),
        abort_flag=False, app_obj=Mock(), plot_all=Mock(),
        register_folder=Mock(), defaults=defaults, decimals=4,
        version='test', version_date='test',
        proc_container=Mock(), plotcanvas=plotcanvas, pool=Mock(),
        collection=Mock(), file_opened=Mock(), file_saved=Mock(),
    )


def _union(value):
    if isinstance(value, list):
        geometries = [geo for geo in value if not geo.is_empty]
    elif value is None or value.is_empty:
        geometries = []
    elif isinstance(value, GeometryCollection):
        geometries = list(value.geoms)
    else:
        geometries = [value]
    return unary_union(geometries) if geometries else GeometryCollection()


def _holes(tools):
    """Flatten Excellon tools into (diameter, x, y) drills and (diameter, x1, y1, x2, y2) slots."""
    drills, slots = [], []
    for tool in tools.values():
        diameter = round(tool['tooldia'], 4)
        drills += [(diameter, point.x, point.y) for point in tool.get('drills', [])]
        slots += [(diameter, a.x, a.y, b.x, b.y) for a, b in tool.get('slots', [])]
    return drills, slots


class TestKiCadPcbFixture(unittest.TestCase):
    FIXTURE = Path(__file__).parent / 'fixtures' / 'kicad' / 'Easyduino_RP2040.kicad_pcb'
    GERBERS = Path(__file__).parent / 'fixtures' / 'kicad' / 'Gerbers'
    DRILLS = GERBERS / 'Easyduino_RP2040.drl'
    REFERENCE = {
        'F.Cu': 'Easyduino_RP2040-F_Cu.gtl',
        'In1.Cu': 'Easyduino_RP2040-In1_Cu.g1',
        'In2.Cu': 'Easyduino_RP2040-In2_Cu.g2',
        'B.Cu': 'Easyduino_RP2040-B_Cu.gbl',
    }
    COPPER = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
    LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu', 'F.Paste', 'F.SilkS', 'B.SilkS', 'F.Mask', 'B.Mask',
              'Dwgs.User', 'Cmts.User', 'Edge.Cuts', 'F.CrtYd', 'B.CrtYd', 'F.Fab', 'B.Fab']
    DEFAULT_LAYERS = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu', 'F.Paste', 'F.SilkS', 'B.SilkS', 'F.Mask',
                      'B.Mask', 'Edge.Cuts']
    OUTLINE_BOUNDS = [133.95, -124.55, 155.05, -73.45]

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app('MM')
        cls.parsed = _parsed_board()
        cls.references = {}
        for layer, filename in cls.REFERENCE.items():
            parser = Gerber(cls.app)
            assert parser.parse_file(str(cls.GERBERS / filename)) != 'fail', filename
            cls.references[layer] = _union(parser.solid_geometry)
        drill_parser = Excellon(cls.app)
        assert drill_parser.parse_file(str(cls.DRILLS)) != 'fail'
        cls.reference_drills, cls.reference_slots = _holes(drill_parser.tools)

    def _mine(self, layer):
        return _union(self.parsed.layers[layer]['solid_geometry'])

    def test_copper_matches_reference_gerbers(self):
        for layer in self.COPPER:
            with self.subTest(layer=layer):
                reference = self.references[layer]
                difference = self._mine(layer).symmetric_difference(reference).area
                self.assertLessEqual(difference / reference.area, 0.01)

    def test_every_hole_matches_a_reference_hole(self):
        all_tools = [tool for tools in self.parsed.drill_groups.values() for tool in tools.values()]
        mine_drills, mine_slots = _holes(dict(enumerate(all_tools)))
        self.assertEqual(len(mine_drills) + len(mine_slots), 141)
        self.assertEqual(len(self.reference_drills) + len(self.reference_slots), 141)
        for diameter, x, y in self.reference_drills:
            best = min((max(abs(x - mx), abs(y - my)) for md, mx, my in mine_drills
                        if abs(md - diameter) < 1e-3), default=None)
            self.assertIsNotNone(best, 'no drill of %s mm near (%s, %s)' % (diameter, x, y))
            self.assertLessEqual(best, 0.005)
        for diameter, x1, y1, x2, y2 in self.reference_slots:
            best = None
            for md, mx1, my1, mx2, my2 in mine_slots:
                if abs(md - diameter) > 1e-3:
                    continue
                direct = max(abs(mx1 - x1), abs(my1 - y1), abs(mx2 - x2), abs(my2 - y2))
                swapped = max(abs(mx1 - x2), abs(my1 - y2), abs(mx2 - x1), abs(my2 - y1))
                distance = min(direct, swapped)
                best = distance if best is None else min(best, distance)
            self.assertIsNotNone(best, 'no slot of %s mm at (%s, %s)' % (diameter, x1, y1))
            self.assertLessEqual(best, 0.005)


class TestKiCadPcbFixtureRoundTrip(unittest.TestCase):
    FIXTURE = TestKiCadPcbFixture.FIXTURE
    COPPER = TestKiCadPcbFixture.COPPER
    PTH = 'PTH_F.Cu-B.Cu'

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.parsed = _parsed_board()

    def _import(self, units, layers):
        app = _app(units)
        io = appIO(app)
        io._register_pcbdoc_outputs = Mock()
        objects = {}

        def create(kind, name, initialize, **kwargs):
            obj = NativeGerberObject(name, app) if kind == 'gerber' else NativeExcellonObject(name, app)
            if initialize(obj, app) == 'fail':
                return 'fail'
            objects[name] = obj
            return obj

        app.app_obj.new_object.side_effect = create
        app.collection.get_by_name.side_effect = objects.__getitem__
        result = io.import_pcbdoc_objects(str(self.FIXTURE), self.parsed, layers, [self.PTH])
        self.assertIsNone(result)
        return app, io, objects

    def test_copper_and_drill_group_survive_import_export_reimport(self):
        # all four layers in millimetres; inches only changes the unit scaling, one layer shows it
        unions = {}
        for units, layers in (('MM', self.COPPER), ('IN', ['F.Cu'])):
            with self.subTest(units=units):
                app, io, objects = self._import(units, layers)
                gerbers = {name: obj for name, obj in objects.items() if isinstance(obj, NativeGerberObject)}
                drills = {name: obj for name, obj in objects.items() if isinstance(obj, NativeExcellonObject)}
                self.assertEqual(sorted(gerbers), sorted('Easyduino_RP2040_%s' % layer.replace('.', '_')
                                                         for layer in layers))
                self.assertEqual(list(drills), ['Easyduino_RP2040_PTH_F_Cu-B_Cu'])
                factor = 1 if units == 'MM' else 1 / 25.4

                with tempfile.TemporaryDirectory() as directory:
                    reimported = {}
                    for layer in layers:
                        name ='Easyduino_RP2040_%s' % layer.replace('.', '_')
                        self.assertEqual(gerbers[name].units, units)
                        filename = Path(directory) / ('%s.gbr' % layer.replace('.', '_'))
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        self.assertTrue(filename.is_file())
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        reimported[layer] = _union(parser.solid_geometry)

                    drill_name = next(iter(drills))
                    self.assertEqual(drills[drill_name].units, units)
                    filename = Path(directory) / 'drills.drl'
                    io.export_excellon(drill_name, str(filename), local_use=None, use_thread=False)
                    parser = Excellon(app)
                    self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                    self.assertEqual(parser.units, units)
                    self.assertEqual(sum(len(tool.get('drills', [])) for tool in parser.tools.values()), 131)
                    self.assertEqual(sum(len(tool.get('slots', [])) for tool in parser.tools.values()), 4)
                    diameters = sorted(round(tool['tooldia'] / factor, 2) for tool in parser.tools.values())
                    self.assertEqual(diameters, [0.3, 0.6, 1.0])

                for layer in layers:
                    if layer not in unions:
                        unions[layer] = _union(self.parsed.layers[layer]['solid_geometry'])
                    expected = scale(unions[layer], xfact=factor, yfact=factor, origin=(0, 0))
                    actual = reimported[layer]
                    difference = expected.symmetric_difference(actual).area
                    # Gerber export rounds each vertex to 4 decimals of the file unit (1e-4 mm or 1e-4 in).
                    # Inches therefore carry up to 2.54 um of rounding; the symmetric difference is
                    # about perimeter x half a grid step, so that bound scales with the unit.
                    tolerance = max(0.01 * factor, expected.area * 0.0001, expected.length * 0.5e-4)
                    self.assertLessEqual(difference, tolerance,
                                         'layer %s geometry differs by %s' % (layer, difference))


if __name__ == '__main__':
    unittest.main()
