import functools
import math
import os
from pathlib import Path
import random
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

from PyQt6 import QtWidgets
import pytest
from shapely import Point
from shapely.affinity import scale, translate
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseEagleBrd import EagleBrdParseError, EagleBrdParser
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from camlib import circle_quad_segs, gerber_circle_steps
from defaults import AppDefaults


# Small inline Eagle XML boards. Units are mm, Y up. Each helper builds a complete document so a test only
# writes the part it checks: the <plain> items, the <libraries>, the <elements>, the <signals> or the rules.

LAYERS_XML = ('<layers><layer number="1" name="Top"/><layer number="16" name="Bottom"/>'
              '<layer number="20" name="Dimension"/><layer number="21" name="tPlace"/>'
              '<layer number="22" name="bPlace"/><layer number="25" name="tNames"/>'
              '<layer number="26" name="bNames"/><layer number="27" name="tValues"/>'
              '<layer number="29" name="tStop"/><layer number="30" name="bStop"/>'
              '<layer number="31" name="tCream"/><layer number="32" name="bCream"/>'
              '<layer number="45" name="Holes"/><layer number="51" name="tDocu"/></layers>')


def circle_area(radius):
    # the area of a circle as the parsers draw it: the steps of the Gerber circle preference (adaptive by default)
    return Point(0, 0).buffer(radius, quad_segs=circle_quad_segs(gerber_circle_steps(None, radius))).area


def doc(plain='', libs='', rules='', elements='', signals='', version='9.6.2', layers=LAYERS_XML):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<eagle version="%s"><drawing>%s<board>'
            '<designrules>%s</designrules><plain>%s</plain><libraries>%s</libraries>'
            '<elements>%s</elements><signals>%s</signals></board></drawing></eagle>'
            % (version, layers, rules, plain, libs, elements, signals))


def package_lib(items, name='l', package='P'):
    return '<library name="%s"><packages><package name="%s">%s</package></packages></library>' % (
        name, package, items)


def element(x, y, rot=None, package='P', name='R1', library='l', value='', extra=''):
    rot_attr = ' rot="%s"' % rot if rot else ''
    return '<element name="%s" library="%s" package="%s" value="%s" x="%s" y="%s"%s>%s</element>' % (
        name, library, package, value, x, y, rot_attr, extra)


def parse(text):
    return EagleBrdParser().parse_text(text)


def copper(parsed, name):
    return unary_union(parsed.layers[name]['solid_geometry'])


def rounded(values, digits=4):
    return [round(value, digits) for value in values]


class TestEagleParseErrors(unittest.TestCase):
    def test_rounded_mirrored_smd_keeps_copper_mask_and_paste_flashes(self):
        parsed = parse(doc(libs=package_lib(
            '<smd name="1" x="2" y="0" dx="2" dy="1" roundness="40" layer="1" rot="R35"/>'),
            elements=element(10, 10, 'MR90')))
        for layer in ('Bottom', 'bStop', 'bCream'):
            records = [r for t in parsed.layers[layer]['tools'].values()
                       if t['type'] == 'AB' for r in t['geometry']]
            self.assertEqual(len(records), 1, layer)
            self.assertAlmostEqual(records[0]['follow'].x, 10)
            self.assertAlmostEqual(records[0]['follow'].y, 12)
            self.assertLess(records[0]['solid'].symmetric_difference(copper(parsed, layer)).area, 1e-12)

    def test_source_flash_and_stroke_records_preserve_geometry(self):
        from types import SimpleNamespace
        text = doc(libs=package_lib('<pad name="1" x="0" y="0" drill="0.4" diameter="1"/>'
                                    '<smd name="2" x="2" y="0" dx="1.2" dy="0.8" layer="1"/>'),
                   elements=element(0, 0),
                   signals='<signal name="n"><wire x1="0" y1="0" x2="4" y2="0" width="0.4" layer="1"/>'
                           '<via x="4" y="0" extent="1-16" drill="0.4" diameter="1"/></signal>')
        baseline = parse(text)
        parsed = EagleBrdParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'})).parse_text(text)
        tools = parsed.layers['Top']['tools']
        self.assertTrue({'C', 'R'}.issubset({t['type'] for t in tools.values()}))
        self.assertTrue(any(getattr(r.get('follow'), 'geom_type', '') == 'LineString'
                            for t in tools.values() for r in t['geometry']))
        self.assertTrue(copper(parsed, 'Top').equals(copper(baseline, 'Top')))

    def assertParseError(self, text, pattern):
        with self.assertRaisesRegex(EagleBrdParseError, pattern):
            parse(text)

    def test_malformed_xml_is_a_parse_error(self):
        self.assertParseError(doc(plain='<wire x1="0" y1="0"'), 'Malformed XML')

    def test_non_eagle_root_is_rejected(self):
        self.assertParseError('<?xml version="1.0"?><foo/>', 'Not an Eagle file')

