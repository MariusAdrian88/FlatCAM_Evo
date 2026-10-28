"""Drilling plugin UI / controller behavior: tool order, levels, selection, exclusion areas, form <-> storage."""

import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6 import QtWidgets, QtCore
from shapely.geometry import Point, Polygon

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return App(qapp=qapp, headless=True)


@pytest.fixture(autouse=True)
def no_plot(monkeypatch, fc):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    fc.exc_areas.exclusion_areas_storage.clear()
    yield
    fc.exc_areas.exclusion_areas_storage.clear()


def _load(fc, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, 'unify_mixed.drl'))
        obj.create_geometry()
    return fc.app_obj.new_object('excellon', name, init, plot=False)


def _open(fc, name):
    tool = fc.drilling_tool
    tool.run(toggle=False)
    tool.ui.object_combo.setCurrentText(name)
    tool.on_object_changed()
    tool.ui.tools_table.selectAll()
    return tool


def _edit(widget, value):
    widget.set_value(value)
    widget.editingFinished.emit()


def _table_uids(tool):
    t = tool.ui.tools_table
    return [int(t.item(r, 3).text()) for r in range(t.rowCount() - 2)]


def _area(obj_type='excellon', x=0.0, strategy='around', overz=1.0, idx=1):
    area = {'obj_type': obj_type, 'shape': Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)]),
            'strategy': strategy, 'overz': overz, 'idx': idx}
    if obj_type == 'excellon':
        area['owner'] = 'drilling'
    return area


# ---------------------------------------------------------------------------------------------------------------------
# Tool order
# ---------------------------------------------------------------------------------------------------------------------

def test_order_sorts_the_rows_only(fc):
    exc = _load(fc, 'e_ui_order')
    tool = _open(fc, 'e_ui_order')
    before = deepcopy({k: v['tooldia'] for k, v in exc.tools.items()})
    keys = list(exc.tools)

    tool.ui.order_combo.setCurrentIndex(2)      # Reverse
    assert _table_uids(tool) == [3, 2, 1]
    assert list(exc.tools) == keys              # the object's tools are untouched: no new or duplicated tool
    assert {k: v['tooldia'] for k, v in exc.tools.items()} == before
    assert len(tool.excellon_tools) == 3

    tool.ui.order_combo.setCurrentIndex(1)      # Forward
    assert _table_uids(tool) == [1, 2, 3]
    tool.ui.order_combo.setCurrentIndex(0)
    assert list(exc.tools) == keys




# ---------------------------------------------------------------------------------------------------------------------
# Level: stored values are never overwritten
# ---------------------------------------------------------------------------------------------------------------------

def test_beginner_level_keeps_the_stored_tool_values(fc):
    exc = _load(fc, 'e_ui_level')
    for uid, tl in exc.tools.items():
        tl['data'].update({'tools_drill_multidepth': True, 'tools_drill_dwell': True,
                           'tools_drill_drill_slots': True, 'tools_drill_area_exclusion': True,
                           'tools_drill_dwelltime': 3.0})
    stored = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    tool = _open(fc, 'e_ui_level')
    for level in ('b', 'a', 'b'):
        tool.change_level(level)
    tool.set_tool_ui()
    tool.on_object_changed()
    assert {k: v['data'] for k, v in exc.tools.items()} == stored

    # the generation takes the safe values in Beginner and the stored ones in Advanced
    tool.change_level('b')
    safe = tool._effective_tool_data(1)
    assert not safe['tools_drill_multidepth'] and not safe['tools_drill_dwell']
    assert not safe['tools_drill_drill_slots'] and not safe['tools_drill_area_exclusion']
    assert exc.tools[1]['data']['tools_drill_multidepth'] is True
    tool.change_level('a')
    assert tool._effective_tool_data(1)['tools_drill_multidepth'] is True




# ---------------------------------------------------------------------------------------------------------------------
# Object lifecycle
# ---------------------------------------------------------------------------------------------------------------------





# ---------------------------------------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------------------------------------



