import functools
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtWidgets
from shapely import Point
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, LineString, Polygon
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appCommon.ProjectFile import load_project_file, save_project_file
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from appParsers.ParseKiCadPcb import KiCadPcbParser, KiCadPcbParseError
from camlib import circle_quad_segs, dict2obj, geometry_wkt_cache, gerber_circle_steps, project_json_default
from defaults import AppDefaults

# Two copper layers: F.Cu, B.Cu. Tenting front, so only the back via mask is generated.
HEAD = '''(kicad_pcb (version 20240108) (generator "test")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen") (39 "F.Mask" user)
  (41 "B.Mask" user) (35 "F.Paste" user) (25 "Edge.Cuts" user) (49 "F.Fab" user) (45 "B.Fab" user))
 (setup (pad_to_mask_clearance 0.05) (tenting (front yes) (back no)))
'''

# Four copper layers: F.Cu, In1.Cu, In2.Cu, B.Cu.
HEAD_INNER = HEAD.replace('(31 "B.Cu" signal)',
                          '(1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)')


def circle_area(radius):
    # the area of a circle as the parsers draw it: the steps of the Gerber circle preference (adaptive by default)
    return Point(0, 0).buffer(radius, quad_segs=circle_quad_segs(gerber_circle_steps(None, radius))).area


def parse(body, head=HEAD):
    return KiCadPcbParser().parse_text(head + body + ')')


def footprint(body, at='(at 0 0)'):
    return '(footprint "T" (layer "F.Cu") %s %s)' % (at, body)


def geo(parsed, layer):
    return unary_union(parsed.layers[layer]['solid_geometry'])


def groups_summary(parsed):
    return {group: {tool['tooldia']: (len(tool['drills']), len(tool['slots']))
                    for tool in tools.values()}
            for group, tools in parsed.drill_groups.items()}


