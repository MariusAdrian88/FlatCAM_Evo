"""PADS manufacturing geometry, using real exports and focused in-memory records."""
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import math
import os
import re
import struct

import pytest
from shapely import LineString, Point, box
from shapely.ops import unary_union


FIXTURES = Path(__file__).parent / 'fixtures' / 'pads'


def parse(text):
    from appParsers.ParsePADS import PADSParser
    return PADSParser(circle_steps=128).parse_text(text)


@lru_cache(maxsize=None)
def real(name, extension):
    """Parse each real fixture once per test session; tests using it only read the result."""
    from appParsers.ParsePADS import PADSParser
    return PADSParser(circle_steps=128).parse_file(FIXTURES / (name + extension))


@pytest.mark.parametrize('extension', ['.asc', '.pcb'])
def test_source_primitives_preserve_ascii_and_native_board_geometry(extension):
    from appParsers.ParsePADS import PADSParser
    baseline = PADSParser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'}),
                          circle_steps=128).parse_file(FIXTURES / ('LCORE_2' + extension))
    parsed = real('LCORE_2', extension)
    records = [r for layer in parsed.layers.values() for t in layer['tools'].values()
               if t['type'] == 'C' for r in t['geometry']]
    assert any(isinstance(r.get('follow'), Point) for r in records)
    assert any(isinstance(r.get('follow'), LineString) for r in records)
    from test_allegro_brd_parser import export_layer
    expected = {25: (0, 264), 22: (0, 131), 23: (0, 17), 24: (0, 16), 26: (0, 22),
                27: (0, 21), 29: (0, 13), 30: (0, 2), 'Outline': (0, 1),
                1: (39, 118), 2: (62, 119)}
    for key, layer in parsed.layers.items():
        assert unary_union(layer['solid_geometry']).equals(unary_union(baseline.layers[key]['solid_geometry']))
        records = [r for t in layer['tools'].values() for r in t['geometry']]
        assert sum(isinstance(r.get('follow'), Point) for r in records) == expected[key][0], key
        strokes = [r for r in records if isinstance(r.get('follow'), LineString)]
        if key in parsed.copper_layers:
            # Substitute glyph buffers can protrude outside composed text; retain every saved route/graphic instead.
            paths = baseline.layers[key]['follow_geometry']
            assert len(paths) == expected[key][1], key
            assert all(any(path.equals(r['follow']) for r in strokes) for path in paths), key
        else:
            assert len(strokes) == expected[key][1], key
        # the regions come from a Clipper2 difference (1e-9 grid): they can miss a feature by a grid step
        assert unary_union([r['solid'] for r in records]).symmetric_difference(
            unary_union(layer['solid_geometry'])).area < 1e-7, key
        exported, code = export_layer(layer, circle_steps=128)
        assert code.count('D03*') == expected[key][0]
        assert unary_union(exported.solid_geometry).symmetric_difference(
            unary_union(layer['solid_geometry'])).area < 0.01, key
        # Every saved graphic/text stroke on the technical layers remains independently selectable.
        if key in (26, 27, 'Outline'):
            strokes = [r for t in layer['tools'].values() for r in t['geometry']
                       if isinstance(r.get('follow'), LineString)]
            assert strokes, key
            assert unary_union([r['solid'] for r in strokes]).symmetric_difference(
                unary_union(layer['solid_geometry'])).area < 1e-9


def patched_lcore_text():
    """LCORE_2.asc with the binary's clearance distances.

    LCORE_2.pcb and LCORE_2.asc are different save states: the .asc holds 450000 in the 28 *_TO_* distances of its
    CLEARANCE_RULE block, the .pcb holds 225000. The text is patched in memory; the fixture stays unchanged.
    """
    text = (FIXTURES / 'LCORE_2.asc').read_bytes().decode('cp1252')
    text, count = re.subn(r'^((?:TRACK|VIA|PAD|SMD|COPPER|TEXT|OUTLINE|DRILL)_TO_[A-Z]+) 450000', r'\1 225000',
                          text, flags=re.M)
    assert count == 28
    return text


def native_reader(name, patch=None):
    """Native reader of a fixture; patch(data) may damage the bytes first."""
    from appParsers.ParsePADSBinary import PADSBinaryReader
    data = bytearray((FIXTURES / (name + '.pcb')).read_bytes())
    if patch:
        patch(data, PADSBinaryReader((FIXTURES / (name + '.pcb')).read_bytes()))
    return PADSBinaryReader(data)


def board(body, units='METRIC'):
    return '!PADS-POWERPCB-V9.5-%s!\n*PCB*\nMAXIMUMLAYER 2\n%s\n*END*\n' % (units, body)


def lcore_saved_components():
    """Extract unchanged component/graphic records, NOT a complete LCORE board.

    The source also needs generated pours, teardrops and exact copper lettering.
    Those must not be silently removed by the production importer.
    """
    lines = (FIXTURES / 'LCORE_2.asc').read_text().splitlines()
    output, keep = [lines[0]], False
    for line in lines[1:]:
        if line.startswith('*') and not line.startswith('*REMARK*'):
            keep = line.split('*')[1] in ('PCB', 'LINES', 'VIA', 'PARTDECAL', 'PARTTYPE', 'PART')
        if keep:
            output.append(line)
    return '\n'.join(output + ['*END*'])


def test_2005_real_component_records_preserve_pads_holes_and_arc_outline():
    result = parse(lcore_saved_components())
    assert result.copper_layers == [1, 2]
    # U2 pin 1: rotate (-2437500, 382500) by 180, then place at (0, -19947000).
    top = unary_union(result.layers[1]['solid_geometry'])
    expected = box(1.225, -13.728, 2.025, -13.378)
    assert top.covers(expected)
    assert top.intersection(expected.buffer(1e-4)).symmetric_difference(expected).area < 1e-6
    # U1 pin 2: 270-degree rotation, then board-frame mirroring, matches ROUTE endpoint.
    assert unary_union(result.layers[2]['solid_geometry']).covers(Point(-0.65, -11.25))
    holes = result.drill_groups['NPTH_1-2']
    assert sorted((tool['tooldia'], len(tool['drills'])) for tool in holes.values()) == [(1.6, 2), (2.0, 2)]
    assert all(not tool['slots'] for tool in holes.values())
    assert any(point.distance(Point(5.17633, -19.31867)) < 1e-9
               for tool in holes.values() for point in tool['drills'])
    # Saved board arc: the first diameter box gives this circle, independently of tessellation.
    center = ((-48629239 + 17370751) / 3000000 + 15625000 / 1500000,
              (3333653 + 69333643) / 3000000 - 36324500 / 1500000)
    radius = (17370751 + 48629239) / 3000000
    midpoint = Point(center[0] + radius * math.cos(math.radians(243.4)),
                     center[1] + radius * math.sin(math.radians(243.4)))
    assert result.board_outline.boundary.distance(midpoint) < 0.002
    assert len(result.layers['Outline']['follow_geometry'][0].coords) > 15
    # Explicit saved COPCLS on layer 25 is artwork, not a generated pour.
    assert unary_union(result.layers[25]['solid_geometry']).covers(Point(7.25, -15.4))


def test_2005_full_lcore_reconstructs_both_pours_and_copper_lettering():
    result = real('LCORE_2', '.asc')
    assert {pour['layer'] for pour in result.pours} == {1, 2}
    assert all(pour['net'] == 'GND' and pour['geometry'].area > 1 for pour in result.pours)
    for layer, region in ((1, box(23, -12, 44, -8)), (2, box(24, -16, 42, -10))):
        assert unary_union(result.layers[layer]['solid_geometry']).intersection(region).area > 0.1
    assert any('substitut' in warning.lower() for warning in result.warnings)
    assert any('teardrop' in warning.lower() for warning in result.warnings)
    assert any('reconstruct' in warning.lower() for warning in result.warnings)
    assert sorted((tool['tooldia'], len(tool['drills']))
                  for tool in result.drill_groups['NPTH_1-2'].values()) == [(1.6, 2), (2.0, 2)]


