#!/usr/bin/env python
"""Regressions for GRBL recovery, controller resets, map visuals and the outcome gate."""
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.grbl_stream_helpers import (
    CheckFake, FakeGrbl, ended_how, gcode, has_success, make_stream_tool, make_levelled_stream_tool,
)
from tests.test_grbl_sender_construct import make_app
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
from appPlugins.Common.grbl.grbl_client import stream_lines


class TestResumeBypass(unittest.TestCase):
    def test_emergency_stop_cancels_motion_before_guarded_reset(self):
        tool, fake = self.make_button_tool()
        tool._grbl_set_ui_connected(True)
        op = tool._grbl_reserve('probe', motion=True)
        callbacks = []
        with patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot',
                   side_effect=lambda delay, callback: callbacks.append((delay, callback))):
            tool.ui.estop_button.click()
        self.assertTrue(op.cancel.is_set())
        self.assertEqual(fake.written, [b'!'])
        tool.ui.resume_button.click()
        self.assertEqual(fake.written, [b'!'])
        self.assertEqual(callbacks[0][0], 500)
        callbacks[0][1]()
        self.assertIn(b'\x18', fake.written)
        tool._grbl_set_ui_connected(False)
        self.assertFalse(tool.ui.estop_button.isEnabled())

    @staticmethod
    def make_button_tool():
        tool = ToolGCodeSender(make_app())
        tool.build_ui()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        return tool, fake

    def test_resume_blocked_after_stop(self):
        tool, fake = self.make_button_tool()
        tool._grbl_stream_dirty = True
        tool.on_grbl_stop()
        tool.ui.resume_button.click()
        self.assertEqual(fake.written, [b'!'])

    def test_resume_blocked_pending_reset(self):
        tool, fake = self.make_button_tool()
        tool._grbl_reset_pending = True
        tool.ui.resume_button.click()
        self.assertEqual(fake.written, [])

    def test_healthy_active_hold_resume(self):
        tool, fake = self.make_button_tool()
        self.assertIsNotNone(tool._grbl_reserve('stream', motion=True))
        tool._grbl_stream_dirty = True
        tool.ui.hold_button.click()
        tool.ui.resume_button.click()
        self.assertEqual(fake.written, [b'!', b'~'])



class ResetDuringCheckFake(CheckFake):
    def __init__(self):
        super().__init__()
        self.g1_count = 0
        self.banner_seen = False
        self.writes_after_banner = []

    def _answer(self, cmd):
        if cmd.startswith('G1 '):
            self.g1_count += 1
            if self.g1_count == 3:
                self.rx.clear()
                self.in_check = False
                self.state = 'Idle'
                self._send("Grbl 1.1h ['$' for help]")
            if self.banner_seen:
                self.writes_after_banner.append(cmd)
        super()._answer(cmd)

    def readline(self):
        chunk = super().readline()
        if chunk.startswith(b'Grbl '):
            self.banner_seen = True
        return chunk


