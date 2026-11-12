import math
import os
import re
import struct
import unittest
from pathlib import Path

import pytest
from shapely.geometry import Point
from shapely.ops import unary_union

from appParsers.ParseAllegroBrd import AllegroBrdParser, AllegroBrdParseError
from appParsers.ParseAllegroBrd16 import AllegroBrd16Parser, is_16x, parse_into


# Synthetic Allegro 16.x boards, built byte by byte from the 16.x layout (see the ParseAllegroBrd16 docstring).
# Coordinates are in mils * 1000 (unit code 1, divisor 1000): 1 mm = 1000 / 0.0254 board units.

MM = 1000 / 0.0254
MAGIC_164, MAGIC_166 = 0x00130C03, 0x00131504


def u32(value):
    return struct.pack('<I', value & 0xFFFFFFFF)


def put(buf, offset, value, fmt='<i'):
    struct.pack_into(fmt, buf, offset, value)


def swapped_double(value):
    low, high = struct.unpack('<II', struct.pack('<d', value))
    return u32(high) + u32(low)


class Builder16:
    """Strings and blocks of a 16.4 or 16.6 board, written as a complete file."""

    def __init__(self, magic=MAGIC_166, layers=('TOP', 'BOTTOM')):
        self.magic = magic
        self.layer_names = list(layers)
        self.inline_names = magic == MAGIC_164          # 16.4 keeps the layer names in the layer list
        self.strings = {}
        self.blocks = []
        self.next_key = 0x5BA20000
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
        entry = 36 if self.inline_names else 12
        block = bytearray(8 + entry * len(self.layer_names))
        block[0] = 0x2A
        put(block, 2, len(self.layer_names), '<H')
        for i, name in enumerate(self.layer_names):
            if self.inline_names:
                block[4 + entry * i:4 + entry * i + len(name)] = name.encode('latin-1')
            else:
                put(block, 4 + entry * i, self.string(name), '<I')
        put(block, len(block) - 4, self.layer_list_key, '<I')
        return block

    def build(self):
        header = bytearray(0x1200)
        put(header, 0, self.magic, '<I')
        put(header, 4, 3, '<I')
        put(header, 8, 1, '<I')
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

    # ---- block builders

    def padstack(self, name, diameter, drill, layers=2, plated=True):
        """A round through padstack of `layers` copper layers: pads, no mask."""
        newer = self.magic == MAGIC_166
        head, fixed = (88, 11) if newer else (84, 10)
        comps = fixed + 3 * layers
        block = bytearray(head + comps * 28 - 4)
        key = self.key()
        block[0] = 0x1C
        put(block, 4, key, '<I')
        put(block, 12, self.string(name), '<I')
        put(block, 16, round(drill * MM))
        block[41] = 0x01 if plated else 0x02
        put(block, 50, layers, '<H')
        for g in range(layers):
            e = head + 28 * (fixed + 3 * g + 2)
            block[e] = 0x02
            put(block, e + 4, round(diameter * MM))
            put(block, e + 8, round(diameter * MM))
        self.add(block)
        return key

    def net(self):
        """A net assignment block (0x04): word 3 of a via, track or shape of the net refers to it."""
        block = bytearray(20)
        block[0] = 0x04
        key = self.key()
        put(block, 4, key, '<I')
        self.add(block)
        return key

    def footprint_definition(self):
        block = bytearray(72 if self.magic == MAGIC_166 else 68)
        block[0] = 0x2B
        key = self.key()
        put(block, 4, key, '<I')
        self.add(block)
        return key

    def film(self, layers, keeps_unconnected_pads):
        """A film (0x38) with its layer list (0x39) and one node (0x3A) per copper layer it plots."""
        film_key, list_key = self.key(), self.key()
        nodes = [self.key() for _ in layers]
        block = bytearray(52 if self.magic == MAGIC_166 else 64)
        block[0] = 0x38
        put(block, 4, film_key, '<I')
        put(block, 12, list_key, '<I')
        self.add(block)
        block = bytearray(60)
        block[0] = 0x39
        put(block, 4, list_key, '<I')
        put(block, 8, film_key, '<I')
        put(block, 12, nodes[0], '<I')
        put(block, 44, 0x10000 if keeps_unconnected_pads else 0, '<I')
        self.add(block)
        for i, layer in enumerate(layers):
            block = bytearray(16)
            block[0], block[2], block[3] = 0x3A, 0x06, layer
            put(block, 4, nodes[i], '<I')
            put(block, 8, nodes[i + 1] if i + 1 < len(nodes) else list_key, '<I')
            self.add(block)

    def via(self, x, y, stack, flag=0x10, net=0):
        block = bytearray(72)
        block[0] = 0x33
        block[2] = 0x12
        block[3] = flag
        put(block, 4, self.key(), '<I')
        put(block, 12, net, '<I')
        put(block, 24, round(x * MM))
        put(block, 28, round(y * MM))
        put(block, 36, stack, '<I')
        self.add(block)

    def track(self, layer, segments, width, owner=0):
        """segments: ('line', (x1, y1), (x2, y2)) or ('arc', start, end, centre, clockwise), in mm; owner: its net
        assignment or footprint definition."""
        keys = [self.key() for _ in segments]
        track_key = self.key()
        block = bytearray(60)
        block[0], block[2], block[3] = 0x05, 0x06, layer
        put(block, 4, track_key, '<I')
        put(block, 12, owner, '<I')
        put(block, 48, keys[0], '<I')
        self.add(block)
        for i, segment in enumerate(segments):
            following = keys[i + 1] if i + 1 < len(keys) else track_key
            if segment[0] == 'line':
                seg = bytearray(40)
                seg[0] = 0x15
                coords = (*segment[1], *segment[2])
                start = 24
            else:
                seg = bytearray(80)
                seg[0] = 0x01
                seg[3] = 0x40 if segment[4] else 0
                coords = (*segment[1], *segment[2])
                start = 24
                seg[40:48] = swapped_double(segment[3][0] * MM)
                seg[48:56] = swapped_double(segment[3][1] * MM)
                seg[56:64] = swapped_double(math.hypot(segment[1][0] - segment[3][0], segment[1][1] - segment[3][1]) * MM)
            put(seg, 4, keys[i], '<I')
            put(seg, 8, following, '<I')
            put(seg, 12, track_key, '<I')
            put(seg, 20, round(width * MM))
            for k, value in enumerate(coords):
                put(seg, start + 4 * k, round(value * MM))
            self.add(seg)


