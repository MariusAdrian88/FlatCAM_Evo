"""Selected native transforms use the real editor transaction and storage paths."""

from types import MethodType
from unittest.mock import Mock, MagicMock, patch

import pytest

from appEditors.appExcEditor import AppExcEditor
from appEditors.exc_plugins.ExcProperties import native_feature_properties
from test_exc_editor_history import scene, drill
from tests.helpers_objects import app, _headless_qt, reparse_excellon  # noqa: F401,E402
from appHandlers.appIO import appIO
from PyQt6 import QtCore
from PyQt6 import QtWidgets
from appGUI.GUIElements import FCTable
from appEditors.appExcEditor import CopyEditorExc, MoveEditorExc
from shapely.geometry import box


def editor_scene():
    editor, drill, slot = scene()
    editor.transform_features = MethodType(AppExcEditor.transform_features, editor)
    return editor, drill, slot


@pytest.mark.parametrize('reference,expected', [
    ('upper_right', (1, 1)),
])
def test_selection_corner_reference(reference, expected):
    editor, first, _ = editor_scene()
    assert editor.transform_features('scale', 2, reference) != 'fail'
    assert native_feature_properties(editor.selected[0], 2, 0)['center'] == pytest.approx(
        (-expected[0] / 2, -expected[1] / 2))
    assert editor.undo() and editor.selected == [first]


@pytest.mark.parametrize('diameter', [1.0])
def test_real_table_keeps_exact_scaled_diameters(diameter):
    from types import SimpleNamespace
    from shapely.affinity import scale
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    editor, first, _ = editor_scene()
    storage = editor.make_storage()
    first.geo = scale(first.geo, diameter, diameter, origin=(0, 0))
    storage.insert(first)
    other = drill(4)
    other.geo = scale(other.geo, diameter * 1.000001, diameter * 1.000001, origin=(4, 0))
    second = editor.make_storage()
    second.insert(other)
    editor.storage_dict = {diameter: storage, diameter * 1.000001: second}
    editor.olddia_newdia = {key: key for key in editor.storage_dict}
    editor.points_edit = {key: [(0, 0)] for key in editor.storage_dict}
    editor.slot_points_edit = {key: [] for key in editor.storage_dict}
    editor.tool2tooldia = dict(enumerate(editor.storage_dict, 1))
    editor.app.ui = MagicMock()
    editor.app.app_units = 'MM'
    editor.app.defaults = MagicMock()
    editor.app.options['global_mselect_key'] = 'Control'
    editor.edited_obj = SimpleNamespace(obj_options={'name': 'exact'})
    editor.ui = SimpleNamespace(tools_table_exc=FCTable(), name_entry=Mock(), exc_edit_widget=QtWidgets.QWidget())
    editor.ui.tools_table_exc.setColumnCount(4)
    for name in ('build_ui', 'on_row_selected', 'on_tool_edit', 'on_tool_delete', 'sync_tool_table_selection'):
        method = getattr(AppExcEditor, name)
        setattr(editor, name, lambda *args, method=method: method(editor, *args))
    editor.build_ui()
    editor.transform_features('scale', 1.234567)
    exact = diameter * 1.234567
    assert exact in editor.tool2tooldia.values()
    row = next(row for row in range(2)
               if editor.ui.tools_table_exc.item(row, 1).data(QtCore.Qt.ItemDataRole.UserRole) == exact)
    editor.on_row_selected(row, 0)
    assert editor.selected and editor.selected[0] in editor.storage_dict[exact].get_objects()
    assert editor.undo()
    assert editor.redo()
    assert exact in editor.tool2tooldia.values()
    editor.ui.tools_table_exc.clearSelection()
    editor.ui.tools_table_exc.selectRow(row)
    editor.app.plotcanvas = MagicMock()
    editor.launched_from_shortcuts = False
    assert set(MoveEditorExc(editor).selected_dia_list) == {exact}
    with patch('appEditors.appExcEditor.ExcCopyEditorTool'):
        assert set(CopyEditorExc(editor).selected_dia_list) == {exact}
    editor.on_tool_delete()
    assert exact not in editor.storage_dict
    assert editor.undo() and exact in editor.storage_dict
    row = next(row for row in range(2)
               if editor.ui.tools_table_exc.item(row, 1).data(QtCore.Qt.ItemDataRole.UserRole) == exact)
    editor.ui.tools_table_exc.setCurrentCell(row, 0)
    editor.ui.tools_table_exc.item(row, 1).setText('2.3456789')
    assert 2.3456789 in editor.storage_dict
    assert exact not in editor.storage_dict
    assert editor.undo() and exact in editor.storage_dict
    editor.ui.tools_table_exc.clearSelection()
    editor.ui.tools_table_exc.selectRow(row)
    other_storage = editor.storage_dict[diameter * 1.000001]
    AppExcEditor.on_slots_conversion(editor)
    assert set(editor.storage_dict) == {diameter * 1.000001, exact}
    assert editor.storage_dict[diameter * 1.000001] is other_storage
    editor.ui.tools_table_exc.setCurrentCell(row, 1)
    editor.ui.tools_table_exc.item(row, 1).setText('2.3456789')
    table = editor.ui.tools_table_exc

    def selected_diameters():
        return {table.item(index.row(), 1).data(QtCore.Qt.ItemDataRole.UserRole)
                for index in table.selectionModel().selectedRows()}

    AppExcEditor.on_tool_add(editor, tooldia=10)
    added_rows = selected_diameters()
    editor.sync_tool_table_selection()
    canvas_rows = selected_diameters()
    editor.app.delete_selection_shape = Mock()
    AppExcEditor.draw_selection_area_handler(editor, (-2, -2), (2, 2), True)
    assert editor.selected and editor.selected[0] in editor.storage_dict[2.3456789].get_objects()
    assert (added_rows, canvas_rows, selected_diameters()) == ({10}, {2.3456789}, {2.3456789})
    for diameter_to_delete in (2.3456789, [2.3456789]):
        editor.on_tool_delete(diameter_to_delete)
        assert 2.3456789 not in editor.storage_dict
        assert editor.undo() and 2.3456789 in editor.storage_dict
    editor.selected.append(other)
    editor.sync_tool_table_selection()
    assert selected_diameters() == {diameter * 1.000001, 2.3456789}
    assert qapp is not None


