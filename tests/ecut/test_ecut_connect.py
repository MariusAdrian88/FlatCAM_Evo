"""E-Cut board presence check and the connect that does not block the GUI thread."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys
import threading

import pytest

from test_ecut_sender_integration import make_sender
from appPlugins.Common.ecut.ecut_protocol import find_boards, EcutUsbError

PORT_NAME = 'SH-9001 E-Cut USB'


@pytest.fixture
def tool():
    tool = make_sender()
    tool.ui.com_list_combo.clear()
    tool.ui.com_list_combo.addItem(PORT_NAME)
    yield tool
    tool._continuous_jog_timer.stop()
    tool._ecut_motion_io_timer.stop()
    tool.close()
    tool.deleteLater()


def worker_fcn(tool):
    return tool.app.worker_task.emit.call_args.args[0]['fcn']


def test_find_boards_lists_entries_of_the_given_vid_pid(monkeypatch):
    fake = SimpleNamespace(enumerate=MagicMock(return_value=[{'serial_number': 'A1', 'path': b'p1'}]))
    monkeypatch.setitem(sys.modules, 'hid', fake)
    boards = find_boards(0x0483, 0x5750)
    fake.enumerate.assert_called_once_with(0x0483, 0x5750)
    assert boards[0]['serial_number'] == 'A1' and boards[0]['path'] == b'p1'


def test_find_boards_empty_when_no_board(monkeypatch):
    monkeypatch.setitem(sys.modules, 'hid', SimpleNamespace(enumerate=lambda vid, pid: []))
    assert find_boards() == []


def test_find_boards_without_hidapi_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, 'hid', None)  # makes "import hid" fail
    with pytest.raises(EcutUsbError, match='hidapi is not installed'):
        find_boards()


def test_connect_refuses_when_no_board_and_keeps_state(tool):
    factory = MagicMock()
    with patch.dict(sys.modules, {'appPlugins.Common.ecut.ecut_sender_port': SimpleNamespace(EcutSenderPort=factory)}), \
            patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[]):
        tool.on_grbl_connect()
        worker_fcn(tool)()
    message = tool.app.inform.emit.call_args.args[0]
    assert message.startswith('[ERROR_NOTCL]') and 'not found' in message
    assert '0483' in message and '5750' in message
    assert tool._grbl_connect_attempt is None and tool.grbl_ser_port is None
    factory.assert_not_called()


def test_connect_shows_missing_hidapi(tool):
    with patch('appPlugins.Common.ecut.ecut_protocol.find_boards', side_effect=EcutUsbError('hidapi is not installed')):
        tool.on_grbl_connect()
        worker_fcn(tool)()
    assert 'hidapi is not installed' in tool.app.inform.emit.call_args.args[0]
    assert tool._grbl_connect_attempt is None


def test_board_check_runs_off_the_gui_thread(tool):
    threads = []
    # the GUI slot must not run on the worker thread (it starts Qt timers): the result is only recorded
    tool.ecut_connect_result_sig = MagicMock()
    module = SimpleNamespace(EcutSenderPort=MagicMock())
    with patch.dict(sys.modules, {'appPlugins.Common.ecut.ecut_sender_port': module}), \
            patch('appPlugins.Common.ecut.ecut_protocol.find_boards',
                  side_effect=lambda *a: threads.append(threading.current_thread()) or [{'path': b'p'}]):
        tool.on_grbl_connect()
        assert threads == []  # the GUI thread does not enumerate the USB devices
        runner = threading.Thread(target=worker_fcn(tool))
        runner.start()
        runner.join(5)
    assert threads == [runner]


def test_connect_creates_the_port_in_a_worker_and_finishes_on_the_result(tool):
    port = MagicMock()
    factory = MagicMock(return_value=port)
    module = SimpleNamespace(EcutSenderPort=factory)
    with patch.dict(sys.modules, {'appPlugins.Common.ecut.ecut_sender_port': module}), \
            patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[{'path': b'p'}]), \
            patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot') as timer:
        tool.on_grbl_connect()
        factory.assert_not_called()  # the GUI thread does not create the port
        assert tool.grbl_ser_port is None
        worker_fcn(tool)()
    factory.assert_called_once()
    assert tool.grbl_ser_port is port
    port.write.assert_called_once_with(b'\r\n\r\n')
    assert callable(port.tool_change_callback)
    assert timer.call_args.args[0] == 1000


def test_failed_port_creation_reports_and_clears_the_attempt(tool):
    factory = MagicMock(side_effect=EcutUsbError('boom'))
    module = SimpleNamespace(EcutSenderPort=factory)
    with patch.dict(sys.modules, {'appPlugins.Common.ecut.ecut_sender_port': module}), \
            patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[{'path': b'p'}]):
        tool.on_grbl_connect()
        worker_fcn(tool)()
    assert tool.grbl_ser_port is None and tool._grbl_connect_attempt is None
    assert 'Could not connect' in tool.app.inform.emit.call_args.args[0]


def test_stale_connect_result_is_closed_and_ignored(tool):
    port = MagicMock()
    old_attempt = object()
    tool._grbl_connect_attempt = object()  # a newer Connect or a Disconnect replaced it
    with patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot') as timer:
        tool._on_ecut_connect_result((PORT_NAME, old_attempt, port, None))
    assert tool.grbl_ser_port is None
    port.write.assert_not_called()
    timer.assert_not_called()
    port.close.assert_not_called()  # the close runs in a worker, not on the GUI thread
    worker_fcn(tool)()
    port.close.assert_called_once()


def test_stale_connect_error_does_not_touch_the_new_attempt(tool):
    current = object()
    tool._grbl_connect_attempt = current
    tool._on_ecut_connect_result((PORT_NAME, object(), None, EcutUsbError('boom')))
    assert tool._grbl_connect_attempt is current
