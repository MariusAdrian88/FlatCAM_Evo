"""App Undo / Redo history of the Milling plugin: the tool table of an existing Geometry / Excellon target."""

import os
import sys
import time
from copy import deepcopy

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import box

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    # the history records nothing in headless mode; these tests need it
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def no_plot(monkeypatch):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()


def _settle(fc, timeout=5.0):
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _pump(n=10):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _select_row(table, row):
    """Select only this row. selectRow() reads the keyboard modifiers of the application: a Shift or Ctrl state
    that an earlier test left behind extends the selection, and a later delete removes more than 1 tool."""
    table.clearSelection()
    table.selectionModel().select(
        table.model().index(row, 0),
        QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect | QtCore.QItemSelectionModel.SelectionFlag.Rows)


def _load_exc(fc, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, 'unify_mixed.drl'))
        obj.create_geometry()
    return fc.app_obj.new_object('excellon', name, init, plot=False)


def _new_geo(fc, name):

    def init(obj, _app):
        obj.solid_geometry = box(0, 0, 10, 10)
    return fc.app_obj.new_object('geometry', name, init, plot=False)


def _tools(obj):
    """tools without the geometries (keys, tooldia, data), comparable."""
    return {int(k): {'tooldia': float(v['tooldia']), 'data': deepcopy(v['data'])} for k, v in obj.tools.items()}


def _open(fc, kind, obj):
    """Open the plugin on the target kind and select the object (what the user does)."""
    mt = fc.milling_tool
    if not getattr(fc, "_milling_test_open", False):   # run(toggle=True) a second time would close the plugin
        mt.run(toggle=True)
        fc._milling_test_open = True
    _pump()
    mt.ui.target_radio.set_value(kind)
    _pump()
    mt.ui.object_combo.setCurrentIndex(mt.ui.object_combo.findText(obj.obj_options['name']))
    mt.on_object_changed()
    _pump()
    return mt


def _steps(fc):
    return len(fc.history.undo_stack)


def test_geo_tool_actions_undo_redo(fc, no_plot):
    geo = _new_geo(fc, 'm_geo_actions')
    _settle(fc)
    mt = _open(fc, 'geo', geo)
    assert mt.target_obj is geo

    # add
    s0 = _tools(geo)
    n = _steps(fc)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    assert _steps(fc) == n + 1
    s1 = _tools(geo)
    assert len(s1) == len(s0) + 1
    assert fc.history.undo() and _tools(geo) == s0
    assert fc.history.redo() and _tools(geo) == s1

    # a second tool through the Add button path
    n = _steps(fc)
    mt.on_tool_add(dia=0.8)
    _pump()
    assert _steps(fc) == n + 1
    s2 = _tools(geo)
    assert len(s2) == len(s1) + 1

    # copy
    _select_row(mt.ui.tools_table_mill_geo, 0)
    n = _steps(fc)
    mt.on_tool_copy()
    _pump()
    assert _steps(fc) == n + 1
    s3 = _tools(geo)
    assert len(s3) > len(s2)
    assert fc.history.undo() and _tools(geo) == s2
    assert fc.history.redo() and _tools(geo) == s3

    # edit diameter
    n = _steps(fc)
    item = mt.ui.tools_table_mill_geo.item(0, 1)
    item.setText('%.4f' % 1.25)
    _pump()
    assert _steps(fc) == n + 1
    s4 = _tools(geo)
    assert s4 != s3
    assert fc.history.undo() and _tools(geo) == s3
    assert fc.history.redo() and _tools(geo) == s4

    # apply to all
    _select_row(mt.ui.tools_table_mill_geo, 0)
    first_uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    geo.tools[first_uid]['data']['tools_mill_cutz'] = -0.77
    s4 = _tools(geo)
    n = _steps(fc)
    mt.on_apply_param_to_all_clicked()
    _pump()
    assert _steps(fc) == n + 1
    s5 = _tools(geo)
    assert all(v['data']['tools_mill_cutz'] == -0.77 for v in s5.values())
    assert fc.history.undo() and _tools(geo) == s4
    assert fc.history.redo() and _tools(geo) == s5

    # delete one, then all
    _select_row(mt.ui.tools_table_mill_geo, 0)
    n = _steps(fc)
    mt.on_tool_delete()
    _pump()
    assert _steps(fc) == n + 1
    s6 = _tools(geo)
    assert len(s6) == len(s5) - 1
    assert fc.history.undo() and _tools(geo) == s5
    assert fc.history.redo() and _tools(geo) == s6

    n = _steps(fc)
    mt.on_tool_delete(all_tools=True)
    _pump()
    assert _steps(fc) == n + 1
    assert not geo.tools
    assert fc.history.undo() and _tools(geo) == s6


