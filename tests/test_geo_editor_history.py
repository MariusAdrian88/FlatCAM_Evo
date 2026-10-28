"""Committed editor operations, not previews, own history entries."""
from contextlib import nullcontext
from threading import Event, Thread, Timer
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import unary_union

from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, DrawToolUtilityShape, FCMove, FCExplode, FCEraser, FCShapeTool
from appGUI.MainGUI import MainGUI
from appGUI.GUIElements import FCTree
from appEditors.geo_plugins.GeoCirclePlugin import CircleEditorTool
from appEditors.geo_plugins.GeoRectanglePlugin import RectangleEditorTool
from appEditors.geo_plugins.GeoPaintPlugin import PaintOptionsTool
from appMain import App
from appHandlers.appHistory import DEFAULT_STEPS, HistoryManager
from appHandlers.appIO import appIO
from appHandlers.appSignalConnector import AppSignalConnector
from tests.test_geo_editor_save import geometry_object
from tests.test_geo_editor_delete_plot import make_editor


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def editor_with_history(shapes=(), selected=()):
    editor = make_editor(shapes, selected)
    for name in ('add_shape', '_collect_shape', 'delete_shape', 'undo', 'redo', 'reset_history',
                 'history_operation', 'replace_shapes', 'submit_worker', 'on_shape_complete'):
        setattr(editor, name, getattr(AppGeoEditor, name).__get__(editor))
    editor.build_ui_sig = SimpleNamespace(emit=Mock())
    editor.tool_shape = SimpleNamespace(clear=Mock(), redraw=Mock())
    editor.app.inform = SimpleNamespace(emit=Mock())
    editor.app.proc_container = SimpleNamespace(new=lambda *a: nullcontext())
    editor.app.worker_task = SimpleNamespace(emit=Mock())
    editor.reset_history()
    return editor


def run_worker(editor):
    job = editor.app.worker_task.emit.call_args.args[0]
    return job['fcn'](*job['params'])


def interactive_move_editor():
    original = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([original], [original])
    editor.canvas = SimpleNamespace(translate_coords=lambda pos: pos)
    editor.snap = lambda x, y: (x, y)
    editor.x, editor.y = 3, 2
    editor.in_action = True
    editor.select_tool = Mock()
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.app.grid_status = lambda: False
    editor.app.ui = SimpleNamespace(rel_position_label=Mock(), update_location_labels=Mock(),
                                    popMenu=SimpleNamespace(mouse_is_panning=False))
    editor.app.plotcanvas = SimpleNamespace(on_update_text_hud=Mock())
    editor.app.jump_signal = Mock()
    editor.app.mouse_pos, editor.app.on_jump_to = (3, 2), Mock()
    editor.app.geo_editor = editor
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    tool = FCMove.__new__(FCMove)
    tool.draw_app, tool.storage, tool.name = editor, editor.storage, 'move'
    tool.origin, tool.destination = (0, 0), (3, 2)
    tool.has_selection, tool.complete = True, False
    tool.geometry, tool.points = [], [(0, 0)]
    tool.draw_cursor_data = Mock()
    tool.move_tool = SimpleNamespace(length=0, width=0)
    editor.active_tool = tool
    return editor, tool, original


def assert_no_success(editor):
    assert not any('[success]' in str(call) for call in editor.app.inform.emit.call_args_list)


def test_busy_move_canvas_preserves_operation_then_retry_commits():
    editor, tool, original = interactive_move_editor()
    editor.submit_worker(Mock(), [])
    event = SimpleNamespace(pos=(3, 2), button=1)
    assert AppGeoEditor.on_canvas_click(editor, event) == 'fail'
    assert tool.origin == (0, 0) and not tool.complete and tool.geometry == []
    assert tool.points == [(0, 0)]
    assert list(editor.storage.get_objects()) == [original]
    tool.draw_cursor_data.assert_not_called()
    editor.select_tool.assert_not_called()
    assert_no_success(editor)
    run_worker(editor)
    assert AppGeoEditor.on_canvas_click(editor, event) != 'fail'
    assert editor.selected[0].geo.bounds == (3, 2, 4, 2)
    assert tool.origin is None
    editor.select_tool.assert_called_once_with('select')
    assert editor.undo()
    assert list(editor.storage.get_objects()) == [original]