class TestKiCadPcbTracks(unittest.TestCase):
    def test_parse_text_returns_self_and_flips_y(self):
        parser = KiCadPcbParser()
        self.assertIs(parser.parse_text(HEAD + '(segment (start 1 2) (end 11 2) (width 0.5) '
                                        '(layer "F.Cu") (net 1))' + ')'), parser)
        for actual, expected in zip(geo(parser, 'F.Cu').bounds, (0.75, -2.25, 11.25, -1.75)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(parser.version, 20240108)
        self.assertEqual(parser.copper_layers, ['F.Cu', 'B.Cu'])

    def test_arc_passes_through_start_mid_and_end(self):
        parser = parse('(arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2) (layer "F.Cu") (net 1))')
        follow = parser.layers['F.Cu']['follow_geometry'][0]
        coords = list(follow.coords)
        self.assertAlmostEqual(coords[0][0], 0, places=6)
        self.assertAlmostEqual(coords[0][1], 0, places=6)
        self.assertAlmostEqual(coords[-1][0], 2, places=6)
        self.assertAlmostEqual(coords[-1][1], 0, places=6)
        # File mid (1, 1) is output (1, -1): the arc bulges to negative output Y.
        self.assertLess(follow.distance(Point(1, -1)), 0.01)
        # The file's mid point (1, 1) lies on the circle's other half, away from the drawn arc.
        self.assertGreater(follow.distance(Point(1, 1)), 1.0)
        bounds = geo(parser, 'F.Cu').bounds
        for actual, expected in zip(bounds, (-0.1, -1.1, 2.1, 0.1)):
            self.assertAlmostEqual(actual, expected, places=2)

class TestKiCadPcbVias(unittest.TestCase):
    def test_through_via_on_all_copper_layers_with_drill_group(self):
        parser = parse('(via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))')
        self.assertIn('F.Cu', parser.layers)
        self.assertIn('B.Cu', parser.layers)
        for layer in ('F.Cu', 'B.Cu'):
            for actual, expected in zip(geo(parser, layer).bounds, (4.6, -5.4, 5.4, -4.6)):
                self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(list(parser.drill_groups), ['PTH_F.Cu-B.Cu'])
        tool = list(parser.drill_groups['PTH_F.Cu-B.Cu'].values())[0]
        self.assertAlmostEqual(tool['tooldia'], 0.4)
        self.assertEqual((tool['drills'][0].x, tool['drills'][0].y), (5, -5))

    def test_blind_via_gets_its_own_span_group_and_only_its_layers(self):
        parser = parse('(via blind (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "In1.Cu") (net 1))',
                       HEAD_INNER)
        self.assertIn('F.Cu', parser.layers)
        self.assertIn('In1.Cu', parser.layers)
        self.assertNotIn('B.Cu', parser.layers)
        self.assertNotIn('In2.Cu', parser.layers)
        self.assertEqual(list(parser.drill_groups), ['PTH_F.Cu-In1.Cu'])

class TestKiCadPcbPads(unittest.TestCase):
    def test_rotated_and_rounded_source_flashes_survive_on_all_pad_layers(self):
        parser = parse(footprint(''.join(
            '(pad "%d" smd %s (at %d 2 35) (size 2 1) %s '
            '(layers "F.Cu" "F.Mask" "F.Paste"))' % (index, shape, index * 4, extra)
            for index, (shape, extra) in enumerate((('rect', ''), ('oval', ''),
                                                    ('roundrect', '(roundrect_rratio 0.25)'))))))
        for layer in ('F.Cu', 'F.Mask', 'F.Paste'):
            records = [record for tool in parser.layers[layer]['tools'].values()
                       if tool['type'] == 'AB' for record in tool['geometry']]
            self.assertEqual(len(records), 3, layer)
            self.assertEqual([(r['follow'].x, r['follow'].y) for r in records],
                             [(0, -2), (4, -2), (8, -2)])
            self.assertLess(unary_union([r['solid'] for r in records]).symmetric_difference(
                geo(parser, layer)).area, 1e-12)

    def test_pad_shapes_have_expected_outlines(self):
        cases = {
            'circle': ('(size 2 2)', circle_area(1)),
            'rect': ('(size 2 1)', 2.0),
            'oval': ('(size 2 1)', 1 + 3.14159265 / 4),
            'roundrect': ('(size 2 1) (roundrect_rratio 0.5)', 1 + 3.14159265 / 4),
        }
        for shape, (size, area) in cases.items():
            with self.subTest(shape=shape):
                parser = parse(footprint('(pad "1" smd %s (at 0 0) %s (layers "F.Cu"))' % (shape, size)))
                self.assertAlmostEqual(geo(parser, 'F.Cu').area, area, places=2)

    def test_footprint_rotation_moves_pad_centre(self):
        # Footprint at (10, 10) rotated 90 degrees: local (2, 0) lands at board (10, 8) in KiCad
        # coordinates, which is output (10, -8).
        parser = parse(footprint('(pad "1" thru_hole circle (at 2 0) (size 1.6 1.6) (drill 0.8) '
                                 '(layers "*.Cu"))', at='(at 10 10 90)'))
        tool = list(parser.drill_groups['PTH_F.Cu-B.Cu'].values())[0]
        self.assertAlmostEqual(tool['drills'][0].x, 10, places=6)
        self.assertAlmostEqual(tool['drills'][0].y, -8, places=6)

    def test_mask_margin_grows_the_mask_opening(self):
        parser = parse(footprint('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))'))
        for actual, expected in zip(geo(parser, 'F.Mask').bounds, (-0.55, -0.55, 0.55, 0.55)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_thru_hole_oval_drill_becomes_slot(self):
        parser = parse(footprint('(pad "6" thru_hole oval (at 0 0) (size 1.6 3) (drill oval 0.8 2) '
                                 '(layers "*.Cu" "*.Mask"))'))
        tools = parser.drill_groups['PTH_F.Cu-B.Cu']
        self.assertEqual(groups_summary(parser), {'PTH_F.Cu-B.Cu': {0.8: (0, 1)}})
        first, last = list(tools.values())[0]['slots'][0]
        self.assertAlmostEqual(first.x, 0, places=6)
        self.assertAlmostEqual(last.x, 0, places=6)
        self.assertAlmostEqual(sorted([first.y, last.y])[0], -0.6, places=6)
        self.assertAlmostEqual(sorted([first.y, last.y])[1], 0.6, places=6)

    def test_non_plated_hole_goes_to_npth_group(self):
        parser = parse(footprint('(pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 1) '
                                 '(layers "*.Cu" "*.Mask"))'))
        self.assertEqual(groups_summary(parser), {'NPTH_F.Cu-B.Cu': {1.0: (1, 0)}})
        self.assertIn('F.Cu', parser.layers)           # annular ring larger than the hole

    def test_unknown_pad_shape_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse(footprint('(pad "1" smd weird (at 0 0) (size 2 1) (layers "F.Cu"))'))

class TestKiCadPcbZones(unittest.TestCase):
    SQUARE = '(polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))'

    def test_zone_uses_saved_filled_polygon(self):
        parser = parse('(zone (net 1) (net_name "GND") (layer "F.Cu") (fill yes (thermal_gap 0.2)) %s '
                       '(filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))'
                       % self.SQUARE)
        self.assertAlmostEqual(geo(parser, 'F.Cu').area, 100, places=6)
        for actual, expected in zip(geo(parser, 'F.Cu').bounds, (0, -10, 10, 0)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(parser.warnings, [])

    def test_zone_keeps_a_hole_from_a_keyhole_fill(self):
        # Outer square 0..10 with a 2x2 hole at 4..6, joined to the outline by a zero-width bridge.
        ring = ('(xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10) (xy 0 5) (xy 4 5) (xy 4 6) (xy 6 6) '
                '(xy 6 4) (xy 4 4) (xy 4 5) (xy 0 5)')
        parser = parse('(zone (net 1) (net_name "GND") (layer "F.Cu") (fill yes) %s '
                       '(filled_polygon (layer "F.Cu") (pts %s)))' % (self.SQUARE, ring))
        copper = geo(parser, 'F.Cu')
        self.assertAlmostEqual(copper.area, 96, places=6)
        self.assertFalse(copper.contains(Point(5, -5)))  # inside the hole (output Y negated)
        self.assertTrue(copper.contains(Point(1, -1)))

class TestKiCadPcbText(unittest.TestCase):
    def test_hidden_property_is_not_drawn(self):
        parser = parse(footprint(
            '(property "Reference" "R1" (at 0 -2 0) (layer "F.SilkS") (hide yes) '
            '(effects (font (size 1 1) (thickness 0.15))))'))
        self.assertNotIn('F.SilkS', parser.layers)

class TestKiCadPcbOutline(unittest.TestCase):
    def test_closed_edge_cuts_rectangle_becomes_board_outline(self):
        parser = parse('(gr_rect (start 0 0) (end 20 10) (stroke (width 0.1) (type default)) '
                       '(fill none) (layer "Edge.Cuts"))')
        self.assertAlmostEqual(parser.board_outline.area, 200, places=1)
        for actual, expected in zip(parser.board_outline.bounds, (0, -10, 20, 0)):
            self.assertAlmostEqual(actual, expected, places=3)

class TestKiCadPcbErrors(unittest.TestCase):
    def test_unreadable_boards_raise_a_parse_error(self):
        for text in ('(foo)', '(kicad_pcb (version 20210101) (layers (0 "F.Cu" signal)))', HEAD,
                     HEAD + '(segment (start 0 0) (end 1 0) (width 0.2) (layer "In3.Cu")))'):
            with self.subTest(text=text[:40]):
                with self.assertRaises(KiCadPcbParseError):
                    KiCadPcbParser().parse_text(text)


BOARD = os.path.join('D:' + os.sep, 'boards', 'Easyduino_RP2040.kicad_pcb')
MINIMAL_BOARD = '''(kicad_pcb (version 20240108) (generator "test")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen") (25 "Edge.Cuts" user))
 (segment (start 0 0) (end 5 0) (width 0.3) (layer "F.Cu") (net 1)))
'''


class TestKiCadPcbImportDialog(unittest.TestCase):
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
        polygon = Polygon([(-25.4, 0), (25.4, 0), (25.4, 25.4), (-25.4, 25.4)],
                          [[(-5, 5), (5, 5), (5, 10), (-5, 10)]])
        self.parsed = SimpleNamespace(
            layers={
                'F.Cu': dict(name='F.Cu', solid_geometry=[polygon],
                             follow_geometry=[LineString([(0, 0), (25.4, 0)])],
                             tools={10: dict(type='C', size=2.54, geometry=[])}),
                'In1.Cu': dict(name='In1.Cu', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'F.Fab': dict(name='F.Fab', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'F.SilkS': dict(name='F.Silkscreen', solid_geometry=[polygon], follow_geometry=[], tools={}),
                'Edge.Cuts': dict(name='Edge.Cuts', solid_geometry=[polygon], follow_geometry=[], tools={}),
            },
            drill_groups={'PTH_F.Cu-B.Cu': {1: dict(tooldia=2.54, drills=[Point(25.4, 0)],
                                                    slots=[(Point(0, 0), Point(25.4, 0))])}},
            default_layers=['F.Cu', 'In1.Cu', 'Edge.Cuts', 'F.SilkS'],
            warnings=[])
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

    def test_kicad_objects_are_named_from_layer_and_drill_group(self):
        self.io.import_pcbdoc_objects(BOARD, self.parsed, ['F.Cu'], ['PTH_F.Cu-B.Cu'])
        self.assertEqual([obj.obj_options['name'] for kind, obj in self.objects],
                         ['Easyduino_RP2040_F_Cu', 'Easyduino_RP2040_PTH_F_Cu-B_Cu'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'excellon'])
        for kind, obj in self.objects:
            with self.subTest(name=obj.obj_options['name']):
                self.assertTrue(obj.obj_options['pcbdoc_import'])
                self.assertEqual(obj.units, 'MM')

    def test_dialog_accept_queues_only_selected_layers_and_drill_groups(self):
        self.app.ui = QtWidgets.QWidget()

        def accepted(dialog):
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text() == 'In1.Cu':
                    box.setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed(BOARD, self.parsed)
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['fcn'], self.io.import_pcbdoc_objects)
        self.assertEqual(job['params'][0], BOARD)
        self.assertEqual(job['params'][2:], [['F.Cu', 'F.SilkS', 'Edge.Cuts'], ['PTH_F.Cu-B.Cu']])


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


@pytest.mark.slow
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


@pytest.mark.slow
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


@pytest.mark.slow
@pytest.mark.parametrize('units', ['MM', 'IN'])
def test_native_primitives_survive_object_creation_and_gerber_roundtrip(units, tmp_path):
    app = _app(units)
    app.options['gerber_board_import_mode'] = 'primitives'
    app.options['gerber_exp_decimals'] = 6
    text = '''(kicad_pcb (version 20240108)
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
      (segment (start 0 0) (end 4 0) (width 0.4) (layer "F.Cu") (net 1))
      (via (at 4 0) (size 1) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
      (footprint "pads" (layer "F.Cu") (at 0 0)
        (pad "1" smd rect (at 0 0) (size 1.2 0.8) (layers "F.Cu"))
        (pad "2" smd oval (at 2 0 90) (size 1.4 0.6) (layers "F.Cu"))
        (pad "3" smd rect (at 7 0 30) (size 1.2 0.8) (layers "F.Cu")))
      (zone (net 1) (layer "F.Cu")
        (filled_polygon (layer "F.Cu") (pts (xy -1 -1) (xy 1 -1) (xy 1 -3) (xy -1 -3)))))'''
    baseline = KiCadPcbParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'})).parse_text(text)
    assert {tool['type'] for tool in baseline.layers['F.Cu']['tools'].values()} == {'REG'}
    parsed = KiCadPcbParser(app=app).parse_text(text)
    tools = parsed.layers['F.Cu']['tools']
    assert {tool['type'] for tool in tools.values()} == {'REG', 'C', 'R', 'O', 'AB'}
    blocks = [r for t in tools.values() if t['type'] == 'AB' for r in t['geometry']]
    assert len(blocks) == 1
    assert blocks[0]['follow'].equals(Point(7, 0))
    assert any(isinstance(record.get('follow'), Point) for tool in tools.values()
               for record in tool['geometry'])
    assert any(getattr(record.get('follow'), 'geom_type', '') == 'LineString'
               for tool in tools.values() for record in tool['geometry'])
    assert _union(parsed.layers['F.Cu']['solid_geometry']).equals(
        _union(baseline.layers['F.Cu']['solid_geometry']))

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    io = appIO(app)
    objects = {}

    def create(kind, name, initialize, **kwargs):
        obj = NativeGerberObject(name, app) if kind == 'gerber' else NativeExcellonObject(name, app)
        assert initialize(obj, app) != 'fail'
        objects[kind] = obj
        return obj

    app.app_obj.new_object.side_effect = create
    assert io.import_pcbdoc_objects('primitives.kicad_pcb', parsed, ['F.Cu'], list(parsed.drill_groups)) is None
    obj = objects['gerber']
    assert not Gerber.uses_region_export(obj)
    assert objects['excellon'].tools
    state = {field: getattr(obj, field) for field in (
        'obj_options', 'tools', 'solid_geometry', 'follow_geometry', 'units', 'source_file')}
    project_file = tmp_path / 'primitives.FlatPrj'
    save_project_file(str(project_file), state, default=project_json_default(geometry_wkt_cache(state)))
    saved = load_project_file(str(project_file), object_hook=dict2obj)
    restored = NativeGerberObject('restored', app)
    restored.ser_attrs = list(state)
    restored.from_dict(saved)
    # The project object loader restores JSON's string tool keys to integers.
    restored.tools = {int(key): tool for key, tool in restored.tools.items()}
    assert not Gerber.uses_region_export(restored)
    assert {tool['type'] for tool in restored.tools.values()} == {'REG', 'C', 'R', 'O', 'AB'}
    filename = tmp_path / 'primitives.gbr'
    filename.write_text(io.export_gerber('restored', str(filename), local_use=restored, use_thread=False))
    reloaded = Gerber(app)
    assert reloaded.parse_file(str(filename)) != 'fail'
    assert {'C', 'R', 'O'}.issubset({tool['type'] for tool in reloaded.tools.values()})
    assert any(isinstance(record.get('follow'), Point) for tool in reloaded.tools.values()
               for record in tool.get('geometry', []))
    factor = 1 if units == 'MM' else 1 / 25.4
    expected = scale(_union(baseline.layers['F.Cu']['solid_geometry']),
                     xfact=factor, yfact=factor, origin=(0, 0))
    assert expected.symmetric_difference(_union(reloaded.solid_geometry)).area < 0.002 * factor ** 2


@pytest.mark.slow
@pytest.mark.parametrize('module,parser_name,fixture', [
    ('ParseKiCadPcb', 'KiCadPcbParser', 'kicad/Easyduino_RP2040.kicad_pcb'),
    ('ParsePcbDoc', 'PcbDocParser', 'altium/I2C_level_shifter.PcbDoc'),
    ('ParseCSPcbDoc', 'CSPcbDocParser', 'altium_cs/PCI Express_4-Port_Serial_Interface.CSPcbDoc'),
    ('ParseSprintLayout', 'SprintLayoutParser', 'sprint_layout_6/cata.lay6'),
    ('ParseEagleBrd', 'EagleBrdParser', 'eagle_cad/Pcb39.brd'),
    ('ParsePADS', 'PADSParser', 'pads/LCORE_2.pcb'),
    ('ParseAllegroBrd', 'AllegroBrdParser', 'orcad_17.2/proiectCAD.brd'),
])
def test_other_native_source_records_survive_gerber_roundtrip(module, parser_name, fixture, tmp_path):
    import importlib
    app = _app('MM')
    app.options.update(gerber_board_import_mode='primitives', gerber_exp_decimals=6)
    filename = Path(__file__).parent / 'fixtures' / fixture
    parser_class = getattr(importlib.import_module('appParsers.' + module), parser_name)
    parsed = parser_class(app=app).parse_file(filename)
    layers = []
    for key, layer in parsed.layers.items():
        follows = [r.get('follow') for t in layer['tools'].values() for r in t.get('geometry', [])]
        if any(isinstance(f, Point) for f in follows) and any(getattr(f, 'geom_type', '') == 'LineString' for f in follows):
            layers.append(key)
    assert layers, 'the board must retain both source flashes and source tracks'
    keys = [1, 32, 35, 36, 37, 38, 33, 34, 57] if module == 'ParsePcbDoc' else layers[:1]
    owned_format = module in ('ParseKiCadPcb', 'ParseEagleBrd', 'ParseSprintLayout')
    if owned_format:
        keys = list(parsed.layers)
        expected_blocks = {
            'ParseKiCadPcb': {'F.Cu': 156, 'F.Mask': 156, 'F.Paste': 160},
            'ParseEagleBrd': {'Top': 36, 'Bottom': 36, 'tStop': 36, 'bStop': 36},
            'ParseSprintLayout': {'C1': 10, 'C2': 10, 'M1': 10, 'M2': 10},
        }[module]
        for key, count in expected_blocks.items():
            assert sum(len(t['geometry']) for t in parsed.layers[key]['tools'].values()
                       if t['type'] == 'AB') == count
    objects = []

    def create(kind, name, initialize, **kwargs):
        obj = NativeGerberObject(name, app)
        assert initialize(obj, app) != 'fail'
        objects.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    io = appIO(app)
    assert io.import_pcbdoc_objects(str(filename), parsed, keys, []) is None
    for key, obj in zip(keys, objects):
        if module == 'ParsePcbDoc':
            source_follows = [r.get('follow') for t in parsed.layers[key]['tools'].values()
                              for r in t['geometry']]
            assert sum(getattr(f, 'geom_type', '') == 'LineString' for f in source_follows) == \
                {1: 80, 32: 20, 33: 48, 34: 27, 57: 26}.get(key, 0)
            if key not in (1, 32):
                assert all(t['type'] != 'REG' for t in parsed.layers[key]['tools'].values())
        exported = tmp_path / ('native_%s.gbr' % key)
        exported.write_text(obj.source_file)
        reloaded = Gerber(app)
        assert reloaded.parse_file(str(exported)) != 'fail'
        follows = [r.get('follow') for t in reloaded.tools.values() for r in t.get('geometry', [])]
        flashes = [f for f in follows if isinstance(f, Point)]
        if owned_format:
            source_flashes = [r['follow'] for t in parsed.layers[key]['tools'].values()
                              for r in t['geometry'] if isinstance(r.get('follow'), Point)]
            assert len(flashes) == len(source_flashes)
            unmatched = list(source_flashes)
            for flash in flashes:
                index = min(range(len(unmatched)), key=lambda i: flash.distance(unmatched[i]))
                assert flash.distance(unmatched.pop(index)) < 0.000001
        elif module != 'ParsePcbDoc' or key in (1, 32, 35, 36, 37, 38):
            assert flashes
        if module == 'ParsePcbDoc' and key in (1, 32, 35, 36, 37, 38):
            assert len(flashes) == {1: 22, 32: 20, 35: 14, 36: 12, 37: 22, 38: 20}[key]
            source_flashes = [r['follow'] for t in parsed.layers[key]['tools'].values()
                              for r in t['geometry'] if isinstance(r.get('follow'), Point)]
            assert sorted((round(f.x, 5), round(f.y, 5)) for f in flashes) == sorted(
                (round(f.x, 5), round(f.y, 5)) for f in source_flashes)
        if key in layers or (module == 'ParsePcbDoc' and key in (33, 34, 57)):
            assert any(getattr(f, 'geom_type', '') == 'LineString' for f in follows)
        expected = _union(parsed.layers[key]['solid_geometry'])
        difference = expected.symmetric_difference(_union(reloaded.solid_geometry))
        # Both readers tessellate curves to the default 2 um deviation; export rounds to a 1 nm grid.
        assert difference.buffer(-0.003).is_empty
        assert difference.area < expected.length * 0.003


@pytest.mark.slow
def test_clipped_source_feature_falls_back_without_losing_overlaps_holes_or_islands(tmp_path):
    from appParsers.board_primitives import BoardPrimitives
    from shapely.geometry import LineString, box
    from camlib import circle_quad_segs, gerber_circle_steps

    app = _app('MM')
    app.options['gerber_board_import_mode'] = 'primitives'
    app.options['gerber_exp_decimals'] = 6
    recorder = BoardPrimitives(app.options)
    quad = circle_quad_segs(gerber_circle_steps(app.options, 1, 'MM'))
    intact = Point(0, 0).buffer(1, quad_segs=quad)
    clipped = Point(4, 0).buffer(1, quad_segs=quad)
    track = LineString([(0, 0), (4, 0)])
    stroke = track.buffer(0.2)
    ring = box(-3, -3, 8, 3).difference(box(-2, 1, 2, 2))
    island = box(-1.5, 1.2, -1, 1.7)
    target = unary_union([ring, island, intact, clipped, stroke]).difference(box(4, -2, 6, 2))
    recorder.flash('top', intact, (0, 0), 'circle', 2, 2)
    recorder.flash('top', clipped, (4, 0), 'circle', 2, 2)
    recorder.stroke('top', stroke, track, 0.4)
    result = {'solid_geometry': [target], 'tools': {}}
    recorder.finish('top', result)
    flashes = [record for tool in result['tools'].values() for record in tool['geometry']
               if isinstance(record.get('follow'), Point)]
    assert len(flashes) == 1 and flashes[0]['follow'].equals(Point(0, 0))
    obj = NativeGerberObject('clipped', app)
    obj.obj_options.update(pcbdoc_import=True, pcbdoc_primitives=True)
    obj.tools, obj.solid_geometry = result['tools'], [target]
    filename = tmp_path / 'clipped.gbr'
    filename.write_text(appIO(app).export_gerber('clipped', str(filename), local_use=obj, use_thread=False))
    reloaded = Gerber(app)
    assert reloaded.parse_file(str(filename)) != 'fail'
    assert target.symmetric_difference(_union(reloaded.solid_geometry)).area < 0.002


@pytest.mark.slow
@pytest.mark.parametrize('pads', [1, 70])      # 1: the GEOS path, 70: the Clipper2 path (64 shapes or more)
def test_pads_in_a_pour_stay_connected_to_it_in_the_exported_gerber(tmp_path, pads):
    from appParsers.board_primitives import BoardPrimitives
    from shapely.geometry import box

    app = _app('MM')
    app.options['gerber_board_import_mode'] = 'primitives'
    recorder = BoardPrimitives(app.options)
    pour = box(0, 0, 40, 40)
    # off the 4-decimal export grid, so the export rounds the region vertices and the flash positions differently
    centres = [(1.234567 + 3.7 * (i % 10), 1.765432 + 3.7 * (i // 10)) for i in range(pads)]
    for x, y in centres:
        recorder.flash('top', Point(x, y).buffer(0.6, quad_segs=16), (x, y), 'circle', 1.2, 1.2)
    result = {'solid_geometry': [pour], 'tools': {}}
    recorder.finish('top', result)
    assert sum(isinstance(r.get('follow'), Point) for t in result['tools'].values() for r in t['geometry']) == pads
    obj = NativeGerberObject('pour', app)
    obj.obj_options.update(pcbdoc_import=True, pcbdoc_primitives=True)
    obj.records_in_order = True
    obj.tools, obj.solid_geometry = result['tools'], [pour]
    filename = tmp_path / 'pour.gbr'
    filename.write_text(appIO(app).export_gerber('pour', str(filename), local_use=obj, use_thread=False))
    reloaded = Gerber(app)
    assert reloaded.parse_file(str(filename)) != 'fail'
    # one copper island: no pad is cut off from the pour by a hairline gap
    copper = _union(reloaded.solid_geometry)
    assert copper.geom_type == 'Polygon' and not copper.interiors

