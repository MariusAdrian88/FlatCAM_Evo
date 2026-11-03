"""Toolbar Join and continuous Gerber tracks, including bends and legacy click records."""
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import LineString, MultiLineString

from appEditors.appGeoEditor import AppGeoEditor
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins.GrbTrackBend import sweep_parameters
from appGUI.MainGUI import MainGUI
from tests.test_editor_corner import track_tool_with_storage, track_click
from tests.test_gerber_follow_ops import editor_for, track, flash, footprint
from tests.test_gerber_editor_pick import make_select
from tests.test_geo_editor_join import join_editor
from tests.test_vertex_grips import FakeCollection


@pytest.mark.parametrize('coords', [[(0, 0), (5, 0), (8, 4)]])
def test_completed_track_is_one_record_and_drags_across_the_bend(coords):
    tool, storage = track_tool_with_storage(5)
    tool.buf_val, tool.steps_per_circle = 1, 16
    for point in coords:
        track_click(tool, point)
    originals = storage[:]
    editor = editor_for(storage)
    editor.active_tool = tool
    editor.last_aperture_selected = 10
    tool.draw_app = editor
    tool.make()
    editor.on_grb_shape_complete()
    result, = editor.storage_dict[10]['geometry']
    assert list(result.geo['follow'].coords) == coords
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == originals
    assert editor.redo()
    result, = editor.storage_dict[10]['geometry']
    editor.selected = [result]
    make_select(editor)
    editor.tool_shape = FakeCollection()
    grips = editor.vertex_grips
    assert grips.press((0, 0), (1, 0), QtCore.Qt.KeyboardModifier.NoModifier)
    grips.release((20, 0), (1, 2))
    moved, = editor.storage_dict[10]['geometry']
    assert list(moved.geo['follow'].coords) == [(x, y + 2) for x, y in coords]


@pytest.mark.parametrize('paths', [
    [[(0, 0), (5, 0), (8, 4)], [(8, 4), (13, 4), (16, 8)]],
])
def test_join_bent_records_preserves_copper_and_remains_vertex_editable(paths):
    a, b = [track(coords) for coords in paths]
    editor = editor_for([a, b])
    before = footprint(editor)
    assert editor.apply_path_join(0) != 'fail'
    result, = editor.selected
    assert footprint(editor).equals(before)
    assert sweep_parameters(editor, editor.storage_dict[10], result) == (1, 16)
    assert editor.apply_vertex_edit('move', 1, (5, 1)) != 'fail'
    assert editor.undo() and editor.selected == [result]
    assert editor.undo() and editor.storage_dict[10]['geometry'] == [a, b]




def test_join_does_not_ignore_a_selected_multiline_branch():
    a, b = track([(0, 0), (5, 0)]), track([(5, 0), (10, 0)])
    follow = MultiLineString([[(5, 0), (5, 5)], [(20, 0), (25, 0)]])
    branch = DrawToolShape({'follow': follow, 'solid': follow.buffer(1, 16)})
    originals = [a, b, branch]
    editor = editor_for(originals[:])
    assert editor.apply_path_join(0) == 'fail'
    assert editor.storage_dict[10]['geometry'] == originals
    assert not editor.history.undo_stack


