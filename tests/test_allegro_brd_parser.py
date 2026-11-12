import functools
import math
import struct
import unittest
import warnings
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from appParsers.ParseAllegroBrd import AllegroBrdParser, AllegroBrdParseError
from camlib import circle_quad_segs, gerber_circle_steps


# Synthetic Allegro 17.2 boards, built byte by byte from the layout in the ParseAllegroBrd docstring and the
# sample boards. Coordinates are in the board's mils * 1000 (unit code 1, divisor 1000): 1 mil = 1000 = 0.0254 mm.

MM = 1000 / 0.0254
FIXTURE = Path(__file__).parent / 'fixtures' / 'orcad_17.2' / 'proiectCAD.brd'


def circle_area(radius):
    # the area of a circle as the parsers draw it: the steps of the Gerber circle preference (adaptive by default)
    return Point(0, 0).buffer(radius, quad_segs=circle_quad_segs(gerber_circle_steps(None, radius))).area


@functools.lru_cache(maxsize=None)
def parsed_sample(path):
    """1 parse of a sample board for the tests that only read the result."""
    return AllegroBrdParser().parse_file(path)

SH_CIRCLE, SH_RECT, SH_OBLONG_Y, SH_DIAMOND = 0x02, 0x06, 0x0C, 0x07


def u32(value):
    return struct.pack('<I', value & 0xFFFFFFFF)


def put(buf, offset, value, fmt='<i'):
    struct.pack_into(fmt, buf, offset, value)


def swapped_double(value):
    low, high = struct.unpack('<II', struct.pack('<d', value))
    return u32(high) + u32(low)


