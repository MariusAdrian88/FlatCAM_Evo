"""Cutout regressions not covered by the existing outline/golden suite."""

from copy import deepcopy
import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.affinity import rotate
from shapely.geometry import GeometryCollection, LineString, LinearRing, Point, Polygon, box
from shapely.ops import linemerge, unary_union

from test_cutout_inner_contours import (  # noqa: F401
    BASE, _headless_qt, configure, flat, load_source, make_geometry, make_tool,
)
from appPlugins.ToolCutOut import (
    CutOut, _cutout_intervals, _manual_gap_geometry, _manual_subtract_selected, freeform_cutout_paths,
)


@pytest.fixture
def cutout_app():
    return make_tool()


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


@pytest.mark.parametrize('rectangular', [False, True])
def test_signed_automatic_cutters_preserve_geometry(cutout_app, rectangular):
    source = load_source(cutout_app.test_app, 'outline_single')
    paths = []
    for diameter in (2.4, -2.4):
        _configure(cutout_app, source, rectangular, dia=diameter)
        _generate(cutout_app, rectangular)
        paths.append(unary_union(flat(_geometry(cutout_app).solid_geometry)))
    assert paths[0].equals(paths[1])


def test_extensionless_mouse_bites_keep_source_name(cutout_app):
    source = make_geometry(cutout_app.test_app, 'board', box(0, 0, 50, 30))
    _configure(cutout_app, source, gap_type=2)
    _generate(cutout_app)
    assert 'board_mouse_bites' in cutout_app.test_app.created
    assert _drills(cutout_app)


@pytest.mark.parametrize('rectangular', [False, True])
def test_failed_cutout_releases_its_promises(cutout_app, rectangular):
    source = make_geometry(cutout_app.test_app, 'board', box(0, 0, 10, 10))
    _configure(cutout_app, source, rectangular, gap_type=2, gapsize=20.0)
    _generate(cutout_app, rectangular)
    assert not cutout_app.test_app.created
    assert not cutout_app.test_app.collection.has_promises()



def test_manual_gap_without_cutter_preserves_geometry(cutout_app):
    source = _manual_target(cutout_app, [LineString([(0, 0), (20, 0)])])
    source.tools = {}
    original = unary_union(source.solid_geometry)
    multigeo = source.multigeo
    cutout_app.on_manual_cutout((10, 0))
    assert unary_union(source.solid_geometry).equals(original)
    assert source.multigeo == multigeo
    assert not source.tools


@pytest.mark.parametrize('gap_type', [0, 1, 2])
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


@pytest.mark.parametrize('gap_type', [0, 1, 2])
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


def test_manual_mouse_bites_stay_on_the_material_side_of_inner_contours(cutout_app):
    outer = box(0, 0, 50, 30).exterior
    inner = box(10, 5, 40, 25).exterior
    _manual_target(cutout_app, [outer, inner])
    app = cutout_app.test_app
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    cutout_app.ui.big_cursor_cb.set_value(False)
    cutout_app.ui.gaptype_combo.set_value(2)
    cutout_app.ui.mb_dia_entry.set_value(0.6)
    cutout_app.ui.mb_spacing_entry.set_value(0.3)
    cutout_app.on_manual_gap_click()
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 5), button=1))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    holes = _drills(cutout_app)
    assert holes
    assert all(point.y == pytest.approx(4.1) for point in holes)