def test_geo_field_edit_is_one_undo_step(fc, no_plot):
    geo = _new_geo(fc, 'm_geo_edits')
    _settle(fc)
    mt = _open(fc, 'geo', geo)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    _select_row(mt.ui.tools_table_mill_geo, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    n = _steps(fc)
    for k, v in enumerate((-0.1, -0.2, -0.3), 1):
        mt.ui.cutz_entry.set_value(v)
        mt.ui.cutz_entry.returnPressed.emit()
        _pump()
        assert _steps(fc) == n + k
    assert geo.tools[uid]['data']['tools_mill_cutz'] == -0.3
    assert fc.history.undo() and geo.tools[uid]['data']['tools_mill_cutz'] == -0.2
    assert fc.history.redo() and geo.tools[uid]['data']['tools_mill_cutz'] == -0.3


def test_v_shape_cut_z_follows_undo_redo(fc, no_plot):
    """The Cut Z that a V shape (or a V-tip edit) calculates is in the same Undo step as the edit."""
    geo = _new_geo(fc, 'm_geo_vshape')
    _settle(fc)
    mt = _open(fc, 'geo', geo)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    _select_row(mt.ui.tools_table_mill_geo, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    geo.tools[uid]['data']['tools_mill_cutz'] = -2.4
    before = _tools(geo)

    mt.ui.tool_shape_combo.setCurrentIndex(mt.ui.tool_shape_combo.findText('V'))
    _pump()
    v_shape = _tools(geo)
    assert v_shape[uid]['data']['tools_mill_cutz'] != -2.4
    assert fc.history.undo() and _tools(geo) == before
    assert fc.history.redo() and _tools(geo) == v_shape

    mt.ui.tipdia_entry.set_value(float(mt.ui.tipdia_entry.get_value()) + 0.05)
    _pump()
    v_tip = _tools(geo)
    assert v_tip[uid]['data']['tools_mill_cutz'] != v_shape[uid]['data']['tools_mill_cutz']
    assert fc.history.undo() and _tools(geo) == v_shape
    assert fc.history.redo() and _tools(geo) == v_tip


def test_exc_field_edits_and_order(fc, no_plot):
    exc = _load_exc(fc, 'm_exc_edits')
    _settle(fc)
    mt = _open(fc, 'exc', exc)
    assert mt.target_obj is exc and len(exc.tools) >= 2
    _select_row(mt.ui.tools_table_mill_exc, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_exc.item(0, 3).text())
    n = _steps(fc)
    mt.ui.cutz_entry.set_value(-0.6)
    mt.ui.cutz_entry.returnPressed.emit()
    _pump()
    assert exc.tools[uid]['data']['tools_mill_cutz'] == -0.6
    assert _steps(fc) == n + 1

    # the Order combo only orders the table rows: the tools of the object and the history stay as they are
    before = _tools(exc)
    n = _steps(fc)
    mt.ui.order_combo.set_value(2)
    _pump()
    mt.ui.order_combo.set_value(0)
    _pump()
    assert _tools(exc) == before
    assert _steps(fc) == n
