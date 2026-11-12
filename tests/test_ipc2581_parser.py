import functools
import gzip
import io
import math
import zipfile
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from appParsers.ParseIPC2581 import IPC2581ParseError, IPC2581Parser

FIXTURE = Path(__file__).parent / 'fixtures' / 'ipc2581' / 'Easyduino_RP2040.xml.gz'
NS = 'http://webstds.ipc.org/2581'


def assert_native_roundtrip(parsed, key, tmp_path):
    from tests.test_kicad_pcb_e2e import _app, NativeGerberObject, Gerber, appIO, _union
    app = _app('MM')
    app.options.update(gerber_board_import_mode='primitives', gerber_exp_decimals=6)
    objects = []

    def create(kind, name, initialize, **kwargs):
        obj = NativeGerberObject(name, app)
        assert initialize(obj, app) != 'fail'
        objects.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    assert appIO(app).import_pcbdoc_objects('board', parsed, [key], []) is None
    exported = tmp_path / 'native.gbr'
    exported.write_text(objects[0].source_file)
    reloaded = Gerber(app)
    assert reloaded.parse_file(str(exported)) != 'fail'
    follows = [r.get('follow') for t in reloaded.tools.values() for r in t.get('geometry', [])]
    source_follows = [r.get('follow') for t in parsed.layers[key]['tools'].values() for r in t['geometry']]
    source_centres = sorted((f.x, f.y) for f in source_follows if isinstance(f, Point))
    exported_centres = sorted((f.x, f.y) for f in follows if isinstance(f, Point))
    assert len(exported_centres) == len(source_centres)
    for actual, expected_centre in zip(exported_centres, source_centres):
        assert actual == pytest.approx(expected_centre, abs=1e-6)
    if any(isinstance(f, LineString) for f in source_follows):
        assert any(isinstance(f, LineString) for f in follows)
    expected = _union(parsed.layers[key]['solid_geometry'])
    difference = expected.symmetric_difference(_union(reloaded.solid_geometry))
    assert difference.buffer(-0.003).is_empty
    assert difference.area < expected.length * 0.003
    return reloaded


def test_preserved_dictionary_primitives_transforms_and_clear(tmp_path):
    from types import SimpleNamespace
    dictionaries = ('<DictionaryStandard units="MILLIMETER"><EntryStandard id="r"><RectCenter width="2" height="1"/>'
                    '</EntryStandard><EntryStandard id="o"><Oval width="2" height="1"/></EntryStandard>'
                    '<EntryStandard id="c"><Circle diameter="1"/></EntryStandard></DictionaryStandard>')
    xml = doc(pad('<StandardPrimitiveRef id="r"/>', (4, 0), '<Xform mirror="true" rotation="90" scale="2"/>')
              + pad('<StandardPrimitiveRef id="o"/>', (8, 0))
              + pad('<StandardPrimitiveRef id="c"/>')
              + '<Set><Features><Xform mirror="true" rotation="90" scale="2"/><Location x="10" y="10"/>'
                '<Location x="20" y="10"/><Line startX="0" startY="0" endX="3" endY="0">'
                '<LineDesc lineWidth="0.3" lineEnd="ROUND"/></Line></Features></Set>'
              + '<Set polarity="NEGATIVE"><Features><Circle diameter="0.4"/></Features></Set>',
              dictionaries=dictionaries)
    baseline = parse(xml)
    parsed = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'}),
                           circle_steps=64).parse_bytes(xml.encode())
    tools = parsed.layers['L1']['tools']
    assert {'C', 'R', 'O', 'REG'} <= {t['type'] for t in tools.values()}
    assert any(t['type'] == 'R' and t['width'] == 2 and t['height'] == 4 for t in tools.values())
    assert sum(isinstance(r.get('follow'), Point) for t in tools.values() for r in t['geometry']) == 2
    assert layer_geometry(parsed).equals(layer_geometry(baseline))
    assert_native_roundtrip(parsed, 'L1', tmp_path)


