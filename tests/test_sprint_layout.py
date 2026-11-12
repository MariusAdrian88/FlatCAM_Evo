import functools
import math
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

from PyQt6 import QtWidgets
import pytest
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from appParsers.ParseSprintLayout import SprintLayoutParseError, SprintLayoutParser
from defaults import AppDefaults

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


# Synthetic Sprint Layout 6 files, built byte by byte from the layout in the ParseSprintLayout docstring.
# Units: file units are 1e-4 mm. The stored Y is negated and measured from the board top, so a Gerber-frame
# point (x, y) on a board of height H is stored as (x, (y - H) * 1e4).

def u32(value):
    return struct.pack('<I', value)


def fixed(kind, x=0.0, y=0.0, rout=0.0, rin=0.0, width=0, layer=1, shape=1, gdist=0, thermal=0,
          cutoff=0, plated=1, mask=0, flag=1, comp=0):
    """The 77-byte fixed part of one object."""
    return (struct.pack('<B', kind) + struct.pack('<4f', x, y, rout, rin) + u32(width)
            + bytes([0, layer, shape]) + u32(0) + struct.pack('<H', comp) + bytes([0])
            + bytes(4) + bytes(5) + bytes([flag])
            + u32(gdist) + u32(0) + bytes([0, thermal, 0, cutoff]) + u32(0) + bytes([plated, mask])
            + bytes(18))


def points(pts):
    return u32(len(pts)) + b''.join(struct.pack('<ff', x, y) for x, y in pts)


def child(kind, pts, **fields):
    """A text stroke: the fixed part and its point list, no name, marker or group fields."""
    return fixed(kind, **fields) + points(pts)


def obj(kind, pts=(), children=(), text=b'', **fields):
    data = fixed(kind, **fields) + u32(len(text)) + text + u32(0) + u32(0)
    if kind == 5:
        return data
    if kind == 7:
        data += u32(len(children)) + b''.join(children)
        if fields.get('shape', 1) == 1:
            data += bytes(9) + bytes(8) + u32(0) + u32(0) + bytes(1)
        return data
    return data + points(pts)


def board(items, name=b'Test', w=200000, h=200000, plane=(0, 0, 0, 0, 0, 0, 0), multilayer=0):
    """items: list of (kind, bytes). Pads (kinds 2 and 8) get their empty connection list after the objects."""
    data = bytes([len(name)]) + name.ljust(30, b'\0')
    data += u32(0) + u32(w) + u32(h)
    data += bytes(plane) + bytes(24) + bytes([1]) + bytes(3) + bytes(7)
    data += bytes(2 + 201 + 201) + bytes(40) + bytes([multilayer])
    data += u32(len(items)) + b''.join(blob for _, blob in items)
    for kind, _ in items:
        if kind in (2, 8):
            data += u32(0)
    return data


def sprint_file(boards):
    return b'\x06\x33\xaa\xff' + u32(len(boards)) + b''.join(boards)


def fu(mm):
    """mm -> file units."""
    return round(mm * 1e4)


def fy(y_mm, height_mm):
    """Gerber-frame y (mm) -> stored file y."""
    return (y_mm - height_mm) * 1e4


BOARD_MM = 20.0
PAD_AT = (10.0, 10.0)


def pad_at(x_mm, y_mm, rout=5000, rin=3500, plated=1, mask=1, gdist=0, height=BOARD_MM, **fields):
    return obj(2, x=float(fu(x_mm)), y=float(fy(y_mm, height)), rout=rout, rin=rin,
               plated=plated, mask=mask, gdist=gdist, **fields)


def one_pad_file(**fields):
    pad = pad_at(*PAD_AT, **fields)
    return sprint_file([board([(2, pad)], w=fu(BOARD_MM), h=fu(BOARD_MM))])


def valid_two_object_file():
    """A pad and a line: the smallest file that exercises several record kinds."""
    pad = pad_at(10.0, 10.0)
    line = obj(6, pts=[(fu(2), fy(2, BOARD_MM)), (fu(8), fy(2, BOARD_MM))], width=2000, layer=1, shape=0)
    return sprint_file([board([(2, pad), (6, line)], w=fu(BOARD_MM), h=fu(BOARD_MM))])


def geo(parser, key):
    return unary_union(parser.layers[key]['solid_geometry'])


