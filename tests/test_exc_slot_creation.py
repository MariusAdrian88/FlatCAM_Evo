"""Native slot creation keeps source endpoints, not polygon estimates."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString
from shapely.affinity import translate

from appEditors.appExcEditor import SlotAdd, SlotArray
from appEditors.appExcEditor import AppExcEditor, translated_exc_shape
from appEditors.exc_plugins.ExcProperties import feature_properties
from appHandlers.appIO import appIO
from test_exc_editor_history import scene
from tests.helpers_objects import app, _headless_qt, make_same_diameter_excellon, reparse_excellon  # noqa: F401,E402


def slot_tool(cls, direction='A', length=5.0, angle=31.0):
    editor, _, _ = scene()
    editor.app.defaults = SimpleNamespace(get=lambda *args: 64, factory_defaults={})
    tool = cls.__new__(cls)
    tool.draw_app = editor
    tool.ui = Mock()
    tool.ui.slot_length_entry.get_value.return_value = length
    tool.ui.slot_direction_radio.get_value.return_value = direction
    tool.ui.slot_angle_spinner.get_value.return_value = angle
    tool.ui.slot_angle_entry.get_value.return_value = angle
    tool.slot_point = (10, 20)
    tool.points = (10, 20)
    tool.selected_dia = 1.0
    tool.slot_array = 'linear'
    tool.slot_array_size = 3
    tool.slot_axis = 'X'
    tool.slot_pitch = 7
    return tool


@pytest.mark.parametrize('cls, direction', [(SlotAdd, 'A'), (SlotArray, 'X')])
def test_native_creation_and_linear_array(cls, direction):
    tool = slot_tool(cls, direction)
    tool.make()
    shapes = tool.geometry if isinstance(tool.geometry, list) else [tool.geometry]
    assert len(shapes) == (3 if cls is SlotArray else 1)
    angle = math.radians(31)
    vector = (2, 0) if direction == 'X' else (0, 2) if direction == 'Y' else (
        2 * math.sin(angle), 2 * math.cos(angle))
    for index, shape in enumerate(shapes):
        x, y = 10 + index * 7, 20
        assert shape.exc_slot[0] == pytest.approx((x - vector[0], y - vector[1]))
        assert shape.exc_slot[1] == pytest.approx((x + vector[0], y + vector[1]))
        assert shape.exc_slot_geometry is shape.geo
        assert not feature_properties(shape, 1, index)['inferred']
        assert LineString(shape.exc_slot).buffer(0.5).hausdorff_distance(shape.geo) < 0.01


@pytest.mark.parametrize('direction', ['CCW'])
def test_circular_array_rotates_native_endpoints_with_geometry(direction):
    tool = slot_tool(SlotArray)
    tool.origin = (4, 9)
    tool.ui.array_direction_radio.get_value.return_value = direction
    tool.ui.array_angle_entry.get_value.return_value = 60
    tool.ui.array_size_entry.get_value.return_value = 3
    shapes = tool.circular_util_shape(10, 0.4)
    for shape in shapes:
        assert math.dist(*shape.exc_slot) == pytest.approx(4)
        assert shape.exc_slot_geometry is shape.geo
        assert LineString(shape.exc_slot).buffer(0.5).hausdorff_distance(shape.geo) < 0.01


@pytest.mark.parametrize('cls, length', [(SlotAdd, 0), (SlotArray, float('nan'))])
def test_invalid_slot_creation_leaves_counts_and_storage_unchanged(cls, length):
    tool = slot_tool(cls, length=length)
    before = list(tool.draw_app.slot_points_edit[1.0])
    assert tool.make() == 'fail'
    assert not tool.complete
    assert tool.draw_app.slot_points_edit[1.0] == before
    assert len(list(tool.draw_app.storage_dict[1.0].get_objects())) == 2


@pytest.mark.parametrize('cls, circular', [(SlotAdd, False), (SlotArray, True)])
@pytest.mark.parametrize('export_units', ['METRIC'])
def test_created_and_copied_slots_history_save_and_native_roundtrip(app, tmp_path, cls, export_units, circular):
    tool = slot_tool(cls)
    if circular and cls is SlotArray:
        tool.slot_array = 'circular'
        tool.slot_angle = 60
        tool.origin, tool.destination = (4, 9), (14, 9)
        tool.ui.array_direction_radio.get_value.return_value = 'CCW'
        tool.ui.array_angle_entry.get_value.return_value = 60
        tool.ui.array_size_entry.get_value.return_value = 3
    tool.make()
    editor = tool.draw_app
    storage = editor.storage_dict[1.0]
    storage.replace(removed=list(storage.get_objects()), inserted=[])
    shapes = tool.geometry if isinstance(tool.geometry, list) else [tool.geometry]
    copied = translated_exc_shape(shapes[0], translate(shapes[0].geo, xoff=2.3, yoff=-4.5))
    shapes = shapes + [copied]
    editor.add_exc_shape(shapes, storage)
    assert editor.undo()
    assert list(storage.get_objects()) == []
    assert editor.redo()
    assert list(storage.get_objects()) == shapes
    source = make_same_diameter_excellon(app)
    editor._save_excellon_in_place = Mock()
    AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    source.tools = editor.new_tools
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 6
    app.f_handlers = appIO(app)
    assert source.regenerate_source()
    again = reparse_excellon(app, source.source_file, tmp_path)
    factor = 25.4 if again.units == 'IN' else 1.0
    slots = again.tools[1]['slots']
    assert len(slots) == len(shapes)
    for shape, (start, stop) in zip(shapes, slots):
        assert (start.x * factor, start.y * factor) == pytest.approx(shape.exc_slot[0], abs=0.001)
        assert (stop.x * factor, stop.y * factor) == pytest.approx(shape.exc_slot[1], abs=0.001)