class Builder:
    """Collects strings and blocks and writes a complete file."""

    def __init__(self, layers=('TOP', 'BOTTOM'), magic=0x00140400, role=1):
        self.magic = magic
        self.role = role
        self.strings = {}
        self.blocks = []
        self.next_key = 0x5BA20000
        self.layer_names = list(layers)
        self.layer_list_key = self.key()
        self.add(self.layer_list())

    def key(self):
        self.next_key += 1
        return self.next_key

    def string(self, text):
        sid = 0xD9A20000 + len(self.strings) * 4 + 1
        self.strings[sid] = text
        return sid

    def add(self, block):
        self.blocks.append(bytes(block))
        return block

    def layer_list(self):
        names = [self.string(name) for name in self.layer_names]
        block = bytearray(8 + 12 * len(names))
        block[0] = 0x2A
        put(block, 2, len(names), '<H')
        for i, sid in enumerate(names):
            put(block, 4 + 12 * i, sid, '<I')
        put(block, len(block) - 4, self.layer_list_key, '<I')
        return block

    def build(self):
        header = bytearray(0x1200)
        put(header, 0, self.magic, '<I')
        put(header, 4, 3, '<I')
        put(header, 8, self.role, '<I')
        header[0x180] = 1
        put(header, 0x194, len(self.strings), '<I')
        put(header, 0x26C, 1000, '<I')
        for i in range(25):
            put(header, 0x430 + 8 * i + 4, self.layer_list_key if i < 6 else 0, '<I')
        body = bytearray()
        for sid, text in self.strings.items():
            raw = text.encode('latin-1') + b'\0'
            raw += bytes((-len(raw)) % 4)
            body += u32(sid) + raw
        for block in self.blocks:
            body += block
        data = bytearray(header) + body
        put(data, 0x20, len(data), '<I')
        return bytes(data)

    # ---- block builders (all return the key of the new block)

    def padstack(self, name, comps, drill=0.0, plated=True, layers=2, start=0, slot=(0.0, 0.0), ptype=0):
        """comps: {slot index: (shape code, w, h, ox, oy, z1)}, sizes in mm."""
        block = bytearray(192 + (21 + 4 * layers) * 36)
        key = self.key()
        block[0] = 0x1C
        block[3] = start
        put(block, 4, key, '<I')
        put(block, 12, self.string(name), '<I')
        block[28] = ptype
        block[30] = 0x21 if plated else 0x01
        put(block, 44, layers, '<H')
        put(block, 64, round(drill * MM))
        put(block, 76, round(slot[0] * MM))
        put(block, 80, round(slot[1] * MM))
        for index, (shape, w, h, ox, oy, z1) in comps.items():
            e = 192 + 36 * index
            block[e] = shape
            for offset, value in ((8, w), (12, h), (16, z1), (20, ox), (24, oy)):
                put(block, e + offset, round(value * MM))
        self.add(block)
        return key

    def through_padstack(self, name, diameter, drill, mask=None, layers=2, plated=True, shape=SH_CIRCLE, slot=(0, 0)):
        comps = {14: (shape, mask or diameter + 0.4, mask or diameter + 0.4, 0, 0, 0),
                 15: (shape, mask or diameter + 0.4, mask or diameter + 0.4, 0, 0, 0)}
        for g in range(layers):
            comps[21 + 4 * g + 2] = (shape, diameter, diameter, 0, 0, 0)
        return self.padstack(name, comps, drill=drill, layers=layers, plated=plated, slot=slot)

    def smd_padstack(self, name, shape, w, h, mask=None, paste=None):
        comps = {23: (shape, w, h, 0, 0, 0), 14: (shape, mask or w, mask or h, 0, 0, 0)}
        if paste:
            comps[16] = (shape, paste, paste, 0, 0, 0)
        return self.padstack(name, comps, layers=1)

    def footprint(self, x, y, rot, pads, bottom=False):
        """pads: list of (local x mm, local y mm, padstack key, local rotation deg)."""
        key = self.key()
        placed_keys = [self.key() for _ in pads]
        for i, (px, py, stack, prot) in enumerate(pads):
            pad = bytearray(44)
            pad[0] = 0x0D
            pad_key = self.key()
            put(pad, 4, pad_key, '<I')
            put(pad, 16, round(px * MM))
            put(pad, 20, round(py * MM))
            put(pad, 24, stack, '<I')
            put(pad, 40, round(prot * 1000))
            self.add(pad)
            placed = bytearray(84)
            placed[0] = 0x32
            placed[2] = 0x0C
            put(placed, 4, placed_keys[i], '<I')
            put(placed, 24, placed_keys[i + 1] if i + 1 < len(pads) else key, '<I')
            put(placed, 28, key, '<I')
            put(placed, 36, pad_key, '<I')
            self.add(placed)
        inst = bytearray(72)
        inst[0] = 0x2D
        inst[2] = 3 if bottom else 0
        put(inst, 4, key, '<I')
        put(inst, 28, round(rot * 1000))
        put(inst, 32, round(x * MM))
        put(inst, 36, round(y * MM))
        put(inst, 48, placed_keys[0] if pads else 0, '<I')
        self.add(inst)
        return key

    def segment(self, key, nxt, owner, width, p1, p2):
        seg = bytearray(44)
        seg[0] = 0x15
        put(seg, 4, key, '<I')
        put(seg, 8, nxt, '<I')
        put(seg, 12, owner, '<I')
        put(seg, 24, round(width * MM))
        for i, value in enumerate((p1[0], p1[1], p2[0], p2[1])):
            put(seg, 28 + 4 * i, round(value * MM))
        return seg

    def arc(self, key, nxt, owner, width, start, end, centre, clockwise=False):
        seg = bytearray(84)
        seg[0] = 0x01
        seg[2] = 0x40 if clockwise else 0
        put(seg, 4, key, '<I')
        put(seg, 8, nxt, '<I')
        put(seg, 12, owner, '<I')
        put(seg, 24, round(width * MM))
        for i, value in enumerate((start[0], start[1], end[0], end[1])):
            put(seg, 28 + 4 * i, round(value * MM))
        radius = math.hypot(start[0] - centre[0], start[1] - centre[1]) * MM
        seg[44:52] = swapped_double(centre[0] * MM)
        seg[52:60] = swapped_double(centre[1] * MM)
        seg[60:68] = swapped_double(radius)
        return seg

    def chain(self, owner, parts):
        """parts: functions (key, next key) -> block; returns the key of the first one."""
        keys = [self.key() for _ in parts]
        for i, make in enumerate(parts):
            self.add(make(keys[i], keys[i + 1] if i + 1 < len(parts) else owner))
        return keys[0]

    def polyline(self, owner, points, width=0.0, closed=False):
        pts = list(points) + ([points[0]] if closed else [])
        parts = [(lambda k, n, a=pts[i], b=pts[i + 1]: self.segment(k, n, owner, width, a, b))
                 for i in range(len(pts) - 1)]
        return self.chain(owner, parts)

    def track(self, layer, points, width):
        key = self.key()
        first = self.polyline(key, points, width)
        block = bytearray(68)
        block[0] = 0x05
        block[2], block[3] = 0x06, layer
        put(block, 4, key, '<I')
        put(block, 56, first, '<I')
        self.add(block)
        return key

    def shape(self, cls, sub, points, voids=(), parent=0x5ACE831B):
        key = self.key()
        first = self.polyline(key, points, closed=True)
        kout = 0
        void_keys = []
        for void in voids:
            vkey = self.key()
            void_keys.append((vkey, void))
        for i, (vkey, void) in enumerate(void_keys):
            seg = self.polyline(vkey, void, closed=True)
            block = bytearray(36)
            block[0] = 0x34
            block[2], block[3] = cls, sub
            put(block, 4, vkey, '<I')
            put(block, 8, void_keys[i + 1][0] if i + 1 < len(void_keys) else key, '<I')
            put(block, 24, seg, '<I')
            self.add(block)
        if void_keys:
            kout = void_keys[0][0]
        block = bytearray(76)
        block[0] = 0x28
        block[2], block[3] = cls, sub
        put(block, 4, key, '<I')
        put(block, 12, parent, '<I')
        put(block, 36, kout, '<I')
        put(block, 40, first, '<I')
        self.add(block)
        return key

    def via(self, x, y, stack):
        block = bytearray(80)
        block[0] = 0x33
        block[2] = 0x12
        put(block, 4, self.key(), '<I')
        put(block, 32, round(x * MM))
        put(block, 36, round(y * MM))
        put(block, 44, stack, '<I')
        self.add(block)


