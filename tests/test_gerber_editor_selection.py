"""Gerber Editor selection and edit operations: invert, filter, chain, step and repeat, transcode and snap to grid.
Behavior only: geometry, selection, one undo step, undo/redo restore and atomic failure."""
import pytest
from shapely import LineString, Point
from shapely.geometry import Polygon
from shapely.ops import unary_union

from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import make_editor
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402
from appEditors.grb_plugins import GrbSelectionPlugin as module


def track(coords, width=2):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / 2, 16)})


def flash_at(x, y, diameter=2):
    follow = Point(x, y)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 16)})


def region(coords):
    poly = Polygon(coords)
    return DrawToolShape({'follow': LineString(poly.exterior.coords), 'solid': poly})


def editor_for(storage, selected=()):
    editor = make_editor(storage)
    editor.loaded_as_regions = False
    editor.selected = list(selected)
    return editor


def footprint(editor):
    return unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                        for shape in aperture['geometry']])


def centers(editor):
    return sorted((round(shape.geo['follow'].x, 6), round(shape.geo['follow'].y, 6))
                  for aperture in editor.storage_dict.values() for shape in aperture['geometry'])


def ids(shapes):
    return sorted(id(shape) for shape in shapes)


def has(editor, shape):
    return any(picked is shape for picked in editor.selected)


def storage_ids(editor):
    return {key: ids(aperture['geometry']) for key, aperture in editor.storage_dict.items()}


def test_invert_and_filter_change_the_selection_without_an_undo_step():
    a, b, c = flash_at(0, 0), flash_at(5, 0), flash_at(10, 0)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a, b, c]}}, selected=[a])
    module.invert_selection(editor)
    assert ids(editor.selected) == ids([b, c])
    pad, line, reg = flash_at(0, 0), track([(0, 5), (10, 5)]), region([(0, 0), (4, 0), (4, 4)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [pad, line]},
                         'REG': {'type': 'REG', 'size': 0, 'geometry': [reg]}},
                        selected=[pad, line, reg])
    module.filter_selection(editor, 'track')
    assert ids(editor.selected) == ids([line])
    editor.selected = []
    module.filter_selection(editor, 'pad')
    assert ids(editor.selected) == ids([pad])
    assert not editor.history.undo_stack


def test_chain_follows_connected_tracks_and_skips_the_far_one():
    t1, t2, t3 = track([(0, 0), (5, 0)]), track([(5, 0), (5, 5)]), track([(5, 5), (9, 5)])
    far = track([(20, 20), (25, 20)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [t1, t2, t3, far]}})
    module.select_chain(editor, (2, 0.1), 0.5)
    assert ids(editor.selected) == ids([t1, t2, t3])
    editor.selected = [far]
    module.select_chain(editor, (2, 0.1), 0.5, add=True)
    assert ids(editor.selected) == ids([far, t1, t2, t3])


@pytest.mark.parametrize('branching, far_selected', [(False, False), (True, True)])
def test_chain_stops_at_a_junction_unless_branching(branching, far_selected):
    a = track([(0, 0), (5, 0)])
    b = track([(5, 0), (5, 5)])
    c = track([(5, 0), (9, -3)])
    d = track([(5, 5), (9, 5)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a, b, c, d]}})
    module.select_chain(editor, (2, 0.1), 0.5, branching=branching)
    assert has(editor, a)
    assert has(editor, d) is far_selected


def test_step_repeat_makes_the_grid_and_undo_restores_the_source():
    a = flash_at(0, 0)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a]}}, selected=[a])
    assert module.apply_step_repeat(editor, 2, 3, 10, 5) != 'fail'
    assert centers(editor) == sorted((x, y) for x in (0, 10, 20) for y in (0, 5))
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert ids(editor.storage_dict[10]['geometry']) == ids([a])
    assert editor.redo()
    assert len(centers(editor)) == 6
    assert module.apply_step_repeat(editor, 1, 1, 10, 5) == 'fail'
    assert len(centers(editor)) == 6
    assert len(editor.history.undo_stack) == 1