class TestSprintLayoutErrors(unittest.TestCase):
    def test_polygon_encoded_pad_is_flash_but_polygon_fill_is_region(self):
        pts = [(fu(x), fy(y, BOARD_MM)) for x, y in ((9, 9), (11, 9), (11, 11), (9, 11))]
        pad = obj(2, pts=pts, x=fu(10), y=fy(10, BOARD_MM), shape=2, rin=3000, mask=1)
        fill = obj(4, pts=[(fu(x), fy(y, BOARD_MM)) for x, y in ((1, 1), (3, 1), (2, 3))], layer=1)
        parsed = SprintLayoutParser().parse_bytes(sprint_file([board([(2, pad), (4, fill)])]))
        for layer in ('C1', 'C2', 'M1', 'M2'):
            records = [r for t in parsed.layers[layer]['tools'].values()
                       if t['type'] == 'AB' for r in t['geometry']]
            self.assertEqual(len(records), 1, layer)
            self.assertTrue(records[0]['follow'].equals(Point(10, 10)))
        self.assertEqual(len(parsed.layers['C1']['tools'][0]['geometry']), 1)

    def test_source_flash_and_stroke_records_preserve_geometry(self):
        from types import SimpleNamespace
        data = valid_two_object_file()
        baseline = SprintLayoutParser().parse_bytes(data)
        parsed = SprintLayoutParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'})).parse_bytes(data)
        records = [r for t in parsed.layers['C1']['tools'].values() if t['type'] == 'C' for r in t['geometry']]
        self.assertTrue(any(isinstance(r['follow'], Point) for r in records))
        self.assertTrue(any(r['follow'].geom_type == 'LineString' for r in records))
        self.assertTrue(geo(parsed, 'C1').equals(geo(baseline, 'C1')))

    def assertRaisesWith(self, fragment, data):
        with self.assertRaises(SprintLayoutParseError) as context:
            SprintLayoutParser().parse_bytes(data)
        self.assertIn(fragment, str(context.exception))

    def test_bad_signature_raises(self):
        self.assertRaisesWith('bad signature', b'JUNK' + bytes(50))
        self.assertRaisesWith('bad signature', b'\x06\x33\xaa\xfe' + bytes(50))

    def test_truncated_file_raises_and_clears_results(self):
        valid = valid_two_object_file()
        parser = SprintLayoutParser()
        parser.parse_bytes(valid)
        self.assertIn('C1', parser.layers)
        self.assertIn('PTH', parser.drill_groups)
        with self.assertRaises(SprintLayoutParseError):
            parser.parse_bytes(valid[:-3])
        self.assertEqual(parser.layers, {})
        self.assertEqual(parser.drill_groups, {})
        self.assertEqual(parser.default_layers, [])

    def test_non_finite_and_huge_coordinates_raise(self):
        nan_x = sprint_file([board([(2, obj(2, x=float('nan'), y=0.0, rout=5000, rin=3500))],
                                   w=fu(BOARD_MM), h=fu(BOARD_MM))])
        self.assertRaisesWith('Invalid x coordinate', nan_x)
        huge = sprint_file([board([(2, obj(2, x=2e9, y=0.0, rout=5000, rin=3500))],
                                  w=fu(BOARD_MM), h=fu(BOARD_MM))])
        self.assertRaisesWith('Invalid x coordinate', huge)

    def test_mutated_files_never_leak_internal_errors(self):
        # Random byte changes must end in a parse or a SprintLayoutParseError. The parser wraps any other
        # exception as 'Cannot read Sprint Layout file: ...', so that prefix means an unguarded code path.
        # A RuntimeWarning (e.g. a numpy 'divide by zero' from a degenerate shape) is an error here, so the
        # parser wraps it and it counts as a leak too.
        rng = random.Random(1)
        pad = pad_at(10.0, 10.0)
        smd_square = obj(8, pts=[(fu(3), fy(5, BOARD_MM)), (fu(5), fy(5, BOARD_MM)),
                                 (fu(5), fy(3, BOARD_MM)), (fu(3), fy(3, BOARD_MM))],
                         x=0.0, y=0.0, rout=0, rin=0, layer=1, shape=3, mask=1)
        line = obj(6, pts=[(fu(2), fy(2, BOARD_MM)), (fu(8), fy(2, BOARD_MM))], width=2000, layer=1, shape=0)
        txt = obj(7, children=[child(6, [(fu(3), fy(3, BOARD_MM)), (fu(6), fy(3, BOARD_MM))],
                                     width=1500, layer=2, shape=0)],
                  x=0.0, y=0.0, layer=2, shape=1, flag=1)
        arc = obj(5, x=float(fu(15)), y=float(fy(15, BOARD_MM)), rout=5000, rin=4000, layer=2, shape=1)
        poly = obj(4, pts=[(fu(12), fy(12, BOARD_MM)), (fu(16), fy(12, BOARD_MM)), (fu(14), fy(16, BOARD_MM))],
                   width=0, layer=2, shape=0)
        data = sprint_file([board([(2, pad), (8, smd_square), (6, line), (7, txt), (5, arc), (4, poly)],
                                  w=fu(BOARD_MM), h=fu(BOARD_MM))])
        leaked = []
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            for _ in range(300):
                blob = bytearray(data)
                for _ in range(rng.choice((1, 3, 5))):
                    blob[rng.randrange(4, len(blob))] = rng.randrange(256)
                try:
                    SprintLayoutParser().parse_bytes(bytes(blob))
                except SprintLayoutParseError as error:
                    if str(error).startswith('Cannot read Sprint Layout file'):
                        leaked.append(str(error))
        self.assertEqual(leaked, [])