class TestEagleWiresAndVias(unittest.TestCase):
    def test_wire_with_width_and_round_caps_on_top(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/>'))
        self.assertEqual(list(parsed.layers), ['Top'])
        self.assertEqual(parsed.copper_layers, ['Top', 'Bottom'])
        self.assertAlmostEqual(copper(parsed, 'Top').area, 10 * 0.5 + circle_area(0.25), places=3)
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [-0.25, -0.25, 10.25, 0.25])
        self.assertEqual(parsed.warnings, [])

    def test_curved_wire_is_an_arc(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1" curve="90"/>'))
        chord = 10.0
        radius = chord / (2 * math.sin(math.radians(45)))
        expected_length = radius * math.pi / 2
        follow = parsed.layers['Top']['follow_geometry']
        self.assertEqual(len(follow), 1)
        self.assertAlmostEqual(follow[0].length, expected_length, delta=0.01)
        minx, miny, maxx, maxy = copper(parsed, 'Top').bounds
        self.assertLess(miny, -2.2)          # the arc bulges to one side of the chord
        self.assertAlmostEqual(maxy, 0.25, delta=0.01)

    def test_via_on_top_and_bottom_is_a_pth_hole(self):
        parsed = parse(doc(signals='<signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" '
                                   'diameter="0.6" shape="round"/></signal>'))
        self.assertEqual(list(parsed.drill_groups), ['PTH'])
        drills = [(point.x, point.y) for tool in parsed.drill_groups['PTH'].values() for point in tool['drills']]
        self.assertEqual(drills, [(2.0, 3.0)])
        # diameter 0.6 is smaller than drill 0.3 plus the minimum restring (0.2032 mm per side), so the
        # restring rule sets the copper: 0.3 + 2 * 0.25 * 0.3 clamped to 0.2032..0.508 -> 0.7064 mm
        for layer in ('Top', 'Bottom'):
            with self.subTest(layer=layer):
                self.assertEqual(rounded(copper(parsed, layer).bounds), [1.6468, 2.6468, 2.3532, 3.3532])
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 2), [1.55, 2.55, 2.45, 3.45])

class TestEagleThroughHolePads(unittest.TestCase):
    PAD_LIBS = '<library name="l"><packages><package name="P">%s</package></packages></library>'

    def _pad(self, shape, diameter='0.5', drill='0.8'):
        pad = '<pad name="1" x="0" y="0" drill="%s" diameter="%s" shape="%s"/>' % (drill, diameter, shape)
        return parse(doc(libs=self.PAD_LIBS % pad, elements=element(10, 10)))

    def test_round_pad_uses_restring_when_diameter_is_smaller(self):
        # drill 0.8 + 2 * clamp(0.25 * 0.8, 0.254, 0.508) = 1.308 mm copper
        parsed = self._pad('round')
        self.assertAlmostEqual(copper(parsed, 'Top').area, circle_area(0.654), delta=0.001)
        self.assertEqual(rounded(copper(parsed, 'Top').bounds, 3), [9.346, 9.346, 10.654, 10.654])
        self.assertEqual(rounded(copper(parsed, 'Bottom').bounds, 3), [9.346, 9.346, 10.654, 10.654])
        self.assertNotIn('tCream', parsed.layers)      # THT pads get no paste

    def test_pad_drill_and_stop_opening(self):
        parsed = self._pad('round')
        self.assertEqual(list(parsed.drill_groups), ['PTH'])
        drills = [(point.x, point.y) for tool in parsed.drill_groups['PTH'].values() for point in tool['drills']]
        self.assertEqual(drills, [(10.0, 10.0)])
        # stop frame: the minimum 0.1016 mm (the rule mvStopFrame x min side is 0.1016)
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 2), [9.24, 9.24, 10.76, 10.76])
        self.assertEqual(rounded(copper(parsed, 'bStop').bounds, 2), [9.24, 9.24, 10.76, 10.76])