def test_rebuild_keeps_a_partial_tool_selection(fc):
    _load(fc, 'e_ui_sel')
    tool = _open(fc, 'e_ui_sel')
    table = tool.ui.tools_table
    table.clearSelection()
    sel = table.selectionModel()
    for row in (0, 1):
        sel.select(table.model().index(row, 0), QtCore.QItemSelectionModel.SelectionFlag.Select |
                   QtCore.QItemSelectionModel.SelectionFlag.Rows)
    chosen = sorted(tool.get_selected_tools_uid())
    assert len(chosen) == 2
    # an exclusion area added rebuilds the table: the user's two tools stay selected, the third is not added
    _add_areas(fc, tool, [_area('excellon', x=20)])
    assert sorted(tool.get_selected_tools_uid()) == chosen
    # an other object with the same tool ids starts with all its tools selected
    _load(fc, 'e_ui_sel2')
    tool = _open(fc, 'e_ui_sel2')
    assert len(tool.get_selected_tools_uid()) == 3




# ---------------------------------------------------------------------------------------------------------------------
# Form <-> storage
# ---------------------------------------------------------------------------------------------------------------------

def test_edit_adds_a_missing_key_and_form_shows_default_for_it(fc):
    exc = _load(fc, 'e_ui_keys')
    for tl in exc.tools.values():
        tl['data'].pop('tools_drill_dwelltime', None)
    tool = _open(fc, 'e_ui_keys')
    tool.ui.tools_table.clearSelection()
    tool.ui.tools_table.selectRow(0)
    # tool 1 lacks the key: the form must not keep the value of a previously shown tool
    tool.ui.dwelltime_entry.set_value(7.5)
    tool.ui.tools_table.selectRow(1)
    assert tool.ui.dwelltime_entry.get_value() == tool._default('tools_drill_dwelltime')
    _edit(tool.ui.dwelltime_entry, 2.5)
    assert exc.tools[2]['data']['tools_drill_dwelltime'] == 2.5


def test_blank_xy_entries_store_none_and_show_empty(fc):
    exc = _load(fc, 'e_ui_xy')
    tool = _open(fc, 'e_ui_xy')
    assert tool.ui.endxy_entry.text() == ''             # the default is None: not the text 'None'
    _edit(tool.ui.endxy_entry, '')
    tool.ui.endxy_entry.setText('')
    tool.ui.endxy_entry.editingFinished.emit()
    assert all(t['data']['tools_drill_endxy'] is None for t in exc.tools.values())
    tool.ui.endxy_entry.setText('1, 2')
    tool.ui.endxy_entry.editingFinished.emit()
    assert exc.tools[1]['data']['tools_drill_endxy'] == (1, 2)
    assert tool._validate_xy_fields()
    tool.ui.endxy_entry.setText('5')
    tool.ui.endxy_entry.editingFinished.emit()
    assert not tool._validate_xy_fields()


def test_toolchange_xy_is_validated_only_when_toolchange_is_on(fc):
    exc = _load(fc, 'e_ui_txy')
    for tl in exc.tools.values():
        tl['data']['tools_drill_toolchangexy'] = None
        tl['data']['tools_drill_toolchange'] = False
    tool = _open(fc, 'e_ui_txy')
    assert tool._validate_xy_fields([1, 2, 3])
    exc.tools[2]['data']['tools_drill_toolchange'] = True
    # the shipped default is text, a blank or None means no XY move: all valid with toolchange on
    for xy in ('0.0, 0.0', '', None, (1.5, -2)):
        exc.tools[2]['data']['tools_drill_toolchangexy'] = xy
        assert tool._validate_xy_fields([1, 2, 3]), xy
    # a value that is not a pair of numbers is an error (only when the tool with toolchange is selected)
    exc.tools[2]['data']['tools_drill_toolchangexy'] = '5'
    assert not tool._validate_xy_fields([1, 2, 3])
    assert tool._validate_xy_fields([1, 3])


def test_apply_to_all_with_a_totals_row_current_never_blanks_the_tools(fc):
    exc = _load(fc, 'e_ui_all')
    tool = _open(fc, 'e_ui_all')
    before = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    tool.ui.tools_table.setCurrentCell(tool.ui.tools_table.rowCount() - 1, 1)     # the 'Total Slots' row
    tool.on_apply_param_to_all_clicked()
    after = {k: v['data'] for k, v in exc.tools.items()}
    assert all(d for d in after.values())
    assert set(after[1]) == set(before[1])