class TestSprintLayoutPads(unittest.TestCase):
    def test_tht_pad_is_copper_on_both_sides_with_pth_drill(self):
        parser = SprintLayoutParser().parse_bytes(one_pad_file())
        self.assertEqual(list(parser.layers), ['C1', 'C2', 'M1', 'M2'])
        top = geo(parser, 'C1')
        self.assertAlmostEqual(top.area, math.pi * 0.5 ** 2, delta=0.01)
        self.assertEqual(len(top.bounds), 4)
        for actual, expected in zip(top.bounds, (9.5, 9.5, 10.5, 10.5)):
            self.assertAlmostEqual(actual, expected, delta=1e-3)
        self.assertLess(geo(parser, 'C2').symmetric_difference(top).area, 1e-9)
        self.assertEqual(list(parser.drill_groups), ['PTH'])
        tool = parser.drill_groups['PTH'][1]
        self.assertAlmostEqual(tool['tooldia'], 0.7, places=4)
        self.assertEqual(len(tool['drills']), 1)
        self.assertAlmostEqual(tool['drills'][0].x, 10.0, places=6)
        self.assertAlmostEqual(tool['drills'][0].y, 10.0, places=6)
        self.assertEqual(tool['slots'], [])


    def test_smd_pad_is_copper_and_mask_on_its_own_layer_only(self):
        square = obj(8, pts=[(fu(3), fy(5, BOARD_MM)), (fu(5), fy(5, BOARD_MM)),
                             (fu(5), fy(3, BOARD_MM)), (fu(3), fy(3, BOARD_MM))],
                     x=0.0, y=0.0, rout=0, rin=0, layer=1, shape=3, mask=1)
        parser = SprintLayoutParser().parse_bytes(
            sprint_file([board([(8, square)], w=fu(BOARD_MM), h=fu(BOARD_MM))]))
        self.assertEqual(list(parser.layers), ['C1', 'M1'])
        self.assertEqual(parser.drill_groups, {})
        for actual, expected in zip(geo(parser, 'C1').bounds, (3.0, 3.0, 5.0, 5.0)):
            self.assertAlmostEqual(actual, expected, delta=1e-6)
        for actual, expected in zip(geo(parser, 'M1').bounds, (2.88, 2.88, 5.12, 5.12)):
            self.assertAlmostEqual(actual, expected, delta=1e-6)


class TestSprintLayoutLines(unittest.TestCase):
    def test_line_with_width_is_a_round_capped_stroke(self):
        line = obj(6, pts=[(fu(2), fy(2, BOARD_MM)), (fu(8), fy(2, BOARD_MM))], width=2000, layer=1, shape=0)
        parser = SprintLayoutParser().parse_bytes(sprint_file([board([(6, line)], w=fu(BOARD_MM), h=fu(BOARD_MM))]))
        self.assertEqual(list(parser.layers), ['C1'])
        stroke = geo(parser, 'C1')
        # 6 mm long, 0.2 mm wide, round caps
        self.assertAlmostEqual(stroke.area, 6 * 0.2 + math.pi * 0.1 ** 2, delta=1e-3)
        for actual, expected in zip(stroke.bounds, (1.9, 1.9, 8.1, 2.1)):
            self.assertAlmostEqual(actual, expected, delta=1e-3)
        self.assertTrue(stroke.contains(Point(8.09, 2.0)))
        # a square cap would reach this corner; the round cap does not
        self.assertFalse(stroke.contains(Point(8.05, 2.09)))

