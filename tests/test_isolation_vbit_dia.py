"""Isolation plugin: V-shape tool diameter (on_update_tool_dia, on_tool_add, on_tool_default_add).

The tests build the real ToolIsolation headless (offscreen Qt, MagicMock app) and drive the production methods.
"""

import json
import math
import os
import sys
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6 import QtWidgets, sip  # noqa: E402

from defaults import AppDefaults  # noqa: E402
from appPlugins.ToolIsolation import ToolIsolation  # noqa: E402
from test_plugin_geometry_bases import make_tool_app  # noqa: E402

V = ToolIsolation.V_SHAPE_INDEX if hasattr(ToolIsolation, 'V_SHAPE_INDEX') else 5


def v_dia(tip, angle, cut_z):
    return round(tip + 2 * abs(cut_z) * math.tan(math.radians(angle / 2)), 4)


def v_data(tip=0.1, angle=60, cut_z=-0.1, shape=V):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_mill_tool_shape=shape, tools_mill_vtipdia=tip, tools_mill_vtipangle=angle,
                tools_mill_cutz=cut_z)
    return data


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tool(qapp, tmp_path):
    app = make_tool_app()
    app.dec_format = lambda value, decimals=4: round(float(value), decimals)
    db_path = tmp_path / 'tools.FlatDB'
    db_path.write_text('{}', encoding='utf-8')
    app.tools_database_path = lambda: str(db_path)
    t = ToolIsolation(app)
    t.db_path = db_path
    t.decimals = 4
    t.default_data = v_data()
    t.ui.v_frame.show()
    # the frame is only "visible" when its parents are shown
    t.ui.v_frame.isVisible = lambda: True
    yield t
    # Destroy the signal-connected widgets while their QApplication and Python owners are still alive.
    sip.delete(t)


def add_tool_row(t, uid, dia, data=None):
    t.iso_tools[uid] = {'tooldia': dia, 'data': deepcopy(data if data is not None else t.default_data),
                        'solid_geometry': []}


def select_uid(t, uid):
    t.build_ui()
    for row in range(t.ui.tools_table.rowCount()):
        if int(t.ui.tools_table.item(row, 3).text()) == uid:
            t.ui.tools_table.setCurrentCell(row, 1)
            return row
    raise AssertionError('uid not in table')


# ------------------------------------------------------------------ helper

# ------------------------------------------------------------------ on_update_tool_dia

def test_update_changes_diameter(tool):
    add_tool_row(tool, 1, 0.5)
    row = select_uid(tool, 1)
    tool.ui.tipdia_entry.set_value(0.2)
    tool.ui.tipangle_entry.set_value(60)
    tool.ui.cutz_entry.set_value(-0.1)
    tool.on_update_tool_dia()
    expected = v_dia(0.2, 60, -0.1)
    assert tool.iso_tools[1]['tooldia'] == expected
    assert tool.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert float(tool.ui.tools_table.item(row, 1).text()) == expected


def test_update_clash_with_other_tool_warns_and_keeps(tool):
    original = v_dia(0.1, 60, -0.1)
    clash = v_dia(0.2, 60, -0.1)
    add_tool_row(tool, 1, original, v_data(tip=0.1))
    add_tool_row(tool, 2, clash, v_data(tip=0.2))
    row = select_uid(tool, 1)
    tool.ui.tipdia_entry.set_value(0.2)
    tool.ui.tipangle_entry.set_value(60)
    tool.ui.cutz_entry.set_value(-0.1)
    tool.app.inform.emit.reset_mock()
    tool.ui.tipdia_entry.returnPressed.emit()
    assert tool.app.inform.emit.call_count == 1
    assert 'WARNING' in tool.app.inform.emit.call_args[0][0]
    assert tool.iso_tools[1]['tooldia'] == original
    assert float(tool.ui.tools_table.item(row, 1).text()) == original
    # the rejected V parameters are not kept: the tool data and its diameter still describe the same cutter
    assert tool.iso_tools[1]['data']['tools_mill_vtipdia'] == 0.1
    assert tool.app.dec_format(tool.v_effective_diameter(tool.iso_tools[1]['data']), tool.decimals) == original
    # the V edit left the plugin free to pass the Optimal result to the UI
    tool.optimal_found_sig.emit(0.25)
    assert tool.ui.new_tooldia_entry.get_value() == pytest.approx(0.25)


# ------------------------------------------------------------------ add paths