def test_reset_button_restores_the_defaults_of_the_object(fc):
    exc = _load(fc, 'e_ui_reset')
    tool = _open(fc, 'e_ui_reset')
    default = exc.obj_options['tools_drill_cutz']
    _edit(tool.ui.cutz_entry, default - 1.0)
    assert exc.tools[1]['data']['tools_drill_cutz'] == default - 1.0
    tool.ui.reset_button.click()
    assert all(t['data']['tools_drill_cutz'] == default for t in exc.tools.values())


# ---------------------------------------------------------------------------------------------------------------------
# Exclusion areas
# ---------------------------------------------------------------------------------------------------------------------

def _add_areas(fc, tool, areas):
    fc.exc_areas.exclusion_areas_storage.extend(areas)
    tool.build_tool_ui()


def test_exclusion_table_shows_only_excellon_areas_and_maps_rows(fc):
    _load(fc, 'e_ui_ex')
    tool = _open(fc, 'e_ui_ex')
    geo = _area('geometry', x=10, idx=1)
    e1 = _area('excellon', x=20, idx=1, overz=1.0)          # duplicate idx on purpose
    e2 = _area('excellon', x=30, idx=1, overz=2.0)
    _add_areas(fc, tool, [geo, e1, e2])
    table = tool.ui.exclusion_table
    assert table.rowCount() == 2
    assert [table.item(r, 0).text() for r in range(2)] == ['1', '2']

    table.item(1, 3).setText('4.5')                         # a user edit of row 2 -> the second excellon area
    assert e2['overz'] == pytest.approx(4.5) and e1['overz'] == 1.0 and geo['overz'] == 1.0
    table.cellWidget(0, 1 + 1).setCurrentIndex(1)           # row 1 strategy -> Over
    assert e1['strategy'] != 'around' and e2['strategy'] == 'around' and geo['strategy'] == 'around'


def test_milling_area_on_an_excellon_is_not_a_drilling_area(fc):
    _load(fc, 'e_ui_ex5')
    tool = _open(fc, 'e_ui_ex5')
    milling = _area('excellon', x=10)
    del milling['owner']                                    # Milling stores obj_type 'excellon' too, without owner
    mine = _area('excellon', x=20)
    _add_areas(fc, tool, [milling, mine])
    assert tool.ui.exclusion_table.rowCount() == 1
    assert tool._drill_exclusion_areas() == [mine]
    tool.on_clear_area_click()
    assert fc.exc_areas.exclusion_areas_storage == [milling]


def test_delete_selected_area_after_a_previous_delete(fc):
    _load(fc, 'e_ui_ex3')
    tool = _open(fc, 'e_ui_ex3')
    areas = [_area('excellon', x=10 * i, idx=i + 1) for i in range(3)]
    _add_areas(fc, tool, areas)
    tool.ui.exclusion_table.selectRow(0)
    tool.on_delete_sel_areas()
    assert fc.exc_areas.exclusion_areas_storage == areas[1:]
    tool.ui.exclusion_table.selectRow(1)                    # the last one now, whatever its idx is
    tool.on_delete_sel_areas()
    assert fc.exc_areas.exclusion_areas_storage == [areas[1]]
    assert len(tool._drill_exclusion_areas()) == 1




def test_peck_dwell_bottom_spindle_and_plunge_fields_are_per_tool_and_advanced_only(fc):
    exc = _load(fc, 'e_ui_peck')
    tool = _open(fc, 'e_ui_peck')
    tool.change_level('a')
    tool.ui.tools_table.clearSelection()
    tool.ui.tools_table.selectRow(0)
    first = list(exc.tools)[0]
    _edit(tool.ui.peck_depth_entry, 0.8)
    tool.ui.peck_cb.setChecked(True)
    tool.ui.peck_cb.clicked.emit(True)
    _edit(tool.ui.peck_retract_entry, 0.25)
    tool.ui.spindledir_radio.set_value('CCW')
    tool.ui.spindledir_radio.activated_custom.emit(tool.ui.spindledir_radio.get_value())
    data = exc.tools[first]['data']
    assert data['tools_drill_peck_depth'] == 0.8 and data['tools_drill_peck_retract'] == 0.25
    assert data['tools_drill_spindledir'] == 'CCW'
    other = [t for t in exc.tools if t != first][0]
    assert exc.tools[other]['data']['tools_drill_peck_depth'] != 0.8
    tool.change_level('b')
    for widget in (tool.ui.peck_cb, tool.ui.peck_depth_entry, tool.ui.dwell_bottom_cb, tool.ui.spindledir_radio,
                   tool.ui.fplunge_cb, tool.ui.fretract_cb):
        assert widget.isHidden()
    tool.change_level('a')
    assert not tool.ui.peck_cb.isHidden() and not tool.ui.spindledir_radio.isHidden()