@pytest.mark.parametrize('array_type', ['circular'])
def test_large_copy_preview_is_box_but_commit_keeps_native_slots(array_type):
    editor, first, slot = editor_scene()
    editor.selected = [slot]
    editor.delete_utility_geometry = Mock()
    tool = CopyEditorExc.__new__(CopyEditorExc)
    tool.draw_app, tool.ui = editor, MagicMock()
    tool.origin, tool.destination, tool.points = (0, 0), (10, 0), [(0, 0)]
    tool.sel_limit, tool.selection_shape = 2, box(*slot.geo.bounds)
    tool.draw_cursor_data = Mock()
    for name, value in {'mode_radio': 'a', 'array_type_radio': array_type, 'array_size_entry': 3,
                        'rows': 2, 'columns': 2, 'axis_radio': 'X', 'pitch_entry': 2,
                        'linear_angle_spinner': 0, 'placement_radio': 'o', 'offsetx_entry': 3,
                        'offsety_entry': 4, 'spacing_rows': 1, 'spacing_columns': 1,
                        'array_dir_radio': 'CCW', 'angle_entry': 90}.items():
        getattr(tool.ui, name).get_value.return_value = value
    preview = tool.utility_geometry((10, 0))
    assert preview.geo.geom_type == 'Polygon'
    assert tool.make() != 'fail'
    copies = [shape for shape in editor.storage_dict[1].get_objects() if shape not in (first, slot)]
    assert len(copies) == (4 if array_type == '2D' else 3)
    assert all(native_feature_properties(shape, 1, 0)['type'] == 'slot' for shape in copies)
    if array_type == 'circular':
        from shapely import LineString
        from shapely.affinity import rotate, translate
        endpoints = native_feature_properties(slot, 1, 0)['endpoints']
        expected = translate(rotate(LineString(endpoints), 90, origin=(0, 0)), 0, 10)
        actual = native_feature_properties(copies[1], 1, 0)['endpoints']
        assert all(tuple(point) == pytest.approx(wanted)
                   for point, wanted in zip(actual, expected.coords))
    assert editor.undo()
    assert list(editor.storage_dict[1].get_objects()) == [first, slot]