def test_reconstructed_pours_keep_foreign_clearance_voids_and_thermal_connections():
    result = parse(board('''DEFAULTCLEAR 0.4
THERLINEWID 0.3
*PARTDECAL*
D M 0 0 0 1 1 0 0
T0 0 0 0
PAD 0 3
-2 0 R 0.6 N
-1 0 R
0 0 R
*PARTTYPE*
P D UND 0 0 0 0
*PART*
H1 P 2 5 0 U N 0 -1 0 -1 0
*LINES*
B BOARD 0 0 1 1
CLOSED 5 0 0 0
0 0
10 0
10 10
0 10
0 0
K KEEPOUT 0 0 1 1
KPTCLS 5 0 0 1
3 7
4 7
4 8
3 8
3 7
C COPCUT 0 0 1 1
COPCUT 5 0 0 1
1 7
2 7
2 8
1 8
1 7
*VIA*
V 0.3 3
-2 2 R
-1 2 R
0 2 R
*ROUTE*
*SIGNAL* GND
U1.1 U2.1
2 2 1 0.2 0 V THERMAL
3 2 65 0.2 0
*SIGNAL* OTHER
U3.1 U4.1
5 -1 1 0.2 0
5 11 65 0.2 0
*POUR*
P POUROUT 0 0 1 0 P GND
POLY 5 0 0.2 1
0 0
10 0
10 10
0 10
0 0
Q POUROUT 5 5 1 0 Q GND
CIRCLE 2 0 0.2 2
-5 0
5 0'''))
    top, bottom = [pour['geometry'] for pour in result.pours]
    assert top.distance(box(4.9, 1, 5.1, 9)) >= 0.399
    assert top.covers(Point(3, 6))
    assert top.distance(Point(2, 5)) >= 0.699  # NPTH radius plus copper clearance
    assert not top.covers(Point(3.5, 7.5))  # keepout inside the connected island
    assert not top.covers(Point(1.5, 7.5))  # unassociated void only cuts new fill
    assert not top.covers(Point(8, 2))  # foreign track separates an unconnected island
    assert top.covers(Point(2, 3.1))  # a spoke connects the same-net via
    assert not top.covers(Point(2.8, 2.8))  # diagonal thermal gap
    assert bottom.covers(Point(3, 5)) and not bottom.covers(Point(0, 0))
    assert result.board_outline.covers(top) and result.board_outline.covers(bottom)


@pytest.mark.parametrize('reverse', [False, True])
def test_default_teardrop_is_regenerated_and_spans_short_collinear_segments(reverse):
    text = board('''*VIA*
V 0.3 3
-2 2 R
-1 2 R
0 2 R
*ROUTE*
*SIGNAL* N
U1.1 U2.1
0 0 1 0.2 0 V TEARDROP N 90 90
0.1 0 1 0.2 0
5 0 65 0.2 0''')
    if reverse:
        text = text.replace('0 0 1 0.2 0 V TEARDROP N 90 90\n0.1 0 1 0.2 0\n5 0 65 0.2 0',
                            '5 0 1 0.2 0\n0.1 0 1 0.2 0\n0 0 65 0.2 0 V TEARDROP P 90 90')
    result = parse(text)
    copper = unary_union(result.layers[1]['solid_geometry'])
    assert copper.covers(Point(1.1, 0.3))
    assert not copper.covers(Point(4, 0.3))
    # Default fields aren't interpreted as pad-diameter percentages.
    other = parse(text.replace('90 90', '10 10'))
    assert copper.symmetric_difference(unary_union(other.layers[1]['solid_geometry'])).area < 1e-9


def test_romansim_substitute_preserves_mirrored_rotated_artwork_and_rejects_missing_glyphs():
    body = '*TEXT*\n10 20 90 1 2 0.2 N LEFT DOWN\nRegular <Romansim Stroke Font>\nTOP'
    normal = unary_union(parse(board(body)).layers[1]['solid_geometry'])
    mirrored = unary_union(parse(board(body.replace('0.2 N', '0.2 M'))).layers[1]['solid_geometry'])
    from shapely.affinity import scale
    assert normal.area > 0.1
    assert normal.symmetric_difference(scale(mirrored, xfact=1, yfact=-1, origin=(10, 20))).area < 1e-9
    lowercase = unary_union(parse(board(body.replace('10 20 90', '0 0 0').replace('\nTOP', '\nbg')))
                            .layers[1]['solid_geometry'])
    # Real lowercase has an ascender and a descender, not reduced uppercase outlines.
    assert lowercase.bounds[1] < -0.4 and lowercase.bounds[3] > 1.4
    from appParsers.ParsePADS import PADSParseError
    with pytest.raises(PADSParseError, match='glyph'):
        parse(board(body.replace('\nTOP', '\nTOP Ж')))


def test_regenerated_teardrop_cannot_close_a_foreign_net_clearance():
    text = board('''DEFAULTCLEAR 0.2
*VIA*
V 0.3 3
-2 2 R
-1 2 R
0 2 R
*ROUTE*
*SIGNAL* N
U1.1 U2.1
0 0 1 0.2 0 V TEARDROP N 90 90
5 0 65 0.2 0
*SIGNAL* OTHER
U3.1 U4.1
1.1 0.65 1 0.1 0
1.8 0.65 65 0.1 0''')
    plain = unary_union(parse(text.replace(' TEARDROP N 90 90', '')).layers[1]['solid_geometry'])
    added = unary_union(parse(text).layers[1]['solid_geometry']).difference(plain)
    assert added.area > 0.01
    assert added.distance(box(1.1, 0.6, 1.8, 0.7)) >= 0.1999


def test_track_junction_teardrop_uses_local_width_not_the_crossing_track_length():
    result = parse(board('''*ROUTE*
*SIGNAL* N
U1.1 U2.1
0 0 1 0.2 0 TEARDROP N 90 90
5 0 65 0.2 0
U3.1 U4.1
0 -5 1 0.6 0
0 5 65 0.6 0'''))
    copper = unary_union(result.layers[1]['solid_geometry'])
    assert copper.covers(Point(0.4, 0.15))
    assert not copper.covers(Point(1, 1))


def test_decal_free_lettering_follows_each_placed_component():
    result = parse(board('''*PARTDECAL*
D M 0 0 0 0 0 1 0
0 0 0 1 2 0.2 N LEFT DOWN
Regular <Romansim Stroke Font>
TOP
*PARTTYPE*
P D UND 0 0 0 0
*PART*
U1 P 10 20 0 U N 0 -1 0 -1 0
U2 P -10 20 0 U M 0 -1 0 -1 0'''))
    top = unary_union(result.layers[1]['solid_geometry'])
    bottom = unary_union(result.layers[2]['solid_geometry'])
    from shapely.affinity import scale
    assert top.bounds[0] > 9 and bottom.bounds[2] < -9
    assert top.symmetric_difference(scale(bottom, xfact=-1, yfact=1, origin=(0, 0))).area < 1e-9