def layers_of(parsed):
    return {name: unary_union(layer['solid_geometry']) for name, layer in parsed.layers.items()}


class TestAllegro16Synthetic(unittest.TestCase):
    def test_shape_symbol_pads_with_outline_only_sizes_keep_flashes(self):
        from test_allegro_brd_parser import export_layer
        builder = Builder16()
        shape_key = builder.key()
        vertices = [(-0.6, -0.4), (0.6, -0.4), (0.2, 0.5), (-0.6, -0.4)]
        keys = [builder.key() for _ in range(3)]
        for i in range(3):
            segment = bytearray(40)
            segment[0] = 0x15
            put(segment, 4, keys[i], '<I')
            put(segment, 8, keys[i + 1] if i < 2 else shape_key, '<I')
            put(segment, 12, shape_key, '<I')
            for j, value in enumerate((*vertices[i], *vertices[i + 1])):
                put(segment, 24 + 4 * j, round(value * MM))
            builder.add(segment)
        shape = bytearray(68)
        shape[0] = 0x28
        put(shape, 4, shape_key, '<I')
        put(shape, 12, builder.footprint_definition(), '<I')
        put(shape, 32, keys[0], '<I')
        builder.add(shape)
        stack = builder.padstack('SHAPE', 0.6, 0.3)
        block = bytearray(builder.blocks[-1])
        for slot in (1, 13, 16):
            at = 88 + 28 * slot
            block[at] = 0x16
            put(block, at + 4, 0)
            put(block, at + 8, 0)
            put(block, at + 12, round(0.2 * MM))
            put(block, at + 16, round(-0.1 * MM))
            put(block, at + 20, shape_key, '<I')
        builder.blocks[-1] = bytes(block)
        builder.via(20, 30, stack)
        parsed = AllegroBrd16Parser().parse_bytes(builder.build())
        self.assertEqual(set(parsed.layers), {'TOP', 'BOTTOM', 'SOLDERMASK_TOP'})
        for name, layer in parsed.layers.items():
            records = [r for t in layer['tools'].values() if t['type'] == 'AB' for r in t['geometry']]
            self.assertEqual(len(records), 1, name)
            self.assertLess(records[0]['follow'].distance(Point(20.2, 29.9)), 0.00003)
            exported, code = export_layer(layer)
            self.assertEqual(code.count('D03*'), 1)
            self.assertLess(unary_union(exported.solid_geometry).symmetric_difference(
                records[0]['solid']).area, 1e-5)

    def test_rotated_filled_rectangles_keep_source_flashes(self):
        from types import SimpleNamespace
        from test_allegro_brd_parser import export_layer
        for magic in (MAGIC_164, MAGIC_166):
            builder = Builder16(magic)
            for cls, sub in ((0x06, 0), (0x09, 0xF4), (0x09, 0xED)):
                block = bytearray(52)
                block[0], block[2], block[3] = 0x24, cls, sub
                put(block, 4, builder.key(), '<I')
                for i, value in enumerate((10, 20, 12, 21)):
                    put(block, 20 + i * 4, round(value * MM))
                put(block, 48, 37000)
                builder.add(block)
            data = builder.build()
            parsed = AllegroBrd16Parser().parse_bytes(data)
            regions = AllegroBrd16Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'})) \
                .parse_bytes(data)
            self.assertEqual(set(parsed.layers), {'TOP', 'SOLDERMASK_TOP', 'PASTEMASK_TOP'})
            for name, layer in parsed.layers.items():
                records = [r for t in layer['tools'].values() if t['type'] == 'AB' for r in t['geometry']]
                self.assertEqual(len(records), 1, name)
                self.assertTrue(records[0]['follow'].equals(records[0]['solid'].centroid))
                original = unary_union(regions.layers[name]['solid_geometry'])
                self.assertTrue(unary_union(layer['solid_geometry']).equals(original))
                exported, code = export_layer(layer)
                self.assertEqual(code.count('D03*'), 1)
                self.assertLess(unary_union(exported.solid_geometry).symmetric_difference(original).area, 1e-5)

    def test_16_4_and_16_6_boards_are_read(self):
        for magic, name in ((MAGIC_164, '16.4'), (MAGIC_166, '16.6')):
            with self.subTest(release=name):
                builder = Builder16(magic, layers=('TOP', 'BOTTOM'))
                builder.track(0, [('line', (0, 0), (10, 0))], 0.3)
                parsed = AllegroBrd16Parser().parse_bytes(builder.build())
                self.assertEqual(parsed.version, name)
                self.assertEqual(parsed.copper_layers, ['TOP', 'BOTTOM'])
                top = layers_of(parsed)['TOP']
                self.assertAlmostEqual(top.bounds[2], 10.15, places=2)
                self.assertTrue(top.contains(Point(5, 0)))

    def test_a_clockwise_arc_takes_the_short_way(self):
        # the direction bit of a 16.x arc is bit 6 of the 4th header byte
        for clockwise, inside in ((True, (7.07, 7.07)), (False, (-7.07, -7.07))):
            with self.subTest(clockwise=clockwise):
                builder = Builder16()
                builder.track(0, [('arc', (0, 10), (10, 0), (0, 0), clockwise)], 0.3)
                top = layers_of(AllegroBrd16Parser().parse_bytes(builder.build()))['TOP']
                self.assertTrue(top.contains(Point(inside)))
                self.assertFalse(top.contains(Point(-inside[0], -inside[1])))

    def test_vias_and_their_holes_but_not_the_footprint_definition_copper(self):
        builder = Builder16()
        stack = builder.padstack('VIA16', 0.6, 0.3)
        builder.via(5, 5, stack, 0x10)
        builder.via(8, 5, stack, 0x90)
        builder.via(20, 5, stack, 0x80)
        builder.track(0, [('line', (0, 10), (10, 10))], 0.3, owner=builder.footprint_definition())
        parsed = AllegroBrd16Parser().parse_bytes(builder.build())
        holes = [(round(point.x, 3), round(point.y, 3)) for tool in parsed.drill_groups['PTH'].values()
                 for point in tool['drills']]
        self.assertEqual(sorted(holes), [(5.0, 5.0), (8.0, 5.0)])
        self.assertAlmostEqual(list(parsed.drill_groups['PTH'].values())[0]['tooldia'], 0.3, places=3)
        top = layers_of(parsed)['TOP']
        self.assertTrue(top.contains(Point(5.2, 5)))
        self.assertFalse(top.intersects(Point(20, 5)) or top.intersects(Point(5, 10)))

    def test_inner_pads_without_copper_of_their_net_follow_the_film_option(self):
        for keeps in (True, False):
            with self.subTest(film_keeps_unconnected_pads=keeps):
                builder = Builder16(layers=('TOP', 'IN1', 'IN2', 'BOTTOM'))
                stack = builder.padstack('VIA4', 0.6, 0.3, layers=4)
                net = builder.net()
                builder.via(5, 5, stack, net=net)
                builder.via(20, 5, stack, net=net)
                builder.track(1, [('line', (5, 5), (10, 5))], 0.2, owner=net)
                builder.film([1], keeps)
                builder.film([0], True)
                layers = layers_of(AllegroBrd16Parser().parse_bytes(builder.build()))
                self.assertTrue(layers['IN1'].contains(Point(5, 5.25)))      # the pad its track reaches
                self.assertEqual(layers['IN1'].contains(Point(20, 5.25)), keeps)
                self.assertTrue(layers['TOP'].contains(Point(20, 5.25)))     # outer pads stay

    def test_only_connected_inner_pads_keep_flash_records(self):
        from types import SimpleNamespace
        builder = Builder16(layers=('TOP', 'IN1', 'IN2', 'BOTTOM'))
        stack = builder.padstack('VIA4', 0.6, 0.3, layers=4)
        net = builder.net()
        builder.via(5, 5, stack, net=net)
        builder.via(20, 5, stack, net=net)
        builder.track(1, [('line', (5, 5), (10, 5))], 0.2, owner=net)
        builder.film([1], False)
        builder.film([0], True)
        parsed = AllegroBrd16Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'})) \
            .parse_bytes(builder.build())
        records = [r for t in parsed.layers['IN1']['tools'].values() for r in t['geometry']]
        flashes = [r['follow'] for r in records if isinstance(r.get('follow'), Point)]
        self.assertEqual(len(flashes), 1)
        self.assertLess(flashes[0].distance(Point(5, 5)), 0.00002)  # the builder quantizes board coordinates

    def test_16_x_data_is_routed_for_the_base_reader(self):
        builder = Builder16()
        builder.track(0, [('line', (0, 0), (10, 0))], 0.3)
        data = builder.build()
        self.assertTrue(is_16x(data))
        self.assertFalse(is_16x(struct.pack('<I', 0x00140400) + data[4:]))
        owner = AllegroBrdParser()
        self.assertIs(parse_into(owner, data), owner)
        self.assertEqual(owner.version, '16.6')
        self.assertTrue(unary_union(owner.layers['TOP']['solid_geometry']).contains(Point(5, 0)))

    def test_other_releases_and_damaged_files_are_refused_by_name(self):
        good = Builder16().build()
        with self.assertRaises(AllegroBrdParseError) as context:
            AllegroBrd16Parser().parse_bytes(struct.pack('<I', 0x00140400) + good[4:])
        self.assertIn('Not an Allegro 16.x board', str(context.exception))
        with self.assertRaises(AllegroBrdParseError) as context:
            AllegroBrd16Parser().parse_bytes(struct.pack('<I', 0x00120A0C) + good[4:])
        self.assertIn('Allegro 15.x boards are not supported', str(context.exception))
        with self.assertRaises(AllegroBrdParseError) as context:
            AllegroBrd16Parser().parse_bytes(good[:-8])
        self.assertIn('truncated or damaged', str(context.exception))
        with self.assertRaises(AllegroBrdParseError) as context:
            AllegroBrd16Parser().parse_bytes(good[:0x400])
        self.assertIn('header is incomplete', str(context.exception))


