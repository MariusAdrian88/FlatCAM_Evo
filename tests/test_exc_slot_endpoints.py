"""Native slot endpoint edits replace wrappers, never polygon vertices."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock, MagicMock
from concurrent.futures import ThreadPoolExecutor

import pytest
from PyQt6 import QtWidgets
from shapely import LineString, Point
from shapely.affinity import scale

from appEditors import appExcEditor as exc
from appEditors.appGeoEditor import AppGeoEditor
from appHandlers.appIO import appIO
from test_exc_editor_history import scene
from test_geo_editor_delete_plot import FakeCollection
from tests.helpers_objects import app, _headless_qt, make_same_diameter_excellon, reparse_excellon  # noqa: F401,E402


def endpoint_scene():
    editor, first, slot = scene()
    slot.exc_slot = ((4.0, 0.0), (6.0, 1.0))
    slot.exc_slot_geometry = slot.geo
    editor.selected = [slot]
    editor.delete_utility_geometry = Mock()
    editor.ui = SimpleNamespace()
    for name in ('edit_slot_endpoints', 'on_slot_endpoints_apply', 'load_slot_endpoints'):
        method = getattr(exc.AppExcEditor, name, None)
        assert method is not None, 'Native endpoint editing is missing: ' + name
        setattr(editor, name, MethodType(method, editor))
    return editor, first, slot


def test_endpoint_replacement_preserves_order_width_selection_and_history():
    editor, first, slot = endpoint_scene()
    before_counts = list(editor.slot_points_edit[1.0])
    result = editor.edit_slot_endpoints(slot, ((4, 0), (10, 3)))
    replacement = list(editor.storage_dict[1.0].get_objects())[1]
    assert result != 'fail'
    assert replacement is not slot
    assert replacement.exc_slot == ((4, 0), (10, 3))
    assert replacement.exc_slot_geometry is replacement.geo
    assert replacement.geo.equals(LineString(replacement.exc_slot).buffer(0.5))
    assert editor.selected == [replacement]
    assert list(editor.storage_dict[1.0].get_objects())[0] is first
    assert editor.slot_points_edit[1.0] == before_counts
    assert editor.tool2tooldia == {1: 1.0}
    assert editor.history.revision == 1
    assert len(editor.history.undo_stack) == 1
    editor.replot.assert_called_once()
    assert editor.undo()
    assert editor.selected == [slot]
    assert list(editor.storage_dict[1.0].get_objects()) == [first, slot]
    assert slot.exc_slot_geometry is slot.geo
    assert editor.redo()
    assert editor.selected == [replacement]


@pytest.mark.parametrize('endpoints', [((4, 0), (4, 0)), ((float('nan'), 0), (9, 1))])
def test_invalid_endpoints_are_atomic(endpoints):
    editor, first, slot = endpoint_scene()
    assert editor.edit_slot_endpoints(slot, endpoints) == 'fail'
    assert list(editor.storage_dict[1.0].get_objects()) == [first, slot]
    assert not editor.history.undo_stack
    assert not editor.is_modified
    editor.replot.assert_not_called()


def test_numeric_endpoint_controls_use_the_same_operation():
    editor, first, slot = endpoint_scene()
    editor.ui.slot_endpoint_entries = [Mock() for _ in range(4)]
    editor.load_slot_endpoints()
    for entry, value in zip(editor.ui.slot_endpoint_entries, (4, 0, 6, 1)):
        entry.set_value.assert_called_once_with(value)
    for entry, value in zip(editor.ui.slot_endpoint_entries, (4, 0, 9, 7)):
        entry.get_value.return_value = value
    editor.on_slot_endpoints_apply()
    assert editor.selected[0].exc_slot == ((4, 0), (9, 7))
    assert len(editor.history.undo_stack) == 1
    for entry, value in zip(editor.ui.slot_endpoint_entries, (3, -1, 9, 7)):
        entry.get_value.return_value = value
    editor.on_slot_endpoints_apply()
    assert editor.selected[0].exc_slot == ((3, -1), (9, 7))
    assert len(editor.history.undo_stack) == 2


@pytest.mark.parametrize('units, export_units', [('MM', 'INCH'), ('IN', 'METRIC')])
def test_edited_resized_slot_roundtrip_both_document_and_export_units(app, tmp_path, units, export_units):
    editor, first, slot = endpoint_scene()
    factor = 25.4 if units == 'IN' else 1.0
    diameter = 1.0 / factor
    slot.geo = scale(slot.geo, xfact=1 / factor, yfact=1 / factor, origin=(0, 0))
    slot.exc_slot = tuple(tuple(v / factor for v in point) for point in slot.exc_slot)
    slot.exc_slot_geometry = slot.geo
    storage = AppGeoEditor.make_storage()
    storage.insert(slot)
    editor.storage_dict = {diameter: storage}
    editor.tool2tooldia = {1: diameter}
    editor.olddia_newdia = {diameter: diameter}
    editor.points_edit, editor.slot_points_edit = {diameter: []}, {diameter: [slot.exc_slot]}
    endpoints = ((4 / factor, 0), (11.1234 / factor, 7.6789 / factor))
    replacement = editor.edit_slot_endpoints(slot, endpoints)
    # 2.54 mm / 0.1 inch is exactly representable in both existing tool-header formats.
    editor.resize_features([replacement], 2.54 / factor)
    resized = editor.selected[0]
    assert resized.exc_slot == endpoints
    assert editor.undo() and editor.redo()
    source = make_same_diameter_excellon(app)
    source.units = units
    app.app_units = units
    editor._save_excellon_in_place = Mock()
    exc.AppExcEditor.update_fcexcellon(editor, source, in_place=True)
    source.tools = editor.new_tools
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 6
    app.f_handlers = appIO(app)
    assert source.regenerate_source()
    again = reparse_excellon(app, source.source_file, tmp_path)
    back = (25.4 if again.units == 'IN' else 1) / factor
    tool = next(iter(again.tools.values()))
    start, stop = tool['slots'][0]
    assert (start.x * back, start.y * back) == pytest.approx(endpoints[0], abs=0.001 / factor)
    assert (stop.x * back, stop.y * back) == pytest.approx(endpoints[1], abs=0.001 / factor)
    assert tool['tooldia'] * back == pytest.approx(2.54 / factor, abs=0.001 / factor)


