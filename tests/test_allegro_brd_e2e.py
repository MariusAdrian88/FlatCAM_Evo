import functools
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PyQt6 import QtWidgets
from shapely.affinity import scale
from shapely.geometry import GeometryCollection
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseAllegroBrd import AllegroBrdParser
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from defaults import AppDefaults

import pytest

pytestmark = pytest.mark.slow


FIXTURES = Path(__file__).parent / 'fixtures' / 'orcad_17.2'
BOARD_A = FIXTURES / 'proiectCAD.brd'
BOARD_B = FIXTURES / '2nd project' / 'TI28027.brd'
FILMS_B_DIR = FIXTURES / '2nd project' / 'Gerbers'

# Allegro 17.2 two-layer board: the films were plotted from this board (photoplot.log, Cadence Allegro 17.2-S025)
FILMS_A = {'TOP': 'Copper_TOP.art', 'BOTTOM': 'Copper_Bottom.art',
           'SOLDERMASK_TOP': 'SolderMask_TOP.art', 'SOLDERMASK_BOTTOM': 'SolderMask_Bottom.art',
           'SILKSCREEN_TOP': 'SilkScreen_TOP.art', 'ASSEMBLY_TOP': 'Assembly_Drawing_TOP.art'}
# Six-layer 18.x/24.1 board. TOP, BOTTOM and the two inner films carry the board's copper. The other six films
# (names PASTEMASK, SILKSCREEN, SOLDERMASK) do not hold what their names say: their headers list copper layers
# and their flashes sit at other places than the vias and pads of this board, so they cannot be used as references.
FILMS_B = {'TOP': 'TOP.art', 'BOTTOM': 'BOTTOM.art', 'INNER 1': 'TI28027_INNER1_COPPER_GND.art',
           'INNER 2': 'TI28027_INNER2_COPPER_POWER.art'}


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


def _app(units='MM'):
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
        log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=options, app_units=units,
        new_project_signal=Mock(), abort_flag=False, app_obj=Mock(), plot_all=Mock(), register_folder=Mock(),
        defaults=defaults, decimals=4, version='test', version_date='test',
        proc_container=Mock(), plotcanvas=plotcanvas, pool=Mock(), collection=Mock(), file_opened=Mock(),
        file_saved=Mock())


@functools.lru_cache(maxsize=None)
def _parsed_board_a():
    # 1 parse for both test classes of board A: the import copies the geometry and does not change the parsed board
    return AllegroBrdParser(app=_app('MM')).parse_file(BOARD_A)


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


def read_flashes(path):
    """(x, y) in mm of every D03 flash of an Allegro RS-274X film (leading zeros suppressed, modal coordinates)."""
    text = Path(path).read_text(encoding='latin-1')
    match = re.search(r'%FSLAX(\d)(\d)Y(\d)(\d)', text)
    x_decimals, y_decimals = int(match.group(2)), int(match.group(4))
    x = y = 0.0
    flashes = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith(('%', 'G04', 'M02')):
            continue
        coords = dict(re.findall(r'([XY])(-?\d+)', line))
        if 'X' in coords:
            x = int(coords['X']) / 10 ** x_decimals
        if 'Y' in coords:
            y = int(coords['Y']) / 10 ** y_decimals
        if line.endswith('D03*'):
            flashes.append((x, y))
    return flashes