def test_preserved_point_stroke_and_custom_symbol():
    from types import SimpleNamespace
    xml = doc('<Set><Features><Line startX="0" startY="0" endX="0" endY="0">'
              '<LineDesc lineWidth="1" lineEnd="ROUND"/></Line></Features></Set>'
              + pad('<UserPrimitiveRef id="custom"/>', (5, 0)),
              dictionaries='<DictionaryUser units="MILLIMETER"><EntryUser id="custom"><UserSpecial>'
                           '<Circle diameter="2"/>' + square(0, 0, 2, 2)
                           + '</UserSpecial></EntryUser></DictionaryUser>')
    parsed = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'}),
                           circle_steps=64).parse_bytes(xml.encode())
    records = [r for t in parsed.layers['L1']['tools'].values() for r in t['geometry']
               if isinstance(r.get('follow'), Point)]
    assert len(records) == 2
    assert {tuple(r['follow'].coords)[0] for r in records} == {(0, 0), (5, 0)}
    assert layer_geometry(parsed).equals(layer_geometry(parse(xml)))


def test_preserved_padstack_dictionary_reuse_and_negative_layer(tmp_path):
    from types import SimpleNamespace
    dictionary = ('<DictionaryStandard units="MICRON"><EntryStandard id="r"><RectCenter width="2000" height="1000"/>'
                  '</EntryStandard><EntryStandard id="c"><Circle diameter="1000"/></EntryStandard>'
                  '</DictionaryStandard>')
    stack = ('<PadStackDef name="p"><PadstackPadDef layerRef="L1"><Xform rotation="90" scale="2"/>'
             '<Location x="1" y="0"/><StandardPrimitiveRef id="r"/></PadstackPadDef></PadStackDef>')
    features = ('<Set><Pad padstackDefRef="p"><Xform mirror="true" rotation="90" scale="0.5"/>'
                '<Location x="4" y="4"/></Pad></Set>'
                + pad('<StandardPrimitiveRef id="r"/>', (8, 0), '<Xform rotation="45"/>')
                + pad('<StandardPrimitiveRef id="c"/>', (0, 0))
                + pad('<StandardPrimitiveRef id="c"/>', (2, 0))
                + '<Set><Features><Arc startX="0" startY="8" endX="2" endY="8" centerX="1" centerY="8" '
                  'clockwise="false"><LineDesc lineWidth="0.2" lineEnd="ROUND"/></Arc></Features></Set>')
    for polarity in ('POSITIVE', 'NEGATIVE'):
        xml = doc(features, dictionaries=dictionary, steps_extra=stack, polarity=polarity,
                  profile='<Profile><Polygon><PolyBegin x="-5" y="-5"/><PolyStepSegment x="15" y="-5"/>'
                          '<PolyStepSegment x="15" y="15"/><PolyStepSegment x="-5" y="15"/></Polygon></Profile>')
        baseline = parse(xml)
        parsed = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'}),
                               circle_steps=64).parse_bytes(xml.encode())
        assert layer_geometry(parsed).equals(layer_geometry(baseline))
        tools = parsed.layers['L1']['tools']
        if polarity == 'NEGATIVE':
            assert {t['type'] for t in tools.values()} == {'REG'}
        else:
            rectangles = [t for t in tools.values() if t['type'] == 'R']
            assert len(rectangles) == 1
            assert (rectangles[0]['width'], rectangles[0]['height']) == pytest.approx((2, 1))
            assert rectangles[0]['geometry'][0]['follow'].equals(Point(4, 3.5))
            assert sum(isinstance(r.get('follow'), Point) for t in tools.values() for r in t['geometry']) == 4
            assert_native_roundtrip(parsed, 'L1', tmp_path)


@pytest.mark.parametrize('units,factor', [('MILLIMETER', 1), ('INCH', 25.4)])
def test_nested_user_pad_holes_decorations_and_source_centres(tmp_path, units, factor):
    from types import SimpleNamespace
    dictionary = ('<DictionaryUser units="MILLIMETER"><EntryUser id="custom"><UserSpecial><UserSpecial>'
                  + square(-2, -2, 2, 2) + '<Circle diameter="1"><FillDesc fillProperty="VOID"/></Circle>'
                  '</UserSpecial></UserSpecial></EntryUser></DictionaryUser>')
    xf = '<Xform mirror="true" rotation="30" scale="2" xOffset="1" yOffset="2"/>'
    xml = doc(pad('<UserPrimitiveRef id="custom"/>', (10, 5), xf)
              + pad('<UserPrimitiveRef id="custom"/>', (20, 5), xf)
              + pad('<RectCenter width="2" height="1"/>', (40, 5), '<Xform rotation="45"/>')
              + pad('<Circle diameter="2"><LineDesc lineWidth="0.2"/></Circle>', (50, 5))
              + '<Set><Features><Line startX="60" startY="0" endX="63" endY="0">'
                '<LineDesc lineWidth="0.2" lineEnd="ROUND"/></Line></Features></Set>',
              dictionaries=dictionary, units=units)
    parsed = IPC2581Parser(circle_steps=64).parse_bytes(xml.encode())
    baseline = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'}),
                            circle_steps=64).parse_bytes(xml.encode())
    flashes = [r for t in parsed.layers['L1']['tools'].values() for r in t['geometry']
               if isinstance(r.get('follow'), Point)]
    assert len(flashes) == 4
    centres = sorted((r['follow'].x, r['follow'].y) for r in flashes)
    for actual, expected in zip(centres, [(11, 7), (21, 7), (40, 5), (50, 5)]):
        assert actual == pytest.approx(tuple(v * factor for v in expected))
    assert sum(len(r['solid'].interiors) for r in flashes) == 2
    assert layer_geometry(parsed).equals(layer_geometry(baseline))
    reloaded = assert_native_roundtrip(parsed, 'L1', tmp_path)
    assert sum(isinstance(r.get('follow'), Point) for t in reloaded.tools.values() for r in t['geometry']) == 4


