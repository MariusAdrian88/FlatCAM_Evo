"""
Freeform cutout (``CutOut.on_freeform_cutout``): golden files of the output of the code before the F10 change, and
the F10 tests (inner contours of a board outline).

The golden tests run the production path on a headless ``CutOut`` and compare ``solid_geo``, ``gaps_solid_geo`` and the
mouse-bite ``holes`` with ``tests/fixtures/cutout/golden_<name>.json`` (WKT, 6 decimals).
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import shapely
from PyQt6 import QtGui, QtWidgets
from shapely.geometry import Polygon, box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers_objects import _headless_qt, make_app, make_gerber  # noqa: F401,E402

FIX = Path(__file__).parent / 'fixtures' / 'cutout'


# ---------------------------------------------------------------------------------------------------------------
# headless harness
# ---------------------------------------------------------------------------------------------------------------

class _Collection(QtGui.QStandardItemModel):
    """Stands in for ObjectCollection: the combo boxes need a real model, the tool needs a few methods."""

    def __init__(self):
        super().__init__()
        self.objs = {}
        self.promises = set()

    def get_by_name(self, name):
        return self.objs[name]

    def promise(self, name):
        self.promises.add(name)

    def remove_promise(self, name):
        self.promises.discard(name)

    def has_promises(self):
        return bool(self.promises)


def make_geometry(app, name, geos):
    from appObjects.GeometryObject import GeometryObject
    obj = GeometryObject(name, app)
    obj.solid_geometry = geos
    obj.multigeo = False
    return obj


def geometry_sources(app):
    return {
        'geo_single': make_geometry(app, 'geo_single', Polygon([(0, 0), (50, 0), (50, 30), (0, 30)])),
        'geo_slot': make_geometry(app, 'geo_slot', [box(0, 0, 50, 30), box(20, 13.5, 30, 16.5)]),
        'geo_panel2': make_geometry(app, 'geo_panel2', [box(0, 0, 40, 25), box(50, 0, 90, 25)]),
    }


def load_source(app, name):
    if name.startswith('geo_'):
        return geometry_sources(app)[name]
    return make_gerber(app, str(FIX / (name + '.gbr')), name=name + '.gbr')


def make_tool():
    from appPlugins.ToolCutOut import CutOut
    app = make_app()
    # the fixtures are parsed with a fixed count of circle segments, so the golden files do not depend on the
    # adaptive tolerance
    app.options['gerber_circle_steps_mode'] = 'fixed'
    app.collection = _Collection()
    app.resource_location = 'res'
    app.plotcanvas = Mock()
    app.proj_selection_changed = Mock()
    app.worker_task = SimpleNamespace(emit=lambda d: d['fcn'](*d['params']))
    app.created = {}
    app.app = app
    app.f_handlers = Mock()

    def new_object(kind, name, init, autoselected=False):
        if kind == 'geometry':
            from appObjects.GeometryObject import GeometryObject
            obj = GeometryObject(name, app)
        else:
            from appObjects.ExcellonObject import ExcellonObject
            obj = ExcellonObject(name, app)
        ret = init(obj, app)
        if ret == 'fail':
            app.collection.remove_promise(name)
            return 'fail'
        app.collection.remove_promise(name)
        app.created[name] = obj
        return None

    app.app_obj = SimpleNamespace(new_object=new_object)
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        tool = CutOut(app)
    tool.cut_tool_dict = {'tooldia': 2.4, 'data': {}, 'solid_geometry': []}
    tool.test_app = app
    return tool


@pytest.fixture
def cutout_app():
    return make_tool()


# the defaults of the plugin: tool 2.4, margin 0.1, 4 gaps of 4 mm, thin gap depth -1.0, bites 0.6 / 0.3
BASE = dict(dia=2.4, margin=0.1, gaps='4', gapsize=4.0, gap_type=0, thin=-1.0, mb_dia=0.6, mb_spacing=0.3,
            convex=False, kind='single')

# name: (source, overrides)
CASES = {
    'single_gerber': ('outline_single', {}),
    'panel_gerber': ('outline_panel2', {'kind': 'panel'}),
    'single_geometry': ('geo_single', {}),
    'panel_geometry': ('geo_panel2', {'kind': 'panel'}),
    'geo_panel2_single': ('geo_panel2', {}),
    'neg_single_gerber': ('outline_single', {'margin': -0.3}),
    'neg_panel_gerber': ('outline_single', {'margin': -0.3, 'kind': 'panel'}),
    'neg_single_geometry': ('geo_single', {'margin': -0.3}),
    'neg_panel_geometry': ('geo_single', {'margin': -0.3, 'kind': 'panel'}),
    'thin_gaps_gerber': ('outline_single', {'gap_type': 1}),
    'mouse_bites_gerber': ('outline_single', {'gap_type': 2}),
    'mouse_bites_panel_gerber': ('outline_panel2', {'gap_type': 2, 'kind': 'panel'}),
    'mouse_bites_geometry': ('geo_single', {'gap_type': 2}),
    'slot_single': ('outline_slot', {}),
    'slot_panel': ('outline_slot', {'kind': 'panel'}),
    'island_slot_single': ('outline_island_slot', {}),
    'panel2_single': ('outline_panel2', {}),
    'clearance_single': ('copper_clearance', {}),
    'clearance_panel': ('copper_clearance', {'kind': 'panel'}),
    'region_cutout_single': ('outline_region_cutout', {}),
    'region_cutout_panel': ('outline_region_cutout', {'kind': 'panel'}),
    'rect_aperture_single': ('outline_rect_aperture', {}),
    'narrow_slot_single': ('outline_narrow_slot', {}),
    'slot_convex': ('outline_slot', {'convex': True}),
    'geo_slot_single': ('geo_slot', {}),
    'no_gaps_gerber': ('outline_single', {'gaps': 'None'}),
}


def configure(tool, name, params):
    """Put the plugin controls to the values of ``params``."""
    ui = tool.ui
    ui.obj_combo = SimpleNamespace(currentText=lambda: name)
    ui.dia.set_value(params['dia'])
    ui.margin.set_value(params['margin'])
    ui.gaps.set_value(params['gaps'])
    ui.gapsize.set_value(params['gapsize'])
    ui.gaptype_combo.set_value(params['gap_type'])
    ui.thin_depth_entry.set_value(params['thin'])
    if params['gap_type'] == 1:
        ui.cutz_entry.set_value(-1.6)
    ui.mb_dia_entry.set_value(params['mb_dia'])
    ui.mb_spacing_entry.set_value(params['mb_spacing'])
    ui.convex_box_cb.set_value(params['convex'])
    ui.obj_kind_combo.set_value(params['kind'])


def flat(geo):
    if geo is None:
        return []
    if isinstance(geo, (list, tuple)):
        out = []
        for g in geo:
            out.extend(flat(g))
        return out
    if hasattr(geo, 'geoms'):
        return list(geo.geoms)
    return [geo]


def wkt(geo):
    return shapely.to_wkt(geo, rounding_precision=6, trim=False)


def run_case(tool, source_name, overrides, after_configure=None):
    """Run the production freeform cutout. Returns solid_geo / gaps_solid_geo / holes as lists of rounded WKT."""
    app = tool.test_app
    obj = load_source(app, source_name)
    app.collection.objs[obj.obj_options['name']] = obj
    params = dict(BASE)
    params.update(overrides)
    configure(tool, obj.obj_options['name'], params)
    if after_configure:
        after_configure(tool)
    app.created.clear()
    tool.on_freeform_cutout()
    out = {'solid_geo': [], 'gaps_solid_geo': [], 'holes': []}
    for name, created in app.created.items():
        if name.endswith('_cutout'):
            tools = created.tools
            out['solid_geo'] = [wkt(g) for g in flat(tools[1]['solid_geometry'])]
            if 99 in tools:
                out['gaps_solid_geo'] = [wkt(g) for g in flat(tools[99]['solid_geometry'])]
        else:
            drills = [d for t in created.tools.values() for d in t['drills']]
            out['holes'] = [wkt(d) for d in drills]
    return out


def golden_path(case):
    return FIX / ('golden_%s.json' % case)


def load_golden(case):
    return json.loads(golden_path(case).read_text())


PANEL_CASES = {'panel_gerber', 'slot_panel', 'clearance_panel', 'region_cutout_panel',
               'mouse_bites_panel_gerber'}
PANEL_FULL_CONTOURS = {
    'panel_gerber': 3,
    'slot_panel': 2,
    'clearance_panel': 1,
    'region_cutout_panel': 1,
    'mouse_bites_panel_gerber': 3,
}
GAP_COUNTS = {'lr': 2, 'tb': 2, '2lr': 4, '2tb': 4, '4': 4, '8': 8}


def _linework(values):
    return [shapely.from_wkt(value) for value in values]


def _expected_holes(rows, params):
    expected = []
    gap_length = params['gapsize'] + abs(params['dia']) - abs(params['dia']) / 2.0
    for row in rows:
        __, removed = _cutout_intervals(row, abs(params['dia']), params['gaps'], gap_length)
        for line in removed:
            distance = 0.0
            while distance <= line.length:
                expected.append(line.interpolate(distance))
                distance += params['mb_dia'] + params['mb_spacing']
    return expected


def _assert_panel_output(tool, case, source, overrides, full, actual):
    params = dict(BASE)
    params.update(overrides)
    full_paths = flat(unary_union(_linework(full['solid_geo'])))
    actual_paths = flat(unary_union(_linework(actual['solid_geo'])))
    assert full_paths and actual_paths
    assert len(full_paths) == PANEL_FULL_CONTOURS[case]

    expected_cut = []
    expected_gaps = []
    for path in full_paths:
        cut, gaps = _cutout_intervals(
            path, abs(params['dia']), params['gaps'],
            params['gapsize'] + abs(params['dia']))
        expected_cut.extend(cut)
        expected_gaps.extend(gaps)

    full_union = unary_union(full_paths)
    actual_union = unary_union(actual_paths)
    expected_union = unary_union(expected_cut)
    expected_removed = sum(gap.length for gap in expected_gaps)
    expected_removed_independent = (
        PANEL_FULL_CONTOURS[case] * GAP_COUNTS[str(params['gaps']).lower()] *
        (params['gapsize'] + abs(params['dia'])))
    assert expected_removed == pytest.approx(expected_removed_independent, abs=2e-6)
    assert full_union.length - actual_union.length == pytest.approx(expected_removed, abs=2e-6)
    assert actual_union.hausdorff_distance(expected_union) < 2e-6
    assert actual_union.difference(full_union.buffer(2e-6)).length < 2e-6
    assert expected_union.difference(actual_union.buffer(2e-6)).length < 2e-6

    if overrides.get('gap_type') == 2:
        source_obj = load_source(tool.test_app, source)
        if source_obj.kind == 'gerber':
            rows = []
            for geometry in flat(cutout_module.gerber_cutout_geometry(source_obj, False)):
                rows.append(geometry.buffer(params['margin'] + params['mb_dia'] / 2.0).exterior)
        else:
            rows = [path.buffer(params['margin']).exterior for path in full_paths]
        expected_holes = _expected_holes(rows, params)
        actual_holes = [shapely.from_wkt(value) for value in actual['holes']]
        assert actual_holes
        assert len(actual_holes) == len(expected_holes)
        assert max(point.distance(expected) for point in actual_holes for expected in [min(expected_holes, key=point.distance)]) < 2e-6
        assert max(expected.distance(actual) for expected in expected_holes for actual in [min(actual_holes, key=expected.distance)]) < 2e-6
    else:
        assert not actual['holes']


def check_golden(tool, case):
    source, overrides = CASES[case]
    full = run_case(tool, source, dict(overrides, gaps='None', gap_type=0)) if case in PANEL_CASES else None
    got = run_case(tool, source, overrides)
    expected = load_golden(case)
    if got == expected:
        return

    if case in PANEL_CASES:
        _assert_panel_output(tool, case, source, overrides, full, got)
        return

    if case == 'mouse_bites_geometry':
        params = dict(BASE)
        params.update(overrides)
        source_geo = unary_union(load_source(tool.test_app, source).solid_geometry)
        row = source_geo.buffer(params['margin']).exterior
        actual_holes = [shapely.from_wkt(value) for value in got['holes']]
        expected_holes = _expected_holes([row], params)
        assert actual_holes
        assert len(actual_holes) == len(expected_holes)
        assert max(point.distance(expected) for point in actual_holes for expected in [min(expected_holes, key=point.distance)]) < 2e-6
        assert max(expected.distance(actual) for expected in expected_holes for actual in [min(actual_holes, key=expected.distance)]) < 2e-6

    # Arclength splitting changes WKT ordering and multipart boundaries while preserving the
    # machined geometry. Keep exact drill coordinates and reject real geometry changes.
    if case != 'mouse_bites_geometry':
        assert sorted(got['holes']) == sorted(expected['holes']), case
    for key in ('solid_geo', 'gaps_solid_geo'):
        got_union = unary_union([shapely.from_wkt(value) for value in got[key]])
        expected_union = unary_union([shapely.from_wkt(value) for value in expected[key]])
        if got_union.is_empty or expected_union.is_empty:
            assert got_union.is_empty and expected_union.is_empty, '%s: %s' % (case, key)
            continue
        assert got_union.length == pytest.approx(expected_union.length, abs=2e-6), '%s: %s' % (case, key)
        assert got_union.hausdorff_distance(expected_union) < 2e-6, '%s: %s' % (case, key)
        assert got_union.difference(expected_union.buffer(2e-6)).length < 2e-6, '%s: %s' % (case, key)
        assert expected_union.difference(got_union.buffer(2e-6)).length < 2e-6, '%s: %s' % (case, key)


# ---------------------------------------------------------------------------------------------------------------
# the output of the code before the change (the option is absent)
# ---------------------------------------------------------------------------------------------------------------

def test_off_single_gerber_matches_golden(cutout_app):
    for case in ('single_gerber', 'slot_single', 'island_slot_single', 'clearance_single', 'region_cutout_single',
                 'narrow_slot_single', 'rect_aperture_single', 'no_gaps_gerber'):
        check_golden(cutout_app, case)


def test_off_gaps_thin_and_bites_match_golden(cutout_app):
    for case in ('thin_gaps_gerber', 'mouse_bites_gerber', 'mouse_bites_panel_gerber',
                 'mouse_bites_geometry', 'slot_convex'):
        check_golden(cutout_app, case)


# ---------------------------------------------------------------------------------------------------------------
# the inner contours (F10)
# ---------------------------------------------------------------------------------------------------------------

from shapely.geometry import LineString  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import appPlugins.ToolCutOut as cutout_module  # noqa: E402
from appPlugins.ToolCutOut import _cutout_intervals, freeform_cutout_paths, offset_contours, outline_contours  # noqa: E402

from copy import deepcopy  # noqa: E402
from shapely.affinity import rotate  # noqa: E402
from shapely.geometry import LinearRing, Point  # noqa: E402
from shapely.ops import linemerge  # noqa: E402
from appPlugins.ToolCutOut import _manual_gap_geometry, _manual_subtract_selected  # noqa: E402

NO_GAPS = {'gaps': 'None'}
# a small slot needs a small tool and small gaps
SMALL = {'dia': 0.8, 'margin': 0.0, 'gapsize': 1.0}


def new_paths(tool, source, inner_gaps=False, obj=None, **overrides):
    """freeform_cutout_paths with the option on, on a fixture (or on ``obj``)."""
    p = dict(BASE)
    p.update(overrides)
    if obj is None:
        obj = load_source(tool.test_app, source)
    return freeform_cutout_paths(
        obj, p['kind'], p['dia'], p['margin'], p['gaps'], p['gapsize'], p['convex'], p['gap_type'], p['thin'],
        p['mb_dia'], p['mb_spacing'], tool.any_cutout_handler, inner_active=True, inner_gaps=inner_gaps)


def old_paths(tool, source, **overrides):
    p = dict(BASE)
    p.update(overrides)
    obj = load_source(tool.test_app, source)
    return freeform_cutout_paths(
        obj, p['kind'], p['dia'], p['margin'], p['gaps'], p['gapsize'], p['convex'], p['gap_type'], p['thin'],
        p['mb_dia'], p['mb_spacing'], tool.any_cutout_handler)


def bounds(geo):
    return tuple(round(v, 6) for v in geo.bounds)


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def mark_inner(tool, gaps=False):
    tool.ui.is_outline_cb.set_value(True)
    tool.ui.inner_cb.set_value(True)
    tool.ui.inner_gaps_cb.set_value(gaps)


def test_copper_with_clearance_gives_no_slot(cutout_app):
    got = run_case(cutout_app, 'copper_clearance', {}, after_configure=lambda t: t.ui.inner_cb.set_value(True))
    assert got == load_golden('clearance_single')
    hole = box(17, 9.5, 23, 15.5)
    assert not any(shapely.from_wkt(w).intersects(hole) for w in got['solid_geo'])


def test_region_hole_is_cutout_when_marked(cutout_app):
    grb = load_source(cutout_app.test_app, 'outline_region_cutout')
    contours = outline_contours(grb, False, 'single')
    assert [(c.side, c.depth) for c in contours] == [('inner', 1), ('outer', 0)]
    res = new_paths(cutout_app, None, obj=grb, **NO_GAPS)
    inner = [g for g in flat(res.solid_geo) if box(20, 13.5, 30, 16.5).contains(g)]
    assert len(inner) == 1
    # w = 0: the path is at margin + r = 1.3 from the edge of the cutout
    assert close(bounds(inner[0]), (21.3, 14.8, 28.7, 15.2))


def test_outline_with_slot(cutout_app):
    res = new_paths(cutout_app, 'outline_slot', **NO_GAPS)
    paths = flat(res.solid_geo)
    assert len(paths) == 2
    slot = min(paths, key=lambda g: g.length)
    outer = max(paths, key=lambda g: g.length)
    # outside edge of the stroke 0.1, margin 0.1, radius 1.2
    assert close(bounds(outer), (-1.4, -1.4, 51.4, 31.4))
    # inside edge of the slot stroke at (20.1, 13.6)-(29.9, 16.4), moved by margin + r into the slot
    assert close(bounds(slot), (21.4, 14.9, 28.6, 15.1))


def _slot_pieces(res, region=box(20, 13.5, 30, 16.5)):
    return [g for g in flat(res.solid_geo) if region.contains(g)]


def test_gaps_on_slot_when_enabled(cutout_app):
    res = new_paths(cutout_app, 'outline_slot', inner_gaps=True, gaps='4', gap_type=1, **SMALL)
    slot = _slot_pieces(res)
    assert len(slot) == 4 and not any(g.is_closed for g in slot)
    pieces = [g for g in flat(res.gaps_solid_geo) if box(20, 13.5, 30, 16.5).contains(g)]
    assert len(pieces) == 4
    cx = unary_union(pieces).bounds
    assert abs((cx[0] + cx[2]) / 2 - 25) < 1e-6 and abs((cx[1] + cx[3]) / 2 - 15) < 1e-6


# ---------------------------------------------------------------------------------------------------------------
# manual cutout, tabs, bites (folded from test_cutout_regressions.py)
# ---------------------------------------------------------------------------------------------------------------

def _configure(tool, source, rectangular=False, **overrides):
    name = source.obj_options['name']
    tool.test_app.collection.objs[name] = source
    configure(tool, name, dict(BASE, **overrides))
    tool.ui.cutout_shape_cb.set_value(rectangular)
    tool.ui.cutz_entry.set_value(-1.6)
    tool.test_app.created.clear()


def _generate(tool, rectangular=False):
    if rectangular:
        tool.on_rectangular_cutout()
    else:
        tool.on_freeform_cutout()


def _geometry(tool):
    return next(obj for obj in tool.test_app.created.values() if obj.kind == 'geometry')


def _drills(tool):
    return [point for obj in tool.test_app.created.values() if obj.kind == 'excellon'
            for data in obj.tools.values() for point in data['drills']]


def _manual_target(tool, geometry, name='manual'):
    app = tool.test_app
    source = make_geometry(app, name, geometry)
    source.tools = {1: deepcopy(tool.cut_tool_dict)}
    source.plot = Mock()
    source.build_ui = Mock()
    tool.man_cutout_obj = source
    tool.manual_solid_geo = deepcopy(geometry)
    app.collection.objs[name] = source
    tool.ui.man_object_combo = SimpleNamespace(currentText=lambda: name)
    tool.ui.dia.set_value(2.4)
    tool.ui.gapsize.set_value(4.0)
    tool.ui.gaptype_combo.set_value(0)
    tool.ui.cutz_entry.set_value(-1.6)
    app.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y), tool_shape=Mock())
    app.plotcanvas.pixel_size = lambda: 0.1
    app.on_mouse_click_over_plot = Mock()
    app.on_mouse_click_release_over_plot = Mock()
    app.on_mouse_move_over_plot = Mock()
    return source


def test_drill_cut_handles_multipart_open_and_closed_paths(cutout_app):
    app = cutout_app.test_app
    for geometry, pitch, expected_x in [
        ([LineString([(0, 0), (10, 0)]), LineString([(20, 0), (30, 0)])], 3.0,
         [0, 3, 6, 9, 10, 20, 23, 26, 29, 30]),
        ([box(0, 0, 4, 4), box(10, 0, 14, 4)], 4.0, [0, 0, 4, 4, 10, 10, 14, 14]),
    ]:
        source = make_geometry(app, 'drill', geometry)
        app.collection.objs['drill'] = source
        cutout_app.ui.drillcut_object_combo = SimpleNamespace(currentText=lambda: 'drill')
        cutout_app.ui.drill_pitch_entry.set_value(pitch)
        cutout_app.ui.drill_dia_entry.set_value(0.5)
        cutout_app.ui.drill_margin_entry.set_value(0.0)
        app.created.clear()
        cutout_app.on_drill_cut_click()
        points = _drills(cutout_app)
        assert sorted(point.x for point in points) == expected_x
        assert len(points) == len({tuple(point.coords)[0] for point in points})


@pytest.mark.parametrize('gap_type', [2])
def test_escape_restores_manual_session_and_next_finish_has_no_stale_bites(cutout_app, gap_type):
    from appHandlers.appHistory import states_equal
    source = _manual_target(cutout_app, [box(0, 0, 50, 30).exterior])
    app = cutout_app.test_app
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    cutout_app.ui.big_cursor_cb.set_value(False)
    cutout_app.ui.gaptype_combo.set_value(gap_type)
    cutout_app.ui.thin_depth_entry.set_value(-0.8)
    cutout_app.ui.mb_dia_entry.set_value(0.6)
    cutout_app.ui.mb_spacing_entry.set_value(0.3)
    original = deepcopy((source.solid_geometry, source.tools, source.multigeo))
    cutout_app.on_manual_gap_click()
    cutout_app.on_manual_cutout((25, 0))
    cutout_app.on_manual_cutout((50, 15))
    assert unary_union(flat(source.solid_geometry)).length < 160
    app.plotcanvas.translate_coords = lambda pos: pos
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 0), button=1))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 30), button=1))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(50, 15), button=1))
    cutout_app.on_key_press(SimpleNamespace(key='Delete'))
    source.obj_options['session_note'] = 'keep this unrelated edit'
    source.tools[2] = {'tooldia': 0.5, 'data': {}, 'solid_geometry': []}
    original[1][2] = deepcopy(source.tools[2])
    cutout_app.on_key_press(SimpleNamespace(key='Escape'))
    assert states_equal((source.solid_geometry, source.tools, source.multigeo), original)
    assert source.obj_options['session_note'] == 'keep this unrelated edit'
    assert not app.created
    # A new session must not drill the cancelled gaps when it finishes.
    cutout_app.on_manual_gap_click()
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    assert not _drills(cutout_app)
    cutout_app.on_manual_gap_click()
    cutout_app.on_manual_cutout((25, 30))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    assert unary_union(flat(source.solid_geometry)).length == pytest.approx(160 - 6.4)
    if gap_type == 2:
        holes = _drills(cutout_app)
        assert holes and all(point.y == pytest.approx(29.1) for point in holes)
    else:
        assert not _drills(cutout_app)


@pytest.mark.parametrize('gap_type', [1])
def test_manual_bridge_edit_rebuilds_overlaps_and_keeps_inner_contours(cutout_app, gap_type):
    outer = box(0, 0, 50, 30).exterior
    inner = box(10, 0.5, 40, 20).exterior
    source = _manual_target(cutout_app, [outer, inner])
    app = cutout_app.test_app
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    cutout_app.ui.big_cursor_cb.set_value(False)
    cutout_app.ui.gaptype_combo.set_value(gap_type)
    cutout_app.ui.thin_depth_entry.set_value(-0.8)
    cutout_app.ui.mb_dia_entry.set_value(0.6)
    cutout_app.ui.mb_spacing_entry.set_value(0.3)
    cutout_app.on_manual_gap_click()

    def click(pos):
        cutout_app.on_mouse_click_release(SimpleNamespace(pos=pos, button=1))

    click((20, 0))
    # The second bridge overlaps the first but isn't inside its selection footprint.
    click((24, 0))
    # A neighboring contour inside that footprint remains available for placement.
    click((24, 0.5))
    assert inner.intersection(unary_union(flat(source.solid_geometry))).length == pytest.approx(inner.length - 6.4)
    click((24, 0.5))
    cutout_app.on_key_press(SimpleNamespace(key='Delete'))
    assert inner.difference(unary_union(flat(source.solid_geometry))).length < 1e-7
    click((20, 0))
    cutout_app.on_key_press(SimpleNamespace(key='Delete'))
    selected, gap, _ = _manual_gap_geometry([outer, inner], (24, 0), 2.4, 4.0)
    expected = _manual_subtract_selected([outer, inner], selected, gap)
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    click((24, 0))
    # Selection doesn't change machining geometry until the destination is accepted.
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    click((25, 30))
    selected, gap, _ = _manual_gap_geometry([outer, inner], (25, 30), 2.4, 4.0)
    expected = _manual_subtract_selected([outer, inner], selected, gap)
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    assert unary_union(flat(source.tools[1]['solid_geometry'])).equals(expected)
    if gap_type == 1:
        assert unary_union(flat(source.tools[99]['solid_geometry'])).equals(gap)
    click((25, 30))
    click((24, 0))
    selected, gap, _ = _manual_gap_geometry([outer, inner], (24, 0), 2.4, 4.0)
    expected = _manual_subtract_selected([outer, inner], selected, gap)
    click((24, 0))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    if gap_type == 2:
        holes = _drills(cutout_app)
        assert holes and all(point.y == pytest.approx(0.9) for point in holes)
    else:
        assert not _drills(cutout_app)
    # Removing every new bridge in a later session preserves completed gaps and Thin tools.
    from appHandlers.appHistory import states_equal
    original = deepcopy((source.solid_geometry, source.tools, source.multigeo))
    cutout_app.on_manual_gap_click()
    click((50, 15))
    click((50, 15))
    cutout_app.on_key_press(SimpleNamespace(key='Delete'))
    assert states_equal((source.solid_geometry, source.tools, source.multigeo), original)
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    assert states_equal((source.solid_geometry, source.tools, source.multigeo), original)


@pytest.mark.parametrize('source_name,kind,margin,convex', [
    ('outline_slot', 'panel', -0.3, False), ('outline_panel2', 'single', 0.1, True),
])
def test_manual_inner_contours_match_automatic_ungapped_outline(cutout_app, source_name, kind, margin, convex):
    source = load_source(cutout_app.test_app, source_name)
    _configure(cutout_app, source, kind=kind, margin=margin, convex=convex, gaps='None')
    cutout_app.ui.is_outline_cb.set_value(True)
    cutout_app.ui.inner_cb.set_value(True)
    _generate(cutout_app)
    expected = unary_union(flat(_geometry(cutout_app).solid_geometry))
    cutout_app.test_app.created.clear()
    cutout_app.ui.cutout_type_radio.set_value('m')
    cutout_app.on_cutout_type('m')
    assert cutout_app.ui.inner_cb.isEnabled()
    assert not cutout_app.ui.inner_gaps_cb.isEnabled()
    cutout_app.on_manual_geo()
    actual = unary_union(flat(_geometry(cutout_app).solid_geometry))
    assert actual.hausdorff_distance(expected) < 1e-7
    assert actual.length == pytest.approx(expected.length)
    cutout_app.ui.cutout_type_radio.set_value('a')
    cutout_app.on_cutout_type('a')
    assert cutout_app.ui.inner_gaps_cb.isEnabled()


@pytest.mark.parametrize('source_kind,kind', [
    ('geometry', 'single'), ('gerber', 'panel'),
])
def test_rectangular_thin_paths_partition_the_full_contour(cutout_app, source_kind, kind):
    source = (load_source(cutout_app.test_app, 'outline_panel2') if source_kind == 'gerber' else
              make_geometry(cutout_app.test_app, 'thin', [box(0, 0, 40, 25), box(50, 0, 90, 25)]))
    _configure(cutout_app, source, True, kind=kind, gaps='None')
    _generate(cutout_app, True)
    full = unary_union(flat(_geometry(cutout_app).solid_geometry))
    _configure(cutout_app, source, True, kind=kind, gap_type=1, thin=-0.8)
    _generate(cutout_app, True)
    output = _geometry(cutout_app)
    cut = unary_union(flat(output.tools[1]['solid_geometry']))
    thin = unary_union(flat(output.tools[99]['solid_geometry']))
    assert thin.length == pytest.approx((full.length - cut.length), abs=1e-7)
    assert thin.intersection(cut).length < 1e-7
    assert unary_union([cut, thin]).hausdorff_distance(full) < 1e-7


RECTANGLE = box(0, 0, 50, 30).exterior
U_SHAPE = Polygon([(0, 0), (50, 0), (50, 30), (35, 30), (35, 10),
                   (15, 10), (15, 30), (0, 30)]).exterior


@pytest.mark.parametrize('mode,path,count', [
    ('LR', LinearRing([(0, 15), (0, 0), (50, 0), (50, 30), (0, 30), (0, 15)]), 2),
    ('4', U_SHAPE, 4),
    ('8', rotate(RECTANGLE, 27), 8),
])
def test_automatic_tab_modes_partition_the_contour(mode, path, count):
    cut, gaps = _cutout_intervals(path, 2.4, mode, 6.4)
    gap_union = unary_union(gaps)
    # Joining the seam pieces counts machining tabs rather than Shapely fragments.
    assert len(flat(linemerge(gap_union))) == count
    assert sum(gap.length for gap in gaps) == pytest.approx(count * 6.4)
    assert sum(part.length for part in cut + gaps) == pytest.approx(path.length)
    assert unary_union(cut).intersection(gap_union).length < 1e-7
    assert unary_union(cut + gaps).hausdorff_distance(path) < 1e-7


@pytest.mark.parametrize('rectangular,kind,closed_path,margin', [
    (False, 'single', True, 2.0), (True, 'panel', False, 2.0),
])
def test_mouse_bites_follow_margin_rows_and_keep_spacing(cutout_app, rectangular, kind, closed_path, margin):
    boards = [box(0, 0, 50, 30)] if kind == 'single' else [box(0, 0, 40, 25), box(50, 0, 90, 25)]
    source = make_geometry(cutout_app.test_app, 'margin',
                           [board.exterior if closed_path else board for board in boards])
    _configure(cutout_app, source, rectangular, kind=kind, margin=margin, gap_type=2)
    _generate(cutout_app, rectangular)
    holes = _drills(cutout_app)
    rows = [board.buffer(margin).exterior for board in boards]
    assert holes
    assert max(min(row.distance(point) for row in rows) for point in holes) < 2e-6
    assert len(holes) == 24 * len(boards)
    for row in rows:
        xmin, ymin, xmax, ymax = row.bounds
        centers = [Point(xmin, (ymin + ymax) / 2), Point(xmax, (ymin + ymax) / 2),
                   Point((xmin + xmax) / 2, ymin), Point((xmin + xmax) / 2, ymax)]
        groups = [[] for _ in centers]
        for point in holes:
            if row.distance(point) < 2e-6:
                index = min(range(4), key=lambda index: centers[index].distance(point))
                groups[index].append(point)
        for index, group in enumerate(groups):
            values = sorted(point.y if index < 2 else point.x for point in group)
            assert len(values) == 6
            assert all(b - a == pytest.approx(0.9) for a, b in zip(values, values[1:]))

