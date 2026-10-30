"""Gerber-local history records affected records, not board snapshots."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import Point, LineString, MultiPolygon, Polygon, box
from shapely.affinity import translate
from shapely.ops import unary_union

from appEditors.appGerberEditor import (AppGerberEditor, CopyEditorGrb, MoveEditorGrb, EraserEditorGrb,
                                       ImportEditorGrb, PoligonizeEditorGrb, TrackEditorGrb)
from appEditors.grb_plugins.GrbBufferPlugin import BufferEditorTool
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
from appGUI.MainGUI import MainGUI
from appHandlers.appSignalConnector import AppSignalConnector
from appMain import App
from appParsers.ParseGerber import Gerber
from test_gerber_editor_pick import make_editor, aperture, pad, trace
import test_gerber_editor_pick as pick_cases
from test_editor_save_modes import gerber_stand_in, gerber_storage
from test_gerber_editor_size_simplify import simplify_tool, run_job
from test_gerber_track_bend import editor_for, start, release, track
from test_grb_editor_transform import Host
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR, export_gerber_text  # noqa: F401,E402


def scene():
    first = DrawToolShape({'solid': box(0, 0, 1, 1), 'follow': Point(0.5, 0.5)})
    second = DrawToolShape({'solid': box(10, 0, 11, 1), 'follow': Point(10.5, 0.5)})
    editor = make_editor({10: aperture([first]), 11: aperture([second])})
    editor.history.reset()
    return editor, first, second


def test_delete_is_one_step_and_restores_identity_selection_and_order():
    editor, first, second = scene()
    editor.selected = [first]
    editor.delete_selected()
    assert len(editor.history.undo_stack) == 1
    assert editor.storage_dict[10]['geometry'] == []
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.selected == [first]
    assert list(editor.storage_dict) == [10, 11]
    assert not editor.is_modified
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == []
    assert editor.storage_dict[11]['geometry'] == [second]


def test_nested_mutations_group_and_capture_immutable_geometry_values():
    editor, first, second = scene()
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    old = dict(first.geo)
    with editor.history.operation('Move then copy') as accepted:
        assert accepted
        first.geo['solid'] = translate(first.geo['solid'], xoff=2)
        first.geo['follow'] = translate(first.geo['follow'], xoff=2)
        editor.touch_geometry()
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
        added = DrawToolShape({'solid': box(5, 5, 6, 6)})
        editor.add_gerber_shape(added, editor.storage_dict[10]['geometry'])
    assert len(editor.history.undo_stack) == 1
    assert editor.history.undo_stack[0]['label'] == 'Move then copy'
    assert editor.undo()
    assert first.geo == old
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == [first, added]
    assert first.geo['follow'].equals(Point(2.5, 0.5))
    assert first.geo['solid'].bounds == pytest.approx((1.9, -0.1, 3.1, 1.1))


def test_transaction_rolls_back_geometry_apertures_flags_and_selection_on_exception():
    editor, first, second = scene()
    editor.selected = [first]
    old = dict(first.geo)
    with pytest.raises(ValueError):
        with editor.history.operation('Broken'):
            first.geo['solid'] = box(1, 1, 2, 2)
            editor.storage_dict[10]['geometry'].clear()
            editor.storage_dict[99] = {'type': 'REG', 'size': 0, 'geometry': []}
            editor.loaded_as_regions = True
            editor.selected = []
            raise ValueError('computation failed')
    assert first.geo == old
    assert editor.storage_dict[10]['geometry'] == [first]
    assert list(editor.storage_dict) == [10, 11]
    assert editor.selected == [first]
    assert not getattr(editor, 'loaded_as_regions', False)
    assert editor.history.undo_stack == []


def test_branching_limits_and_non_mutating_selection():
    editor, first, second = scene()
    editor.app.options['global_history_steps'] = 2
    for index in range(3):
        editor.add_gerber_shape(DrawToolShape({'solid': box(index, 3, index + 1, 4)}),
                                editor.storage_dict[10]['geometry'])
    assert len(editor.history.undo_stack) == 2
    assert editor.history.baseline_evicted
    assert editor.undo()
    editor.selected = [second]
    editor.plot_selection()
    assert len(editor.history.redo_stack) == 1
    editor.delete_selected()
    assert editor.history.redo_stack == []
    assert not editor.redo()


def test_aperture_delete_and_add_restore_metadata_as_one_group():
    editor, first, second = scene()
    editor.last_aperture_selected = 10
    with editor.history.operation('Replace aperture'):
        editor.on_aperture_delete(ap_code=[10])
        editor.ui.apsize_entry.set_value(0.5)
        editor.on_aperture_add(12)
    assert 10 not in editor.storage_dict
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert 12 not in editor.storage_dict
    assert editor.last_aperture_selected == 10


def snapshot(editor):
    return {key: ({name: value for name, value in aperture.items() if name != 'geometry'},
                  [(shape, dict(shape.geo)) for shape in aperture.get('geometry', ())])
            for key, aperture in editor.storage_dict.items()}


@pytest.mark.parametrize('family', ['pad', 'region', 'move', 'erase', 'buffer'])
def test_mutation_family_round_trip_is_one_step(family):
    first, second = pad(0, 0, 1), pad(10, 0, 1)
    editor = make_editor({10: {'type': 'C', 'size': 2.0, 'geometry': [first, second]}})
    editor.gerber_obj = SimpleNamespace(obj_options={'name': 'board'})
    editor.history.reset()
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    editor.is_modified = False
    before = snapshot(editor)
    before_regions = getattr(editor, 'loaded_as_regions', False)
    if family in ('pad', 'array', 'region', 'disc', 'semidisc'):
        geometry = pad(4, 4, 1)
        editor.active_tool = SimpleNamespace(name=family, geometry=geometry)
        shapes = [geometry, pad(6, 4, 1)] if family == 'array' else geometry
        editor.on_grb_shape_complete(editor.storage_dict[10]['geometry'], specific_shape=shapes)
    elif family == 'track':
        tool = TrackEditorGrb.__new__(TrackEditorGrb)
        tool.draw_app, tool.app = editor, editor.app
        tool.points, tool.temp_points, tool.click_shapes = [], [(0, 0), (3, 0), (3, 3)], []
        tool.buf_val, tool.quad_segs = 1, 16
        tool.track_tool = SimpleNamespace(length=1)
        editor.snap_x, editor.snap_y = 3, 3
        tool.ui = SimpleNamespace(x_entry=MagicMock(), y_entry=MagicMock())
        tool.click((3, 3))
    elif family in ('move', 'copy'):
        tool = (MoveEditorGrb if family == 'move' else CopyEditorGrb)(editor)
        editor.active_tool = tool
        tool.origin, tool.destination = (0, 0), (0, 5)
        tool.make()
    elif family == 'erase':
        editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [trace(0, 0, 10, 0, 0.4)]}})
        tool = pick_cases.TestEraserMake().eraser(editor, 5, 0)
        before = snapshot(editor)
        tool.make()
    elif family == 'polygonize':
        tool = PoligonizeEditorGrb(editor)
        tool.make()
    elif family == 'mark_delete':
        editor.geo_to_delete = [first]
        editor.delete_marked_polygons()
    elif family == 'buffer':
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
        run_job(editor)
    elif family == 'scale':
        editor.ui.scale_factor_entry.set_value(1.2)
        editor.on_scale()
    elif family == 'transform':
        Host(editor).on_offset('X', 2)
    elif family == 'simplify':
        simplify_tool(editor).on_simplification_click()
        run_job(editor)
    else:
        tool = SimpleNamespace(draw_app=editor, app=editor.app, sel_storage=[{
            'type': 'REG', 'size': 0, 'geometry': [DrawToolShape({'solid': box(5, 5, 6, 6)})]}])
        ImportEditorGrb.import_shapes(tool)
    after = snapshot(editor)
    after_regions = getattr(editor, 'loaded_as_regions', False)
    assert after != before
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot(editor) == before
    assert getattr(editor, 'loaded_as_regions', False) == before_regions
    assert editor.redo()
    assert snapshot(editor) == after
    assert getattr(editor, 'loaded_as_regions', False) == after_regions


def test_track_backspace_is_its_own_reversible_committed_action():
    first = trace(0, 0, 3, 0, 0.4)
    editor = make_editor({10: {'type': 'C', 'size': 0.4, 'geometry': [first]}})
    tool = TrackEditorGrb.__new__(TrackEditorGrb)
    tool.draw_app, tool.app = editor, editor.app
    tool.points, tool.temp_points = [(0, 0), (3, 0)], [(0, 0), (3, 0)]
    tool.click_shapes = [(first, editor.storage_dict[10]['geometry'])]
    tool.utility_geometry = lambda data: None
    editor.draw_utility_geometry = MagicMock()
    tool.on_key('Backspace')
    assert editor.storage_dict[10]['geometry'] == []
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]


def test_bend_joins_normal_history_and_undo_can_cross_other_edits():
    first = track([(0, 0), (4, 0), (4, 4)])
    editor = editor_for([first])
    editor.history.reset()
    start(editor, (4, 0))
    release(editor, (5, 1))
    moved = editor.storage_dict[10]['geometry'][0]
    assert len(editor.history.undo_stack) == 1
    editor.selected = [moved]
    editor.delete_selected()
    assert len(editor.history.undo_stack) == 2
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [moved]
    assert editor.undo()
    assert editor.storage_dict[10]['geometry'] == [first]
    assert editor.redo()
    assert editor.storage_dict[10]['geometry'] == [moved]


def test_legacy_buffer_keeps_follow_and_records_region_export_requirement():
    first = pad(0, 0, 1)
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': [first]}})
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    follow = first.geo['follow']
    BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), 0.1, 1)
    run_job(editor)
    assert first.geo['follow'] is follow
    assert editor.loaded_as_regions
    assert editor.undo()
    assert not editor.loaded_as_regions


@pytest.mark.parametrize('column,text', [(1, '12'), (3, '0.5')])
def test_aperture_table_edits_restore_metadata_records_selection_and_aliases(column, text):
    editor, first, second = scene()
    editor.tid2apcode = {1: 10, 2: 11}
    editor.oldapcode_newapcode = {10: 10, 11: 11}
    editor.selected = [first]
    editor.last_aperture_selected = 10
    before = snapshot(editor)
    table = editor.ui.apertures_table
    with QtCore.QSignalBlocker(table):
        table.setItem(0, column, QtWidgets.QTableWidgetItem(text))
        table.setCurrentCell(0, column)
    table.itemChanged.connect(editor.on_tool_edit)
    editor.on_tool_edit()
    after = snapshot(editor)
    assert after != before
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert snapshot(editor) == before
    assert editor.selected == [first]
    assert editor.oldapcode_newapcode == {10: 10, 11: 11}
    assert editor.last_aperture_selected == 10
    assert editor.redo()
    assert snapshot(editor) == after
    if column == 1:
        assert editor.last_aperture_selected == 12
    else:
        assert editor.storage_dict[10]['size'] == 0.5


def test_invalid_buffer_and_failed_nested_action_do_not_leave_partial_edits():
    editor, first, second = scene()
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    before = snapshot(editor)
    result = BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), float('nan'), 1)
    assert result == 'fail'
    assert snapshot(editor) == before
    assert editor.history.undo_stack == []
    original_storage = editor.storage_dict[10]['geometry']
    with editor.history.operation('Atomic group'):
        editor.delete_selected()
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), float('nan'), 1)
    assert snapshot(editor) == before
    assert editor.storage_dict[10]['geometry'] is original_storage
    assert editor.history.undo_stack == []


@pytest.mark.parametrize('distance,standalone,keep_other', [(0.3, True, True), (-2, True, False), (-2, False, True)])
def test_buffer_undo_redo_save_reparse_preserves_material_and_export_mode(app, distance, standalone, keep_other):

    source = make_gerber(app, NATIVE_GBR, name='board')
    first = DrawToolShape({'solid': Point(4, 4).buffer(1, 16), 'follow': Point(4, 4)})
    other = DrawToolShape({'solid': Point(10, 4).buffer(1, 16), 'follow': Point(10, 4)})
    editor = make_editor({10: {'type': 'C', 'size': 2, 'geometry': [first, other] if keep_other else [first]}})
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    if standalone:
        BufferEditorTool.buffer(SimpleNamespace(draw_app=editor, app=editor.app), distance, 1)
        if editor.history.pending is not None:
            run_job(editor)
    else:
        Host(editor).on_buffer_action(distance, 1)
    for mode in ('buffered', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = getattr(editor, 'loaded_as_regions', False)
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        saved = result['obj']
        assert saved.export_as_regions is (mode != 'undo')
        expected = unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                                for shape in aperture.get('geometry', ())])
        for text in (saved.source_file, export_gerber_text(app, saved)):
            parsed = Gerber(app, steps_per_circle=64)
            # The importer refuses empty boards by design; it must still contain no resurrected copper.
            assert parsed.parse_lines(text.splitlines()) == ('fail' if expected.is_empty else None)
            actual = unary_union(parsed.solid_geometry)
            if expected.is_empty:
                assert actual.is_empty
            else:
                assert actual.hausdorff_distance(expected) < 0.001


def test_save_empty_solid_keeps_clear_data_without_flashing_stale_follow(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    clear = Point(2, 2).buffer(0.2)
    erased = DrawToolShape({'solid': Polygon(), 'follow': Point(2, 2), 'clear': clear})
    other = DrawToolShape({'solid': Point(10, 2).buffer(1, 16), 'follow': Point(10, 2)})
    storage = {10: {'type': 'C', 'size': 2, 'geometry': [erased, other]}}
    stand_in = gerber_stand_in(app, source, storage)
    AppGerberEditor.build_edited_gerber(stand_in, 'board', storage)
    saved = app.custom_signal.emit.call_args.args[0]['obj']
    assert saved.tools[10]['geometry'][0]['clear'].equals(clear)
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    actual = unary_union(parsed.solid_geometry)
    assert not actual.contains(Point(2, 2))
    assert actual.hausdorff_distance(other.geo['solid']) < 0.001


def test_save_as_new_detached_build_commits_reparseable_copy_without_changing_source(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    tools_before, solid_before, source_before = source.tools, source.solid_geometry, source.source_file
    editor = gerber_stand_in(app, source, gerber_storage(source))
    editor.deactivate_grb_editor = MagicMock()
    app.connect_custom_signal = MagicMock()
    app.worker_task = MagicMock()
    created = []

    def new_object(kind, name, initialize):
        obj = type(source)(name, app)
        initialize(obj, app)
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = new_object
    AppGerberEditor.update_fcgerber(editor)
    job = app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    app.connect_custom_signal.call_args.args[0](app.custom_signal.emit.call_args.args[0])
    assert len(created) == 1
    saved = created[0]
    assert saved.obj_options['name'] == 'board_edit'
    assert saved.history_uid != source.history_uid
    assert source.tools is tools_before and source.solid_geometry is solid_before
    assert source.source_file == source_before
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(saved.source_file.splitlines()) is None
    assert parsed.solid_geometry
    editor.deactivate_grb_editor.assert_called_once()


def test_save_keeps_the_object_attributes_of_unchanged_and_moved_records_but_not_of_new_ones(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    source.to_attributes = {1: {'.N': ['GND']}, 2: {'.N': ['VCC']}}
    source.file_attributes = {'.FileFunction': ['Copper']}
    records = [rec for tool in source.tools.values() for rec in tool['geometry']]
    for number, rec in enumerate(records):
        rec['to'] = 1 + number % 2
    storage = gerber_storage(source)
    first = next(iter(storage))
    storage[first]['geometry'].append(SimpleNamespace(geo={'solid': Point(30, 30).buffer(1), 'follow': Point(30, 30)}))
    AppGerberEditor.build_edited_gerber(gerber_stand_in(app, source, storage), 'board', storage)
    saved = app.custom_signal.emit.call_args.args[0]['obj']
    assert saved.to_attributes == source.to_attributes
    assert saved.file_attributes == source.file_attributes
    saved_to = [[rec.get('to') for rec in saved.tools[ap]['geometry']] for ap in storage]
    expected = [[rec.get('to') for rec in source.tools[ap]['geometry']] for ap in storage]
    expected[0].append(None)
    assert saved_to == expected


@pytest.mark.parametrize('family', ['move', 'copy'])
def test_move_and_copy_keep_the_to_id_and_undo_restores_it(family):
    editor, first, second = scene()
    first.geo['to'] = 3
    editor.selected = [first]
    editor.sync_aperture_rows({10})
    tool =(MoveEditorGrb if family == 'move' else CopyEditorGrb)(editor)
    editor.active_tool = tool
    tool.origin, tool.destination = (0, 0), (0, 5)
    tool.make()
    shapes = editor.storage_dict[10]['geometry']
    assert len(shapes) == (1 if family == 'move' else 2)
    assert shapes[-1].geo['to'] == 3
    assert shapes[-1].geo['follow'].equals(Point(0.5, 5.5))
    assert 'to' not in second.geo
    assert editor.undo()
    assert [shape.geo.get('to') for shape in editor.storage_dict[10]['geometry']] == [3]


def test_region_union_keeps_the_to_id_only_when_all_the_merged_records_have_it():
    for ids, expected in (((4, 4), 4), ((4, 5), None), ((4, None), None)):
        editor, first, second = scene()
        second.geo['solid'] = box(0.5, 0, 11, 1)
        editor.selected = [first, second]
        for shape, to in zip((first, second), ids):
            if to is not None:
                shape.geo['to'] = to
        assert editor.apply_region_operation('union') != 'fail'
        merged = [shape for aperture in editor.storage_dict.values() for shape in aperture['geometry']]
        assert [shape.geo.get('to') for shape in merged] == [expected]