class TestUnexpectedReset(unittest.TestCase):
    def test_unexpected_reset_full_worker(self):
        fake = ResetDuringCheckFake()
        tool = make_stream_tool(fake, source=gcode(20), check_mode=True)

        def short_timeout(*args, **kwargs):
            return stream_lines(*args, **kwargs, stall_timeout=0.02)

        with patch('appPlugins.ToolGcodeSender.Sender.stream_lines', side_effect=short_timeout):
            tool.on_start()
        self.assertTrue(fake.banner_seen)
        self.assertEqual(fake.writes_after_banner, [])
        self.assertEqual(ended_how(tool), 'unexpected_reset')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertFalse(has_success(tool))

    def test_fragmented_banner_stops_normal_stream(self):
        chunks = iter([b'Gr', b"bl 1.1h ['$' for help]\r\n"])
        writes = []

        def write(data):
            writes.append(data)
            return 'ok'

        result = stream_lines(['G1 X1 F100', 'G1 X2 F100'], write, lambda: next(chunks), rx_size=12)
        self.assertEqual(result['status'], 'unexpected_reset')
        self.assertEqual(writes, [b'G1 X1 F100\n'])

    def test_reset_after_check_preflight_prevents_first_job_write(self):
        class ResetAfterCheckReport(CheckFake):
            def _answer(self, cmd):
                was_check = self.in_check
                super()._answer(cmd)
                if cmd == '?' and was_check:
                    self.in_check = False
                    self.state = 'Idle'
                    self._send("Grbl 1.1h ['$' for help]")

        fake = ResetAfterCheckReport()
        tool = make_stream_tool(fake, check_mode=True)
        tool.on_start()
        self.assertFalse(any(cmd.startswith('G1 ') for cmd in fake.commands()))
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertFalse(has_success(tool))

    def test_reset_during_final_idle_query_is_not_success(self):
        class ResetAtCompletion(FakeGrbl):
            def _answer(self, cmd):
                if cmd == '?' and any(line.startswith('G1 ') for line in self.commands()):
                    self._send("Grbl 1.1h ['$' for help]")
                super()._answer(cmd)

        tool = make_stream_tool(ResetAtCompletion(), source='G1 X1 F100\n')
        tool.on_start()
        self.assertNotEqual(ended_how(tool), 'finished')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertFalse(has_success(tool))


class TestStaleHeightMesh(unittest.TestCase):
    @staticmethod
    def make_display_tool():
        tool, _target = make_levelled_stream_tool(FakeGrbl())
        canvas = MagicMock()
        tool.app.plotcanvas3d = canvas
        tool.view3d = SimpleNamespace(canvas=canvas, set_height_map=MagicMock(), set_toolpath=MagicMock(),
                                      probing_shapes=MagicMock(), excellon_shapes=MagicMock())
        tool._update_3d_height_map()
        tool.view3d.set_height_map.reset_mock()
        return tool

    def test_clear_probe_data_clears_mesh(self):
        tool = self.make_display_tool()
        tool._clear_probe_data()
        self.assertFalse(tool.al_heights_valid)
        tool.view3d.set_height_map.assert_called_once_with(None)

    def test_on_show_3d_failed_build_clears_mesh(self):
        tool = self.make_display_tool()
        tool.al_heights_valid = False
        tool.app.collection.get_by_name.return_value = None
        tool.on_show_3d()
        tool.view3d.set_toolpath.assert_called_once_with([])
        tool.view3d.set_height_map.assert_called_once_with(None)

    def test_replacement_start_clears_mesh(self):
        tool = self.make_display_tool()
        self.assertIsNotNone(tool._al_capture_context(replacement=True))
        self.assertFalse(tool.al_heights_valid)
        tool.view3d.set_height_map.assert_called_once_with(None)