def test_2005_named_silk_layers_and_finger_drill_without_corner_field():
    result = parse(board('''*PARTDECAL*
D M 0 0 1 1 1 0 0
OPEN 2 0.2 0
0 0
2 0
T0 0 0 0
PAD 0 3
-2 2 RF 0 4 1 0.5 N 90 3 0.5
-1 0 R
0 0 R
*PARTTYPE*
P D I UND 0 0 0 0 Y
*PART*
U1 P 10 20 90 U M 0 -1 0 -1 0
*MISC*
LAYER DATA
{
LAYER 22
{
LAYER_NAME Silkscreen Top
}
LAYER 25
{
LAYER_NAME Silkscreen Bottom
}
}''').replace('V9.5', 'V2005.0'))
    assert unary_union(result.layers[2]['solid_geometry']).symmetric_difference(box(9, 19, 11, 23)).area < 1e-9
    assert 25 in result.layers and result.layers[25]['name'] == 'Silkscreen Bottom'
    assert 26 not in result.layers and 27 not in result.layers
    slot = result.drill_groups['NPTH_1-2'][1]
    assert slot['tooldia'] == 0.5
    # Local slot offset (0, 0.5), rotated 90 and mirrored, places its centre at (10.5, 20).
    assert sorted(point.x for point in slot['slots'][0]) == pytest.approx([9.25, 11.75])
    assert [point.y for point in slot['slots'][0]] == pytest.approx([20, 20])


def test_2005_saved_copper_cutouts_route_arcs_and_via_drilling():
    result = parse(board('''*LINES*
A COPPER 0 0 2
COPCLS 5 0 1
0 0
5 0
5 5
0 5
0 0
COPCUT 5 0 1
1 1
4 1
4 4
1 4
1 1
*VIA*
V 0.5 3
-2 1 R
-1 1 R
0 1.5 S
*ROUTE*
*SIGNAL* N
U1.1 U2.1
12 0 1 0.2 0
10 0 1 0.2 4096 CCW
10 2 2 0.2 0 V
13 2 65 0.2 0''').replace('V9.5', 'V2005.0'))
    top = unary_union(result.layers[1]['solid_geometry'])
    assert top.covers(Point(0.5, 0.5))
    assert not top.covers(Point(2.5, 2.5))
    assert top.covers(Point(10 + 2 ** 0.5, 2 ** 0.5))
    assert not top.covers(Point(11, 1))
    assert unary_union(result.layers[2]['solid_geometry']).covers(Point(12, 2))
    tool = result.drill_groups['PTH_1-2'][1]
    assert tool['tooldia'] == 0.5
    assert len(tool['drills']) == 1 and tool['drills'][0].equals(Point(10, 2))


@pytest.mark.parametrize(('body', 'message'), [
    ('*TEXT*\n0 0 0 1 1 0.1 N LEFT DOWN\nRegular Unknown\nTOP COPPER', 'font'),
    ('*ROUTE*\n*SIGNAL* N\nU1.1 U2.1\n0 0 1 1 0 THERMAL TEARDROP N 90\n1 0 65 1 0', 'teardrop'),
    ('*MISC*\nLAYER DATA\n{\nLAYER 1\n{\nPLANE MIXED\n}\n}', 'plane'),
])
def test_2005_unsafe_artwork_is_not_silently_omitted(body, message):
    from appParsers.ParsePADS import PADSParseError
    with pytest.raises(PADSParseError, match=message):
        parse(board(body).replace('V9.5', 'V2005.0'))


def test_real_claysight_copper_holes_and_outline():
    result = real('ClaySight_MK1', '.asc')
    assert result.source_kind == 'pads'
    assert result.copper_layers == [1, 2]
    assert result.board_outline.bounds == pytest.approx((5.08, 38.1, 106.68, 142.24))
    assert len(result.board_outline.interiors) == 8
    holes = [p for tools in result.drill_groups.values() for tool in tools.values() for p in tool['drills']]
    slots = [s for tools in result.drill_groups.values() for tool in tools.values() for s in tool['slots']]
    assert len(holes) == 124
    assert len(slots) == 16  # the diode pads explicitly save short slotted holes
    # U1 pin 1: saved placement plus decal terminal, in BASIC units (2/3 nm).
    pin = Point((70484848 + 2415311) / 1500000, (131444733 + 74444619) / 1500000)
    assert min(p.distance(pin) for p in holes) < 1e-9
    copper = unary_union(result.layers[1]['solid_geometry'])
    assert copper.covers(pin)
    # A long saved top route, not a connection airwire.
    assert copper.covers(Point(38232207 / 1500000, 148000000 / 1500000))
    assert 'Top Solder Mask Layer' == result.layers[121]['name']
    assert result.layers['Outline']['follow_geometry']


def test_real_tms_export_keeps_airwires_out_of_copper():
    result = real('TMS1mmX19', '.asc')
    assert result.layers[1]['solid_geometry'] and result.layers[2]['solid_geometry']
    assert any('unrouted' in warning.lower() for warning in result.warnings)
    # R5.1 -> U8.3 is layer 0, not a track.
    airwire = LineString([(254179800 / 3000000, 124023300 / 3000000), (293789100 / 3000000, 141541500 / 3000000)])
    assert unary_union(result.layers[1]['solid_geometry']).intersection(airwire).is_empty
    # Native export's JM3.54 endpoint independently fixes the bottom-side transform.
    bottom_pin = Point(144209466 / 1500000, 141975001 / 1500000)
    assert unary_union(result.layers[2]['solid_geometry']).covers(bottom_pin)


@pytest.mark.parametrize(('units', 'length'), [('METRIC', '25.4'), ('MILS', '1000'),
                                               ('INCHES', '1'), ('BASIC', '38100000')])
def test_units_and_partial_routes(units, length):
    result = parse(board('*ROUTE*\n*SIGNAL* N\nU1.1 U2.1\n'
                         '0 0 1 0.1 0\n%s 0 0 0.1 0\n%s 10 65 0.1 0' % (length, length), units))
    follows = result.layers[1]['follow_geometry']
    assert len(follows) == 1
    assert follows[0].length == pytest.approx(25.4)


def test_mirrored_pad_offsets_slots_and_layer_stack():
    result = parse(board('''*PARTDECAL*
D M 0 0 0 1 1 0 0
T2 3 2 3 1
PAD 0 4
-2 2 RF 0 4 1 0 1 N 90 3 0.5
-1 0 R
0 0 R
26 2 RF 0 4 1 0
*PARTTYPE*
P D UND 0 0 0 0
*PART*
U1 P 10 20 90 U M 0 -1 0 -1 0'''))
    # PADS rotates before mirroring in the board frame (also checked against real JM3.54).
    copper = unary_union(result.layers[2]['solid_geometry'])
    assert copper.symmetric_difference(box(12, 21, 14, 25)).area < 1e-9
    assert 1 not in result.layers
    slot = result.drill_groups['NPTH_1-2'][1]['slots'][0]
    assert sorted(p.x for p in slot) == pytest.approx([12.5, 14.5])
    assert [p.y for p in slot] == pytest.approx([22, 22])
    assert 27 in result.layers  # explicit top silk pad is mirrored to bottom silk


def test_route_arc_via_and_deduplication():
    text = board('''*VIA*
V 0.5 3
-2 1 R
-1 1 R
0 1.5 S
*ROUTE*
*SIGNAL* N
U1.1 U2.1
2 0 1 0.2 0
0 0 1 0.2 4096 CCW
0 2 2 0.2 0 V
3 2 65 0.2 0
U3.1 U2.1
0 2 2 0.2 0 V
3 2 65 0.2 0''')
    result = parse(text)
    top = unary_union(result.layers[1]['solid_geometry'])
    assert top.covers(Point(2 ** 0.5, 2 ** 0.5))
    assert not top.covers(Point(1, 1))
    assert unary_union(result.layers[2]['solid_geometry']).covers(Point(2, 2))
    assert len(result.drill_groups['PTH_1-2'][1]['drills']) == 1