def test_add_delete_redo_invalidation_and_metadata():
    editor = editor_with_history()
    shape = DrawToolShape(LineString([(0, 0), (1, 0)]))
    shape.data['custom'] = {'name': 'original'}
    editor.add_shape(shape)
    assert editor.undo()
    assert not list(editor.storage.get_objects())
    assert editor.redo()
    editor.selected = [shape]
    editor.delete_shape(shape)
    assert editor.undo()
    assert editor.selected == [shape]
    assert shape.data['custom'] == {'name': 'original'}
    assert editor.storage.nearest((0, 0))[1] is shape
    editor.add_shape(DrawToolShape(box(3, 3, 4, 4)))
    assert not editor.redo()


def test_editor_and_application_histories_are_independent():
    editor = editor_with_history()
    app = editor.app
    # the app history checkpoints the object (object_state) before undo, so the stand-in needs its state attributes
    obj = SimpleNamespace(history_uid='project-object', name='Renamed', kind='geometry',
                          ser_attrs=['obj_options'], obj_options={'name': 'Renamed'})
    collection = SimpleNamespace(get_list=lambda: [obj],
                                 rename_object=lambda target, name: setattr(target, 'name', name))
    app.call_source, app.headless, app.projects = 'app', False, None
    app.history_changed = SimpleNamespace(emit=Mock())
    app.history = HistoryManager(app, collection)
    app.history.record_rename(obj, 'Original', 'Renamed')
    project_step = app.history.undo_stack[0]
    assert editor._history_undo is not app.history.undo_stack
    assert editor._history_redo is not app.history.redo_stack

    # A direct subtool commit and a worker subtool commit use the same editor stack.
    editor.active_tool = SimpleNamespace(name='circle')
    editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    editor.active_tool = SimpleNamespace(name='buffer')
    editor.submit_worker(lambda: editor.add_shape(DrawToolShape(box(2, 0, 3, 1))), [])
    run_worker(editor)
    assert AppGeoEditor.history_labels(editor) == ['Circle', 'buffer']
    assert app.history.undo_stack == [project_step]
    assert not app.history.redo_stack

    assert editor.undo()
    editor_undo, editor_redo = list(editor._history_undo), list(editor._history_redo)
    app.call_source = 'geo_editor'
    assert not app.history.undo()
    assert app.history.undo_stack == [project_step]
    app.call_source = 'app'
    assert app.history.undo()
    assert obj.name == 'Original'
    assert editor._history_undo == editor_undo
    assert editor._history_redo == editor_redo
    assert app.history.redo()
    assert obj.name == 'Renamed'
    assert editor._history_undo == editor_undo
    assert editor._history_redo == editor_redo

    editor.reset_history()
    assert app.history.undo_stack == [project_step]
    assert not app.history.redo_stack
    editor.add_shape(DrawToolShape(box(4, 0, 5, 1)))
    editor_step = editor._history_undo[0]
    app.history.clear()
    assert editor._history_undo == [editor_step]
    assert editor.undo()




@pytest.mark.parametrize('route', ['redo', 'jump'])
def test_changed_depth_is_enforced_on_redo(route):
    editor = editor_with_history()
    for index in range(4):
        editor.add_shape(DrawToolShape(box(index, 0, index + 1, 1)))
    assert AppGeoEditor.history_go_to(editor, 0)
    editor.app.options['global_history_steps'] = 2
    if route == 'jump':
        assert AppGeoEditor.history_go_to(editor, 4)
    else:
        for _ in range(4):
            assert editor.redo()
            assert len(editor._history_undo) <= 2
    assert len(editor._history_undo) == 2
    assert len(list(editor.storage.get_objects())) == 4