# ---- boards of the sample corpus (ALLEGRO16_EXAMPLES, see the README.md there): a few layers of each board against
# the films that Allegro plotted from it (inside the board outline), the holes against the NC drill files and the
# slots against the routing file.

CORPUS = Path(os.environ.get('ALLEGRO16_EXAMPLES', ''))

# folder -> board file, films ({copper layer index or layer name: film}), NC drill files with the digits after the
# point of their coordinates and their unit, tolerance of the hole position in mm, routing file (None: none made)
BOARDS = {
    # 16.6; films that plot every pad
    'cutiepi': dict(brd='CutiePi_V2_3.brd', films={1: 'gnd02.art', 3: 'l04.art', 'SOLDERMASK_TOP': 'solder_top.art'},
                    drills=[('cutiepi_v2_3-1-6.drl', 5, 'IN')], position=0.02, rou='cutiepi_v2_3.rou'),
    # 16.4; every internal film leaves out the unconnected pads
    'xm': dict(brd='BeagleBoard-xM.brd', position=0.02, rou=None,
               films={0: 'xM_RevC1_lyr1.art', 2: 'xM_RevC1_lyr3.art', 4: 'xM_RevC1_lyr5.art',
                      'PASTEMASK_TOP': 'xM_RevC1_spc.art'},
               drills=[('BeagleBoard_xM_RevC1_NCDRILL_1-6.tap', 5, 'IN'), ('BeagleBoard_xM_RevC1_NCDRILL_1-2.tap', 5, 'IN'),
                       ('BeagleBoard_xM_RevC1_NCDRILL_1-3.tap', 5, 'IN')]),
    # 16.4; the plane films leave out the unconnected pads, the signal films keep them; tented vias
    'bbb': dict(brd='BeagleBone_Black_RevB4_NOLOGO.brd', position=0.02, rou='BeagleBone_Black_RevB4_NCROUTE.rou',
                films={1: 'BB_Black_RB4_lyr2.art', 3: 'BB_Black_RB4_lyr4.art', 'SOLDERMASK_TOP': 'BB_Black_RB4_smc.art'},
                drills=[('BeagleBone_Black_RevB4_NCDRILL.drl', 5, 'IN')]),
    'bbc1': dict(brd='BeagleBone_revC1_ALLEGRO.brd', position=0.02, rou='beaglebone_revC1_NCROUTE.rou',
                 films={4: 'beaglebone_lyr5.art', 'SOLDERMASK_BOTTOM': 'beaglebone_sms.art'},
                 drills=[('beaglebone_revC1_NCDRILL.drl', 5, 'IN')]),
    # 16.6 with dynamic unused pad suppression
    'x15': dict(brd='BB_X15_revB1_public.brd', position=0.02, rou=None,
                films={1: 'X15_RevB1_L2.art', 9: 'X15_RevB1_L10.art', 'SOLDERMASK_TOP': 'X15_RevB1_smc.art'},
                drills=[('X15_RevB1-1-12.drl', 5, 'IN')]),
    # Parallella 16.4: a fabrication drawing frame on the outline subclass; drill files without tool codes
    'pa0': dict(brd='parallella_gen0.brd', position=0.02, rou='PARALLELLA-I_revA.rou', drills=[('ncdrill1-1-12.tap', 5, 'IN')],
                films={0: 'parallella-I_revA_top.art', 7: 'parallella-I_revA_LAYER8_SIGNAL.art'}),
    'pa1': dict(brd='parallella_gen1.brd', position=0.02, rou='PARALLELLA-I_revB.rou',
                drills=[('PARALLELLA-I_revB_ncdrill1-1-12.tap', 5, 'IN')],
                films={4: 'parallella-I_revB_LAYER5_SIGNAL.art', 'SOLDERMASK_BOTTOM': 'parallella-I_revB_smask_b.art'}),
    'pa11': dict(brd='parallella_layout.brd', position=0.02, rou='PARALLELLA-I_revc.rou',
                 drills=[('ncdrill1-1-12.tap', 5, 'IN')],
                 films={2: 'parallella-I_revc_LAYER3_SIGNAL.art', 'SOLDERMASK_TOP': 'parallella-I_revc_smask_t.art'}),
    # 16.6, unit code 3 (millimetres); the drill file has 3 digits of inches and repeat codes whose rounded steps add
    # up (up to 0.25 mm)
    'aafm': dict(brd='HB1A-AAFM.brd', position=0.3, rou=None, drills=[('HB1A-AAFM-1-18.drl', 3, 'IN')],
                 films={6: 'HB1A-AAFM-L07.art', 16: 'HB1A-AAFM-L17.art', 'SOLDERMASK_BOTTOM': 'HB1A-AAFM-SMB.art'}),
    'buddy': dict(brd='buddy_lay_rev1.brd', position=0.02, rou=None, drills=[('BUDDY_Board_Rev1-1-14.drl', 5, 'MM')],
                  films={1: 'BuddyBoard_Rev1-L02.art', 9: 'BuddyBoard_Rev1-L10.art'}),
}
# symmetric difference / film area inside the board outline less 0.3 mm (the films also plot the outline as a line)
LIMIT = 0.0005