def test_negative_layer_retains_only_restored_uncut_custom_pad(tmp_path):
    from types import SimpleNamespace
    dictionary = ('<DictionaryUser units="MILLIMETER"><EntryUser id="p"><UserSpecial>'
                  + square(-2, -2, 2, 2) + '<RectCenter width="1" height="1">'
                  '<FillDesc fillProperty="VOID"/></RectCenter></UserSpecial></EntryUser></DictionaryUser>')
    xml = doc('<Set><Features>' + square(-5, -5, 5, 5) + '</Features></Set>'
              '<Set polarity="NEGATIVE"><Pad><Location x="0" y="0"/><Xform rotation="30"/>'
              '<UserPrimitiveRef id="p"/></Pad><Pad><Location x="10" y="0"/>'
              '<UserPrimitiveRef id="p"/></Pad></Set>', dictionaries=dictionary, polarity='NEGATIVE',
              profile='<Profile><Polygon><PolyBegin x="-10" y="-10"/><PolyStepSegment x="10" y="-10"/>'
                      '<PolyStepSegment x="10" y="10"/><PolyStepSegment x="-10" y="10"/></Polygon></Profile>')
    parsed = IPC2581Parser(circle_steps=64).parse_bytes(xml.encode())
    baseline = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'regions'}),
                            circle_steps=64).parse_bytes(xml.encode())
    flashes = [r for t in parsed.layers['L1']['tools'].values() for r in t['geometry']
               if isinstance(r.get('follow'), Point)]
    assert len(flashes) == 1 and flashes[0]['follow'].equals(Point(0, 0))
    assert len(flashes[0]['solid'].interiors) == 1
    assert layer_geometry(parsed).equals(layer_geometry(baseline))
    assert_native_roundtrip(parsed, 'L1', tmp_path)


def test_preserved_real_ipc_board_matches_regions(tmp_path):
    from types import SimpleNamespace
    parsed = IPC2581Parser(app=SimpleNamespace(options={'gerber_board_import_mode': 'primitives'})).parse_file(FIXTURE)
    assert parsed.drill_groups.keys() == easyduino().drill_groups.keys()
    assert parsed.warnings == easyduino().warnings
    for name in parsed.layers:
        assert layer_geometry(parsed, name).equals(layer_geometry(easyduino(), name))
        assert_native_roundtrip(parsed, name, tmp_path)
    for name, flashes, strokes in [('F.Cu', 320, 465), ('In1.Cu', 135, 0), ('In2.Cu', 135, 3),
                                  ('B.Cu', 138, 102), ('F.Mask', 238, 0), ('B.Mask', 56, 0), ('F.Paste', 177, 0)]:
        records = [r for t in parsed.layers[name]['tools'].values() for r in t['geometry']]
        assert sum(isinstance(r.get('follow'), Point) for r in records) == flashes
        assert sum(isinstance(r.get('follow'), LineString) for r in records) == strokes