def test_filled_copper_cutouts_are_local_to_their_item():
    result = parse(board('''*LINES*
A COPPER 0 0 2 1
COPCLS 5 0 0 1
0 0
5 0
5 5
0 5
0 0
COPCUT 5 0 0 1
1 1
4 1
4 4
1 4
1 1
B COPPER 0 0 1 1
COPCIR 2 0 0 1
2 2.5
3 2.5
C COPPER 0 0 2 1
COPCIR 2 0 0 1
10 2.5
12 2.5
COPCUT 5 0 0 1
11 0
13 0
13 5
11 5
11 0'''))
    copper = unary_union(result.layers[1]['solid_geometry'])
    assert copper.covers(Point(2.5, 2.5))
    assert not copper.covers(Point(1.5, 1.5))
    flashes = [r for t in result.layers[1]['tools'].values() for r in t['geometry']
               if isinstance(r.get('follow'), Point)]
    assert len(flashes) == 1
    assert flashes[0]['follow'].distance(Point(2.5, 2.5)) < 1e-12
    assert copper.covers(Point(10.5, 2.5))
    assert not copper.covers(Point(11.5, 2.5))
    from test_allegro_brd_parser import export_layer
    exported, code = export_layer(result.layers[1], circle_steps=128)
    assert code.count('D03*') == 1  # the partially cut circle must not be restored by a flash
    assert unary_union(exported.solid_geometry).symmetric_difference(copper).area < 1e-5


def test_documented_94_decal_without_linestyle_field():
    text = board('''*PARTDECAL*
D M 0 0 1 0 0 0 0
OPEN 2 0.2 26
0 0
2 0
*PARTTYPE*
P D UND 0 0 0 0
*PART*
U1 P 0 0 0 U N 0 -1 0 -1 0''').replace('V9.5', 'V9.4')
    result = parse(text)
    assert unary_union(result.layers[26]['solid_geometry']).covers(Point(1, 0))


def test_multilayer_defaults_and_blind_via_span():
    result = parse(board('''*PARTDECAL*
D M 0 0 0 1 1 0 0
T0 0 0 0 1
PAD 0 4
-2 1 R 0.5 P
-1 2 R
0 3 S 0
3 4 S 0
*PARTTYPE*
P D UND 0 0 0 0
*PART*
U1 P 0 0 0 U N 0 -1 0 -1 0
*VIA*
V 0.3 3 2 3
-2 0.8 R
-1 1 R
0 1.2 R
*ROUTE*
*SIGNAL* N
U1.1 U2.1
10 0 2 0.1 0 V
11 0 65 0.1 0''').replace('MAXIMUMLAYER 2', 'MAXIMUMLAYER 4'))
    for layer, area in ((1, 3.1416 / 4), (2, 3.1416), (3, 16), (4, 9)):
        pad = unary_union(result.layers[layer]['solid_geometry']).intersection(box(-3, -3, 3, 3))
        assert pad.area == pytest.approx(area, rel=0.002)
    assert result.drill_groups['PTH_2-3'][1]['tooldia'] == pytest.approx(0.3)
    assert not unary_union(result.layers[1]['solid_geometry']).covers(Point(10, 0))


@pytest.mark.parametrize('corner', [0.2, -0.2])
def test_rounded_and_chamfered_finger_pads(corner):
    result = parse(board('''*PARTDECAL*
D M 0 0 0 1 1 0 0
T0 0 0 0 1
PAD 0 3
-2 2 RF 0 4 0 %s 0 P
-1 0 R
0 0 R
*PARTTYPE*
P D UND 0 0 0 0
*PART*
U1 P 0 0 0 U N 0 -1 0 -1 0''' % corner))
    expected = 8 - ((4 - math.pi) if corner > 0 else 2) * corner ** 2
    assert unary_union(result.layers[1]['solid_geometry']).area == pytest.approx(expected, abs=0.0001)
    flashes = [r for t in result.layers[1]['tools'].values() if t['type'] == 'AB' for r in t['geometry']]
    assert len(flashes) == 1
    assert flashes[0]['follow'].equals(Point(0, 0))
    assert flashes[0]['solid'].equals(unary_union(result.layers[1]['solid_geometry']))
    from test_allegro_brd_parser import export_layer
    exported, code = export_layer(result.layers[1], units='IN')
    assert code.count('D03*') == 1
    assert unary_union(exported.solid_geometry).symmetric_difference(flashes[0]['solid']).area < 0.0001


@pytest.mark.parametrize(('body', 'message'), [
    ('*POUR*\nP saved-outline-only', 'pour'),
    ('*ROUTE*\n*SIGNAL* N\nU1.1 U2.1\n0 0 1 1 0', 'end'),
    ('*LINES*\nA COPPER 0 0 1 1\nCOPCLS 5 0 0 1\n0 0', 'truncated'),
    ('*PART*\nU1 MISSING 0 0 0 U N 0 -1 0 -1 0', 'part type'),
    ('*ROUTE*\n*SIGNAL* N\nU1.1 U2.1\n0 0 1 nan 0\n1 0 65 1 0', 'finite'),
    ('*TEXT*\n0 0 0 1 1 0.1 N LEFT DOWN\nRegular PADS\nCOPPER', 'copper text'),
    ('*MISC*\nLAYER DATA\n{\nLAYER 1\n{\nLAYER_TYPE PLANE\n}\n}', 'plane'),
    ('*ROUTE*\n*SIGNAL* N\nU1.1 U2.1\n0 0 1 1 0 THERMAL GARBAGE\n1 0 65 1 0', 'route'),
    ('*LINES*\nA COPPER 0 0 1 1\nCOPCLS 4 0 0 1\n0 0\n5 0\n5 5\n0 5', 'closed'),
    ('*ROUTE*\n*SIGNAL* N\n0 0 1 1 0\n1 0 65 1 0', 'pin pair'),
])
def test_unsafe_or_incomplete_geometry_is_rejected(body, message):
    from appParsers.ParsePADS import PADSParseError
    with pytest.raises(PADSParseError, match=message):
        parse(board(body))


def test_native_binary_and_unrelated_asc_are_not_ascii_boards():
    from appParsers.ParsePADS import PADSParser, PADSParseError
    for text in ('Version 4\nSHEET 1 880 680', '*PADS-PCB*\n*END*',
                 '!PADS-POWERPCB-V2024.2-METRIC!\n*END*'):
        with pytest.raises(PADSParseError):
            parse(text)
    with pytest.raises(PADSParseError, match='END'):
        parse(board('').replace('*END*', ''))


@pytest.mark.parametrize('damage', ['footer', 'directory', 'reference',
                                   'route_cells', 'route_layer', 'drawing_shape', 'pour_arc', 'drill_span'])
def test_native_sdb_rejects_truncation_and_out_of_range_references(damage, tmp_path):
    from appParsers.ParsePADS import PADSParseError, PADSParser
    from appParsers.ParsePADSBinary import PADSBinaryReader

    data = bytearray((FIXTURES / 'LCORE_2.pcb').read_bytes())
    source = PADSBinaryReader(data)
    if damage == 'footer':
        data = data[:-1]
    elif damage == 'directory':
        struct.pack_into('<I', data, 26, 0xffffffff)
    elif damage == 'reference':
        # C4's successor carries its parttype index, not an absolute file pointer.
        struct.pack_into('<I', data, 50190 - 44 + 112 + 4, 0xffffffff)
    elif damage == 'route_cells':
        section = source.sections[62]
        offset = section['offset'] + (32 + (section['count'] - 1) * 48 + 36) % section['size']
        struct.pack_into('<I', data, offset, 0xffffffff)
    elif damage == 'route_layer':
        struct.pack_into('<H', data, source.sections[63]['offset'], 0xffff)
    elif damage == 'drawing_shape':
        section = source.sections[11]
        data[section['offset'] + (section['size'] - 12 + 20) % section['size']] = 0xff
    elif damage == 'pour_arc':
        struct.pack_into('<I', data, source.sections[53]['offset'] + 4, 1)
    else:
        # Referenced via stack 55: the exporter mustn't silently turn a partial span into through drilling.
        data[source.sections[4]['offset'] - 28 + 55 * 64 + 59] = 2
    filename = tmp_path / 'damaged.pcb'
    filename.write_bytes(data)
    with pytest.raises(PADSParseError, match='PADS binary'):
        PADSParser().parse_file(filename)