def read_drill_hits(path, decimals, unit):
    """(diameter mm, x mm, y mm) of every hit of an Allegro NC drill file. Tool sizes come from the TnnC lines of
    the header or else from the ';Holesize' lines; a file without tool codes stops (M00) between its tools."""
    k = 25.4 if unit == 'IN' else 1.0
    sized, listed, hits, body, metric = {}, [], [], False, False
    current = 1
    for line in Path(path).read_text(encoding='latin-1').split('\n'):
        line = line.strip()
        if not body:
            metric = metric or line == 'METRIC'
            match = re.match(r'^T(\d+)C([\d.]+)', line)
            if match:
                sized[int(match.group(1))] = float(match.group(2)) * (1.0 if metric else 25.4)
            match = re.match(r'^;.*Holesize\s+\d+\..*?= *([\d.]+).*?(MILS|MM)', line)
            if match:
                listed.append(float(match.group(1)) * (0.0254 if match.group(2) == 'MILS' else 1.0))
            if line in ('%', 'M95'):
                body = True
                tools = sized or dict(enumerate(listed, 1))
            continue
        if line == 'M00':
            current += 1
            continue
        match = re.match(r'^T(\d+)$', line)
        if match:
            current = int(match.group(1))
            continue
        match = re.match(r'^R(\d+)(?:X(-?\d+))?(?:Y(-?\d+))?$', line)
        if match and hits:
            count = int(match.group(1))
            dx = int(match.group(2) or 0) / 10 ** decimals * k
            dy = int(match.group(3) or 0) / 10 ** decimals * k
            diameter, x, y = hits[-1]
            hits.extend((diameter, x + dx * i, y + dy * i) for i in range(1, count + 1))
            continue
        match = re.match(r'^X(-?\d+)Y(-?\d+)', line)
        if match:
            hits.append((tools.get(current, 0.0), int(match.group(1)) / 10 ** decimals * k,
                         int(match.group(2)) / 10 ** decimals * k))
    return hits


