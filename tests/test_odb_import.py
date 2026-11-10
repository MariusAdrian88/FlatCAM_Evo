"""ODB++ importer: features, symbols, profiles, negative layers, drills and step-repeat (appParsers/ParseODB.py)."""
import math
import os

import io
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import unary_union

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets  # noqa: E402

from appHandlers.appIO import appIO  # noqa: E402
from appParsers.ParseODB import ODBJob, ODBParseError, ODBParser, _unlzw  # noqa: E402

STEPS = 64          # circle steps: the expected shapes use the same quad_segs


# ----------------------------------------------------------------------------------------------------------------------
# Job builder
# ----------------------------------------------------------------------------------------------------------------------

def surface(ring, holes=(), polarity='P'):
    """S record lines: a closed island (list of points) with holes."""
    lines = ['S %s 0' % polarity]
    for points, kind in [(ring, 'I')] + [(h, 'H') for h in holes]:
        lines.append('OB %s %s %s' % (points[0][0], points[0][1], kind))
        for x, y in list(points[1:]) + [points[0]]:
            lines.append('OS %s %s' % (x, y))
        lines.append('OE')
    lines.append('SE')
    return lines


def rect_ring(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def features(lines, symbols=(), units='MM'):
    """Text of a features / profile file. symbols: the names of the $0, $1, ... table."""
    head = ['UNITS=%s' % units, '#', '#Feature symbol names']
    head += ['$%d %s' % (i, name) for i, name in enumerate(symbols)]
    return '\n'.join(head + ['#', '#Layer features'] + list(lines)) + '\n'


def write_job(root, layers, steps, units='MM', symbols=None, font=None):
    """
    layers: list of dicts for the matrix (name; type, context, polarity, start, end optional).
    steps: {step: {'stephdr': extra text, 'profile': text, 'layers': {layer: {'features':, 'profile':, 'tools':}}}}
    symbols: {name: features text}
    font: text of fonts/standard
    """
    def put(rel, text):
        path = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)

    matrix = []
    for col, name in enumerate(steps, 1):
        matrix.append('STEP {\n  COL=%d\n  NAME=%s\n}\n' % (col, name))
    for row, layer in enumerate(layers, 1):
        matrix.append('LAYER {\n  ROW=%d\n  CONTEXT=%s\n  TYPE=%s\n  NAME=%s\n  POLARITY=%s\n  START_NAME=%s\n'
                      '  END_NAME=%s\n}\n' % (row, layer.get('context', 'BOARD'), layer.get('type', 'SIGNAL'),
                                              layer['name'], layer.get('polarity', 'POSITIVE'),
                                              layer.get('start', ''), layer.get('end', '')))
    put('matrix/matrix', ''.join(matrix))
    put('misc/info', 'UNITS=%s\nJOB_NAME=test\n' % units)
    for step, data in steps.items():
        put('steps/%s/stephdr' % step, 'UNITS=%s\n%s' % (data.get('units', units), data.get('stephdr', '')))
        if 'profile' in data:
            put('steps/%s/profile' % step, data['profile'])
        for layer, files in data.get('layers', {}).items():
            for kind, text in files.items():
                put('steps/%s/layers/%s/%s' % (step, layer, kind), text)
    for name, text in (symbols or {}).items():
        put('symbols/%s/features' % name, text)
    if font is not None:
        put('fonts/standard', font)
    return root


def parse(tmp_path, layers, steps, step=None, **kwargs):
    root = write_job(str(tmp_path / 'job'), layers, steps, **kwargs)
    return ODBParser(circle_steps=STEPS).parse_file(root, step=step)


def one_layer(tmp_path, lines, symbols=(), units='MM', user_symbols=None, name='top'):
    steps = {'pcb': {'layers': {name: {'features': features(lines, symbols, units)}}}}
    return parse(tmp_path, [{'name': name}], steps, units=units, symbols=user_symbols)


def copper(result, name='top'):
    return unary_union(result.layers[name]['solid_geometry'])


def same(a, b, tol=1e-6):
    return a.symmetric_difference(b).area < tol


def flat(points):
    return [c for p in points for c in (p.x, p.y)]


def cw_mirror(points, angle, mirror):
    """The pad transform of the spec: rotate clockwise, then x -> -x."""
    t = math.radians(angle)
    out = []
    for x, y in points:
        x, y = x * math.cos(t) + y * math.sin(t), -x * math.sin(t) + y * math.cos(t)
        out.append((-x if mirror else x, y))
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Symbols
# ----------------------------------------------------------------------------------------------------------------------

ARROW = [(1, 0), (3, 0), (3, 1), (2, 1), (2, 3), (1, 3)]


@pytest.mark.parametrize('orient, angle, mirror', [('1', 90, False), ('9 30', 30, True)])
def test_pad_orientation(tmp_path, orient, angle, mirror):
    symbol = features(surface(ARROW))
    result = one_layer(tmp_path, ['P 10 20 0 P 0 %s' % orient], symbols=['arrow'], user_symbols={'arrow': symbol})
    expected = Polygon([(x + 10, y + 20) for x, y in cw_mirror(ARROW, angle, mirror)])
    assert same(copper(result), expected)


