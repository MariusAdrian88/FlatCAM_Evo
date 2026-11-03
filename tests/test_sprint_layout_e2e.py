import functools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtWidgets
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, Point
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from appParsers.ParseSprintLayout import SprintLayoutParser
from defaults import AppDefaults

import pytest

pytestmark = pytest.mark.slow


@functools.lru_cache(maxsize=None)
def _parsed_board():
    # 1 parse for both test classes: the import copies the geometry and does not change the parsed board
    return SprintLayoutParser(app=_app('MM')).parse_file(TestSprintLayoutFixture.FIXTURE)


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


class TestSprintLayoutFixture(unittest.TestCase):
    FIXTURE = Path(__file__).parent / 'fixtures' / 'sprint_layout_6' / 'cata.lay6'
    GERBERS = Path(__file__).parent / 'fixtures' / 'sprint_layout_6' / 'Gerber'
    DRILLS = GERBERS / 'cata.DRL'
    REFERENCE = {
        'C1': 'cata.GTL',
        'C2': 'cata.GBL',
        'O': 'cata.GKO',
    }
    LAYERS = ['C1', 'C2', 'M1', 'M2', 'S1', 'O']
    DEFAULT_LAYERS = ['C1', 'C2', 'M1', 'M2', 'S1', 'O']
    OUTLINE_BOUNDS = [0.4, 0.4, 161.6, 101.6]
    BOARD_OUTLINE_BOUNDS = [0.5, 0.5, 161.5, 101.5]
    # diameter (mm, 2 decimals) -> (drills, slots)
    DRILL_COUNTS = {0.7: (9, 0), 0.8: (177, 0), 1.0: (44, 0), 1.14: (5, 0), 1.3: (2, 0), 3.0: (8, 0)}

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
        # The Excellon parser keeps the file's own units (cata.DRL is inches); the Gerber parser converts to mm.
        assert drill_parser.units in ('IN', 'MM'), drill_parser.units
        factor = 25.4 if drill_parser.units == 'IN' else 1.0
        cls.reference_tools = {
            number: {'tooldia': tool['tooldia'] * factor,
                     'drills': [Point(point.x * factor, point.y * factor) for point in tool.get('drills', [])]}
            for number, tool in drill_parser.tools.items()}
        cls.reference_drills, cls.reference_slots = _holes(cls.reference_tools)

    def _mine(self, layer):
        return _union(self.parsed.layers[layer]['solid_geometry'])


    def test_copper_matches_reference_gerbers(self):
        for layer in ('C1', 'C2'):
            with self.subTest(layer=layer):
                reference = self.references[layer]
                difference = self._mine(layer).symmetric_difference(reference).area
                self.assertLessEqual(difference / reference.area, 0.01)


    def test_every_hole_matches_a_reference_hole(self):
        all_tools = [tool for tools in self.parsed.drill_groups.values() for tool in tools.values()]
        mine_drills, mine_slots = _holes(dict(enumerate(all_tools)))
        self.assertEqual(len(mine_drills) + len(mine_slots), 245)
        self.assertEqual(len(self.reference_drills) + len(self.reference_slots), 245)
        for diameter, x, y in mine_drills:
            best = min((max(abs(x - rx), abs(y - ry)) for rd, rx, ry in self.reference_drills
                        if abs(rd - diameter) < 0.003), default=None)
            self.assertIsNotNone(best, 'no reference drill of %s mm near (%s, %s)' % (diameter, x, y))
            self.assertLessEqual(best, 0.013)


class TestSprintLayoutFixtureRoundTrip(unittest.TestCase):
    FIXTURE = TestSprintLayoutFixture.FIXTURE
    COPPER = ['C1', 'C2']
    PTH = 'PTH'

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.parsed = _parsed_board()

    def _import(self, units):
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
        result = io.import_pcbdoc_objects(str(self.FIXTURE), self.parsed, self.COPPER, [self.PTH])
        self.assertIsNone(result)
        return app, io, objects

    def test_copper_and_drill_group_survive_import_export_reimport(self):
        for units in ('MM', 'IN'):
            with self.subTest(units=units):
                app, io, objects = self._import(units)
                gerbers = {name: obj for name, obj in objects.items() if isinstance(obj, NativeGerberObject)}
                drills = {name: obj for name, obj in objects.items() if isinstance(obj, NativeExcellonObject)}
                layers = self.COPPER if units == 'MM' else self.COPPER[:1]  # inch only needs one layer: the unit conversion is the same
                self.assertEqual(sorted(gerbers), ['cata_C1', 'cata_C2'])
                self.assertEqual(list(drills), ['cata_PTH'])
                factor = 1 if units == 'MM' else 1 / 25.4

                with tempfile.TemporaryDirectory() as directory:
                    reimported = {}
                    for layer in layers:
                        name = 'cata_%s' % layer
                        self.assertEqual(gerbers[name].units, units)
                        filename = Path(directory) / ('%s.gbr' % layer)
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        self.assertTrue(filename.is_file())
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        reimported[layer] = _union(parser.solid_geometry)

                    self.assertEqual(drills['cata_PTH'].units, units)
                    filename = Path(directory) / 'drills.drl'
                    io.export_excellon('cata_PTH', str(filename), local_use=None, use_thread=False)
                    parser = Excellon(app)
                    self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                    self.assertEqual(parser.units, units)
                    self.assertEqual(sum(len(tool.get('drills', [])) for tool in parser.tools.values()), 245)
                    self.assertEqual(sum(len(tool.get('slots', [])) for tool in parser.tools.values()), 0)
                    diameters = sorted(round(tool['tooldia'] / factor, 2) for tool in parser.tools.values())
                    self.assertEqual(diameters, [0.7, 0.8, 1.0, 1.14, 1.3, 3.0])

                for layer in layers:
                    expected = scale(_union(self.parsed.layers[layer]['solid_geometry']),
                                     xfact=factor, yfact=factor, origin=(0, 0))
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