def test_transcode_flash_takes_the_target_shape_at_the_same_center():
    a = flash_at(3, 4)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a]},
                         11: {'type': 'R', 'size': 0, 'width': 1, 'height': 3, 'geometry': []}},
                        selected=[a])
    assert module.apply_transcode(editor, 11) != 'fail'
    assert editor.storage_dict[10]['geometry'] == []
    (moved,) = editor.storage_dict[11]['geometry']
    assert moved.geo['solid'].bounds == pytest.approx((2.5, 2.5, 3.5, 5.5))
    assert len(editor.history.undo_stack) == 1


def test_transcode_track_takes_the_width_of_a_circular_target():
    t = track([(0, 0), (10, 0)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [t]},
                         11: {'type': 'C', 'size': 4, 'geometry': []}},
                        selected=[t])
    assert module.apply_transcode(editor, 11) != 'fail'
    (moved,) = editor.storage_dict[11]['geometry']
    assert moved.geo['solid'].bounds[1] == pytest.approx(-2)
    assert moved.geo['solid'].bounds[3] == pytest.approx(2)


def test_transcode_refuses_regions_and_tracks_to_non_circular_apertures():
    for source in ('region', 'track'):
        rect = {11: {'type': 'R', 'size': 0, 'width': 1, 'height': 3, 'geometry': []}}
        if source == 'region':
            shape = region([(0, 0), (4, 0), (4, 4)])
            storage = {0: {'type': 'REG', 'size': 0, 'geometry': [shape]}, **rect}
        else:
            shape = track([(0, 0), (10, 0)])
            storage = {10: {'type': 'C', 'size': 2, 'geometry': [shape]}, **rect}
        editor = editor_for(storage, selected=[shape])
        before = storage_ids(editor)
        assert module.apply_transcode(editor, 11) == 'fail', source
        assert storage_ids(editor) == before
        assert not editor.history.undo_stack


def test_snap_to_grid_moves_flash_centers_and_track_vertices():
    f = flash_at(1.04, 2.96)
    t = track([(0.02, 0.01), (5.04, 0.02)])
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [f, t]}}, selected=[f, t])
    assert module.apply_snap_to_grid(editor, 0.1, 0.1) != 'fail'
    geometry = editor.storage_dict[10]['geometry']
    (flash,) = [s for s in geometry if s.geo['follow'].geom_type == 'Point']
    (line,) = [s for s in geometry if s.geo['follow'].geom_type == 'LineString']
    assert (flash.geo['follow'].x, flash.geo['follow'].y) == pytest.approx((1.0, 3.0))
    assert (flash.geo['solid'].centroid.x, flash.geo['solid'].centroid.y) == pytest.approx((1.0, 3.0))
    assert [v for xy in line.geo['follow'].coords for v in xy] == pytest.approx([0.0, 0.0, 5.0, 0.0])


def test_native_save_reparse_after_step_repeat_undo_redo(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    a = flash_at(0, 0)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [a]}}, selected=[a])
    assert module.apply_step_repeat(editor, 2, 3, 10, 5) != 'fail'
    for mode in ('edited', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = False
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
        assert unary_union(parsed.solid_geometry).hausdorff_distance(footprint(editor)) < 0.002


def assert_saved_as_flashes(app, editor):
    """The edited Gerber keeps the flashes in R or O apertures: no composed regions, a Point follow after reparse."""
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for aperture in parsed.tools.values()
               if aperture.get('type') in ('R', 'O') for record in aperture['geometry'])


def test_transcoded_flash_saves_as_a_rectangular_flash(app):
    flash = flash_at(5, 5)
    editor = editor_for({10: {'type': 'C', 'size': 2, 'geometry': [flash]},
                         11: {'type': 'R', 'size': 2.5, 'width': 2, 'height': 1.5, 'geometry': []}},
                        selected=[flash])
    assert module.apply_transcode(editor, 11) != 'fail'
    assert not editor.loaded_as_regions
    assert_saved_as_flashes(app, editor)