def test_changed_memory_limit_discards_farthest_redo_first():
    editor = editor_with_history()
    for index in range(3):
        editor.add_shape(DrawToolShape(LineString([(i, index) for i in range(23000)])))
    assert AppGeoEditor.history_go_to(editor, 0)
    editor.app.options['global_history_memory_mb'] = 1
    assert editor.redo()
    assert len(editor._history_redo) == 1
    assert editor.redo()
    assert not editor.redo()
    assert len(list(editor.storage.get_objects())) == 2


def test_common_history_uses_application_limits_and_navigation():
    editor = editor_with_history()
    editor.app.options['global_history_steps'] = 3
    assert AppGeoEditor.history_limits(editor) == HistoryManager(editor.app, None).limits()
    for index in range(5):
        with editor.history_operation(label='Shape %d' % index):
            editor.add_shape(DrawToolShape(box(index, 0, index + 1, 1)))
    assert len(editor._history_undo) == 3
    assert AppGeoEditor.history_labels(editor) == ['Shape 2', 'Shape 3', 'Shape 4']
    assert AppGeoEditor.history_go_to(editor, 0)
    assert len(list(editor.storage.get_objects())) == 2
    assert AppGeoEditor.history_go_to(editor, 2)
    assert len(list(editor.storage.get_objects())) == 4
    editor.add_shape(DrawToolShape(box(9, 0, 10, 1)))
    assert not editor._history_redo
    assert len(editor._history_undo) == 3


def test_history_navigation_rejects_busy_and_nested_edits_share_entry():
    editor = editor_with_history()
    with editor.history_operation(label='Grouped'):
        editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
        editor.add_shape(DrawToolShape(box(2, 0, 3, 1)))
    assert AppGeoEditor.history_labels(editor) == ['Grouped']
    editor.submit_worker(Mock(), [])
    assert not AppGeoEditor.history_go_to(editor, 0)
    assert len(list(editor.storage.get_objects())) == 2
    run_worker(editor)
    assert AppGeoEditor.history_go_to(editor, 0)
    assert not list(editor.storage.get_objects())


def test_history_widgets_show_chronological_states_and_next_actions():
    editor = editor_with_history()
    editor.app.call_source = 'geo_editor'
    editor.app.ui = SimpleNamespace(on_history_changed=Mock())
    editor.ui = SimpleNamespace()
    editor.ui.history_positions = QtWidgets.QComboBox()
    for label in ('Circle', 'Buffer'):
        with editor.history_operation(label=label):
            editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    editor.undo()
    AppGeoEditor.refresh_history(editor)
    combo = editor.ui.history_positions
    assert [combo.itemText(i) for i in range(combo.count())] == ['Initial state', 'Circle', 'Buffer']
    assert combo.currentIndex() == 1
    editor.app.ui.on_history_changed.assert_called_with('Circle', 'Buffer')














def test_move_completion_restores_metadata_selection_and_index():
    original = DrawToolShape(LineString([(0, 0), (2, 0)]))
    original.data['custom'] = {'label': 'track'}
    editor = editor_with_history([original], [original])
    tool = FCMove.__new__(FCMove)
    tool.draw_app = editor
    tool.origin, tool.destination = (0, 0), (4, 3)
    tool.draw_cursor_data = Mock()
    editor.app.jump_signal = SimpleNamespace(disconnect=Mock())
    editor.app.options['geometry_editor_milling_type'] = 'cl'
    editor.active_tool = tool
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    tool.make()
    assert list(editor.storage.get_objects()) == [original]
    assert not editor.undo()
    AppGeoEditor.on_shape_complete(editor)
    moved = tool.geometry[0]
    assert moved.data['custom'] == original.data['custom']
    assert editor.selected == [moved]
    assert editor.storage.nearest((4, 3))[1] is moved
    assert editor.undo()
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == [moved]
    assert editor.selected == [moved]