class TestSprintLayoutFrame(unittest.TestCase):
    def test_units_and_y_frame(self):
        # Pad at Gerber (5, 17) on a 20 mm board: stored y is (17 - 20) * 1e4 = -30000
        pad = obj(2, x=float(fu(5)), y=float(fy(17, BOARD_MM)), rout=5000, rin=3500, plated=1, mask=0)
        parser = SprintLayoutParser().parse_bytes(sprint_file([board([(2, pad)], w=fu(BOARD_MM), h=fu(BOARD_MM))]))
        self.assertEqual(parser.board_size, (20.0, 20.0))
        self.assertEqual(parser.board_count, 1)
        self.assertEqual(parser.board_name, 'Test')
        tool = parser.drill_groups['PTH'][1]
        self.assertAlmostEqual(tool['tooldia'], 0.7, places=4)
        self.assertAlmostEqual(tool['drills'][0].x, 5.0, places=6)
        self.assertAlmostEqual(tool['drills'][0].y, 17.0, places=6)


class TestSprintLayoutGroundPlane(unittest.TestCase):
    def test_plane_is_board_minus_clearance_with_pad_island(self):
        # Plane on C1 only. Clearance 0.3 mm around a 1 mm pad: the cleared disc has radius 0.8 mm.
        flags = (1, 0, 0, 0, 0, 0, 0)
        board_data = sprint_file([board([(2, pad_at(10.0, 10.0, gdist=3000, mask=0))],
                                        w=fu(BOARD_MM), h=fu(BOARD_MM), plane=flags)])
        parser = SprintLayoutParser().parse_bytes(board_data)
        top = geo(parser, 'C1')
        expected = BOARD_MM ** 2 - math.pi * (0.8 ** 2 - 0.5 ** 2)
        self.assertAlmostEqual(top.area, expected, delta=0.01)
        self.assertTrue(top.contains(Point(10.0, 10.0)))      # the pad itself is an island
        self.assertFalse(top.contains(Point(10.0, 10.6)))     # inside the clearance ring
        self.assertTrue(top.contains(Point(10.0, 10.9)))      # outside the clearance ring
        self.assertTrue(top.contains(Point(1.0, 1.0)))
        self.assertEqual(len(parser.warnings), 1)
        self.assertIn('C1 (Copper Top)', parser.warnings[0])
        self.assertNotIn('C2', parser.warnings[0])
        # C2 has no plane: only the pad
        self.assertAlmostEqual(geo(parser, 'C2').area, math.pi * 0.5 ** 2, delta=0.01)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


FILE = os.path.join('D:' + os.sep, 'boards', 'cata.lay6')


def _layer(name, polygon):
    return dict(name=name, solid_geometry=[polygon], follow_geometry=[LineString([(0, 0), (10, 0)])],
                tools={0: dict(type='REG', size=0.0, geometry=[{'solid': polygon}])})


class TestSprintLayoutImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock(),
                                   ui=QtWidgets.QWidget(), get_last_folder=Mock(return_value=''))
        self.app.projects = SimpleNamespace(is_busy=lambda: False,
                                            active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
        self.app.project_filename, self.app.should_we_save = None, False
        self.io = appIO(self.app)
        polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        # Same layer keys, names and default selection as the cata.lay6 fixture, plus one non-default layer.
        self.parsed = SimpleNamespace(
            layers={
                'C1': _layer('Copper Top', polygon),
                'C2': _layer('Copper Bottom', polygon),
                'M1': _layer('Solder Mask Top', polygon),
                'M2': _layer('Solder Mask Bottom', polygon),
                'S1': _layer('Silkscreen Top', polygon),
                'S2': _layer('Silkscreen Bottom', polygon),
                'O': _layer('Board Outline', polygon),
            },
            drill_groups={'PTH': {1: dict(tooldia=0.7, drills=[Point(2, 2)], slots=[])}},
            default_layers=['C1', 'C2', 'M1', 'M2', 'S1', 'O'],
            warnings=[])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj_ = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj_, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj_))
            return obj_

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_sprint_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(FILE, self.parsed, ['C1', 'M1', 'O'], ['PTH'])
        self.assertEqual([obj_.obj_options['name'] for kind, obj_ in self.objects],
                         ['cata_C1', 'cata_M1', 'cata_O', 'cata_PTH'])
        self.assertEqual([kind for kind, obj_ in self.objects], ['gerber', 'gerber', 'gerber', 'excellon'])
        for kind, obj_ in self.objects:
            with self.subTest(name=obj_.obj_options['name']):
                self.assertTrue(obj_.obj_options['pcbdoc_import'])
                self.assertEqual(obj_.units, 'MM')

    def test_inch_import_scales_drill_tooldia_and_sets_units(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects(FILE, self.parsed, ['C1'], ['PTH'])
        excellon = [obj_ for kind, obj_ in self.objects if kind == 'excellon'][0]
        self.assertEqual(excellon.units, 'IN')
        self.assertAlmostEqual(excellon.tools[1]['tooldia'], 0.7 / 25.4, places=6)


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


@pytest.mark.slow
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


@pytest.mark.slow
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