@pytest.mark.parametrize('gap_type', [0, 1, 2])
def test_manual_grip_drag_previews_then_commits_and_escape_restores(cutout_app, gap_type):
    path = box(0, 0, 50, 30).exterior
    source = _manual_target(cutout_app, [path])
    app = cutout_app.test_app
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    app.grid_status = lambda: False
    # VisPy can return homogeneous coordinates, not just x/y.
    app.plotcanvas.translate_coords = lambda pos: (*pos, 0, 1)
    app.plotcanvas.pixel_size = lambda: 0.1
    cutout_app.ui.big_cursor_cb.set_value(False)
    cutout_app.ui.gaptype_combo.set_value(gap_type)
    cutout_app.ui.thin_depth_entry.set_value(-0.8)
    cutout_app.ui.mb_dia_entry.set_value(0.6)
    cutout_app.ui.mb_spacing_entry.set_value(0.3)
    cutout_app.on_manual_gap_click()
    cutout_app.on_manual_cutout((25, 0))
    before = unary_union(flat(source.solid_geometry))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 0), button=1))
    shapes = app.geo_editor.tool_shape
    grip = shapes.add.call_args.kwargs
    assert grip['face_color'] == grip['color']
    assert grip['shape'].centroid.distance(Point(25, 0)) < 1e-7
    assert grip['shape'].bounds == pytest.approx((24.6, -0.4, 25.4, 0.4))
    cutout_app.on_mouse_click_press(SimpleNamespace(pos=(25.5, 0), button=1))
    cutout_app.on_mouse_move(SimpleNamespace(pos=(25, 30), button=1, is_dragging=True))
    assert unary_union(flat(source.solid_geometry)).equals(before)
    assert shapes.add.call_args.kwargs['shape'].centroid.distance(Point(25, 30)) < 1e-7
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 30), button=1, is_dragging=True))
    selected, gap, _ = _manual_gap_geometry([path], (25, 30), 2.4, 4.0)
    expected = _manual_subtract_selected([path], selected, gap)
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    if gap_type == 1:
        assert unary_union(flat(source.tools[99]['solid_geometry'])).equals(gap)
    # A pan release must neither place another bridge nor accept the session.
    cutout_app.on_mouse_move(SimpleNamespace(pos=(50, 15), button=2, is_dragging=True))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(50, 15), button=2, is_dragging=True))
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 30), button=1))
    cutout_app.on_mouse_click_press(SimpleNamespace(pos=(25, 30), button=1))
    cutout_app.on_mouse_move(SimpleNamespace(pos=(50, 15), button=1, is_dragging=True))
    cutout_app.on_key_press(SimpleNamespace(key='Escape'))
    assert unary_union(flat(source.solid_geometry)).equals(path)
    assert shapes.clear.call_args.kwargs['update'] is True
    assert not shapes.enabled
    assert not _drills(cutout_app)
    cutout_app.on_manual_gap_click()
    cutout_app.on_manual_cutout((25, 0))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 0), button=1))
    cutout_app.on_mouse_click_press(SimpleNamespace(pos=(25, 0), button=1))
    cutout_app.on_key_press(SimpleNamespace(key='Delete'))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 30), button=1))
    assert unary_union(flat(source.solid_geometry)).equals(path)
    cutout_app.on_manual_cutout((25, 0))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 0), button=1))
    cutout_app.on_mouse_click_press(SimpleNamespace(pos=(25, 0), button=1))
    cutout_app.on_mouse_move(SimpleNamespace(pos=(25, 30), button=1, is_dragging=True))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(25, 30), button=1))
    cutout_app.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    assert unary_union(flat(source.solid_geometry)).equals(expected)
    if gap_type == 2:
        holes = _drills(cutout_app)
        assert holes and all(point.y == pytest.approx(29.1) for point in holes)


