"""Selection-tool segment stretching, rubber-band cords and atomic editor commits."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore
from shapely.geometry import LineString, Polygon, MultiLineString, box

from appEditors.segment_ops import segment_geometries
from appEditors.appGeoEditor import AppGeoEditor
from tests.test_geo_editor_vertex_drag import drag_editor
from tests.test_gerber_editor_vertices import track_editor
from tests.test_vertex_grips import FakeCollection

CTRL = QtCore.Qt.KeyboardModifier.ControlModifier
SHIFT_ONLY = QtCore.Qt.KeyboardModifier.ShiftModifier
STRETCH = CTRL | QtCore.Qt.KeyboardModifier.ShiftModifier
CORDS = CTRL | QtCore.Qt.KeyboardModifier.AltModifier
NONE = QtCore.Qt.KeyboardModifier.NoModifier


def test_alt_connected_polyline_gets_cords_only_at_outer_extremities():
    line = LineString([(0, 0), (2, 0), (4, 0), (6, 0), (8, 0)])
    result = segment_geometries({'line': line}, {'line': {(0, 0, 1), (0, 0, 2)}}, (0, 2), cords=True)
    assert list(result['line'].coords) == [(0, 0), (2, 0), (2, 2), (4, 2), (6, 2), (6, 0), (8, 0)]








def test_alt_closed_ring_wraparound_is_one_group_and_preserves_holes():
    polygon = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)], [[(3, 3), (4, 3), (4, 4), (3, 4)]])
    result = segment_geometries({'p': polygon}, {'p': {(0, 0, 3), (0, 0, 0)}}, (-1, -1), cords=True)['p']
    assert list(result.exterior.coords) == [(-1, -1), (7, -1), (8, 0), (8, 8), (0, 8), (-1, 7), (-1, -1)]
    assert list(result.interiors[0].coords) == list(polygon.interiors[0].coords)




def test_invalid_result_is_rejected_and_zero_offset_is_a_noop():
    line = LineString([(0, 0), (2, 0), (2, 2), (0, 2)])
    selected = {'l': {(0, 0, 1)}}
    with pytest.raises(ValueError):
        segment_geometries({'l': line}, selected, (-2, 0))
    assert segment_geometries({'l': line}, selected, (0, 0), cords=True) == {}
    with pytest.raises(ValueError):
        segment_geometries({'l': line}, selected, (float('nan'), 0))


def geo_drag(geometry):
    editor, original = drag_editor(geometry)
    editor.apply_selection_drag = lambda *a, **k: AppGeoEditor.apply_selection_drag(editor, *a, **k)
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.pick_candidates = lambda point: AppGeoEditor.pick_candidates(editor, point)
    return editor, original


@pytest.mark.parametrize('modifier,expected', [
    (STRETCH, [(0, 0), (2, 2), (6, 2), (8, 0)]),
    (CORDS, [(0, 0), (2, 0), (2, 2), (6, 2), (6, 0), (8, 0)]),
])
def test_geo_direct_drag_commits_once_and_undo_redo_restore(modifier, expected):
    editor, original = geo_drag(LineString([(0, 0), (2, 0), (6, 0), (8, 0)]))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (3, 0), modifier)
    assert grips.move((20, 0), (3, 2))
    assert editor.selected == [original]
    assert grips.release((20, 0), (3, 2))
    assert list(editor.selected[0].geo.coords) == expected
    assert len(editor._history_undo) == 1
    assert editor.undo() and editor.selected == [original]
    assert editor.redo() and list(editor.selected[0].geo.coords) == expected




def test_cancel_and_tool_change_do_not_commit_segment_drags():
    editor, original = geo_drag(LineString([(0, 0), (4, 0)]))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (1, 0), CORDS)
    grips.move((20, 0), (1, 2))
    grips.cancel()
    assert not grips.release((20, 0), (1, 2))
    assert editor.selected == [original] and not editor._history_undo
    editor.active_tool.name = 'move'
    assert not grips.press((0, 0), (1, 0), STRETCH)


@pytest.mark.parametrize('modifier', [CORDS])
def test_gerber_trace_drag_preserves_width_and_one_undo_step(modifier):
    editor, original = track_editor(LineString([(0, 0), (4, 0), (8, 0), (12, 0)]))
    editor.tool_shape = FakeCollection()
    grips = editor.vertex_grips
    grips.sync()
    assert grips.press((0, 0), (5, 0), modifier)
    grips.release((20, 0), (5, 2))
    result = editor.selected[0]
    assert result is not original
    assert result.geo['solid'].equals_exact(result.geo['follow'].buffer(0.2, 16), 1e-9)
    assert len(editor.history.undo_stack) == 1
    assert editor.undo() and editor.selected == [original]
    assert editor.redo()


def test_grid_snaps_the_displacement_without_a_jump_at_the_press():
    editor, original = geo_drag(LineString([(0, 0), (4, 0)]))
    editor.app.grid_status = lambda: True
    editor.editor_options = {'global_gridx': 0.1, 'global_gridy': 0.1}
    editor.snap = lambda x, y: (round(x), round(y))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (1.2, 0), CORDS)
    grips.release((20, 0), (1.3, 1.7))
    assert list(editor.selected[0].geo.coords) == [(0, 0), (0, 2), (4, 2), (4, 0)]