class TestEagleSmdPads(unittest.TestCase):
    LIBS = '<library name="l"><packages><package name="S"><smd name="1" x="0" y="0" dx="1" dy="2" layer="1"%s/>' \
           '</package></packages></library>'

    def test_smd_copper_stop_and_cream(self):
        parsed = parse(doc(libs=self.LIBS % '', elements=element(10, 10, package='S')))
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [9.5, 9.0, 10.5, 11.0])
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 4), [9.3984, 8.8984, 10.6016, 11.1016])
        # cream: the default cream frame is 0, so the paste equals the pad
        self.assertAlmostEqual(copper(parsed, 'tCream').area, 2.0, places=6)
        self.assertEqual(parsed.copper_layers, ['Top', 'Bottom'])
        self.assertNotIn('Bottom', parsed.layers)

class TestEagleElementPlacement(unittest.TestCase):
    LIBS = ('<library name="l"><packages><package name="S">'
            '<smd name="1" x="2" y="0" dx="1" dy="2" layer="1"/>'
            '<wire x1="0" y1="0" x2="1" y2="0" width="0.1" layer="21"/>'
            '</package></packages></library>')

    def test_rotation_r90_turns_the_pad_and_the_silkscreen(self):
        parsed = parse(doc(libs=self.LIBS, elements=element(10, 10, rot='R90', package='S')))
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [9.0, 11.5, 11.0, 12.5])
        self.assertEqual(rounded(copper(parsed, 'tPlace').bounds), [9.95, 9.95, 10.05, 11.05])

    def test_mirror_moves_top_objects_to_bottom(self):
        parsed = parse(doc(libs=self.LIBS, elements=element(10, 10, rot='MR0', package='S')))
        self.assertNotIn('Top', parsed.layers)
        self.assertEqual(rounded(copper(parsed, 'Bottom').bounds), [7.5, 9.0, 8.5, 11.0])
        self.assertEqual(rounded(copper(parsed, 'bPlace').bounds), [8.95, 9.95, 10.05, 10.05])
        self.assertNotIn('tPlace', parsed.layers)
        self.assertIn('bStop', parsed.layers)
        self.assertNotIn('tStop', parsed.layers)

class TestEagleHolesAndDimensions(unittest.TestCase):
    def test_hole_is_npth_with_a_mask_opening(self):
        parsed = parse(doc(plain='<hole x="5" y="5" drill="3.2"/>'))
        self.assertEqual(list(parsed.drill_groups), ['NPTH'])
        drills = [(point.x, point.y, tool['tooldia']) for tool in parsed.drill_groups['NPTH'].values()
                  for point in tool['drills']]
        self.assertEqual(drills, [(5.0, 5.0, 3.2)])
        for layer in ('tStop', 'bStop'):
            with self.subTest(layer=layer):
                self.assertEqual(rounded(copper(parsed, layer).bounds, 3), [3.298, 3.298, 6.702, 6.702])
        self.assertIn('Holes', parsed.layers)

    def test_dimension_wires_form_the_board_outline(self):
        outline = ('<wire x1="0" y1="0" x2="10" y2="0" width="0" layer="20"/>'
                   '<wire x1="10" y1="0" x2="10" y2="5" width="0" layer="20"/>'
                   '<wire x1="10" y1="5" x2="0" y2="5" width="0" layer="20"/>'
                   '<wire x1="0" y1="5" x2="0" y2="0" width="0" layer="20"/>')
        parsed = parse(doc(plain=outline))
        self.assertEqual(rounded(parsed.board_outline.bounds), [0.0, 0.0, 10.0, 5.0])
        self.assertAlmostEqual(parsed.board_outline.area, 50.0, places=3)
        self.assertIn('Dimension', parsed.layers)
        self.assertEqual(len(parsed.warnings), 1)
        self.assertIn('Zero-width Dimension/Milling lines', parsed.warnings[0])

class TestEagleSilkscreenAndSmashed(unittest.TestCase):
    PKG = ('<library name="l"><packages><package name="S">'
           '<wire x1="0" y1="0" x2="1" y2="0" width="0.1" layer="21"/>'
           '<text x="0" y="3" size="1" layer="25">&gt;NAME</text>'
           '<smd name="1" x="0" y="0" dx="1" dy="2" layer="1"/>'
           '</package></packages></library>')