def doc(features='', function='CONDUCTOR', polarity='POSITIVE', units='MILLIMETER', revision='C', dictionaries='',
        layers='', profile='', steps_extra='', layer_features=None):
    """A minimal IPC-2581 board: the layer 'L1' with the given LayerFeature content."""
    if layer_features is None:
        layer_features = '<LayerFeature layerRef="L1">%s</LayerFeature>' % features
    return ('<?xml version="1.0" encoding="UTF-8"?><IPC-2581 revision="%s" xmlns="%s"><Content roleRef="Owner">'
            '<StepRef name="S"/>%s</Content><Ecad name="D"><CadHeader units="%s"/><CadData>'
            '<Layer name="L1" layerFunction="%s" polarity="%s" side="TOP"/>%s'
            '<Step name="S" type="BOARD"><Datum x="0" y="0"/>%s%s%s</Step></CadData></Ecad></IPC-2581>'
            % (revision, NS, dictionaries, units, function, polarity, layers, profile, layer_features, steps_extra))


def parse(xml, **kwargs):
    return IPC2581Parser(circle_steps=64).parse_bytes(xml.encode('utf-8'), **kwargs)


def layer_geometry(parsed, name='L1'):
    return unary_union(parsed.layers[name]['solid_geometry'])


def pad(shape, at=(0, 0), xform=''):
    return '<Set><Pad>%s<Location x="%s" y="%s"/>%s</Pad></Set>' % (xform, at[0], at[1], shape)


def square(x0, y0, x1, y1):
    return '<RectCorner lowerLeftX="%s" lowerLeftY="%s" upperRightX="%s" upperRightY="%s"/>' % (x0, y0, x1, y1)


# ---------------------------------------------------------------------------------------------------- real board

@functools.lru_cache(maxsize=None)
def easyduino():
    # KiCad 10.0.8 export (revision C) of tests/fixtures/kicad/Easyduino_RP2040.kicad_pcb; see the README
    return IPC2581Parser().parse_file(FIXTURE)


@pytest.mark.parametrize('layer, area', [('F.Cu', 698.281), ('In1.Cu', 920.219), ('In2.Cu', 899.074),
                                         ('B.Cu', 860.901)])
def test_copper_matches_the_kicad_gerbers(layer, area):
    # areas of the KiCad Gerbers of the same board (parsed by FlatCAM): the 4 layers have pours, tracks and pads
    # of both sides (the bottom ones are mirrored components); the symmetric difference has to stay below 0.02 %
    mine = layer_geometry(easyduino(), layer)
    reference = {'F.Cu': (134.5, -124.06, 154.5, -73.89), 'In1.Cu': (134.5, -124.0, 154.5, -73.969),
                 'In2.Cu': (134.5, -124.0, 154.5, -73.969), 'B.Cu': (134.5, -124.0, 154.5, -73.969)}[layer]
    assert mine.area == pytest.approx(area, abs=0.0002 * area)
    assert mine.bounds == pytest.approx(reference, abs=0.01)
    assert layer in easyduino().default_layers


def test_mask_paste_silkscreen_outline_and_profile_of_the_real_board():
    parsed = easyduino()
    for layer, area in (('F.Mask', 198.733), ('B.Mask', 122.546), ('F.Paste', 70.828), ('F.Silkscreen', 24.975)):
        assert layer_geometry(parsed, layer).area == pytest.approx(area, abs=0.002 * area)
    assert parsed.board_profile.bounds == pytest.approx((134.0, -124.5, 155.0, -73.5), abs=1e-6)
    assert parsed.board_profile.area == pytest.approx(1070.14, abs=0.01)
    assert 'Edge.Cuts' in parsed.default_layers and 'profile' not in parsed.default_layers
    assert 'F.Fab' in parsed.layers and 'F.Fab' not in parsed.default_layers
    assert 'DIELECTRIC_1' not in parsed.layers


def test_drills_and_slots_match_the_excellon_file():
    parsed = easyduino()
    groups = {}
    for group, tools in parsed.drill_groups.items():
        for tool in tools.values():
            groups.setdefault(group.rsplit(' ', 1)[1], []).append((tool['tooldia'], len(tool['drills']),
                                                                   len(tool['slots'])))
    assert sorted(groups['PTH']) == [(0.3, 88, 0), (0.6, 0, 4), (1.0, 43, 0)]
    assert sorted(groups['NPTH']) == [(0.65, 2, 0), (2.2, 4, 0)]
    drills = [p for tools in parsed.drill_groups.values() for t in tools.values() for p in t['drills']]
    assert (sum(p.x for p in drills), sum(p.y for p in drills)) == pytest.approx((19801.947, -13611.837), abs=0.2)
    assert (min(p.x for p in drills), min(p.y for p in drills), max(p.x for p in drills),
            max(p.y for p in drills)) == pytest.approx((135.534, -123.079, 153.441, -74.819), abs=0.001)
    slots = sorted(tuple(sorted(((a.x, a.y), (b.x, b.y)))) for tools in parsed.drill_groups.values()
                   for t in tools.values() for a, b in t['slots'])
    expected = sorted([((140.36, -80.55), (140.36, -79.45)), ((140.36, -76.2), (140.36, -75.4)),
                       ((149.0, -80.55), (149.0, -79.45)), ((149.0, -76.2), (149.0, -75.4))])
    assert [c for slot in slots for end in slot for c in end] == pytest.approx(
        [c for slot in expected for end in slot for c in end], abs=0.001)
    assert parsed.drill_groups.keys() == set(parsed.default_drill_groups)