@pytest.mark.parametrize('operation', ['union', 'subtract', 'cutpath'])
def test_boolean_variants_undo_and_redo_as_one_operation(operation):
    a, b = DrawToolShape(box(0, 0, 2, 2).exterior), DrawToolShape(box(1, 1, 3, 3).exterior)
    editor = editor_with_history([a, b], [a, b])
    editor.flatten = lambda geo: AppGeoEditor.flatten(editor, geo)
    getattr(AppGeoEditor, operation)(editor)
    run_worker(editor)
    result = list(editor.storage.get_objects())
    result_selection = list(editor.selected)
    assert editor.undo()
    assert set(editor.storage.get_objects()) == {a, b}
    assert editor.selected == [a, b]
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == result
    assert editor.selected == result_selection


@pytest.mark.parametrize('operation', ['subtract', 'cutpath'])
@pytest.mark.parametrize('target_kind', ['ring', 'open_path'])
@pytest.mark.parametrize('cutter', [box(1, 1, 3, 3)])
def test_subtraction_uses_areas_while_cutpath_uses_outlines(operation, target_kind, cutter):
    polygon = box(0, 0, 2, 2)
    geometry = {
        'ring': polygon.exterior,
        'closed_path': LineString(polygon.exterior.coords),
        'polygon': polygon,
        'open_path': LineString([(0, 1), (2, 1)])
    }[target_kind]
    target, tool = DrawToolShape(geometry), DrawToolShape(cutter.exterior)
    editor = editor_with_history([target, tool], [target, tool])
    editor.flatten = lambda geo: AppGeoEditor.flatten(editor, geo)
    getattr(AppGeoEditor, operation)(editor)
    run_worker(editor)
    shapes = list(editor.storage.get_objects())
    assert (tool in shapes) == (operation != 'subtract')
    results = [shape.geo for shape in shapes if shape is not tool]
    assert results
    expected_target = geometry if target_kind == 'open_path' else \
        polygon.exterior if operation == 'cutpath' else polygon
    assert unary_union(results).equals(expected_target.difference(cutter))




@pytest.mark.parametrize('transform,args,kwargs', [
    ('skew', (90, 0, (0, 0)), {}),
])
def test_invalid_transform_preserves_selection_storage_and_history(transform, args, kwargs):
    shape = DrawToolShape(box(0, 0, 2, 2))
    editor = editor_with_history([shape], [shape])
    with pytest.raises(ValueError):
        AppGeoEditor.transform_shapes(editor, transform, *args, **kwargs)
    assert list(editor.storage.get_objects()) == [shape]
    assert editor.selected == [shape]
    assert not editor._history_undo
    assert editor.storage.nearest((0, 0))[1] is shape


@pytest.mark.parametrize('transform,args,kwargs,expected', [
    ('scale', (-2, 1), {}, (-4, 0, 0, 2)),
    ('buffer', (-0.25,), {'join': 2}, (0.25, 0.25, 1.75, 1.75)),
])
def test_transform_reflection_and_erosion_preserve_metadata_and_replay(transform, args, kwargs, expected):
    shape = DrawToolShape(box(0, 0, 2, 2))
    shape.data['custom'] = {'name': 'source'}
    editor = editor_with_history([shape], [shape])
    AppGeoEditor.transform_shapes(editor, transform, *args, **kwargs)
    result = editor.selected[0]
    assert result.geo.is_valid and not result.geo.is_empty
    assert result.geo.bounds == expected
    assert result.data['bounds'] == result.bounds()
    assert result.data['custom'] == shape.data['custom']
    assert editor.storage.nearest(expected[:2])[1] is result
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.selected == [shape]
    assert shape.geo.bounds == (0, 0, 2, 2)
    assert list(editor.storage.get_objects()) == [shape]
    assert editor.redo()
    assert editor.selected[0].geo.bounds == expected