def layers_of(parsed):
    return {name: unary_union(layer['solid_geometry']) for name, layer in parsed.layers.items()}


def parse(builder):
    return AllegroBrdParser().parse_bytes(builder.build())


def export_layer(layer, units='MM', circle_steps=None):
    """Exercise the production board-object export, including aperture declarations and blocks."""
    from types import SimpleNamespace
    from unittest.mock import Mock
    from appHandlers.appIO import appIO
    from appObjects.GerberObject import GerberObject
    from appParsers.ParseGerber import Gerber
    from defaults import AppDefaults

    defaults = AppDefaults()
    app = SimpleNamespace(defaults=defaults, options=dict(defaults.defaults), version='test', version_date='test',
                          app_units='MM', abort_flag=False)
    app.options.update(gerber_exp_units=units, gerber_exp_integer=4, gerber_exp_decimals=7, gerber_exp_zeros='L')
    obj = SimpleNamespace(tools=layer['tools'], solid_geometry=layer['solid_geometry'], units='MM',
                          obj_options={'pcbdoc_import': True, 'pcbdoc_primitives': True})
    obj.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(obj, *args, **kwargs)
    io = SimpleNamespace(app=app, app_units='MM', log=Mock(), inform=Mock())
    code = appIO.export_gerber(io, 'board', 'unused.gbr', local_use=obj, use_thread=False)
    assert code != 'fail'
    app.decimals, app.log, app.inform, app.proc_container = 4, Mock(), Mock(), Mock()
    app.plotcanvas = Mock()
    result = Gerber(app=app, steps_per_circle=circle_steps)
    assert result.parse_lines(code.splitlines()) != 'fail'
    if result.units != 'MM':
        result.convert_units('MM')
    return result, code


