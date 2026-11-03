import functools
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets
from shapely.affinity import scale, translate
from shapely.geometry import GeometryCollection, Point
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseEagleBrd import EagleBrdParser
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from defaults import AppDefaults

import pytest

pytestmark = pytest.mark.slow


# The Eagle CAM references (GERBER/, inch) and Pcb39.TXT are shifted against the board coordinates by OFFSET mm.
OFFSET = (14.62913, 9.88898)


@functools.lru_cache(maxsize=None)
def _parsed_board():
    # 1 parse for both test classes: the import copies the geometry and does not change the parsed board
    return EagleBrdParser(app=_app('MM')).parse_file(TestEagleFixture.FIXTURE)


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


def _read_drill_file(path):
    """Pcb39.TXT as (diameter_mm, x_mm, y_mm) drills, in the reference (shifted) frame. Inch, 2.5 format."""
    holes, diameters, tool = [], {}, None
    with open(path, encoding='utf-8', errors='replace') as handle:
        lines = handle.read().replace('\r', '').split('\n')
    for line in lines:
        match = re.match(r'T(\d+)C([\d.]+)', line)
        if match:
            diameters[match.group(1)] = float(match.group(2)) * 25.4
            continue
        match = re.match(r'T(\d+)$', line)
        if match:
            tool = match.group(1)
            continue
        match = re.match(r'X(\d+)Y(\d+)', line)
        if match and tool:
            holes.append((diameters[tool], int(match.group(1)) / 1e5 * 25.4 - OFFSET[0],
                          int(match.group(2)) / 1e5 * 25.4 - OFFSET[1]))
    return holes


class TestEagleFixture(unittest.TestCase):
    FIXTURE = Path(__file__).parent / 'fixtures' / 'eagle_cad' / 'Pcb39.brd'
    GERBERS = Path(__file__).parent / 'fixtures' / 'eagle_cad' / 'GERBER'
    DRILLS = GERBERS / 'Pcb39.TXT'
    REFERENCE = {ext: 'Pcb39.%s' % ext for ext in ('GTL', 'GBL')}
    EXPECTED_LAYERS = ['Top', 'Bottom', 'Dimension', 'tPlace', 'bPlace', 'tNames', 'bNames', 'tValues', 'bValues',
                       'tStop', 'bStop', 'tCream', 'bCream', 'Holes']
    DEFAULT_LAYERS = ['Top', 'Bottom', 'Dimension', 'tPlace', 'bPlace', 'tNames', 'bNames', 'tStop', 'bStop',
                      'tCream', 'bCream']
    HOLE_COUNT = 211
    PTH_COUNT = 181
    NPTH_COUNT = 30

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app('MM')
        cls.parsed = _parsed_board()
        cls.references = {}
        for key, filename in cls.REFERENCE.items():
            parser = Gerber(cls.app)
            assert parser.parse_file(str(cls.GERBERS / filename)) != 'fail', filename
            assert parser.units == 'IN', (filename, parser.units)
            geometry = _union(parser.solid_geometry)
            # reference frame -> board frame: inch to mm, then remove the CAM offset
            cls.references[key] = translate(scale(geometry, 25.4, 25.4, origin=(0, 0)), -OFFSET[0], -OFFSET[1])
        cls.reference_drills = _read_drill_file(str(cls.DRILLS))

    def _mine(self, layer):
        return _union(self.parsed.layers[layer]['solid_geometry'])

    def _mine_follow(self, layers):
        lines = []
        for layer in layers:
            lines += self.parsed.layers[layer]['follow_geometry']
        return unary_union(lines)

    def test_top_and_bottom_copper_match_reference_gerbers(self):
        for layer, reference in (('Top', 'GTL'), ('Bottom', 'GBL')):
            with self.subTest(layer=layer):
                expected = self.references[reference]
                difference = self._mine(layer).symmetric_difference(expected).area
                self.assertLessEqual(difference / expected.area, 0.01)

    def test_every_hole_matches_a_reference_hole(self):
        mine = [(tool['tooldia'], point.x, point.y)
                for tools in self.parsed.drill_groups.values() for tool in tools.values()
                for point in tool['drills']]
        self.assertEqual(len(mine), self.HOLE_COUNT)
        used = set()
        for diameter, x, y in self.reference_drills:
            candidates = [(max(abs(mx - x), abs(my - y)), index)
                          for index, (md, mx, my) in enumerate(mine)
                          if index not in used and abs(md - diameter) <= 0.003]
            self.assertTrue(candidates, 'no drill of %.4f mm near (%.3f, %.3f)' % (diameter, x, y))
            distance, index = min(candidates)
            used.add(index)
            self.assertLessEqual(distance, 0.01, 'hole at (%.3f, %.3f) is %.4f mm off' % (x, y, distance))


class TestEagleFixtureRoundTrip(unittest.TestCase):
    FIXTURE = TestEagleFixture.FIXTURE
    LAYERS = ['Top', 'Bottom']
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
        result = io.import_pcbdoc_objects(str(self.FIXTURE), self.parsed, self.LAYERS, [self.PTH])
        self.assertIsNone(result)
        return app, io, objects

    def test_top_bottom_and_pth_survive_import_export_reimport(self):
        for units in ('MM', 'IN'):
            with self.subTest(units=units):
                app, io, objects = self._import(units)
                gerbers = {name: obj for name, obj in objects.items() if isinstance(obj, NativeGerberObject)}
                drills = {name: obj for name, obj in objects.items() if isinstance(obj, NativeExcellonObject)}
                self.assertEqual(sorted(gerbers), ['Pcb39_Bottom', 'Pcb39_Top'])
                self.assertEqual(list(drills), ['Pcb39_PTH'])
                factor = 1 if units == 'MM' else 1 / 25.4

                with tempfile.TemporaryDirectory() as directory:
                    reimported = {}
                    for layer in self.LAYERS:
                        name = 'Pcb39_%s' % layer
                        self.assertEqual(gerbers[name].units, units)
                        filename = Path(directory) / ('%s.gbr' % layer)
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        self.assertTrue(filename.is_file())
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        reimported[layer] = _union(parser.solid_geometry)

                    self.assertEqual(drills['Pcb39_PTH'].units, units)
                    filename = Path(directory) / 'pth.drl'
                    io.export_excellon('Pcb39_PTH', str(filename), local_use=None, use_thread=False)
                    parser = Excellon(app)
                    self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                    self.assertEqual(parser.units, units)
                    self.assertEqual(sum(len(tool.get('drills', [])) for tool in parser.tools.values()),
                                     TestEagleFixture.PTH_COUNT)
                    expected_diameters = sorted({round(tool['tooldia'], 2) for tool in
                                                 self.parsed.drill_groups[self.PTH].values()})
                    diameters = sorted({round(tool['tooldia'] / factor, 2) for tool in parser.tools.values()})
                    self.assertEqual(diameters, expected_diameters)

                for layer in self.LAYERS:
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