@pytest.mark.parametrize('units, name, expected', [
    ('MM', 'r500', Point(0, 0).buffer(0.25, quad_segs=STEPS // 4)),
    ('INCH', 'r50', Point(0, 0).buffer(0.635, quad_segs=STEPS // 4)),
    ('MM', 'rect1000x500xr100',
     None),
    ('MM', 'donut_r1000x400', Point(0, 0).buffer(0.5, quad_segs=STEPS // 4).difference(
        Point(0, 0).buffer(0.2, quad_segs=STEPS // 4))),
    ('INCH', 'donut_r100x40', Point(0, 0).buffer(1.27, quad_segs=STEPS // 4).difference(
        Point(0, 0).buffer(0.508, quad_segs=STEPS // 4))),
    ('MM', 'ths1000x600x0x4x100', Point(0, 0).buffer(0.5, quad_segs=STEPS // 4).difference(
        Point(0, 0).buffer(0.3, quad_segs=STEPS // 4)).difference(
        unary_union([box(0, -0.05, 1, 0.05), box(-1, -0.05, 0, 0.05), box(-0.05, 0, 0.05, 1),
                     box(-0.05, -1, 0.05, 0)]))),
])
def test_symbol_size(tmp_path, units, name, expected):
    result = one_layer(tmp_path, ['P 0 0 0 P 0 0'], symbols=[name], units=units)
    shape = copper(result)
    if expected is None:        # rounded rectangle: 1.0 x 0.5 mm, corner radius 0.1 mm
        expected_area = 1.0 * 0.5 - (4 - math.pi) * 0.1 ** 2
        assert shape.area == pytest.approx(expected_area, rel=2e-3)
        assert shape.bounds == pytest.approx((-0.5, -0.25, 0.5, 0.25))
    else:
        assert same(shape, expected, tol=1e-4)


@pytest.mark.parametrize('name, inside, outside', [
    # rounded ring pieces: the cap (centre 0.371, 0.15; radius 0.1) leaves the outer corner at the gap empty
    ('thr1000x600x0x4x100', [(0.4, 0.07), (0.32, 0.32)], [(0.49, 0.06)]),
    ('ths1000x600x0x4x100', [(0.49, 0.06), (0.4, 0.07)], [(0.4, 0.04)]),
    # line thermal: side lines with round ends of diameter (os - is) / 2, gaps at the corners
    ('s_thr1000x600x45x4x100', [(0.45, 0.25), (0.45, 0)], [(0.49, 0.3)]),
    ('s_ths1000x600x45x4x100', [(0.49, 0.3), (0.45, 0.25)], [(0.45, 0.45)]),
    ('cross1000x1000x200x200x500x500xr', [(0.45, 0.05)], [(0.49, 0.09), (0.105, 0.105)]),
    ('cross1000x1000x200x200x500x500xs', [(0.49, 0.09)], [(0.105, 0.105)]),
    ('cross1000x1000x200x200x500x500xs50', [(0.49, 0.09), (0.105, 0.105)], [(0.12, 0.12)]),
])
def test_symbol_variants_keep_their_shape(tmp_path, name, inside, outside):
    shape = copper(one_layer(tmp_path, ['P 0 0 0 P 0 0'], symbols=[name]))
    assert all(shape.contains(Point(p)) for p in inside)
    assert not any(shape.contains(Point(p)) for p in outside)


@pytest.mark.parametrize('entry', ['r50I', 'r50 I'])
def test_symbol_unit_suffix_attached_or_separate(tmp_path, entry):
    result = one_layer(tmp_path, ['P 0 0 0 P 0 0'], symbols=[entry], units='MM')
    assert same(copper(result), Point(0, 0).buffer(0.635, quad_segs=STEPS // 4), tol=1e-4)


@pytest.mark.parametrize('lines', [['P 1 1 0 P 0 0', 'L 0 0 BROKEN'],
                                   ['P 1 1 0 P 0 0', 'S P 0', 'OB 0 0 I', 'OS 1 0']])
def test_malformed_or_unfinished_records_fail_the_job(tmp_path, lines):
    with pytest.raises(ODBParseError, match='(?i)steps/pcb/layers/top/features, line'):
        one_layer(tmp_path, lines, symbols=['r500'])


def test_diagonal_line_with_square_symbol_is_a_hexagon(tmp_path):
    result = one_layer(tmp_path, ['L 0 0 4 3 0 P 0'], symbols=['s1000'])
    expected = Polygon([(-0.5, -0.5), (0.5, -0.5), (4.5, 2.5), (4.5, 3.5), (3.5, 3.5), (-0.5, 0.5)])
    assert same(copper(result), expected)
    assert len(result.layers['top']['follow_geometry']) == 1


def test_line_with_a_rect_symbol_is_skipped_with_a_warning(tmp_path):
    result = one_layer(tmp_path, ['L 0 0 4 3 0 P 0', 'P 10 10 1 P 0 0'], symbols=['rect1000x1000', 's1000'])
    assert same(copper(result), box(9.5, 9.5, 10.5, 10.5))
    assert any('rect1000x1000' in warning for warning in result.warnings)


def test_user_symbol_loop_is_stopped(tmp_path):
    loop = features(['P 0 0 0 P 0 0', 'P 1 0 1 P 0 0'], symbols=['loop', 's1000'])
    result = one_layer(tmp_path, ['P 5 5 0 P 0 0'], symbols=['loop'], user_symbols={'loop': loop})
    assert same(copper(result), box(5.5, 4.5, 6.5, 5.5))


# ----------------------------------------------------------------------------------------------------------------------
# Polarity and surfaces
# ----------------------------------------------------------------------------------------------------------------------

def test_negative_feature_cuts_only_what_came_before(tmp_path):
    lines = ['P 0 0 0 P 0 0', 'P 0 0 1 N 0 0', 'P 0 0 2 P 0 0', 'P 5 0 1 N 0 0']
    result = one_layer(tmp_path, lines, symbols=['s2000', 's1000', 's200'])
    expected = box(-1, -1, 1, 1).difference(box(-0.5, -0.5, 0.5, 0.5)).union(box(-0.1, -0.1, 0.1, 0.1))
    assert same(copper(result), expected)


def test_surface_with_a_hole(tmp_path):
    outer, hole = rect_ring(0, 0, 10, 8), [(2, 2), (2, 4), (4, 4), (4, 2)]
    result = one_layer(tmp_path, surface(outer, [hole]))
    polygons = result.layers['top']['solid_geometry']
    assert len(polygons) == 1 and len(polygons[0].interiors) == 1
    assert set(polygons[0].interiors[0].coords) == set(hole + [hole[0]])
    assert polygons[0].area == pytest.approx(80 - 4)


# ----------------------------------------------------------------------------------------------------------------------
# Profiles and negative layers
# ----------------------------------------------------------------------------------------------------------------------

def plane_job(tmp_path, features_lines, layer_profile=None, step_profile=None, symbols=('s1000',)):
    layer_files = {'features': features(features_lines, symbols)}
    if layer_profile is not None:
        layer_files['profile'] = features(layer_profile)
    step = {'layers': {'plane': layer_files}}
    if step_profile is not None:
        step['profile'] = features(step_profile)
    return parse(tmp_path, [{'name': 'plane', 'type': 'POWER_GROUND', 'polarity': 'NEGATIVE'}], {'pcb': step})


def test_negative_plane_uses_the_layer_profile_and_the_step_holes(tmp_path):
    step_profile = surface(rect_ring(0, 0, 20, 20), [[(10, 10), (10, 14), (14, 14), (14, 10)]])
    layer_profile = surface(rect_ring(2, 2, 12, 12))
    result = plane_job(tmp_path, ['P 5 5 0 P 0 0'], layer_profile, step_profile)
    expected = box(2, 2, 12, 12).difference(box(10, 10, 14, 14)).difference(box(4.5, 4.5, 5.5, 5.5))
    assert same(copper(result, 'plane'), expected)


def test_negative_plane_without_a_boundary_is_not_listed(tmp_path):
    result = plane_job(tmp_path, ['P 5 5 0 P 0 0'])
    assert 'plane' not in result.layers
    assert any('plane' in warning for warning in result.warnings)


def test_negative_plane_features_do_not_leave_the_boundary(tmp_path):
    cutout = [(8, 8), (8, 12), (12, 12), (12, 8)]
    step_profile = surface(rect_ring(0, 0, 20, 20), [cutout])
    # a positive feature (erases), then a negative one (draws back) that crosses the outer edge and the cutout
    result = plane_job(tmp_path, ['P 6 10 0 P 0 0', 'P 3 10 1 N 0 0'], step_profile=step_profile,
                       symbols=('s8000', 's14000'))
    assert same(copper(result, 'plane'), box(0, 0, 20, 20).difference(box(8, 8, 12, 12)))


def test_profile_layer_is_a_thin_outline(tmp_path):
    hole = [(2, 1), (2, 2), (3, 2), (3, 1)]
    outer = rect_ring(0, 0, 10, 5)
    steps = {'pcb': {'profile': features(surface(outer, [hole]))}}
    result = parse(tmp_path, [{'name': 'top'}], steps)
    layer = result.layers['profile']
    assert 'profile' in result.default_layers
    assert unary_union(layer['solid_geometry']).area == pytest.approx((30 + 4) * 0.01, abs=1e-6)
    expected = MultiLineString([LineString(outer + [outer[0]]), LineString(hole + [hole[0]])])
    assert unary_union(layer['follow_geometry']).symmetric_difference(expected).length < 1e-9


# ----------------------------------------------------------------------------------------------------------------------
# Drills
# ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('units, factor, sizes', [('MM', 1.0, ('r800', 'r1000')), ('INCH', 25.4, ('r32', 'r40'))])
def test_drill_layer(tmp_path, units, factor, sizes):
    u = 1 / factor
    # the last pad resizes the small symbol by 200 microns (8 mil): it drills with the big tool
    lines = ['P %.10g %.10g 0 P 0 0' % (5 * u, 5 * u), 'P %.10g %.10g 0 P 2 0' % (8 * u, 5 * u),
             'L %.10g %.10g %.10g %.10g 1 P 0' % (10 * u, 10 * u, 14 * u, 10 * u),
             'P %.10g %.10g -1 0 %s P 0 0' % (20 * u, 5 * u, '200' if units == 'MM' else '8')]
    tools = 'UNITS=%s\nTOOLS {\n NUM=2\n TYPE=NON_PLATED\n}\n' % units
    steps = {'pcb': {'layers': {'drl': {'features': features(lines, sizes, units), 'tools': tools}}}}
    result = parse(tmp_path, [{'name': 'drl', 'type': 'DRILL', 'start': 'top', 'end': 'bottom'}], steps, units=units)
    assert 'drl' not in result.layers
    assert sorted(result.drill_groups) == ['drl top-bottom NPTH', 'drl top-bottom PTH']
    mil = 0.0254 if units == 'INCH' else 0.001
    small = float(sizes[0][1:]) * mil
    big = float(sizes[1][1:]) * mil
    pth = result.drill_groups['drl top-bottom PTH']
    assert pth[1]['tooldia'] == pytest.approx(small)
    assert flat(pth[1]['drills']) == pytest.approx([5, 5])
    assert pth[2]['tooldia'] == pytest.approx(big)
    assert flat([p for slot in pth[2]['slots'] for p in slot]) == pytest.approx([10, 10, 14, 10])
    assert flat(pth[2]['drills']) == pytest.approx([20, 5])
    npth = result.drill_groups['drl top-bottom NPTH']
    assert flat(npth[1]['drills']) == pytest.approx([8, 5])
    assert result.default_drill_groups == list(result.drill_groups)


def test_oval_drill_pads_are_slots(tmp_path):
    # Wide oval at 0 and 90 degrees (clockwise), tall oval mirrored at 30 degrees, round oval = hole.
    lines = ['P 5 5 0 P 0 8 0', 'P 10 5 0 P 0 8 90', 'P 20 5 1 P 0 9 30', 'P 30 5 2 P 0 8 0']
    steps = {'pcb': {'layers': {'drl': {'features': features(lines, ('oval3000x1000', 'oval600x1600',
                                                                      'oval800x800'), 'MM')}}}}
    result = parse(tmp_path, [{'name': 'drl', 'type': 'DRILL'}], steps, units='MM')
    tools = {t['tooldia']: t for t in result.drill_groups['drl PTH'].values()}
    assert flat([p for slot in tools[1.0]['slots'] for p in slot]) == pytest.approx([4, 5, 6, 5, 10, 6, 10, 4])
    s, c = 0.5 * math.sin(math.radians(30)), 0.5 * math.cos(math.radians(30))
    assert flat(tools[0.6]['slots'][0]) == pytest.approx([20 + s, 5 - c, 20 - s, 5 + c])
    assert flat(tools[0.8]['drills']) == pytest.approx([30, 5])


def test_kicad_zip_job_has_the_connector_slots():
    result = ODBParser().parse_file(Path(__file__).parent / 'fixtures' / 'odb++' / 'Easyduino_RP2040-odb.zip')
    assert (result.step, result.name) == ('PCB', 'Easyduino_RP2040-odb')
    assert 'F.CU' in result.layers and 'EDGE.CUTS' in result.layers
    tools = {t['tooldia']: t for t in result.drill_groups['DRILL_PLATED_F.CU-B.CU F.CU-B.CU PTH'].values()}
    ends = sorted(c for a, b in tools[0.6]['slots'] for c in sorted([(a.x, a.y), (b.x, b.y)]))
    assert [c for p in ends for c in p] == pytest.approx(
        [140.36, -80.55, 140.36, -79.45, 140.36, -76.2, 140.36, -75.4,
         149, -80.55, 149, -79.45, 149, -76.2, 149, -75.4])
    assert not any('skipped' in w for w in result.warnings)


# ----------------------------------------------------------------------------------------------------------------------
# Step-repeat
# ----------------------------------------------------------------------------------------------------------------------

def repeat_block(name, x, y, dx, dy, nx, ny, angle=0, mirror='NO'):
    return ('STEP-REPEAT {\n NAME=%s\n X=%s\n Y=%s\n DX=%s\n DY=%s\n NX=%d\n NY=%d\n ANGLE=%s\n FLIP=NO\n'
            ' MIRROR=%s\n}\n' % (name, x, y, dx, dy, nx, ny, angle, mirror))


def test_panel_places_copper_and_drills(tmp_path):
    datum = (1, 1)
    shape = [(2, 1), (4, 1), (4, 2), (3, 2), (3, 4), (2, 4)]
    child = {'stephdr': 'X_DATUM=1\nY_DATUM=1\n', 'layers': {
        'top': {'features': features(surface(shape))},
        'drl': {'features': features(['P 3 3 0 P 0 0'], ['r500'])}}}
    panel = {'stephdr': repeat_block('cell', 10, 20, 8, 9, 2, 2, angle=90, mirror='YES')}
    layers = [{'name': 'top'}, {'name': 'drl', 'type': 'DRILL'}]
    result = parse(tmp_path, layers, {'cell': child, 'panel': panel}, step='panel')
    expected_copper, expected_holes = [], []
    for j in range(2):
        for i in range(2):
            move = (10 + 8 * i, 20 + 9 * j)
            local = cw_mirror([(x - datum[0], y - datum[1]) for x, y in shape + [(3, 3)]], 90, True)
            moved = [(x + move[0], y + move[1]) for x, y in local]
            expected_copper.append(Polygon(moved[:-1]))
            expected_holes.append(moved[-1])
    assert same(copper(result), unary_union(expected_copper))
    drills = result.drill_groups['drl PTH'][1]['drills']
    assert sorted((round(p.x, 6), round(p.y, 6)) for p in drills) == sorted(
        (round(x, 6), round(y, 6)) for x, y in expected_holes)


def test_later_copy_erases_earlier_copper(tmp_path):
    child = {'layers': {'top': {'features': features(['P 0 0 0 P 0 0', 'P -1.5 0 1 N 0 0'], ['s4000', 's1000'])}}}
    panel = {'stephdr': repeat_block('cell', 0, 0, 3, 0, 2, 1)}
    result = parse(tmp_path, [{'name': 'top'}], {'cell': child, 'panel': panel}, step='panel')
    expected = unary_union([box(-2, -2, 2, 2), box(1, -2, 5, 2)]).difference(
        box(-2, -0.5, -1, 0.5)).difference(box(1, -0.5, 2, 0.5))
    assert same(copper(result), expected)


# ----------------------------------------------------------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------------------------------------------------------

# Font units are free: XSIZE/YSIZE 0.4, a cell advance of 0.5. 'W' is the widest character (0.2), 'I' is narrow.
FONT = ('XSIZE 0.4\nYSIZE 0.4\nOFFSET 0.1\n'
        'CHAR I\nLINE 0.05 0 0.05 0.3 P S 0.02\nECHAR\n'
        'CHAR W\nLINE 0 0 0.2 0.3 P S 0.02\nECHAR\n')
HALF = 12 * 0.0254 / 2          # half of a stroke at width_factor 1


def text_job(tmp_path, records, font=FONT, layers=None):
    """records: {layer name: T record lines}"""
    layers = layers or {'top': records}
    steps = {'pcb': {'layers': {name: {'features': features(lines)} for name, lines in layers.items()}}}
    return parse(tmp_path, [{'name': name} for name in layers], steps, font=font)


@pytest.mark.parametrize('version, centre', [(0, 10.1), (1, 10.2)])
def test_text_first_character_placement(tmp_path, version, centre):
    # x scale 0.8 / 0.4 = 2: the stroke of 'I' is at 0.05 * 2 = 0.1 (version 0); version 1 centres it in the cell
    # of the widest character 'W' (0.2 * 2 = 0.4 wide, centre 0.2)
    result = text_job(tmp_path, ["T 10 5 standard P 0 0.8 0.8 1 'I' %d" % version])
    assert copper(result).bounds == pytest.approx((centre - HALF, 5 - HALF, centre + HALF, 5.6 + HALF))


@pytest.mark.parametrize('orient, angle, mirror', [('1', 90, False), ('9 30', 30, True)])
def test_text_orientation(tmp_path, orient, angle, mirror):
    result = text_job(tmp_path, ["T 10 5 standard P %s 0.8 0.8 1 'I' 0" % orient])
    local = [(0.1 - HALF, -HALF), (0.1 + HALF, -HALF), (0.1 + HALF, 0.6 + HALF), (0.1 - HALF, 0.6 + HALF)]
    expected = Polygon([(x + 10, y + 5) for x, y in cw_mirror(local, angle, mirror)])
    assert same(copper(result), expected)


def test_semicolon_inside_the_quotes_stays_in_the_text(tmp_path):
    # ';' has no glyph but takes a cell: the second 'I' is two cells (2 * 1.0) from the first
    result = text_job(tmp_path, ["T 0 0 standard P 0 0.8 0.8 1 'I;I' 0;0=1;ID=7"])
    assert len(result.layers['top']['solid_geometry']) == 2
    assert copper(result).bounds == pytest.approx((0.1 - HALF, -HALF, 2.1 + HALF, 0.6 + HALF))


def test_layer_variable_is_the_layer_name(tmp_path):
    result = text_job(tmp_path, None, layers={'WI': ["T 1 1 standard P 0 0.8 0.8 1 '$$LAYER' 0"],
                                              'plain': ["T 1 1 standard P 0 0.8 0.8 1 'WI' 0"]})
    assert same(copper(result, 'WI'), copper(result, 'plain'))
    assert copper(result, 'WI').bounds == pytest.approx((1 - HALF, 1 - HALF, 1 + 1.1 + HALF, 1.6 + HALF))


def test_missing_font_draws_the_builtin_strokes_with_one_warning(tmp_path):
    # 'H' on the 6-cell grid: x 0..4, y 0..6 cells; ysize 1.2 -> a cell of 0.2 mm
    result = text_job(tmp_path, ["T 0 0 standard P 0 1.2 1.2 1 'H' 0"], font=None)
    assert copper(result).bounds == pytest.approx((-HALF, -HALF, 0.8 + HALF, 1.2 + HALF), abs=1e-6)
    assert len(result.warnings) == 1


# ----------------------------------------------------------------------------------------------------------------------
# Job access, archive policy, .Z members, app flow
# ----------------------------------------------------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / 'fixtures' / 'odb'

TWO_STEPS = {
    'matrix/matrix': 'STEP {\nCOL=2\nNAME=PANEL\n}\nSTEP {\nCOL=1\nNAME=PCB\n}\n'
                     'LAYER {\nROW=1\nCONTEXT=BOARD\nTYPE=SIGNAL\nNAME=TOP\nPOLARITY=POSITIVE\n}\n',
    'misc/info': 'JOB_NAME=demo\nUNITS=MM\n',
    'steps/pcb/stephdr': 'UNITS=MM\nX_DATUM=0\nY_DATUM=0\n',
    'steps/panel/stephdr': 'UNITS=MM\nSTEP-REPEAT {\nNAME=PCB\nX=0\nY=0\nDX=20\nDY=20\nNX=2\nNY=1\n'
                           'ANGLE=0\nFLIP=NO\nMIRROR=NO\n}\n',
    'steps/pcb/profile': 'UNITS=MM\nS P 0\nOB 0 0 I\nOS 10 0\nOS 10 10\nOS 0 10\nOS 0 0\nOE\nSE\n',
    'steps/pcb/layers/top/features': 'UNITS=MM\n$0 r1000\nP 5 5 0 P 0 0\n',
}


def write_folder(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return Path(root)


def write_zip(path, files, prefix='demo/'):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rel, text in files.items():
            archive.writestr(prefix + rel, text)
    return path


def write_tar(path, files, mode='w:gz', tar_format=tarfile.PAX_FORMAT, prefix='demo/'):
    with tarfile.open(path, mode, format=tar_format) as archive:
        for rel, text in files.items():
            data = text if isinstance(text, bytes) else text.encode()
            info = tarfile.TarInfo(prefix + rel)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def make_io():
    app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options={}, app_units='MM',
                          new_project_signal=Mock(), abort_flag=False, app_obj=Mock(), plot_all=Mock(),
                          file_opened=Mock(), get_last_folder=Mock(return_value=''))
    app.projects = SimpleNamespace(is_busy=lambda: False, inform_busy=Mock(),
                                   active=SimpleNamespace(collection=SimpleNamespace(get_list=lambda: [])))
    app.project_filename, app.should_we_save = None, False
    return appIO(app)


# ------------------------------------------------------------------------------------------------ 1. input forms
@pytest.mark.parametrize('form', ['tgz', 'zip', 'gnu_tar', 'v7_tar', 'folder_matrix', 'folder_features'])
def test_every_input_form_opens_the_same_job(tmp_path, form):
    if form == 'tgz':
        path = write_tar(tmp_path / 'job.dat', TWO_STEPS)        # found by content, not by extension
    elif form == 'zip':
        path = write_zip(tmp_path / 'job.brd', TWO_STEPS)
    elif form in ('gnu_tar', 'v7_tar'):
        path = write_tar(tmp_path / 'job.tar', TWO_STEPS, mode='w',
                         tar_format=tarfile.GNU_FORMAT if form == 'gnu_tar' else tarfile.USTAR_FORMAT)
        if form == 'v7_tar':
            # a v7 header: no 'ustar' magic, the checksum alone identifies the archive
            data = bytearray(path.read_bytes())
            for start in range(0, len(data) - 512, 512):
                block = data[start:start + 512]
                if block[257:262] == b'ustar':
                    block[257:265] = b'\0' * 8
                    block[148:156] = b' ' * 8
                    block[148:156] = b'%06o\0 ' % sum(block)
                    data[start:start + 512] = block
            path.write_bytes(bytes(data))
            assert b'ustar' not in data
    else:
        root = write_folder(tmp_path / 'demo', TWO_STEPS)
        path = root / ('matrix/matrix' if form == 'folder_matrix' else 'steps/pcb/layers/top/features')
    parsed = ODBParser().parse_file(path)
    assert parsed.source_kind == 'odb'
    assert parsed.steps == ['PCB', 'PANEL'] and parsed.default_step == 'PCB' and parsed.step is None
    assert parsed.layers == {} and parsed.drill_groups == {}
    job = ODBJob.open(path)
    assert job.read_text('STEPS/PCB/LAYERS/TOP/FEATURES') == TWO_STEPS['steps/pcb/layers/top/features']


# ------------------------------------------------------------------------------------------------ 3. archive policy
def _policy_archive(tmp_path, case):
    path = tmp_path / 'job.tgz'
    if case == 'two_roots':
        with zipfile.ZipFile(path, 'w') as archive:
            for prefix in ('a/', 'b/'):
                for rel, text in TWO_STEPS.items():
                    archive.writestr(prefix + rel, text)
    elif case == 'parent':
        write_zip(path, dict(TWO_STEPS, **{'../evil': 'x'}), prefix='')
    elif case == 'symlink':
        write_tar(path, TWO_STEPS)
        with tarfile.open(path, 'r:gz') as source:
            members = [(info, source.extractfile(info).read()) for info in source]
        with tarfile.open(path, 'w:gz') as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo('demo/steps/pcb/layers/top/attrlist')
            link.type, link.linkname = tarfile.SYMTYPE, '/etc/passwd'
            archive.addfile(link)
    elif case == 'case_fold':
        write_zip(path, dict(TWO_STEPS, **{'MISC/INFO': 'UNITS=INCH\n'}))
    elif case == 'exact_duplicate':
        write_zip(path, TWO_STEPS)
        with pytest.warns(UserWarning), zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('demo/steps/pcb/layers/top/features', 'UNITS=MM\n$0 r3000\nP 1 1 0 P 0 0\n')
    return path


@pytest.mark.parametrize('case', ['two_roots', 'parent', 'symlink', 'case_fold', 'exact_duplicate'])
def test_unsafe_or_ambiguous_archives_are_refused(tmp_path, case):
    with pytest.raises(ODBParseError):
        ODBJob.open(_policy_archive(tmp_path, case))


@pytest.mark.parametrize('limit', ['zip_size', 'zip_ratio', 'tar_stream', 'kept_total'])
def test_archive_limits_fail_the_import(tmp_path, monkeypatch, limit):
    padding = '#' + ' ' * 200000 + '\n'
    if limit == 'zip_size':
        monkeypatch.setattr(ODBJob, 'MEMBER_LIMIT', 64)
        path = write_zip(tmp_path / 'job.zip', TWO_STEPS)
    elif limit == 'zip_ratio':
        path = write_zip(tmp_path / 'job.zip', dict(TWO_STEPS, **{'matrix/matrix': TWO_STEPS['matrix/matrix'] + padding}))
    elif limit == 'tar_stream':
        # the skipped member is not on the graphics list, but the decoded stream still counts it
        monkeypatch.setattr(ODBJob, 'STREAM_LIMIT', 100000)
        path = write_tar(tmp_path / 'job.tgz', dict(TWO_STEPS, **{'misc/big_unused': padding}))
    else:
        monkeypatch.setattr(ODBJob, 'KEPT_LIMIT', 200)
        path = write_tar(tmp_path / 'job.tgz', TWO_STEPS)
    with pytest.raises(ODBParseError):
        ODBParser().parse_file(path)
    handlers = make_io()
    assert handlers.parse_odb(str(path)) == 'fail'


# ------------------------------------------------------------------------------------------------ 4. .Z members
def z_job(tmp_path, packed):
    files = dict(TWO_STEPS)
    del files['steps/pcb/layers/top/features']
    files['steps/pcb/layers/top/FEATURES.Z'] = packed
    return ODBJob.open(write_tar(tmp_path / 'job.tgz', files))


@pytest.mark.parametrize('name', ['small', 'wide', 'clear'])
def test_compressed_member_decodes_to_its_source(tmp_path, name):
    # made with the C code of ncompress (tests/fixtures/odb/make_z_fixtures.py): small = 9-bit codes only,
    # wide = every code width 9..16 bits, clear = a block-mode CLEAR code
    job = z_job(tmp_path, (FIXTURES / (name + '.txt.Z')).read_bytes())
    assert job.read('steps/pcb/layers/top/features') == (FIXTURES / (name + '.txt')).read_bytes()


@pytest.mark.parametrize('case', ['truncated', 'over_limit', 'not_compress'])
def test_bad_compressed_member_fails(tmp_path, monkeypatch, case):
    packed = (FIXTURES / 'wide.txt.Z').read_bytes()
    if case == 'truncated':
        packed = packed[:4]
    elif case == 'over_limit':
        monkeypatch.setattr(ODBJob, 'MEMBER_LIMIT', 200000)     # packed 102 kB, decoded 275 kB
    else:
        packed = b'\x1f\x8b\x08' + packed[3:]
    job = z_job(tmp_path, packed)
    with pytest.raises(ODBParseError):
        job.read('steps/pcb/layers/top/features')


# ------------------------------------------------------------------------------------------------ 2, 15. app flow
ONE_STEP = {
    'matrix/matrix': 'STEP {\nCOL=1\nNAME=PCB\n}\n'
                     'LAYER {\nROW=1\nCONTEXT=BOARD\nTYPE=SIGNAL\nNAME=TOP\nPOLARITY=POSITIVE\n}\n'
                     'LAYER {\nROW=2\nCONTEXT=BOARD\nTYPE=DRILL\nNAME=DRILL\nPOLARITY=POSITIVE\n'
                     'START_NAME=TOP\nEND_NAME=TOP\n}\n',
    'misc/info': 'JOB_NAME=demo\nUNITS=MM\n',
    'steps/pcb/stephdr': 'UNITS=MM\nX_DATUM=0\nY_DATUM=0\n',
    'steps/pcb/profile': TWO_STEPS['steps/pcb/profile'],
    'steps/pcb/layers/top/features': 'UNITS=MM\n$0 r1000\nP 5 5 0 P 0 0\n',
    'steps/pcb/layers/drill/features': 'UNITS=MM\n$0 r400\nP 5 5 0 P 0 0\n',
}


def gui_io():
    handlers = make_io()
    handlers.app.ui = QtWidgets.QApplication.instance() and QtWidgets.QWidget()
    objects = []

    def create(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
        if initialize(obj, handlers.app) == 'fail':
            return 'fail'
        objects.append((kind, obj))
        return obj

    handlers.app.app_obj.new_object.side_effect = create
    handlers.export_gerber = Mock(return_value='G04*\nM02*\n')
    handlers.export_excellon = Mock(return_value='M48\nM30\n')
    return handlers, objects


def run_job(handlers):
    job = handlers.app.worker_task.emit.call_args.args[0]
    result = job['fcn'](*job['params'])
    QtWidgets.QApplication.instance().processEvents()
    return result


class LayerDialog:
    """Stands in for QDialog.exec: records the dialog texts, then accepts or rejects."""

    def __init__(self, accept=True):
        self.accept, self.seen = accept, []

    def __call__(self, dialog):
        self.seen.append({'title': dialog.windowTitle(),
                          'labels': [label.text() for label in dialog.findChildren(QtWidgets.QLabel)],
                          'checks': [box.text() for box in dialog.findChildren(QtWidgets.QCheckBox)]})
        return QtWidgets.QDialog.DialogCode.Accepted if self.accept else QtWidgets.QDialog.DialogCode.Rejected


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    # an import that a test leaves pending keeps its window modal layer dialog open: cancel it like the user
    for widget in app.topLevelWidgets():
        if isinstance(widget, QtWidgets.QDialog) and widget.isVisible():
            widget.reject()
    app.processEvents()


@pytest.mark.parametrize('file_name', ['board.dat', 'board.brd'])
def test_one_step_job_imports_without_a_step_prompt_and_keeps_its_kind(qapp, tmp_path, file_name):
    path = str(write_zip(tmp_path / file_name, ONE_STEP))
    handlers, objects = gui_io()
    dialog = LayerDialog()
    with patch.object(QtWidgets.QInputDialog, 'getItem', side_effect=AssertionError('step prompt')), \
            patch.object(QtWidgets.QDialog, 'exec', lambda shown: dialog(shown)):
        handlers.on_file_import_odb(path)
        run_job(handlers)
    assert dialog.seen[0]['title'] == 'Import ODB++ Job'
    assert 'Step: PCB' in dialog.seen[0]['labels']
    assert {'TOP', 'profile'} <= set(dialog.seen[0]['checks'])
    run_job(handlers)                               # the object creation of the accepted dialog
    qapp.processEvents()
    kinds = [kind for kind, obj in objects]
    assert 'gerber' in kinds and 'excellon' in kinds
    assert all(obj.obj_options['name'].startswith('demo_') for kind, obj in objects)
    handlers.app.file_opened.emit.assert_called_once_with('odb', path)


def test_two_step_job_asks_the_step_then_parses_it(qapp, tmp_path):
    path = str(write_tar(tmp_path / 'job.tgz', TWO_STEPS))
    handlers, objects = gui_io()
    dialog = LayerDialog(accept=False)
    with patch.object(QtWidgets.QInputDialog, 'getItem',
                      side_effect=lambda parent, title, label, items, current, editable:
                      (items[current], True)) as prompt, patch.object(QtWidgets.QDialog, 'exec', lambda shown: dialog(shown)):
        handlers.on_file_import_odb(path)
        run_job(handlers)                           # metadata parse -> step prompt -> worker parse
        assert prompt.call_args.args[3] == ['PCB', 'PANEL']
        assert dialog.seen == []
        run_job(handlers)                           # the submitted geometry parse of the chosen step
    assert 'Step: PCB' in dialog.seen[0]['labels']
    # the cancelled layer dialog ended the request
    handlers.on_file_import_odb(path)
    handlers.app.projects.inform_busy.assert_not_called()


def test_cancel_at_the_step_prompt_imports_nothing(qapp, tmp_path):
    path = str(write_tar(tmp_path / 'job.tgz', TWO_STEPS))
    handlers, objects = gui_io()
    with patch.object(QtWidgets.QInputDialog, 'getItem', return_value=('', False)), \
            patch.object(QtWidgets.QDialog, 'exec', side_effect=AssertionError('layer dialog')):
        handlers.on_file_import_odb(path)
        run_job(handlers)
    assert handlers.app.worker_task.emit.call_count == 1 and objects == []
    handlers.on_file_import_odb(path)
    assert handlers.app.worker_task.emit.call_count == 2
    handlers.app.projects.inform_busy.assert_not_called()