# ---------------------------------------------------------------------------------------------------- transforms

def test_xform_is_mirror_then_rotation_then_offset_then_location():
    # a rectangle x 1..3, y 0..2: mirror -> x -3..-1; 90 deg CCW -> x -2..0, y -3..-1; offset x +1; location (10, 20)
    xml = doc(pad(square(1, 0, 3, 2), (10, 20), '<Xform rotation="90" mirror="true" xOffset="1"/>'))
    assert layer_geometry(parse(xml)).bounds == pytest.approx((9, 17, 11, 19))


@pytest.mark.parametrize('units, factor, revision', [('INCH', 25.4, 'B'), ('MICRON', 0.001, 'C'),
                                                     ('MILLIMETER', 1.0, 'C')])
def test_units_are_converted_to_mm_for_both_revisions(units, factor, revision):
    xml = doc(pad('<Circle diameter="4"/>', (10, 20)) +
              '<Set><Features><Location x="0" y="0"/><Line startX="0" startY="0" endX="10" endY="0">'
              '<LineDesc lineWidth="2" lineEnd="NONE"/></Line></Features></Set>', units=units, revision=revision)
    parsed = parse(xml)
    circle = [p for p in parsed.layers['L1']['solid_geometry'] if p.contains(Point(10 * factor, 20 * factor))][0]
    assert circle.bounds == pytest.approx((8 * factor, 18 * factor, 12 * factor, 22 * factor), rel=0.01)
    assert layer_geometry(parsed).bounds[2] == pytest.approx(12 * factor, rel=0.01)
    assert parsed.revision == revision


# ---------------------------------------------------------------------------------------------------- shapes

@pytest.mark.parametrize('shape, area', [
    ('<RectRound width="2" height="1" radius="0.25" upperRight="true" upperLeft="true" lowerRight="true" '
     'lowerLeft="true"/>', 2 - (4 - math.pi) * 0.0625),
    ('<RectCham width="2" height="2" chamfer="0.5" upperRight="true"/>', 4 - 0.125),
    ('<Oval width="3" height="1"/>', 2 + math.pi * 0.25),
    ('<Diamond width="2" height="4"/>', 4.0),
    ('<Donut shape="ROUND" outerDiameter="2" innerDiameter="1"/>', math.pi * 0.75),
    ('<Thermal shape="ROUND" outerDiameter="4" innerDiameter="2" spokeCount="4" spokeWidth="0.5" '
     'spokeStartAngle="45"/>', math.pi * 3 - 4 * 0.5 * 1.0),
    ('<Contour><Polygon><PolyBegin x="0" y="0"/><PolyStepSegment x="4" y="0"/><PolyStepSegment x="4" y="4"/>'
     '<PolyStepSegment x="0" y="4"/><PolyStepSegment x="0" y="0"/></Polygon>'
     '<Cutout><PolyBegin x="1" y="1"/><PolyStepSegment x="3" y="1"/><PolyStepSegment x="3" y="3"/>'
     '<PolyStepSegment x="1" y="3"/><PolyStepSegment x="1" y="1"/></Cutout></Contour>', 12.0),
])
def test_standard_primitives_have_their_area(shape, area):
    geometry = layer_geometry(parse(doc(pad(shape))))
    assert geometry.area == pytest.approx(area, rel=0.01)