def read_routed_slots(path, decimals=5):
    """Centre (x, y) in mm of every routed path (M15 ... M16) of an Allegro NC route file (inches)."""
    k = 25.4 / 10 ** decimals
    out, points, cutting = [], [], False
    for line in Path(path).read_text(encoding='latin-1').split('\n'):
        line = line.strip()
        match = re.match(r'^G0[0-3]X(-?\d+)Y(-?\d+)', line)
        if match:
            points.append((int(match.group(1)) * k, int(match.group(2)) * k))
        elif line == 'M15':
            cutting = True
        elif line == 'M16':
            if cutting and points:
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                out.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
            points, cutting = [], False
    return out


@pytest.mark.allegro16
class TestAllegro16Corpus(unittest.TestCase):
    """Allegro 16.4 and 16.6 boards compared with the films, drill and route files that Allegro made of them."""

    parsed = {}            # board -> reader result, parsed once for all tests

    @classmethod
    def setUpClass(cls):
        from appParsers.ParseGerber import Gerber
        from tests.test_allegro_brd_e2e import _app      # an app object with the options that the Gerber reader needs
        cls.app = _app()
        cls.gerber = Gerber

    def board(self, key):
        if key not in self.parsed:
            self.parsed[key] = AllegroBrd16Parser(app=self.app).parse_file(CORPUS / key / BOARDS[key]['brd'])
        return self.parsed[key]

    def film(self, path):
        parser = self.gerber(self.app)
        self.assertNotEqual(parser.parse_file(str(path)), 'fail', path)
        geometry = unary_union([g for g in parser.solid_geometry if not g.is_empty])
        if parser.units == 'IN':
            from shapely.affinity import scale
            geometry = scale(geometry, 25.4, 25.4, origin=(0, 0))
        return geometry

    def check_board(self, key):
        setup = BOARDS[key]
        folder = CORPUS / key / 'film'
        parsed = self.board(key)
        region = parsed.board_outline.buffer(-0.3)
        for layer, film in setup['films'].items():
            name = parsed.copper_layers[layer] if isinstance(layer, int) else layer
            with self.subTest(board=key, layer=name):
                reference = self.film(folder / film).intersection(region)
                mine = unary_union(parsed.layers[name]['solid_geometry']).intersection(region)
                self.assertLessEqual(mine.symmetric_difference(reference).area / reference.area, LIMIT)
        tools = [tool for groups in parsed.drill_groups.values() for tool in groups.values()]
        holes = [(tool['tooldia'], p) for tool in tools for p in tool['drills']]
        hits = [h for name, decimals, unit in setup['drills'] for h in read_drill_hits(folder / name, decimals, unit)]
        with self.subTest(board=key, check='holes'):
            # every hit of the drill files is a hole of the reader of that size, and there is no other round hole
            self.assertEqual(len(holes), len(hits))
            tolerance = setup['position']
            cell = max(0.1, tolerance)
            grid = {}
            for diameter, point in holes:
                grid.setdefault((round(point.x / cell), round(point.y / cell)), []).append((diameter, point))
            for diameter, x, y in hits:
                near = [d for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                        for d, p in grid.get((round(x / cell) + dx, round(y / cell) + dy), [])
                        if abs(p.x - x) <= tolerance and abs(p.y - y) <= tolerance]
                self.assertTrue(near, 'no hole near (%.3f, %.3f) mm' % (x, y))
                self.assertTrue(any(abs(d - diameter) < 0.01 for d in near),
                                'the hole at (%.3f, %.3f) mm is not %.3f mm' % (x, y, diameter))
        if setup['rou']:
            with self.subTest(board=key, check='slots'):
                slots = [((a.x + b.x) / 2, (a.y + b.y) / 2) for tool in tools for a, b in tool['slots']]
                routed = read_routed_slots(folder / setup['rou'])
                self.assertEqual(len(slots), len(routed))
                for x, y in routed:
                    self.assertTrue(any(abs(sx - x) < 0.05 and abs(sy - y) < 0.05 for sx, sy in slots),
                                    'no slot at (%.3f, %.3f) mm' % (x, y))

    def test_cutiepi_16_6(self):
        self.check_board('cutiepi')

    def test_beagleboard_xm_16_4(self):
        self.check_board('xm')

    def test_beaglebone_black_16_4(self):
        self.check_board('bbb')

    def test_beaglebone_revc1_16_4(self):
        self.check_board('bbc1')

    def test_beagleboard_x15_16_6_twelve_layers(self):
        self.check_board('x15')

    def test_parallella_gen0_16_4(self):
        self.check_board('pa0')

    def test_parallella_gen1_16_4(self):
        self.check_board('pa1')

    def test_parallella_gen11_16_4(self):
        self.check_board('pa11')

    def test_parallella_hb1a_16_6_millimetres(self):
        self.check_board('aafm')

    def test_parallella_buddy_16_6_millimetres(self):
        self.check_board('buddy')

    def test_older_15_x_boards_are_refused(self):
        for key, name in (('refused15_ctc057', 'ctc057.brd'), ('refused15_ctc072', 'ctc072.brd')):
            for reader in (AllegroBrdParser, AllegroBrd16Parser):
                with self.subTest(board=key, reader=reader.__name__), self.assertRaises(AllegroBrdParseError):
                    reader().parse_file(CORPUS / key / name)
            with self.assertRaises(AllegroBrdParseError) as context:
                AllegroBrd16Parser().parse_file(CORPUS / key / name)
            self.assertIn('15.x', str(context.exception))