@pytest.mark.filterwarnings('error::RuntimeWarning')
def test_affine_overflow_keeps_all_originals_atomically():
    shapes = [DrawToolShape(LineString([(0, 0), (1, 1)])),
              DrawToolShape(LineString([(1e308, 0), (1e308, 1)]))]
    editor = editor_with_history(shapes, shapes)
    with pytest.raises(ValueError):
        AppGeoEditor.transform_shapes(editor, 'scale', 2, 2)
    assert list(editor.storage.get_objects()) == shapes
    assert editor.selected == shapes
    assert not editor._history_undo


def test_transform_and_replay_plot_only_changed_shapes():
    shapes = [DrawToolShape(LineString([(x, 0), (x, 1)])) for x in range(100)]
    editor = editor_with_history(shapes, [shapes[0]])
    editor.plot_all = lambda: AppGeoEditor.plot_all(editor)
    editor.plot_changes = lambda: AppGeoEditor.plot_changes(editor)
    editor.plot_all()
    editor.shapes.calls.clear()
    editor.sel_shapes.calls.clear()
    AppGeoEditor.transform_shapes(editor, 'offset', (200, 0))
    assert len(editor._history_undo) == 1
    assert editor.undo()
    assert editor.redo()
    assert not any(call[0] == 'clear' for call in editor.shapes.calls + editor.sel_shapes.calls)
    assert len([call for call in editor.shapes.calls if call[0] == 'add']) <= 3
    assert editor.build_ui_sig.emit.call_count == 3


def test_clear_resets_history_and_rejects_old_worker():
    editor = editor_with_history()
    editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    called = Mock()
    editor.submit_worker(called, [])
    AppGeoEditor.clear(editor)
    run_worker(editor)
    called.assert_not_called()
    assert not editor.undo()
    assert not editor.redo()


def test_eraser_restores_replacement_selection_on_redo():
    original = DrawToolShape(box(0, 0, 5, 5))
    editor = editor_with_history([original], [original])
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.app.jump_signal = SimpleNamespace(disconnect=Mock())
    tool = FCEraser.__new__(FCEraser)
    tool.draw_app = editor
    tool.destination = (0, 0)
    tool.utility_geometry = lambda **kwargs: SimpleNamespace(geo=[box(-1, -1, 2, 6)])
    tool.make()
    replacement = list(editor.selected)
    assert replacement and replacement != [original]
    assert editor.undo()
    assert editor.selected == [original]
    assert editor.redo()
    assert editor.selected == replacement


def test_explode_is_one_delete_plus_add_operation():
    originals = [DrawToolShape(box(x, 0, x + 1, 1).exterior) for x in (0, 3)]
    originals[0].data['custom'] = 'first ring'
    editor = editor_with_history(originals, originals)
    editor.app.options['geometry_editor_milling_type'] = 'cl'
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    tool = FCExplode.__new__(FCExplode)
    tool.draw_app = editor
    editor.active_tool = tool
    tool.make()
    segments = list(editor.storage.get_objects())
    assert len(segments) == 8
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(originals)
    assert editor.selected == originals
    assert originals[0].data['custom'] == 'first ring'
    assert not editor.undo()
    assert editor.redo()
    assert set(editor.storage.get_objects()) == set(segments)
    assert editor.selected == segments


