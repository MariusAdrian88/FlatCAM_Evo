"""Undo / Redo history of the Drilling plugin: a user edit of excellon_obj.tools is 1 step, a UI rebuild adds none."""

import os
import sys
import time
from copy import deepcopy

import pytest
from PyQt6 import QtWidgets

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


def _load(fc, kind, filename, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, filename))
        if kind == 'excellon':
            obj.create_geometry()
    return fc.app_obj.new_object(kind, name, init, plot=False)


def _settle(fc, timeout=5.0):
    """Wait for the tasks that an object rebuild starts (the history refuses Undo / Redo while the app is busy)."""
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _steps(fc):
    return len(fc.history.undo_stack)


def _open(fc, name):
    """Open the plugin on the object the way the user does: run() then a target change."""
    tool = fc.drilling_tool
    tool.run(toggle=False)
    tool.ui.object_combo.setCurrentText(name)
    tool.on_object_changed()
    tool.ui.tools_table.selectAll()
    return tool


def _field(tool):
    """A numeric per-tool option of the form and its widget."""
    key = 'tools_drill_cutz'
    assert key in tool.tool_form_fields
    return key, tool.tool_form_fields[key]


def _edit(tool, widget, value):
    widget.set_value(value)
    widget.editingFinished.emit()


def test_field_edit_and_tool_type_change_add_no_step(fc, no_plot, monkeypatch):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_field')
    tool = _open(fc, 'e_drl_field')
    assert tool.excellon_tools is exc.tools
    key, widget = _field(tool)
    uid = next(iter(exc.tools))
    n = _steps(fc)
    for v in (-1.5, -1.6, -1.7):
        _edit(tool, widget, v)
    assert exc.tools[uid]['data'][key] == -1.7      # the edit reaches the object
    assert _steps(fc) == n
    # the handler is not connected to a widget now; call it the way a cell combo would
    cw = QtWidgets.QComboBox()
    cw.addItems(['C', 'V'])
    cw.setCurrentText('V')
    tool.ui.tools_table.setCellWidget(0, 2, cw)
    monkeypatch.setattr(tool, 'sender', lambda: cw)
    tool.on_tooltable_cellwidget_change()
    assert _steps(fc) == n


def test_apply_to_all_is_one_step_with_exact_undo_redo(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_all')
    tool = _open(fc, 'e_drl_all')
    assert len(exc.tools) > 1
    key, widget = _field(tool)
    tool.ui.tools_table.selectRow(0)
    _edit(tool, widget, -1.1)
    first_uid = tool.ui.tools_table.item(0, 3).text()
    before = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    n = _steps(fc)
    tool.ui.tools_table.setCurrentCell(0, 0)
    tool.on_apply_param_to_all_clicked()
    assert _steps(fc) == n + 1
    after = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    assert all(d[key] == -1.1 for d in after.values()), first_uid
    assert fc.history.undo()
    assert {k: v['data'] for k, v in exc.tools.items()} == before
    assert fc.history.redo()
    assert {k: v['data'] for k, v in exc.tools.items()} == after
    # the plugin follows the restored dict: an edit after Undo still reaches the object
    assert fc.history.undo()
    assert tool.excellon_tools is exc.tools
    uid = next(iter(exc.tools))
    _edit(tool, widget, -2.5)
    assert exc.tools[uid]['data'][key] == -2.5


def test_edit_after_a_tools_db_load_does_not_reach_the_object_and_adds_no_step(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_detached')
    tool = _open(fc, 'e_drl_detached')
    key, widget = _field(tool)
    uid = next(iter(exc.tools))
    first = exc.tools[uid]['data'][key]
    tool.rebuild_ui()                           # a row drag and drop: the plugin keeps a copy
    assert tool.excellon_tools is not exc.tools
    n = _steps(fc)
    tool.ui.tools_table.selectAll()
    _edit(tool, widget, -3.3)
    assert _steps(fc) == n
    assert exc.tools[uid]['data'][key] == first
