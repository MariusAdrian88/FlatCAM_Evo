"""E-Cut settings use worker requests, preserve stock senders, and reject stale callbacks."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6 import QtCore
import pytest

from test_ecut_sender_integration import make_sender
from appPlugins.Common.ecut.ecut_config import EcutBoardConfig


def snapshot(revision='r1'):
    cfg = EcutBoardConfig()
    keys = ('steps_per_mm','max_velocity_mm_s','acceleration_mm_s2','deceleration_mm_s2',
            'soft_limits_enabled','soft_limit_negative_mm','soft_limit_positive_mm')
    return dict(revision=revision, interpolation_factor=64,
                axes=[{k: axis.to_dict()[k] for k in keys} for axis in cfg.axes[:4]],
                motors=[dict(steps_per_revolution=200,microsteps=16,gear_ratio=1,
                             travel_per_revolution_mm=5) for _ in range(4)])


@pytest.fixture
def sender():
    tool = make_sender()
    port = SimpleNamespace(is_open=True, write=MagicMock(),
                           get_motion_settings=MagicMock(return_value=snapshot()),
                           save_motion_settings=MagicMock(return_value=snapshot('r2')),
                           calibration_move=MagicMock(return_value={'position_mm':[1,0,0,0,0,0],
                                                                     'distance_mm':1,'revision':'r1'}),
                           get_io_state=MagicMock(return_value=dict(connected=True,age_s=0,inputs=0,outputs=0,busy=False)),
                           set_manual_output=MagicMock(return_value=dict(connected=True,age_s=0,inputs=0,outputs=1,busy=False)))
    tool.grbl_ser_port = port
    tool._grbl_connected = True
    tool._grbl_set_ui_connected(True)
    yield tool, port
    dialog = tool.__dict__.get('_ecut_motion_dialog')
    if dialog is not None:
        dialog.set_busy(False)
        dialog.close()
    tool._continuous_jog_timer.stop()
    tool._ecut_motion_io_timer.stop()
    tool.close()
    tool.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def dispatch(tool):
    task = tool.app.worker_task.emit.call_args.args[0]
    task['fcn'](*task['params'])


def open_panel(tool):
    tool.on_ecut_motion_settings()
    dispatch(tool)
    return tool._ecut_motion_dialog


def test_settings_button_only_for_capable_ecut_connection(sender):
    tool, port = sender
    assert not tool.ui.ecut_motion_button.isHidden()
    tool.grbl_ser_port = SimpleNamespace(is_open=True)
    tool._grbl_set_ui_connected(True)
    assert tool.ui.ecut_motion_button.isHidden()


def test_opening_panel_only_reads_settings_and_does_not_hold_sender(sender):
    tool, port = sender
    dialog = open_panel(tool)
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    assert tool._grbl_op is None
    port.get_motion_settings.assert_called_once()
    port.save_motion_settings.assert_not_called()
    port.calibration_move.assert_not_called()
    port.set_manual_output.assert_not_called()


def test_apply_is_explicit_and_releases_the_reservation_after_worker_reply(sender):
    tool, port = sender
    dialog = open_panel(tool)
    payload = dialog.values()
    dialog.apply_requested.emit(payload)
    assert tool._grbl_op is not None
    port.save_motion_settings.assert_not_called()
    dispatch(tool)
    port.save_motion_settings.assert_called_once_with(payload)
    assert dialog.values()['revision'] == 'r2'
    assert tool._grbl_op is None


def test_calibration_move_only_happens_after_explicit_request(sender):
    tool, port = sender
    dialog = open_panel(tool)
    dialog.calibration_requested.emit(0,1,60,'r1')
    assert tool._grbl_op.motion
    dispatch(tool)
    port.calibration_move.assert_called_once_with(0,1,60,'r1')
    assert tool._grbl_op is None


def test_manual_output_request_uses_worker_and_current_revision(sender):
    tool, port = sender
    dialog = open_panel(tool)
    dialog.output_requested.emit(0,True)
    port.set_manual_output.assert_not_called()
    dispatch(tool)
    port.set_manual_output.assert_called_once_with(0,True,'r1')


def test_replaced_port_prevents_a_queued_settings_read(sender):
    tool, port = sender
    tool.on_ecut_motion_settings()
    tool.grbl_ser_port = SimpleNamespace(is_open=True)
    dispatch(tool)
    port.get_motion_settings.assert_not_called()
    assert tool._ecut_motion_dialog is None


def test_monitor_reads_cached_state_without_port_transaction(sender):
    tool, port = sender
    dialog = open_panel(tool)
    dialog.update_io = MagicMock()
    tool._refresh_ecut_motion_io()
    dialog.update_io.assert_called_with(port.get_io_state.return_value)
    port.write.assert_not_called()


def test_disconnect_closes_settings_and_stops_monitor(sender):
    tool, port = sender
    dialog = open_panel(tool)
    tool._grbl_set_ui_connected(False)
    assert tool._ecut_motion_dialog is None
    assert not dialog.isVisible()
    assert not tool._ecut_motion_io_timer.isActive()


def test_live_panel_can_open_during_job_without_releasing_job(sender):
    tool, port = sender
    job = tool._grbl_reserve('Job', motion=True)
    dialog = open_panel(tool)
    assert dialog.isVisible()
    assert tool._grbl_op is job
    tool._grbl_release(job)


def test_busy_job_rejects_apply_without_leaving_dialog_locked(sender):
    tool, port = sender
    dialog = open_panel(tool)
    job = tool._grbl_reserve('Job', motion=True)
    dialog.apply_button.click()
    assert not dialog._busy
    assert dialog.apply_button.isEnabled()
    assert tool._grbl_op is job
    port.save_motion_settings.assert_not_called()
    tool._grbl_release(job)


def test_invalid_settings_snapshot_warns_without_opening_dialog(sender):
    tool, port = sender
    port.get_motion_settings.return_value['axes'][0]['steps_per_mm'] = 1.5
    open_panel(tool)
    assert tool._ecut_motion_dialog is None
    assert 'Invalid E-Cut settings' in tool.app.inform.emit.call_args.args[0]


def test_queued_calibration_is_cancelled_before_usb_on_stop(sender):
    tool, port = sender
    dialog = open_panel(tool)
    tool._grbl_realtime = MagicMock()
    dialog.calibration_requested.emit(0, 1, 60, 'r1')
    op = tool._grbl_op
    assert dialog.calibration_stop_button.isEnabled()
    dialog.calibration_stop_button.click()
    assert op.cancel.is_set()
    tool._grbl_realtime.assert_called_once_with(b'\x85')
    dispatch(tool)
    port.calibration_move.assert_not_called()
    assert not dialog._busy


def test_disconnected_queued_read_cannot_reopen_panel(sender):
    tool, port = sender
    tool.on_ecut_motion_settings()
    tool._grbl_connected = False
    tool._grbl_set_ui_connected(False)
    dispatch(tool)
    port.get_motion_settings.assert_not_called()
    assert tool._ecut_motion_dialog is None


def test_actual_backend_snapshot_roundtrips_through_all_eight_tabs(sender, tmp_path):
    from test_ecut_machine_settings import motion_engine
    tool, port = sender
    engine, api = motion_engine(tmp_path)
    before = engine.get_motion_settings()
    port.get_motion_settings.return_value = before
    dialog = open_panel(tool)
    assert dialog.tabs.count() == 9
    assert dialog.hardware_tabs is not None
    payload = dialog.values()
    dialog.hardware_tabs.unverified_fields['mpg'].setChecked(True)
    assert dialog.values()['hardware']['unverified']['mpg'] is True
    assert payload['hardware'] == before['hardware']
    saved = engine.save_motion_settings(payload)
    assert saved['axes'] == before['axes']
    assert saved['hardware'] == before['hardware']
    dialog.load_settings(saved)
    dialog.hardware_tabs.output_fields['ENABLE1']['enabled'].setChecked(True)
    dialog.hardware_tabs.output_fields['ENABLE1']['pin'].setCurrentIndex(6)
    enabled = engine.save_motion_settings(dialog.values())
    assert enabled['hardware']['outputs']['ENABLE1'] == dict(enabled=True, pin=6, inverted=False)
    dialog.update_io(dict(connected=True, fresh=False, age_s=None,
                          inputs=0, outputs=0, busy=True))
    assert not dialog.hardware_tabs.output_buttons[0].isEnabled()


def test_result_is_reported_when_stop_came_after_the_board_call(sender):
    tool, port = sender
    dialog = open_panel(tool)
    tool._grbl_realtime = MagicMock()
    dialog.calibration_requested.emit(0, 1, 60, 'r1')
    op = tool._grbl_op

    def moved_then_stop(*args):
        op.cancel.set()  # Stop arrives while the board call runs
        return {'position_mm': [1, 0, 0, 0, 0, 0], 'distance_mm': 1, 'revision': 'r1'}

    port.calibration_move.side_effect = moved_then_stop
    tool.ecut_motion_result_sig = MagicMock()
    dispatch(tool)
    response = tool.ecut_motion_result_sig.emit.call_args.args[0]
    assert response[3]['distance_mm'] == 1 and response[4] is None
    assert any('Stop came after' in call.args[0] for call in tool.app.inform.emit.call_args_list)