def test_explode_replaces_every_selected_shape_by_its_lines():
    # a ring before a MultiLineString (its segments were thrown away), an open path and a polygon (both were
    # removed with nothing in their place) and a single segment (kept as it is)
    segment = DrawToolShape(LineString([(9, 0), (9, 1)]))
    originals = [DrawToolShape(box(0, 0, 1, 1).exterior),
                 DrawToolShape(MultiLineString([[(3, 0), (4, 0)], [(3, 1), (4, 1)]])),
                 DrawToolShape(LineString([(5, 0), (6, 0), (6, 1)])),
                 DrawToolShape(Polygon(box(10, 0, 14, 4).exterior.coords, [box(11, 1, 12, 2).exterior.coords])),
                 segment]
    editor = editor_with_history(originals, originals)
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    tool = FCExplode.__new__(FCExplode)
    tool.draw_app = editor
    editor.active_tool = tool
    tool.make()
    shapes = list(editor.storage.get_objects())
    assert segment in shapes and not set(originals[:-1]) & set(shapes)
    assert len(shapes) == 4 + 2 + 2 + 8 + 1
    assert unary_union([s.geo for s in shapes]).equals(unary_union([s.geo.boundary if s.geo.geom_type == 'Polygon'
                                                                    else s.geo for s in originals]))
    assert editor.undo()
    assert set(editor.storage.get_objects()) == set(originals)


def test_replay_restores_real_tree_rows_and_selection():
    shape = DrawToolShape(box(0, 0, 1, 1).exterior)
    editor = editor_with_history([shape], [shape])
    tree = FCTree(columns=3, extended_sel=True)
    editor.ui = SimpleNamespace(tw=tree, geo_parent=tree.invisibleRootItem(), geo_font=QtGui.QFont())
    editor.shapes_by_tree_id = lambda: AppGeoEditor.shapes_by_tree_id(editor)
    editor.sync_tree_selection = lambda: AppGeoEditor.sync_tree_selection(editor)
    editor.build_ui_sig.emit = lambda: AppGeoEditor.build_ui(editor)
    editor.build_ui_sig.emit()
    assert [item.text(0) for item in tree.selectedItems()] == [str(id(shape))]
    editor.delete_shape(shape)
    editor.build_ui_sig.emit()
    assert tree.topLevelItemCount() == 0
    assert editor.undo()
    assert tree.topLevelItemCount() == 1
    assert [item.text(0) for item in tree.selectedItems()] == [str(id(shape))]
    assert editor.redo()
    assert tree.topLevelItemCount() == 0
    assert not tree.selectedItems()




@pytest.mark.parametrize('tool_class', [CircleEditorTool])
@pytest.mark.parametrize('valid', [True, False])
def test_numeric_apply_replaces_geometry_and_selection_in_one_step(tool_class, valid):
    original = DrawToolShape(box(0, 0, 1, 1).exterior)
    original.data['custom'] = {'label': 'original'}
    editor = editor_with_history([original], [original])
    editor.delete_selected = lambda: AppGeoEditor.delete_selected(editor)
    editor.storage.replace = Mock(wraps=editor.storage.replace)
    values = dict(x_entry=5, y_entry=6, radius_x_entry=2 if valid else 0, radius_y_entry=2, angle_entry=0,
                  anchor_radio='c', corner_radio='s', radius_entry=0, length_entry=4 if valid else 0, width_entry=2)
    ui = SimpleNamespace(**{name: SimpleNamespace(get_value=lambda v=value: v)
                            for name, value in values.items()})
    ui.radius_link_btn = SimpleNamespace(isChecked=lambda: True)
    tool = SimpleNamespace(draw_app=editor, app=editor.app, ui=ui, mode='change')
    tool.on_add = lambda: tool_class.on_add(tool)
    tool_class.on_execute(tool)
    replacements = list(editor.storage.get_objects())
    if not valid:
        assert replacements == [original]
        assert editor.selected == [original]
        editor.storage.replace.assert_not_called()
        assert not editor.undo()
        return
    assert replacements and original not in replacements
    editor.storage.replace.assert_called_once()
    assert editor.selected == replacements
    assert editor.undo()
    assert list(editor.storage.get_objects()) == [original]
    assert editor.selected == [original]
    assert original.data['custom'] == {'label': 'original'}
    assert not editor.undo()
    assert editor.redo()
    assert list(editor.storage.get_objects()) == replacements
    assert editor.selected == replacements






