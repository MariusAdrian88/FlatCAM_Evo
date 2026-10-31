"""Press/release UI keeps E-Cut continuous jogging leased and stock jogs finite."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6 import QtCore, QtWidgets
import pytest

from test_ecut_sender_integration import make_sender as _make_sender


_test_senders = []


def make_sender():
    tool = _make_sender()
    _test_senders.append(tool)
    return tool


@pytest.fixture(autouse=True)
def active_test_application(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'applicationState',
                        lambda: QtCore.Qt.ApplicationState.ApplicationActive)
    yield
    for tool in _test_senders:
        tool._stop_continuous_jog()
        tool._continuous_jog_timer.stop()
        tool._sim_ui_timer.stop()
        dock = tool.__dict__.get('_test_jog_dock')
        if dock is not None:
            dock.close()
            dock.deleteLater()
        tool.close()
        tool.deleteLater()
    _test_senders.clear()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def connected_sender(continuous=True):
    tool = make_sender()
    tool.units = 'MM'
    tool.app.app_units = 'MM'
    tool.isActiveWindow = lambda: True
    port = SimpleNamespace(is_open=True, write=MagicMock(), readline=MagicMock(return_value=b''),
                           close=MagicMock())
    if continuous:
        port.start_jog = MagicMock()
        port.stop_jog = MagicMock()
        port.refresh_jog = MagicMock(return_value=True)
        port.jogging = True
    tool.grbl_ser_port = port
    tool._grbl_connected = True
    tool._grbl_set_ui_connected(True)
    tool.ui.jog_fr_entry.set_value(120)
    tool.ui.jog_step_entry.set_value(1)
    from appPlugins.ToolGcodeSender.Sender import GrblConsole
    dock = QtWidgets.QDockWidget()
    tool._test_jog_dock = dock
    tool.app.ui.grbl_console_dock = dock
    console = GrblConsole(tool)
    dock.setWidget(console)
    console.set_jog_page(tool.ui.jog_page)
    console.tabs.setCurrentWidget(console.jog_page)
    dock.isActiveWindow = lambda: tool.isActiveWindow()
    dock.show()
    return tool, port



def test_press_starts_and_release_stops_without_finite_click():
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    tool._grbl_start_op = MagicMock()
    button = tool.ui.jog_wdg.jog_left_button
    button.setDown(True)
    button.pressed.emit()
    port.start_jog.assert_called_once_with(0, 1, 120)
    assert tool._grbl_op is not None
    assert tool._continuous_jog_timer.isActive()
    button.setDown(False)
    button.released.emit()
    button.clicked.emit()
    port.stop_jog.assert_called_once()
    tool._grbl_start_op.assert_not_called()
    assert not tool._continuous_jog_timer.isActive()
    # The sender reservation remains until its existing worker confirms idle.
    assert tool._grbl_op is not None
    tool._grbl_wait_idle = MagicMock(return_value=True)
    task = tool.app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])
    assert tool._grbl_op is None


@pytest.mark.parametrize('continuous', [True, False])
def test_default_click_keeps_existing_finite_jog(continuous):
    tool, port = connected_sender(continuous)
    tool._grbl_start_op = MagicMock()
    tool.ui.jog_wdg.jog_right_button.clicked.emit()
    assert tool._grbl_start_op.call_args.args[0] == 'jog'
    if continuous:
        port.start_jog.assert_not_called()


@pytest.mark.parametrize('live_units', ['IN', None])
def test_live_app_units_change_stops_ecut_hold(live_units):
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    button = tool.ui.jog_wdg.jog_up_button
    button.setDown(True)
    button.pressed.emit()
    port.start_jog.assert_called_once_with(1, 0, 120)
    tool.app.app_units = live_units
    tool._refresh_continuous_jog()
    port.stop_jog.assert_called_once()
    port.refresh_jog.assert_not_called()



def test_heartbeat_checks_actual_button_state_and_expired_lease():
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    button = tool.ui.jog_wdg.jog_up_button
    button.setDown(True)
    button.pressed.emit()
    tool._refresh_continuous_jog()
    port.refresh_jog.assert_called_once()
    port.stop_jog.assert_not_called()
    button.setDown(False)
    tool._refresh_continuous_jog()
    port.stop_jog.assert_called_once()
    assert not tool._continuous_jog_timer.isActive()


@pytest.mark.parametrize('event_type', [QtCore.QEvent.Type.ApplicationDeactivate,
                                       QtCore.QEvent.Type.WindowDeactivate])
def test_focus_loss_cancels_continuous_jog(event_type):
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    button = tool.ui.jog_wdg.jog_up_button
    button.setDown(True)
    button.pressed.emit()
    tool.event(QtCore.QEvent(event_type))
    port.stop_jog.assert_called_once()
    assert not tool._continuous_jog_timer.isActive()


def test_unchecking_continuous_cancels_and_cannot_turn_release_into_finite_jog():
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    tool._grbl_start_op = MagicMock()
    button = tool.ui.jog_wdg.jog_up_button
    button.setDown(True)
    button.pressed.emit()
    tool.ui.continuous_jog_cb.setChecked(False)
    button.setDown(False)
    button.released.emit()
    button.clicked.emit()
    port.stop_jog.assert_called_once()
    tool._grbl_start_op.assert_not_called()


def test_selected_check_mode_refuses_continuous_motion():
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    tool.ui.check_mode_cb.setChecked(True)
    tool.ui.jog_wdg.jog_right_button.pressed.emit()
    port.start_jog.assert_not_called()
    assert tool._grbl_op is None



def test_ui_rebuild_stops_jog_before_deleting_the_held_button():
    tool, port = connected_sender()
    tool.ui.continuous_jog_cb.setChecked(True)
    button = tool.ui.jog_wdg.jog_up_button
    button.setDown(True)
    button.pressed.emit()
    tool.connect_signals_at_init = MagicMock()
    tool._populate_tool_ui = MagicMock()
    try:
        tool.set_tool_ui()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        tool._refresh_continuous_jog()
        port.stop_jog.assert_called_once()
        assert not tool._continuous_jog_timer.isActive()
        assert tool._continuous_jog is None
    finally:
        tool._continuous_jog_timer.stop()
        tool._continuous_jog = None
