"""Guard bool-emitting Qt signals from silently changing command options."""

from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from appHandlers.appSignalConnector import AppSignalConnector


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize('signal_type,receiver_kind', [('action', 'slot'), ('button', 'adapter')])
def test_discard_adapter_keeps_command_default_and_checked_receiver_gets_state(qt_app, signal_type, receiver_kind):
    calls, states = [], []

    def command(use_thread=True):
        calls.append(use_thread)

    class Commands(QtCore.QObject):
        @QtCore.pyqtSlot()
        def command(self, use_thread=True):
            calls.append(use_thread)

    commands = Commands()
    sender = QtGui.QAction('Command') if signal_type == 'action' else QtWidgets.QPushButton('Command')
    sender.setCheckable(True)
    signal = sender.triggered if signal_type == 'action' else sender.clicked
    if receiver_kind == 'slot':
        signal.connect(commands.command)
    else:
        signal.connect(lambda: command())
    signal.connect(states.append)
    invoke = sender.trigger if signal_type == 'action' else sender.click
    invoke()
    invoke()
    assert calls == [True, True]
    assert states == [True, False]


@pytest.mark.parametrize('registration, action_name', [
    ('connect_editmenu_signals', 'menuedit_center_in_origin'),
    ('connect_toolbar_signals', 'replot_btn'),
])
def test_real_registered_actions_keep_command_defaults(qt_app, registration, action_name):
    app = Mock()
    action = QtGui.QAction('Command')
    setattr(app.ui, action_name, action)
    app.ui.pop_menucolor.actions.return_value = []
    calls = []

    def plot_all(fit_view=True, muted=False, use_thread=True):
        calls.append((fit_view, use_thread))

    def on_custom_origin(use_thread=True):
        calls.append((True, use_thread))

    app.plot_all = plot_all
    app.edit_class.on_custom_origin = on_custom_origin
    connector = AppSignalConnector(app)
    getattr(connector, registration)()
    action.trigger()
    assert calls == [(True, True)]