@pytest.mark.parametrize(('name', 'counts', 'drills', 'slots'), [('LCORE_2', (16, 4, 0), 20, 0),
                                                                 ('TMS1mmX19', (61, 22, 3), 83, 3)])
def test_native_board_imports_copper_outline_and_positioned_drilling(name, counts, drills, slots):
    result, reader = real(name, '.pcb'), native_reader(name)
    assert result.layers[1]['name'] == 'Top'
    assert result.layers[2]['name'] == 'Bottom'
    assert all(unary_union(result.layers[layer]['solid_geometry']).area > 1 for layer in (1, 2))
    assert result.board_outline.is_valid and result.board_outline.area > 100
    assert sum(len(tool['drills']) for tools in result.drill_groups.values()
               for tool in tools.values()) == drills
    assert sum(len(tool['slots']) for tools in result.drill_groups.values()
               for tool in tools.values()) == slots
    # The plating is the stored flag: (plated round, non-plated, plated slots), counting each hole once.
    assert (sum(h['plated'] and not h['slot_length'] for h in reader.holes),
            sum(not h['plated'] for h in reader.holes),
            sum(h['plated'] and bool(h['slot_length']) for h in reader.holes)) == counts

    def groups(board):
        return {group: sorted((round(tool['tooldia'], 6), len(tool['drills']), len(tool['slots']))
                              for tool in tools.values()) for group, tools in board.drill_groups.items()}
    assert groups(result) == groups(real(name, '.asc'))
    if name == 'TMS1mmX19':
        # Same pad width as the bore in both: only the stored flag tells the plated 250 mil hole
        # from the non-plated M3 hole.
        plating = {reader.decals[part['decal']]['name']: hole['plated'] for part in reader.placements
                   for hole in reader.holes if hole['reference'] == part['reference']}
        assert plating['MTHOLE-M3-3.2MM'] is False and plating['MTHOLE-250MIL'] is True
    else:
        assert len(result.pours) == 2
        assert {pour['layer'] for pour in result.pours} == {1, 2}
        assert all(pour['net'] == 'GND' for pour in result.pours)
        npth = [tool for group, tools in result.drill_groups.items() if group.startswith('NPTH')
                for tool in tools.values()]
        assert sorted((round(tool['tooldia'], 6), len(tool['drills'])) for tool in npth) == [(1.6, 2), (2.0, 2)]
        hole = Point(5.17633, -19.31867)
        assert any(point.distance(hole) < 1e-6 for tool in npth for point in tool['drills'])
        # Native via row 74 has a 0.2 mm bore and 0.4 mm copper land on both sides.
        vias = [tool for tool in result.drill_groups['PTH_1-2'].values()]
        assert [round(tool['tooldia'], 6) for tool in vias] == [0.2]
        assert any(point.distance(Point(-2.65, -10.05)) < 1e-6 for tool in vias for point in tool['drills'])
        for layer in (1, 2):
            assert unary_union(result.layers[layer]['solid_geometry']).covers(Point(-2.65, -10.05).buffer(0.19))


@pytest.mark.parametrize('name', ['LCORE_2', 'TMS1mmX19'])
def test_native_board_equals_its_ascii_twin_in_saved_copper_outline_and_silkscreen(name):
    ascii_board, native = real(name, '.asc'), real(name, '.pcb')
    assert ascii_board.board_outline.symmetric_difference(native.board_outline).area < 1e-7
    for layer in (1, 2):
        expected_pads = unary_union([geo for target, net, geo in ascii_board._pad_items if target == layer])
        actual_pads = unary_union([geo for target, net, geo in native._pad_items if target == layer])
        assert expected_pads.symmetric_difference(actual_pads).area < 1e-7
        expected_tracks = unary_union([geo.buffer(width / 2) for target, net, geo, width in
                                       ascii_board._track_items if target == layer])
        actual_tracks = unary_union([geo.buffer(width / 2) for target, net, geo, width in
                                     native._track_items if target == layer])
        assert expected_tracks.symmetric_difference(actual_tracks).area < 1e-7
        for net in ('GND', '+5V', '+3.3V'):
            expected = unary_union([geo for target, owner, geo in ascii_board._pad_items
                                    if target == layer and owner == net])
            actual = unary_union([geo for target, owner, geo in native._pad_items
                                  if target == layer and owner == net])
            assert expected.symmetric_difference(actual).area < 1e-7
        if name == 'TMS1mmX19':
            # 10 native RT/ST/RA pad rows (vias and the JTAG header) and no pours: the whole copper is equal.
            assert unary_union(ascii_board.layers[layer]['solid_geometry']).symmetric_difference(
                unary_union(native.layers[layer]['solid_geometry'])).area < 1e-6
    for layer in (26, 27):
        # The strokes are many thousand polygons: a union of them is slow, so compare each polygon's extent and area.
        def strokes(board):
            return sorted(tuple(round(c, 2) for c in p.bounds) for p in board.layers[layer]['solid_geometry'])

        def area(board):
            return sum(p.area for p in board.layers[layer]['solid_geometry'])
        assert area(ascii_board) > 1 and area(native) == pytest.approx(area(ascii_board), rel=1e-6)
        assert len(strokes(native)) == len(strokes(ascii_board))
        assert max(abs(a - b) for x, y in zip(strokes(native), strokes(ascii_board)) for a, b in zip(x, y)) < 0.011
    assert not any('not imported' in warning and 'decal text' in warning for warning in native.warnings)


def test_native_pour_keeps_the_files_own_clearance_from_foreign_copper_and_the_outline():
    native = real('LCORE_2', '.pcb')
    assert len(native.pours) == 2
    for pour in native.pours:
        foreign = unary_union([geo for layer, owner, geo in native._pad_items
                               if layer == pour['layer'] and owner != pour['net']])
        # The binary's COPPER_TO_* and OUTLINE_TO_COPPER rules are 225000 BASIC units = 0.15 mm (the .asc of
        # another save state holds 0.3 mm); the tolerance covers polygonal round pads.
        assert pour['geometry'].distance(foreign) >= 0.15 - 1e-3
        assert pour['geometry'].distance(native.board_outline.boundary) >= 0.15 - 1e-3


@pytest.mark.parametrize('damage', ['layer_rule', 'copper_pin_unreachable'])
def test_native_unproven_rules_or_unreachable_copper_reject_the_file(damage):
    from appParsers.ParsePADS import PADSParseError
    from appParsers.ParsePADSBinary import PADSBinaryReader

    source = native_reader('LCORE_2')
    data = bytearray(source.data)
    if damage == 'layer_rule':
        struct.pack_into('<I', data, source.sections[41]['offset'], 1)
    else:
        # Drop pin B of connection 0 from the junction set of its copper (relationship direction 1).
        section, cursor = source.sections[49], source.sections[49]['offset']
        pin = [i for i, j in enumerate(source._junctions()) if j[1] == 0x16 and (j[2], j[3]) == source._edges()[0][1]][-1]
        for net in (name for name in source.nets if name and name != '___Unassigned_Obstacles_'):
            for direction in range(2):
                count = struct.unpack_from('<I', data, cursor)[0]
                cursor += 4
                for _ in range(count):
                    owner, members = struct.unpack_from('<II', data, cursor)
                    cursor += 8
                    if direction == 1 and owner == 0x18000000:
                        for at in range(cursor, cursor + 4 * members, 4):
                            if struct.unpack_from('<I', data, at)[0] == 0x3c000000 | pin:
                                struct.pack_into('<I', data, at, 0x3c000000 | (pin + 1) % section['count'])
                    cursor += 4 * members
    with pytest.raises(PADSParseError, match='PADS binary'):
        PADSBinaryReader(data).as_ascii()


def parameters_offset(reader):
    return reader.sections[3]['offset'] + reader.sections[1]['size'] - 12