def test_default_add_zero_dia_v_shape(tool):
    tool.on_tool_default_add(dia=0)
    assert list(tool.iso_tools) == [1]
    expected = v_dia(0.1, 60, -0.1)
    assert tool.iso_tools[1]['tooldia'] == expected
    assert tool.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert float(tool.ui.tools_table.item(0, 1).text()) == expected


def test_default_add_zero_dia_not_v_shape_warns(tool):
    tool.default_data = v_data(shape=0)
    tool.app.inform.emit.reset_mock()
    tool.on_tool_default_add(dia=0)
    assert tool.iso_tools == {}
    assert 'WARNING' in tool.app.inform.emit.call_args[0][0]


def test_add_zero_dia_final_diameter_from_matched_record(tool):
    provisional = v_dia(0.1, 60, -0.1)
    record = {'name': 'v', 'tooldia': provisional,
              'data': v_data(tip=0.2, angle=90, cut_z=-0.3)}
    record['data'].update(tool_target=3, tol_min=0.0, tol_max=0.0)
    other = {'name': 'other', 'tooldia': 5.0, 'data': v_data()}
    other['data'].update(tool_target=3, tol_min=0.0, tol_max=0.0)
    tool.db_path.write_text(json.dumps({'1': record, '2': other}), encoding='utf-8')

    tool.on_tool_add(custom_dia=0)

    expected = v_dia(0.2, 90, -0.3)
    assert expected != provisional
    assert tool.iso_tools[1]['tooldia'] == expected
    assert tool.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert tool.iso_tools[1]['data']['tools_mill_vtipdia'] == 0.2
    assert float(tool.ui.tools_table.item(0, 1).text()) == expected


def test_effective_diameter_rejects_impossible_tools():
    from appPlugins.ToolIsolation import IsolationGen
    for tip, angle, cut_z in [(0.1, 0, -0.1), (0.1, 180, -0.1), (0.1, 200, -0.1), (0.1, -30, -0.1),
                              (-0.1, 60, -0.1), (0.0, 60, 0.0)]:
        assert IsolationGen.v_effective_diameter(v_data(tip, angle, cut_z)) is None, (tip, angle, cut_z)
    assert IsolationGen.v_effective_diameter(v_data(0.1, 60, -0.1)) == pytest.approx(v_dia(0.1, 60, -0.1), abs=1e-4)


# ------------------------------------------------------------------ tool table / UI controller

def table_dias(t):
    return [float(t.ui.tools_table.item(r, 1).text()) for r in range(t.ui.tools_table.rowCount())]


def selected_uids(t):
    return sorted({int(t.ui.tools_table.item(i.row(), 3).text()) for i in t.ui.tools_table.selectedItems()})


@pytest.fixture
def plain(tool):
    tool.default_data = v_data(shape=0)
    tool.app.options = dict(AppDefaults.factory_defaults, global_app_level='a')
    tool.ui.iso_order_combo.set_value(2)  # Reverse: the table is sorted by diameter
    return tool


def test_added_tools_are_selected_under_a_sorted_table_and_duplicates_are_refused(plain):
    plain.on_tool_add(custom_dia=0.1)
    plain.on_tool_add(custom_dia=0.5)
    assert table_dias(plain) == [0.5, 0.1]
    uids = selected_uids(plain)
    assert len(uids) == 1 and plain.iso_tools[uids[0]]['tooldia'] == 0.5
    plain.on_tool_default_add(dia=0.3)
    uids = selected_uids(plain)
    assert len(uids) == 1 and plain.iso_tools[uids[0]]['tooldia'] == 0.3

    for add in (lambda: plain.on_tool_add(custom_dia=0.1), lambda: plain.on_tool_default_add(dia=0.1)):
        plain.app.inform.emit.reset_mock()
        add()
        assert len(plain.iso_tools) == 3
        assert 'Tool already in Tool Table' in plain.app.inform.emit.call_args[0][0]

    db_tool = {'tooldia': 0.4, 'data': {'tool_target': 3, 'tools_iso_passes': 4}}
    uid = plain.on_tool_from_db_inserted(db_tool)
    assert uid not in ('fail', True) and plain.iso_tools[uid]['tooldia'] == 0.4
    assert selected_uids(plain) == [uid]
    data = plain.iso_tools[uid]['data']
    assert data['tools_iso_passes'] == 4
    assert data['tools_iso_overlap'] == plain.default_data['tools_iso_overlap']
    assert data['tools_iso_tooldia'] == 0.4
    assert plain.on_tool_from_db_inserted(db_tool) == 'fail'
    assert len(plain.iso_tools) == 4