class TestAllegroHeaderErrors(unittest.TestCase):
    def assertRaisesWith(self, text, data):
        with self.assertRaises(AllegroBrdParseError) as context:
            AllegroBrdParser().parse_bytes(data)
        self.assertIn(text, str(context.exception))
        return context.exception

    def test_empty_file_and_garbage(self):
        self.assertRaisesWith('empty or too short', b'')
        self.assertRaisesWith('empty or too short', b'\x00\x04')
        self.assertRaisesWith('unknown release', b'MZ' + bytes(5000))
        builder = Builder()
        block = bytearray(8)
        block[0] = 0x0B
        builder.add(block)
        self.assertRaisesWith('Unsupported block type 0x0B', builder.build())

    def test_releases_before_17_2_are_refused_by_name(self):
        # 16.x boards go to their own reader (tests/test_allegro16_parser.py); 15.x and 17.0/17.1 are refused by
        # name, and unknown families stay unknown
        self.assertRaisesWith('Allegro 15.x boards are not supported', Builder(magic=0x00120A0C).build())
        self.assertRaisesWith('boards are not supported', Builder(magic=0x00140200).build())
        self.assertRaisesWith('unknown release', Builder(magic=0x00991100).build())
        self.assertRaisesWith('unknown release', Builder(magic=0x00160200).build())
        self.assertRaisesWith('unknown release', Builder(magic=0x00170000).build())


