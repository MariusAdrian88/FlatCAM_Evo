"""
Issue #41: the Milling plugin took the general job settings of a multi-geometry job (preprocessor, End Z, toolchange,
exclusion areas) from whatever its form showed last. A job could be made with the settings of the previous target.
The form must show, for the selected target, the settings that the job will use: the general fields from the saved
values of the target and the per-tool fields from the saved data of the tool.
"""
import os
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import LineString

from appPlugins.ToolMilling import ToolMilling
from test_milling_old_tool_data import MILL_OPTIONS, flatcam_8_994_cutout_tool, milling_harness

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

PER_TOOL = ('tools_mill_cutz', 'tools_mill_multidepth', 'tools_mill_depthperpass', 'tools_mill_travelz',
            'tools_mill_feedrate', 'tools_mill_feedrate_z', 'tools_mill_spindlespeed')

LASER = dict(tools_mill_ppname_g='GRBL_laser', tools_mill_endz=20.0, tools_mill_toolchangez=30.0,
             tools_mill_cutz=0.0, tools_mill_feedrate=1000.0)
MILL = dict(tools_mill_ppname_g='default', tools_mill_endz=15.0, tools_mill_toolchangez=12.0,
            tools_mill_cutz=-1.8, tools_mill_feedrate=120.0)