class TestStopResets(unittest.TestCase):
    """Stop = feed hold, then a soft reset (it stops the spindle); an idle machine keeps its position."""

    @staticmethod
    def make_tool():
        tool = make_stream_tool(FakeGrbl())
        tool.grbl_ser_port.written.clear()
        tool.grbl_port_closed_sig = MagicMock()
        return tool, tool.grbl_ser_port

    def stop(self, tool):
        with patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot', side_effect=lambda _ms, fn: fn()):
            tool.on_stream_stop()

    def test_stop_when_idle_resets_and_keeps_the_position(self):
        tool, fake = self.make_tool()
        self.stop(tool)
        self.assertEqual(fake.written[:2], [b'!', b'\x18'])
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(tool._grbl_reset_pending)
        self.assertIsNone(tool._grbl_op)

    def test_stop_during_motion_resets_and_needs_home(self):
        tool, fake = self.make_tool()
        op = tool._grbl_reserve('command', motion=True)
        self.assertIsNotNone(op)
        with patch('appPlugins.ToolGcodeSender.Sender.QtCore.QTimer.singleShot') as timer:
            tool.on_stream_stop()
        self.assertEqual(timer.call_args.args[0], 500)  # the machine brakes in the feed hold first
        tool._grbl_release(op)
        timer.call_args.args[1]()
        self.assertIn(b'\x18', fake.written)
        self.assertEqual(tool._grbl_recovery, 'needs_home')

    def test_reset_button_does_not_take_checked_as_keep_position(self):
        tool, fake = self.make_tool()
        tool.on_grbl_reset()
        self.assertIsNone(tool._grbl_recovery)

    def test_plugin_close_resets_and_disconnects(self):
        tool, fake = self.make_tool()
        tool._grbl_recovery = 'needs_reset'
        tool._grbl_shutdown()
        self.assertEqual(fake.written[:2], [b'!', b'\x18'])
        self.assertFalse(fake.is_open)
        self.assertIsNone(tool.grbl_ser_port)
        self.assertFalse(tool._grbl_connected)
        self.assertIsNone(tool._grbl_recovery)

    def test_plugin_close_during_motion_leaves_needs_home(self):
        tool, fake = self.make_tool()
        self.assertIsNotNone(tool._grbl_reserve('command', motion=True))
        tool._grbl_shutdown()
        self.assertIn(b'\x18', fake.written)
        self.assertEqual(tool._grbl_recovery, 'needs_home')

    def test_manual_reference_of_xyz_ends_needs_home(self):
        tool, fake = self.make_tool()
        tool._grbl_recovery = 'needs_home'
        fake.reference_axes = MagicMock(return_value=None)
        tool.ecut_motion_result_sig = MagicMock()
        for axes, expected in (([0, 1], 'needs_home'), ([0, 1, 2], None)):
            op = tool._grbl_reserve('E-Cut settings')
            tool._ecut_settings_task(op, None, 'reference', (axes,))
            self.assertEqual(tool._grbl_recovery, expected)