class TestAllegroGeometry(unittest.TestCase):
    def test_rotated_and_custom_padstacks_keep_all_openings(self):
        from types import SimpleNamespace
        builder = Builder()
        for i, shape in enumerate((SH_RECT, SH_OBLONG_Y, 0x1B, 0x1C)):
            stack = builder.smd_padstack('PAD%d' % i, shape, 1.0, 0.5, mask=1.2, paste=0.9)
            builder.footprint(10 + i * 3, 20, 37, [(0, 0, stack, 0)], bottom=True)
        data = builder.build()
        parsed = AllegroBrdParser().parse_bytes(data)
        regions = AllegroBrdParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'})) \
            .parse_bytes(data)
        for name, layer in parsed.layers.items():
            flashes = [r for t in layer['tools'].values() if t['type'] == 'AB' for r in t['geometry']]
            self.assertEqual(len(flashes), 4, name)
            self.assertEqual(sorted(round(r['follow'].x) for r in flashes), [10, 13, 16, 19])
            self.assertTrue(unary_union(layer['solid_geometry']).equals(
                unary_union(regions.layers[name]['solid_geometry'])))
            exported, code = export_layer(layer)
            self.assertEqual(code.count('D03*'), 4)
            self.assertLess(unary_union(exported.solid_geometry).symmetric_difference(
                unary_union(layer['solid_geometry'])).area, 1e-5)

    def test_track_and_round_pad_copper_mask_and_drill(self):
        builder = Builder()
        stack = builder.through_padstack('PAD60', 1.524, 0.914, mask=1.905)
        builder.footprint(10, 10, 0, [(0, 0, stack, 0), (2.54, 0, stack, 0)])
        builder.track(0, [(10, 10), (12.54, 10)], 0.3)
        parsed = parse(builder)
        self.assertEqual(list(parsed.layers), ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM'])
        layers = layers_of(parsed)
        disc = circle_area(0.762)
        self.assertAlmostEqual(layers['BOTTOM'].area, 2 * disc, delta=0.01)
        # the track between the pad centres adds its part outside the two pads
        self.assertAlmostEqual(layers['TOP'].area, 2 * disc + 0.3 * (2.54 - 1.524), delta=0.02)
        self.assertAlmostEqual(layers['SOLDERMASK_TOP'].area, 2 * circle_area(0.9525), delta=0.01)
        self.assertEqual(parsed.default_layers, list(parsed.layers))
        self.assertEqual({g: [(round(t['tooldia'], 3), len(t['drills'])) for t in tools.values()]
                          for g, tools in parsed.drill_groups.items()}, {'PTH': [(0.914, 2)]})
        points = sorted((p.x, p.y) for p in parsed.drill_groups['PTH'][1]['drills'])
        self.assertAlmostEqual(points[0][0], 10.0, places=3)
        self.assertAlmostEqual(points[1][0], 12.54, places=3)
        self.assertEqual(parsed.copper_layers, ['TOP', 'BOTTOM'])
        self.assertEqual(parsed.layers['TOP']['name'], 'ETCH/TOP')

    def test_footprint_rotation_turns_pad_positions_and_shapes(self):
        builder = Builder()
        stack = builder.smd_padstack('RECT', SH_RECT, 1.0, 0.5)
        builder.footprint(10, 20, 90, [(2, 0, stack, 0)])
        layers = layers_of(parse(builder))
        # local (2, 0) turns to (0, 2); the 1.0 x 0.5 pad turns to 0.5 x 1.0
        self.assertEqual([round(v, 3) for v in layers['TOP'].bounds], [9.75, 21.5, 10.25, 22.5])
        # an oblong pad has round ends on the short sides
        builder = Builder()
        stack = builder.smd_padstack('OBL', SH_OBLONG_Y, 1.0, 2.0)
        builder.footprint(0, 0, 0, [(0, 0, stack, 0)])
        area = layers_of(parse(builder))['TOP'].area
        self.assertAlmostEqual(area, math.pi * 0.25 + 1.0 * 1.0, delta=0.01)

    def test_bottom_footprint_is_mirrored_then_rotated_and_lands_on_bottom_layers(self):
        builder = Builder()
        stack = builder.smd_padstack('RECT', SH_RECT, 1.0, 0.5, mask=1.2, paste=0.9)
        builder.footprint(10, 20, 90, [(2, 0, stack, 0)], bottom=True)
        parsed = parse(builder)
        layers = layers_of(parsed)
        self.assertNotIn('TOP', layers)
        # local (2, 0) mirrors to (-2, 0), then turns to (0, -2)
        self.assertEqual([round(v, 3) for v in layers['BOTTOM'].bounds], [9.75, 17.5, 10.25, 18.5])
        self.assertIn('SOLDERMASK_BOTTOM', layers)
        self.assertNotIn('SOLDERMASK_TOP', layers)
        self.assertIn('PASTEMASK_BOTTOM', layers)
        self.assertNotIn('PASTEMASK_TOP', layers)

    def test_arc_track_follows_the_circle(self):
        builder = Builder()
        key = builder.key()
        first = builder.chain(key, [lambda k, n: builder.arc(k, n, key, 0.2, (5, 0), (0, 5), (0, 0))])
        block = bytearray(68)
        block[0] = 0x05
        block[2], block[3] = 0x06, 0
        put(block, 4, key, '<I')
        put(block, 56, first, '<I')
        builder.add(block)
        layer = layers_of(parse(builder))['TOP']
        # quarter circle of radius 5 and width 0.2
        self.assertAlmostEqual(layer.area, 0.2 * 5 * math.pi / 2 + math.pi * 0.01, delta=0.02)
        self.assertEqual([round(v, 1) for v in layer.bounds], [-0.1, -0.1, 5.1, 5.1])

    def test_copper_shape_fill_with_void(self):
        builder = Builder()
        void = [(5 + 1 * math.cos(a * math.pi / 8), 5 + 1 * math.sin(a * math.pi / 8)) for a in range(16)]
        builder.shape(0x06, 1, [(0, 0), (10, 0), (10, 10), (0, 10)], voids=[void])
        layers = layers_of(parse(builder))
        self.assertNotIn('TOP', layers)
        expected = 100 - Polygon(void).area
        self.assertAlmostEqual(layers['BOTTOM'].area, expected, delta=0.01)

    def test_zone_without_a_stored_fill_raises(self):
        builder = Builder()
        builder.shape(0x15, 1, [(0, 0), (10, 0), (10, 10), (0, 10)])
        with self.assertRaisesRegex(AllegroBrdParseError, 'never filled'):
            parse(builder)

    def test_footprint_library_copies_are_not_placed(self):
        builder = Builder()
        definition = bytearray(76)
        definition[0] = 0x2B
        key = builder.key()
        put(definition, 4, key, '<I')
        builder.add(definition)
        builder.shape(0x06, 0, [(0, 0), (10, 0), (10, 10), (0, 10)], parent=key)
        self.assertEqual(parse(builder).layers, {})

    def test_endless_segment_chain_raises(self):
        builder = Builder()
        key = builder.key()
        builder.add(builder.segment(key + 1, key + 1, key, 0.2, (0, 0), (1, 0)))
        block = bytearray(68)
        block[0], block[2], block[3] = 0x05, 0x06, 0
        put(block, 4, key, '<I')
        put(block, 56, key + 1, '<I')
        builder.add(block)
        with self.assertRaisesRegex(AllegroBrdParseError, 'Endless segment chain'):
            parse(builder)

    def test_via_span_decides_the_drill_group_and_mask_openings(self):
        builder = Builder(layers=('TOP', 'IN1', 'IN2', 'BOTTOM'))
        through = builder.through_padstack('THRU', 0.6, 0.3, mask=0.7, layers=4)
        comps = {14: (SH_CIRCLE, 0.7, 0.7, 0, 0, 0), 15: (SH_CIRCLE, 0.7, 0.7, 0, 0, 0)}
        for g in range(2):
            comps[21 + 4 * g + 2] = (SH_CIRCLE, 0.6, 0.6, 0, 0, 0)
        blind = builder.padstack('BLIND', comps, drill=0.2, layers=2, start=0)
        buried = builder.padstack('BURIED', {21 + 4 * 0 + 2: (SH_CIRCLE, 0.6, 0.6, 0, 0, 0),
                                             21 + 4 * 1 + 2: (SH_CIRCLE, 0.6, 0.6, 0, 0, 0),
                                             14: (SH_CIRCLE, 0.7, 0.7, 0, 0, 0)},
                                  drill=0.25, layers=2, start=1)
        builder.via(1, 1, through)
        builder.via(2, 2, blind)
        builder.via(3, 3, buried)
        builder.track(1, [(0, 0), (0.2, 0)], 0.1)
        builder.track(2, [(0, 0), (0.2, 0)], 0.1)
        # the buried via opens no mask although its padstack has a top mask entry
        parsed = parse(builder)
        self.assertEqual(sorted(parsed.drill_groups), ['PTH', 'PTH_IN1-IN2', 'PTH_TOP-IN1'])
        layers = layers_of(parsed)
        self.assertEqual(len(parsed.layers['SOLDERMASK_TOP']['solid_geometry']), 2)
        self.assertEqual(len(parsed.layers['SOLDERMASK_BOTTOM']['solid_geometry']), 1)
        self.assertAlmostEqual(layers['IN2'].area, 2 * math.pi * 0.09, delta=0.03)    # through + buried
        self.assertAlmostEqual(layers['BOTTOM'].area, math.pi * 0.09, delta=0.01)     # through only

    def test_unplated_hole_goes_to_npth_and_keeps_its_pad(self):
        builder = Builder()
        mounting = builder.through_padstack('MH', 3.0, 3.2, plated=False)
        builder.footprint(5, 5, 0, [(0, 0, mounting, 0)])
        parsed = parse(builder)
        self.assertEqual(list(parsed.drill_groups), ['NPTH'])
        self.assertAlmostEqual(parsed.drill_groups['NPTH'][1]['tooldia'], 3.2, places=3)
        self.assertAlmostEqual(layers_of(parsed)['TOP'].area, math.pi * 1.5 ** 2, delta=0.02)

    def test_slotted_hole_becomes_a_slot_with_a_warning(self):
        builder = Builder()
        stack = builder.through_padstack('SLOT', 2.0, 1.0, slot=(2.0, 1.0))
        builder.footprint(5, 5, 90, [(0, 0, stack, 0)])
        parsed = parse(builder)
        tool = parsed.drill_groups['PTH'][1]
        self.assertEqual(tool['drills'], [])
        self.assertAlmostEqual(tool['tooldia'], 1.0, places=3)
        (first, last), = tool['slots']
        self.assertAlmostEqual(math.hypot(first.x - last.x, first.y - last.y), 1.0, places=3)
        self.assertAlmostEqual(first.x, 5.0, places=3)
        self.assertTrue(any('Slotted holes' in w for w in parsed.warnings))

    def test_outline_rectangle_and_board_outline(self):
        builder = Builder()
        key = builder.key()
        block = bytearray(56)
        block[0], block[2], block[3] = 0x24, 0x01, 0xEA
        put(block, 4, key, '<I')
        put(block, 12, 0x5ACE8323, '<I')
        for i, value in enumerate((0, 0, 50, 30)):
            put(block, 24 + 4 * i, round(value * MM))
        builder.add(block)
        parsed = parse(builder)
        self.assertEqual([round(v, 3) for v in parsed.layers['OUTLINE']['solid_geometry'][0].bounds],
                         [-0.005, -0.005, 50.005, 30.005])
        self.assertEqual([round(v, 3) for v in parsed.board_outline.bounds], [0.0, 0.0, 50.0, 30.0])
        self.assertEqual(parsed.default_layers, ['OUTLINE'])
        # an open outline gives an empty board outline and a warning
        builder = Builder()
        gkey = builder.key()
        first = builder.polyline(gkey, [(0, 0), (10, 0), (10, 10)], 0.0)
        block = bytearray(36)
        block[0], block[2], block[3] = 0x14, 0x04, 0xFD
        put(block, 4, gkey, '<I')
        put(block, 12, 0x5ACE8323, '<I')
        put(block, 24, first, '<I')
        builder.add(block)
        parsed = parse(builder)
        self.assertTrue(parsed.board_outline.is_empty)
        self.assertTrue(any('not a closed shape' in w for w in parsed.warnings))

UNTESTED = 'is not tested and this board could not be read safely'
FIXTURE_B = FIXTURE.parent / '2nd project' / 'TI28027.brd'


class TestAllegroUntestedReleases(unittest.TestCase):
    """Releases that were never compared with a sample board are tried under a strict gate."""

    @staticmethod
    def _outline(builder, width, height):
        block = bytearray(56)
        block[0], block[2], block[3] = 0x24, 0x01, 0xEA
        put(block, 4, builder.key(), '<I')
        put(block, 12, 0x5ACE8323, '<I')
        for i, value in enumerate((0, 0, width, height)):
            put(block, 24 + 4 * i, round(value * MM))
        builder.add(block)

    def _board(self, magic, pad_at=(10, 10), outline=True, pad=1.0):
        builder = Builder(magic=magic)
        stack = builder.through_padstack('PAD', pad, 0.5)
        builder.footprint(pad_at[0], pad_at[1], 0, [(0, 0, stack, 0)])
        builder.track(0, [(5, 5), (15, 5)], 0.3)
        if outline:
            self._outline(builder, 50, 30)
        return builder.build()

    def test_patched_magic_on_the_sample_boards(self):
        # Fixture A has the 17.2 layout and fixture B the checked 18.1 layout. A magic that selects the same layout
        # reads them (with the warning first); any other layout reads the data with wrong sizes or header offsets
        # and must end in the clear error of an untested release.
        cases = ((FIXTURE, 0x00140500, '17.x (format 0x00140500)', True), (FIXTURE, 0x00140900, '17.4', False),
                 (FIXTURE, 0x00150000, '18.0', False),
                 (FIXTURE_B, 0x00150300, '18.x (format 0x00150300)', True), (FIXTURE_B, 0x00160100, '19.0', False))
        for path, magic, name, reads in cases:
            with self.subTest(file=path.name, release=name):
                blob = bytearray(path.read_bytes())
                put(blob, 0, magic, '<I')
                if reads:
                    parsed = AllegroBrdParser().parse_bytes(bytes(blob))
                    self.assertEqual(parsed.warnings[0], 'Allegro %s is not tested: check the result against '
                                                         'Gerbers made by Allegro.' % name)
                    if path != FIXTURE:
                        # the 1.2 MB board B takes 4 s to parse: no second parse for a reference; the e2e test
                        # compares the unpatched B with the films
                        self.assertTrue(all(layer['solid_geometry'] for layer in parsed.layers.values()))
                        self.assertIn('INNER 1', parsed.layers)
                        continue
                    reference = parsed_sample(path)
                    self.assertEqual(parsed.warnings[1:], reference.warnings)
                    self.assertEqual(list(parsed.layers), list(reference.layers))
                    for layer in parsed.layers:
                        self.assertEqual(len(parsed.layers[layer]['solid_geometry']),
                                         len(reference.layers[layer]['solid_geometry']))
                else:
                    with self.assertRaises(AllegroBrdParseError) as context:
                        AllegroBrdParser().parse_bytes(bytes(blob))
                    self.assertTrue(str(context.exception).startswith('Allegro %s %s: ' % (name, UNTESTED)),
                                    str(context.exception))

    def test_gate_catches_a_pad_outside_the_outline(self):
        data = self._board(0x00140500, pad_at=(80, 10))
        with self.assertRaisesRegex(AllegroBrdParseError, UNTESTED + ': A .* item at .* lies outside the board'):
            AllegroBrdParser().parse_bytes(data)
        # the same data in a checked release is not gated
        self.assertIn('TOP', AllegroBrdParser().parse_bytes(self._board(0x00140400, pad_at=(80, 10))).layers)

class TestAllegroRobustness(unittest.TestCase):
    @staticmethod
    def _sample():
        builder = Builder(layers=('TOP', 'IN1', 'BOTTOM'))
        stack = builder.through_padstack('PAD', 1.5, 0.9, layers=3)
        smd = builder.smd_padstack('SMD', SH_RECT, 1.0, 0.6, paste=0.8)
        builder.footprint(10, 10, 90, [(0, 0, stack, 0), (2.54, 0, stack, 0)])
        builder.footprint(30, 10, 180, [(0, 0, smd, 0)], bottom=True)
        builder.track(0, [(10, 10), (20, 10), (20, 20)], 0.25)
        builder.track(1, [(11, 11), (21, 11)], 0.25)
        void = [(25 + math.cos(a * math.pi / 6), 25 + math.sin(a * math.pi / 6)) for a in range(12)]
        builder.shape(0x06, 2, [(22, 22), (40, 22), (40, 40), (22, 40)], voids=[void])
        builder.shape(0x15, 2, [(22, 22), (40, 22), (40, 40), (22, 40)])
        builder.via(5, 5, builder.through_padstack('VIA', 0.6, 0.3, layers=3))
        return builder.build()

    def test_every_truncation_is_a_clean_error(self):
        data = self._sample()
        AllegroBrdParser().parse_bytes(data)
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            for cut in list(range(0, len(data), 7)) + [len(data) - 1, len(data) - 4]:
                for fix_size in (False, True):
                    blob = bytearray(data[:cut])
                    if fix_size and len(blob) >= 0x24:
                        put(blob, 0x20, len(blob), '<I')
                    with self.subTest(cut=cut, fix_size=fix_size):
                        try:
                            AllegroBrdParser().parse_bytes(bytes(blob))
                        except AllegroBrdParseError as error:
                            self.assertFalse(str(error).startswith('Cannot read Allegro board:'), str(error))
                        else:
                            # only a cut at a block boundary with a corrected size field is a valid shorter file
                            self.assertTrue(fix_size, 'a truncated file with the old size field was accepted')

class TestAllegroFixtureBoards(unittest.TestCase):
    def test_sample_board_summary(self):
        parsed = parsed_sample(FIXTURE)
        self.assertEqual(parsed.version, '17.2')
        self.assertEqual(parsed.copper_layers, ['TOP', 'BOTTOM'])
        self.assertEqual(list(parsed.layers), ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM',
                                               'SILKSCREEN_TOP', 'ASSEMBLY_TOP', 'OUTLINE'])
        self.assertEqual(parsed.default_layers, ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM',
                                                 'SILKSCREEN_TOP', 'OUTLINE'])
        self.assertEqual(sum(len(t['drills']) for t in parsed.drill_groups['PTH'].values()), 26)
        self.assertEqual(sum(len(t['drills']) for t in parsed.drill_groups['NPTH'].values()), 4)
        self.assertEqual([round(v, 2) for v in parsed.board_outline.bounds], [0.0, 0.0, 70.0, 55.0])
        from types import SimpleNamespace
        from shapely.geometry import LineString
        regions = AllegroBrdParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'})) \
            .parse_file(FIXTURE)
        expected = {'TOP': (30, 33), 'BOTTOM': (30, 27), 'SOLDERMASK_TOP': (26, 0),
                    'SOLDERMASK_BOTTOM': (26, 0), 'SILKSCREEN_TOP': (0, 243),
                    'ASSEMBLY_TOP': (0, 86), 'OUTLINE': (0, 1)}
        for name, layer in parsed.layers.items():
            records = [r for t in layer['tools'].values() for r in t['geometry']]
            self.assertEqual((sum(isinstance(r.get('follow'), Point) for r in records),
                              sum(isinstance(r.get('follow'), LineString) for r in records)), expected[name], name)
            original = unary_union(regions.layers[name]['solid_geometry'])
            self.assertTrue(unary_union(layer['solid_geometry']).equals(original))
            exported, code = export_layer(layer)
            self.assertEqual(code.count('D03*'), expected[name][0], name)
            self.assertLess(unary_union(exported.solid_geometry).symmetric_difference(original).area, 0.01, name)


if __name__ == '__main__':
    unittest.main()