def test_native_teardrop_copper_matches_the_ascii_tapers():
    from appParsers.ParsePADS import PADSParser

    def parse_without_pours(text):
        head = text.split('*POUR*')[0]
        if '*MISC*' not in head:
            # The .asc keeps its design rules in *MISC* after *POUR*; its plane layers need the pours, so keep
            # only the rule rows.
            head += '*MISC*\n' + '\n'.join(re.findall(r'^[A-Z_]+_TO_[A-Z_]+ \d+$', text, flags=re.M)) + '\n'
        return PADSParser().parse_text(head + '*END*')

    ascii_board = parse_without_pours(patched_lcore_text())
    native = parse_without_pours(native_reader('LCORE_2').as_ascii())
    # Pin-pair paths give the same tapers over junctions as the .asc: all routed copper is exact.
    for layer in (1, 2):
        expected = unary_union(ascii_board.layers[layer]['solid_geometry'])
        actual = unary_union(native.layers[layer]['solid_geometry'])
        assert expected.symmetric_difference(actual).area < 1e-6


def _shape_byte(reader, shape):
    """Offset of the shape code of the first padstack pad row that has this shape (or any row of the first
    referenced padstack when shape is None)."""
    base = reader.sections[4]['offset'] - 28
    layer_base = base + reader.sections[4]['size'] + 24
    for decal in reader.decals:
        for index in decal['stacks'].values():
            stack = reader.padstacks[index]
            start = struct.unpack_from('<I', reader.data, base + index * 64 + 52)[0]
            for k, layer in enumerate(stack['layers']):
                if shape is None or layer['shape'] == shape:
                    return layer_base + (start + k) * 24 + (24 if reader.version == 0x2027 else 0) + 1
    raise AssertionError('no pad row found')


@pytest.mark.parametrize('damage', ['thermal_marker', 'teardrop_order', 'spoke_array_row', 'old_version_thermal_row'])
def test_native_unproven_thermal_or_teardrop_data_rejects_the_file(damage):
    from appParsers.ParsePADS import PADSParseError
    from appParsers.ParsePADSBinary import PADSBinaryReader

    name = 'LCORE_2' if damage in ('teardrop_order', 'old_version_thermal_row') else 'TMS1mmX19'
    source = native_reader(name)
    data = bytearray(source.data)
    if damage == 'thermal_marker':
        struct.pack_into('<i', data, parameters_offset(source) + 988, 7)
    elif damage == 'teardrop_order':
        word = struct.unpack_from('<I', data, parameters_offset(source) + 844)[0]
        struct.pack_into('<I', data, parameters_offset(source) + 844, (word & ~0x3ff) | 91)
    elif damage == 'spoke_array_row':
        data[_shape_byte(source, 6)] = 9  # SA has no sample in any fixture
    else:
        data[_shape_byte(source, None)] = 6  # 0x2026 thermal rows have no sample either
    with pytest.raises(PADSParseError, match='PADS binary'):
        PADSBinaryReader(data).as_ascii()


def test_native_pour_clearance_does_not_use_default_drawing_linewidth():
    from appParsers.ParsePADSBinary import PADSBinaryReader

    data = bytearray((FIXTURES / 'LCORE_2.pcb').read_bytes())
    # The board setup word at +36 is LINEWIDTH, not an electrical clearance rule: the rules that drive the pour
    # fill stay as they are (the pour fill itself is a pure function of these rows).
    def rules(blob):
        return re.findall(r'^[A-Z_]+_TO_[A-Z_]+ \S+$', PADSBinaryReader(blob).as_ascii(), flags=re.M)
    before = rules(data)
    struct.pack_into('<i', data, 10 + 74 * 16 + 36, 15000000)
    assert before and rules(data) == before


ROMANSIM = 'Regular <Romansim Stroke Font>'


def label_board(decal_labels, parts, part_labels=None):
    """One label-only decal D (labels given as (visible, x, y, layer, attribute)) and its parts."""
    def record(label):
        visible, x, y, layer, attribute = label
        return '%s %s %s 0 %d 2 0.2 N CENTER CENTER\n%s\n%s' % (visible, x, y, layer, ROMANSIM, attribute)
    text = '*PARTDECAL*\nD M 0 0 0 0 0 0 %d\n%s\n*PARTTYPE*\nP D UND 0 0 0 0\n*PART*\n' % (
        len(decal_labels), '\n'.join(record(label) for label in decal_labels))
    for reference, x, y, mirror in parts:
        own = (part_labels or {}).get(reference, [])
        text += '%s P %s %s 0 U %s 0 -1 0 -1 %d\n%s\n' % (
            reference, x, y, mirror, len(own), '\n'.join(record(label) for label in own))
    return board(text)


def copper(result):
    return [p for layer in result.copper_layers for p in result.layers.get(layer, {}).get('solid_geometry', [])]


def layer_bounds(result, layer):
    return unary_union(result.layers[layer]['solid_geometry']).bounds


def test_ref_des_label_is_drawn_near_each_placed_part_on_its_side():
    result = parse(label_board([('VALUE', 0, 5, 1, 'Ref.Des.')],
                               [('U1', 10, 20, 'N'), ('U2', -10, 20, 'M')]))
    # A copper label is silkscreen: layer 1 -> top silkscreen 26, mirrored to the bottom -> 27.
    top, bottom = layer_bounds(result, 26), layer_bounds(result, 27)
    assert 8.5 < top[0] and top[2] < 11.5 and 24 < top[1] and top[3] < 26.5
    assert -11.5 < bottom[0] and bottom[2] < -8.5 and 24 < bottom[1] and bottom[3] < 26.5
    assert not copper(result)
    assert any('silkscreen' in warning for warning in result.warnings)


def test_invisible_label_draws_nothing_and_visibility_selects_name_or_value():
    labels = [('NONE', 0, 5, 1, 'Ref.Des.'), ('VALUE', 0, -5, 1, 'Ref.Des.')]
    top = layer_bounds(parse(label_board(labels, [('U1', 0, 20, 'N')])), 26)
    assert top[3] < 16
    widths = {}
    for visible in ('VALUE', 'NAME', 'BOTH'):
        bounds = layer_bounds(parse(label_board([(visible, 0, 0, 1, 'Ref.Des.')], [('U1', 0, 0, 'N')])), 26)
        widths[visible] = bounds[2] - bounds[0]
    assert widths['VALUE'] < widths['NAME'] < widths['BOTH']


def test_part_label_replaces_the_decal_default_of_the_same_attribute():
    result = parse(label_board([('VALUE', 0, 5, 1, 'Ref.Des.')],
                               [('U1', 0, 20, 'N'), ('U2', 0, 40, 'N')],
                               {'U1': [('VALUE', 0, -5, 1, 'Ref.Des.')]}))
    bounds = [polygon.bounds for polygon in result.layers[26]['solid_geometry']]
    assert any(14 < b[1] and b[3] < 16.5 for b in bounds)
    assert not any(24 < b[1] and b[3] < 26.5 for b in bounds)
    assert any(44 < b[1] and b[3] < 46.5 for b in bounds)


def test_unvalued_attribute_label_is_skipped_with_a_warning():
    result = parse(label_board([('VALUE', 0, 5, 1, 'Ref.Des.'), ('VALUE', 0, -5, 1, 'Tolerance')],
                               [('U1', 0, 20, 'N')]))
    assert layer_bounds(result, 26)[1] > 24
    assert any('Tolerance' in warning for warning in result.warnings)
    with pytest.raises(Exception, match='PADS'):
        parse(label_board([('SOMETIMES', 0, 5, 1, 'Ref.Des.')], [('U1', 0, 20, 'N')]))