@pytest.mark.parametrize('operation,value,reference,expected', [
    ('mirror_x', 1, 'origin', (5, -0.5)),
    ('scale', 2, (2, 3), (8, -2)),
])
def test_native_slot_transform_and_history(operation, value, reference, expected):
    editor, drill, slot = editor_scene()
    editor.selected = [slot]
    assert editor.transform_features(operation, value, reference) != 'fail'
    transformed = editor.selected[0]
    diameter = 2 if operation == 'scale' else 1
    record = native_feature_properties(transformed, diameter, 0)
    assert record['center'] == pytest.approx(expected)
    assert len(transformed.exc_slot) == 2
    assert drill in editor.storage_dict[1].get_objects()
    assert len(editor.history.undo_stack) == 1
    assert editor.history.revision == 1
    assert editor.undo()
    assert editor.selected == [slot]
    assert editor.redo()
    assert editor.selected == [transformed]


@pytest.mark.parametrize('operation,value,reference', [
    ('scale', 0, 'origin'), ('rotate', float('nan'), 'origin'), ('shear', 2, 'origin'),
])
def test_invalid_transform_is_atomic(operation, value, reference):
    editor, drill, slot = editor_scene()
    assert editor.transform_features(operation, value, reference) == 'fail'
    assert list(editor.storage_dict[1].get_objects()) == [drill, slot]
    assert not editor.history.undo_stack
    assert not editor.is_modified


