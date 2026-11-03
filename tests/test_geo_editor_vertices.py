"""Numeric vertex edits replace one contour without losing its history or holes."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtWidgets
from shapely.geometry import LineString, LinearRing, Polygon, MultiLineString, box

from appEditors.appGeoEditor import AppGeoEditor, AppGeoEditorUI, DrawToolShape
from appGUI.GUIElements import FCTree
from tests.test_geo_editor_history import editor_with_history, run_worker


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def vertex_editor(geometry):
    shape = DrawToolShape(geometry)
    shape.data['custom'] = {'label': 'track'}
    editor = editor_with_history([shape], [shape])
    editor.plot_all = lambda: AppGeoEditor.plot_all(editor)
    editor.plot_changes = lambda: AppGeoEditor.plot_changes(editor)
    editor.plot_all()
    editor.apply_vertex_edit = lambda *a, **k: AppGeoEditor.apply_vertex_edit(editor, *a, **k)
    return editor, shape


@pytest.mark.parametrize('action,index,point,expected', [
    ('move', 1, (2, 1), [(0, 0), (2, 1), (4, 0)]),
    ('insert', 0, (1, 0), [(0, 0), (1, 0), (2, 0), (4, 0)]),
])
def test_open_path_edit_is_one_replacement_with_history(action, index, point, expected):
    editor, original = vertex_editor(LineString([(0, 0), (2, 0), (4, 0)]))
    assert editor.apply_vertex_edit(action, index, point) != 'fail'
    replacement, = editor.selected
    assert list(replacement.geo.coords) == expected
    assert replacement.data['custom'] == original.data['custom']
    assert replacement.data['bounds'] == replacement.geo.bounds
    assert editor.storage.nearest(expected[index if index < len(expected) else -1])[1] is replacement
    assert editor.sel_shapes.items == [replacement.geo]
    editor.build_ui_sig.emit.assert_called_once()
    assert editor.undo()
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert editor.sel_shapes.items == [original.geo]
    assert not editor.undo()
    assert editor.redo()
    assert editor.selected == [replacement]
    assert editor.sel_shapes.items == [replacement.geo]


@pytest.mark.parametrize('factory', [Polygon])
@pytest.mark.parametrize('index', [0])
def test_closing_vertex_alias_moves_both_ends(factory, index):
    editor, original = vertex_editor(factory([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]))
    assert editor.apply_vertex_edit('move', index, (-1, -1)) != 'fail'
    geometry = editor.selected[0].geo
    coords = list(geometry.exterior.coords if isinstance(geometry, Polygon) else geometry.coords)
    assert coords[0] == coords[-1] == (-1, -1)
    assert type(geometry) is type(original.geo)
    assert len(coords) == 5


def test_polygon_holes_survive_move_insert_and_remove():
    hole = [(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)]
    editor, original = vertex_editor(Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [hole]))
    for action, index, point in [('move', 0, (-1, -1)), ('insert', 1, (6, 3)), ('remove', 2, None)]:
        assert editor.apply_vertex_edit(action, index, point) != 'fail'
        assert list(editor.selected[0].geo.interiors[0].coords) == hole
    assert len(editor._history_undo) == 3
    for _ in range(3):
        assert editor.undo()
    assert editor.selected == [original]


@pytest.mark.parametrize('factory', [Polygon])
def test_insert_on_closing_segment_and_remove_closing_alias(factory):
    editor, original = vertex_editor(factory([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]))
    assert editor.apply_vertex_edit('insert', 3, (0, 2)) != 'fail'
    assert editor.apply_vertex_edit('remove', 5) != 'fail'
    geometry = editor.selected[0].geo
    coords = list(geometry.exterior.coords if isinstance(geometry, Polygon) else geometry.coords)
    assert coords == [(4, 0), (4, 4), (0, 4), (0, 2), (4, 0)]
    assert geometry.is_valid


@pytest.mark.parametrize('geometry,action,index,point', [
    (LineString([(0, 0), (1, 0)]), 'remove', 0, None),
    (LineString([(0, 0), (2, 0), (4, 0)]), 'move', 1, (float('nan'), 0)),
    (Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [[(1, 1), (2, 1), (2, 2), (1, 2)]]),
     'move', 0, (4, 4)),
])
def test_invalid_edits_preserve_storage_selection_and_redo(geometry, action, index, point):
    editor, original = vertex_editor(geometry)
    assert editor.apply_vertex_edit(action, index, point) == 'fail'
    assert editor.selected == [original]
    assert list(editor.storage.get_objects()) == [original]
    assert not editor.undo()
    editor.build_ui_sig.emit.assert_not_called()
    assert editor.app.inform.emit.called


@pytest.mark.parametrize('geometry', [LineString([(0, 0), (1, 0), (1, 1), (0, 1)])])
def test_3d_or_self_crossing_result_is_rejected(geometry):
    editor, original = vertex_editor(geometry)
    assert editor.apply_vertex_edit('move', 0, (0, 2)) == 'fail'
    assert editor.selected == [original]
    assert not editor.undo()