def test_labels_on_inner_or_all_layers_are_skipped_and_other_fonts_never_fail():
    four = lambda text: text.replace('MAXIMUMLAYER 2', 'MAXIMUMLAYER 4')
    kept = ('VALUE', 0, 5, 26, 'Ref.Des.')
    for layer in (0, 2):
        result = parse(four(label_board([kept, ('VALUE', 0, -5, layer, 'Ref.Des.')], [('U1', 0, 20, 'N')])))
        assert layer_bounds(result, 26)[1] > 24 and not copper(result)
        assert any('inner' in warning for warning in result.warnings)
    other = label_board([('VALUE', 0, -5, 1, 'Ref.Des.'), kept], [('U1', 0, 20, 'N')])
    result = parse(other.replace(ROMANSIM, 'Regular <Arial>', 1))
    assert layer_bounds(result, 26)[1] > 24 and not copper(result)
    assert any('Arial' in warning for warning in result.warnings)


def test_real_lcore_labels_are_silkscreen_and_never_copper():
    text = lcore_saved_components()
    hidden = '\n'.join('NONE' + line[5:] if line.startswith('VALUE ') else line for line in text.splitlines())
    with_labels, without = parse(text), parse(hidden)
    for layer in (1, 2):
        assert unary_union(with_labels.layers[layer]['solid_geometry']).symmetric_difference(
            unary_union(without.layers[layer]['solid_geometry'])).area < 1e-9
    for reference, side in (('R2', 26), ('C4', 27)):
        row = next(line.split() for line in text.splitlines() if line.startswith(reference + ' '))
        part = Point(int(row[2]) / 1500000, int(row[3]) / 1500000)
        added = unary_union(with_labels.layers[side]['solid_geometry']).difference(
            unary_union(without.layers[side]['solid_geometry']))
        assert added.intersection(part.buffer(3)).area > 0.1


def _label_and_text_records(reader):
    """Section-8 offsets of a part label chain member and of a board free text."""
    base = reader.sections[8]['offset']
    head = reader._read(reader.sections[22]['offset'] - 44 + 96, 'i')[0]
    text = next(base + k * 72 for k in range(reader.sections[8]['count'])
                if reader._read(base + k * 72 + 40, 'III')[2] == 0
                and (reader._read(base + k * 72 + 60)[0] >> 16) & 255 == 0x20)
    return base + head * 72, text


