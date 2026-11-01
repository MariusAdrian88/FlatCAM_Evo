"""Native Gerber FOLLOW operations and selected-only circular flash resizing."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import LineString, Point
from shapely.ops import unary_union

from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins import GrbFollowPlugin as module
from appEditors.grb_plugins import GrbTrackEditPlugin as track_edit
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins.GrbTrackBend import sweep_parameters
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from test_gerber_editor_pick import make_editor
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402


def track(coords, width=2):
    follow = LineString(coords)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(width / 2, 16)})


def flash(x, diameter=2):
    follow = Point(x, 4)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 16)})


def editor_for(shapes):
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': shapes}})
    editor.loaded_as_regions = False
    editor.selected = list(shapes)
    return editor


def footprint(editor):
    return unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                        for shape in aperture['geometry']])


def test_split_keeps_width_ownership_order_selection_and_one_history_step():
    first, path, last = flash(-5), track([(0, 0), (10, 0), (10, 10)]), flash(20)
    editor = editor_for([first, path, last])
    editor.selected = [path]
    before, revision = footprint(editor), editor.geometry_revision
    assert editor.apply_path_split((5, 0.001), 0.01) != 'fail'
    pieces = editor.selected
    assert len(pieces) == 2
    assert list(pieces[0].geo['follow'].coords) == [(0, 0), (5, 0)]
    assert list(pieces[1].geo['follow'].coords) == [(5, 0), (10, 0), (10, 10)]
    assert editor.storage_dict[10]['geometry'] == [first, *pieces, last]
    assert editor.storage_dict[10]['size'] == 2
    assert footprint(editor).symmetric_difference(before).area < 1e-10
    assert not editor.loaded_as_regions
    assert editor.geometry_revision == revision + 1
    assert len(editor.history.undo_stack) == 1
    editor.plot_all.assert_called_once()
    assert editor.undo()
    assert editor.selected == [path]
    assert editor.storage_dict[10]['geometry'] == [first, path, last]
    assert editor.redo()
    assert editor.selected == pieces


def test_join_reversed_records_preserves_material_and_unrelated_order():
    a, b = track([(0, 0), (5, 0)]), track([(10, 0), (5, 0)])
    unrelated = flash(30)
    editor = editor_for([a, unrelated, b])
    editor.selected = [b, a]
    before = footprint(editor)
    assert editor.apply_path_join(0) != 'fail'
    result = editor.selected[0]
    assert result.geo['follow'].equals(LineString([(0, 0), (5, 0), (10, 0)]))
    assert result.geo['solid'].equals(unary_union([a.geo['solid'], b.geo['solid']]))
    assert editor.storage_dict[10]['geometry'] == [result, unrelated]
    assert footprint(editor).equals(before)
    assert not editor.loaded_as_regions
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [a, unrelated, b]
    assert editor.selected == [b, a]
    assert editor.redo()


@pytest.mark.parametrize('kind', ['branch', 'gap', 'overlap'])
def test_join_rejects_incompatible_records_without_mutation(kind):
    a, b = track([(0, 0), (5, 0)]), track([(5, 0), (10, 0)])
    editor = editor_for([a, b])
    if kind == 'branch':
        editor.storage_dict[10]['geometry'].append(track([(5, 0), (5, 5)]))
    elif kind == 'interior_branch':
        editor.storage_dict[10]['geometry'].append(track([(2, 0), (2, 5)]))
    elif kind == 'mixed':
        editor.storage_dict[10]['geometry'].remove(b)
        editor.storage_dict[11] = {'type': 'C', 'size': 2, 'geometry': [b]}
    elif kind == 'clear':
        b.geo['clear'] = Point(5, 0).buffer(0.1)
    elif kind == 'modified':
        b.geo['solid'] = b.geo['solid'].buffer(0.1)
    elif kind == 'gap':
        b.geo.update(track([(5.001, 0), (10, 0)]).geo)
    elif kind == 'overlap':
        b.geo.update(track([(5, 0), (2, 0)]).geo)
    else:
        editor.storage_dict[11] = {'type': 'C', 'size': 2, 'geometry': [a]}
    original = {key: list(value['geometry']) for key, value in editor.storage_dict.items()}
    revision = editor.geometry_revision
    assert editor.apply_path_join(0.01) == 'fail'
    assert {key: value['geometry'] for key, value in editor.storage_dict.items()} == original
    assert editor.selected == [a, b]
    assert editor.geometry_revision == revision
    assert not editor.history.undo_stack
    editor.plot_all.assert_not_called()


@pytest.mark.parametrize('point,tolerance', [((0, 0), 0), ((20, 0), 0.01)])
def test_invalid_split_is_atomic(point, tolerance):
    path = track([(0, 0), (10, 0)])
    editor = editor_for([path])
    revision = editor.geometry_revision
    assert editor.apply_path_split(point, tolerance) == 'fail'
    assert editor.selected == [path]
    assert editor.storage_dict[10]['geometry'] == [path]
    assert editor.geometry_revision == revision
    assert not editor.history.undo_stack


def test_selected_flash_resize_reuses_matching_metadata_without_touching_shared_aperture():
    a, b, line = flash(0), flash(5), track([(0, 0), (10, 0)])
    editor = editor_for([a, b, line])
    editor.storage_dict[10]['tag'] = {'net': 'GND'}
    editor.storage_dict['11'] = {'type': 'C', 'size': 4, 'tag': {'net': 'GND'}, 'geometry': []}
    editor.selected = [a, line]
    editor.ui.resize_circles_entry.set_value(4)
    editor.ui.resize_circles_btn.click()
    resized = editor.selected[0]
    assert editor.selected[1] is line
    assert editor.storage_dict[10]['geometry'] == [b, line]
    assert editor.storage_dict['11']['geometry'] == [resized]
    assert resized.geo['follow'].equals(a.geo['follow'])
    assert resized.geo['solid'].equals(Point(0, 4).buffer(2, 16))
    assert editor.storage_dict[10]['size'] == 2
    assert len(editor.history.undo_stack) == 1
    assert not editor.loaded_as_regions
    assert editor.undo()
    assert editor.selected == [a, line]
    assert editor.storage_dict[10]['geometry'] == [a, b, line]
    assert editor.storage_dict['11']['geometry'] == []
    assert editor.redo()


def test_resize_creates_aperture_and_undo_removes_it():
    a, b = flash(0), flash(5)
    editor = editor_for([a, b])
    editor.selected = [a]
    editor.storage_dict[10]['tag'] = 'keep'
    assert editor.apply_resize_circles(3) != 'fail'
    assert list(editor.storage_dict) == [10, 11]
    assert editor.storage_dict[11]['tag'] == 'keep'
    assert editor.storage_dict[10]['geometry'] == [b]
    assert editor.undo()
    assert list(editor.storage_dict) == [10]
    assert editor.redo()
    assert editor.storage_dict[11]['size'] == 3


@pytest.mark.parametrize('diameter', [0, float('nan')])
def test_invalid_diameter_does_not_mutate(diameter):
    a = flash(0)
    editor = editor_for([a])
    assert editor.apply_resize_circles(diameter) == 'fail'
    assert editor.storage_dict[10]['geometry'] == [a]
    assert not editor.history.undo_stack


@pytest.mark.parametrize('kind', ['hole', 'macro'])
def test_unsupported_flash_does_not_mutate(kind):
    a = flash(0)
    editor = editor_for([a])
    if kind == 'hole':
        a.geo['solid'] = a.geo['solid'].difference(Point(0, 4).buffer(0.1))
    elif kind == 'macro':
        editor.storage_dict[10]['type'] = 'AM'
    elif kind == 'clear':
        a.geo['clear'] = Point(0, 4).buffer(0.1)
    else:
        a.geo['solid'] = a.geo['solid'].buffer(0.1)
    assert editor.apply_resize_circles(3) == 'fail'
    assert editor.storage_dict[10]['geometry'] == [a]
    assert not editor.history.undo_stack


@pytest.mark.parametrize('operation', ['split', 'join', 'resize'])
def test_native_save_reparse_after_operation_undo_redo(app, operation):

    source = make_gerber(app, NATIVE_GBR, name='board')
    if operation == 'split':
        editor = editor_for([track([(0, 0), (10, 0)])])
        assert editor.apply_path_split((5, 0), 0) != 'fail'
    elif operation == 'join':
        editor = editor_for([track([(0, 0), (5, 0)]), track([(10, 0), (5, 0)])])
        assert editor.apply_path_join(0) != 'fail'
    else:
        editor = editor_for([flash(0), flash(5)])
        editor.selected = [editor.selected[0]]
        assert editor.apply_resize_circles(4) != 'fail'
    for mode in ('edited', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        assert not editor.loaded_as_regions
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = False
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        assert 'G36' not in result['obj'].source_file
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
        assert unary_union(parsed.solid_geometry).hausdorff_distance(footprint(editor)) < 0.002


def test_joined_record_can_be_split_and_bend_validated_again():

    editor = editor_for([track([(0, 0), (5, 0)]), track([(10, 0), (5, 0)])])
    assert editor.apply_path_join(0) != 'fail'
    assert sweep_parameters(editor, editor.storage_dict[10], editor.selected[0]) == (1, 16)
    assert editor.apply_path_split((7, 0), 0) != 'fail'


def test_clip_splits_track_and_selects_inside_records():
    path, dot = track([(0, 0), (10, 0)]), flash_at(5, 0)
    editor = editor_for([path, dot])
    editor.selected = []
    assert track_edit.apply_clip(editor, (4, -1, 6, 1)) != 'fail'
    assert coords_of(editor) == [[(0, 0), (4, 0)], [(4, 0), (6, 0)], [(6, 0), (10, 0)]]
    assert len(editor.selected) == 2
    assert any(s.geo['follow'].equals(LineString([(4, 0), (6, 0)])) for s in editor.selected)
    assert any(s.geo['follow'].equals(Point(5, 0)) for s in editor.selected)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert coords_of(editor) == [[(0, 0), (10, 0)]]
    assert editor.redo()


def test_clip_splits_region_into_inside_and_outside_pieces():
    from shapely import Polygon
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    editor = make_editor({'REG': {'type': 'REG', 'size': 0, 'geometry': [region(square)]}})
    editor.loaded_as_regions = False
    editor.selected = []
    assert track_edit.apply_clip(editor, (5, -1, 11, 11)) != 'fail'
    pieces = editor.storage_dict['REG']['geometry']
    assert len(pieces) == 2
    assert unary_union([p.geo['solid'] for p in pieces]).equals(square)
    assert [round(p.geo['solid'].area, 6) for p in pieces] == [50.0, 50.0]
    assert len(editor.selected) == 1
    assert editor.selected[0].geo['solid'].bounds == (5, 0, 10, 10)


def test_clip_and_replace_removes_inside_part_and_adds_2x2_pad():
    editor = editor_for([track([(0, 0), (10, 0)])])
    editor.selected = []
    assert track_edit.apply_clip_replace_pad(editor, (4, -1, 6, 1)) != 'fail'
    assert coords_of(editor) == [[(0, 0), (4, 0)], [(6, 0), (10, 0)]]
    pads = [a for a in editor.storage_dict.values() if a['type'] == 'R']
    assert len(pads) == 1
    assert pads[0]['width'] == 2 and pads[0]['height'] == 2
    assert len(pads[0]['geometry']) == 1
    assert pads[0]['geometry'][0].geo['solid'].bounds == (4, -1, 6, 1)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert coords_of(editor) == [[(0, 0), (10, 0)]]
    assert list(editor.storage_dict) == [10]


def test_stretch_moves_shared_endpoint_and_flash_in_one_undo_step():
    editor = editor_for([track([(0, 0), (5, 0)]), track([(5, 0), (5, 5)]), flash_at(5, 0)])
    editor.selected = []
    assert track_edit.apply_stretch(editor, (4.5, -0.5, 5.5, 0.5), 1, 1) != 'fail'
    assert coords_of(editor) == [[(0, 0), (6, 1)], [(6, 1), (5, 5)]]
    points = [s for s in editor.storage_dict[10]['geometry'] if s.geo['follow'].geom_type == 'Point']
    assert len(points) == 1 and points[0].geo['follow'].equals(Point(6, 1))
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert coords_of(editor) == [[(0, 0), (5, 0)], [(5, 0), (5, 5)]]
    assert editor.redo()


def test_insert_elbow_adds_vertex_undo_restores_and_a_miss_changes_nothing():
    path = track([(0, 0), (10, 0)])
    editor = editor_for([path])
    revision = editor.geometry_revision
    # no track near the point: nothing changes
    assert track_edit.apply_insert_elbow(editor, (5, 5), (5, 8), 0.5) == 'fail'
    assert coords_of(editor) == [[(0, 0), (10, 0)]]
    assert editor.selected == [path]
    assert editor.geometry_revision == revision
    assert not editor.history.undo_stack
    assert track_edit.apply_insert_elbow(editor, (5, 0), (5, 3), 0.5) != 'fail'
    assert coords_of(editor) == [[(0, 0), (5, 3), (10, 0)]]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert coords_of(editor) == [[(0, 0), (10, 0)]]


def test_orthogonal_elbow_drops_zero_length_parts():
    for vertex, expected in (((5, 3), [(0, 0), (5, 0), (5, 3), (10, 3), (10, 0)]),
                             ((0, 3), [(0, 0), (0, 3), (10, 3), (10, 0)])):
        editor = editor_for([track([(0, 0), (10, 0)])])
        assert track_edit.apply_insert_elbow(editor, (5, 0), vertex, 0.5, orthogonal=True) != 'fail'
        assert coords_of(editor) == [expected], vertex


def flash_at(x, y, diameter=2):
    follow = Point(x, y)
    return DrawToolShape({'follow': follow, 'solid': follow.buffer(diameter / 2, 16)})


def region(polygon):
    return DrawToolShape({'follow': LineString(polygon.exterior.coords), 'solid': polygon})


def coords_of(editor, code=10):
    return sorted(list(s.geo['follow'].coords) for s in editor.storage_dict[code]['geometry']
                  if s.geo['follow'].geom_type == 'LineString')




def test_clip_replace_pad_saves_rectangular_flashes_as_flashes(app):
    from appEditors.grb_plugins.GrbSelectionPlugin import flash_geo
    probe = make_editor({10: {'type': 'C', 'size': 2, 'geometry': []}})
    pad_aperture = {'type': 'R', 'size': 2.5, 'width': 2, 'height': 1.5, 'geometry': []}
    pad = DrawToolShape(flash_geo(probe, pad_aperture, Point(0, 0)))
    pad_aperture['geometry'].append(pad)
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': [track([(20, 0), (30, 0)])]}, 11: pad_aperture})
    editor.loaded_as_regions = False
    editor.selected = []
    assert track_edit.apply_clip_replace_pad(editor, (19, -3, 25, 3)) != 'fail'
    assert not editor.loaded_as_regions
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for tool in parsed.tools.values()
               if tool.get('type') in ('R', 'O') for record in tool['geometry'])