def test_spot_pass_fields_are_job_wide_and_advanced_only(fc):
    exc = _load(fc, 'e_ui_spot')
    tool = _open(fc, 'e_ui_spot')
    tool.change_level('a')
    tool.ui.spot_cb.setChecked(True)
    tool.ui.spot_cb.clicked.emit(True)
    _edit(tool.ui.spot_dia_entry, 0.6)
    _edit(tool.ui.spot_cutz_entry, -0.4)
    _edit(tool.ui.spot_feedrate_entry, 40.0)
    for data in (t['data'] for t in exc.tools.values()):
        assert data['tools_drill_spot'] is True and data['tools_drill_spot_dia'] == 0.6
        assert data['tools_drill_spot_cutz'] == -0.4 and data['tools_drill_spot_feedrate_z'] == 40.0
    tool.change_level('b')
    for widget in (tool.ui.spot_cb, tool.ui.spot_dia_entry, tool.ui.spot_cutz_entry, tool.ui.spot_feedrate_entry):
        assert widget.isHidden()
    tool.change_level('a')
    assert not tool.ui.spot_cb.isHidden()


def test_job_transform_fields_are_job_wide_and_advanced_only(fc):
    exc = _load(fc, 'e_ui_xform')
    tool = _open(fc, 'e_ui_xform')
    tool.change_level('a')
    _edit(tool.ui.job_offset_entry, '1.5, -2')
    tool.ui.mirror_radio.set_value('xy')
    tool.ui.mirror_radio.activated_custom.emit(tool.ui.mirror_radio.get_value())
    _edit(tool.ui.mirror_point_entry, '3, 4')
    _edit(tool.ui.array_columns_entry, 3)
    _edit(tool.ui.array_rows_entry, 2)
    _edit(tool.ui.array_pitch_entry, '10, 20')
    for data in (t['data'] for t in exc.tools.values()):
        assert tuple(data['tools_drill_job_offset_xy']) == (1.5, -2) and data['tools_drill_mirror'] == 'xy'
        assert tuple(data['tools_drill_mirror_point']) == (3, 4)
        assert data['tools_drill_array_columns'] == 3 and data['tools_drill_array_rows'] == 2
        assert tuple(data['tools_drill_array_pitch']) == (10, 20)
    tool.change_level('b')
    for widget in (tool.ui.job_offset_entry, tool.ui.mirror_radio, tool.ui.mirror_point_entry,
                   tool.ui.array_columns_entry, tool.ui.array_rows_entry, tool.ui.array_pitch_entry):
        assert widget.isHidden()
    tool.change_level('a')
    assert not tool.ui.job_offset_entry.isHidden() and not tool.ui.array_pitch_entry.isHidden()


# ---------------------------------------------------------------------------------------------------------------------
# Add objects (more Excellon objects in one job) and holes inside drawn areas
# ---------------------------------------------------------------------------------------------------------------------

def _pick_extras(tool, *names):
    for row in range(tool.ui.extra_objects_list.count()):
        item = tool.ui.extra_objects_list.item(row)
        item.setSelected(item.text() in names)


def test_add_objects_list_shows_the_merged_tools_until_it_is_cleared(fc):
    _load(fc, 'ui_x1')
    _load(fc, 'ui_x2')
    tool = _open(fc, 'ui_x1')
    tool.ui.level.setChecked(True)
    names = [tool.ui.extra_objects_list.item(r).text() for r in range(tool.ui.extra_objects_list.count())]
    assert 'ui_x2' in names and 'ui_x1' not in names

    _pick_extras(tool, 'ui_x2')
    assert _table_uids(tool) == [1, 2, 3]
    assert sum(len(t['drills']) for t in tool.excellon_tools.values()) == 12
    assert sum(len(t['drills']) for t in fc.collection.get_by_name('ui_x1').tools.values()) == 6
    # the tools are a merged copy while extras are selected: the per-tool parameters are read-only
    assert tool.ui.tool_param_frame.isEnabled() is False
    assert tool.ui.apply_param_to_all.isEnabled() is False
    assert tool.ui.extras_note_label.isHidden() is False

    _pick_extras(tool)
    assert sum(len(t['drills']) for t in tool.excellon_tools.values()) == 6
    assert tool.ui.tool_param_frame.isEnabled() is True
    assert tool.ui.apply_param_to_all.isEnabled() is True
    assert tool.ui.extras_note_label.isHidden() is True


