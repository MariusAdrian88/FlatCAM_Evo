"""Select by Size: filter by area / length, Select replaces the selection, Delete is one undo step."""
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString, Point, Polygon, MultiLineString, box

from appEditors import appGeoEditor as geo
from tests.test_geo_editor_join import join_editor
from tests.test_geo_editor_split import qt_application  # noqa: F401  (autouse Qt fixture)


def size_editor(selected_all=True):
    """Editor with: lines of length 1, 3, 10; boxes of area 1, 4, 100; a point."""
    editor, shapes = join_editor([[(0, 0), (1, 0)], [(0, 0), (3, 0)], [(0, 0), (10, 0)]])
    for side in (1, 2, 10):
        shape = geo.DrawToolShape(box(0, 0, side, side))
        editor.storage.insert(shape)
        shapes.append(shape)
    point = geo.DrawToolShape(Point(5, 5))
    editor.storage.insert(point)
    shapes.append(point)
    editor.selected = list(shapes) if selected_all else []
    editor.sync_tree_selection = lambda: geo.AppGeoEditor.sync_tree_selection(editor)
    editor.update_all_vertex_count = lambda: geo.AppGeoEditor.update_all_vertex_count(editor)
    return editor, shapes


def configure(editor, area=None, length=None):
    ui = editor.ui
    ui.size_area_chk.setChecked(area is not None)
    ui.size_length_chk.setChecked(length is not None)
    if area is not None:
        ui.size_min_area_entry.setText(str(area[0]))
        ui.size_max_area_entry.setText(str(area[1]))
    if length is not None:
        ui.size_min_length_entry.setText(str(length[0]))
        ui.size_max_length_entry.setText(str(length[1]))


def last_message(editor):
    return editor.app.inform.emit.call_args.args[0]


def test_area_filter():
    editor, shapes = size_editor()
    configure(editor, area=(2, 10))
    assert geo.AppGeoEditor.size_filter_matches(editor) == [shapes[4]]


def test_scope_is_selection_when_present_else_all():
    editor, shapes = size_editor()
    configure(editor, length=(0, 100))
    editor.selected = [shapes[0], shapes[3]]
    assert geo.AppGeoEditor.size_filter_matches(editor) == [shapes[0]]
    editor.selected = []
    assert len(geo.AppGeoEditor.size_filter_matches(editor)) == 3


def test_delete_is_one_undo_step():
    editor, shapes = size_editor(selected_all=False)
    configure(editor, area=(0, 5), length=(0, 5))
    assert len(geo.AppGeoEditor.on_delete_by_size(editor)) == 4
    remaining = set(editor.storage.get_objects())
    assert remaining == {shapes[2], shapes[5], shapes[6]}
    assert len(editor._history_undo) == 1
    assert last_message(editor) == '4 shapes deleted'
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(shapes)
    assert editor.redo()
    assert set(editor.storage.get_objects()) == remaining


@pytest.mark.parametrize('low,high', [('abc', '5')])
@pytest.mark.parametrize('action', ['on_select_by_size'])
def test_invalid_input_warns_and_changes_nothing(action, low, high):
    editor, shapes = size_editor()
    editor.selected = shapes[:2]
    editor.ui.size_area_chk.setChecked(True)
    editor.ui.size_min_area_entry.setText(low)
    editor.ui.size_max_area_entry.setText(high)
    assert getattr(geo.AppGeoEditor, action)(editor) == 'fail'
    assert 'WARNING' in last_message(editor)
    assert editor.selected == shapes[:2]
    assert set(editor.storage.get_objects()) == set(shapes)
    assert not editor._history_undo