@pytest.mark.parametrize('source_name,kind,margin,convex', [
    ('outline_slot', 'single', 0.1, False), ('outline_slot', 'panel', -0.3, False),
    ('outline_slot', 'single', 0.1, True), ('outline_panel2', 'single', 0.1, False),
    ('outline_panel2', 'panel', 0.1, False),
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


@pytest.mark.parametrize('gap_type,rectangular', [(1, False), (2, True)])
def test_beginner_generates_basic_bridges_from_visible_defaults(cutout_app, gap_type, rectangular):
    source = load_source(cutout_app.test_app, 'outline_single')
    _configure(cutout_app, source, rectangular, gap_type=gap_type, convex=True)
    cutout_app.on_level_changed(False)
    assert cutout_app.ui.gaptype_combo.get_value() == 0
    assert not cutout_app.ui.convex_box_cb.get_value()
    assert cutout_app.ui.thin_depth_entry.isHidden()
    assert cutout_app.ui.mb_dia_entry.isHidden()
    _generate(cutout_app, rectangular)
    assert set(_geometry(cutout_app).tools) == {1}
    assert not _drills(cutout_app)
    # the Advanced level brings back the user's values, not the preferences
    cutout_app.test_app.options['tools_cutout_gap_type'] = 0
    cutout_app.test_app.options['tools_cutout_convexshape'] = False
    cutout_app.on_level_changed(True)
    assert cutout_app.ui.gaptype_combo.get_value() == gap_type
    assert cutout_app.ui.convex_box_cb.get_value()


def test_added_tool_keeps_the_automatic_widgets_and_shows_its_gap_type(cutout_app):
    ui = cutout_app.ui
    ui.cutout_type_radio.set_value('a')
    cutout_app.on_cutout_type('a')
    # 'Search and Add' of a diameter that is not in the Tools Database, then 'Pick from DB' with Mouse Bites
    cutout_app.on_tool_default_add(dia=2.4, muted=True)
    data = dict(cutout_app.default_data, tool_target=6, tools_cutout_gap_type=2)
    cutout_app.on_cutout_tool_add_from_db_executed({'tooldia': 3.0, 'data': data})
    shown = {name: not getattr(ui, name).isHidden() for name in (
        'generate_cutout_btn', 'gaps', 'man_geo_creation_btn', 'man_gaps_creation_btn', 'mb_dia_entry',
        'thin_depth_entry')}
    assert ui.cutout_type_radio.get_value() == 'a' and ui.gaptype_combo.get_value() == 2
    assert shown == {'generate_cutout_btn': True, 'gaps': True, 'man_geo_creation_btn': False,
                     'man_gaps_creation_btn': False, 'mb_dia_entry': True, 'thin_depth_entry': False}


@pytest.mark.parametrize('source_kind,kind', [
    ('geometry', 'single'), ('geometry', 'panel'), ('gerber', 'single'), ('gerber', 'panel'),
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


@pytest.mark.parametrize('rectangular', [False, True])
def test_manual_panels_keep_all_paths_and_independent_tools(cutout_app, rectangular):
    template = deepcopy(cutout_app.cut_tool_dict)
    outputs = []
    for name in ('first.gbr', 'second.gbr'):
        source = SimpleNamespace(kind='gerber', obj_options={'name': name},
                                 solid_geometry=[box(0, 0, 40, 25), box(50, 0, 90, 25)])
        _configure(cutout_app, source, rectangular, kind='panel')
        cutout_app.on_manual_geo()
        output = _geometry(cutout_app)
        paths = flat(output.solid_geometry)
        assert len(paths) == 2
        assert all(any(board.contains(path) for board in
                       [box(-2, -2, 42, 27), box(48, -2, 92, 27)]) for path in paths)
        outputs.append(output)
    assert outputs[0].tools[1]['data']['name'] == 'first.gbr_cutout'
    assert outputs[0].tools[1] is not outputs[1].tools[1]
    assert cutout_app.cut_tool_dict == template


RECTANGLE = box(0, 0, 50, 30).exterior
U_SHAPE = Polygon([(0, 0), (50, 0), (50, 30), (35, 30), (35, 10),
                   (15, 10), (15, 30), (0, 30)]).exterior


@pytest.mark.parametrize('mode,path,count', [
    ('LR', LinearRing([(0, 15), (0, 0), (50, 0), (50, 30), (0, 30), (0, 15)]), 2),
    ('TB', rotate(RECTANGLE, 27), 2),
    ('2LR', RECTANGLE, 4),
    ('2TB', LinearRing(list(RECTANGLE.coords)[::-1]), 4),
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


def test_manual_preview_click_and_swept_web_agree(cutout_app):
    path = rotate(LineString([(0, 0), (50, 0)]), 27)
    neighbor = rotate(LineString([(0, 10), (50, 10)]), 27, origin=(25, 0))
    source = _manual_target(cutout_app, [path, neighbor])
    app = cutout_app.test_app
    app.grid_status = lambda: False
    cutout_app.canvas.translate_coords = lambda point: point
    cutout_app.draw_utility_geometry = Mock()
    cutout_app.ui.dia.set_value(-2.4)
    cutout_app.ui.gapsize.set_value(1000.0)
    assert cutout_app.cutting_geo((25, 0)).is_empty
    cutout_app.ui.gapsize.set_value(4.0)
    cutout_app.on_mouse_move(SimpleNamespace(pos=(25, 0), is_dragging=False))
    preview = cutout_app.draw_utility_geometry.call_args.kwargs['geo']
    assert preview.area == pytest.approx(6.4 * 2.4)
    cutout_app.on_manual_cutout((25, 0))
    remaining = unary_union(flat(source.solid_geometry))
    assert path.length + neighbor.length - remaining.length == pytest.approx(6.4)
    assert neighbor.difference(remaining.buffer(1e-7)).length < 1e-7
    residual = path.difference(remaining.buffer(1.2))
    assert residual.length == pytest.approx(4.0)


def test_valid_thin_gap_owns_its_tool_and_zero_depth_keeps_a_bridge(cutout_app):
    source = _manual_target(cutout_app, [LineString([(0, 0), (20, 0)])])
    template = deepcopy(cutout_app.cut_tool_dict)
    cutout_app.ui.gaptype_combo.set_value(1)
    cutout_app.ui.thin_depth_entry.set_value(-0.8)
    cutout_app.on_manual_cutout((10, 0))
    assert source.tools[99]['data']['tools_mill_cutz'] == -0.8
    assert source.tools[99] is not source.tools[1]
    assert cutout_app.cut_tool_dict == template
    source = make_geometry(cutout_app.test_app, 'zero_thin', box(0, 0, 50, 30))
    _configure(cutout_app, source, gap_type=1, thin=0.0)
    _generate(cutout_app)
    assert 99 not in _geometry(cutout_app).tools


@pytest.mark.parametrize('rectangular,kind,closed_path,margin', [
    (False, 'single', True, 2.0), (False, 'panel', False, -0.3),
    (True, 'single', True, -0.3),
    (True, 'panel', False, 2.0),
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

