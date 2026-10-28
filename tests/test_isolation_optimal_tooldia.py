"""Isolation: safe tool recommendations must agree with Check validity.

The distances between the copper polygons are rounded to the app decimals, so the smallest one can be up to half a unit
of the last decimal larger than the real gap (0.15996 -> 0.16). The tests build the real plugins headless
(offscreen Qt, MagicMock app) and drive the Optimal button and the Check validity step
(find_safe_tooldia_multiprocessing) on pads with known gaps.
"""

import functools
import os
import sys
import threading
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402
from shapely import MultiPolygon, box  # noqa: E402

from appMain import App  # noqa: E402
from defaults import AppDefaults  # noqa: E402
from appPlugins.ToolIsolation import ToolIsolation  # noqa: E402
from appPlugins.ToolOptimal import ToolOptimal  # noqa: E402
from test_plugin_geometry_bases import make_tool_app  # noqa: E402

DEC = 4
UNIT = 10 ** -DEC


def gerber_tools(gap):
    # 3 pads; the smallest gap is between the first 2
    pads = [box(0, 0, 1, 1), box(1 + gap, 0, 2, 1), box(0, 1 + 3 * gap, 1, 2)]
    return {10: {'geometry': [{'solid': p} for p in pads]}}


@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tool(qapp):
    app = make_tool_app()
    app.abort_flag = False
    app.dec_format = functools.partial(App.dec_format, app)
    # run the jobs in place of the worker thread and of the process pool
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.pool.apply_async.side_effect = lambda fcn, args: MagicMock(get=lambda: fcn(*args))
    t = ToolIsolation(app)
    t.decimals = DEC
    app.collection = MagicMock()
    return t


def find_optimal(t, gap):
    t.app.collection.get_by_name.return_value = MagicMock(tools=gerber_tools(gap))
    found = []
    t.optimal_found_sig.connect(found.append)
    t.on_find_optimal_tooldia()
    t.optimal_found_sig.disconnect(found.append)
    return found[0] if found else None


def messages(t):
    return [c.args[0] for c in t.app.inform.emit.call_args_list]


@pytest.mark.parametrize('gap', [0.15996, 0.20004, 0.30005])
def test_optimal_tool_fits_in_the_smallest_gap(tool, gap):
    dia = find_optimal(tool, gap)
    assert dia is not None
    assert dia < gap
    assert dia >= gap - 1.5 * UNIT - 1e-12
    assert dia == round(dia, DEC)
    assert tool.ui.new_tooldia_entry.get_value() == dia


def test_gap_rounded_up_gives_a_tool_below_the_gap(tool):
    # 0.15996 is rounded to 0.1600; the Optimal button proposed a 0.16 tool, larger than the gap
    assert find_optimal(tool, 0.15996) == 0.1599
    assert any('0.1599' in m for m in messages(tool) if m.startswith('[success]'))


@pytest.mark.parametrize('gap, decimals, units', [(0.15996, 4, 'MM'), (0.20000051, 6, 'MM'), (0.0079945, 4, 'IN')])
def test_optimal_tool_passes_the_check_validity(tool, gap, decimals, units):
    tool.decimals = decimals
    tool.app.decimals = decimals
    tool.app.app_units = units
    dia = find_optimal(tool, gap)
    assert dia is not None
    assert 0 < dia < gap
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    setattr(tool, 'iso_tools', {1: {'tooldia': dia, 'data': data, 'solid_geometry': []}})
    tool.build_ui()
    tool.app.inform.emit.reset_mock()
    tool.validation_status = True

    tool.find_safe_tooldia_multiprocessing()

    assert tool.validation_status is True
    assert 'Tool validation passed.' in messages(tool)
    assert not any(m.startswith('[WARNING]') for m in messages(tool))


def test_gap_below_the_resolution_gives_no_tool(tool):
    assert find_optimal(tool, 0.2) is not None
    tool.app.inform.emit.reset_mock()
    assert find_optimal(tool, 0.00004) is None
    assert tool.safe_tooldia is None
    assert any(m.startswith('[ERROR_NOTCL]') for m in messages(tool))


def test_validation_rejects_unrepresentable_gap(tool):
    msg, _ = tool.find_optim_mp(gerber_tools(0.00004), DEC)
    assert msg.startswith('[ERROR_NOTCL]')


def test_validation_rejects_oversized_tool(tool):
    dia = find_optimal(tool, 0.15996)
    assert dia is not None
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    setattr(tool, 'iso_tools', {1: {'tooldia': dia + UNIT, 'data': data, 'solid_geometry': []}})
    tool.build_ui()
    tool.app.inform.emit.reset_mock()
    tool.find_safe_tooldia_multiprocessing()
    assert any(m.startswith('[WARNING]') for m in messages(tool))
    assert 'Tool validation passed.' not in messages(tool)


