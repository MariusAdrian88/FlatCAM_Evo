"""Gerber Editor aperture table operations: delete unused, renumber, merge duplicates, round sizes, same type."""
import math
from copy import deepcopy
from types import SimpleNamespace

import pytest
from shapely import Point, box
from shapely.ops import unary_union

from appEditors.appGerberEditor import AppGerberEditor
from appEditors.grb_plugins import GrbAperturePlugin as module
from appEditors.grb_plugins.GrbCommon import DrawToolShape
from test_gerber_editor_pick import make_editor
from appParsers.ParseGerber import Gerber
from test_editor_save_modes import gerber_stand_in
from tests.helpers_objects import app, _headless_qt, make_gerber, NATIVE_GBR  # noqa: F401,E402


def test_database_autoloads_only_checked_apertures_with_converted_dimensions(tmp_path):
    from appCommon.ToolDatabase import write_database, write_apertures
    from defaults import AppDefaults
    path = tmp_path / 'tools.sqlite3'
    write_database(path, {})
    write_apertures(path, {
        10: {'type': 'C', 'size': 0.1, 'units': 'IN', 'autoload': True},
        11: {'type': 'R', 'width': 0.1, 'height': 0.2, 'units': 'IN', 'autoload': True},
        12: {'type': 'O', 'width': 1, 'height': 2, 'units': 'MM', 'autoload': False},
    }, expected={})
    editor = make_editor()
    editor.app.options.update(AppDefaults.factory_defaults)
    editor.app.tools_database_path.return_value = str(path)
    editor.gerber_obj = SimpleNamespace(tools={}, solid_geometry=[], obj_options={'name': 'new_grb'})
    editor.build_ui = AppGerberEditor.build_ui.__get__(editor)
    pending = editor.history.reserve()
    editor.apply_load_result({'pending': pending, 'storage': {}, 'regions': False})
    assert set(editor.storage_dict) == {10, 11}
    assert editor.ui.apertures_table.rowCount() == 2
    assert [editor.ui.apertures_table.item(row, 1).text() for row in range(2)] == ['10', '11']
    assert editor.storage_dict[10] == {'type': 'C', 'size': pytest.approx(2.54), 'geometry': []}
    assert editor.storage_dict[11]['width'] == pytest.approx(2.54)
    assert editor.storage_dict[11]['height'] == pytest.approx(5.08)
    assert editor.storage_dict[11]['size'] == pytest.approx(math.hypot(2.54, 5.08))


@pytest.mark.parametrize('tools, solid', [
    ({10: {'type': 'C', 'size': 1, 'geometry': []}}, []),
    ({10: {'type': 'C', 'size': 1, 'geometry': [DrawToolShape({'solid': box(0, 0, 1, 1)})]}}, []),
    ({}, [box(0, 0, 1, 1)]),
])
def test_autoload_preserves_existing_apertures_and_geometry(tools, solid):
    editor = make_editor(deepcopy(tools))
    editor.gerber_obj = SimpleNamespace(tools=tools, solid_geometry=solid)
    before = dict(editor.storage_dict)
    editor.app.tools_database_path.side_effect = AssertionError('Existing objects must not read defaults')
    editor.autoload_apertures()
    assert editor.storage_dict == before


def test_aperture_library_saves_autoload_and_rejects_stale_or_invalid_edits(tmp_path):
    from appCommon.ToolDatabase import read_apertures, write_apertures, write_database, read_database
    from appGUI.ApertureLibrary import ApertureLibrary
    from PyQt6 import QtCore
    path = tmp_path / 'tools.sqlite3'
    write_database(path, {})
    editor = make_editor()
    editor.app.tools_database_path.return_value = str(path)
    dialog = ApertureLibrary(editor.app, apertures={
        10: {'type': 'C', 'size': 0.6, 'geometry': [flash(0, 0, 0.6)]}})
    dialog.table.item(0, 6).setCheckState(QtCore.Qt.CheckState.Checked)
    assert dialog.save_library()
    stored = read_apertures(path)
    assert stored == {10: {'type': 'C', 'size': 0.6, 'units': 'MM', 'autoload': True}}
    stale = ApertureLibrary(editor.app)
    dialog.table.item(0, 6).setCheckState(QtCore.Qt.CheckState.Unchecked)
    assert dialog.save_library()
    assert not stale.save_library()
    assert not read_apertures(path)[10]['autoload']
    with pytest.raises(ValueError):
        write_apertures(path, {10: {'type': 'C', 'size': -1, 'units': 'MM', 'autoload': True}},
                        expected=read_apertures(path))
    assert read_apertures(path)[10]['size'] == 0.6
    write_database(path, {})
    assert read_apertures(path)[10]['size'] == 0.6
    assert read_database(path)[0] == {}
    dialog.close()
    stale.close()


