"""S3 connection regressions. RED tests and passing compatibility are separate."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_levelling_audit import FakeGrbl, make_audit_tool, run_worker, sg_module


class ConnectionPort(FakeGrbl):
    def __init__(self, replies=(), fail_dtr=False):
        super().__init__()
        self.handshake = list(replies)
        self.fail_dtr = fail_dtr
        self.read_hook = None
        self.read_count = 0
        self.reset_count = 0
        self.close_count = 0

    def open(self):
        self.is_open = True

    def write(self, data):
        # Handshake responses are supplied explicitly, including silent/no-answer cases.
        if data in (b'?', b'\x80') and not self.read_count:
            self.written.append(data)
            return
        super().write(data)

    @property
    def dtr(self):
        return True

    @dtr.setter
    def dtr(self, value):
        if self.fail_dtr:
            raise OSError('DTR unavailable')

    @property
    def in_waiting(self):
        return sum(len(line) for line in self.handshake) if not self.read_count else super().in_waiting

    def read(self, size):
        if self.read_count:
            return super().read(size)
        self.read_count += 1
        if self.read_hook is not None:
            self.read_hook()
        data = b''.join(self.handshake)
        self.handshake = []
        self.rx.insert(0, data[size:])
        return data[:size]

    def reset_input_buffer(self):
        self.reset_count += 1
        super().reset_input_buffer()

    def close(self):
        self.close_count += 1
        super().close()


def connect(tool, port, callbacks):
    tool.ui.com_list_combo.currentText.return_value = 'COM3'
    tool.ui.baudrates_list_combo.currentText.return_value = '115200'
    with patch.object(sg_module.serial, 'serial_for_url', return_value=port) as opened, \
            patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot',
                  side_effect=lambda delay, callback: callbacks.append(callback)):
        tool.on_grbl_connect()
    return opened


class TestConnectionRed:
    def test_failed_handshake_read_blocks_motion_after_reconnect(self):
        tool = make_audit_tool()
        callbacks = []
        port = ConnectionPort([b'ok\r\n'])
        connect(tool, port, callbacks)
        port.read_hook = MagicMock(side_effect=OSError('handshake read failed'))
        callbacks.pop()()
        connect(tool, ConnectionPort([b'ok\r\n']), callbacks)
        callbacks.pop()()
        tool._grbl_identity_pending = False
        assert tool._grbl_port_ready()
        assert tool._grbl_reserve('move', motion=True) is None

    def test_stale_callback_cannot_touch_replacement(self):
        tool = make_audit_tool()
        callbacks = []
        first = ConnectionPort([b'ok\r\n'])
        second = ConnectionPort([b'ok\r\n'])
        connect(tool, first, callbacks)
        tool._grbl_disconnect()
        connect(tool, second, callbacks)
        before = (second.read_count, second.reset_count, second.close_count,
                  list(second.written), tool.app.inform.emit.call_count,
                  list(tool.ui.mock_calls))
        callbacks[0]()
        assert (second.read_count, second.reset_count, second.close_count,
                list(second.written), tool.app.inform.emit.call_count,
                list(tool.ui.mock_calls)) == before
        assert tool.grbl_ser_port is second

    def test_connecting_refuses_probe_and_motion(self):
        tool = make_audit_tool()
        port = ConnectionPort()
        connect(tool, port, [])
        ready = tool._grbl_port_ready()
        probe = tool._grbl_reserve('probe', motion=True)
        move = tool._grbl_reserve('move', motion=True)
        assert (ready, probe, move) == (False, None, None)
        assert tool._grbl_op is None
        assert port.written == [b'?', b'\x80']

    def test_failed_dtr_and_ok_do_not_prove_reset(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n'], fail_dtr=True), callbacks)
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool._grbl_recovery == 'needs_reset'


    def test_handshake_alarm_latches_recovery_even_after_ok(self):
        tool = make_audit_tool()
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n', b'ALARM:4\r\n']), callbacks)
        callbacks[0]()
        assert tool._grbl_recovery is not None
        assert tool._grbl_reserve('probe', motion=True) is None


    def test_attempt_identity_rechecked_after_read(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        callbacks = []
        first = ConnectionPort([b"Grbl 1.1h ['$' for help]\r\n"])
        second = ConnectionPort()
        connect(tool, first, callbacks)

        def replace():
            tool._grbl_disconnect()
            connect(tool, second, callbacks)
            tool.app.inform.emit.reset_mock()
            tool._grbl_set_ui_connected.reset_mock()

        tool._grbl_set_ui_connected = MagicMock()
        first.read_hook = replace
        callbacks[0]()
        assert tool.grbl_ser_port is second
        assert tool._grbl_recovery == 'needs_reset'
        tool._grbl_set_ui_connected.assert_not_called()
        tool.app.inform.emit.assert_not_called()
        assert second.read_count == 0
        assert second.close_count == 0

    def test_banner_cannot_override_alarm_in_same_chunk(self):
        tool = make_audit_tool()
        callbacks = []
        connect(tool, ConnectionPort([b"Grbl 1.1h ['$' for help]\r\nALARM:4\r\n"]), callbacks)
        callbacks[0]()
        assert tool._grbl_recovery == 'needs_reset'

    def test_handshake_does_not_flush_late_alarm(self):
        tool = make_audit_tool()
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        port.read_hook = lambda: port._send('ALARM:4')
        callbacks[0]()
        assert port.reset_count == 0
        op = tool._grbl_reserve('units')
        assert tool._grbl_transact(op, 'G0 X1')[1] != 'ok'
        assert tool._grbl_recovery == 'needs_reset'
        assert b'G0 X1\n' not in port.written


    def test_close_invalidates_attempt_and_clears_receive_state(self):
        for close in ('_grbl_disconnect', '_grbl_force_close'):
            tool = make_audit_tool()
            port = ConnectionPort([b'ok\r\n'])
            callbacks = []
            connect(tool, port, callbacks)
            tool._grbl_rx_buffer = b'partial'
            tool._grbl_reply_pending = True
            getattr(tool, close)()
            assert not tool._grbl_connected
            assert tool._grbl_connect_attempt is None
            assert tool._grbl_rx_port is None
            assert tool._grbl_rx_buffer == b''
            assert not tool._grbl_reply_pending
            callbacks[0]()
            assert port.read_count == 0
            assert port.close_count == 1

    def test_handshake_preserves_receive_state_on_same_port(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        tool._grbl_rx_buffer = b'[MSG:pending]\r\n'
        tool._grbl_reply_pending = True
        callbacks[0]()
        assert tool._grbl_port_ready()
        assert tool.grbl_ser_port is port
        assert tool._grbl_rx_port is port
        assert tool._grbl_rx_buffer == b'[MSG:pending]\r\n'
        assert tool._grbl_reply_pending
        assert tool._grbl_recovery == 'needs_reset'
        assert port.reset_count == 0
        assert port.close_count == 0

    def test_incomplete_prefix_plus_ok_is_not_handshake_ack(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([b'ok\r\n'])
        callbacks = []
        connect(tool, port, callbacks)
        tool._grbl_rx_buffer = b'partial'
        tool._grbl_reply_pending = True
        callbacks[0]()
        assert not tool._grbl_port_ready()
        assert tool.grbl_ser_port is None
        assert tool._grbl_recovery == 'needs_reset'
        assert port.close_count == 1


def run_home(tool):
    if tool.__dict__.get('_grbl_identity_pending'):
        op = tool._grbl_reserve('units')
        tool._grbl_read_after_connect(op)
        tool._grbl_release(op)
    tool.on_grbl_homing()
    if not tool.app.worker_task.emit.call_args_list:
        return
    ticks = iter(range(10000))
    with patch('appPlugins.Common.grbl.grbl_client.time.monotonic', side_effect=lambda: next(ticks)), \
            patch('appPlugins.Common.grbl.grbl_client.time.sleep'):
        run_worker(tool)


class TestFragmentedHandshake:
    BANNER = b"Grbl 1.1h ['$' for help]\r\n"

    def test_fragmented_alarm_after_banner_blocks_home(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([self.BANNER, b'AL'])
        callbacks = []
        connect(tool, port, callbacks)
        callbacks[0]()
        assert tool._grbl_rx_buffer == b'AL'
        assert tool._grbl_rx_port is port
        port.handshake = []
        port.rx.append(b'ARM:9\r\n')
        run_home(tool)
        assert '$H' not in port.commands()
        assert tool._grbl_recovery == 'needs_reset'

    def test_complete_banner_without_suffix_still_homes(self):
        tool = make_audit_tool()
        tool._grbl_recovery = 'needs_reset'
        port = ConnectionPort([self.BANNER])
        callbacks = []
        connect(tool, port, callbacks)
        callbacks[0]()
        assert tool._grbl_recovery == 'needs_home'
        assert tool._grbl_rx_buffer == b''
        port.handshake = []
        run_home(tool)
        assert '$H' in port.commands()
        assert tool._grbl_recovery is None


class TestConnectionCompatibility:

    def test_native_uses_nominal_baud_not_uart_preference(self):
        tool = make_audit_tool()
        tool.ui.controller_combo.currentData.return_value = 'grblhal'
        tool.ui.connection_combo.currentData.return_value = 'native'
        port = ConnectionPort([b'<Idle|MPos:0,0,0>\r\n'])
        tool.ui.com_list_combo.currentText.return_value = 'COM3'
        tool.ui.baudrates_list_combo.currentText.return_value = '9600'
        with patch.object(sg_module.serial, 'serial_for_url', return_value=port) as opened:
            tool.on_grbl_connect()
        assert opened.call_args.args[1] == 115200
        assert port.reset_count == 0
        assert port.written == [b'?', b'\x80']
        assert tool._grbl_reserve('move', motion=True) is None

    def test_explicit_grbl_cannot_route_legacy_ecut_sentinel(self):
        tool = make_audit_tool()
        tool.ui.controller_combo.currentData.return_value = 'grbl'
        tool.ui.com_list_combo.currentText.return_value = 'SH-9001 E-Cut USB'
        tool._ecut_connect_start = MagicMock()
        with patch.object(sg_module.serial, 'serial_for_url') as opened, \
                patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[object()]):
            tool.on_grbl_connect()
        opened.assert_not_called()
        assert tool._ecut_connect_start.call_args.args[0] == 'SH-9001 E-Cut USB'


class TestNetworkConnection:
    def make_tool(self):
        tool = make_audit_tool()
        tool.ui.controller_combo.currentData.return_value = 'grblhal'
        tool.ui.connection_combo.currentData.return_value = 'network'
        tool.ui.network_host_entry.get_value.return_value = ' localhost '
        tool.ui.network_port_entry.get_value.return_value = 23
        tool.network_connect_result_sig = MagicMock()
        return tool

    def test_worker_opens_bannerless_and_stale_success_is_disposed(self):
        tool = self.make_tool()
        port = ConnectionPort([b'<Idle|MPos:0,0,0>\r\n'])
        port.open = lambda: tool.on_grbl_connect()  # cancel while the open is in progress
        with patch.object(sg_module.serial, 'serial_for_url', return_value=port) as opened:
            tool.on_grbl_connect()
            opened.assert_not_called()
            job = tool.app.worker_task.emit.call_args.args[0]
            job['fcn']()
        assert port.close_count == 1
        assert tool.grbl_ser_port is None
        assert not tool._grbl_connected

        tool.on_grbl_connect()
        attempt = tool._grbl_connect_attempt
        newer = ConnectionPort()
        tool.grbl_ser_port = newer
        tool._grbl_connect_attempt = object()
        tool._on_network_connect_result(('socket://localhost:23', attempt, port, b'ok\n', None))
        run_worker(tool)
        assert tool.grbl_ser_port is newer
        tool._grbl_connected = True
        tool._grbl_identity_pending = False
        tool._on_grbl_port_closed()
        assert tool._grbl_reserve('move', motion=True) is not None

    def test_worker_status_connects_and_eof_after_status_is_failure(self):
        for eof in (False, True):
            tool = self.make_tool()
            tool.ui.network_host_entry.get_value.return_value = '::1'
            port = ConnectionPort([b'<Idle|MPos:0,0,0>\r\n'])
            original_read = port.read

            def read(size):
                if eof and port.read_count:
                    raise sg_module.serial.SerialException('EOF')
                return original_read(size)

            port.read = read
            with patch.object(sg_module.serial, 'serial_for_url', return_value=port) as opened:
                tool.on_grbl_connect()
                opened.assert_not_called()
                run_worker(tool)
            response = tool.network_connect_result_sig.emit.call_args.args[0]
            with patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot'):
                tool._on_network_connect_result(response)
            assert tool._grbl_connected is (not eof)
            assert port.written == [b'?', b'\x80']
            if eof:
                assert port.close_count == 1
                # a connection that never opened has no motion to hold: no Reset/Home is needed
                assert tool._grbl_recovery is None
            else:
                assert opened.call_args.args == ('socket://[::1]:23',)

    def test_invalid_network_endpoints_never_schedule_open(self):
        for host, port in (('', 23), ('host/path', 23), ('host?x', 23), ('user@host', 23),
                           ('host\n', 23), ('socket://host', 23), ('host', 0), ('host', 65536),
                           ('host', '1.5'), ('::1%eth0?logging=debug', 23)):
            tool = self.make_tool()
            tool.ui.network_host_entry.get_value.return_value = host
            tool.ui.network_port_entry.get_value.return_value = port
            tool.on_grbl_connect()
            tool.app.worker_task.emit.assert_not_called()

    def test_refused_open_closes_partial_port(self):
        tool = self.make_tool()
        port = ConnectionPort()
        port.open = MagicMock(side_effect=sg_module.serial.SerialException('refused'))
        with patch.object(sg_module.serial, 'serial_for_url', return_value=port):
            tool.on_grbl_connect()
            run_worker(tool)
        tool._on_network_connect_result(tool.network_connect_result_sig.emit.call_args.args[0])
        assert port.close_count == 1
        assert not tool._grbl_connected
        assert tool.grbl_ser_port is None


class TestConnectionSettings:
    def test_saved_unavailable_port_and_endpoint_restore(self):
        from PyQt6 import QtWidgets

        tool = make_audit_tool()
        tool.ui.com_list_combo = QtWidgets.QComboBox()
        tool.ui.controller_combo = QtWidgets.QComboBox()
        tool.ui.connection_combo = QtWidgets.QComboBox()
        for label in ('grbl', 'grblhal', 'ecut'):
            tool.ui.controller_combo.addItem(label, label)
        for label in ('serial', 'native', 'network'):
            tool.ui.connection_combo.addItem(label, label)
        tool.app.options.update(tools_grbl_serial_port='COM99', tools_grbl_controller='grblhal',
                                tools_grbl_connection='network', tools_grbl_network_host='cnc.local',
                                tools_grbl_network_port=2023)
        tool.on_grbl_list_serial_ports = lambda: ['COM3']
        tool.on_grbl_search_ports(muted=True)
        tool._restore_connection_settings()
        assert tool.ui.com_list_combo.currentText() == 'COM99'
        assert tool.ui.controller_combo.currentData() == 'grblhal'
        assert tool.ui.connection_combo.currentData() == 'network'
        tool.ui.network_host_entry.set_value.assert_called_with('cnc.local')
        tool.ui.network_port_entry.set_value.assert_called_with(2023)
        assert tool.grbl_ser_port is None
        assert tool.ui.com_list_combo.findText('SH-9001 E-Cut USB') == -1
        tool.ui.network_host_entry.get_value.return_value = 'cnc.local'
        tool.ui.network_port_entry.get_value.return_value = 2023
        tool.ui.controller_combo.setCurrentIndex(tool.ui.controller_combo.findData('ecut'))
        tool._save_connection_settings()
        assert tool.app.options['tools_grbl_controller'] == 'ecut'
        assert tool.app.options['tools_grbl_connection'] == 'network'
        assert tool.app.options['tools_grbl_serial_port'] == 'COM99'
        assert tool.app.options['tools_grbl_network_host'] == 'cnc.local'
        assert tool.app.options['tools_grbl_network_port'] == 2023

    def test_explicit_ecut_needs_no_serial_selection(self):
        tool = make_audit_tool()
        tool.ui.controller_combo.currentData.return_value = 'ecut'
        tool.ui.com_list_combo.currentText.return_value = ''
        tool._ecut_connect_start = MagicMock()
        with patch('appPlugins.Common.ecut.ecut_protocol.find_boards', return_value=[object()]):
            tool.on_grbl_connect()
        assert tool._ecut_connect_start.call_args.args[0] == 'SH-9001 E-Cut USB'

    def test_a_failed_connection_does_not_latch_recovery(self):
        tool = make_audit_tool()
        attempt = object()
        tool._grbl_connect_attempt = attempt
        tool._on_network_connect_result(('socket://unreachable:23', attempt, None, b'', OSError('offline')))
        callbacks = []
        connect(tool, ConnectionPort([b'ok\r\n']), callbacks)
        callbacks.pop()()
        tool._grbl_identity_pending = False
        op = tool._grbl_reserve('move', motion=True)
        assert op is not None
        tool._grbl_release(op)

    def test_the_legacy_ecut_port_only_restores_a_default_controller(self):
        from PyQt6 import QtWidgets

        for saved, expected in (('grbl', 'ecut'), ('grblhal', 'grblhal')):
            tool = make_audit_tool()
            tool.ui.controller_combo = QtWidgets.QComboBox()
            tool.ui.connection_combo = QtWidgets.QComboBox()
            for label in ('grbl', 'grblhal', 'ecut'):
                tool.ui.controller_combo.addItem(label, label)
            for label in ('serial', 'native', 'network'):
                tool.ui.connection_combo.addItem(label, label)
            tool.app.options.update(tools_grbl_serial_port='SH-9001 E-Cut USB',
                                    tools_grbl_controller=saved)
            tool._restore_connection_settings()
            assert tool.ui.controller_combo.currentData() == expected