class QtMilling(QtCore.QObject):
    """The real ToolMilling methods on a QObject: the form's signals reach form_to_storage() with their sender()."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))


@pytest.fixture(autouse=True)
def slot_errors(monkeypatch):
    """PyQt6 aborts the process on an exception in a slot: report it as a test failure instead."""
    import sys
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, value, tb: errors.append(value))
    yield
    assert not errors, errors


def saved_tool(**changes):
    geometry = [LineString([(0, 0), (10, 0)]), LineString([(0, 5), (10, 5)])]
    data = dict(deepcopy(MILL_OPTIONS), tools_mill_tooldia=1.0, tools_mill_offset_type=0, tools_mill_job_type=0,
                tools_mill_multidepth=False, tools_mill_optimization_type='R', tools_mill_toolchange=False,
                tools_mill_area_exclusion=False)
    data.update(changes)
    return {'tooldia': 1.0, 'solid_geometry': geometry, 'data': data}


def make_object(name, multigeo=True, tool=None, **changes):
    tool = tool or saved_tool(**changes)
    tool.setdefault('solid_geometry', [LineString([(0, 0), (10, 0)])])
    return SimpleNamespace(kind='geometry', multigeo=multigeo, tools={1: tool}, solid_geometry=tool.get('solid_geometry', []),
                           obj_options=dict(tool['data'], name=name, xmin=0, ymin=0, xmax=10, ymax=5))


def plugin(objects, multigeo=True, **options):
    """
    The Milling plugin with its real form and signals, and several Geometry targets. Selecting a target in the Target
    combo box runs on_object_changed(), as the project tree selection does.
    """
    from unittest.mock import MagicMock
    from PyQt6 import QtGui
    from appPlugins.ToolMilling import MillingUI

    harness, jobs = milling_harness(saved_tool(), multigeo, **options)
    milling = QtMilling()
    vars(milling).update(vars(harness))
    app = milling.app
    app.app_units = 'MM'
    app.resource_location = os.path.abspath('assets/resources')
    app.proj_selection_changed = MagicMock()
    app.exc_areas = MagicMock(exclusion_areas_storage=[], travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    app.ui = SimpleNamespace(notebook=SimpleNamespace(tabText=lambda _index: 'Milling'))
    app.collection = QtGui.QStandardItemModel()
    app.collection.get_by_name = lambda name: objects[name]
    app.collection.set_all_inactive = lambda: None
    app.collection.set_active = lambda _name: None
    milling.ui = MillingUI(layout=QtWidgets.QVBoxLayout(), app=app, name='Milling')
    ui = milling.ui
    ui.object_combo = QtWidgets.QComboBox()      # the app's combo shows the project tree; this one the test targets
    ui.object_combo.addItems(list(objects))
    ui.target_radio.set_value('geo')
    ui.pp_geo_name_cb.clear()
    ui.pp_geo_name_cb.addItems(list(app.preprocessors))
    ui.pp_geo_name_cb.set_value(app.options['tools_mill_ppname_g'])
    milling.form_fields = {
        "tools_mill_offset_type": ui.offset_type_combo, "tools_mill_offset_value": ui.offset_entry,
        "tools_mill_tool_shape": ui.tool_shape_combo, "tools_mill_job_type": ui.job_type_combo,
        "tools_mill_cutz": ui.cutz_entry, "tools_mill_multidepth": ui.mpass_cb,
        "tools_mill_depthperpass": ui.maxdepth_entry, "tools_mill_travelz": ui.travelz_entry,
        "tools_mill_feedrate": ui.xyfeedrate_entry, "tools_mill_feedrate_z": ui.feedrate_z_entry,
        "tools_mill_feedrate_rapid": ui.feedrate_rapid_entry, "tools_mill_spindlespeed": ui.spindlespeed_entry,
    }
    milling.general_form_fields = {
        "tools_mill_toolchange": ui.toolchange_cb, "tools_mill_toolchangez": ui.toolchangez_entry,
        "tools_mill_toolchangexy": ui.toolchangexy_entry, "tools_mill_endz": ui.endz_entry,
        "tools_mill_endxy": ui.endxy_entry, "tools_mill_z_p_depth": ui.pdepth_entry,
        "tools_mill_feedrate_probe": ui.feedrate_probe_entry, "tools_mill_ppname_g": ui.pp_geo_name_cb,
        "tools_mill_area_exclusion": ui.exclusion_cb, "tools_mill_area_shape": ui.area_shape_radio,
        "tools_mill_area_strategy": ui.strategy_radio, "tools_mill_area_overz": ui.over_z_entry,
        "tools_mill_optimization_type": ui.opt_algorithm_radio, "tools_mill_search_time": ui.opt_time_entry,
    }
    milling.name2option = {widget.objectName(): key for key, widget in
                           {**milling.form_fields, **milling.general_form_fields}.items() if widget.objectName()}
    milling.plot_cb_handler = lambda: None
    milling.jobs = jobs
    milling.ui_connect()
    return milling


def select(milling, name):
    milling.ui.object_combo.setCurrentText(name)
    milling.on_object_changed()


def generate(milling):
    """Generate CNCJob for the selected target: all its tools, as on_generate_cnc_from_geo() selects them."""
    milling.sel_tools.clear()
    milling.sel_tools.update(deepcopy(milling.target_obj.tools))
    milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False)
    return milling.jobs[-1]


def form_values(milling, keys):
    fields = {**milling.form_fields, **milling.general_form_fields}
    return {key: fields[key].get_value() for key in keys}


def test_multigeo_job_uses_the_settings_of_the_selected_target():
    objects = {'A': make_object('A', **LASER), 'B': make_object('B', **MILL)}
    milling = plugin(objects)
    select(milling, 'A')
    assert 'laser' in generate(milling).pp_geometry_name
    select(milling, 'B')
    job = generate(milling)
    assert job.pp_geometry_name == 'default'
    assert 'G01 Z-1.8000' in job.source_file


def test_form_edit_goes_to_the_target_only():
    objects = {'A': make_object('A', **LASER), 'B': make_object('B', **MILL)}
    milling = plugin(objects)
    select(milling, 'B')
    milling.general_form_fields['tools_mill_endz'].set_value(33.0)
    field = milling.general_form_fields['tools_mill_endz']
    field.objectName = lambda: 'endz'
    milling.sender = lambda: field
    milling.name2option = {'endz': 'tools_mill_endz'}
    milling.form_to_storage()
    assert objects['B'].tools[1]['data']['tools_mill_endz'] == 33.0
    assert objects['A'].tools[1]['data']['tools_mill_endz'] == 20.0
    select(milling, 'A')
    assert form_values(milling, ['tools_mill_endz']) == {'tools_mill_endz': 20.0}
    assert generate(milling).pp_geometry_name == 'GRBL_laser'


def test_old_project_target_resets_the_general_fields():
    # the object saved by FlatCAM 8.994 keeps the settings under old names (ppname_g, endz): the form shows them
    old = make_object('cutout', tool=flatcam_8_994_cutout_tool())
    old.obj_options = dict(MILL_OPTIONS, name='cutout', xmin=0, ymin=0, xmax=10, ymax=5)
    objects = {'iso': make_object('iso', **LASER), 'cutout': old}
    milling = plugin(objects, tools_mill_ppname_g='GRBL_laser')
    select(milling, 'iso')
    select(milling, 'cutout')
    assert milling.ui.pp_geo_name_cb.get_value() == 'default'
    assert milling.ui.endz_entry.get_value() == 15.0
    assert generate(milling).pp_geometry_name == 'default'


# ---- the tools table of the plugin: copy, delete, add, edit, Excellon order ----

def tools_milling(n_tools=2, diameters=None):
    """The plugin with one Geometry target of n tools; the table shows them."""
    tools = {}
    for uid in range(1, n_tools + 1):
        tool = saved_tool(tools_mill_tooldia=float(uid))
        tool['tooldia'] = float(uid)
        tools[uid] = tool
    obj = make_object('A')
    obj.tools = tools
    obj.build_ui = lambda: None
    obj.ser_attrs = []
    milling = plugin({'A': obj})
    milling.build_ui_sig = SimpleNamespace(emit=milling.build_ui)
    milling.default_data = dict(MILL_OPTIONS, tools_mill_tooldia=1.0)
    milling.app.plot_all = lambda *a, **k: None
    select(milling, 'A')
    return milling, obj


def select_rows(milling, rows):
    table = milling.ui.tools_table_mill_geo
    table.clearSelection()
    for row in rows:
        table.selectionModel().select(
            table.model().index(row, 0),
            QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows)


def test_delete_removes_only_the_selected_rows():
    milling, obj = tools_milling(3)
    select_rows(milling, [0, 2])
    milling._on_tool_delete()
    assert sorted(obj.tools) == [2]


def test_new_tool_cuts_with_its_own_diameter():
    milling, obj = tools_milling(1)
    milling._on_tool_default_add(dia=0.5)
    assert obj.tools[2]['tooldia'] == 0.5 and obj.tools[2]['data']['tools_mill_tooldia'] == 0.5
    milling._on_tool_from_db_inserted({'tooldia': 0.8, 'data': {'tools_mill_cutz': -0.3}})
    new = obj.tools[3]
    assert new['data']['tools_mill_tooldia'] == 0.8 and new['data']['tools_mill_cutz'] == -0.3
    assert new['data']['tools_mill_feedrate'] == MILL_OPTIONS['tools_mill_feedrate']


def test_diameter_zero_is_rejected_in_the_table():
    milling, obj = tools_milling(1)
    item = milling.ui.tools_table_mill_geo.item(0, 1)
    item.setText('0')
    assert obj.tools[1]['tooldia'] == 1.0 and obj.tools[1]['data']['tools_mill_tooldia'] == 1.0
    assert float(milling.ui.tools_table_mill_geo.item(0, 1).text()) == 1.0


def exc_milling(diameters):
    milling, _obj = tools_milling(1)
    tools = {i: {'tooldia': dia, 'drills': [object()] * 2, 'slots': []} for i, dia in enumerate(diameters, 1)}
    milling.app.dec_format = lambda value, decimals=4: round(value, decimals)
    milling.target_obj = SimpleNamespace(tools=tools)
    milling.ui.order_combo.blockSignals(True)
    return milling, tools


def exc_uids(milling):
    table = milling.ui.tools_table_mill_exc
    return [int(table.item(row, 3).text()) for row in range(table.rowCount() - 2)]


def test_excellon_table_lists_each_tool_once_and_keeps_the_object():
    milling, tools = exc_milling([0.8, 0.8, 0.8, 1.0])
    before = dict(tools)
    for _ in range(3):
        milling.build_ui_exc()
    assert exc_uids(milling) == [1, 2, 3, 4] and milling.target_obj.tools == before
    assert milling.ui.tools_table_mill_exc.item(4, 2).text() == '8'
    milling.ui.order_combo.set_value(2)
    milling.build_ui_exc()
    assert exc_uids(milling) == [4, 1, 2, 3] and milling.target_obj.tools == before
    assert milling.ui.tools_table_mill_exc.item(4, 2).text() == '8'
    assert milling.ui.tools_table_mill_exc.item(5, 4).text() == '0'


# ---- UI state of the tools table and the form ----

def v_index(milling):
    return milling.ui.tool_shape_combo.findText('V')


def click_row(milling, row):
    table = milling.ui.tools_table_mill_geo
    table.setCurrentCell(row, 0)
    milling.update_ui()


def test_level_change_does_not_write_the_tool_data():
    milling, obj = tools_milling(2)
    data = obj.tools[1]['data']
    data.update(tools_mill_offset_type=2, tools_mill_offset_value=0.3, tools_mill_job_type=3, tools_mill_extracut=True,
                tools_mill_dwell=True, tools_mill_area_exclusion=True)
    before = deepcopy(obj.tools)
    milling.on_level_changed(False)
    milling.on_level_changed(True)
    milling.on_level_changed(False)
    assert obj.tools == before


def test_clicking_a_row_keeps_the_cut_z_of_every_tool():
    milling, obj = tools_milling(2)
    obj.tools[1]['data'].update(tools_mill_tool_shape=v_index(milling), tools_mill_cutz=-0.2, tools_mill_vtipdia=0.1,
                                tools_mill_vtipangle=30.0)
    obj.tools[2]['data'].update(tools_mill_tool_shape=0, tools_mill_cutz=-1.0)
    click_row(milling, 0)
    click_row(milling, 1)
    assert obj.tools[2]['data']['tools_mill_cutz'] == -1.0
    assert obj.tools[1]['data']['tools_mill_cutz'] == -0.2
    assert milling.ui.cutz_entry.get_value() == -1.0
    assert milling.ui.tipdia_entry.isHidden()


def test_v_tip_edit_recomputes_the_cut_z_of_all_selected_v_tools():
    milling, obj = tools_milling(3)
    for uid in (1, 2):
        obj.tools[uid]['data'].update(tools_mill_tool_shape=v_index(milling), tools_mill_vtipangle=90.0)
    obj.tools[3]['data'].update(tools_mill_tool_shape=0, tools_mill_cutz=-1.0)
    click_row(milling, 0)
    select_rows(milling, [0, 1, 2])
    milling.ui.tipangle_entry.set_value(90.0)
    milling.ui.tipdia_entry.set_value(0.5)
    assert obj.tools[1]['data']['tools_mill_cutz'] == pytest.approx(-0.25)
    assert obj.tools[2]['data']['tools_mill_cutz'] == pytest.approx(-0.75)
    assert obj.tools[3]['data']['tools_mill_cutz'] == -1.0


def test_dragging_geometry_rows_reorders_the_tools_of_the_object():
    milling, obj = tools_milling(3)
    table = milling.ui.tools_table_mill_geo
    milling.ui_disconnect()
    for row, uid in enumerate((3, 1, 2)):
        table.item(row, 3).setText(str(uid))
    milling.on_geo_rebuild_ui()
    assert [obj.tools[uid]['tooldia'] for uid in (1, 2, 3)] == [3.0, 1.0, 2.0]


def excellon_generate(diameters, select_exc_rows=None):
    """The uids that Generate CNCJob hands to the Excellon paint step."""
    milling, tools = exc_milling(diameters)
    milling.build_ui_exc()
    target = milling.app.collection.get_by_name('A')
    target.kind, target.tools = 'excellon', tools
    table = milling.ui.tools_table_mill_exc
    table.clearSelection()
    for row in select_exc_rows or []:
        table.selectionModel().select(
            table.model().index(row, 0),
            QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows)
    painted = []
    milling.paint_excellon_cncjpb = lambda: painted.append(list(milling.sel_tools))
    milling.on_generate_cnc_from_exc()
    return painted


def test_excellon_generates_the_selected_tools_in_table_order():
    assert excellon_generate([0.8, 1.0, 1.2], select_exc_rows=[2, 0]) == [[1, 3]]


# ---- path optimization, mistakes in the settings ----


def told(milling, level):
    return ' '.join(str(c.args[0]) for c in milling.app.inform.emit.call_args_list
                    if c.args and str(c.args[0]).startswith('[%s' % level))


@pytest.mark.parametrize('changes, text', [
    (dict(tools_mill_tooldia=0.0), 'diameter must be above zero'),
    (dict(tools_mill_multidepth=True, tools_mill_depthperpass=0.0), 'Depth per Pass must be above zero'),
])
@pytest.mark.parametrize('multigeo', [True])
def test_a_tool_that_cannot_make_a_job_blocks_the_job(changes, text, multigeo):
    objects = {'A': make_object('A', multigeo=multigeo, **changes)}
    milling = plugin(objects, multigeo=multigeo)
    select(milling, 'A')
    milling.sel_tools.clear()
    milling.sel_tools.update(deepcopy(milling.target_obj.tools))
    assert milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False) == 'fail'
    assert not milling.jobs
    assert text in told(milling, 'ERROR')


# ---- the form of a laser tool, the tool number label, the Excellon totals ----
