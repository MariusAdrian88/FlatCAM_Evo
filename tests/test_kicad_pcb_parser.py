import unittest

from shapely import Point
from shapely.ops import unary_union

from appParsers.ParseKiCadPcb import KiCadPcbParser, KiCadPcbParseError
from camlib import circle_quad_segs, gerber_circle_steps


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

if __name__ == '__main__':
    unittest.main()