class TestEagleMutationFuzz(unittest.TestCase):
    """Seeded character mutations of one valid board: only EagleBrdParseError may escape."""

    BASE = doc(
        plain=('<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1" curve="20"/>'
               '<wire x1="0" y1="0" x2="0" y2="5" width="0.2" layer="20"/>'
               '<wire x1="0" y1="5" x2="10" y2="5" width="0.2" layer="20"/>'
               '<wire x1="10" y1="5" x2="10" y2="0" width="0.2" layer="20"/>'
               '<hole x="5" y="2" drill="3.2"/>'),
        libs=('<library name="l"><packages><package name="P">'
              '<pad name="1" x="0" y="0" drill="0.8" diameter="1.0" shape="long"/>'
              '<smd name="2" x="3" y="0" dx="1" dy="2" layer="1" roundness="50"/>'
              '<wire x1="0" y1="0" x2="1" y2="1" width="0.1" layer="21"/>'
              '<text x="0" y="2" size="1" layer="25">&gt;NAME</text>'
              '</package></packages></library>'),
        elements=element(20, 20, rot='R90', package='P', extra='<attribute name="VALUE" x="1" y="1" size="1" '
                                                               'layer="27"/>'),
        signals='<signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" diameter="0.6" shape="round"/>'
                '<wire x1="0" y1="0" x2="4" y2="0" width="0.2" layer="1"/></signal>')

    ALPHABET = '<>/"=&;!?-.0123456789abcdefgxyz \n'

    def test_mutated_files_never_leak_internal_errors(self):
        self.assertEqual(parse(self.BASE).default_layers[:1], ['Top'])
        rng = random.Random(20260614)
        leaked = []
        parsed_ok = 0
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            for _ in range(250):
                text = list(self.BASE)
                for _ in range(rng.randint(1, 4)):
                    text[rng.randrange(len(text))] = rng.choice(self.ALPHABET)
                mutated = ''.join(text)
                try:
                    EagleBrdParser().parse_text(mutated)
                    parsed_ok += 1
                except EagleBrdParseError as error:
                    if isinstance(error.__cause__, RuntimeWarning):
                        leaked.append(('RuntimeWarning', str(error), mutated))
                except Exception as error:      # anything else is a leak
                    leaked.append((type(error).__name__, str(error), mutated))
        self.assertEqual(leaked, [], 'first leak: %r' % (leaked[:1],))
        self.assertGreater(parsed_ok, 0)


BOARD = os.path.join('D:' + os.sep, 'boards', 'Pcb39.brd')
GOOD_BOARD = '''<?xml version="1.0" encoding="utf-8"?>
<eagle version="8.3.2"><drawing>
<layers><layer number="1" name="Top"/><layer number="16" name="Bottom"/><layer number="51" name="tDocu"/></layers>
<board><plain><wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/>
<wire x1="0" y1="2" x2="10" y2="2" width="0.5" layer="51"/><hole x="5" y="5" drill="3.2"/></plain>
<signals><signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" diameter="0.6" shape="round"/></signal></signals>
</board></drawing></eagle>
'''


class TestEagleBrdImport(unittest.TestCase):
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
        polygon = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])
        self.parsed = SimpleNamespace(
            layers={
                'Top': dict(name='layer 1', solid_geometry=[polygon],
                            follow_geometry=[LineString([(0, 0), (10, 0)])],
                            tools={0: dict(type='REG', size=0.0, geometry=[])}),
                'Bottom': dict(name='layer 16', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'tStop': dict(name='layer 29', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'Empty': dict(name='layer 9', solid_geometry=[], follow_geometry=[], tools={}),
            },
            drill_groups={
                'PTH': {1: dict(tooldia=0.8, drills=[Point(1, 1)], slots=[])},
                'NPTH': {1: dict(tooldia=3.2, drills=[Point(2, 2)], slots=[])},
            },
            default_layers=['Top', 'Bottom', 'tStop'],
            warnings=['Text is drawn with an approximate stroke font'])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['Top', 'tStop'], ['PTH', 'NPTH'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['Pcb39_Top', 'Pcb39_tStop', 'Pcb39_PTH', 'Pcb39_NPTH'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'gerber', 'excellon', 'excellon'])
        for kind, obj in self.objects:
            with self.subTest(name=obj.obj_options['name']):
                self.assertTrue(obj.obj_options['pcbdoc_import'])
                self.assertEqual(obj.units, 'MM')

    def test_inch_project_converts_objects_to_inch(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['Top'], ['PTH'])
        kind, obj = self.objects[0]
        self.assertEqual(obj.units, 'IN')


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


@pytest.mark.slow
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


@pytest.mark.slow
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