def test_renamed_extra_object_stays_in_the_job(fc):
    _load(fc, 'ui_r1')
    _load(fc, 'ui_r2')
    tool = _open(fc, 'ui_r1')
    tool.ui.level.setChecked(True)
    _pick_extras(tool, 'ui_r2')
    extra = fc.collection.get_by_name('ui_r2')
    extra.obj_options['name'] = 'ui_r2_renamed'
    assert tool._extra_objects() == [extra]




# ---------------------------------------------------------------------------------------------------------------------
# Generate CNC Job: the job the user gets (the worker runs in place)
# ---------------------------------------------------------------------------------------------------------------------

def _generate(fc, monkeypatch, tool):
    """Generate the CNC job of the open object. Returns the CNCJob, or None when no job was made."""
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=lambda task: task['fcn'](*task['params'])))
    tool.on_generate_cnc_job()
    try:
        job = fc.collection.get_by_name(tool.ui.object_combo.currentText() + '_cnc')
    except Exception:
        return None
    return job if getattr(job, 'kind', None) == 'cncjob' else None


def test_no_toolchange_job_counts_the_drills_of_every_selected_tool(fc, monkeypatch):
    exc = _load(fc, 'e_job_cnt')
    for tl in exc.tools.values():
        tl['data']['tools_drill_toolchange'] = False
    tool = _open(fc, 'e_job_cnt')
    job = _generate(fc, monkeypatch, tool)
    assert job is not None
    assert len(job.tools) == 1                      # one tool entry for the whole job without toolchange
    entry = next(iter(job.tools.values()))
    total = sum(len(t['drills']) for t in exc.tools.values())
    assert entry['nr_drills'] == total
    assert len(entry['drills']) == entry['nr_drills']


def test_exclusion_areas_steer_the_travels_only_when_the_tool_uses_them(fc, monkeypatch):
    def two_holes(name, use_areas):
        # one tool, two holes 20 apart: the travel between them crosses the area below
        exc = _load(fc, name)
        for uid in [u for u in exc.tools if u != 1]:
            del exc.tools[uid]
        exc.tools[1]['drills'] = [Point(-10, 0), Point(10, 0)]
        exc.tools[1]['data'].update(tools_drill_area_exclusion=use_areas, tools_drill_toolchange=False)
        return exc

    area = dict(_area('excellon', x=0), shape=Polygon([(-6, -1), (-4, -1), (-4, 1), (-6, 1)]))
    # the reference: no exclusion area at all
    two_holes('e_job_plain', False)
    tool = _open(fc, 'e_job_plain')
    tool.change_level('b')
    plain = _generate(fc, monkeypatch, tool).gcode

    fc.exc_areas.exclusion_areas_storage.append(area)
    # Beginner: the Exclusion areas option is hidden and off, even if the stored value is on
    two_holes('e_job_off', True)
    tool = _open(fc, 'e_job_off')
    tool.change_level('b')
    assert _generate(fc, monkeypatch, tool).gcode == plain

    # Advanced with the option on: the travel goes around the area
    two_holes('e_job_on', True)
    tool = _open(fc, 'e_job_on')
    tool.change_level('a')
    assert _generate(fc, monkeypatch, tool).gcode != plain


def test_solder_paste_preprocessors_are_offered_by_neither_drilling_nor_milling(fc):
    # they have no feed rate, spindle or start Z code: Generate failed with an AttributeError
    assert {'Paste_1', 'Paste_GRBL', 'Paste_Marlin'} <= set(fc.preprocessors)
    _load(fc, 'e_ui_pp')
    drilling = _open(fc, 'e_ui_pp')
    fc.milling_tool.set_tool_ui()
    for combo in (drilling.ui.pp_excellon_name_cb, fc.milling_tool.ui.pp_geo_name_cb):
        names = [combo.itemText(index) for index in range(combo.count())]
        assert {'default', 'GRBL_11', 'Marlin'} <= set(names)
        assert [name for name in names if 'paste' in name.lower()] == []
