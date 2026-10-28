"""Editing in the FlatCAM input widgets (GUIElements): the number fields and the tool tables."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtTest import QTest

from appGUI.GUIElements import FCDoubleSpinner, FCSpinner, FCTable


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spinner_with_800(cls):
    """A spinner with the range 0 to 1000 and the value 800, its text selected for typing."""
    spinner = cls()
    spinner.set_range(0, 1000)
    spinner.set_value(800)
    spinner.lineEdit().selectAll()
    return spinner


@pytest.mark.parametrize('cls', [FCSpinner, FCDoubleSpinner])
def test_typed_number_over_the_maximum_keeps_the_previous_value(cls):
    spinner = spinner_with_800(cls)
    values = []
    spinner.valueChanged.connect(values.append)

    QTest.keyClicks(spinner.lineEdit(), '1500')
    QTest.keyClick(spinner.lineEdit(), QtCore.Qt.Key.Key_Return)

    assert spinner.get_value() == 800  # not 150, the last prefix in the range
    assert values == []  # the prefixes 1, 15 and 150 were not committed


@pytest.mark.parametrize('cls', [FCSpinner, FCDoubleSpinner])
def test_typed_number_reaches_the_slots_of_the_keypad_enter(cls):
    spinner = spinner_with_800(cls)
    seen = []
    spinner.returnPressed.connect(lambda: seen.append(spinner.get_value()))

    QTest.keyClicks(spinner.lineEdit(), '950')
    QTest.keyClick(spinner, QtCore.Qt.Key.Key_Enter)

    assert spinner.get_value() == 950
    assert seen and set(seen) == {950}


class DragFromTable:
    """A drag enter event of a drag that started in the table (a real event has a source only during a drag)."""

    def __init__(self, table):
        self.table = table

    def source(self):
        return self.table

    def accept(self):
        pass


def tool_table():
    table = FCTable(drag_drop=True)
    table.setColumnCount(2)
    table.setRowCount(2)
    for row in range(2):
        for column in range(2):
            table.setItem(row, column, QtWidgets.QTableWidgetItem('%d' % (row + 1)))
    return table


def test_drag_that_leaves_the_tool_table_unblocks_its_signals():
    table = tool_table()
    edits = []
    table.itemChanged.connect(edits.append)

    table.dragEnterEvent(DragFromTable(table))
    assert table.signalsBlocked()
    table.dragLeaveEvent(QtGui.QDragLeaveEvent())  # the mouse is released outside the table, or Esc
    table.item(0, 1).setText('0.15')

    assert not table.signalsBlocked()
    assert edits == [table.item(0, 1)]


def test_drag_that_ends_without_a_drop_unblocks_the_signals(monkeypatch):
    def drag_without_drop(view, supported_actions):
        view.dragEnterEvent(DragFromTable(view))  # QDrag.exec() returns without a drop or leave event

    monkeypatch.setattr(QtWidgets.QTableWidget, 'startDrag', drag_without_drop)
    table = tool_table()

    table.startDrag(QtCore.Qt.DropAction.MoveAction)

    assert not table.signalsBlocked()