def test_project_restore_honors_rejected_editor_exit():
    app = MagicMock()
    handler = appIO(app)
    handler.on_file_new_project = Mock(return_value='fail')
    assert handler._restore_project_handler({'objs': [], 'options': {}},
                                            'unused.FlatPrj', False, False, False, False) is False
    app.options.load_project.assert_not_called()




def test_accepted_deactivation_invalidates_queued_job_without_later_mutation():
    original = DrawToolShape(box(0, 0, 1, 1))
    editor = editor_with_history([original], [original])
    editor.app.ui, editor.app.plotcanvas = Mock(), Mock()
    editor.app.history = Mock()
    editor.ui, editor.fcgeometry, editor.item_selected, editor.clear_tree_sig = Mock(), Mock(), Mock(), Mock()
    editor.toolbar_old_state = False
    editor.disconnect_canvas_event_handlers = Mock()
    editor.make_storage = AppGeoEditor.make_storage
    editor.clear = lambda: AppGeoEditor.clear(editor)
    editor.on_tree_geo_click, editor.on_tree_selection = Mock(), Mock()
    called = Mock(side_effect=lambda: editor.add_shape(DrawToolShape(box(2, 2, 3, 3))))
    editor.submit_worker(called, [])
    job = editor.app.worker_task.emit.call_args.args[0]
    assert AppGeoEditor.deactivate(editor) != 'fail'
    assert not list(editor.storage.get_objects())
    job['fcn'](*job['params'])
    called.assert_not_called()
    assert not list(editor.storage.get_objects())
    assert not editor.undo()




def test_failed_operation_rolls_back_without_clearing_redo():
    editor = editor_with_history()
    editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    editor.undo()
    with pytest.raises(ValueError, match='cancel operation'):
        with editor.history_operation():
            editor.add_shape(DrawToolShape(box(2, 2, 3, 3)))
            raise ValueError('cancel operation')
    assert not list(editor.storage.get_objects())
    assert not editor.undo()
    assert editor.redo()




def test_worker_failure_releases_selection_guard_and_rolls_back():
    editor = editor_with_history()
    editor.interdict_selection = False

    def fail():
        assert editor.interdict_selection
        editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
        raise ValueError('failed worker')

    editor.submit_worker(fail, [])
    with pytest.raises(ValueError, match='failed worker'):
        run_worker(editor)
    assert not editor.interdict_selection
    assert not list(editor.storage.get_objects())
    assert not editor.undo()






def test_boolean_worker_is_one_step_and_stale_worker_is_rejected():
    a, b = DrawToolShape(box(0, 0, 2, 2)), DrawToolShape(box(1, 1, 3, 3))
    editor = editor_with_history([a, b], [a, b])
    AppGeoEditor.union(editor)
    job = editor.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    assert len(list(editor.storage.get_objects())) == 1
    assert editor.undo()
    assert set(editor.storage.get_objects()) == {a, b}
    assert editor.selected == [a, b]
    assert not editor.undo()
    AppGeoEditor.union(editor)
    job = editor.app.worker_task.emit.call_args.args[0]
    editor.reset_history()
    job['fcn'](*job['params'])
    assert set(editor.storage.get_objects()) == {a, b}
    assert not editor.undo()


def test_preview_and_load_are_not_history_and_reset_clears_redo():
    editor = editor_with_history()
    editor.add_shape(DrawToolUtilityShape(box(0, 0, 1, 1)))
    AppGeoEditor.delete_utility_geometry(editor)
    assert not editor.undo()
    with editor.history_operation(record=False):
        editor.add_shape(DrawToolShape(box(0, 0, 1, 1)))
    assert not editor.undo()
    editor.add_shape(DrawToolShape(box(2, 2, 3, 3)))
    assert editor.undo()
    editor.reset_history()
    assert not editor.redo()