def test_database_aperture_code_collision_keeps_both_presets_and_deletes_only_selected(tmp_path):
    from appCommon.ToolDatabase import read_apertures, write_apertures, write_database
    from appGUI.ApertureLibrary import ApertureLibrary
    path = tmp_path / 'tools.sqlite3'
    write_database(path, {})
    write_apertures(path, {10: {'type': 'C', 'size': 0.6, 'units': 'MM', 'autoload': True}}, expected={})
    editor = make_editor()
    editor.app.tools_database_path.return_value = str(path)
    dialog = ApertureLibrary(editor.app, apertures={10: {'type': 'C', 'size': 0.8, 'geometry': []}})
    assert dialog.save_library()
    assert {code: item['size'] for code, item in read_apertures(path).items()} == {10: 0.6, 11: 0.8}
    dialog.table.selectRow(1)
    dialog.delete_selected()
    assert dialog.save_library()
    assert set(read_apertures(path)) == {10}
    dialog.close()


def flash(x, y, diameter):
    center = Point(x, y)
    return DrawToolShape({'follow': center, 'solid': center.buffer(diameter / 2, 16)})


def rect_flash(x, y, width, height):
    return DrawToolShape({'follow': Point(x, y), 'solid': box(x - width / 2, y - height / 2,
                                                              x + width / 2, y + height / 2)})


def region(x):
    polygon = box(x, 0, x + 2, 2)
    return DrawToolShape({'follow': polygon.exterior, 'solid': polygon})


def test_delete_unused_removes_empty_circle_and_keeps_empty_region():
    editor = make_editor({
        10: {'type': 'C', 'size': 2, 'geometry': [flash(0, 0, 2)]},
        11: {'type': 'C', 'size': 1, 'geometry': []},
        0: {'type': 'REG', 'size': 0, 'geometry': []},
    })
    assert module.apply_delete_unused(editor) == [11]
    assert set(editor.storage_dict) == {10, 0}
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert 11 in editor.storage_dict
    assert editor.redo()
    assert 11 not in editor.storage_dict


def test_renumber_follows_rank_order_and_moves_records_with_their_aperture():
    rect = rect_flash(0, 0, 1, 2)
    round_flash = flash(5, 0, 1)
    editor = make_editor({
        15: {'type': 'C', 'size': 1, 'geometry': [round_flash]},
        12: {'type': 'R', 'size': 0, 'width': 1, 'height': 2, 'geometry': [rect]},
        0: {'type': 'REG', 'size': 0, 'geometry': [region(20)]},
    })
    assert module.apply_renumber(editor, start=10) == {12: 10, 15: 11}
    assert set(editor.storage_dict) == {10, 11, 0}
    assert editor.storage_dict[10]['type'] == 'R'
    assert editor.storage_dict[10]['geometry'] == [rect]
    assert editor.storage_dict[11]['type'] == 'C'
    assert editor.storage_dict[11]['geometry'] == [round_flash]
    assert editor.storage_dict[0]['type'] == 'REG'
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert set(editor.storage_dict) == {0, 12, 15}
    assert editor.storage_dict[12]['type'] == 'R'


def test_merge_duplicates_moves_records_into_lowest_code_and_keeps_other_sizes():
    first, second = flash(0, 0, 0.5), flash(5, 0, 0.5)
    other = flash(9, 0, 0.6)
    editor = make_editor({
        10: {'type': 'C', 'size': 0.5, 'geometry': [first]},
        14: {'type': 'C', 'size': 0.5, 'geometry': [second]},
        16: {'type': 'C', 'size': 0.6, 'geometry': [other]},
    })
    assert module.apply_merge_duplicates(editor) == {14: 10}
    assert set(editor.storage_dict) == {10, 16}
    assert editor.storage_dict[10]['geometry'] == [first, second]
    assert editor.storage_dict[16]['geometry'] == [other]
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert set(editor.storage_dict) == {10, 14, 16}
    assert len(editor.storage_dict[10]['geometry']) == 1