@pytest.mark.parametrize('gap', [0.15996, 0.00004, 0.0001])
def test_standalone_optimal_reports_only_positive_safe_diameters(qapp, gap):
    app = make_tool_app()
    app.abort_flag = False
    app.dec_format = functools.partial(App.dec_format, app)
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    t = ToolOptimal(app)
    t.ui.precision_spinner.set_value(DEC)
    app.collection = MagicMock()
    obj = app.collection.index.return_value.internalPointer.return_value.obj
    obj.kind = 'gerber'
    obj.tools = gerber_tools(0.2)
    t.find_minimum_distance()
    assert float(t.ui.result_entry.get_value()) > 0
    app.inform.emit.reset_mock()
    app.proc_container.new.return_value.done.reset_mock()
    obj.tools = gerber_tools(gap)
    t.find_minimum_distance()
    if gap > UNIT:
        assert 0 < float(t.ui.result_entry.get_value()) < gap
    else:
        assert t.ui.result_entry.get_value() == ''
        assert any(m.startswith('[ERROR_NOTCL]') for m in messages(t))
        assert not any(m.startswith('[success]') for m in messages(t))
    app.proc_container.new.return_value.done.assert_called_once()


def test_isolation_runs_after_the_check_even_if_tasks_are_reordered(qapp):
    # the check and the isolation are one worker task: the isolation always reads the result of the check
    from tests.helpers_objects import make_app, make_gerber
    gerber = make_gerber(make_app(), name='g')
    app = make_tool_app()
    app.dec_format = functools.partial(App.dec_format, app)
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    t = ToolIsolation(app)
    t.decimals = DEC
    app.collection = MagicMock()
    app.collection.get_by_name.return_value = gerber
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    t.iso_tools = {1: {'tooldia': 5.0, 'data': data, 'solid_geometry': []}}
    t.build_ui()
    t.ui.tools_table.selectAll()
    t.ui.valid_cb.set_value(True)

    t.on_iso_button_click()
    for task in reversed(tasks):
        task['fcn'](*task['params'])

    # a 5 mm tool does not fit between the copper features: the object is not selected
    assert any(m.startswith('[WARNING]') for m in messages(t))
    assert app.app_obj.new_object.call_args.kwargs['autoselected'] is False


def standalone_optimal(tools):
    # the Find Optimal plugin with the job run in place of the worker thread
    app = make_tool_app()
    app.dec_format = functools.partial(App.dec_format, app)
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    t = ToolOptimal(app)
    t.ui.precision_spinner.set_value(DEC)
    app.collection = MagicMock()
    obj = app.collection.index.return_value.internalPointer.return_value.obj
    obj.kind = 'gerber'
    obj.tools = tools
    return t


def click_line(text_box, move):
    with QtCore.QSignalBlocker(text_box):
        text_box.moveCursor(move)


def test_standalone_optimal_click_on_the_empty_last_line_of_the_distances(qapp):
    t = standalone_optimal(gerber_tools(0.2))
    t.find_minimum_distance()
    distances = t.ui.distances_textb
    assert distances.toPlainText().endswith('\n')
    click_line(distances, QtGui.QTextCursor.MoveOperation.Start)
    t.on_distances_textb_clicked()
    shown = t.ui.locations_sec_textb.toPlainText()
    assert shown.count('\n') == 1
    # the empty line after the last distance raised ValueError: the 'Critical Error' dialog
    click_line(distances, QtGui.QTextCursor.MoveOperation.End)
    t.on_distances_textb_clicked()
    assert t.ui.locations_sec_textb.toPlainText() == shown


def test_standalone_optimal_sets_the_result_entries_in_the_gui_thread(qapp):
    t = standalone_optimal(gerber_tools(0.2))
    tasks = []
    t.app.worker_task.emit.side_effect = tasks.append
    t.find_minimum_distance()
    threads = []
    for entry in (t.ui.result_entry, t.ui.freq_entry):
        entry.set_value = lambda value, set_value=entry.set_value: (
            threads.append(threading.current_thread()), set_value(value))
    worker = threading.Thread(target=tasks[0]['fcn'], args=tasks[0]['params'])
    worker.start()
    worker.join()
    qapp.processEvents()
    # Qt widgets are not thread-safe: the job set the entries from the worker thread
    assert threads == [threading.main_thread()] * 2
    assert 0 < float(t.ui.result_entry.get_value()) < 0.2
    assert t.ui.freq_entry.get_value() == '1'


def test_standalone_optimal_with_a_multi_part_solid(qapp):
    # a shape split by a negative Etch Compensation is 1 MultiPolygon solid: the search stopped without a message
    tools = gerber_tools(0.2)
    tools[10]['geometry'].append({'solid': MultiPolygon([box(0, 5, 1, 6), box(1.15, 5, 2, 6)])})
    t = standalone_optimal(tools)
    t.find_minimum_distance()
    assert (t.ui.result_entry.get_value(), t.ui.freq_entry.get_value()) == ('0.1499', '1')
    assert any(m.startswith('[success]') for m in messages(t))
