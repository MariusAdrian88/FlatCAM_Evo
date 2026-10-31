"""E-Cut connection selection and manual confirmation in the existing sender."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys

from PyQt6 import QtWidgets
import pytest

from tests.test_grbl_sender_construct import make_app
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender


def make_sender():
    tool = ToolGCodeSender(make_app())
    tool._sim_ui_timer.stop()
    return tool



def test_ecut_connection_uses_python_adapter_without_dtr_or_serial_port():
    tool = make_sender()
    tool.ui.controller_combo.setCurrentIndex(tool.ui.controller_combo.findData('ecut'))
    assert tool.ui.controller_combo.currentData() == 'ecut'
    connection = MagicMock()
    factory = MagicMock(return_value=connection)
    module = SimpleNamespace(EcutSenderPort=factory)
    with patch.dict(sys.modules, {'appPlugins.Common.ecut.ecut_sender_port': module}), \
            patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[{'path': b'p'}]), \
            patch('appPlugins.ToolGcodeSender.Sender.serial.serial_for_url') as serial_factory, \
            patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot'):
        tool.on_grbl_connect()
        tool.app.worker_task.emit.call_args.args[0]['fcn']()  # the port is created in a worker
    factory.assert_called_once()
    serial_factory.assert_not_called()
    connection.reset_input_buffer.assert_not_called()
    assert tool.grbl_ser_port is connection
    assert callable(connection.tool_change_callback)


@pytest.mark.parametrize('accept', [True, False])
def test_manual_tool_dialog_confirms_matching_request_or_cancels_job(accept):
    token = object()
    port = SimpleNamespace(is_open=True,pending_tool_change=2,tool_change_token=token,
                           confirm_tool_change=MagicMock())
    tool = SimpleNamespace(grbl_ser_port=port,app=SimpleNamespace(ui=None),on_grbl_reset=MagicMock())
    result = QtWidgets.QMessageBox.StandardButton.Yes if accept else QtWidgets.QMessageBox.StandardButton.Cancel
    with patch('appPlugins.ToolGcodeSender.Sender.QtWidgets.QMessageBox.question',return_value=result):
        ToolGCodeSender._on_ecut_tool_change(tool, port, 2, token)
    if accept:
        port.confirm_tool_change.assert_called_once_with(2,token=token)
    else:
        port.confirm_tool_change.assert_not_called()
        tool.on_grbl_reset.assert_called_once()


def test_stale_tool_confirmation_cannot_confirm_replacement_connection():
    token = object()
    old = SimpleNamespace(is_open=True,pending_tool_change=2,tool_change_token=token,
                          confirm_tool_change=MagicMock())
    replacement = SimpleNamespace(is_open=True,pending_tool_change=2,tool_change_token=object())
    tool = SimpleNamespace(grbl_ser_port=old,app=SimpleNamespace(ui=None),on_grbl_reset=MagicMock())

    def replace_connection(*args):
        tool.grbl_ser_port = replacement
        return QtWidgets.QMessageBox.StandardButton.Yes

    with patch('appPlugins.ToolGcodeSender.Sender.QtWidgets.QMessageBox.question',side_effect=replace_connection):
        ToolGCodeSender._on_ecut_tool_change(tool, old, 2, token)
    old.confirm_tool_change.assert_not_called()
import threading


@pytest.mark.parametrize('ecut,check_mode', [(True,False),(True,True),(False,False)])
def test_whole_job_preparation_is_only_for_ecut_and_preserves_completion(ecut,check_mode):
    port = SimpleNamespace()
    if ecut:
        port.prepare_program = MagicMock()
    op = SimpleNamespace(port=port,cancel=threading.Event(),failed=False)
    tool = SimpleNamespace(_grbl_op_lock=threading.RLock(),_grbl_active_locked=lambda op:True,
                           _grbl_op=op,grbl_ser_port=port,
                           _grbl_query_status=lambda op:'<Idle|MPos:0,0,0>',
                           _grbl_read_report_units=lambda op:'ok',
                           _grbl_enter_check_mode=lambda op:None,
                           _grbl_prepare_locked=lambda op,port:('',None),
                           _grbl_stream_io=lambda op,req:(lambda data:'ok',lambda:b''),
                           _grbl_realtime_byte=lambda data:data,
                           _grbl_stream_control=lambda op,data:'ok',
                           _grbl_stream_finish=MagicMock(return_value='finished'),
                           stream_progress_sig=MagicMock(),console_line_sig=MagicMock(),
                           status_report_sig=MagicMock())
    lines = ['G21','G1 X1 F60','X0']
    response = dict(status='finished',done=3,errors=[],last_text='ok',in_flight=0)
    with patch('appPlugins.ToolGcodeSender.Sender.stream_lines',return_value=response) as stream:
        result = ToolGCodeSender._grbl_stream_run(tool, op, lines, [1, 2, 3], check_mode, .1)
    assert result == 'finished'
    tool._grbl_stream_finish.assert_called_once_with(op,response,check_mode)
    if ecut and not check_mode:
        port.prepare_program.assert_called_once_with(lines)
    elif ecut:
        port.prepare_program.assert_not_called()
    assert stream.call_args.args[0] == lines