def test_round_sizes_rounds_mm_size_rebuilds_flash_and_leaves_exact_codes_alone():
    off_grid = flash(0, 0, 0.5004)
    exact = flash(5, 0, 0.5)
    editor = make_editor({
        10: {'type': 'C', 'size': 0.5004, 'geometry': [off_grid]},
        11: {'type': 'C', 'size': 0.5, 'geometry': [exact]},
    })
    assert module.apply_round_sizes(editor) == [10]
    assert editor.storage_dict[10]['size'] == pytest.approx(0.5)
    minx, miny, maxx, maxy = editor.storage_dict[10]['geometry'][0].geo['solid'].bounds
    assert maxx - minx == pytest.approx(0.5, abs=1e-3)
    assert editor.storage_dict[11]['geometry'][0] is exact
    assert len(editor.history.undo_stack) == 1
    assert editor.undo()
    assert editor.storage_dict[10]['size'] == pytest.approx(0.5004)


def test_same_type_codes_returns_only_codes_of_the_same_type():
    editor = make_editor({
        10: {'type': 'C', 'size': 1, 'geometry': []},
        11: {'type': 'C', 'size': 2, 'geometry': []},
        12: {'type': 'R', 'size': 0, 'width': 1, 'height': 2, 'geometry': []},
    })
    assert sorted(module.same_type_codes(editor, 10)) == [10, 11]
    assert module.same_type_codes(editor, 12) == [12]


def test_native_save_reparse_after_renumber_undo_redo(app):
    source = make_gerber(app, NATIVE_GBR, name='board')
    editor = make_editor({
        15: {'type': 'C', 'size': 1, 'geometry': [flash(5, 0, 1)]},
        12: {'type': 'C', 'size': 2, 'geometry': [flash(0, 0, 2)]},
    })
    editor.loaded_as_regions = False
    assert module.apply_renumber(editor, start=10) == {12: 10, 15: 11}
    for mode in ('edited', 'undo', 'redo'):
        if mode == 'undo':
            assert editor.undo()
        elif mode == 'redo':
            assert editor.redo()
        assert not editor.loaded_as_regions
        stand_in = gerber_stand_in(app, source, editor.storage_dict)
        stand_in.loaded_as_regions = False
        AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
        result = app.custom_signal.emit.call_args.args[0]
        assert 'error' not in result
        parsed = Gerber(app, steps_per_circle=64)
        assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
        saved = unary_union(parsed.solid_geometry)
        expected = unary_union([shape.geo['solid'] for aperture in editor.storage_dict.values()
                                for shape in aperture['geometry']])
        assert saved.hausdorff_distance(expected) < 0.002


@pytest.mark.parametrize('kind', ['R', 'O'])
def test_rounded_rectangular_and_oblong_flashes_save_as_flashes(app, kind):
    from appEditors.grb_plugins.GrbSelectionPlugin import flash_geo
    probe = make_editor({10: {'type': 'C', 'size': 2, 'geometry': []}})
    width, height = 1.23456, 2.34567
    item = {'type': kind, 'size': math.hypot(width, height), 'width': width, 'height': height, 'geometry': []}
    item['geometry'].append(DrawToolShape(flash_geo(probe, item, Point(0, 0))))
    editor = make_editor({12: item})
    editor.loaded_as_regions = False
    assert module.apply_round_sizes(editor) == [12]
    assert not editor.loaded_as_regions
    source = make_gerber(app, NATIVE_GBR, name='board')
    stand_in = gerber_stand_in(app, source, editor.storage_dict)
    stand_in.loaded_as_regions = editor.loaded_as_regions
    AppGerberEditor.build_edited_gerber(stand_in, 'board', editor.storage_dict)
    result = app.custom_signal.emit.call_args.args[0]
    assert 'error' not in result
    assert 'G36' not in result['obj'].source_file
    parsed = Gerber(app, steps_per_circle=64)
    assert parsed.parse_lines(result['obj'].source_file.splitlines()) is None
    assert any(isinstance(record['follow'], Point) for tool in parsed.tools.values()
               if tool.get('type') == kind for record in tool['geometry'])
