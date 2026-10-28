import math
from pathlib import Path
import re
import struct
import unittest
from unittest.mock import patch

import pytest

from shapely import LineString, Point, Polygon, box
from shapely.affinity import rotate, translate
from shapely.ops import unary_union

from appParsers.ParsePcbDoc import PcbDocParser, PcbDocParseError


UNIT = 2.54e-6


def raw(value):
    return round(value / UNIT)


def block(data):
    return struct.pack('<I', len(data)) + data


def properties(values):
    return block(('|' + '|'.join('%s=%s' % item for item in values.items()) + '\0').encode('latin1'))


def track(layer=1, start=(1, 2), end=(5, 2), width=0.4, flags=0):
    data = bytearray(45)
    data[0] = layer
    data[1] = flags
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<iiiii', data, 13, *(raw(v) for v in (*start, *end, width)))
    return b'\x04' + block(data)


def region(shell, holes=(), layer=1, kind=0, cutout=False, polygon=65535):
    data = bytearray(18)
    data[0] = layer
    struct.pack_into('<HHH', data, 3, 65535, polygon, 65535)
    struct.pack_into('<H', data, 14, len(holes))
    data += properties({'KIND': kind, 'ISBOARDCUTOUT': str(cutout).upper()})
    for ring in [shell, *holes]:
        data += struct.pack('<I', len(ring))
        for x, y in ring:
            data += struct.pack('<dd', x / UNIT, y / UNIT)
    return b'\x0b' + block(data)


def pad(layer=74, size=(3, 2), shape=1, hole=1, rotation=0, slot=0, slotrotation=0,
        mode=0, alt=0, corner=0, plated=True):
    data = bytearray(171)
    data[0] = layer
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<ii', data, 13, raw(5), raw(5))
    for offset in (21, 29, 37):
        struct.pack_into('<ii', data, offset, *(raw(v) for v in size))
    struct.pack_into('<i', data, 45, raw(hole))
    data[49:52] = bytes([shape] * 3)
    struct.pack_into('<d', data, 52, rotation)
    data[60] = plated
    data[62] = mode
    data[101] = 0
    data[102] = 2
    struct.pack_into('<i', data, 90, raw(0.1))
    stack = bytearray(596)
    stack[262] = 2 if slot else 0
    struct.pack_into('<id', stack, 263, raw(slot), slotrotation)
    stack[532:564] = bytes([alt] * 32)
    stack[564:596] = bytes([corner] * 32)
    return b'\x02' + block(b'\x011') + block(b'') * 3 + block(data) + block(stack)


def board(stack=(1, 32)):
    values = {'ORIGINX': '0mil', 'ORIGINY': '0mil', 'KIND': 'Protel_Advanced_PCB'}
    for i, (x, y) in enumerate([(0, 0), (10, 0), (10, 10), (0, 10)]):
        values['VX%d' % i] = '%smm' % x
        values['VY%d' % i] = '%smm' % y
    for i, layer in enumerate(stack):
        values['LAYER%dNAME' % layer] = 'Layer %d' % layer
        values['LAYER%dNEXT' % layer] = str(stack[i + 1] if i + 1 < len(stack) else 0)
        values['LAYER%dPREV' % layer] = str(stack[i - 1] if i else 0)
    return properties(values)


def text_record(text, position=(1, 2), height=1, width=0.1, angle=0, mirrored=False,
                inverted=False, frame=None, alphabet=1):
    data = bytearray(137)
    data[0] = 1
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<iiiH', data, 13, raw(position[0]), raw(position[1]), raw(height), alphabet)
    struct.pack_into('<d', data, 27, angle)
    data[35] = mirrored
    struct.pack_into('<i', data, 36, raw(width))
    data[110] = inverted
    if frame:
        data[123] = 1
        struct.pack_into('<ii', data, 124, *(raw(v) for v in frame))
        data[132] = 3
    value = text.encode('latin1')
    return b'\x05' + block(data) + block(bytes([len(value)]) + value)


def reference_linear_strokes(filename, diameter):
    """Read original Gerber line strokes for comparison, not production rendering."""
    data = filename.read_bytes().decode('ascii')
    factor = 25.4 if '%MOIN' in data else 1.0
    format_spec = re.search(r'%FSLAX\d(\d)Y', data)
    if not format_spec:
        raise ValueError('Reference has no supported absolute coordinate format')
    decimals = int(format_spec[1])
    coordinate_factor = factor * 10 ** -decimals
    tools = {int(number) for number, size in re.findall(r'%ADD(\d+)C,([\d.]+)\*%', data)
             if abs(float(size) * factor - diameter) < 1e-9}
    x = y = 0.0
    operation = 2
    tool = 0
    interpolation = 1
    current = []
    paths = []
    for token in data.split('*'):
        token = token.strip().strip('%').strip()
        if re.fullmatch(r'D[1-9]\d+', token):
            if len(current) > 1 and tool in tools:
                paths.append(LineString(current))
            current = []
            tool = int(token[1:])
            continue
        if token in ('G01', 'G02', 'G03'):
            interpolation = int(token[-1])
            continue
        if not token or not re.fullmatch(r'(?:X-?\d+)?(?:Y-?\d+)?(?:I-?\d+)?(?:J-?\d+)?(?:D0?[123])?', token):
            continue
        code = re.search(r'D0?([123])$', token)
        if code:
            operation = int(code[1])
        coord_x = re.search(r'X(-?\d+)', token)
        coord_y = re.search(r'Y(-?\d+)', token)
        x = int(coord_x[1]) * coordinate_factor if coord_x else x
        y = int(coord_y[1]) * coordinate_factor if coord_y else y
        if operation == 2:
            if len(current) > 1 and tool in tools:
                paths.append(LineString(current))
            current = [(x, y)]
        elif operation == 1 and interpolation == 1:
            current.append((x, y))
    if len(current) > 1 and tool in tools:
        paths.append(LineString(current))
    return paths, coordinate_factor