@pytest.mark.parametrize('source_units', ['MM'])
@pytest.mark.parametrize('editor_units, export_format', [('MM', 'dec'), ('IN', 'ndec')])
@pytest.mark.parametrize('export_units', ['METRIC'])
@pytest.mark.parametrize('in_place', [True])
@pytest.mark.parametrize('string_ids', [True])
def test_source_loader_transform_save_native_unit_roundtrip(app, tmp_path, source_units, editor_units,
                                                           export_units, in_place, string_ids, export_format):
    header = 'METRIC' if source_units == 'MM' else 'INCH'
    text = 'M48\n%s,TZ\nT01C0.100\nT02C0.200\n%%\nT01\nX1.0Y2.0\nX3.0Y4.0\nT02\nX5.0Y6.0G85X7.0Y9.0\nM30\n' % header
    source = reparse_excellon(app, text, tmp_path, name='source')
    source.tools[1]['data'] = {'tag': 'drill'}
    source.tools[2]['data'] = {'tag': 'slot'}
    if string_ids:
        source.tools = {str(key): values for key, values in source.tools.items()}
    original = source.tools
    editor, _, _ = editor_scene()
    editor.app, editor.units, editor.decimals = app, editor_units, 8
    app.app_units = editor_units    # the editor takes its units from the application when it loads
    editor.storage_dict.clear()
    editor.selected.clear()
    editor.dec_format = lambda value: round(value, 8)
    editor.deactivate, editor.activate, editor.set_editor_ui = Mock(), Mock(), Mock()
    editor.on_tool_edit = Mock()
    editor._save_excellon_in_place = MethodType(AppExcEditor._save_excellon_in_place, editor)
    editor.ui = MagicMock()
    AppExcEditor.edit_fcexcellon(editor, source)
    editor.edited_obj_name = 'source'
    factor = 1 if source_units == editor_units else (25.4 if editor_units == 'MM' else 1 / 25.4)
    diameters = sorted(editor.storage_dict)
    first, untouched = list(editor.storage_dict[diameters[0]].get_objects())
    slot = next(iter(editor.storage_dict[diameters[1]].get_objects()))
    assert first.geo.centroid.x == pytest.approx(factor)
    editor.selected = [first, slot]
    editor.transform_features('rotate', 37)
    editor.transform_features('scale', 1.5)
    expected = [(diameter, native_feature_properties(shape, diameter, 0))
                for diameter, storage in editor.storage_dict.items() for shape in storage.get_objects()]
    assert untouched.geo.centroid.x == pytest.approx(3 * factor)
    assert source.tools is original and source.units == source_units
    editor.worker_result = Mock()
    editor.deactivate = Mock(side_effect=editor.history.reset)
    app.worker_task = Mock()
    app.app_units = editor_units
    app.f_handlers = appIO(app)
    app.options['excellon_exp_units'] = export_units
    app.options['excellon_exp_decimals'] = 8
    app.options['excellon_exp_format'] = export_format
    app.main_thread = QtCore.QThread.currentThread()
    created = []

    def create(kind, name, initialize):
        obj = type(source)(name, app=app)
        initialize(obj, app)
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    assert AppExcEditor.update_fcexcellon(editor, source, in_place=in_place) == 'pending'
    app.worker_task.emit.call_args.args[0]['fcn']()
    assert 'error' not in editor.worker_result.emit.call_args.args[0], app.log.error.call_args_list
    editor.history.finish(editor.worker_result.emit.call_args.args[0])
    saved = source if in_place else created[0]
    assert saved.units == editor_units
    again = reparse_excellon(app, saved.source_file, tmp_path)
    output_factor = 1 if again.units == editor_units else (25.4 if editor_units == 'MM' else 1 / 25.4)
    actual = []
    actual_slots = []
    for values in again.tools.values():
        diameter = values['tooldia'] * output_factor
        actual.extend((diameter, (point.x * output_factor, point.y * output_factor))
                      for point in values.get('drills', []))
        for start, stop in values.get('slots', []):
            actual_slots.append(((start.x * output_factor, start.y * output_factor),
                                 (stop.x * output_factor, stop.y * output_factor)))
            actual.append((diameter, ((start.x + stop.x) / 2 * output_factor,
                                      (start.y + stop.y) / 2 * output_factor)))
    assert len(actual) == len(expected)
    for diameter, record in expected:
        assert any(abs(diameter - d) < 1e-6 and center == pytest.approx(record['center'], abs=1e-6)
                   for d, center in actual)
        if record['type'] == 'slot':
            assert any(all(point == pytest.approx(wanted, abs=1e-6)
                           for point, wanted in zip(sorted(endpoints), sorted(record['endpoints'])))
                       for endpoints in actual_slots)
    assert {values['data']['tag'] for values in saved.tools.values()} == {'drill', 'slot'}
    if not in_place:
        assert source.tools is original and source.units == source_units


def test_transform_order_merge_and_rollback():
    editor, first, slot = editor_scene()
    editor.transform_features('rotate', 45)
    assert list(editor.storage_dict[1].get_objects()) == [editor.selected[0], slot]
    assert editor.undo()
    target = editor.make_storage()
    other = drill(10)
    target.insert_many([other])
    editor.storage_dict[2] = target
    editor.points_edit[2], editor.slot_points_edit[2] = [(10, 0)], []
    editor.olddia_newdia[2] = 2
    editor.tool2tooldia['second'] = 2
    editor.transform_features('scale', 2)
    assert list(editor.storage_dict) == [1, 2]
    assert list(target.get_objects()) == [other, editor.selected[0]]
    assert editor.undo()
    editor.build_ui.side_effect = RuntimeError('render failed')
    with pytest.raises(RuntimeError, match='render failed'):
        editor.transform_features('rotate', 17)
    assert list(editor.storage_dict[1].get_objects()) == [first, slot]
    assert editor.selected == [first]
