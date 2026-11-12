import math
import random
import unittest
import warnings

from shapely import Point
from shapely.ops import unary_union

from camlib import circle_quad_segs, gerber_circle_steps

from appParsers.ParseEagleBrd import EagleBrdParser, EagleBrdParseError


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


if __name__ == '__main__':
    unittest.main()
