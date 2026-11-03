"""Milling Tool: 'Apply parameters to all tools' reads the tools table of the current target."""
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtWidgets

from appGUI.GUIElements import FCTable
from appPlugins.ToolMilling import ToolMilling


class MillingHarness(SimpleNamespace):
    """Run the real controller methods without constructing its application GUI."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))


@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def tools_table(uids, totals=False):
    """A tools table with the tool UID in column 3; the Excellon table ends with 2 rows of totals (no UID)."""
    table = QtWidgets.QTableWidget(len(uids) + (2 if totals else 0), 5)
    for row, uid in enumerate(uids):
        table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(row + 1)))
        table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(uid)))
    if totals:
        for row in (len(uids), len(uids) + 1):
            table.setItem(row, 1, QtWidgets.QTableWidgetItem('Total'))
    return table


def harness(target, geo_table, exc_table, tool_uids):
    events, calls = [], []
    milling = MillingHarness()
    milling.app = SimpleNamespace(log=SimpleNamespace(debug=lambda *_: None),
                                  inform=SimpleNamespace(emit=events.append))
    milling.ui = SimpleNamespace(target_radio=SimpleNamespace(get_value=lambda: target),
                                 tools_table_mill_geo=geo_table, tools_table_mill_exc=exc_table)
    milling.target_obj = SimpleNamespace(tools={uid: {'data': {'tools_mill_feedrate': 100.0 * uid}}
                                                for uid in tool_uids})
    milling.ui_disconnect = lambda: calls.append('disconnect')
    milling.ui_connect = lambda: calls.append('connect')
    return milling, events, calls


def feedrates(milling):
    return [record['data']['tools_mill_feedrate'] for record in milling.target_obj.tools.values()]


@pytest.mark.parametrize('exc_uids', [[], [1, 2, 3]], ids=['new_tool', 'after_excellon'])
def test_geometry_target_uses_the_geometry_tools_table(qapp, exc_uids):
    # the Excellon table is hidden for a Geometry target: it holds its 2 rows of totals (a tool opened on a
    # Geometry) or the rows of the Excellon object milled before
    geo_table = tools_table([1, 2, 3])
    geo_table.setCurrentCell(1, 0)
    milling, events, calls = harness('geo', geo_table, tools_table(exc_uids, totals=True), [1, 2, 3])
    milling.on_apply_param_to_all_clicked()
    assert feedrates(milling) == [200.0, 200.0, 200.0]
    assert calls == ['disconnect', 'connect']
    assert any('applied to all tools' in str(msg) for msg in events)


def test_excellon_target_uses_the_excellon_tools_table(qapp):
    exc_table = tools_table([1, 2], totals=True)
    exc_table.setCurrentCell(1, 0)
    milling, events, calls = harness('exc', tools_table([]), exc_table, [1, 2])
    milling.on_apply_param_to_all_clicked()
    assert feedrates(milling) == [200.0, 200.0]
    assert calls == ['disconnect', 'connect']