class FixtureCase(unittest.TestCase):
    @classmethod
    def read_film(cls, path):
        parser = Gerber(cls.app)
        assert parser.parse_file(str(path)) != 'fail', path
        geometry = _union(parser.solid_geometry)
        if parser.units == 'IN':
            geometry = scale(geometry, 25.4, 25.4, origin=(0, 0))
        return geometry

    def mine(self, name):
        # the parsed board does not change during a class: fuse each layer 1 time for all its tests
        cls = type(self)
        if '_mine_cache' not in cls.__dict__:
            cls._mine_cache = {}
        if name not in cls._mine_cache:
            cls._mine_cache[name] = (_union(self.parsed.layers[name]['solid_geometry'])
                                     if name in self.parsed.layers else None)
        return cls._mine_cache[name]

    def ratio(self, name, reference, extra=None):
        mine = self.mine(name)
        if extra is not None:
            mine = unary_union([mine, extra])
        return mine.symmetric_difference(reference).area / reference.area

    def holes(self, group):
        return [(tool['tooldia'], point) for tool in self.parsed.drill_groups[group].values()
                for point in tool['drills']]

    def assert_holes_on_flashes(self, top_film, bottom_film):
        top, bottom = np.array(read_flashes(top_film)), np.array(read_flashes(bottom_film))
        holes = np.array([(point.x, point.y) for group in self.parsed.drill_groups
                          for _diameter, point in self.holes(group)]).reshape(-1, 2)
        worst = 0.0
        for flashes in (top, bottom):
            # distance from each hole to its nearest flash, for all holes at once
            nearest = np.sqrt(((holes[:, None, :] - flashes[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            worst = max(worst, float(nearest.max()) if len(nearest) else 0.0)
        self.assertLessEqual(worst, 0.01)
        return worst


class TestAllegro172Board(FixtureCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app()
        cls.parsed = _parsed_board_a()
        cls.films = {name: cls.read_film(FIXTURES / film) for name, film in FILMS_A.items()}
        cls.films['older TOP'] = cls.read_film(FIXTURES / 'TOP.art')
        cls.films['older BOTTOM'] = cls.read_film(FIXTURES / 'BOTTOM.art')

    def test_copper_matches_the_copper_films(self):
        for name in ('TOP', 'BOTTOM'):
            with self.subTest(layer=name):
                self.assertLessEqual(self.ratio(name, self.films[name]), 0.01)
                self.assertLessEqual(self.ratio(name, self.films['older ' + name]), 0.01)
                self.assertEqual([round(v, 2) for v in self.mine(name).bounds],
                                 [round(v, 2) for v in self.films[name].bounds])

    def test_drills_sit_on_pad_flashes_in_both_outer_films(self):
        self.assert_holes_on_flashes(FIXTURES / 'Copper_TOP.art', FIXTURES / 'Copper_Bottom.art')
        # 26 plated pins (16 round 36 mil, 6 transistor, 4 round 42 mil); no vias on this board
        counts = {}
        for diameter, _point in self.holes('PTH'):
            counts[round(diameter, 3)] = counts.get(round(diameter, 3), 0) + 1
        self.assertEqual(counts, {0.787: 6, 0.914: 16, 1.067: 4})
        self.assertEqual([(round(d, 3)) for d, _p in self.holes('NPTH')], [3.2] * 4)
        self.assertEqual(list(self.parsed.drill_groups), ['PTH', 'NPTH'])

class TestAllegro181Board(FixtureCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app()
        cls.parsed = AllegroBrdParser(app=cls.app).parse_file(BOARD_B)
        cls.films = {name: cls.read_film(FILMS_B_DIR / film) for name, film in FILMS_B.items()}

    def test_copper_matches_the_four_copper_films(self):
        for name in FILMS_B:
            with self.subTest(layer=name):
                self.assertLessEqual(self.ratio(name, self.films[name]), 0.01)

class TestAllegroRoundTrip(unittest.TestCase):
    LAYERS = ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'OUTLINE']
    GROUPS = ['PTH', 'NPTH']

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.parsed = _parsed_board_a()

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
        self.assertIsNone(io.import_pcbdoc_objects(str(BOARD_A), self.parsed, self.LAYERS, self.GROUPS))
        return app, io, objects

    def test_layers_and_drills_survive_import_export_reimport(self):
        for units in ('MM', 'IN'):
            with self.subTest(units=units):
                app, io, objects = self._import(units)
                factor = 1 if units == 'MM' else 1 / 25.4
                self.assertEqual(sorted(objects), sorted('proiectCAD_%s' % key for key in self.LAYERS + self.GROUPS))
                with tempfile.TemporaryDirectory() as directory:
                    for layer in self.LAYERS:
                        name = 'proiectCAD_%s' % layer
                        filename = Path(directory) / ('%s.gbr' % layer)
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        expected = scale(_union(self.parsed.layers[layer]['solid_geometry']), xfact=factor,
                                         yfact=factor, origin=(0, 0))
                        difference = expected.symmetric_difference(_union(parser.solid_geometry)).area
                        # the Gerber export rounds each vertex to 4 decimals of the file unit
                        tolerance = max(0.01 * factor, expected.area * 0.0001, expected.length * 0.5e-4)
                        self.assertLessEqual(difference, tolerance, 'layer %s differs by %s' % (layer, difference))
                    for group, count in (('PTH', 26), ('NPTH', 4)):
                        filename = Path(directory) / ('%s.drl' % group)
                        io.export_excellon('proiectCAD_%s' % group, str(filename), local_use=None, use_thread=False)
                        parser = Excellon(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                        self.assertEqual(parser.units, units)
                        self.assertEqual(sum(len(tool.get('drills', [])) for tool in parser.tools.values()), count)


if __name__ == '__main__':
    unittest.main()