def test_dictionary_primitives_line_descriptions_and_end_caps():
    dictionaries = ('<DictionaryLineDesc units="MILLIMETER"><EntryLineDesc id="W"><LineDesc lineWidth="1" '
                    'lineEnd="SQUARE"/></EntryLineDesc></DictionaryLineDesc>'
                    '<DictionaryStandard units="MILLIMETER"><EntryStandard id="C"><Circle diameter="2"/>'
                    '</EntryStandard></DictionaryStandard>'
                    '<DictionaryUser units="MILLIMETER"><EntryUser id="U"><UserSpecial>'
                    '<Line startX="-2" startY="0" endX="2" endY="0"><LineDescRef id="W"/></Line>'
                    '<Circle diameter="0.6"><FillDesc fillProperty="VOID"/></Circle>'
                    '</UserSpecial></EntryUser></DictionaryUser>')
    features = (pad('<StandardPrimitiveRef id="C"/>', (100, 0)) +
                '<Set><Features><Location x="0" y="50"/><UserPrimitiveRef id="U"/></Features></Set>')
    parsed = parse(doc(features, dictionaries=dictionaries))
    polygons = sorted(parsed.layers['L1']['solid_geometry'], key=lambda p: p.bounds[0])
    # the square-ended line 4 + 2 * 0.5 long, 1 wide, with the void circle of the user primitive erased at its origin
    assert polygons[0].area == pytest.approx(5.0 - math.pi * 0.09, rel=0.01)
    assert polygons[0].bounds == pytest.approx((-2.5, 49.5, 2.5, 50.5))
    assert polygons[1].area == pytest.approx(math.pi, rel=0.01)


@pytest.mark.parametrize('clockwise, expected', [('false', (-1.05, 0.0, 1.05, 1.05)),
                                                 ('true', (-1.05, -1.05, 1.05, 0.0))])
def test_arc_direction_and_polyline_curves(clockwise, expected):
    arc = ('<Set><Features><Location x="0" y="0"/><Arc startX="1" startY="0" endX="-1" endY="0" centerX="0" '
           'centerY="0" clockwise="%s"><LineDesc lineWidth="0.1" lineEnd="NONE"/></Arc></Features></Set>' % clockwise)
    assert layer_geometry(parse(doc(arc))).bounds == pytest.approx(expected, abs=0.01)
    curve = ('<Set><Features><UserSpecial><Polyline><PolyBegin x="1" y="0"/>'
             '<PolyStepCurve x="-1" y="0" centerX="0" centerY="0" clockwise="%s"/>'
             '<LineDesc lineWidth="0.1" lineEnd="NONE"/></Polyline></UserSpecial></Features></Set>' % clockwise)
    assert layer_geometry(parse(doc(curve))).bounds == pytest.approx(expected, abs=0.01)


def test_polarity_of_sets_and_negative_layers():
    positive = pad('<Circle diameter="4"/>') + '<Set polarity="NEGATIVE"><Pad><Location x="0" y="0"/>' \
                                                '<Circle diameter="2"/></Pad></Set>'
    assert layer_geometry(parse(doc(positive))).area == pytest.approx(math.pi * 3, rel=0.01)
    profile = '<Profile><Polygon><PolyBegin x="-5" y="-5"/><PolyStepSegment x="5" y="-5"/>' \
              '<PolyStepSegment x="5" y="5"/><PolyStepSegment x="-5" y="5"/><PolyStepSegment x="-5" y="-5"/>' \
              '</Polygon></Profile>'
    plane = parse(doc(pad('<Circle diameter="2"/>'), function='PLANE', polarity='NEGATIVE', profile=profile))
    assert layer_geometry(plane).area == pytest.approx(100 - math.pi, rel=0.01)
    with pytest.raises(IPC2581ParseError, match='negative layer'):
        parse(doc(pad('<Circle diameter="2"/>'), function='PLANE', polarity='NEGATIVE'))


def test_profile_cutouts_make_holes_in_the_board():
    profile = ('<Profile><Polygon><PolyBegin x="0" y="0"/><PolyStepSegment x="20" y="0"/>'
               '<PolyStepSegment x="20" y="10"/><PolyStepSegment x="0" y="10"/><PolyStepSegment x="0" y="0"/>'
               '</Polygon><Cutout><PolyBegin x="2" y="2"/><PolyStepSegment x="4" y="2"/>'
               '<PolyStepSegment x="4" y="4"/><PolyStepSegment x="2" y="4"/><PolyStepSegment x="2" y="2"/>'
               '</Cutout></Profile>')
    parsed = parse(doc(pad('<Circle diameter="1"/>', (10, 5)), profile=profile))
    assert parsed.board_profile.area == pytest.approx(196.0)
    assert len(parsed.board_profile.interiors) == 1
    assert 'profile' in parsed.layers and 'profile' in parsed.default_layers


