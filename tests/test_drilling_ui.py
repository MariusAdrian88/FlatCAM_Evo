"""Drilling plugin UI / controller behavior: tool order, levels, selection, exclusion areas, form <-> storage."""

import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6 import QtWidgets
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


def _area(obj_type='excellon', x=0.0, strategy='around', overz=1.0, idx=1):
    area = {'obj_type': obj_type, 'shape': Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)]),
            'strategy': strategy, 'overz': overz, 'idx': idx}
    if obj_type == 'excellon':
        area['owner'] = 'drilling'
    return area


# ---------------------------------------------------------------------------------------------------------------------
# Tool order
# ---------------------------------------------------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------------------------------------------------
# Form <-> storage
# ---------------------------------------------------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------------------------------------------------
# Add objects (more Excellon objects in one job) and holes inside drawn areas
# ---------------------------------------------------------------------------------------------------------------------


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