class TestSpindleButton(unittest.TestCase):
    """The Spindle button sends M3 S<RPM> / M5 and follows the spindle state."""

    def test_typed_rpm_is_kept(self):
        from PyQt6 import QtCore, QtTest
        sender = ToolGCodeSender(make_app())
        sender.build_ui()
        entry = sender.ui.spindle_rpm_entry
        entry.lineEdit().selectAll()
        QtTest.QTest.keyClicks(entry, '15000')
        QtTest.QTest.keyClick(entry, QtCore.Qt.Key.Key_Return)
        entry.interpretText()
        self.assertEqual(entry.get_value(), 15000)

    def test_spindle_state_of_line(self):
        from appPlugins.Common.grbl.grbl_client import spindle_state_of_line
        for line, expected in (('M3 S1000', True), ('m4s200', True), ('G1 X1 M5', False), ('M30', False),
                               ('M2', False), ('G0 X3', None), ('(M3) G0 X1', None), ('G0 ; M3', None),
                               ('M03 S10', True), ('M13', None), ('$H', None)):
            with self.subTest(line=line):
                self.assertIs(spindle_state_of_line(line), expected)

    def test_status_accessory_field(self):
        from appPlugins.Common.grbl.grbl_protocol import parse_grbl_status_report
        report = parse_grbl_status_report('<Idle|MPos:0.000,0.000,0.000|FS:0,1000|Ov:100,100,100|A:SF>')
        self.assertEqual(report['accessory'], 'SF')

    def test_button_sends_m3_and_turns_on_only_after_ok(self):
        tool = make_stream_tool(FakeGrbl())
        tool.spindle_state_sig = MagicMock()
        tool._spindle_on = False
        tool.ui.spindle_rpm_entry.get_value.return_value = 12000
        tool.on_spindle_button(True)
        tool.ui.spindle_button.setChecked.assert_called_with(False)  # not yet: the controller must accept it
        self.assertIn('M3 S12000', tool.grbl_ser_port.commands())
        tool.spindle_state_sig.emit.assert_called_with(True)

    def test_button_m5_is_allowed_in_recovery(self):
        tool = make_stream_tool(FakeGrbl())
        tool.spindle_state_sig = MagicMock()
        tool._grbl_recovery = 'needs_home'
        tool.on_spindle_button(True)
        self.assertNotIn('M3 S0', ' '.join(tool.grbl_ser_port.commands()))
        self.assertFalse(any(c.startswith('M3') for c in tool.grbl_ser_port.commands()))
        tool.on_spindle_button(False)
        self.assertIn('M5', tool.grbl_ser_port.commands())
        tool.spindle_state_sig.emit.assert_called_with(False)

    def test_status_report_sets_the_button(self):
        tool = make_stream_tool(FakeGrbl())
        report = {'state': 'Run', 'ov': (100, 100, 100), 'accessory': 'S'}
        tool._on_status_report(report)
        tool.ui.spindle_button.setChecked.assert_called_with(True)
        tool._on_status_report({'state': 'Run', 'ov': (100, 100, 100)})
        tool.ui.spindle_button.setChecked.assert_called_with(False)
        tool.ui.spindle_button.setChecked.reset_mock()
        tool._on_status_report({'state': 'Run'})  # no 'Ov': the accessory state is unknown
        tool.ui.spindle_button.setChecked.assert_not_called()

    @staticmethod
    def poll_tool():
        tool = make_stream_tool(FakeGrbl())
        tool._plugin_active = True
        tool.grbl_ser_port.written.clear()
        return tool

    def test_idle_poll_reads_the_status(self):
        tool = self.poll_tool()
        tool.app.worker_task.emit.reset_mock()
        tool._poll_idle_status()
        deadline = time.monotonic() + 2.0
        while tool._grbl_op is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        tool.app.worker_task.emit.assert_not_called()  # its own thread, not the app worker pool
        self.assertEqual(tool.grbl_ser_port.written, [b'?'])
        report = tool.status_report_sig.emit.call_args.args[0]
        self.assertEqual(report['state'], 'Idle')
        self.assertIsNone(tool._grbl_op)

    def test_idle_poll_waits_in_recovery_and_during_operations(self):
        for setup in (lambda t: setattr(t, '_grbl_recovery', 'needs_home'),
                      lambda t: setattr(t, '_plugin_active', False),
                      lambda t: t._grbl_reserve('command', motion=True)):
            tool = self.poll_tool()
            setup(tool)
            tool._poll_idle_status()
            self.assertEqual(tool.grbl_ser_port.written, [])


    def test_ecut_status_reports_the_spindle(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ecut'))
        from test_ecut_gcode_engine import engine_api
        engine, api = engine_api()
        self.assertNotIn('|A:', engine.status_report())
        api._spindle_direction = 'SPINDLE_CW'
        self.assertTrue(engine.status_report().endswith('|A:S>'))


class TestDeletedQtObjects(unittest.TestCase):
    """A new project deletes the plugins and the Plot 3D tab; the old objects must not raise."""

    def test_view3d_of_a_deleted_canvas_is_none(self):
        from PyQt6 import QtCore, sip
        tool = make_stream_tool(FakeGrbl())
        native = QtCore.QObject()
        tool.view3d = SimpleNamespace(canvas=SimpleNamespace(native=native))
        self.assertIsNotNone(tool.view3d)
        sip.delete(native)
        self.assertIsNone(tool.view3d)  # was: RuntimeError in the console click handler

    def test_close_port_of_a_deleted_plugin_closes_the_port(self):
        tool = make_stream_tool(FakeGrbl())
        fake = tool.grbl_ser_port
        tool.grbl_port_closed_sig = MagicMock()
        tool.grbl_port_closed_sig.emit.side_effect = RuntimeError('wrapped C/C++ object has been deleted')
        self.assertTrue(tool._grbl_close_port())
        self.assertFalse(fake.is_open)

    def test_stop_all_runs_then_removes_the_stop_of_a_deleted_plugin(self):
        from PyQt6 import QtCore, sip
        from appCommon import MachineSafety

        class Owner(QtCore.QObject):
            stops = 0

            def stop(self):
                Owner.stops += 1

        owner = Owner()
        stop = owner.stop
        MachineSafety.register(stop)
        self.addCleanup(MachineSafety.unregister, stop)
        sip.delete(owner)
        MachineSafety.stop_all()  # the port of a deleted plugin can be open: its stop still runs
        self.assertEqual(Owner.stops, 1)
        self.assertNotIn(stop, MachineSafety._stoppers)


if __name__ == '__main__':
    unittest.main()
