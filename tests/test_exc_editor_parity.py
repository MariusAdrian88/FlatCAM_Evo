"""Excellon Editor parity tests: click-select cycling (pick index, stacked drills, modifiers, tool table sync) and the
numeric offset transform (drills and slots, one undo step, invalid input, the offset action). Headless: the editor is
real, the app is a stand-in."""

from contextlib import contextmanager
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, MultiLineString

from appEditors.appExcEditor import AppExcEditor, EDITOR_ACTION_PROPERTY
from appEditors.appGeoEditor import DrawToolShape
from appEditors.exc_plugins.ExcProperties import native_feature_properties
from test_exc_editor_defects import bind, qt_editor, warned
from test_exc_editor_history import scene
from tests.helpers_objects import _headless_qt  # noqa: F401,E402

OFFSET_LABEL = 'Offset selected features'
MSELECT_KEY = 'Control'
CONTROL = QtCore.Qt.KeyboardModifier.ControlModifier
NO_MODIFIER = QtCore.Qt.KeyboardModifier.NoModifier


# ---------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------

def cross(x, y, size=1.0):
    """A drill as the editor stores it: a MultiLineString cross whose arms are the drill diameter."""
    r = size / 2
    return DrawToolShape(MultiLineString([[(x - r, y), (x + r, y)], [(x, y - r), (x, y + r)]]))


def slot_shape():
    """A native-looking slot 4 units long, 1 unit wide, from (0, 0) to (4, 0)."""
    return DrawToolShape(LineString([(0, 0), (4, 0)]).buffer(0.5))


def pick_editor():
    """A real AppExcEditor with no storages: the canvas reports 0.01 plot units per pixel (PICK_TOLERANCE_PX = 5)."""
    app, editor = qt_editor('MM')
    app.plotcanvas.pixel_size = Mock(return_value=0.01)
    app.options['global_mselect_key'] = MSELECT_KEY
    editor.ui = SimpleNamespace(tools_table_exc=MagicMock())
    editor.storage_dict, editor.tool2tooldia, editor.selected = {}, {}, []
    editor.last_tool_selected = 0
    return app, editor


def put(editor, dia, *shapes):
    """Store shapes in the tool storage of diameter dia, in the given order, and number the tools like build_ui()."""
    storage = AppExcEditor.make_storage()
    storage.insert_many(list(shapes))
    editor.storage_dict[dia] = storage
    editor.tool2tooldia = {index: d for index, d in enumerate(sorted(editor.storage_dict), 1)}
    return storage


@contextmanager
def held(modifier):
    """Pretend the keyboard modifier is held during the click."""
    with patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=modifier):
        yield


def same(actual, expected):
    """Same objects in the same order (DrawToolShape equality is identity, this states it explicitly)."""
    return len(actual) == len(expected) and all(a is b for a, b in zip(actual, expected))


def flat(points):
    return [coordinate for point in points for coordinate in point]


def offset_editor():
    """The scene() stand-in with the real transform_features: a drill at (0, 0) and a slot (4, 0)-(6, 1), both 1.0."""
    editor, drill_shape, slot = scene()
    editor.transform_features = MethodType(AppExcEditor.transform_features, editor)
    return editor, drill_shape, slot


# ---------------------------------------------------------------------------------------------------------------
# 1. click-select cycling
# ---------------------------------------------------------------------------------------------------------------


def test_repeated_click_on_stacked_drills_cycles_a_b_a():
    app, editor = pick_editor()
    first, second = cross(5, 5), cross(5, 5)
    put(editor, 1.0, first, second)
    picked = []
    for _ in range(3):
        assert click(editor, (5, 5)) == 1
        picked.append(editor.selected[0])
    assert picked[0] is first and picked[1] is second and picked[2] is first


def test_mselect_modifier_adds_and_removes_features():
    app, editor = pick_editor()
    first, second = cross(0, 0), cross(10, 0)
    put(editor, 1.0, first, second)
    click(editor, (0, 0))
    assert same(editor.selected, [first])
    with held(CONTROL):
        click(editor, (10, 0))
        assert same(editor.selected, [first, second])
        click(editor, (0, 0))            # toggles the feature off again
    assert same(editor.selected, [second])


def click(editor, point):
    """Click at point; returns the number of selected features (the cycle sets one at a time)."""
    editor.click_select(point)
    return len(editor.selected)


# ---------------------------------------------------------------------------------------------------------------
# 2. numeric offset
# ---------------------------------------------------------------------------------------------------------------

def test_offset_moves_drill_and_slot_and_keeps_diameters():
    editor, drill_shape, slot = offset_editor()
    assert flat(native_feature_properties(slot, 1.0, 0)['endpoints']) == pytest.approx([4, 0, 6, 1])
    slot_area = slot.geo.area
    editor.selected = [drill_shape, slot]
    assert editor.transform_features('offset', (2, 3)) != 'fail'

    shapes = list(editor.storage_dict[1.0].get_objects())
    assert len(shapes) == 2 and list(editor.storage_dict) == [1.0]
    moved_drill = next(s for s in shapes if native_feature_properties(s, 1.0, 0)['type'] == 'drill')
    moved_slot = next(s for s in shapes if s is not moved_drill)

    assert native_feature_properties(moved_drill, 1.0, 0)['center'] == pytest.approx((2, 3))
    bounds = moved_drill.geo.bounds
    assert bounds[2] - bounds[0] == pytest.approx(1.0)          # the drill keeps its 1.0 diameter
    assert flat(native_feature_properties(moved_slot, 1.0, 0)['endpoints']) == pytest.approx([6, 3, 8, 4])
    assert flat(moved_slot.exc_slot) == pytest.approx([6, 3, 8, 4])   # the native centerline moved too
    assert moved_slot.geo.area == pytest.approx(slot_area)      # same width, only translated
    assert editor.tool2tooldia == {1: 1.0}
    assert editor.is_modified


def test_offset_is_one_undo_step_and_redo_reapplies_it():
    editor, drill_shape, slot = offset_editor()
    editor.selected = [drill_shape, slot]
    assert editor.transform_features('offset', (2, 3)) != 'fail'
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert same(list(editor.storage_dict[1.0].get_objects()), [drill_shape, slot])
    assert same(editor.selected, [drill_shape, slot])
    assert drill_shape.geo.centroid.coords[0] == pytest.approx((0, 0))
    assert editor.redo()
    assert len(editor.history.undo_stack) == 1
    moved = list(editor.storage_dict[1.0].get_objects())
    assert any(native_feature_properties(s, 1.0, 0)['type'] == 'drill' and
               native_feature_properties(s, 1.0, 0)['center'] == pytest.approx((2, 3)) for s in moved)


def test_offset_with_nothing_selected_fails_and_warns():
    editor, drill_shape, slot = offset_editor()
    editor.selected = []
    assert editor.transform_features('offset', (1, 1)) == 'fail'
    assert warned(editor)
    assert not editor.history.undo_stack