# ---------------------------------------------------------------------------------------------------- holes

def test_holes_are_split_in_plated_and_non_plated_and_slots_are_read():
    stadium = LineString([(0, -1.0), (0, 1.0)]).buffer(0.3, quad_segs=16)       # revision B: the outline polygon
    outline = ('<SlotCavity name="S2" platingStatus="NONPLATED" plusTol="0" minusTol="0"><Outline><Polygon>'
               '<PolyBegin x="%s" y="%s"/>%s</Polygon><LineDesc lineWidth="0" lineEnd="ROUND"/></Outline>'
               '</SlotCavity>' % (stadium.exterior.coords[0][0] + 30, stadium.exterior.coords[0][1],
                                  ''.join('<PolyStepSegment x="%s" y="%s"/>' % (x + 30, y)
                                          for x, y in list(stadium.exterior.coords)[1:])))
    features = (
        '<Set><Hole name="H1" diameter="0.8" platingStatus="PLATED" plusTol="0" minusTol="0" x="1" y="1"/>'
        '<Hole name="H2" diameter="2" platingStatus="NONPLATED" plusTol="0" minusTol="0" x="5" y="5"/>'
        '<Hole name="H3" diameter="0.3" platingStatus="VIA" plusTol="0" minusTol="0" x="7" y="7"/>'
        '<SlotCavity name="S1" platingStatus="PLATED" plusTol="0" minusTol="0"><Location x="10" y="10"/>'
        '<Xform rotation="90"/><Oval width="2" height="0.6"/></SlotCavity>' + outline + '</Set>')
    xml = doc(layers='<Layer name="D" layerFunction="DRILL" polarity="POSITIVE" side="ALL"/>',
              layer_features='<LayerFeature layerRef="D">%s</LayerFeature>' % features)
    parsed = parse(xml)
    pth = {t['tooldia']: t for t in parsed.drill_groups['D PTH'].values()}
    npth = {t['tooldia']: t for t in parsed.drill_groups['D NPTH'].values()}
    assert sorted(pth) == [0.3, 0.6, 0.8] and sorted(npth) == [0.6, 2.0]
    assert [(p.x, p.y) for p in pth[0.8]['drills']] == [(1, 1)]
    assert [(p.x, p.y) for p in npth[2.0]['drills']] == [(5, 5)]
    (first, last), = pth[0.6]['slots']
    assert (first.x, first.y, last.x, last.y) == pytest.approx((10, 9.3, 10, 10.7))
    (first, last), = npth[0.6]['slots']
    assert sorted([first.y, last.y]) == pytest.approx([-1.0, 1.0], abs=0.02)
    assert (first.x, last.x) == pytest.approx((30, 30), abs=0.02)
    assert not parsed.layers.keys() - {'profile'}


# ---------------------------------------------------------------------------------------------------- refusals

@pytest.mark.parametrize('feature, name', [
    ('<Butterfly shape="ROUND" diameter="2"/>', 'Butterfly'),
    ('<Text textString="A" fontSize="10"><BoundingBox lowerLeftX="0" lowerLeftY="0" upperRightX="1" '
     'upperRightY="1"/></Text>', 'Text'),
    ('<Circle diameter="2"><FillDesc fillProperty="HATCH" lineWidth="0.1" pitch1="0.5" pitch2="0.5" angle1="45" '
     'angle2="135"/></Circle>', 'HATCH'),
    ('<Hexagon length="1"/>', 'Hexagon'),
])
def test_unsupported_copper_fails_but_the_same_feature_on_silkscreen_is_a_warning(feature, name):
    with pytest.raises(IPC2581ParseError, match=name):
        parse(doc(pad(feature)))
    parsed = parse(doc(pad(feature) + pad('<Circle diameter="1"/>'), function='SILKSCREEN'))
    assert layer_geometry(parsed).area == pytest.approx(math.pi / 4, rel=0.01)
    assert any('unsupported feature' in warning for warning in parsed.warnings)