class TestPcbDocParser(unittest.TestCase):
    def parser(self, streams=None, stack=(1, 32)):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        parser = PcbDocParser()
        data = {'Board6': board(stack)}
        data.update(streams or {})
        parser._parse_streams(data)
        return parser

    def test_regions_preserve_holes_and_nested_islands_for_export(self):
        outer = list(box(0, 0, 9, 9).exterior.coords)
        inner = list(box(2, 2, 7, 7).exterior.coords)
        island = list(box(3, 3, 4, 4).exterior.coords)
        parser = self.parser({'Regions6': region(outer, [inner]) + region(island)})
        layer = parser.layers[1]
        self.assertAlmostEqual(unary_union(layer['solid_geometry']).area, 57)
        exported = Polygon()
        for item in layer['tools'][0]['geometry']:
            solid = item['solid']
            exported = exported.union(Polygon(solid.exterior)).difference(
                unary_union([Polygon(r) for r in solid.interiors]))
        self.assertAlmostEqual(exported.area, 57)
        self.assertTrue(exported.contains(Point(3.5, 3.5)))
        self.assertFalse(exported.contains(Point(5, 5)))

    def test_cutout_removes_board_and_copper_not_keepout_tracks(self):
        hole = list(box(4, 4, 6, 6).exterior.coords)
        parser = self.parser({'Regions6': region(list(box(0, 0, 10, 10).exterior.coords)) +
                              region(hole, cutout=True), 'Tracks6': track(56)})
        self.assertAlmostEqual(parser.board_outline.area, 96)
        self.assertAlmostEqual(unary_union(parser.layers[1]['solid_geometry']).area, 96)
        self.assertGreater(unary_union(parser.layers[56]['solid_geometry']).area, 0)

    def test_negative_plane_is_bounded_and_not_positive_artwork(self):
        parser = self.parser({'Tracks6': track(39)}, stack=(1, 39, 32))
        plane = unary_union(parser.layers[39]['solid_geometry'])
        self.assertFalse(plane.contains(Point(3, 2)))
        self.assertTrue(plane.contains(Point(8, 8)))
        self.assertTrue(box(0, 0, 10, 10).covers(plane))

    def test_multilayer_pad_uses_only_linked_copper_layers(self):
        parser = self.parser({'Pads6': pad()}, stack=(1, 2, 32))
        self.assertEqual(set(parser.layers), {1, 2, 32, 37, 38})
        self.assertEqual(sum(len(t['drills']) for g in parser.drill_groups.values() for t in g.values()), 1)
        self.assertAlmostEqual(next(iter(next(iter(parser.drill_groups.values())).values()))['tooldia'], 1, places=5)

    def test_rounded_rectangle_and_oblong_are_not_ellipses(self):
        oblong = self.parser({'Pads6': pad(layer=1, hole=0)})
        geo = unary_union(oblong.layers[1]['solid_geometry'])
        self.assertAlmostEqual(geo.area, 2 + math.pi, delta=0.01)
        rounded = self.parser({'Pads6': pad(layer=1, shape=2, hole=0, alt=9, corner=50)})
        geo = unary_union(rounded.layers[1]['solid_geometry'])
        self.assertAlmostEqual(geo.area, 6 - (4 - math.pi) * 0.25, delta=0.01)

    def test_true_slot_endpoints_include_pad_and_slot_rotation(self):
        parser = self.parser({'Pads6': pad(slot=3, rotation=30, slotrotation=15, plated=False)})
        group = next(iter(parser.drill_groups))
        self.assertIn('NPTH', group)
        tool = next(iter(parser.drill_groups[group].values()))
        self.assertEqual(tool['drills'], [])
        first, last = tool['slots'][0]
        self.assertAlmostEqual(first.distance(last), 2, places=5)
        self.assertAlmostEqual(last.x - first.x, math.sqrt(2), places=5)
        self.assertAlmostEqual(last.y - first.y, math.sqrt(2), places=5)

    def test_tracks_arcs_and_optional_tails(self):
        arc = bytearray(56)
        arc[0] = 1
        struct.pack_into('<HHH', arc, 3, 65535, 65535, 65535)
        struct.pack_into('<iii', arc, 13, raw(5), raw(5), raw(2))
        struct.pack_into('<dd', arc, 25, 0, 90)
        struct.pack_into('<i', arc, 41, raw(0.2))
        parser = self.parser({'Tracks6': track() * 2, 'Arcs6': b'\x01' + block(arc)})
        follows = parser.layers[1]['follow_geometry']
        self.assertEqual(len(follows), 3)
        self.assertAlmostEqual(follows[-1].length, math.pi, delta=0.001)
        self.assertAlmostEqual(follows[-1].coords[-1][1], 7, places=5)

    def test_bad_lengths_tags_and_header_counts_raise(self):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        for stream in (track()[:-1], b'\x04\xff\xff\xff\x7f', b'\x09' + block(b'abc'), pad()[:-1]):
            with self.subTest(stream=stream[:8]):
                with self.assertRaises(PcbDocParseError):
                    self.parser({'Tracks6' if stream[0] != 2 else 'Pads6': stream})
        with self.assertRaises(PcbDocParseError):
            PcbDocParser()._parse_streams({'Board6': board(), 'Tracks6': track()}, {'Tracks6': 2})

    def test_v8_pad_layer_marker_is_not_a_blind_drill_span(self):
        record = bytearray(pad())
        reader_offset = 1
        for _ in range(4):
            reader_offset += 4 + struct.unpack_from('<I', record, reader_offset)[0]
        struct.pack_into('<I', record, reader_offset + 4 + 114, 0x0103000f)
        parser = self.parser({'Pads6': bytes(record)})
        self.assertEqual(set(parser.drill_groups), {'PTH_1-32'})

    def test_full_pad_stack_uses_inner_size_and_positive_copper_offset(self):
        record = bytearray(pad(mode=2, shape=2, rotation=90))
        offset = 1
        parts = []
        for _ in range(6):
            length = struct.unpack_from('<I', record, offset)[0]
            parts.append(offset + 4)
            offset += 4 + length
        struct.pack_into('<ii', record, parts[4] + 29, raw(1.5), raw(0.8))
        struct.pack_into('<i', record, parts[5], raw(0.9))
        struct.pack_into('<i', record, parts[5] + 116, raw(0.5))
        record[parts[5] + 232] = 2
        struct.pack_into('<i', record, parts[5] + 275, raw(0.5))
        parser = self.parser({'Pads6': bytes(record)}, stack=(1, 2, 3, 32))
        top = unary_union(parser.layers[1]['solid_geometry'])
        inner = unary_union(parser.layers[2]['solid_geometry'])
        self.assertAlmostEqual(top.centroid.y, 5.5, places=5)
        self.assertAlmostEqual(inner.area, 1.2, places=5)
        mid2 = unary_union(parser.layers[3]['solid_geometry'])
        self.assertAlmostEqual(mid2.area, 0.45, places=5)
        drill = next(iter(parser.drill_groups['PTH_1-32'].values()))['drills'][0]
        self.assertAlmostEqual(drill.y, 5, places=5)

    def test_scoped_mask_rule_and_tenting(self):
        record = bytearray(pad(layer=1, hole=0))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        record[offset + 4 + 102] = 1
        rules = b'\0\0' + properties({'RULEKIND': 'SolderMaskExpansion', 'PRIORITY': 1,
            'SCOPE1EXPRESSION': "IsPad AND (IsSMDPad OR InNet('GND'))", 'EXPANSION': '0.2mm'})
        parser = self.parser({'Pads6': bytes(record), 'Rules6': rules})
        opening = unary_union(parser.layers[37]['solid_geometry'])
        self.assertAlmostEqual(opening.bounds[0], 3.3, delta=0.002)   # the circle tolerance (2 um)
        record[offset + 4 + 1] |= 0x20
        parser = self.parser({'Pads6': bytes(record), 'Rules6': rules})
        self.assertNotIn(37, parser.layers)

    def test_shape_based_duplicate_does_not_fill_plain_region_hole(self):
        shell = list(box(0, 0, 9, 9).exterior.coords)
        hole = list(box(2, 2, 7, 7).exterior.coords)
        data = bytearray(18)
        data[0] = 1
        struct.pack_into('<HHH', data, 3, 65535, 0, 65535)
        data += properties({'KIND': 0, 'SUBPOLYINDEX': 65535})
        data += struct.pack('<I', len(shell) - 1)
        for x, y in shell:
            data += struct.pack('<Biiiiidd', 0, raw(x), raw(y), 0, 0, 0, 0, 0)
        parser = self.parser({'Regions6': region(shell, [hole], polygon=0),
                              'ShapeBasedRegions6': b'\x0b' + block(data)})
        self.assertAlmostEqual(unary_union(parser.layers[1]['solid_geometry']).area, 56)

    def test_polygon_cutout_erases_pour_but_not_independent_tracks(self):
        parser = self.parser({'Regions6': region(list(box(0, 0, 9, 9).exterior.coords), polygon=0) +
                              region(list(box(2, 2, 7, 7).exterior.coords), kind=1),
                              'Tracks6': track(start=(3, 4), end=(6, 4))})
        geo = unary_union(parser.layers[1]['solid_geometry'])
        self.assertTrue(geo.contains(Point(4, 4)))
        self.assertFalse(geo.contains(Point(4, 5)))

    def test_plane_clearance_and_connected_pad_relief(self):
        data = bytearray(pad(size=(2, 2)))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', data, offset)[0]
        struct.pack_into('<H', data, offset + 4 + 3, 0)
        board_data = board((1, 39, 32))
        values = PcbDocParser._props(board_data[4:])
        values['LAYER39NETNAME'] = 'GND'
        rules = b'\0\0' + properties({'RULEKIND': 'PlaneClearance', 'CLEARANCE': '0.5mm'})
        rules += b'\0\0' + properties({'RULEKIND': 'PlaneConnect', 'CONNECTSTYLE': 'Relief',
            'AIRGAPWIDTH': '0.5mm', 'RELIEFCONDUCTORWIDTH': '0.3mm', 'RELIEFENTRIES': 4})
        parser = PcbDocParser()._parse_streams({'Board6': properties(values), 'Pads6': bytes(data),
                                              'Nets6': properties({'NAME': 'GND'}), 'Rules6': rules})
        copper = unary_union(parser.layers[39]['solid_geometry'])
        self.assertTrue(copper.contains(Point(6.3, 5)))
        self.assertFalse(copper.contains(Point(5.9, 5.9)))
        values['LAYER39NETNAME'] = 'VCC'
        parser = PcbDocParser()._parse_streams({'Board6': properties(values), 'Pads6': bytes(data),
                                              'Nets6': properties({'NAME': 'GND'}), 'Rules6': rules})
        self.assertFalse(unary_union(parser.layers[39]['solid_geometry']).contains(Point(6.3, 5)))

    def test_true_type_font_counters_remain_holes(self):
        parser = self.parser()
        geo = parser._font_geometry('OB', 'Arial', 'Regular', 1)
        self.assertEqual(sum(len(p.interiors) for p in parser._polygons_of(geo)), 3)

    def test_reconstructed_strokes_match_original_gto_not_filled_glyphs(self):
        import olefile
        from appParsers.ParsePcbDoc import _Reader

        fixture = Path(__file__).parent / 'fixtures' / 'altium'
        references, quantum = reference_linear_strokes(fixture / 'Gerber' / 'I2C_level_shifter.GTO', 0.127)
        parser = PcbDocParser().parse_file(fixture / 'I2C_level_shifter.PcbDoc')
        with olefile.OleFileIO(str(fixture / 'I2C_level_shifter.PcbDoc')) as container:
            reader = _Reader(container.openstream('Texts6/Data').read(), 'Texts6')
        checked = set()
        while reader.remaining():
            reader.take(1)
            data = reader.block()
            text = reader.block()[1:].decode('latin1')
            if data[0] != 33 or text not in ('H1', 'H2', 'R1', 'R2', 'R3', 'R4', 'T1', 'T2', '3.3V'):
                continue
            height = struct.unpack_from('<i', data, 21)[0] * UNIT
            width = struct.unpack_from('<i', data, 36)[0] * UNIT
            position = parser._xy(*struct.unpack_from('<ii', data, 13))
            angle = struct.unpack_from('<d', data, 27)[0]
            _, follows = parser._stroke_geometry(text, height, width)
            expected = unary_union([translate(rotate(line, angle, origin=(0, 0)),
                                              xoff=position[0], yoff=position[1]) for line in follows])
            window = box(*expected.bounds).buffer(quantum * (len(text) + 1))
            original = unary_union([line for line in references if window.covers(line)])
            self.assertFalse(original.is_empty, text)
            # Altium's export accumulates integer coordinate quantization per glyph.
            self.assertLessEqual(expected.hausdorff_distance(original), quantum * (len(text) + 1), text)
            checked.add(text)
        self.assertEqual(checked, {'H1', 'H2', 'R1', 'R2', 'R3', 'R4', 'T1', 'T2', '3.3V'})

    def test_dimension_annotations_render_on_native_layer(self):
        dim = {'DIMENSIONKIND': 1, 'LAYER': 'MECHANICAL1', 'ANGLE': 0,
               'X1': '0mm', 'Y1': '-2mm', 'REFERENCES_COUNT': 2,
               'REFERENCE0POINTX': '0mm', 'REFERENCE0POINTY': '0mm',
               'REFERENCE1POINTX': '10mm', 'REFERENCE1POINTY': '0mm',
               'LINEWIDTH': '0.1mm', 'TEXTHEIGHT': '1mm', 'TEXT1X': '4mm', 'TEXT1Y': '-3mm',
               'TEXT1ANGLE': 0, 'TEXTDIMENSIONUNIT': 'Millimeters', 'TEXTPRECISION': 2,
               'FONTNAME': 'Arial', 'USETTFONTS': 'TRUE', 'ARROWSIZE': '0.3mm',
               'ARROWLENGTH': '0.5mm', 'EXTENSIONPICKGAP': '0.1mm', 'EXTENSIONOFFSET': '0.2mm'}
        parser = self.parser({'Dimensions6': b'\x01\0' + properties(dim)})
        self.assertIn(57, parser.layers)
        self.assertGreater(len(parser.layers[57]['follow_geometry']), 2)

    def test_blind_via_uses_stack_span_and_only_reached_mask_side(self):
        data = bytearray(203)
        struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
        struct.pack_into('<iiii', data, 13, raw(4), raw(4), raw(2), raw(0.6))
        data[29:31] = bytes((2, 32))
        data[66] = 2
        struct.pack_into('<i', data, 54, raw(0.1))
        parser = self.parser({'Vias6': b'\x03' + block(data)}, stack=(1, 2, 32))
        self.assertEqual(set(parser.layers), {2, 32, 38})
        self.assertEqual(set(parser.drill_groups), {'PTH_2-32'})
        self.assertEqual(len(parser.drill_groups['PTH_2-32'][1]['drills']), 1)

    def test_fixture_native_content_origin_and_drills(self):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        filename = Path(__file__).parent / 'fixtures' / 'altium' / 'I2C_level_shifter.PcbDoc'
        parser = PcbDocParser().parse_file(filename)
        self.assertEqual(parser.record_counts['Tracks6'], 284)
        self.assertEqual(parser.record_counts['Pads6'], 34)
        self.assertEqual(parser.record_counts['Texts6'], 32)
        self.assertEqual(parser.record_counts['Regions6'], 33)
        self.assertAlmostEqual(parser.origin[0], 57.15)
        self.assertAlmostEqual(parser.origin[1], 71.374)
        for actual, expected in zip(parser.board_outline.bounds, (0, 0, 20.32, 12.446)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(set(parser.layers), {1, 32, 33, 34, 35, 36, 37, 38, 56, 57, 69, 70, 71})
        self.assertGreater(unary_union(parser.layers[32]['solid_geometry']).area, 35)
        self.assertGreater(len(parser.layers[32]['follow_geometry']), 10)
        self.assertGreater(sum(len(p.interiors) for p in parser.layers[1]['solid_geometry']), 0)
        self.assertEqual(sum(len(t['drills']) for g in parser.drill_groups.values() for t in g.values()), 8)
        for layer in parser.layers.values():
            self.assertTrue(all(isinstance(p, Polygon) and p.is_valid for p in layer['solid_geometry']))


@pytest.mark.diptrace
class TestDipTraceBoard(unittest.TestCase):
    FIXTURE = Path(__file__).parent / 'fixtures' / 'diptrace' / 'STM-32_GPSDO_v1.0.dip'

    def test_native_counted_component_field_ids_preserve_manufacturing_geometry(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        # Differential_Pairs v41 stores two or three field IDs before the
        # display/ref/value strings. GPSDO's first component has an empty list.
        for fields in ((222, 1786), (3147, 278, 3154)):
            with self.subTest(fields=fields):
                encoded = b''.join((1000000 + value).to_bytes(3, 'big') for value in fields)
                changed = (data[:1476] + (1000000 + len(fields)).to_bytes(3, 'big')
                           + encoded + data[1479:])
                parsed = DipTraceParser().parse_bytes(changed)
                self.assertEqual(parsed.pad_nets, baseline.pad_nets)
                self.assertEqual(parsed.drill_groups, baseline.drill_groups)
                for layer in (0, 1):
                    self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_first_point_width_is_not_a_segment_width(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        changed = bytearray(data)
        changed[78477:78481] = (1000000000).to_bytes(4, 'big')
        parsed = DipTraceParser().parse_bytes(changed)
        self.assertEqual(parsed.drill_groups, baseline.drill_groups)
        for layer in (0, 1):
            self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)
        # The same zero on the entering segment's second point is invalid.
        changed[78518:78522] = (1000000000).to_bytes(4, 'big')
        with self.assertRaises(DipTraceParseError):
            parsed.parse_bytes(changed)
        self.assertEqual(parsed.layers, {})

    def test_native_documentation_table_is_drawn_on_top_assembly(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        string = lambda s: len(s).to_bytes(2, 'big') + s.encode('utf-16be')
        point = lambda x, y: number(round(x * 30000), 4) + number(round(-y * 30000), 4)

        # Marking/Quick_Start/Habitat_V3 table grammar; synthetic text and
        # rectangles avoid redistributing those boards. Cells are column-major.
        def table(layer=0, tail=0):
            data = (b'\1\0' + number(0) + number(layer) + number(4) + number(0) + number(-1) + b'\0' * 3
                    + number(457200, 4) + number(121920, 4) + point(250, 250) + point(270, 246)
                    + string('Arial') + string('Table') + number(2) + number(300000, 4) * 2
                    + number(2) + number(60000, 4) * 2)
            for column, row, text, align in ((0, 0, 'A', 1), (0, 1, 'B', 0), (1, 0, '', 0), (1, 1, '', 0)):
                x, y = 250 + 10 * column, 250 - 2 * row
                data += (b'\0' + number(4) + number(0) + number(align) + b'\0' * 3 + string(text)
                         + string('Arial') + point(x, y) + point(x + 10, y - 2)
                         + b'\1' + number(-20000, 4) + number(10000, 4))
            return data + b'\0\1' + number(-20000, 4) + number(10000, 4) + b'\0\0' + number(tail)

        # DAP42 v40 has the line-width frame after an empty list, as all three
        # table specimens do (GPSDO's list isn't empty).
        data = (self.FIXTURE.parent / 'DAP42v2.dip').read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        font = data.index(DipTraceParser.FONT_MARKER)
        count = font + 27 + 2 * int.from_bytes(data[font + 9:font + 11], 'big')
        insert = lambda raw: data[:count] + number(1) + raw + data[count + 3:]
        parsed = DipTraceParser().parse_bytes(insert(table()))
        for layer in (0, 1):
            self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)
        region = box(240, 240, 280, 260)
        drawn = unary_union(parsed.layers['Top Assembly']['solid_geometry']).intersection(region)
        # Every cell is a closed rectangle at the table line width (0.25 mm).
        for xy in ((250, 247), (260, 248), (270, 249), (265, 250), (265, 246)):
            self.assertTrue(drawn.covers(Point(xy)), xy)
        self.assertFalse(drawn.covers(Point(265, 249)))
        self.assertFalse(drawn.covers(Point(260.2, 249)))
        # Align 1 centres the text; align 0 starts the ink 1.000 mm from the
        # left edge (Habitat_V3 TopAssembly.gbr).
        centred = drawn.intersection(box(250.3, 248.3, 259.7, 249.7)).bounds
        self.assertAlmostEqual((centred[0] + centred[2]) / 2, 255, delta=0.1)
        left = drawn.intersection(box(250.3, 246.3, 259.7, 247.7)).bounds
        self.assertAlmostEqual(left[0], 251.0, delta=0.05)
        # A table on an unproved layer is reported, not drawn somewhere.
        parsed = DipTraceParser().parse_bytes(insert(table(layer=1)))
        layer =parsed.layers.get('Top Assembly', {'solid_geometry': []})
        self.assertTrue(unary_union(layer['solid_geometry']).intersection(region).is_empty)
        self.assertTrue(any('table' in warning.lower() for warning in parsed.warnings))
        # Wrong grid dimensions, a nonzero table tail and truncated cells
        # mustn't yield partial copper or shift the pour cache.
        bad_width = bytearray(table())
        bad_width[71:75] = number(30000, 4)
        for broken in (bytes(bad_width), table(tail=1), table()[:100]):
            with self.subTest(tail=broken[-12:]):
                with self.assertRaises(DipTraceParseError):
                    parsed.parse_bytes(insert(broken))
                self.assertEqual(parsed.layers, {})

    def test_native_net_membership_and_clearance_survive_import(self):
        from appParsers.ParseDipTrace import DipTraceParser

        parsed = DipTraceParser().parse_file(self.FIXTURE)
        # Independently decoded primary table at 78350: 26 nets. The
        # component/pad references belong to the net, not to nearby artwork.
        self.assertEqual(len(parsed.nets), 26)
        net = parsed.nets[0]
        self.assertEqual(net['name'], '1PPS')
        self.assertEqual(net['pads'], ((24, 2), (31, 1), (37, 37)))
        self.assertAlmostEqual(net['width'], 0.254)
        self.assertAlmostEqual(net['clearance'], 0.2)
        self.assertAlmostEqual(net['via_size'], 1.016)
        self.assertAlmostEqual(net['via_drill'], 0.2665)
        self.assertEqual(net['net_class'], 0)
        self.assertEqual(parsed.nets[3]['name'], 'Gnd')
        self.assertEqual(parsed.nets[3]['pads'], ())

    def test_native_broken_net_identity_does_not_return_manufacturing_geometry(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = bytearray(self.FIXTURE.read_bytes())
        data[78364:78367] = (1000099).to_bytes(3, 'big')
        parsed = DipTraceParser()
        with self.assertRaisesRegex(DipTraceParseError, 'net'):
            parsed.parse_bytes(data)
        self.assertEqual(parsed.layers, {})
        self.assertEqual(parsed.drill_groups, {})

    def test_native_unresolved_route_geometry_is_not_imported_as_a_straight_track(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        original = self.FIXTURE.read_bytes()
        # Second point of the first 1PPS trace, at 78504. These fields are
        # zero in the verified straight-route frame. Nonzero geometry must
        # not disappear while the importer emits a straight copper segment.
        for offset, value in ((78527, (1000001).to_bytes(3, 'big')), (78530, b'\1')):
            with self.subTest(offset=offset):
                data = bytearray(original)
                data[offset:offset + len(value)] = value
                parsed = DipTraceParser()
                with self.assertRaisesRegex(DipTraceParseError, 'routing geometry'):
                    parsed.parse_bytes(data)
                self.assertEqual(parsed.layers, {})
                self.assertEqual(parsed.drill_groups, {})

    def test_native_curved_routes_follow_the_circle_in_both_directions(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        original = self.FIXTURE.read_bytes()
        # The arm v38 example retains Arc=1 on the middle of three circular
        # points. Unequal half-sweeps ensure it isn't just a rounded corner.
        for middle, end, sweep in (((0.6, 0.8), (0, 1), math.pi / 2),
                                   ((0.6, -0.8), (0, -1), -math.pi / 2),
                                   ((-1, 0), (0, 1), -3 * math.pi / 2)):
            with self.subTest(sweep=sweep):
                nodes = b''
                for index, (x, y) in enumerate(((1, 0), middle, end)):
                    nodes += (number(round((10 + x) * 30000), 4) + number(round(-(100 + y) * 30000), 4)
                              + number(0) + number(index) + number(6000, 4) + number(0, 4)
                              + b'\0' + number(0) + bytes((index == 1,)) + number(-1)
                              + number(0, 4) + number(0) * 2 + b'\0')
                data = original[:78460] + number(3) + nodes + original[78545:]
                parsed = DipTraceParser(circle_steps=64).parse_bytes(data)
                count = round(64 * abs(sweep) / (2 * math.pi))
                expected = LineString([(10 + math.cos(sweep * i / count), 100 + math.sin(sweep * i / count))
                                       for i in range(count + 1)]).buffer(0.1)
                actual = unary_union(parsed.layers[0]['solid_geometry']).intersection(box(8, 98, 12, 102))
                # Floating-point circle fitting can choose one extra segment.
                # At 64 steps/circle the unit-circle chord error is <0.002 mm.
                self.assertLess(actual.hausdorff_distance(expected), 0.002)

    def test_native_grouped_or_selected_trace_keeps_its_saved_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        original = DipTraceParser().parse_bytes(data)
        # The first 1PPS trace's Group follows its three state flags.
        # The last byte of its second point is Selected. Neither changes copper.
        for offset, value in ((78454, (1000000).to_bytes(3, 'big')), (78544, b'\1')):
            with self.subTest(offset=offset):
                changed = bytearray(data)
                changed[offset:offset + len(value)] = value
                parsed = DipTraceParser().parse_bytes(changed)
                for layer in (0, 1):
                    self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(original.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_different_nets_at_one_coordinate_do_not_create_a_via(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        nets = number(26)
        for identity in range(26):
            name = ('Net %d' % identity).encode('utf-16be')
            nets += (b'\1\0' + number(0) + number(-1) * 2 + number(identity) + number(0)
                     + number(7620, 4) + number(6000, 4) + (len(name) // 2).to_bytes(2, 'big') + name
                     + number(30480, 4) + number(7995, 4) + b'\1' + b'\0' * 7 + number(0) * 2)
            nets += number(1 if identity < 2 else 0)
            if identity >= 2:
                continue
            # A declared trace-end connection on Net 1 doesn't connect it to
            # Net 0. Merely sharing coordinates across layers isn't a via.
            nets += (number(0) * 6 + bytes((identity, 0)) + b'\0' * 3
                     + number(-1) + number(0) + number(2))
            for x, y in ((10, 100), (11, 100) if identity == 0 else (10, 101)):
                nets += (number(x * 30000, 4) + number(-y * 30000, 4) + number(identity)
                         + number(0) + number(6000, 4) + number(18000, 4)
                         + b'\1' + number(0) + b'\0' + number(0) + number(9000, 4)
                         + number(0) + number(1) + b'\0')
        data = self.FIXTURE.read_bytes()
        parsed = DipTraceParser().parse_bytes(data[:78350] + nets + data[91463:])
        holes = [p for group in parsed.drill_groups.values() for tool in group.values() for p in tool['drills']]
        self.assertFalse(any(p.distance(Point(10, 100)) < 1e-8 for p in holes))
        self.assertTrue(unary_union(parsed.layers[0]['solid_geometry']).covers(Point(10.5, 100)))
        self.assertTrue(unary_union(parsed.layers[1]['solid_geometry']).covers(Point(10, 100.5)))

    def test_native_v38_example_corpus_plane_geometry(self):
        import os
        from appParsers.ParseDipTrace import DipTraceParser

        root = os.environ.get('DIPTRACE_V38_EXAMPLES')
        if not root:
            self.skipTest('Set DIPTRACE_V38_EXAMPLES to the genuine SorokinRed Examples directory')
        for name in ('PCB_1', 'PCB_5', 'PCB_6', 'arm', 'BGA_autorouter', 'example_routed'):
            with self.subTest(board=name):
                parsed = DipTraceParser().parse_file(Path(root) / (name + '.dip'))
                self.assertEqual(parsed.version, 38)
                self.assertTrue(parsed.drill_groups)
                planes = [layer for layer, settings in parsed.copper_layers.items() if settings['type'] == 1]
                self.assertTrue(planes)
                for layer in planes:
                    copper = unary_union(parsed.layers[layer]['solid_geometry'])
                    self.assertTrue(copper.is_valid)
                    self.assertGreater(copper.area, 0)
                    # Plane type isn't negative artwork: empty board corners
                    # outside all saved pads/traces/pours stay empty.
                    self.assertFalse(copper.contains(Point(-999, -999)))
                if name == 'PCB_1':
                    copper = unary_union(parsed.layers[1]['solid_geometry'])
                    # Independently decoded native cache: first scan at
                    # y=28.392 stops at -45.542 and resumes at -43.352667.
                    self.assertTrue(copper.contains(Point(-40, 28.392)))
                    self.assertFalse(copper.contains(Point(-44.447, 28.392)))
                    # Two diagonal 0.330333-mm spokes cross at (-24.13,
                    # -21.59). Off-axis clearance must not become solid fill.
                    self.assertTrue(copper.contains(Point(-23.33, -20.79)))
                    self.assertFalse(copper.contains(Point(-23.33, -21.59)))
                    self.assertFalse(copper.contains(Point(-24.13, -20.79)))
                if name == 'BGA_autorouter':
                    self.assertIn('PTH_3-7', parsed.drill_groups)
                if name == 'example_routed':
                    self.assertTrue(any(str(layer).startswith('Unfilled Copper ') for layer in parsed.layers))

    def test_native_pair_metadata_does_not_add_centerline_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        string = lambda s: len(s).to_bytes(2, 'big') + s.encode('utf-16be')
        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        nets = data[78350:91463].replace(string('1PPS'), string('Pair_P'), 1)
        nets = nets.replace(string('2KHz'), string('Pair_N'), 1)
        # Native v41 pair table, with raw center parameters. Its remote
        # centerline is metadata and mustn't become manufactured copper.
        pair = (number(1) + b'\1' + string('Pair') + number(0) * 2 + number(1) + number(0)
                + bytes.fromhex('00ffffaa') + number(0) + number(1)
                + number(0) * 4 + number(-1) * 2 + number(2))
        for x in (250, 260):
            pair += (number(x * 30000, 4) + number(-7500000, 4) + number(0) * 9
                     + b'\0' * 3 + number(0) * 2)
        parsed = DipTraceParser().parse_bytes(data[:78350] + nets + data[91463:] + pair)
        for layer in (0, 1):
            self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_example_pairs_and_documentation_tables(self):
        import os
        from appParsers.ParseDipTrace import DipTraceParser

        root = os.environ.get('DIPTRACE_V38_EXAMPLES')
        if not root:
            self.skipTest('Set DIPTRACE_V38_EXAMPLES to the genuine SorokinRed Examples directory')
        parsed = DipTraceParser().parse_file(Path(root) / 'Differential_Pairs.dip')
        self.assertEqual(len(parsed.nets), 108)
        self.assertEqual(sum(len(net['traces']) for net in parsed.nets.values()), 381)
        self.assertEqual(parsed.nets[39]['name'], 'DiffPair_P')
        self.assertEqual(parsed.nets[38]['name'], 'DiffPair_N')
        copper = unary_union(parsed.layers[3]['solid_geometry'])
        self.assertTrue(copper.contains(Point(247.35, 166.9)))
        self.assertTrue(copper.contains(Point(247.35, 166.6)))
        # Stroking the pair centerline as extra copper would bridge this gap.
        self.assertFalse(copper.contains(Point(247.35, 166.75)))
        # Habitat_V3 is optional (HabitatControl-hardware, not SorokinRed).
        # (board, a point on the table's left border).
        habitat = 'corpus-machadoleonardo94--HabitatControl-hardware--Habitat_V3--layout.dip'
        boards = (('Marking.dip', (-85.09, 10)), ('Quick_Start.dip', (-155.111, 40)), (habitat, (91.1225, 70)))
        for name, edge in boards:
            with self.subTest(board=name):
                if not (Path(root) / name).exists():
                    self.skipTest('%s is not in DIPTRACE_V38_EXAMPLES' % name)
                parsed = DipTraceParser().parse_file(Path(root) / name)
                assembly = unary_union(parsed.layers['Top Assembly']['solid_geometry'])
                self.assertTrue(assembly.covers(Point(edge)))
                if name == 'Marking.dip':
                    self.assertEqual(len(parsed.nets), 14)
                    self.assertEqual(sum(len(net['traces']) for net in parsed.nets.values()), 16)
                elif name == habitat:
                    # Habitat_V3 TopAssembly.gbr (DipTrace 4.3.0.4) ends at the
                    # table corner (299.30263, 136.2075) + its 0.25 mm stroke.
                    self.assertAlmostEqual(assembly.bounds[2], 299.30263 + 0.125, delta=0.002)
                    self.assertAlmostEqual(assembly.bounds[3], 136.2075 + 0.125, delta=0.002)
                    # The pour count follows the table (5 pours from 371695);
                    # the first pour's top hatch line spans the whole board.
                    copper = unary_union(parsed.layers[0]['solid_geometry'])
                    for x in (5, 45, 85):
                        self.assertTrue(copper.covers(Point(x, 133.492)), x)

    def test_native_saved_teardrops_add_copper_or_warn(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        point = lambda x, y: number(round(x * 30000), 4) + number(round(-y * 30000), 4)
        record = lambda layer, draw, points: (number(layer) + point(*draw) + number(len(points))
                                              + b''.join(point(*xy) for xy in points))
        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        self.assertFalse([w for w in baseline.warnings if 'teardrop' in w])
        # GPSDO v54 frame: 222 pad settings from 542514, net 1PPS settings
        # at 550506, its two trace link lists at 550542/550545, records at 550548.
        records = (record(0, (999, 999), ((250, 250), (252, 250), (251, 252)))
                   # Bowtie: both ears are filled copper (nonzero = even-odd).
                   + record(1, (0, 0), ((260, 250), (262, 252), (262, 250), (260, 252)))
                   + record(0, (561.408, -561.408), ((270, 250),))
                   + record(7, (0, 0), ((280, 250), (282, 250), (281, 252))))
        changed = (data[:550542] + number(2) + number(0) + number(1) + number(2) + number(2) + number(3)
                   + number(4) + records + data[550551:])
        parsed = DipTraceParser().parse_bytes(changed)
        top, bottom = (unary_union(parsed.layers[layer]['solid_geometry']) for layer in (0, 1))
        # Absolute XY, not translated by the saved draw position.
        self.assertTrue(top.covers(Polygon(((250, 250), (252, 250), (251, 252)))))
        self.assertFalse(top.intersects(Point(1249, 1249)))
        self.assertTrue(bottom.covers(Point(260.2, 251)) and bottom.covers(Point(261.8, 251)))
        self.assertFalse(top.intersects(box(269, 249, 271, 251)))
        self.assertFalse(any(layer.intersects(box(279, 249, 283, 253)) for layer in (top, bottom)))
        self.assertGreater(len(parsed.warnings), len(baseline.warnings))
        # An unframed table: the board still imports unchanged, with a warning.
        broken = DipTraceParser().parse_bytes(data[:542514] + number(5) + data[542517:])
        self.assertGreater(len(broken.warnings), len(baseline.warnings))
        for layer in (0, 1):
            self.assertLess(unary_union(broken.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_example_teardrops_and_marked_pour_tail(self):
        import os
        from appParsers.ParseDipTrace import DipTraceParser

        root = os.environ.get('DIPTRACE_V38_EXAMPLES')
        if not root or not (Path(root) / 'CNC_controller.dip').exists():
            self.skipTest('Set DIPTRACE_V38_EXAMPLES to a directory with the official CNC_controller.dip')
        # Official CNC_controller v54 (sha256 c3d580ba): pours 0 and 4 end in 1,
        # 496 saved teardrops in 94 nets. Pads + traces gain 24.443432 mm² on
        # layer 0 and 5.464662 mm² on layer 1 from them.
        parsed = DipTraceParser().parse_file(Path(root) / 'CNC_controller.dip')
        self.assertFalse([w for w in parsed.warnings if 'teardrop' in w])
        self.assertTrue(unary_union(parsed.layers[0]['solid_geometry']).covers(Point(24.3510, 15.7201)))
        self.assertTrue(unary_union(parsed.layers[1]['solid_geometry']).covers(Point(1.8450, 23.8391)))

    def test_native_repeated_custom_pad_contour_is_one_copper_island(self):
        from appParsers.ParseDipTrace import DipTraceParser

        vertices = [(-1, 1), (1, 1), (1, -1), (-1, -1)]
        shape = DipTraceParser()._pad_outline(3, 2, 2, vertices * 3)
        self.assertTrue(shape.equals(box(-1, -1, 1, 1)))

    def test_native_pad_layer_lists_have_counted_not_fixed_framing(self):
        from appParsers.ParseDipTrace import DipTraceParser, _Reader

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        def pad(identity, blind, hidden=(), thermal=False):
            return (b'\0' + number(identity) + number(-1) + number(0, 4) * 2 + b'\0' * 4
                    + number(30000, 4) * 2 + number(15000, 4) * 2 + number(0) + b'\0'
                    + number(0) + b'\0' + number(0) + b'\0' + number(len(hidden))
                    + b''.join(number(layer) for layer in hidden)
                    + number(len(blind)) + b''.join(number(layer) for layer in blind)
                    + (number(1) + number(-1) + number(0) + number(4) + number(9910, 4)
                       if thermal else number(0))
                    + b'\0' * 5 + number(0, 4) * 2 + b'\0')
        data = number(3) + pad(0, []) + pad(1, [3, 4, 5, 6, 7], [4], True) + pad(2, [])
        reader = _Reader(data)
        pads = DipTraceParser._pad_table(reader)
        self.assertEqual(reader.pos, len(data))
        self.assertEqual(pads[0][-1]['blind'], (3, 4, 5, 6, 7))
        data += number(0) * 2  # no footprint graphics or mechanical holes
        component = {'header_end': 0, 'end': len(data), 'placement': None, 'ref': 'BlindVia',
                     'angle': 0, 'bottom': False, 'xy': (0, 0), 'local_dimensions': number(10000, 4) * 2}
        parsed = DipTraceParser()
        parsed.version = 38
        parsed._copper_layers = tuple(range(8))
        parsed._component_geometry(data, component)
        self.assertEqual({layer for layer in parsed._solids if isinstance(layer, int)}, {3, 5, 6, 7})
        self.assertEqual(set(parsed._drills), {'PTH_3-7'})
        self.assertAlmostEqual(next(iter(parsed._drills['PTH_3-7'].values()))['tooldia'], 0.5)

    def test_native_plane_by_pads_does_not_invent_board_wide_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        original = DipTraceParser().parse_bytes(data)
        # Native layer framing confirmed in PCB_1, PCB_5, PCB_6, arm and
        # BGA_autorouter examples: Type, PlanePad, NetId, PlaneRing, Locked.
        data[131:134] = (1000001).to_bytes(3, 'big')
        plane = DipTraceParser().parse_bytes(data)
        self.assertLess(unary_union(plane.layers[1]['solid_geometry']).symmetric_difference(
            unary_union(original.layers[1]['solid_geometry'])).area, 1e-8)
        self.assertFalse(unary_union(plane.layers[1]['solid_geometry']).contains(Point(0, 0)))

    def test_native_plane_fixed_ring_uses_drill_not_rectangular_pad(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import rotate

        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data[131:145] = number(1) + number(1) + number(-1) + number(6000, 4) + b'\0'
        parsed = DipTraceParser(circle_steps=128).parse_bytes(data)
        drills = [p for t in parsed.drill_groups['PTH'].values() for p in t['drills']]
        angles = (0, 0, math.degrees(0.7854), math.degrees(0.7854), 90, 90,
                  math.degrees(5.3988), math.degrees(5.3988))
        expected = unary_union([rotate(p.buffer(0.7, quad_segs=32), angle, origin=(p.x, p.y))
                                for p, angle in zip(drills, angles)])
        self.assertLess(unary_union(parsed.layers[1]['solid_geometry']).symmetric_difference(expected).area, 1e-8)
        # The signal side and mask still use the original rectangular/circular
        # copper pad. Fixed Ring is a plane-layer operation, not a pad edit.
        original = DipTraceParser(circle_steps=128).parse_file(self.FIXTURE.parent / 'rotate.dip')
        for layer in (0, 'Top Mask', 'Bottom Mask'):
            self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(original.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_plane_fixed_ring_applies_to_routed_vias(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        parsed = DipTraceParser(circle_steps=128)
        parsed.copper_layers[1] = {'type': 1, 'plane_pad': 1, 'net_id': 7, 'ring': 0.2}
        # A via exists where the route changes layer: 0.1 mm traces end inside both pads.
        node = lambda x, y, layer, via: (number(round(x * 30000), 4) + number(round(-y * 30000), 4)
                                         + number(layer) + number(0) + number(3000, 4) + number(60000, 4)
                                         + bytes((via,)) + number(0) + b'\0' + number(-1)
                                         + number(30000, 4) + number(0) * 2 + b'\0')
        route = (parsed.CHAIN + number(0) + number(3)
                 + node(-0.5, 0, 0, 0) + node(0, 0, 0, 1) + node(0, 0.5, 1, 0))
        parsed._routing(route, 0, len(route))
        expected = Point(0, 0).buffer(0.7, quad_segs=32)
        self.assertLess(unary_union(parsed._solids[1]).symmetric_difference(expected).area, 1e-8)
        self.assertAlmostEqual(unary_union(parsed._solids[0]).bounds[2], 1)
        self.assertAlmostEqual(next(iter(parsed._drills['PTH'].values()))['tooldia'], 1)

    def test_native_plane_saved_fill_keeps_clearance_void_and_thermal_spoke(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        point = lambda x, y: number(round(x * 30000), 4) + number(round(-y * 30000), 4)
        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        data[131:145] = number(1) + number(0) + number(7) + number(6000, 4) + b'\0'
        # Independent saved-cache writer using the native framing observed in
        # PCB_1/5/6. The void is absence of hatch copper, not a positive polygon
        # to fill. A plane's net needn't match every pour: split planes are valid.
        outline = b''.join(point(x, y) for x, y in ((-4, -4), (4, -4), (4, 4), (-4, 4)))
        lines = [((-2.5, y), (2.5, y)) for y in (-2.5, -1.5, 1.5, 2.5)]
        lines += [((x, -2.5), (x, 2.5)) for x in (-2.5, 2.5)]
        segments = b''.join(point(*a) + point(*b) + number(0) for a, b in lines)
        zone = (b'\1\0\0' + number(30000, 4) + number(7500, 4) + number(0, 4)
                + number(-1) + number(1) + number(8) + number(4) + outline
                + number(len(lines)) + segments + number(0) + b'\0' * 14
                + number(1) + point(1, 0) + point(3, 0) + number(6000, 4) + number(0)
                + b'\0' * 24 + b'\1' + number(0))
        data[5872:5875] = number(1) + zone
        parsed = DipTraceParser().parse_bytes(data)
        copper = unary_union(parsed.layers[1]['solid_geometry'])
        self.assertFalse(copper.contains(Point(0, 0)))
        self.assertFalse(copper.contains(Point(0, 0.75)))
        self.assertTrue(copper.contains(Point(2, 0)))
        self.assertFalse(copper.contains(Point(2, 0.25)))
        self.assertTrue(copper.contains(Point(0, 2)))
        self.assertFalse(copper.contains(Point(3.75, 3.75)))

    def test_native_plane_unresolved_pad_layer_overrides_fail_without_partial_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        data[131:134] = (1000001).to_bytes(3, 'big')
        # C1 pad 1: nonempty native per-layer settings must not be consumed as
        # the ordinary empty-list frame, silently manufacturing a default ring.
        data[921:924] = (1000001).to_bytes(3, 'big')
        parsed = DipTraceParser()
        with self.assertRaisesRegex(DipTraceParseError, 'plane pad settings'):
            parsed.parse_bytes(data)
        self.assertEqual(parsed.layers, {})
        self.assertEqual(parsed.drill_groups, {})

    def test_genuine_v54_and_v60_rotation_boards_preserve_pad_and_hole_locations(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import rotate

        # Authored, independently saved DipTrace boards, not changed version headers.
        for filename, version, placements in (
                ('rotate4.dip', 54, ((-16.6, 17.7, 0), (20.1, 15.8, math.pi / 2),
                                     (-16.1, -8.2, 0.7854), (15.1, -7.1, 5.7239))),
                ('rotate.dip', 60, ((-8.6, 22.85, 0), (-8.35, 7.5, 0.7854),
                                    (9.25, 17.4, math.pi / 2), (9.75, 9.4, 5.3988)))):
            with self.subTest(version=version):
                parsed = DipTraceParser(circle_steps=128).parse_file(self.FIXTURE.parent / filename)
                self.assertEqual(parsed.version, version)
                drills = [p for g in parsed.drill_groups.values() for t in g.values() for p in t['drills']]
                self.assertEqual(len(drills), 8)
                expected = []
                openings = []
                for x, y, radians in placements:
                    angle = math.degrees(radians)
                    for offset, pad in ((1.27, Point(1.27, 0).buffer(0.8, quad_segs=32)),
                                        (-1.27, box(-2.07, -0.8, -0.47, 0.8))):
                        center = translate(rotate(Point(offset, 0), angle, origin=(0, 0)), x, y)
                        self.assertTrue(any(p.distance(center) < 1e-8 for p in drills))
                        expected.append(translate(rotate(pad, angle, origin=(0, 0)), x, y))
                        opening = (Point(offset, 0).buffer(0.85, quad_segs=32) if offset > 0 else
                                   box(-2.12, -0.85, -0.42, 0.85))
                        openings.append(translate(rotate(opening, angle, origin=(0, 0)), x, y))
                for layer in (0, 1):
                    self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(expected)).area, 1e-8)
                for layer in ('Top Mask', 'Bottom Mask'):
                    self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(openings)).area, 1e-8)

    def test_legacy_string_layouts_preserve_outer_layer_pads_and_mechanical_holes(self):
        from appParsers.ParseDipTrace import DipTraceParser

        def number(value, size=3):
            return (value + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')

        # Independent record writer: v37 byte strings versus v39+ UTF-16 strings.
        # The unused trailing pad is numbered; unused hole coordinates aren't zero.
        for version in (37, 39, 41, 44, 45, 46):
            with self.subTest(version=version):
                def string(text):
                    return (number(len(text)) + text.encode('ascii') if version == 37 else
                            len(text).to_bytes(2, 'big') + text.encode('utf-16be'))

                def point(x, y):
                    return number(round(x * 30000), 4) + number(round(-y * 30000), 4)

                def pad(identity):
                    # Legacy pad records use the same inherited-offset sentinel
                    # as v54. Explicit zero isn't inheritance (Re-ARM v41).
                    offset = -10000000 if version >= 45 else 0
                    mask = (b'\0' + number(0) * 3 + b'\0' * 5
                            + number(offset, 4) * 2 + b'\0')
                    return (b'\0' + number(identity) + number(-1) + point(2, 0) + string('1') + string('Pad')
                            + b''.join(number(n, 4) for n in (60000, 30000, 0, 0))
                            + number(0) + b'\1' + number(2) + b'\0' + number(0) + mask)

                header = (DipTraceParser.MAGIC + number(version) + b'\0' * 10 + string('') + b'\0' * 17
                          + number(4) + b''.join(point(x, y) + b'\0' for x, y in
                                                ((0, 0), (30, 0), (30, 30), (0, 30))) + b'\0' * 35)
                layers = number(4) + b''.join(b'\1' + number(i) + b'\0' * 3 + string(name)
                                               + number(0) + b'\0' * 11 for i, name in
                                               enumerate(('Top', 'Inner1', 'Inner2', 'Bottom')))
                via = (number(1) + string('Default') + b'\0' + number(60000, 4)
                       + number(30000, 4) + number(0) + number(3))
                placement = (b'\1\0' + number(0) * 3 + number(1) + number(-1) + number(0) + number(1)
                             + b'\0' * 29 + b'\0' + number(-1) + b'\0' + number(-1) + point(0, 0) + b'\0\0')
                component = (DipTraceParser.BOUNDARY + b'\0' + string('Library') + number(0) * 2
                             + b'\0\1\0\0' + point(10, 20) + b'\0' * 9 + number(0) * 4
                             + string('Pattern') + number(10000, 4) * 2 + b'\0' * 19 + number(0)
                             + string('Component') + string('J1') + string('') * 2)
                geometry = (number(3) + pad(0) + pad(1) + pad(7) + number(0)
                            + number(3) + b'\0' + point(4, 5) + number(0, 4) * 2
                            + b'\0\1' + point(-2, 0) + number(60000, 4) + number(30000, 4)
                            + b'\0' + point(0, 0) + number(0, 4) * 2)
                sheet = (number(0) + b'\0' * 24 + (string('') + b'\0' * 8 + number(0) * 3) * 6 + b'\0')
                settings = (number(4) * 2 + number(0) + string('Arial') + number(10) + b'\1'
                            + number(10000, 4) * 3 + number(0) * 2)
                if version >= 45:
                    # Frame observed in genuine z80_board v45 and logic_probe
                    # v46: global swell/shrink precede the dimension record.
                    settings += number(3000, 4) + number(1500, 4) + b'\0\0\1' + number(0, 4) * 4
                data = header + layers + via + placement + component + geometry + number(0) + sheet + settings
                parsed = DipTraceParser(circle_steps=64).parse_bytes(data)
                self.assertEqual(parsed.version, version)
                self.assertEqual(parsed.board_outline.bounds, (0, 0, 30, 30))
                self.assertNotIn(1, parsed.layers)
                self.assertLess(unary_union(parsed.layers[3]['solid_geometry']).symmetric_difference(
                    box(9.5, 21, 10.5, 23)).area, 1e-8)
                swell, shrink = (0.1, 0.05) if version >= 45 else (0, 0)
                # SJTwo v45's published mask Gerbers expose mechanical holes
                # at drill diameter, without the inherited pad-mask swell.
                opening = Point(10, 18).buffer(0.5)
                self.assertLess(unary_union(parsed.layers['Bottom Mask']['solid_geometry']).symmetric_difference(
                    unary_union([box(9.5 - swell, 21 - swell, 10.5 + swell, 23 + swell), opening])).area, 1e-8)
                self.assertLess(unary_union(parsed.layers['Bottom Paste']['solid_geometry']).symmetric_difference(
                    box(9.5 + shrink, 21 + shrink, 10.5 - shrink, 23 - shrink)).area, 1e-8)
                self.assertLess(unary_union(parsed.layers['Top Mask']['solid_geometry']).symmetric_difference(
                    opening).area, 1e-8)
                self.assertNotIn('Top Paste', parsed.layers)
                hole = next(iter(parsed.drill_groups['NPTH'].values()))
                self.assertEqual(hole['tooldia'], 1)
                self.assertLess(hole['drills'][0].distance(Point(10, 18)), 1e-8)

    def test_native_degenerate_graphics_keep_native_area(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        # Genuine v46 logic-probe board: Top Mask outline at these two points.
        points = [(-49.53, 16.51), (-49.53, 13.97)]
        parsed = DipTraceParser(circle_steps=128)
        solid, follow = parsed._graphic(3, points, 1 / 30)
        self.assertLess(solid.symmetric_difference(LineString(points).buffer(1 / 60, quad_segs=32)).area, 1e-8)
        self.assertLess(follow.hausdorff_distance(LineString(points)), 1e-8)
        # A collapsed outline rectangle is a stroked segment (psu-switch v50 @32397).
        seg = [(-85.3725, 5.2888), (-69.8725, 5.2888)]
        solid, _ = parsed._graphic(2, seg, 0.1524)
        self.assertLess(solid.symmetric_difference(LineString(seg).buffer(0.0762, quad_segs=32)).area, 1e-6)
        # A zero-size outline is a round dot of its width, like a zero-length line (Motus v46).
        dot = [(-22.4744, -8.572), (-22.4744, -8.572)]
        solid, _ = parsed._graphic(2, dot, 0.1524)
        self.assertLess(solid.symmetric_difference(Point(dot[0]).buffer(0.0762, quad_segs=32)).area, 1e-6)
        # A zero-area fill has no stroke: nothing drawn, one warning (m2c v1 J3 @43062).
        solid, _ = parsed._graphic(4, [(-1.27, 0.0), (-1.27, -1.27)], 0.1524)
        self.assertTrue(solid.is_empty)
        self.assertTrue(parsed.warnings)
        # A polygon of 36 copies of one point has no area either (Motus v46 @1432863).
        solid, _ = parsed._graphic(9, [(392.8, 21.95)] * 36, 0.001)
        self.assertTrue(solid.is_empty)
        # A ring that only touches itself keeps its hole; a crossing contour still fails.
        key = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 5), (3, 5), (3, 3), (6, 3), (6, 6), (3, 6), (3, 5), (0, 5)]
        solid, _ = parsed._graphic(9, key, 0.1524)
        self.assertAlmostEqual(solid.area, 91.0, places=6)
        with self.assertRaises(DipTraceParseError):
            parsed._graphic(9, [(0, 0), (2, 2), (2, 0), (0, 2)], 0.1524)

    def test_native_corpus_newer_versions_and_retraced_contours_import(self):
        import os
        from appParsers.ParseDipTrace import DipTraceParser

        root = os.environ.get('DIPTRACE_V38_EXAMPLES')
        if not root:
            self.skipTest('Set DIPTRACE_V38_EXAMPLES to the DipTrace corpus')
        interstellar = 'corpus-fedulovivan--interstellar--'
        valves = interstellar + 'valves-manipulator--'
        boards = (('corpus-SorokinRed--DipTrace--Projects--Макетная плата ESP-12-F--ESP_training_board.dip', 44),
                  (interstellar + 'ir-remote-for-lg-display--pcb-draft.dip', 50),
                  (interstellar + 'psu-switch--pcb.dip', 50),
                  (valves + 'pcb-prev-version--valves-manipulator.dip', 50),
                  (valves + 'jlcpcb-order-23-december-2021--valves-manipulator.dip', 53),
                  (valves + 'schematics--valves-manipulator.dip', 53),
                  ('corpus-dimpleprajapat7568-create--capacitive-touchpad-pcb--TouchPad_Layout.dip', 58),
                  ('corpus-and3rson--deck65--diptrace--main.dip', 54),
                  ('corpus-and3rson--deck65--diptrace--main_top.dip', 54),
                  ('corpus-chajajonee--TheRelativityCo-m2C-PCB--m2c_PCB_v1.dip', 37),
                  ('corpus-chajajonee--TheRelativityCo-m2C-PCB--m2c_PCB_v1_2.dip', 37),
                  ('corpus-chajajonee--TheRelativityCo-m2C-PCB--m2c_PCB_v1_3.dip', 37),
                  ('corpus-machadoleonardo94--HabitatControl-hardware--Habitat_V4-5ch--layout.dip', 54),
                  ('specimens_teardrops/PCB_4.dip', 54),
                  ('specimens_teardrops/paired_KytorIndustries--bluestoothilizer--pcb--PCB_0003-v1.2.dip', 54),
                  ('specimens_teardrops/paired_ACE1046--LazyRolls--Board--Nema17--LazyRolls_v1.2_nema17.dip', 50),
                  ('specimens_teardrops/Differential_Pairs.dip', 49))
        for name, version in boards:
            with self.subTest(board=name):
                if not (Path(root) / name).exists():
                    self.skipTest('%s is not in DIPTRACE_V38_EXAMPLES' % name)
                parsed = DipTraceParser(circle_steps=128).parse_file(Path(root) / name)
                self.assertEqual(parsed.version, version)
                self.assertTrue(parsed.layers)
                drills = sum(len(t['drills']) + len(t['slots']) for g in parsed.drill_groups.values()
                             for t in g.values())
                area = lambda layer: unary_union(parsed.layers[layer]['solid_geometry']).area
                if name.endswith('main.dip'):
                    # Logo: one ring with 10 holes joined by zero-width cut-ins
                    # (@758613). KiCad TopSilk.gbr of the same commit: 52.4901 mm².
                    silk = unary_union(parsed.layers['Top Silk']['solid_geometry'])
                    self.assertAlmostEqual(silk.intersection(box(2.41, 32.22, 13.21, 50.22)).area, 52.49, delta=0.01)
                elif name.endswith('v1_2.dip'):
                    # The closing edge retraces a corner by 1.27 mm: 139.7 x 127 mm.
                    self.assertAlmostEqual(parsed.board_outline.area, 17741.9, delta=0.01)
                    self.assertTrue([w for w in parsed.warnings if 'outline' in w])
                elif 'jlcpcb' in name:
                    # valves-manipulator_gerber.zip: Top 1366.7534 mm², 85 drills.
                    self.assertAlmostEqual(area(0), 1366.8, delta=0.1)
                    self.assertEqual(drills, 85)
                elif version == 58:
                    # TouchPad_Gerber.zip: Bottom 98.1186 mm², 12 drills.
                    self.assertAlmostEqual(area(max(k for k in parsed.layers if isinstance(k, int))),
                                           98.12, delta=0.01)
                    self.assertEqual(drills, 12)
                elif 'Habitat_V4-5ch' in name:
                    # habitat-v4-5ch-cam: Through.drl 339 holes. Route-point via defaults
                    # away from a layer change or a joined two-layer end are no vias.
                    self.assertEqual(drills, 339)
                    self.assertAlmostEqual(area(1), 7984.82, delta=0.01)
                elif name.endswith('PCB_4.dip'):
                    # Mask frame flags 00 01 01 @178968. Same copper and drills as the official v38 save.
                    self.assertAlmostEqual(area(0), 1512.54, delta=0.01)
                    self.assertAlmostEqual(area(1), 997.61, delta=0.01)
                    self.assertEqual(drills, 254)
                    self.assertTrue(parsed.layers['Top Mask']['solid_geometry'])
                elif 'bluestoothilizer' in name:
                    # PCB_0003-v1.2_gerber.zip: Top 200.033 mm², 28 drills; U2 Vert -1 isn't drawn.
                    self.assertAlmostEqual(area(0), 200.05, delta=0.03)
                    self.assertEqual(drills, 28)
                    self.assertTrue([w for w in parsed.warnings if 'unset text alignment' in w])
                elif 'nema17' in name:
                    # Silk text "VDD" after the last placement (272938) is no designator.
                    # LazyRolls_v1.2_nema17_gerber.zip (da74c017): 52 holes, 37 x 0.9 mm PTH.
                    self.assertEqual(drills, 52)
                    pth = [t for t in parsed.drill_groups['PTH'].values() if abs(t['tooldia'] - 0.9) < 1e-6]
                    self.assertEqual(len(pth[0]['drills']), 37)
                elif name.endswith('Differential_Pairs.dip'):
                    # Same design as the v41 save: 296 drills, 108 nets, the inner planes.
                    self.assertEqual(drills, 296)
                    self.assertEqual(len(parsed.nets), 108)
                    self.assertAlmostEqual(area(1), 7396.13, delta=0.01)

    def test_native_large_graphics_table_preserves_all_board_lines(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        point = lambda x, y: number(round(x * 30000), 4) + number(round(-y * 30000), 4)
        # SJTwo main-revE v45 has 194 graphics at bytes 257353..277534.
        # That count encodes as 0f4302, not the formerly assumed 0f42 prefix.
        records = []
        for index in range(194):
            records.append(b'\1\0' + number(0) + b'\0'
                           + number(1) + number(0) * 2 + number(10) + number(0) + number(-1)
                           + b'\0' * 9 + number(3000, 4) + number(2)
                           + point(index * 2, 20) + point(index * 2 + 1, 20)
                           + b'\0' * 4 + b'\1' + number(-1) + b'\0'
                           + number(-20000, 4) + number(10000, 4) + number(index) + b'\0')
        # Reuse the genuine GPSDO counted group/title-block footer. Unrouted
        # net bytes can precede the graphics, so the start isn't a fixed offset.
        footer = self.FIXTURE.read_bytes()[92726:92882]
        data = b'\xff' * 11 + number(len(records)) + b''.join(records) + footer
        parsed = DipTraceParser(circle_steps=64)
        graphics = parsed._board_graphics_table(data, 0, len(data))
        parsed._draw_board_graphics(graphics)
        lines = [LineString([(index * 2, 20), (index * 2 + 1, 20)]) for index in range(194)]
        expected = unary_union([line.buffer(0.05) for line in lines])
        actual = unary_union(parsed._solids['Top Silk'])
        self.assertLess(actual.symmetric_difference(expected).area, 1e-8)
        with self.assertRaises(DipTraceParseError):
            parsed._board_graphics_table(data[:-1], 0, len(data) - 1)

    def test_genuine_v41_four_layer_board_keeps_numbered_tail_pads_and_holes(self):
        from appParsers.ParseDipTrace import DipTraceParser

        parsed = DipTraceParser().parse_file(self.FIXTURE.parent / 're-arm_pub.dip')
        self.assertEqual(parsed.version, 41)
        self.assertEqual([parsed.layers[i]['name'] for i in range(4)], ['Top', 'Layer2', 'Layer3', 'Bottom'])
        drills = [(t['tooldia'], p) for t in parsed.drill_groups['PTH'].values() for p in t['drills']]
        # 146 footprint holes and 126 routed vias. Automatic via defaults
        # at another 46 ordinary track points aren't additional drills.
        self.assertEqual(len(drills), 272)
        # JD1's local +/-1.27 mm pads turn 180 degrees. JP1 turns 90 degrees.
        # Their unused tail records retain id 7, not count-1. Neither is copper.
        for point in (Point(-2.863, 15.052), Point(-5.403, 15.052),
                      Point(-23.183, 54.712), Point(-23.183, 57.252), Point(-23.183, 59.792)):
            self.assertTrue(any(abs(size - 0.889) < 1e-8 and p.distance(point) < 1e-8 for size, p in drills))
            for layer in range(4):
                self.assertTrue(unary_union(parsed.layers[layer]['solid_geometry']).contains(point))
        holes = [(t['tooldia'], p) for t in parsed.drill_groups['NPTH'].values() for p in t['drills']]
        self.assertEqual(len(holes), 9)
        self.assertTrue(any(abs(size - 3.175) < 1e-8 and p.distance(Point(-24.8464666667, 49.2448333333)) < 1e-8
                            for size, p in holes))
        # C7 carries native, explicit zero swell/shrink, not the inheritance
        # sentinel used by most other pads. Its two pads are rotated locally.
        expected = box(15.4566, 34.9792666667, 16.2186, 35.9444666667)
        for layer in ('Top Mask', 'Top Paste'):
            self.assertLess(expected.difference(unary_union(parsed.layers[layer]['solid_geometry'])).area, 1e-8)

    def test_native_via_styles_limit_copper_and_keep_distinct_drill_spans(self):
        from appParsers.ParseDipTrace import DipTraceParser

        def number(value, size=3):
            return (value + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')

        # Native v54 style framing from the GPSDO board; XML documents Size,
        # HoleSize and Lay1/Lay2. Deliberately use non-monotonic layer ids.
        default = self.FIXTURE.read_bytes()[435:466]
        styles = number(3) + default
        for first, last in ((0, 7), (7, 3)):
            styles += default[:-6] + number(first) + number(last)
        parsed = DipTraceParser(circle_steps=128)
        parsed._copper_layers = (0, 7, 3, 1)
        parsed._via_styles(styles, 0, len(styles))
        for style, xy, layers in ((0, (100, 100), (0, 7, 3, 1)),
                                  (1, (110, 100), (0, 7)), (2, (120, 100), (7, 3))):
            with self.subTest(style=style):
                span = number(parsed._copper_layers.index(layers[0])) + number(parsed._copper_layers.index(layers[-1]))

                def node(dx, dy, layer, via_style):
                    # Cached node dimensions intentionally disagree with the strict
                    # style. Style dimensions, not stale caches, define the via.
                    return (number(round((xy[0] + dx) * 30000), 4) + number(round(-(xy[1] + dy) * 30000), 4)
                            + number(layer) + number(0) + number(3000, 4) + number(60000, 4)
                            + b'\0' + number(0) + b'\0' + number(via_style)
                            + number(30000, 4) + span + b'\0')
                # The route changes layer at the via; its 0.1 mm traces end inside the pad.
                route = (parsed.CHAIN + number(0) + number(3) + node(-0.3, 0, layers[0], -1)
                         + node(0, 0, layers[0], style) + node(0, 0.3, layers[-1], -1))
                parsed._routing(route, 0, len(route))
                for layer in parsed._copper_layers:
                    solid = unary_union(parsed._solids.get(layer, []))
                    self.assertEqual(solid.contains(Point(xy)), layer in layers)
                    if layer in layers:
                        local = solid.intersection(Point(xy).buffer(2))
                        self.assertAlmostEqual(local.bounds[2] - local.bounds[0], 1.016, places=6)
                group = 'PTH' if style == 0 else 'PTH_%d-%d' % (layers[0], layers[-1])
                tool = next(iter(parsed._drills[group].values()))
                self.assertAlmostEqual(tool['tooldia'], 0.483)
                self.assertEqual(tool['drills'], [Point(xy)])

    def test_native_automatic_via_settings_do_not_drill_ordinary_track_points(self):
        from appParsers.ParseDipTrace import DipTraceParser

        def number(value, size=3):
            return (value + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')

        parsed = DipTraceParser(circle_steps=64)
        styles = number(1) + self.FIXTURE.read_bytes()[435:466]
        parsed._via_styles(styles, 0, len(styles))

        def route(points, first_join=False):
            # SJTwo v45 retains these automatic via settings at every point.
            # The two flags before CHAIN distinguish trace and pad endpoints.
            data = number(0) * 6 + bytes((first_join, 0)) + parsed.CHAIN + number(0) + number(len(points))
            for x, y, layer in points:
                data += (number(x * 30000, 4) + number(-y * 30000, 4) + number(layer)
                         + number(0) + number(6000, 4) + number(18000, 4)
                         + b'\1' + number(0) + b'\0' + number(0) + number(9000, 4)
                         + number(0) + number(1) + b'\0')
            return data

        # A connected branch changes layer at its first point. A coincident
        # pad endpoint and an interior crossing don't create extra vias.
        data = route(((102, 100, 0), (102, 102, 0)), first_join=True)
        # Its parent appears later. The parent's first Lay is stale; segment
        # properties belong to the second point, not the first point's cache.
        data += route(((100, 100, 1), (101, 100, 0), (102, 100, 1), (103, 100, 1)))
        data += route(((103, 100, 0), (104, 100, 0)))
        data += route(((99, 99, 1), (100, 100, 1), (99, 101, 1)))
        parsed._routing(data, 0, len(data))
        tools = [tool for group in parsed._drills.values() for tool in group.values()]
        holes = [point for tool in tools for point in tool['drills']]
        self.assertCountEqual(holes, [Point(101, 100), Point(102, 100)])
        self.assertTrue(all(abs(tool['tooldia'] - 0.483) < 1e-8 for tool in tools))
        for layer in (0, 1):
            copper = unary_union(parsed._solids[layer])
            self.assertFalse(copper.covers(Point(100.25, 99.7)))
            self.assertTrue(copper.covers(Point(101, 100.4)))

    def test_native_trace_state_does_not_remove_saved_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        parsed = DipTraceParser(circle_steps=64)
        # SJTwo v45, byte 249832: 01 00 00 precedes the trace sentinel.
        # Its short copper stub is present in the Rev.1.E.2 Top Gerber.
        data = b'\1\0\0' + parsed.CHAIN[3:] + number(0) + number(3)
        for x, y, width in ((0, 0, 3000), (1, 0, 6000), (1, 1, 12000)):
            data += (number(x * 30000, 4) + number(-y * 30000, 4) + number(0) * 2
                     + number(width, 4) + number(0, 4) + b'\0' + number(0) + b'\0' + number(-1)
                     + number(0, 4) + number(0) * 2 + b'\0')
        parsed._routing(data, 0, len(data))
        expected = unary_union([LineString([(0, 0), (1, 0)]).buffer(0.1),
                                LineString([(1, 0), (1, 1)]).buffer(0.2)])
        self.assertLess(unary_union(parsed._solids.get(0, [])).symmetric_difference(expected).area, 1e-8)
        self.assertFalse(parsed._drills)

    def test_native_via_styles_reject_reversed_or_missing_layers(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = self.FIXTURE.read_bytes()
        for endpoints in ((1, 0), (0, 9)):
            with self.subTest(endpoints=endpoints):
                changed = data[:460] + b''.join((1000000 + v).to_bytes(3, 'big')
                                               for v in endpoints) + data[466:]
                with self.assertRaises(DipTraceParseError):
                    DipTraceParser().parse_bytes(changed)

    def test_legacy_v40_native_placements_and_drilling_preserve_board_geometry(self):
        import tempfile
        from appParsers.ParseDipTrace import DipTraceParser
        from appParsers.ParseGerber import Gerber
        from appParsers.ParseExcellon import Excellon
        from shapely.affinity import scale
        from shapely.geometry import box
        from test_sprint_layout_e2e import _app

        fixture = self.FIXTURE.parent / 'DAP42v2.dip'
        app = _app('MM')
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / 'legacy.dip'
            native.write_bytes(fixture.read_bytes())
            parsed = DipTraceParser(app, circle_steps=128).parse_file(native)
        self.assertEqual(parsed.version, 40)
        outline = Gerber(app, steps_per_circle=128)
        outline.parse_file(str(fixture.parent / 'gerber/DAP42v2.BoardOutline.gbr'))
        bounds = scale(unary_union(outline.follow_geometry), 25.4, 25.4, origin=(0, 0)).bounds
        dx, dy = bounds[0] - parsed.board_outline.bounds[0], bounds[1] - parsed.board_outline.bounds[1]
        # The author's CAM files differ from native via/trace records and contain a
        # malformed custom-pad macro. Compare the unchanged lower board region,
        # outside that macro's flash, rather than asserting a false whole-board match.
        stable = box(-19.05, 0, 19.05, 6.35).difference(box(-16.6, 1.2, -13.8, 3.9))
        for layer, suffix in ((0, 'Top'), (1, 'Bottom')):
            reference = Gerber(app, steps_per_circle=128)
            reference.parse_file(str(fixture.parent / ('gerber/DAP42v2.%s.gbr' % suffix)))
            expected = scale(unary_union(reference.solid_geometry), 25.4, 25.4, origin=(0, 0))
            actual = translate(unary_union(parsed.layers[layer]['solid_geometry']), dx, dy)
            expected, actual = expected.intersection(stable), actual.intersection(stable)
            self.assertLess(actual.symmetric_difference(expected).area / expected.area, 0.00005, suffix)
        copper = unary_union(parsed.layers[0]['solid_geometry'])
        for y in (15.875, 17.575):
            self.assertLess(box(16.645, y - 0.55, 17.645, y + 0.55).difference(copper).area, 1e-8)
        reference = Excellon(app)
        reference.parse_file(fixture.parent / 'gerber/DAP42v2.Through.drl')
        actual = [(t['tooldia'], translate(p, dx, dy)) for g in parsed.drill_groups.values()
                  for t in g.values() for p in t['drills']]
        expected = [(t['tooldia'] * 25.4, scale(p, 25.4, 25.4, origin=(0, 0)))
                    for t in reference.tools.values() for p in t['drills']]
        # Style 0 on route points without a layer change is no via (byte 38050:
        # (+1.5875,18.415), (-8.89,23.8125)). One native via at a layer change
        # isn't in the author's CAM.
        expected.append((0.508, Point(-10.59, 10.16)))
        self.assertEqual(len(actual), len(expected))
        for diameter, point in expected:
            self.assertTrue(any(abs(diameter - size) < 0.002 and point.distance(at) < 0.003
                                for size, at in actual))

    def test_legacy_v40_custom_openings_do_not_invent_inherited_offsets(self):
        from appParsers.ParseDipTrace import DipTraceParser

        original = (self.FIXTURE.parent / 'DAP42v2.dip').read_bytes()
        # C1 pad 1: mask states at 1054/1056, swell/shrink at 1058/1062.
        # Its pad orientation and component each turn 90 degrees. Native center
        # is (17.145, 15.875); the author CAM isn't used to set any parameter.
        parser = DipTraceParser()
        for mask_mode, paste_mode, swell, shrink, offset in (
                (0, 0, 3600, -10000000, 0.12),
                (3, 1, -10000000, 2400, -0.08),
                (2, 2, 0, 0, None)):
            with self.subTest(mask_mode=mask_mode):
                data = bytearray(original)
                data[1054], data[1056] = mask_mode, paste_mode
                data[1058:1066] = b''.join((1000000000 + n).to_bytes(4, 'big') for n in (swell, shrink))
                parsed = parser.parse_bytes(data)
                if offset is None:
                    self.assertNotIn('Top Mask', parsed.layers)
                else:
                    expected = box(16.645 - offset, 15.325 - offset, 17.645 + offset, 16.425 + offset)
                    actual = unary_union(parsed.layers['Top Mask']['solid_geometry'])
                    self.assertLess(actual.symmetric_difference(expected).area, 1e-8)
                    if paste_mode == 1:
                        self.assertLess(unary_union(parsed.layers['Top Paste']['solid_geometry'])
                                        .symmetric_difference(expected).area, 1e-8)
                if paste_mode != 1:
                    self.assertNotIn('Top Paste', parsed.layers)
        # Reusing a parser must not leak the previous explicit offsets. All
        # original DAP pads inherit unresolved defaults, so none are invented.
        parsed = parser.parse_bytes(original)
        self.assertNotIn('Top Mask', parsed.layers)
        self.assertNotIn('Top Paste', parsed.layers)

    def test_legacy_openings_accept_source_export_settings_without_overriding_native_values(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        original = (self.FIXTURE.parent / 'DAP42v2.dip').read_bytes()
        parsed = DipTraceParser(opening_defaults={'mask': 0.1, 'paste': 0.05}).parse_bytes(original)
        region = box(16.4, 15.1, 17.9, 16.6)
        for layer, offset in (('Top Mask', 0.1), ('Top Paste', -0.05)):
            expected = box(16.645 - offset, 15.325 - offset, 17.645 + offset, 16.425 + offset)
            actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(region)
            self.assertLess(actual.symmetric_difference(expected).area, 1e-8)
        # Explicit zero in a pad wins over supplied globals, just as recorded
        # modern project defaults win over the fallback export settings.
        data = bytearray(original)
        data[1058:1066] = (1000000000).to_bytes(4, 'big') * 2
        parsed.parse_bytes(data)
        self.assertLess(unary_union(parsed.layers['Top Mask']['solid_geometry']).intersection(region)
                        .symmetric_difference(box(16.645, 15.325, 17.645, 16.425)).area, 1e-8)
        native = DipTraceParser().parse_file(self.FIXTURE)
        parsed.parse_file(self.FIXTURE)
        self.assertLess(unary_union(parsed.layers['Top Mask']['solid_geometry']).symmetric_difference(
            unary_union(native.layers['Top Mask']['solid_geometry'])).area, 1e-8)
        expanded = DipTraceParser(opening_defaults={'mask': -0.05, 'paste': -0.05}).parse_bytes(original)
        for layer, offset in (('Top Mask', -0.05), ('Top Paste', 0.05)):
            expected = box(16.645 - offset, 15.325 - offset, 17.645 + offset, 16.425 + offset)
            self.assertLess(unary_union(expanded.layers[layer]['solid_geometry']).intersection(region)
                            .symmetric_difference(expected).area, 1e-8)
        for settings in ({'mask': float('nan')}, {'mask': float('inf')}, {'paste': -10}, {'vias': 'closed'}):
            with self.subTest(settings=settings), self.assertRaises(DipTraceParseError):
                DipTraceParser(opening_defaults=settings)

    def test_via_mask_choice_tents_or_opens_common_and_routed_vias(self):
        from appParsers.ParseDipTrace import DipTraceParser

        # DAP42 v40: 13 Common static vias and 19 routed vias, all 1.016 / 0.508 mm.
        # Via tenting is a Gerber export setting; the board doesn't store it.
        original = (self.FIXTURE.parent / 'DAP42v2.dip').read_bytes()

        def openings(data, **choice):
            parsed = DipTraceParser(opening_defaults={'mask': 0.1, 'paste': 0, **choice}).parse_bytes(data)
            vias = [d for g in parsed.drill_groups.values() for t in g.values()
                    if abs(t['tooldia'] - 0.508) < 1e-6 for d in t['drills']]
            masks = [unary_union(parsed.layers[n]['solid_geometry']) if n in parsed.layers else Polygon()
                     for n in ('Top Mask', 'Bottom Mask')]
            # A via can partly overlap a pad opening, so test full coverage.
            return parsed, vias, [[v for v in vias if v.buffer(0.6).difference(m).area < 1e-6] for m in masks]

        parsed, vias, (top, bottom) = openings(original)
        self.assertEqual(len(vias), 32)
        self.assertIn('vias', parsed.missing_opening_defaults)
        # Only the routed via inside a top surface-pad opening stays uncovered.
        self.assertEqual(([v.coords[0] for v in top], bottom), ([(-10.59, 10.16)], []))
        _, vias, (top, bottom) = openings(original, vias='open')
        self.assertEqual((len(top), len(bottom)), (32, 32))
        # Explicit per-via Open (StaticVia1 mask states at 25915/25916) wins over tenting.
        data = bytearray(original)
        data[25915] = data[25916] = 1
        _, vias, (top, bottom) = openings(bytes(data), vias='tented')
        self.assertEqual((len(top), len(bottom)), (2, 1))
        self.assertIn(bottom[0], top)

    def test_native_negative_opening_offsets_shrink_mask_and_expand_paste(self):
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n: (n + 1000000000).to_bytes(4, 'big')
        for inherited in (False, True):
            with self.subTest(inherited=inherited):
                data = bytearray(self.FIXTURE.read_bytes())
                data[32414:32422] = number(7500000) + number(-7500000)
                data[32591:32599] = number(60000) + number(30000)
                data[32611:32614] = (1000002).to_bytes(3, 'big')
                data[381970:381982] = (1000001).to_bytes(3, 'big') * 4
                offsets = number(-3000) + number(-1500)
                data[32633:32641] = number(-10000000) * 2 if inherited else offsets
                data[380800:380808] = offsets
                parsed = DipTraceParser().parse_bytes(data)
                for layer, offset in ((0, 0), ('Top Mask', -0.1), ('Top Paste', 0.05)):
                    expected = box(249 - offset, 249.5 - offset, 251 + offset, 250.5 + offset)
                    actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(248, 248, 252, 252))
                    self.assertLess(actual.symmetric_difference(expected).area, 1e-8)

    def test_native_board_without_companion_files_matches_manufacturing_geometry(self):
        import tempfile
        from appParsers.ParseDipTrace import DipTraceParser
        from appParsers.ParseGerber import Gerber
        from appParsers.ParseExcellon import Excellon
        from shapely.affinity import scale
        from test_sprint_layout_e2e import _app

        app = _app('MM')
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / 'board.dip'
            native.write_bytes(self.FIXTURE.read_bytes())
            parsed = DipTraceParser(app=app, circle_steps=128).parse_file(native)
        outline = parsed.board_outline
        self.assertTrue(outline.is_valid)
        self.assertAlmostEqual(outline.bounds[2] - outline.bounds[0], 100, places=4)
        self.assertAlmostEqual(outline.bounds[3] - outline.bounds[1], 70.65, places=4)
        # Align the CAM export's margin with the native file frame, using the outline centerline.
        reference_outline = Gerber(app, steps_per_circle=128)
        reference_outline.parse_file(str(self.FIXTURE.parent / 'gerber' / 'GPSDO_simple.GKO'))
        bounds = scale(unary_union(reference_outline.follow_geometry), 25.4, 25.4, origin=(0, 0)).bounds
        dx, dy = bounds[0] - outline.bounds[0], bounds[1] - outline.bounds[1]
        for layer, extension in ((0, 'GTL'), (1, 'GBL'), ('Top Mask', 'GTS'), ('Bottom Mask', 'GBS')):
            reference = Gerber(app, steps_per_circle=128)
            reference.parse_file(str(self.FIXTURE.parent / 'gerber' / ('GPSDO_simple.' + extension)))
            expected = scale(unary_union(reference.solid_geometry), 25.4, 25.4, origin=(0, 0))
            actual = translate(unary_union(parsed.layers[layer]['solid_geometry']), dx, dy)
            # Mask openings are smaller than merged pours, so CAM coordinate rounding
            # contributes a larger relative error to their aggregate boundary area.
            tolerance = 0.0001 if extension in ('GTS', 'GBS') else 0.00002
            self.assertLess(actual.symmetric_difference(expected).area / expected.area, tolerance, extension)
            self.assertTrue(all(p.is_valid for p in parsed.layers[layer]['solid_geometry']))
        reference = Excellon(app)
        reference.parse_file(self.FIXTURE.parent / 'gerber' / 'Through.drl')
        tools = [tool for group in parsed.drill_groups.values() for tool in group.values()]
        expected_drills = [(t['tooldia'] * 25.4, Point(p.x * 25.4, p.y * 25.4))
                           for t in reference.tools.values() for p in t['drills']]
        actual_drills = [(t['tooldia'], translate(p, dx, dy)) for t in tools for p in t['drills']]
        self.assertEqual(len(actual_drills), len(expected_drills))
        for diameter, point in expected_drills:
            self.assertTrue(any(abs(diameter - size) < 0.002 and point.distance(at) < 0.003
                                for size, at in actual_drills), (diameter, point))
        actual_slots = [(t['tooldia'], translate(LineString([a, b]), dx, dy))
                        for t in tools for a, b in t['slots']]
        expected_slots = [(t['tooldia'] * 25.4,
                           scale(LineString([a, b]), 25.4, 25.4, origin=(0, 0)))
                          for t in reference.tools.values() for a, b in t['slots']]
        self.assertEqual(len(actual_slots), 3)
        for diameter, slot in expected_slots:
            self.assertTrue(any(abs(diameter - size) < 0.002 and slot.hausdorff_distance(at) < 0.003
                                for size, at in actual_slots))

    def test_native_copper_cache_paths_retain_open_and_closed_strokes(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError, _Reader

        data = self.FIXTURE.read_bytes()
        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        # SJTwo v45 has a saved two-point boundary at byte 345563. These
        # boundaries are stroked paths, not filled polygons. Its published
        # bottom Gerber also confirms open paths mustn't acquire closing chords.
        # Replace GPSDO's first cached fill with isolated cache strokes.
        width = _Reader(data, 92928).number(4) / 30000
        for points in (((250, 250), (251, 250)),
                       ((250, 250), (251, 250), (251, 251)),
                       ((250, 250), (251, 250), (251, 251), (250, 250))):
            with self.subTest(points=points):
                encoded = b''.join(number(round(x * 30000), 4) + number(round(-y * 30000), 4)
                                   for x, y in points)
                boundary = number(0) + number(1) + number(len(points)) + encoded + number(0)
                changed = (data[:93016] + boundary + data[155169:155183] + number(0)
                           + data[156152:156180] + data[156180:])
                parsed = DipTraceParser(circle_steps=64).parse_bytes(changed)
                expected = LineString(points).buffer(width / 2)
                actual = unary_union(parsed.layers[1]['solid_geometry']).intersection(box(248, 248, 253, 252))
                self.assertLess(actual.symmetric_difference(expected).area, 1e-8)
        # A singleton still isn't a usable boundary path.
        boundary = number(0) + number(1) + number(1) + encoded[:8] + number(0)
        changed = (data[:93016] + boundary + data[155169:155183] + number(0)
                   + data[156152:156180] + data[156180:])
        with self.assertRaises(DipTraceParseError):
            DipTraceParser().parse_bytes(changed)

    def test_native_documentation_layers_and_unpoured_boards(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # Zero pours is a valid board, unlike an incomplete saved fill.
        font = data.index(DipTraceParser.FONT_MARKER, 78000)
        unpoured = data[:font + 40] + (1000000).to_bytes(3, 'big') + data[222628:]
        parsed = DipTraceParser().parse_bytes(unpoured)
        self.assertEqual(sum(len(t['drills']) for g in parsed.drill_groups.values() for t in g.values()), 141)
        for name in ('Top Silk', 'Bottom Silk', 'Top Assembly', 'Outline', 'Top Mask', 'Bottom Mask'):
            self.assertIn(name, parsed.layers)
            self.assertTrue(all(p.is_valid for p in parsed.layers[name]['solid_geometry']))
        self.assertTrue('Cutout' not in parsed.layers)
        assembly = unary_union(parsed.layers['Top Assembly']['follow_geometry'])
        self.assertLess(assembly.distance(Point(89.5984666667, 40)), 1e-8)
        self.assertLess(unary_union(parsed.layers[0]['solid_geometry']).area, 1500)
        # Only the standalone surface pad gets paste, not the through-hole parts.
        self.assertAlmostEqual(unary_union(parsed.layers['Top Paste']['solid_geometry']).area,
                               0.196, delta=0.002)
        self.assertNotIn('Bottom Paste', parsed.layers)
        # Empty ratline/trace lists don't remove independent board artwork.
        # Keep the 26 declared nets: live pad NetIds must not dangle merely
        # because this synthetic board has not been routed yet.
        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        nets = number(26)
        for identity in range(26):
            name = ('Net %d' % identity).encode('utf-16be')
            nets += (b'\1\0' + number(0) + number(-1) * 2 + number(identity) + number(0)
                     + number(7620, 4) + number(6000, 4) + (len(name) // 2).to_bytes(2, 'big') + name
                     + number(30480, 4) + number(7995, 4) + b'\1' + b'\0' * 7 + number(0) * 3)
        unrouted = data[:77717] + number(0) + nets + data[91463:font + 40] + \
            (1000000).to_bytes(3, 'big') + data[222628:]
        parsed = DipTraceParser().parse_bytes(unrouted)
        assembly = unary_union(parsed.layers['Top Assembly']['follow_geometry'])
        self.assertLess(assembly.distance(Point(89.5984666667, 40)), 1e-8)
        self.assertIn('Bottom Silk', parsed.layers)
        # A component can be just its drawing, without physical pads. The two
        # default-pad sentinels remain, as in native mechanical-only patterns.
        padless = data[:1547] + (1000002).to_bytes(3, 'big') + data[1550:1621] + data[1993:]
        parsed = DipTraceParser().parse_bytes(padless)
        self.assertEqual(sum(len(t['slots']) for g in parsed.drill_groups.values() for t in g.values()), 0)
        self.assertFalse(unary_union(parsed.layers['Top Mask']['solid_geometry']).contains(
            Point(100.1951333333, 30.9530333333)))
        # An unfilled zone has no physical copper, but its editable boundary is
        # useful documentation. Don't refill it or enable it as CAM by default.
        tail = bytearray(data[156152:156180])
        tail[24] = 0
        empty_zone = data[:93016] + (1000000).to_bytes(3, 'big') * 2 + data[155169:155183] + \
            (1000000).to_bytes(3, 'big') + tail + data[156180:]
        parsed = DipTraceParser().parse_bytes(empty_zone)
        self.assertIn('Unfilled Copper 1', parsed.layers)
        self.assertNotIn('Unfilled Copper 1', parsed.default_layers)

    def test_native_special_pad_openings_preserve_copper_and_drills(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        center = Point(139.56513333333334, 37.30303333333333)
        # Modern four-state modes are biased integers in the settings table,
        # not the obsolete boolean fields in the primary pad record.
        for states, expected in ((b'\x02\0\x02\0', (False, False, False, False)),
                                 (b'\0\x01\0\x01', (True, True, True, True)),
                                 (b'\x03\0\x02\0', (False, False, False, False)),
                                 (b'\x03\0\x01\0', (True, False, True, False))):
            with self.subTest(states=states):
                modes = b''.join((1000000 + state).to_bytes(3, 'big') for state in states)
                changed = data[:381970] + modes + data[381982:]
                parsed = DipTraceParser().parse_bytes(changed)
                for name, opened in zip(('Top Mask', 'Bottom Mask', 'Top Paste', 'Bottom Paste'), expected):
                    solid = unary_union(parsed.layers.get(name, {}).get('solid_geometry', []))
                    self.assertEqual(solid.contains(center), opened, name)
                if states[0] == 3 and expected[0]:
                    mask = unary_union(parsed.layers['Top Mask']['solid_geometry']).intersection(center.buffer(0.3))
                    paste = unary_union(parsed.layers['Top Paste']['solid_geometry'])
                    self.assertLess(mask.symmetric_difference(paste).area, 1e-8)
                for layer in (0, 1):
                    self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)
                self.assertEqual(parsed.drill_groups, baseline.drill_groups)

    def test_native_surface_pad_side_moves_copper_and_common_openings(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = bytearray(self.FIXTURE.read_bytes())
        # Move the test pad clear of the board's existing bottom copper pour.
        center = Point(250, 250)
        data[32414:32422] = ((1000000000 + 7500000).to_bytes(4, 'big')
                             + (1000000000 - 7500000).to_bytes(4, 'big'))
        data[32641] = 1  # pad Side=Bottom, independent of the component side
        for bottom in (False, True):
            with self.subTest(component_bottom=bottom):
                data[32411] = int(bottom)
                parsed = DipTraceParser().parse_bytes(data)
                for layer, present in ((0, bottom), (1, not bottom), ('Top Mask', bottom),
                                       ('Bottom Mask', not bottom), ('Top Paste', bottom),
                                       ('Bottom Paste', not bottom)):
                    solid = unary_union(parsed.layers.get(layer, {}).get('solid_geometry', []))
                    self.assertEqual(solid.contains(center), present, layer)

    def test_native_pad_offset_and_angle_do_not_move_the_drill(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import rotate, scale, translate

        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data = bytearray(self.FIXTURE.read_bytes())
        data[32414:32422] = number(7500000) + number(-7500000)
        data[32591:32607] = number(60000) + number(30000) + number(9000) * 2
        data[32610] = 0  # through pad; offset copper must not move the hole
        data[32611:32614] = number(2, 3)
        data[381970:381982] = number(1, 3) * 4
        data[32633:32641] = number(3000) + number(1500)
        data[407716:407728] = number(6493) + number(12000) + number(-6000)
        angle = math.degrees(0.6493)
        for bottom in (False, True):
            with self.subTest(bottom=bottom):
                data[32411] = int(bottom)
                parsed = DipTraceParser().parse_bytes(data)
                for layer, margin in ((0, 0), (1, 0), ('Top Mask', 0.1),
                                      ('Bottom Mask', 0.1), ('Top Paste', -0.05), ('Bottom Paste', -0.05)):
                    expected = rotate(translate(box(-1 - margin, -0.5 - margin,
                                                    1 + margin, 0.5 + margin), 0.4, 0.2), angle, origin=(0, 0))
                    if bottom:
                        expected = scale(expected, -1, 1, origin=(0, 0))
                    expected = translate(expected, 250, 250)
                    actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(247, 247, 253, 253))
                    self.assertLess(actual.symmetric_difference(expected).area, 1e-8, layer)
                self.assertTrue(any(point.distance(Point(250, 250)) < 1e-8
                                    for tool in parsed.drill_groups['PTH'].values() for point in tool['drills']))

    def test_native_d_pad_and_terminal_preserve_silhouette_and_openings(self):
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import rotate, scale, translate

        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        original = self.FIXTURE.read_bytes()
        for width, height, angle, bottom in ((0.28, 0.85, 0, False),
                                             (0.28, 0.85, 6493, True),
                                             (0.85, 0.28, 0, False),
                                             (1.0, 0.75, 0, False)):
            with self.subTest(width=width, angle=angle, bottom=bottom):
                data = bytearray(original)
                data[32414:32422] = number(7500000) + number(-7500000)
                data[32411] = int(bottom)
                data[32591:32599] = number(round(width * 30000)) + number(round(height * 30000))
                data[32599:32607] = number(3000) * 2
                data[32610] = 0
                data[381970:381982] = number(1, 3) * 4
                data[32633:32641] = number(3000) + number(1500)
                data[407716:407728] = number(angle) + number(12000) + number(-6000)
                baseline = DipTraceParser(circle_steps=256).parse_bytes(data)
                data[32611:32614] = number(4, 3)
                terminal = (number(1, 3) + number(4, 3) + number(60000) + number(-3000)
                            + number(0) + number(6000) + number(7500) + number(0) + number(0, 3))
                data[407728:407731] = terminal
                parsed = DipTraceParser(circle_steps=256).parse_bytes(data)

                # Independent construction: clip an ellipse, adding the straight
                # skirt only for the tall native silhouette. A wide D is the
                # upper half of an ellipse with semi-axis H, flat at -H/2.
                radius = width / 2
                ry = radius if width <= height else height
                cy = height / 2 - ry
                cap = scale(Point(0, cy).buffer(1, quad_segs=64), radius, ry, origin=(0, cy))
                expected = cap.intersection(box(-radius, cy, radius, height / 2))
                if width <= height:
                    expected = expected.union(box(-radius, -height / 2, radius, cy))
                self.assertTrue(expected.covers(Point(0, -height / 2 + 0.001)))
                self.assertFalse(expected.covers(Point(radius * 0.95, height / 2 - 0.001)))

                def place(shape):
                    shape = rotate(translate(shape, 0.4, 0.2), math.degrees(angle / 10000), origin=(0, 0))
                    if bottom:
                        shape = scale(shape, -1, 1, origin=(0, 0))
                    return translate(shape, 250, 250)

                for layer, margin in ((0, 0), (1, 0), ('Top Mask', 0.1),
                                      ('Bottom Mask', 0.1), ('Top Paste', -0.05), ('Bottom Paste', -0.05)):
                    actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(247, 247, 253, 253))
                    opening = expected.buffer(margin) if margin else expected
                    self.assertLess(actual.symmetric_difference(place(opening)).area, 1e-8, layer)
                terminal_shape = Point(0, 0.025).buffer(0.1, quad_segs=64).intersection(box(-0.1, 0.025, 0.1, 0.125))
                terminal_shape = terminal_shape.union(box(-0.1, -0.125, 0.1, 0.025))
                layer = ('Bottom' if bottom else 'Top') + ' Terminals'
                actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(247, 247, 254, 253))
                self.assertLess(actual.symmetric_difference(place(translate(terminal_shape, 2, 0.1))).area, 1e-8)
                self.assertEqual(parsed.drill_groups, baseline.drill_groups)

    def test_native_rounded_pad_and_terminal_keep_corner_geometry(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data = bytearray(self.FIXTURE.read_bytes())
        data[32414:32422] = number(7500000) + number(-7500000)
        data[32591:32599] = number(60000) + number(30000)
        data[32611:32614] = number(2, 3)
        data[381970:381982] = number(1, 3) * 4
        data[32633:32641] = number(3000) + number(1500)
        # HabitatControl v4's native 206612 on a 1.21 mm pad produces the
        # independently exported 0.25 mm Gerber corner. This is a fraction
        # of the shorter pad side, scaled by 1000000, not a length.
        data[407712:407716] = number(250000)
        terminal = (number(1, 3) + number(2, 3) + number(60000) + number(0)
                    + number(0) + number(30000) + number(30000) + number(200000) + number(0, 3))
        data[407728:407731] = terminal
        parsed = DipTraceParser(circle_steps=256).parse_bytes(data)
        for layer, margin in ((0, 0), ('Top Mask', 0.1), ('Top Paste', -0.05)):
            with self.subTest(layer=layer):
                shape = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(248, 248, 251.2, 252))
                width, height, radius = 2 + 2 * margin, 1 + 2 * margin, 0.25 + margin
                self.assertAlmostEqual(shape.area, width * height - (4 - math.pi) * radius ** 2, delta=0.0001)
                self.assertFalse(shape.covers(Point(251 + margin - 0.01, 250.5 + margin - 0.01)))
                self.assertTrue(shape.covers(Point(250, 250)))
        terminal = unary_union(parsed.layers['Top Terminals']['solid_geometry']).intersection(box(251, 249, 253, 251))
        self.assertAlmostEqual(terminal.area, 1 - (4 - math.pi) * 0.2 ** 2, delta=0.0001)
        self.assertFalse(terminal.covers(Point(252.49, 250.49)))

    def test_native_segmented_paste_and_by_paste_mask_keep_individual_rectangles(self):
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import scale, translate

        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data = bytearray(self.FIXTURE.read_bytes())
        data[32414:32422] = number(7500000) + number(-7500000)
        data[32591:32599] = number(60000) * 2
        data[32611:32614] = number(2, 3)
        # HabitatControl f2850aff v4-5ch U3.6: [0, 0, 3, 0] at
        # 757330 selects its saved rectangles despite all primary flags being 0.
        # GPSDO's corresponding Pad1 state record is at 381970.
        data[381970:381982] = number(3, 3) * 4
        # Reconstructed native extension using the observed counted arrays and
        # official MaskPaste Item X1/Y1/X2/Y2 semantics, not a claimed native export.
        top = number(2, 3) + b''.join(number(x) + number(y) for x, y in
                                      ((-24000, -18000), (-6000, 18000), (6000, -18000), (24000, 18000)))
        bottom = number(1, 3) + b''.join(number(x) + number(y) for x, y in
                                         ((-18000, -9000), (18000, 9000)))
        data[407747:407753] = top + bottom
        for flipped in (False, True):
            with self.subTest(bottom=flipped):
                data[32411] = int(flipped)
                parsed = DipTraceParser().parse_bytes(data)
                for side, expected in (('Top', box(-0.8, -0.6, -0.2, 0.6).union(box(0.2, -0.6, 0.8, 0.6))),
                                       ('Bottom', box(-0.6, -0.3, 0.6, 0.3))):
                    if flipped:
                        side = 'Bottom' if side == 'Top' else 'Top'
                        expected = scale(expected, -1, 1, origin=(0, 0))
                    expected = translate(expected, 250, 250)
                    for suffix in (' Paste', ' Mask'):
                        actual = unary_union(parsed.layers[side + suffix]['solid_geometry']).intersection(
                            box(248, 248, 252, 252))
                        self.assertLess(actual.symmetric_difference(expected).area, 1e-8)

        # A retained rectangle cache isn't proof that Segments is active.
        # The modern Common state also overrides stale primary flags.
        data[32411] = 0
        data[32629:32633] = b'\3\3\3\3'
        data[381970:381982] = number(0, 3) * 4
        parsed = DipTraceParser().parse_bytes(data)
        paste = unary_union(parsed.layers['Top Paste']['solid_geometry']).intersection(box(248, 248, 252, 252))
        self.assertLess(paste.symmetric_difference(box(249.05, 249.05, 250.95, 250.95)).area, 1e-8)
        self.assertLess(unary_union(parsed.layers.get('Bottom Paste', {}).get('solid_geometry', []))
                        .intersection(box(248, 248, 252, 252)).area, 1e-8)

    def test_native_terminal_projection_is_not_added_to_copper(self):
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import scale, translate

        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data = bytearray(self.FIXTURE.read_bytes())
        # The genuine GPSDO U1/U2/U4 records contain 40/14/5 rectangular
        # terminals, independently of the synthetic off-board projection below.
        native = DipTraceParser().parse_bytes(data)
        self.assertAlmostEqual(unary_union(native.layers['Top Terminals']['solid_geometry']).area,
                               40 * 0.133444 + 14 * 0.11375 + 5 * 0.403225, places=8)
        data[32414:32422] = number(7500000) + number(-7500000)
        # Rectangle terminal framing independently observed at bytes 287698..287731
        # of DipTrace's official patterns.zip/_qfn_r_p0.50.lib (downloaded 2026-10-27).
        terminal = (number(1, 3) + number(2, 3) + number(30000) + number(-3000)
                    + number(0) + number(9000) + number(10500) + number(0) + number(0, 3))
        data[407728:407731] = terminal
        for bottom in (False, True):
            with self.subTest(bottom=bottom):
                data[32411] = int(bottom)
                parsed = DipTraceParser().parse_bytes(data)
                expected = box(0.85, -0.075, 1.15, 0.275)
                if bottom:
                    expected = scale(expected, -1, 1, origin=(0, 0))
                expected = translate(expected, 250, 250)
                layer = ('Bottom' if bottom else 'Top') + ' Terminals'
                actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(box(248, 248, 252, 252))
                self.assertLess(actual.symmetric_difference(expected).area, 1e-8)
                for copper in (0, 1):
                    self.assertLess(unary_union(parsed.layers.get(copper, {}).get('solid_geometry', []))
                                    .intersection(expected).area, 1e-8)

    def test_native_malformed_pad_extension_fails_without_partial_geometry(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = self.FIXTURE.read_bytes()
        number = lambda n, size=4: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        for at, replacement in ((407728, number(5, 3)), (407747, number(100000, 3)),
                                (407731, number(-10000)), (407712, number(-1)),
                                (380878, number(4, 3)), (381970, number(4, 3)), (383530, number(4, 3))):
            with self.subTest(at=at):
                parsed = DipTraceParser()
                with self.assertRaises(DipTraceParseError):
                    parsed.parse_bytes(data[:at] + replacement + data[at + len(replacement):])
                self.assertEqual(parsed.layers, {})
                self.assertEqual(parsed.drill_groups, {})

    def test_native_rectangle_saved_contour_preserves_concavity_and_closing_edge(self):
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.geometry import LineString

        # A simplified nine-vertex contour observed in the genuine v41 bus
        # connector footprint. It isn't a pair of rectangle bounding corners.
        points = [(0, 8), (0, 0), (2, 0), (2, -1), (3, -1),
                  (3, 9), (2, 9), (2, 8), (0.2, 8)]
        shape, follow = DipTraceParser(circle_steps=64)._graphic(2, points, 0.2)
        expected = LineString(points + [points[0]])
        self.assertLess(shape.symmetric_difference(expected.buffer(0.1)).area, 1e-8)
        self.assertTrue(follow.is_closed)
        self.assertFalse(shape.contains(Point(1, 4)))

    def test_native_placement_records_are_matched_by_identity(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # Exchange complete extension records for two different capacitor placements.
        a, b, c = 388656, 389722, 390788
        reordered = data[:a] + data[b:c] + data[a:b] + data[c:]
        expected = DipTraceParser().parse_bytes(data)
        # Hidden reference labels have no name in their extension record. Native
        # deck65 v18 and HabitatControl v4 retain their dense component order.
        hidden = data[:389656] + b'\0\0' + data[389662:]
        hidden = hidden[:4383] + b'\0\0' + hidden[4389:]
        for variant in (reordered, hidden):
            with self.subTest(hidden=variant == hidden):
                actual = DipTraceParser().parse_bytes(variant)
                for layer in (0, 1, 'Top Mask', 'Bottom Mask'):
                    self.assertLess(unary_union(expected.layers[layer]['solid_geometry']).symmetric_difference(
                        unary_union(actual.layers[layer]['solid_geometry'])).area, 1e-8)
                self.assertEqual(actual.drill_groups, expected.drill_groups)

    def test_native_unnamed_board_artwork_keeps_manufacturing_geometry(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # HabitatControl v4's first five board shapes have no TextLines at all.
        # Remove the first GPSDO shape's optional name in both native tables.
        changed = data[:437970] + (1000000).to_bytes(3, 'big') + data[437985:]
        name = b'\0\5\0S\0h\0a\0p\0e'
        at = changed.index(name, 91466, 91563)
        changed = changed[:at] + b'\0\0' + changed[at + len(name):]
        expected = DipTraceParser().parse_bytes(data)
        actual = DipTraceParser().parse_bytes(changed)
        self.assertEqual(actual.layers.keys(), expected.layers.keys())
        for layer in expected.layers:
            with self.subTest(layer=layer):
                self.assertLess(unary_union(expected.layers[layer]['solid_geometry']).symmetric_difference(
                    unary_union(actual.layers[layer]['solid_geometry'])).area, 1e-8)
        self.assertEqual(actual.drill_groups, expected.drill_groups)

    def test_legacy_picture_is_drawn_from_its_sidecar_bitmap(self):
        import tempfile
        from PIL import Image
        from appParsers.ParseDipTrace import DipTraceParser

        data = bytearray((self.FIXTURE.parent / 'DAP42v2.dip').read_bytes())
        original = DipTraceParser().parse_bytes(data)
        # Turn the unrotated v40 Top Silk text record at byte 47471 into picture 1
        # (kind, style fields); its two points become the picture box.
        at = 47471
        data[at + 12:at + 15] = (1000007).to_bytes(3, 'big')
        data[at + 18:at + 21] = (1000001).to_bytes(3, 'big')
        left, top, right, bottom = 9.407366666666666, 7.102633333333333, 11.563133333333333, 5.728266666666666
        silk = lambda parsed: unary_union(parsed.layers['Top Silk']['solid_geometry'])
        with tempfile.TemporaryDirectory() as tmp:
            board, bitmap = Path(tmp) / 'DAP42v2.dip', Path(tmp) / 'DAP42v2.dipb1'
            board.write_bytes(bytes(data))
            # Without its image file (or without a known folder) the picture is
            # reported once and the rest of the board still imports.
            missing = DipTraceParser().parse_file(board)
            self.assertEqual(len(missing.warnings), len(original.warnings) + 1)
            base = silk(missing)
            unsourced = DipTraceParser().parse_bytes(data)
            self.assertEqual(len(unsourced.warnings), len(original.warnings) + 1)
            self.assertLess(silk(unsourced).symmetric_difference(base).area, 1e-9)

            # Stretched to the box; the BMP's top-left pixel lands in the box's
            # top-left corner (z80/logic_probe Gerber exports); dark pixels are ink.
            marker = Image.new('RGB', (4, 2), (255, 255, 255))
            marker.putpixel((0, 0), (0, 0, 0))
            marker.save(bitmap, format='BMP')
            expected = box(left, top - (top - bottom) / 2, left + (right - left) / 4, top)
            self.assertLess(base.intersection(expected).area, expected.area * 0.5)
            drawn = silk(DipTraceParser().parse_file(board))
            self.assertLess(drawn.symmetric_difference(base.union(expected)).area, 1e-8)

            # An unreadable, rotated or oversized picture warns and adds nothing.
            rotated = bytearray(data)
            rotated[at + 2:at + 5] = (1000001).to_bytes(3, 'big')
            for name, content in (('unreadable', b'not a bitmap'), ('rotated', bytes(rotated)),
                                  ('oversized', bytes(data))):
                with self.subTest(case=name):
                    if name == 'unreadable':
                        bitmap.write_bytes(content)
                    else:
                        marker.save(bitmap, format='BMP')
                        board.write_bytes(content)
                    limit = 7 if name == 'oversized' else DipTraceParser.PICTURE_PIXELS
                    with patch.object(DipTraceParser, 'PICTURE_PIXELS', limit):
                        parsed = DipTraceParser().parse_file(board)
                    self.assertEqual(len(parsed.warnings), len(original.warnings) + 1)
                    self.assertLess(silk(parsed).symmetric_difference(base).area, 1e-9)

    def test_legacy_corpus_picture_sidecar(self):
        import os
        import shutil
        import tempfile
        from appParsers.ParseDipTrace import DipTraceParser

        root = os.environ.get('DIPTRACE_V38_EXAMPLES')
        source = Path(root or '.') / 'corpus-machadoleonardo94--HabitatControl-hardware--Habitat_V1--Habitat_layout.dip'
        bitmap = Path(root or '.') / 'Habitat_layout.dipb1'
        if not root or not source.exists() or not bitmap.exists():
            self.skipTest('Set DIPTRACE_V38_EXAMPLES to the corpus with Habitat_V1 and Habitat_layout.dipb1')
        with tempfile.TemporaryDirectory() as tmp:
            # The sidecar name follows the original board name.
            board = Path(tmp) / 'Habitat_layout.dip'
            shutil.copyfile(source, board)
            shutil.copyfile(bitmap, Path(tmp) / 'Habitat_layout.dipb1')
            ink = unary_union(DipTraceParser().parse_file(board).layers['Bottom Silk']['solid_geometry'])
        self.assertAlmostEqual(ink.area, 43.4151, places=3)
        self.assertTrue(box(13.0175, 66.3575, 31.4325, 84.455).buffer(1e-9).contains(ink))

    def test_native_vector_picture_preserves_holes_without_external_image_files(self):
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.geometry import box

        data = bytearray(self.FIXTURE.read_bytes())
        # Isolate the native vector picture on the bottom silk. Keep all component
        # geometry, but disable the other board annotations in their native records.
        for at in (91466, 91563, 91672, 91829, 91918, 92116, 92205, 92292, 92379, 92470, 92639):
            data[at] = 0
        data[92033:92036] = (1000004).to_bytes(3, 'big')  # move this picture to bottom silk
        integer = lambda n: (1000000 + n).to_bytes(3, 'big')
        coordinate = lambda n: (1000000000 + n).to_bytes(4, 'big')
        rings = [((0, 0), (5700000, 0), (5700000, 5700000), (0, 5700000)),
                 ((1425000, 1425000), (4275000, 1425000), (4275000, 4275000), (1425000, 4275000))]
        vector = integer(2) + b''.join(integer(4) + b''.join(coordinate(x) + coordinate(y) for x, y in ring)
                                        for ring in rings)
        data[438376:458884] = vector
        parsed = DipTraceParser().parse_bytes(data)
        x, y = 143.10486666666668, 26.24576666666667
        w, h = 482469 / 30000, 478190 / 30000
        expected = box(x - w / 2, y - h / 2, x + w / 2, y + h / 2).difference(
            box(x - w / 4, y - h / 4, x + w / 4, y + h / 4))
        self.assertLess(unary_union(parsed.layers['Bottom Silk']['solid_geometry']).symmetric_difference(
            expected).area, 1e-8)
        # Explicit picture anchors are relative to its insertion point, before
        # bottom-side mirroring, rather than always being the picture center.
        data[438330:438333] = integer(2)  # left
        data[438333:438336] = integer(2)  # top
        anchored = DipTraceParser().parse_bytes(data)
        from shapely.affinity import translate
        expected = translate(expected, -w / 2, -h / 2)
        self.assertLess(unary_union(anchored.layers['Bottom Silk']['solid_geometry']).symmetric_difference(
            expected).area, 1e-8)

    def test_native_settings_and_rotation_invariant_pad_variants(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        baseline = DipTraceParser().parse_bytes(data)
        font = data.index(DipTraceParser.FONT_MARKER, 78000)
        # View/font settings aren't record signatures. Placement state flags aren't
        # signatures either, and a circular standalone pad doesn't need rotation.
        variants = [data[:font] + (1000002).to_bytes(3, 'big') * 2 + data[font + 6:],
                    data[:388668] + b'\0\1' + data[388670:],
                    data[:388683] + data[388685:],  # compact frame: no empty recovery-code string
                    data[:407278] + data[407824:],
                    data[:407278] + data[407278:407824] * 2 + data[407824:]]
        for variant in variants:
            parsed = DipTraceParser().parse_bytes(variant)
            for layer in (0, 1, 'Top Mask', 'Bottom Mask', 'Top Paste'):
                self.assertLess(unary_union(parsed.layers[layer]['solid_geometry']).symmetric_difference(
                    unary_union(baseline.layers[layer]['solid_geometry'])).area, 1e-8)
        # An unfinished layout can contain valid pads/routes without an outline.
        no_outline = data[:156] + (1000000).to_bytes(3, 'big') + data[267:]
        parsed = DipTraceParser().parse_bytes(no_outline)
        self.assertTrue(parsed.board_outline.is_empty)
        self.assertLess(unary_union(parsed.layers[0]['solid_geometry']).symmetric_difference(
            unary_union(baseline.layers[0]['solid_geometry'])).area, 1e-8)

    def test_native_inner_signal_layers_and_mirrored_arbitrary_angle_slots(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser
        from shapely.affinity import rotate, scale

        data = self.FIXTURE.read_bytes()
        integer = lambda n: (1000000 + n).to_bytes(3, 'big')
        inner2 = data[305:306] + integer(2) + data[309:334]
        inner3 = data[305:306] + integer(3) + data[309:334]
        layered = data[:302] + integer(4) + data[305:369] + inner2 + inner3 + data[369:]
        parsed = DipTraceParser().parse_bytes(layered)
        self.assertTrue({0, 1, 2, 3}.issubset(parsed.layers))
        # The inner layers carry through pads/vias, not the standalone SMD pad.
        self.assertFalse(unary_union(parsed.layers[2]['solid_geometry']).contains(Point(139.5651333333, 37.3030333333)))
        self.assertGreater(unary_union(parsed.layers[2]['solid_geometry']).area, 250)

        angle = 37.2
        changed = bytearray(data)
        changed[1385] = 1               # power connector on bottom
        changed[387522:387526] = (1000000000 + round(math.radians(angle) * 10000)).to_bytes(4, 'big')
        original = DipTraceParser().parse_bytes(data)
        mirrored = DipTraceParser().parse_bytes(changed)
        center = (93.84513333333334, 30.353033333333332)
        before = [LineString([a, b]) for t in original.drill_groups['PTH'].values() for a, b in t['slots']]
        after = [LineString([a, b]) for t in mirrored.drill_groups['PTH'].values() for a, b in t['slots']]
        self.assertEqual(len(after), 3)
        stored_angle = math.degrees(round(math.radians(angle) * 10000) / 10000)
        for slot in before:
            expected = scale(rotate(slot, stored_angle, origin=center), -1, 1, origin=center)
            self.assertTrue(any(expected.hausdorff_distance(actual) < 1e-8 for actual in after))

    def test_native_component_text_has_its_own_rotation(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # C1's marking counter-rotates its 180-degree footprint in the native
        # extension. Changing only the marking must change silk, not copper.
        changed = data[:389636] + (1000000000).to_bytes(4, 'big') + data[389640:]
        original = DipTraceParser().parse_bytes(data)
        rotated = DipTraceParser().parse_bytes(changed)
        self.assertGreater(unary_union(original.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(rotated.layers['Top Silk']['solid_geometry'])).area, 0.1)
        self.assertLess(unary_union(original.layers[0]['solid_geometry']).symmetric_difference(
            unary_union(rotated.layers[0]['solid_geometry'])).area, 1e-8)

    def test_native_positioned_markings_use_field_value_font_and_component_transform(self):
        import math
        from appParsers.ParseDipTrace import DipTraceParser

        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        # Genuine native frames: GPSDO v54 C1 Value/Assy, and rotate v60
        # C1 RefDes/Assy. Both positions are deliberately clear of board artwork.
        for fixture, at, text, xy, size_at, custom_at in (
                (self.FIXTURE, 388812, '0.1uF', (112.89513333333333, 30.953033333333334),
                 388688, 388687),
                (self.FIXTURE.parent / 'rotate.dip', 10687, 'C1', (-8.6, 22.85),
                 10659, 10658),
                (self.FIXTURE.parent / 'rotate.dip', 10735, 'CAP100', (-8.6, 22.85),
                 10659, 10658),
                (self.FIXTURE.parent / 'rotate.dip', 10831, 'CAP-2.54/6.6x3', (-8.6, 22.85),
                 10659, 10658)):
            data = bytearray(fixture.read_bytes())
            # Decode the side byte from the real component's primary header,
            # rather than depending on the length of its saved library path.
            from appParsers.ParseDipTrace import _Reader
            component = DipTraceParser()._components(data, 0, len(data))[1 if fixture == self.FIXTURE else 0]
            reader = _Reader(data, component['pos'] + 14)
            reader.string()
            reader.number()
            reader.take(reader.count(8) * 8)
            side_at = reader.pos + 1
            for bottom, custom in ((False, False), (True, True)):
                with self.subTest(version=fixture.name, bottom=bottom):
                    data[side_at] = int(bottom)
                    data[custom_at] = int(custom)
                    data[size_at:size_at + 3] = number(7)
                    data[at:at + 24] = (number(1) + number(8) + number(2) + number(1)
                                        + number(4500000, 4) + number(-2400000, 4)
                                        + number(7854, 4))
                    parsed = DipTraceParser().parse_bytes(data)
                    layer = ('Bottom' if bottom else 'Top') + ' Assembly'
                    expected = DipTraceParser()
                    expected._text(layer, text, (150, 80), 7 if custom else (4 if text == '0.1uF' else 3),
                                   1, -2, 2, 1, angle=math.degrees(0.7854),
                                   component={'xy': xy, 'angle': 180 if text == '0.1uF' else 0,
                                              'bottom': bottom})
                    solid = unary_union(expected._solids[layer])
                    actual = unary_union(parsed.layers[layer]['solid_geometry']).intersection(
                        box(*solid.envelope.buffer(0.1).bounds))
                    self.assertLess(solid.symmetric_difference(actual).area, 1e-8)
                    data[at:at + 3] = number(2)
                    hidden = DipTraceParser().parse_bytes(data)
                    self.assertTrue(unary_union(hidden.layers.get(layer, {}).get('solid_geometry', []))
                                    .intersection(solid).is_empty)

    def test_native_hidden_positioned_reference_removes_saved_marking_cache(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # C1's native RefDes/Silk Position record and graphic cache share the
        # same (3.3080333, .9161667) anchor. Hide must not leave the cached ink.
        original = DipTraceParser().parse_bytes(data)
        hidden = DipTraceParser().parse_bytes(
            data[:388692] + (1000002).to_bytes(3, 'big') + data[388695:])
        before = unary_union(original.layers['Top Silk']['solid_geometry'])
        after = unary_union(hidden.layers['Top Silk']['solid_geometry'])
        removed = before.difference(after)
        self.assertGreater(removed.area, 0.1)
        self.assertLess(removed.distance(Point(109.5871, 30.0368666667)), 2)
        self.assertLess(after.difference(before).area, 1e-8)
        self.assertLess(unary_union(original.layers[0]['solid_geometry']).symmetric_difference(
            unary_union(hidden.layers[0]['solid_geometry'])).area, 1e-8)
        # Hide overrides generated alignments too. Their inactive Position
        # coordinates needn't agree with the saved marking graphic's anchor.
        for align in (1, 2, 7):
            with self.subTest(align=align):
                changed = bytearray(data)
                changed[388692:388698] = (1000002).to_bytes(3, 'big') + (1000000 + align).to_bytes(3, 'big')
                # deck65 v18 retains Horz=-1 on a hidden marking. Its inactive
                # alignment mustn't reject the component or restore cached ink.
                changed[388698:388701] = (999999).to_bytes(3, 'big')
                changed[388704:388712] = (1004500000).to_bytes(4, 'big') + (997600000).to_bytes(4, 'big')
                hidden = DipTraceParser().parse_bytes(changed)
                self.assertLess(unary_union(hidden.layers['Top Silk']['solid_geometry'])
                                .symmetric_difference(after).area, 1e-8)
        # Common inherits the native global Hide; explicit Show overrides it.
        common = bytearray(data)
        common[386929:386932] = (1000002).to_bytes(3, 'big')
        common_hidden = DipTraceParser().parse_bytes(common)
        self.assertLess(unary_union(common_hidden.layers['Top Silk']['solid_geometry'])
                        .intersection(removed).area, 1e-8)
        common[388692:388695] = (1000001).to_bytes(3, 'big')
        shown = DipTraceParser().parse_bytes(common)
        self.assertLess(removed.difference(unary_union(shown.layers['Top Silk']['solid_geometry'])).area, 1e-8)

        # A centered reference has saved text too. Hide must consume that cache
        # even when the inactive Position coordinates are stale.
        centered = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        centered[10663:10687] = (number(1) + number(1) + number(0) + number(0)
                                + number(4500000, 4) + number(-2400000, 4) + number(0, 4))
        visible = DipTraceParser().parse_bytes(centered)
        centered[10663:10666] = number(2)
        hidden = DipTraceParser().parse_bytes(centered)
        ink = unary_union(visible.layers['Top Silk']['solid_geometry'])
        remaining = unary_union(hidden.layers['Top Silk']['solid_geometry'])
        self.assertGreater(ink.difference(remaining).area, 0.1)
        self.assertLess(remaining.difference(ink).area, 1e-8)

        # An ordinary text object can spell the reference too. Its visibility
        # isn't controlled by the component's RefDes marking settings.
        literal = bytearray(data)
        component = DipTraceParser()._components(literal, 0, len(literal))[1]
        at = literal.index(number(700), component['header_end'], component['end'])
        literal[at:at + 3] = number(7)
        visible = DipTraceParser().parse_bytes(literal)
        literal[388692:388695] = number(2)
        hidden = DipTraceParser().parse_bytes(literal)
        self.assertLess(unary_union(visible.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(hidden.layers['Top Silk']['solid_geometry'])).area, 1e-8)

    def test_native_centered_reference_does_not_use_stale_position_offsets(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        number = lambda n, size=3: (n + (1000000 if size == 3 else 1000000000)).to_bytes(size, 'big')
        data[10687:10711] = (number(1) + number(1) + number(2) + number(2)
                            + number(4500000, 4) + number(-2400000, 4) + number(7854, 4))
        parsed = DipTraceParser().parse_bytes(data)
        expected = DipTraceParser()
        expected._text('Top Assembly', 'C1', (-8.6, 22.85), 3, 1, -2, 0, 0)
        self.assertLess(unary_union(parsed.layers['Top Assembly']['solid_geometry']).symmetric_difference(
            unary_union(expected._solids['Top Assembly'])).area, 1e-8)

        # A centered field can retain a cached graphic at the old font size.
        # A component font override replaces that ink rather than adding to it.
        data = bytearray((self.FIXTURE.parent / 'rotate.dip').read_bytes())
        data[10658] = 1
        data[10659:10662] = number(7)
        parsed = DipTraceParser().parse_bytes(data)
        data[10663:10666] = number(2)
        hidden = DipTraceParser().parse_bytes(data)
        expected = DipTraceParser()
        expected._text('Top Silk', 'C1', (-8.6, 22.85), 7, 1, -2, 0, 0)
        expected = unary_union(hidden.layers['Top Silk']['solid_geometry'] + expected._solids['Top Silk'])
        actual = unary_union(parsed.layers['Top Silk']['solid_geometry'])
        self.assertLess(actual.symmetric_difference(expected).area, 1e-8)

    def test_native_empty_component_text_does_not_require_a_placement(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # A blank footprint text object is legitimate (for example SW_PG1350).
        # Keep its one empty TextLine in the extension, not a fabricated label.
        changed = data[:389656] + b'\0\0' + data[389662:]
        # Retain the component identity in its extension metadata, independently
        # of the now-empty graphic's content.
        changed = changed[:388679] + data[389656:389662] + changed[388681:]
        changed = changed[:4383] + b'\0\0' + changed[4389:]
        original = DipTraceParser().parse_bytes(data)
        blank = DipTraceParser().parse_bytes(changed)
        self.assertGreater(unary_union(original.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(blank.layers['Top Silk']['solid_geometry'])).area, 0.01)
        self.assertLess(unary_union(original.layers[0]['solid_geometry']).symmetric_difference(
            unary_union(blank.layers[0]['solid_geometry'])).area, 1e-8)

    def test_native_repeated_identical_text_placements_are_not_ambiguous(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # Two coincident runs are allowed to have identical content and settings.
        text_frame = data[389628:389667]
        changed = data[:389667] + text_frame + data[389667:]
        changed = changed[:4434] + data[4352:4434] + changed[4434:]
        changed = changed[:3649] + (1000012).to_bytes(3, 'big') + changed[3652:]
        original = DipTraceParser().parse_bytes(data)
        repeated = DipTraceParser().parse_bytes(changed)
        self.assertLess(unary_union(original.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(repeated.layers['Top Silk']['solid_geometry'])).area, 1e-8)
        # Each run consumes its own native rotation even when both positions and
        # strings are identical. Only the second run now rotates independently.
        angle_at = 389667 + 82 + 8
        changed = changed[:angle_at] + (1000000000).to_bytes(4, 'big') + changed[angle_at + 4:]
        rotated = DipTraceParser().parse_bytes(changed)
        self.assertGreater(unary_union(repeated.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(rotated.layers['Top Silk']['solid_geometry'])).area, 0.01)

    def test_native_truetype_text_uses_requested_font_not_the_stroke_alphabet(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        # The saved sine board label requests Arial TrueType. Change only its
        # FontVector flag; real font counters/advances differ from the PCB alphabet.
        vector = data[:92362] + b'\1' + data[92363:]
        original = DipTraceParser().parse_bytes(data)
        stroked = DipTraceParser().parse_bytes(vector)
        self.assertGreater(unary_union(original.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(stroked.layers['Top Silk']['solid_geometry'])).area, 0.01)
        # Anchor the font's advance box, including trailing whitespace, not only
        # the ink bounds. Otherwise a centered label silently loses its spacing.
        padded = b'\0\6' + 'sine  '.encode('utf-16be')
        spaced = data[:479455] + padded + data[479465:]
        spaced = spaced[:92340] + padded + spaced[92350:]
        spaced = DipTraceParser().parse_bytes(spaced)
        self.assertGreater(unary_union(original.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(spaced.layers['Top Silk']['solid_geometry'])).area, 0.01)

        # FontScale applies before the native text rotation, including the
        # advance box used for anchoring. Isolate the label outside the board.
        import math
        from shapely.affinity import rotate, scale

        isolated = bytearray(data)
        number = lambda n: (n + 1000000000).to_bytes(4, 'big')
        isolated[92332:92340] = number(7500000) + number(-7500000)
        normal = DipTraceParser().parse_bytes(isolated)
        isolated[92371:92375] = number(20000)
        wide = DipTraceParser().parse_bytes(isolated)
        region = box(240, 240, 260, 260)
        ink = unary_union(normal.layers['Top Silk']['solid_geometry']).intersection(region)
        self.assertGreater(ink.area, 0.01)
        angle = math.degrees(1.5708)
        expected = rotate(scale(rotate(ink, -angle, origin=(250, 250)), xfact=2, yfact=1,
                                origin=(250, 250)), angle, origin=(250, 250))
        actual = unary_union(wide.layers['Top Silk']['solid_geometry']).intersection(region)
        self.assertLess(actual.symmetric_difference(expected).area, 1e-8)

    def test_native_multiline_text_preserves_line_spacing(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = self.FIXTURE.read_bytes()
        integer = lambda n: (1000000 + n).to_bytes(3, 'big')
        # Extend the sine board label with a second line in its counted text frame.
        data = data[:479452] + integer(2) + data[479455:479465] * 2 + data[479465:]
        tight = data[:479448] + (1000010000).to_bytes(4, 'big') + data[479452:]
        wide = data[:479448] + (1000020000).to_bytes(4, 'big') + data[479452:]
        a = DipTraceParser().parse_bytes(tight)
        b = DipTraceParser().parse_bytes(wide)
        self.assertGreater(unary_union(a.layers['Top Silk']['solid_geometry']).symmetric_difference(
            unary_union(b.layers['Top Silk']['solid_geometry'])).area, 0.1)
        self.assertLess(unary_union(a.layers[0]['solid_geometry']).symmetric_difference(
            unary_union(b.layers[0]['solid_geometry'])).area, 1e-8)

    def test_native_filled_obround_is_a_capsule_not_an_ellipse(self):
        from appParsers.ParseDipTrace import DipTraceParser

        data = bytearray(self.FIXTURE.read_bytes())
        data[91466] = 0  # isolate the component assembly graphics as the baseline
        baseline = DipTraceParser(circle_steps=128).parse_bytes(data)
        data[91466] = 1
        data[91478:91481] = (1000005).to_bytes(3, 'big')  # board FillObround
        parsed = DipTraceParser(circle_steps=128).parse_bytes(data)
        x, y = 89.59846666666667, 37.47716666666666
        capsule = LineString([(x + 33, y), (x + 67, y)]).buffer(33, quad_segs=32)
        expected = unary_union(baseline.layers['Top Assembly']['solid_geometry']).union(capsule)
        self.assertLess(unary_union(parsed.layers['Top Assembly']['solid_geometry']).symmetric_difference(
            expected).area, 1e-8)

    def test_bad_native_input_clears_previous_outputs(self):
        from appParsers.ParseDipTrace import DipTraceParser, DipTraceParseError

        data = self.FIXTURE.read_bytes()
        parser = DipTraceParser().parse_bytes(data)
        route = data.index(DipTraceParser.CHAIN, 78000)  # first route in the fixture's net section
        bad_route = data[:route + 9] + (1000000).to_bytes(3, 'big') + data[route + 12:]
        bad_pad = data[:1763] + (1000009).to_bytes(3, 'big') + data[1766:]
        # The first saved fill's completion flag and the second fill's layer field.
        bad_cache = data[:156176] + b'\0' + data[156177:]
        bad_layer = data[:156198] + (1000002).to_bytes(3, 'big') + data[156201:]
        # Corrupt the effective mode, not the superseded primary boolean.
        bad_mask = data[:381970] + (1000004).to_bytes(3, 'big') + data[381973:]
        bad_picture = data[:438376] + (999999).to_bytes(3, 'big') + data[438379:]
        bad_via = data[:83354] + (1000001).to_bytes(3, 'big') + data[83357:]
        missing_placement = data[:388656] + data[389722:]
        for bad in (b'not a board', data[:8] + (1000061).to_bytes(3, 'big') + data[11:],
                    data[:200], data[:100000], bad_route, bad_pad, bad_cache, bad_layer,
                    bad_mask, bad_picture, bad_via, missing_placement):
            with self.subTest(size=len(bad)), self.assertRaises(DipTraceParseError):
                parser.parse_bytes(bad)
            self.assertEqual(parser.layers, {})
            self.assertEqual(parser.drill_groups, {})
            self.assertTrue(parser.board_outline.is_empty)


if __name__ == '__main__':
    unittest.main()
