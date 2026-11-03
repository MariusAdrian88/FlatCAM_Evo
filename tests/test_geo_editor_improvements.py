"""Geometry picking uses screen intent; affine replacements retain ready render buffers."""
from types import SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy
import pickle

import numpy as np
import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import LineString, Polygon, box

from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, FCSelect, FCMove
from appGUI.VisPyVisuals import ShapeCollectionVisual
from tests.test_geo_editor_delete_plot import pick_editor
from tests.test_geo_editor_history import editor_with_history, interactive_move_editor, run_worker


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_overlap_cycles_and_preserves_unrelated_multiselection(monkeypatch):
    big, small, other = [DrawToolShape(geo) for geo in (box(0, 0, 10, 10), box(4, 4, 6, 6), box(20, 0, 21, 1))]
    editor = pick_editor([big, small, other])
    editor.selected = [other]
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        lambda: QtCore.Qt.KeyboardModifier.ControlModifier)
    editor.click_select((5, 5))
    assert editor.selected == [other, small]
    editor.click_select((5.01, 5))
    assert editor.selected == [other, big]
    editor.click_select((5, 5))
    assert editor.selected == [other, small]
    assert '2' in str(editor.app.inform.emit.call_args_list)
    # Moving farther than five pixels resets even when the same polygons overlap.
    editor.click_select((5.2, 5))
    assert editor.selected == [other]


def canvas_editor():
    editor, _, _ = interactive_move_editor()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: (pos[0] / 100, pos[1] / 100))
    editor.snap = Mock(return_value=(0, 0))
    editor.app.grid_status = lambda: True
    editor.app.delete_selection_shape = Mock()
    editor.draw_selection_area_handler = Mock()
    editor.app.selection_type = None
    tool = FCSelect.__new__(FCSelect)
    tool.draw_app, tool.name = editor, 'select'
    tool.click_release = Mock()
    editor.active_tool = tool
    return editor


def test_selection_marquee_uses_only_x_and_y_of_the_vispy_coords():
    # VisPy translate_coords() gives (x, y, z, w); the marquee and the area pick need (x, y)
    editor = canvas_editor()
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: np.array([pos[0] / 100, pos[1] / 100, 0.0, 1.0]))
    editor.app.app_cursor = Mock()
    editor.app.plotcanvas = SimpleNamespace(cursor_color='black', on_update_text_hud=Mock())
    editor.app.ui.update_location_labels = Mock()
    editor.app.draw_moving_selection_shape = Mock()
    editor.update_utility_geometry = Mock()
    AppGeoEditor.on_canvas_click(editor, SimpleNamespace(pos=(23, 37), button=1))
    AppGeoEditor.on_canvas_move(editor, SimpleNamespace(pos=(3, 57), button=1, is_dragging=True))
    start, end = editor.app.draw_moving_selection_shape.call_args.args[:2]
    assert len(start) == 2 and len(end) == 2
    AppGeoEditor.on_canvas_click_release(editor, SimpleNamespace(pos=(3, 57), button=1))
    start, end, __ = editor.draw_selection_area_handler.call_args.args
    assert len(start) == 2 and len(end) == 2


@pytest.mark.parametrize('method,kind', [('buffer_int', 'polygon'), ('buffer_int', 'open')])
def test_buffer_adds_all_usable_contours_and_keeps_original(method, kind):
    from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
    from camlib import flatten_shapely_geometry
    from shapely import MultiLineString
    dumbbell = box(0, 0, 4, 4).union(box(6, 0, 10, 4)).union(box(3, 1.8, 7, 2.2))
    geo = {'polygon': dumbbell, 'ring': dumbbell.exterior,
           'multipart': MultiLineString([dumbbell.exterior]),
           'split_multipart': dumbbell.buffer(-0.3, join_style='mitre'),
           'open': LineString([(0, 0), (4, 0)])}[kind]
    shape = DrawToolShape(geo)
    editor = editor_with_history([shape], [shape])
    editor.app.app_units = 'MM'
    tool = BufferSelectionTool.__new__(BufferSelectionTool)
    tool.draw_app = editor
    getattr(tool, method)(0.5, 'mitre')
    run_worker(editor)
    added = [part for s in editor.storage.get_objects() if s is not shape
             for part in flatten_shapely_geometry(s.geo)]
    assert shape in list(editor.storage.get_objects())
    if kind == 'open' and method == 'buffer_int':
        assert not added
        assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
        assert not editor.undo()
    else:
        assert added and all(not part.is_empty for part in added)
        if method == 'buffer_int':
            assert len(added) == 2
            assert all(dumbbell.covers(part) for part in added)
        assert editor.undo()
        assert list(editor.storage.get_objects()) == [shape]


@pytest.mark.parametrize('method,distance', [('buffer_int', 10.0)])
def test_buffer_without_usable_result_never_reports_done_or_adds_history(method, distance):
    from appEditors.geo_plugins.GeoBufferPlugin import BufferSelectionTool
    shape = DrawToolShape(box(0, 0, 2, 2))
    editor = editor_with_history([shape], [shape])
    editor.app.app_units = 'MM'
    tool = BufferSelectionTool.__new__(BufferSelectionTool)
    tool.draw_app = editor
    getattr(tool, method)(distance, 'mitre')
    assert run_worker(editor) == 'fail'
    assert list(editor.storage.get_objects()) == [shape]
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    assert not editor.undo()
