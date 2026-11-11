"""EasyEDA Pro board reader: a real V3 project against the Gerber files of EasyEDA Pro, and focused synthetic V2 projects."""
import json
import math
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from shapely import Point
from shapely.ops import unary_union

FIXTURES = Path(__file__).parent / 'fixtures' / 'easyeda_pro'
PROJECT = FIXTURES / 'Agent-Light-v1-layout.epro2'
GERBERS = FIXTURES / 'Agent-Light-RevA-DRAFT-Gerber.zip'


def parser(**kwargs):
    from appParsers.ParseEasyEDAPro import EasyEDAProParser
    return EasyEDAProParser(circle_steps=64, **kwargs)


@lru_cache(maxsize=None)
def real():
    return parser().parse_file(PROJECT)


def union(parsed, layer):
    return unary_union(parsed.layers[layer]['solid_geometry'])


@lru_cache(maxsize=None)
def gerber(member):
    """The geometry (mm) of one Gerber file of EasyEDA Pro, read with the Gerber parser of the application."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from appParsers.ParseGerber import Gerber
    app = MagicMock()
    app.decimals, app.app_units, app.abort_flag = 6, 'MM', False
    app.options = {'gerber_use_buffer_for_union': True, 'gerber_clean_apertures': True, 'gerber_extra_buffering': False,
                   'gerber_def_units': 'IN', 'gerber_def_zeros': 'L', 'gerber_buffering': 'full',
                   'gerber_simplification': False, 'gerber_simp_tolerance': 0.0005, 'gerber_circle_steps': 64}
    app.defaults = app.options
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile(GERBERS) as archive:
            archive.extract(member, folder)
        film = Gerber(app, steps_per_circle=64)
        assert film.parse_file(os.path.join(folder, member)) != 'fail'
        geometry = film.solid_geometry
        return unary_union(geometry) if isinstance(geometry, list) else geometry


def drill_file(member):
    """{tool diameter: [drills, slots]} of an Excellon file of EasyEDA Pro."""
    with zipfile.ZipFile(GERBERS) as archive:
        text = archive.read(member).decode('ascii', 'ignore')
    sizes = dict((int(number), round(float(size), 3)) for number, size in re.findall(r'^T(\d+)C([\d.]+)', text, re.M))
    result, tool = {}, None
    for line in text.splitlines():
        if re.fullmatch(r'T\d+', line):
            tool = sizes[int(line[1:])]
        elif line.startswith('X') and tool is not None:
            entry = result.setdefault(tool, [0, 0])
            entry[1 if 'G85' in line else 0] += 1
    return result


def drills(parsed, *groups):
    result = {}
    for group in groups:
        for tool in parsed.drill_groups.get(group, {}).values():
            entry = result.setdefault(round(tool['tooldia'], 3), [0, 0])
            entry[0] += len(tool['drills'])
            entry[1] += len(tool['slots'])
    return result


# ---------------------------------------------------------------------------------------------------------------
# a real V3 project (MIT, EasyEDA Pro 4.1.78) against the Gerber files that EasyEDA Pro wrote at the same time
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('layer, member, limit', [
    ('Top Layer', 'Gerber_TopLayer.GTL', 0.01),
    ('Bottom Layer', 'Gerber_BottomLayer.GBL', 0.01),
    ('Top Solder Mask Layer', 'Gerber_TopSolderMaskLayer.GTS', 0.01),
    ('Bottom Solder Mask Layer', 'Gerber_BottomSolderMaskLayer.GBS', 0.01),
    ('Top Paste Mask Layer', 'Gerber_TopPasteMaskLayer.GTP', 0.02),
    ('Board Outline Layer', 'Gerber_BoardOutlineLayer.GKO', 0.002)])
def test_v3_layers_match_the_gerbers(layer, member, limit):
    # copper = tracks, vias, pads of both sides (rotated parts, polygon pads, slots) and the stored pour fills
    mine, reference = union(real(), layer), gerber(member)
    assert mine.symmetric_difference(reference).area < limit * reference.area


def test_v3_drills_match_the_gerbers():
    parsed = real()
    assert drills(parsed, 'PTH_Through', 'PTH_Through_Via') == drill_file('Drill_PTH_Through.DRL')
    assert drills(parsed, 'PTH_Through_Via') == drill_file('Drill_PTH_Through_Via.DRL')
    assert drills(parsed, 'NPTH_Through') == drill_file('Drill_NPTH_Through.DRL')     # fills on the multi-layer
    assert real().drill_groups['PTH_Through']
    assert any(tool['slots'] for tool in parsed.drill_groups['PTH_Through'].values())


def test_v3_result_shape_and_text_warning():
    parsed = real()
    assert parsed.source_kind == 'easyeda_pro' and parsed.pcb == 'PCB1' and parsed.pcbs == ['PCB1']
    assert math.isclose(parsed.board_outline.area, 12 * 40, rel_tol=1e-6)
    assert parsed.default_layers and set(parsed.default_layers) <= set(parsed.layers)
    layer = parsed.layers['Top Layer']
    assert set(layer) >= {'name', 'solid_geometry', 'follow_geometry', 'tools'}
    assert any('Text is not stored as polygons' in warning for warning in parsed.warnings)


# ---------------------------------------------------------------------------------------------------------------
# synthetic V2 projects (record layout of github.com/easyeda/easyeda-file-format-v2, checked against real files)
# ---------------------------------------------------------------------------------------------------------------

LAYERS = [(1, 'TOP', 'Top Layer'), (2, 'BOTTOM', 'Bottom Layer'), (3, 'TOP_SILK', 'Top Silkscreen Layer'),
          (4, 'BOT_SILK', 'Bottom Silkscreen Layer'), (5, 'TOP_SOLDER_MASK', 'Top Solder Mask Layer'),
          (6, 'BOT_SOLDER_MASK', 'Bottom Solder Mask Layer'), (7, 'TOP_PASTE_MASK', 'Top Paste Mask Layer'),
          (8, 'BOT_PASTE_MASK', 'Bottom Paste Mask Layer'), (11, 'OUTLINE', 'Board Outline Layer'), (12, 'MULTI', 'Multi-Layer')]


def lines(*records):
    return '\n'.join(json.dumps(record, separators=(',', ':')) for record in records) + '\n'


def pcb_text(*records, version='1.8'):
    head = [['DOCTYPE', 'PCB', version], ['CANVAS', 0, 0, 'mil', 5, 5]]
    head += [['LAYER', number, kind, name, 3] for number, kind, name in LAYERS]
    head += [['RULE', '9', 'solderMaskExpansion', 1, ['mm', 2, 2, -1000, -1000, 2, 2]],
             ['RULE', '8', 'pasteMaskExpansion', 1, ['mm', 0, 0, -1000, -1000]]]
    return lines(*head, *records)


def footprint_text(*records):
    return lines(['DOCTYPE', 'FOOTPRINT', '1.8'], *records)


def pad(pad_id, number, x, y, layer=1, hole=None, shape=('RECT', 20, 10, 0), special=(), angle=0, plated=1):
    return ['PAD', pad_id, 0, '', layer, number, x, y, angle, hole, list(shape), list(special), 0, 0, 0, plated, 0,
            None, None, None, None, 0]


def project(tmp_path, pcb, footprints=None, name='board.epro', extra=None, titles=None):
    footprints = footprints if footprints is not None else {'fp1': footprint_text(pad('e5', '1', 10, 4))}
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps({'pcbs': titles or {'pcb1': 'Board'}}))
        for pcb_id, text in (pcb if isinstance(pcb, dict) else {'pcb1': pcb}).items():
            archive.writestr('PCB/%s' % pcb_id, text)
        for uuid, text in footprints.items():
            archive.writestr('FOOTPRINT/%s.efoo' % uuid, text)
        for member, data in (extra or {}).items():
            archive.writestr(member, data)
    return path


def part(layer, x, y, angle, footprint='fp1', comp_id='e1'):
    return [['COMPONENT', comp_id, 0, layer, x, y, angle, {}],
            ['ATTR', comp_id + 'e1', 0, comp_id, 3, None, None, 'Footprint', footprint, 0, 0],
            ['ATTR', comp_id + 'e2', 0, comp_id, 3, 0, 0, 'Designator', 'U1', 0, 0]]


def rotate_mil(x, y, angle):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return x * c - y * s, x * s + y * c


@pytest.mark.parametrize('side, layer_id, copper, expect', [
    ('top', 1, 'Top Layer', lambda p, a: rotate_mil(p[0], p[1], a)),
    ('bottom', 2, 'Bottom Layer', lambda p, a: rotate_mil(p[0], -p[1], a))])
def test_footprint_placement_at_a_non_zero_angle(tmp_path, side, layer_id, copper, expect):
    # a bottom part is mirrored in Y and then turned by its angle (TRACE easyeda-pro.ts; checked against Agni-keyboard tracks)
    path = project(tmp_path, pcb_text(*part(layer_id, 500, 300, 30)))
    parsed = parser().parse_file(path)
    shape = union(parsed, copper)
    center = expect((10, 4), 30)
    assert shape.centroid.distance(Point((500 + center[0]) * 0.0254, (300 + center[1]) * 0.0254)) < 1e-3
    corner = expect((10 + 10, 4 + 5), 30)                     # a corner of the 20 x 10 mil pad
    assert shape.buffer(1e-4).contains(Point((500 + corner[0]) * 0.0254, (300 + corner[1]) * 0.0254))
    assert math.isclose(shape.area, 20 * 10 * 0.0254 ** 2, rel_tol=1e-6)
    mask = 'Top Solder Mask Layer' if side == 'top' else 'Bottom Solder Mask Layer'
    assert math.isclose(union(parsed, mask).area, 24 * 14 * 0.0254 ** 2, rel_tol=0.02)     # 2 mil mask expansion per side


def test_pour_uses_the_stored_fill(tmp_path):
    # POURED coordinates are in 0.01 inch; the pour width is added around the fill
    records = [['POUR', 'e8', 0, 'GND', 1, 0.2, 'POUR1', 0, [['R', 0, 0, 100, -50, 0, 0]], ['SOLID', 8], 0, 0],
               ['POURED', 'e9', 'e8', 0, True, [[0, 0, 'L', 10, 0, 10, -5, 0, -5]]]]
    parsed = parser().parse_file(project(tmp_path, pcb_text(*records)))
    bounds = union(parsed, 'Top Layer').bounds
    assert bounds == pytest.approx((-0.1, -1.37, 2.64, 0.1), abs=1e-6)


def test_pour_without_stored_fill_fails(tmp_path):
    records = [['POUR', 'e8', 0, 'GND', 1, 0.2, 'POUR1', 0, [['R', 0, 0, 100, -50, 0, 0]], ['SOLID', 8], 0, 0]]
    from appParsers.ParseEasyEDAPro import EasyEDAProParseError
    with pytest.raises(EasyEDAProParseError, match='POUR1.*no stored fill'):
        parser().parse_file(project(tmp_path, pcb_text(*records)))


def test_holes_plated_slot_and_non_plated(tmp_path):
    foot = footprint_text(pad('e5', '1', 0, 0, layer=12, hole=['ROUND', 40, 20], shape=('ELLIPSE', 60, 60)),
                          pad('e6', '2', 100, 0, layer=12, hole=['ROUND', 30, 30], shape=('ELLIPSE', 50, 50)),
                          ['FILL', 'e7', 0, '', 12, 0.2, 0, [['CIRCLE', 200, 0, 10]], 0])
    parsed = parser().parse_file(project(tmp_path, pcb_text(*part(1, 0, 0, 0)), {'fp1': foot}))
    plated = parsed.drill_groups['PTH_Through']
    slots = [(tool['tooldia'], slot) for tool in plated.values() for slot in tool['slots']]
    assert len(slots) == 1 and slots[0][0] == pytest.approx(20 * 0.0254)
    (start, end) = slots[0][1]
    assert (end.x - start.x) == pytest.approx(20 * 0.0254) and end.y == pytest.approx(start.y)      # length 40 mil
    round_hole = [tool for tool in plated.values() if tool['drills']]
    assert round_hole[0]['tooldia'] == pytest.approx(30 * 0.0254) and len(round_hole[0]['drills']) == 1
    nplated = parsed.drill_groups['NPTH_Through']
    (tool,) = nplated.values()
    assert tool['tooldia'] == pytest.approx(20 * 0.0254) and tool['drills'][0].x == pytest.approx(200 * 0.0254)
    assert union(parsed, 'Top Layer').area > 0 and 'NPTH_Through' in parsed.drill_groups


def test_outline_with_cutout_and_keepout_warning(tmp_path):
    records = [['POLY', 'e1', 0, '', 11, 10, [['R', 0, 0, 1000, -500, 0, 0]][0], 0],
               ['POLY', 'e2', 0, '', 11, 10, ['CIRCLE', 500, -250, 50], 0],
               ['REGION', 'e3', 0, 1, 0.2, [7], [['R', 0, 0, 100, -50, 0, 0]], 0]]
    parsed = parser().parse_file(project(tmp_path, pcb_text(*records)))
    outline = parsed.board_outline
    assert outline.bounds == pytest.approx((0, -12.7, 25.4, 0), abs=1e-6)
    assert len(outline.interiors) == 1 if outline.geom_type == 'Polygon' else True
    assert outline.area == pytest.approx(25.4 * 12.7 - math.pi * 1.27 ** 2, rel=2e-3)
    assert any('Keepout' in warning for warning in parsed.warnings)
    assert parsed.layers['Board Outline Layer']['follow_geometry']


@pytest.mark.parametrize('what, records, foot, message', [
    ('ngon pad', [], footprint_text(pad('e5', '7', 0, 0, shape=('NGON', 30, 6))), 'unsupported shape'),
    ('special pad', [], footprint_text(pad('e5', '8', 0, 0, special=[[1, 2, ['RECT', 20, 20, 0]]])), 'special pad'),
    ('blind via', [['VIA', 'e9', 0, 'N', 'blind1', 10, 10, 12, 24, 0, None, None, 0]], None, 'blind or buried'),
    ('grid fill', [['FILL', 'e9', 0, '', 1, 0.2, 1, [[0, 0, 'L', 10, 0, 10, -5]], 0]], None, 'grid or internal plane'),
    ('missing footprint', 'part', {}, 'no footprint'),
])
def test_unsupported_copper_fails_by_name(tmp_path, what, records, foot, message):
    from appParsers.ParseEasyEDAPro import EasyEDAProParseError
    if records == 'part':
        records = part(1, 0, 0, 0, footprint='ghost')
    elif foot is not None:
        records = part(1, 0, 0, 0)
    feet = {} if what == 'missing footprint' else ({'fp1': foot} if foot is not None else None)
    with pytest.raises(EasyEDAProParseError, match=message):
        parser().parse_file(project(tmp_path, pcb_text(*records), feet))


def test_internal_plane_layer_fails(tmp_path):
    from appParsers.ParseEasyEDAPro import EasyEDAProParseError
    text = pcb_text(['LAYER', 15, 'PLANE', 'Inner1', 3], ['LINE', 'e1', 0, 'A', 1, 0, 0, 100, 0, 10, 0])
    with pytest.raises(EasyEDAProParseError, match='internal plane'):
        parser().parse_file(project(tmp_path, text))


def test_import_of_a_project_with_two_pcbs_asks_for_the_pcb(tmp_path):
    # the app path: Import dialog -> file chooser -> PCB choice -> parse of that PCB -> layer dialog -> objects
    from unittest.mock import patch
    from PyQt6 import QtWidgets
    from tests.helpers_objects import pcb_import_stub, run_pcb_import

    records = [['LINE', 'e1', 0, 'A', 1, 0, 0, 100, 0, 10, 0]]
    path = project(tmp_path, {'pcb1': pcb_text(*records), 'pcb2': pcb_text(['LINE', 'e1', 0, 'B', 2, 0, 0, 200, 0, 10, 0])},
                   titles={'pcb1': 'First', 'pcb2': 'Second'})
    app, io_handler, objects = pcb_import_stub()

    def choose(selection):
        assert selection.windowTitle() == 'Import EasyEDA Pro PCB'
        assert 'PCB: Second' in [label.text() for label in selection.findChildren(QtWidgets.QLabel)]

    with patch.object(QtWidgets.QInputDialog, 'getItem', return_value=('Second', True)) as ask:
        shown, parsed = run_pcb_import(app, io_handler, 'easyeda_pro', str(path), choose)
    assert ask.call_args.args[3] == ['First', 'Second']
    assert len(shown) == 1 and parsed.pcb == 'Second'
    # only the bottom copper of the second PCB
    assert [kind for kind, name, obj in objects] == ['gerber']
    assert unary_union(objects[0][2].solid_geometry).bounds[2] == pytest.approx(200 * 0.0254, abs=0.2)
    app.file_opened.emit.assert_called_once_with('easyeda_pro', str(path))


def zip_bomb(tmp_path):
    path = tmp_path / 'bomb.epro'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', '{}')
        archive.writestr('PCB/x', b'0' * (40 << 20))
    return path


def traversal(tmp_path):
    path = tmp_path / 'evil.epro'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('project.json', '{}')
        archive.writestr('PCB/../../evil', 'x')
    return path


def malformed_line(tmp_path):
    return project(tmp_path, pcb_text(['LINE', 'e1']) + 'this is not json\n')


def not_a_project(tmp_path):
    path = tmp_path / 'other.epro'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('readme.txt', 'x')
    return path


def truncated_zip(tmp_path):
    path = project(tmp_path, pcb_text(['LINE', 'e1', 0, 'A', 1, 0, 0, 100, 0, 10, 0]))
    path.write_bytes(path.read_bytes()[:-30])
    return path


def bare_pcb_with_part(tmp_path):
    path = tmp_path / 'board.epcb'
    path.write_text(pcb_text(*part(1, 0, 0, 0)), encoding='utf-8')
    return path


@pytest.mark.parametrize('make', [zip_bomb, traversal, malformed_line, not_a_project, truncated_zip, bare_pcb_with_part])
def test_bad_archives_raise_the_parse_error(tmp_path, make):
    from appParsers.ParseEasyEDAPro import EasyEDAProParseError
    with pytest.raises(EasyEDAProParseError):
        parser().parse_file(make(tmp_path))