@pytest.mark.parametrize('damage', ['cycle', 'range', 'attribute', 'justification'])
def test_native_corrupt_or_unproven_label_records_reject_the_file(damage, tmp_path):
    import struct
    from appParsers.ParsePADS import PADSParseError, PADSParser
    from appParsers.ParsePADSBinary import PADSBinaryReader

    data = bytearray((FIXTURES / 'LCORE_2.pcb').read_bytes())
    label, _ = _label_and_text_records(PADSBinaryReader(data))
    if damage == 'cycle':
        struct.pack_into('<I', data, label + 48, 0x08000000 | (label - PADSBinaryReader(data).sections[8]['offset']) // 72)
    elif damage == 'range':
        struct.pack_into('<I', data, label + 48, 0x08ffffff)
    elif damage == 'attribute':
        data[label + 62] = (data[label + 62] & ~7) | 4
    else:
        data[label + 63] = 0x02
    filename = tmp_path / 'damaged.pcb'
    filename.write_bytes(data)
    with pytest.raises(PADSParseError, match='PADS binary'):
        PADSParser().parse_file(filename)


def test_native_board_text_keeps_its_saved_justification():
    from appParsers.ParsePADSBinary import PADSBinaryReader

    data = bytearray((FIXTURES / 'LCORE_2.pcb').read_bytes())
    _, text = _label_and_text_records(PADSBinaryReader(data))
    data[text + 63] = 0x21  # CENTER horizontally, UP vertically: the kind test must ignore these bits
    lines = PADSBinaryReader(data).as_ascii().splitlines()
    assert any(lines[i].endswith(' CENTER UP') and 'Romansim' in lines[i + 1] for i in range(len(lines) - 1))


class TestPadsImportWorker:
    """The PADS import path of the app (file chooser, worker, layer dialog, objects, export), with a small app stub."""

    @classmethod
    def setup_class(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6 import QtWidgets
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setup_method(self):
        from PyQt6 import QtWidgets
        from appHandlers.appIO import appIO
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock())
        self.app.projects = SimpleNamespace(is_busy=lambda: False, inform_busy=Mock(),
                                            active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
        self.app.project_filename, self.app.should_we_save = None, False
        self.io = appIO(self.app)
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.app.ui = QtWidgets.QWidget()
        self.app.get_last_folder = Mock(return_value='')

    def teardown_method(self):
        # an import that a test leaves pending keeps its window modal layer dialog open: cancel it like the user
        from PyQt6 import QtWidgets
        for dialog in self.app.ui.findChildren(QtWidgets.QDialog):
            dialog.reject()
        self.qapp.processEvents()

    def _export_options(self):
        from appObjects.GerberObject import GerberObject
        from appObjects.ExcellonObject import ExcellonObject
        from defaults import AppDefaults

        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['gerber_exp_units'] = 'IN'
        self.app.options['gerber_exp_decimals'] = 6
        self.app.options['excellon_exp_units'] = 'METRIC'
        self.app.options['excellon_exp_decimals'] = 6
        self.app.decimals = 4
        self.app.plotcanvas = Mock()
        self.app.version = self.app.version_date = 'test'
        self.app.proc_container = Mock()
        self.app.resource_location = 'assets/resources'
        self.app.f_handlers = self.io

        def exported(name, filename, local_use, use_thread):
            local_use.app = self.app
            local_use.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(local_use, *args, **kwargs)
            return type(self.io).export_gerber(self.io, name, filename, local_use, use_thread)

        def exported_drills(name, filename, local_use, use_thread):
            local_use.app = self.app
            local_use.export_excellon = lambda *args, **kwargs: ExcellonObject.export_excellon(
                local_use, *args, **kwargs)
            # The test factory supplies namespaces, but both production exporters run unchanged.
            with patch('appHandlers.appIO.ExcellonObject', SimpleNamespace):
                return type(self.io).export_excellon(self.io, name, filename, local_use, use_thread)

        self.io.export_gerber = Mock(side_effect=exported)
        self.io.export_excellon = Mock(side_effect=exported_drills)

    def _run(self, job):
        result = job['fcn'](*job['params'])
        self.qapp.processEvents()
        return result

    def test_chooser_imports_layers_and_drills_in_project_units(self):
        from PyQt6 import QtWidgets
        from appGUI.ImportDialog import ImportDialog
        from appParsers.ParseGerber import Gerber

        self._export_options()
        filename = str(FIXTURES / 'ClaySight_MK1.asc')
        dialog = ImportDialog(self.app)
        dialog.select('pads')
        assert dialog.ext_label.text() == '.asc .pcb'
        dialog.accept()
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(filename, '')):
            dialog.run_selected()
        job = self.app.worker_task.emit.call_args.args[0]
        with patch.object(self.io, 'pcbdoc_parsed') as signal:
            job['fcn'](*job['params'])
        source, parsed, request = signal.emit.call_args.args
        assert source == filename

        def choose(selection):
            assert selection.windowTitle() == 'Import PADS PCB'
            for checkbox in selection.findChildren(QtWidgets.QCheckBox):
                checkbox.setChecked(checkbox.text() in ('1 (Top Layer)', 'PTH_1-2'))
            return QtWidgets.QDialog.DialogCode.Accepted

        self.app.app_units = 'IN'
        with patch.object(QtWidgets.QDialog, 'exec', choose):
            self.io.on_pcbdoc_parsed(filename, parsed, request)
        job = self.app.worker_task.emit.call_args.args[0]
        job['fcn'](*job['params'])
        assert [kind for kind, obj in self.objects] == ['gerber', 'excellon']
        copper, holes = [obj for kind, obj in self.objects]
        assert copper.units == 'IN'
        assert unary_union(copper.solid_geometry).area == pytest.approx(
            unary_union(parsed.layers[1]['solid_geometry']).area / 25.4 ** 2)
        gerber = Gerber(self.app)
        assert gerber.parse_lines(copper.source_file.splitlines()) != 'fail'
        assert unary_union(gerber.solid_geometry).symmetric_difference(unary_union(copper.solid_geometry)).area < 0.001
        assert sum(len(tool['drills']) for tool in holes.tools.values()) == 124
        assert sum(len(tool['slots']) for tool in holes.tools.values()) == 16
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_called_once_with('pads', filename)

    def test_rejected_import_reports_failure_and_releases_worker_request(self, tmp_path):
        from PyQt6 import QtWidgets
        from appCommon.Common import AppLogging

        # Use the production method, without adding a global logging handler.
        logger = AppLogging.__new__(AppLogging)
        logger.app = SimpleNamespace(inform_shell=Mock())
        logger._log_level = 1
        logger._log = Mock()
        self.io.log = self.app.log = logger
        malformed = tmp_path / 'malformed.asc'
        malformed.write_text('not a PADS board\n')
        invalid_pour = tmp_path / 'invalid_pour.asc'
        invalid_pour.write_text('!PADS-POWERPCB-V2005.0-METRIC!\n*PCB*\nMAXIMUMLAYER 2\n'
                                '*POUR*\nP saved-outline-only\n*END*\n')
        truncated = tmp_path / 'truncated.pcb'
        truncated.write_bytes((FIXTURES / 'LCORE_2.pcb').read_bytes()[:-1])
        for filename, detail in ((invalid_pour, 'pour'), (malformed, 'PADS'), (truncated, 'footer')):
            self.app.inform.emit.reset_mock()
            self.io.on_file_import_pads(str(filename))
            job = self.app.worker_task.emit.call_args.args[0]
            with patch.object(self.io, 'pcbdoc_parsed') as signal:
                assert self._run(job) == 'fail'
            signal.emit.assert_not_called()
            message = self.app.inform.emit.call_args.args[0]
            assert '[ERROR_NOTCL]' in message and 'Import failed.' in message and detail in message
            assert self.objects == []
            self.app.file_opened.emit.assert_not_called()
            assert not any(dialog.isVisible() for dialog in self.app.ui.findChildren(QtWidgets.QDialog))
        self.io.on_file_import_kicad_pcb('next.kicad_pcb')
        assert self.app.worker_task.emit.call_args.args[0]['params'] == ['next.kicad_pcb']
        self.app.projects.inform_busy.assert_not_called()

    @pytest.mark.parametrize('name', ['LCORE_2.pcb', 'TMS1mmX19.pcb'])
    def test_native_board_exports_both_copper_layers_and_drills(self, name, tmp_path):
        from PyQt6 import QtWidgets
        from shapely.affinity import scale
        from appParsers.ParseGerber import Gerber
        from appParsers.ParseExcellon import Excellon
        from appParsers.ParsePADS import PADSParser

        self._export_options()
        stem, extension = name.split('.')
        filename = str(FIXTURES / name)
        # The parse itself is tested above: the session's parse of the fixture stands in for the worker's parse.
        with patch.object(PADSParser, 'parse_file', lambda parser, path, **kwargs: real(stem, '.' + extension)), \
                patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(filename, '')) as chooser:
            self.io.on_file_import_pads()
            assert '*.pcb' in chooser.call_args.kwargs['filter']
            job = self.app.worker_task.emit.call_args.args[0]
            with patch.object(self.io, 'pcbdoc_parsed') as signal:
                assert self._run(job) != 'fail'
        source, parsed, request = signal.emit.call_args.args

        def choose(selection):
            selection.show()
            self.qapp.processEvents()
            reports = [label.text().lower() for label in selection.findChildren(QtWidgets.QLabel)
                       if label.isVisible()]
            disclosures = [('romansim', 'substitut', 'glyph', 'shapes', 'metrics', 'case is preserved'),
                           ('plating', 'read from the file'), ('teardrop', 'not reproduced')]
            if name.startswith('LCORE'):
                disclosures.append(('pours', 'reconstructed', 'not original pads'))
            for disclosure in disclosures:
                assert any(all(term in report for term in disclosure) for report in reports), \
                    'Missing import-dialog disclosure: %s' % (disclosure,)
            for checkbox in selection.findChildren(QtWidgets.QCheckBox):
                checkbox.setChecked(checkbox.text() in ('1 (Top)', '2 (Bottom)', 'NPTH_1-2', 'PTH_1-2'))
            return QtWidgets.QDialog.DialogCode.Accepted

        self.app.app_units = 'IN'
        with patch.object(QtWidgets.QDialog, 'exec', choose):
            self.io.on_pcbdoc_parsed(source, parsed, request)
        job = self.app.worker_task.emit.call_args.args[0]
        assert self._run(job) != 'fail'
        assert [kind for kind, obj in self.objects] == ['gerber', 'gerber', 'excellon', 'excellon']
        copper_layers = [obj for kind, obj in self.objects if kind == 'gerber']
        drills = [obj for kind, obj in self.objects if kind == 'excellon']
        assert all(obj.units == 'IN' for kind, obj in self.objects)
        for copper in copper_layers:
            layer = int(copper.obj_options['name'].rsplit('_', 1)[1])
            gerber = Gerber(self.app)
            assert gerber.parse_lines(copper.source_file.splitlines()) != 'fail'
            artwork = unary_union(gerber.solid_geometry)
            assert artwork.symmetric_difference(unary_union(copper.solid_geometry)).area < 0.001
            for pour in (item['geometry'] for item in parsed.pours if item['layer'] == layer):
                assert pour.area > 1
                assert scale(pour, xfact=1 / 25.4, yfact=1 / 25.4, origin=(0, 0)).difference(artwork).area < 0.001
        if name.startswith('LCORE'):
            holes = next(obj for obj in drills if max(tool['tooldia'] for tool in obj.tools.values()) > 1 / 25.4)
            assert sorted((round(tool['tooldia'] * 25.4, 6), len(tool['drills']))
                          for tool in holes.tools.values()) == [(1.6, 2), (2.0, 2)]
            assert any(point.distance(Point(5.17633 / 25.4, -19.31867 / 25.4)) < 1e-9
                       for tool in holes.tools.values() for point in tool['drills'])
        else:
            assert sum(len(tool['drills']) for obj in drills for tool in obj.tools.values()) == 83
            assert sum(len(tool['slots']) for obj in drills for tool in obj.tools.values()) == 3
        for obj in drills:
            reparsed = Excellon(self.app)
            assert reparsed.parse_lines(obj.source_file.splitlines()) != 'fail'
            assert reparsed.units == 'MM'
            for kind in ('drills', 'slots'):
                assert (sum(len(tool[kind]) for tool in reparsed.tools.values())
                        == sum(len(tool[kind]) for tool in obj.tools.values()))
            assert (sorted(round(tool['tooldia'], 6) for tool in reparsed.tools.values())
                    == sorted(round(tool['tooldia'] * 25.4, 6) for tool in obj.tools.values()))
            points = [point for tool in reparsed.tools.values() for point in tool['drills']]
            assert all(min(point.distance(Point(saved.x * 25.4, saved.y * 25.4)) for point in points) < 0.00001
                       for tool in obj.tools.values() for saved in tool['drills'])
            slots = [LineString([start, end]) for tool in reparsed.tools.values() for start, end in tool['slots']]
            assert all(min(slot.hausdorff_distance(LineString([
                (start.x * 25.4, start.y * 25.4), (end.x * 25.4, end.y * 25.4)])) for slot in slots) < 0.00001
                for tool in obj.tools.values() for start, end in tool['slots'])
        self.app.file_opened.emit.assert_called_once_with('pads', filename)