@pytest.mark.parametrize('text, message', [
    ('<IPC-2581 revision="C" xmlns="%s"><Content>' % NS, 'Malformed XML'),
    ('<Other/>', 'Not an IPC-2581'),
    ('<IPC-2581 revision="A" xmlns="%s"/>' % NS, 'revision'),
    ('<IPC-2581 revision="C" xmlns="%s"><Bom name="x"/></IPC-2581>' % NS, 'no Ecad'),
    (doc(pad('<Circle diameter="x"/>')), 'not a number'),
    (doc(pad('<StandardPrimitiveRef id="missing"/>')), 'Unknown StandardPrimitiveRef'),
    (doc('', function='DIELCORE', layer_features=''), 'no manufacturing geometry'),
    (doc(layer_features='<LayerFeature layerRef="nowhere"><Set/></LayerFeature>'), 'unknown layer'),
])
def test_malformed_input_raises_ipc2581_parse_error(text, message):
    with pytest.raises(IPC2581ParseError, match=message):
        IPC2581Parser(circle_steps=64).parse_bytes(text.encode('utf-8'))


def test_entities_and_external_files_are_never_read(tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('TOP-SECRET-VALUE')
    external = ('<?xml version="1.0"?><!DOCTYPE IPC-2581 [<!ENTITY xxe SYSTEM "%s">]><IPC-2581 revision="C" '
                'xmlns="%s"><Content roleRef="&xxe;"/></IPC-2581>' % (secret.as_uri(), NS))
    bomb = ('<?xml version="1.0"?><!DOCTYPE IPC-2581 [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><IPC-2581 revision="C" xmlns="%s"><Content roleRef="&c;"/>'
            '</IPC-2581>' % NS)
    for text in (external, bomb):
        path = tmp_path / 'board.xml'
        path.write_text(text)
        with pytest.raises(IPC2581ParseError, match='entities') as info:
            IPC2581Parser().parse_file(path)
        assert 'TOP-SECRET-VALUE' not in str(info.value)


def test_zip_gzip_input_and_step_selection(tmp_path):
    xml = doc(pad('<Circle diameter="2"/>')).replace('</CadData>', '<Step name="PANEL" type="PALLET"><Datum x="0" '
                                                     'y="0"/><StepRepeat stepRef="S" x="0" y="0" nx="2" ny="1" '
                                                     'dx="10" dy="0" angle="0" mirror="false"/></Step></CadData>')
    zipped = tmp_path / 'board.zip'
    with zipfile.ZipFile(zipped, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('board.xml', xml)
    gzipped = tmp_path / 'board.xml.gz'
    gzipped.write_bytes(gzip.compress(xml.encode('utf-8')))
    for path in (zipped, gzipped):
        parsed = IPC2581Parser(circle_steps=64).parse_file(path)
        assert parsed.step == 'S' and parsed.steps == ['S', 'PANEL']
        assert layer_geometry(parsed).area == pytest.approx(math.pi, rel=0.01)
    with pytest.raises(IPC2581ParseError, match='panel'):
        IPC2581Parser(circle_steps=64).parse_file(gzipped, step='PANEL')
    two = tmp_path / 'two.zip'
    with zipfile.ZipFile(two, 'w') as archive:
        archive.writestr('a.xml', xml)
        archive.writestr('b.xml', xml)
    with pytest.raises(IPC2581ParseError, match='exactly 1'):
        IPC2581Parser().parse_file(two)


def test_import_dialog_entry_creates_named_objects_and_a_recent_file():
    # the app path: Import dialog -> file chooser -> worker parse -> layer dialog -> Gerber and Excellon objects
    from tests.helpers_objects import pcb_import_stub, run_pcb_import
    app, io_handler, objects = pcb_import_stub()
    filename = str(FIXTURE)

    def choose(selection):
        from PyQt6 import QtWidgets
        assert selection.windowTitle() == 'Import IPC-2581 Board'
        for checkbox in selection.findChildren(QtWidgets.QCheckBox):
            checkbox.setChecked(checkbox.text() == 'F.Cu' or checkbox.text().endswith(' PTH'))

    shown, parsed = run_pcb_import(app, io_handler, 'ipc2581', filename, choose)
    assert len(shown) == 1
    # F.Cu and the 2 plated drill groups of the board
    assert [kind for kind, name, obj in objects] == ['gerber', 'excellon', 'excellon']
    # board.xml.gz gives objects named after the board, not after 'board.xml'
    assert all(name.startswith('Easyduino_RP2040') and '.xml' not in name for kind, name, obj in objects)
    assert unary_union(objects[0][2].solid_geometry).area == pytest.approx(layer_geometry(parsed, 'F.Cu').area)
    app.file_opened.emit.assert_called_once_with('ipc2581', filename)
