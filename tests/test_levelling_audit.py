#!/usr/bin/env python
"""
Regression tests for the Levelling Tool audit fixes (2026-10-12).

Run: .venv/Scripts/python.exe -m pytest tests/test_levelling_audit.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import serial
from PyQt6 import QtWidgets

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from shapely import Point, box

import appPlugins.ToolLevelling  # noqa: F401
# appPlugins/__init__.py re-exports the class ToolLevelling under the module name,
# so take the module itself from sys.modules
tl_module = sys.modules['appPlugins.ToolLevelling']
from appPlugins.ToolLevelling import ToolLevelling
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
import appPlugins.Common.grbl.grbl_client  # noqa: F401
# appPlugins/__init__.py also shadows the package name with the class ToolGcodeSender
sg_module = sys.modules['appPlugins.Common.grbl.grbl_client']
from appPlugins.Common import levelling_interp as li
from appPlugins.Common.grbl import grbl_protocol as gp
from unittest.mock import MagicMock, patch
from unittest.mock import ANY
from unittest.mock import ANY, patch
import pytest


def make_audit_tool(units='MM', storage=None, heights_valid=False, cls=None):
    tool = (cls or ToolGCodeSender).__new__(cls or ToolGCodeSender)
    tool.app = MagicMock()
    tool.app.decimals = 4
    tool.app.app_units = units
    tool.app.options = {}
    tool.app.inform_shell = MagicMock()
    tool.ui = MagicMock()
    tool.ui.plot_probing_pts_cb.get_value.return_value = False
    tool.ui.avoid_exc_combo.currentIndex.return_value = 0
    tool.ui.avoid_exc_combo.count.return_value = 0
    tool.units = units
    tool.decimals = 4
    tool.al_voronoi_geo_storage = storage if storage is not None else {}
    tool._al_data_units = units if tool.al_voronoi_geo_storage else None
    tool.al_bilinear_geo_storage = []
    tool.al_heights_valid = heights_valid
    tool.probing_gcode_text = ''
    tool.grbl_probe_result = ''
    tool.grbl_work_offset = (0.0, 0.0, 0.0)
    tool.grbl_ser_port = None
    tool.probing_shapes = None
    tool.solid_geo = None
    tool.mouse_events_connected = False
    tool.mouse_is_dragging = False
    tool.old_selection_state = True
    tool.build_al_table_sig = MagicMock()
    tool.apply_autolevel_sig = MagicMock()
    tool.show_probing_geo_sig = MagicMock()
    tool.grbl_port_closed_sig = MagicMock()
    tool.ecut_tool_change_sig = MagicMock()
    tool.ecut_motion_result_sig = MagicMock()
    tool.ecut_connect_result_sig = MagicMock()
    # publish results at once (in tests the worker runs inline, in the main thread)
    tool.al_result_ready_sig = MagicMock()
    tool.al_result_ready_sig.emit.side_effect = lambda result: tool._on_al_result_ready(result)
    tool._al_apply_running = False
    tool._grbl_op_lock = threading.Lock()
    tool._grbl_op = None
    tool._grbl_recovery = None
    tool._grbl_close_pending = False
    tool._grbl_connected = False
    tool._grbl_connect_attempt = None
    tool._grbl_safety_generation = 0
    tool._grbl_rx_port = None
    tool._grbl_rx_buffer = b''
    tool._grbl_reply_pending = False
    tool._grbl_reset_pending = False
    tool._grbl_stream_dirty = False
    tool._al_generation = 0
    tool._al_target_name = None
    tool._al_target_obj = None
    tool._al_core_signals_connected = False
    tool.console_line_sig = MagicMock()
    tool.job_map_generation = None
    tool.view3d = None
    tool._preview_generation = 0
    tool._probe_start_op = None
    tool.preview_ready_sig = MagicMock()
    if isinstance(tool, ToolGCodeSender):
        tool.preview_ready_sig.emit.side_effect = tool._on_preview_ready
    tool.probe_finished_sig = MagicMock()
    if isinstance(tool, ToolGCodeSender):
        tool.probe_finished_sig.emit.side_effect = tool._on_probe_finished
    tool.stream_progress_sig = MagicMock()
    tool.status_report_sig = MagicMock()
    tool.spindle_state_sig = MagicMock()
    tool.stream_ended_sig = MagicMock()
    tool.frame_ready_sig = MagicMock()
    tool.edge_probe_result_sig = MagicMock()
    if isinstance(tool, ToolGCodeSender):
        tool.edge_probe_result_sig.emit.side_effect = tool._on_edge_probe_result
    return tool


def make_port(lines=None):
    """A MagicMock serial port. readline() returns the items of `lines` in
    order; after the last item it returns b''. reset_input_buffer() does nothing."""
    port = MagicMock()
    port.is_open = True
    port.in_waiting = 0
    queue = list(lines or [])
    port.readline.side_effect = lambda: queue.pop(0) if queue else b''
    return port


class FakeGrbl:
    """A fake GRBL 1.1 controller behind a fake serial port.

    write() answers each line at once. readline() returns the queued answer
    lines. reset_input_buffer() really drops the queued lines.
    A G38.2 move reports the X/Y of the last G0 move and the height from
    `heights`: {(x, y) in command units, rounded to 3 decimals: z in mm}.
    Reports are in mm, or in inches if report_inches is True ($13=1).
    `replies` maps a command prefix to a list of answer lines that replace
    the normal answer, for example {'G38.2': ['ALARM:4']}.
    `fail_probe_at`: the Nth G38.2 answers 'ALARM:4'.
    `read_hook`: called at the start of each readline()."""

    def __init__(self, heights=None, report_inches=False, state='Idle', replies=None,
                 has_report_setting=True, fail_probe_at=None, read_hook=None):
        self.is_open = True
        self.written = []
        self.rx = []
        self.x = 0.0
        self.y = 0.0
        self.units_word = 'G21'
        self.heights = heights or {}
        self.report_inches = report_inches
        self.has_report_setting = has_report_setting
        self.state = state
        self.replies = dict(replies or {})
        self.fail_probe_at = fail_probe_at
        self.probe_count = 0
        self.read_hook = read_hook

    def open(self):
        self.is_open = True

    def reset_input_buffer(self):
        self.rx.clear()

    @property
    def in_waiting(self):
        return sum(len(line) for line in self.rx)

    def read(self, size):
        data = b''.join(self.rx)
        self.rx = [data[size:]] if data[size:] else []
        return data[:size]

    def readline(self):
        if self.read_hook is not None:
            self.read_hook()
        return self.rx.pop(0) if self.rx else b''

    def readlines(self):
        lines, self.rx = self.rx, []
        return lines

    def close(self):
        self.is_open = False

    def commands(self):
        """The written lines as text, without the line end."""
        return [data.decode('utf-8', errors='replace').strip() for data in self.written]

    def write(self, data):
        self.written.append(data)
        self._answer(data.decode('utf-8', errors='replace').strip())

    def _send(self, *lines):
        for line in lines:
            self.rx.append(line.encode('utf-8') + b'\r\n')

    def _report(self, value_in_command_units):
        mm = value_in_command_units * (25.4 if self.units_word == 'G20' else 1.0)
        return mm / 25.4 if self.report_inches else mm

    def _answer(self, cmd):
        for prefix, lines in self.replies.items():
            if cmd.startswith(prefix):
                self._send(*lines)
                return
        if cmd == '?':
            self._send('<%s|MPos:0.000,0.000,0.000|FS:0,0>' % self.state)
            return
        if cmd == '\x18':
            self._send("Grbl 1.1h ['$' for help]")
            return
        if cmd in ('!', '~', ''):
            return
        if cmd == '$$':
            if self.has_report_setting:
                self._send('$10=1', '$13=%d' % (1 if self.report_inches else 0), 'ok')
            else:
                self._send('$10=1', 'ok')
            return
        if cmd == '$G':
            self._send('[GC:G0 G54 G17 %s G90 G94 M5 M9 T0 F0 S0]' % self.units_word, 'ok')
            return
        if cmd == '$#':
            self._send('[G54:0.000,0.000,0.000]', '[G92:0.000,0.000,0.000]', '[TLO:0.000]', 'ok')
            return
        if cmd in ('G20', 'G21'):
            self.units_word = cmd
            self._send('ok')
            return
        if cmd.startswith('G0 '):
            for word in cmd.split()[1:]:
                if word[0] == 'X':
                    self.x = float(word[1:])
                elif word[0] == 'Y':
                    self.y = float(word[1:])
            self._send('ok')
            return
        if cmd.startswith('G38.2'):
            self.probe_count += 1
            if self.fail_probe_at == self.probe_count:
                self._send('ALARM:4')
                return
            z_mm = self.heights.get((round(self.x, 3), round(self.y, 3)), 0.0)
            z = z_mm / 25.4 if self.report_inches else z_mm
            self._send('[PRB:%.4f,%.4f,%.4f:1]' % (self._report(self.x), self._report(self.y), z), 'ok')
            return
        self._send('ok')


class AxisFakeGrbl(FakeGrbl):
    """A FakeGrbl that reports a directional G38.2 contact on X or Y.

    `edges` maps 'xplus', 'xminus', 'yplus' or 'yminus' to the contact coordinate in
    command units; without an entry the commanded target is the contact.
    `machine_offset` is added to the PRB machine coordinates. The reported position
    is in the controller report units ($13).
    """

    def __init__(self, edges=None, machine_offset=(0.0, 0.0, 0.0), no_contact_at=None, **kwargs):
        super().__init__(**kwargs)
        self.edges = dict(edges or {})
        self.machine_offset = machine_offset
        self.no_contact_at = no_contact_at

    def _answer(self, cmd):
        words = {word[0]: word[1:] for word in cmd.split()[1:] if word and word[0] in 'XYF'}
        if cmd.startswith('G38.2') and ('X' in words or 'Y' in words):
            axis = 'x' if 'X' in words else 'y'
            target = float(words[axis.upper()])
            current = self.x if axis == 'x' else self.y
            direction = axis + ('plus' if target >= current else 'minus')
            contact = self.edges.get(direction, target)
            if axis == 'x':
                self.x = contact
            else:
                self.y = contact
            z_mm = self.heights.get((round(self.x, 3), round(self.y, 3)), 0.0)
            z = z_mm / 25.4 if self.report_inches else z_mm
            mx, my, mz = self.machine_offset
            self.probe_count += 1
            if self.fail_probe_at == self.probe_count:
                self._send('ALARM:4')
                return
            hit = 0 if self.no_contact_at == self.probe_count else 1
            self._send('[PRB:%.4f,%.4f,%.4f:%d]' % (
                self._report(self.x + mx), self._report(self.y + my), z + mz, hit), 'ok')
            return
        super()._answer(cmd)


class MovingAxisFakeGrbl(AxisFakeGrbl):
    """AxisFakeGrbl that reports its current work position in the status report."""

    def _answer(self, cmd):
        if cmd == '?':
            self._send('<Idle|MPos:%.4f,%.4f,0.000|FS:0,0>' % (
                self._report(self.x), self._report(self.y)))
            return
        super()._answer(cmd)


class OffsetAxisFakeGrbl(AxisFakeGrbl):
    """AxisFakeGrbl with a nonzero active work offset, in command units."""

    def __init__(self, offset=(0.0, 0.0, 0.0), **kwargs):
        kwargs.setdefault('machine_offset', offset)
        super().__init__(**kwargs)
        self.offset = offset

    def _answer(self, cmd):
        if cmd == '$#':
            self._send('[G54:%.4f,%.4f,%.4f]' % tuple(self._report(value) for value in self.offset),
                       '[G92:0,0,0]', '[TLO:0]', 'ok')
            return
        if cmd == '?':
            self._send('<Idle|MPos:%.4f,%.4f,%.4f|FS:0,0>' % tuple(
                self._report(value) for value in self.offset))
            return
        super()._answer(cmd)


class ModalFakeGrbl(AxisFakeGrbl):
    """AxisFakeGrbl that tracks the distance/feed modes and can refuse 1 command."""

    def __init__(self, distance='G90', feed='G94', fail_on=None, **kwargs):
        super().__init__(**kwargs)
        self.distance = distance
        self.feed = feed
        self.fail_on = fail_on

    def _answer(self, cmd):
        if cmd == '$G':
            self._send('[GC:G0 G54 G17 %s %s %s M5]' % (self.units_word, self.distance, self.feed), 'ok')
            return
        if cmd in ('G90', 'G91'):
            self.distance = cmd
            self._send('ok')
            return
        if cmd in ('G93', 'G94'):
            self.feed = cmd
            self._send('ok')
            return
        if cmd == self.fail_on:
            self._send('error:9')
            return
        super()._answer(cmd)


def emitted(tool):
    """All texts sent to app.inform.emit, as 1 list."""
    return [c.args[0] for c in tool.app.inform.emit.call_args_list if c.args]


def run_worker(tool, index=-1):
    """Run the worker function of the app.worker_task.emit() call `index` (inline)."""
    tool.app.worker_task.emit.call_args_list[index].args[0]['fcn']()


def run_set_tool_ui(storage, heights_valid, loaded_obj=None, plot_points=False, owner=None,
                    real_radios=False, global_method='v'):
    """Run the real set_tool_ui() with a mocked UI. `owner` is (name, obj) or None."""
    tool = ToolLevelling.__new__(ToolLevelling)
    if real_radios:
        QtWidgets.QWidget.__init__(tool)
    tool.app = MagicMock()
    tool.app.app_units = 'MM'
    tool.app.options = {
        'global_app_level': 'a',
        'tools_al_plot_points': plot_points,
        'tools_al_avoid_exc_holes': False,
        'tools_al_method': global_method,
        'tools_al_mode': 'manual',
    }
    tool.app.collection.get_active.return_value = None
    tool.app.collection.get_by_name.return_value = loaded_obj
    tool.layout = MagicMock()
    tool.form_fields = {}
    tool.al_voronoi_geo_storage = storage
    tool.al_bilinear_geo_storage = []
    tool.al_heights_valid = heights_valid
    tool.probing_gcode_text = ''
    tool.probing_gcode = MagicMock(return_value='')
    tool.clear_ui = MagicMock()
    tool.connect_signals_at_init = MagicMock()
    tool.to_form = MagicMock()
    tool.on_controller_change_alter_ui = MagicMock()
    tool.change_level = MagicMock()
    tool.build_tool_ui = MagicMock()
    tool.build_al_table = MagicMock()
    tool.show_probing_geo = MagicMock()
    tool.on_avoid_exc_holes = MagicMock()
    tool.mouse_events_connected = False
    tool.probing_shapes = None
    tool._al_target_name = None
    tool._al_target_obj = None
    tool._al_generation = 0
    if owner is not None:
        tool._al_target_name, tool._al_target_obj = owner
    ui = MagicMock()
    if real_radios:
        from appGUI.GUIElements import RadioSet
        ui.al_mode_radio = RadioSet([{'label': v, 'value': v} for v in ('grid', 'manual')])
        ui.al_method_radio = RadioSet([{'label': v, 'value': v} for v in ('v', 'b')])
        tool.radio_events = MagicMock()
        ui.al_mode_radio.activated_custom.connect(tool.radio_events)
        ui.al_method_radio.activated_custom.connect(tool.radio_events)
        tool._al_core_signals_connected = True
        del tool.connect_signals_at_init
        del tool.to_form
    ui.plot_probing_pts_cb.get_value.return_value = plot_points
    ui.object_combo.get_value.return_value = loaded_obj.obj_options['name'] if loaded_obj is not None else ''
    with patch('appPlugins.ToolLevelling.LevelUI', return_value=ui), \
            patch('appPlugins.Common.levelling_core.ShapeCollection'):
        tool.set_tool_ui()
    return tool


class TestRadioSettings(unittest.TestCase):
    def open_tool(self, target, global_method='v', storage=None):
        return run_set_tool_ui(storage or {}, bool(storage), loaded_obj=target,
                               owner=(target.obj_options['name'], target), real_radios=True,
                               global_method=global_method)


    def test_user_edits_only_selected_target_and_a_b_a_restores(self):
        a = TestTargetOwnership.target('a', tools_al_mode='grid', tools_al_method='v')
        b = TestTargetOwnership.target('b', tools_al_mode='manual', tools_al_method='v')
        tool = self.open_tool(a)
        globals_before = dict(tool.app.options)
        tool._al_target_obj = b
        tool.ui.al_method_radio.set_value('b')
        self.assertEqual(a.obj_options['tools_al_method'], 'b')
        self.assertEqual(b.obj_options['tools_al_method'], 'v')
        tool._new_probing_shapes = MagicMock()
        for target, method in ((b, 'v'), (a, 'b')):
            tool.app.collection.get_by_name.return_value = target
            tool.ui.object_combo.currentText.return_value = target.obj_options['name']
            tool.ui.object_combo.get_value.return_value = target.obj_options['name']
            tool.on_object_changed()
            self.assertEqual(tool.ui.al_mode_radio.get_value(), target.obj_options['tools_al_mode'])
            self.assertEqual(tool.ui.al_method_radio.get_value(), method)
        self.assertEqual(tool.app.options, globals_before)


class TestWordRegex(unittest.TestCase):
    def test_trailing_dot_number_is_replaced_whole(self):
        state = li.new_levelling_state()
        out = li.level_gcode_line('G1 X1 Y1 Z-1.', state, lambda x, y: 0.5)
        self.assertEqual(out, 'G1 X1 Y1 Z-0.5000')


class TestCannedCycles(unittest.TestCase):
    def test_canned_cycle_stops_g1_modal_levelling(self):
        state = li.new_levelling_state()
        offset = lambda x, y: 0.5
        li.level_gcode_line('G1 Z-1', state, offset)
        self.assertEqual(li.level_gcode_line('G81 X3 Y3 Z-2 R1', state, offset), 'G81 X3 Y3 Z-2 R1')
        self.assertEqual(li.level_gcode_line('X4 Y4', state, offset), 'X4 Y4')


class TestDistanceMode(unittest.TestCase):
    @staticmethod
    def off(x, y):
        return 0.5


    def test_g91_moves_not_levelled_and_counted(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G1 Z-1', state, self.off)
        li.level_gcode_line('G91', state, self.off)
        self.assertEqual(li.level_gcode_line('G1 X5 Y5', state, self.off), 'G1 X5 Y5')
        self.assertEqual(state['relative'], 1)

    def test_g91_position_accumulates(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G0 X10 Y10', state, self.off)
        li.level_gcode_line('G91 G0 X5 Y-2', state, self.off)
        self.assertAlmostEqual(state['X'], 15.0)
        self.assertAlmostEqual(state['Y'], 8.0)


    def test_modal_setting_with_axis_words_under_modal_g1_is_levelled(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G1 X0 Y0 Z-1', state, self.off)
        self.assertEqual(li.level_gcode_line('G90 X1 Y1', state, self.off), 'G90 X1 Y1 Z-0.5000')


class TestAllG1Compensated(unittest.TestCase):
    def _z(self, line):
        return float(line.split('Z')[-1])


    def test_no_discontinuity_around_zero(self):
        for nominal in ('-0.0001', '0', '0.0001', '0.3', '0.3001'):
            state = li.new_levelling_state()
            out = li.level_gcode_line('G1 X1 Y1 Z' + nominal, state, lambda x, y: 0.3)
            self.assertAlmostEqual(self._z(out), float(nominal) + 0.3, places=4)


class TestGrblHelpers(unittest.TestCase):


    def test_report_scale(self):
        self.assertEqual(gp.grbl_report_scale('MM', False), 1.0)
        self.assertEqual(gp.grbl_report_scale('IN', True), 1.0)
        self.assertAlmostEqual(gp.grbl_report_scale('IN', False), 1.0 / 25.4)
        self.assertAlmostEqual(gp.grbl_report_scale('MM', True), 25.4)


    def test_modes_confirmed(self):
        gc = '[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]\nok'
        self.assertTrue(gp.grbl_modes_confirmed(gc, 'G21'))
        self.assertFalse(gp.grbl_modes_confirmed(gc, 'G20'))
        self.assertFalse(gp.grbl_modes_confirmed('[GC:G0 G21 G91 G94]', 'G21'))
        self.assertFalse(gp.grbl_modes_confirmed('[GC:G0 G21 G90 G93]', 'G21'))
        self.assertFalse(gp.grbl_modes_confirmed('ok', 'G21'))


class TestGrblSession(unittest.TestCase):
    def _tool(self, port=None):
        tool = make_audit_tool()
        tool.grbl_ser_port = port if port is not None else FakeGrbl()
        tool._grbl_connected = True
        return tool


    def test_recovery_blocks_motion_only(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_reset'
        self.assertIsNone(tool._grbl_reserve('jog', motion=True))
        self.assertIsNotNone(tool._grbl_reserve('param', motion=False))


    def test_transact_error_and_alarm(self):
        fake = FakeGrbl(replies={'G0 Z2': ['error:9'], 'G38.2': ['ALARM:5']})
        tool = self._tool(fake)
        op = tool._grbl_reserve('t')
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'error')
        self.assertEqual(tool._grbl_transact(op, 'G38.2 Z-1 F50', timeout=1.0)[1], 'error')


    def test_pre_cancelled_transact_does_no_io(self):
        port = make_port([b'ok\r\n'])
        tool = self._tool(port)
        op = tool._grbl_reserve('t')
        op.cancel.set()
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'cancelled')
        port.reset_input_buffer.assert_not_called()
        port.write.assert_not_called()


    def test_cancel_during_read(self):
        port = make_port([])
        tool = self._tool(port)
        op = tool._grbl_reserve('t')

        def readline():
            op.cancel.set()
            return b''
        port.readline.side_effect = readline
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=5.0)[1], 'cancelled')

    def test_port_error_does_not_touch_replacement_port(self):
        port = make_port([])
        new_port = make_port([])
        tool = self._tool(port)
        op = tool._grbl_reserve('t')

        def readline():
            tool.grbl_ser_port = new_port
            tool._grbl_connected = True
            raise serial.SerialException('gone')
        port.readline.side_effect = readline
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'port_error')
        new_port.write.assert_not_called()
        new_port.readline.assert_not_called()


class TestGrblConnect(unittest.TestCase):
    def _tool(self):
        tool = make_audit_tool()
        tool.ui.com_list_combo.currentText.return_value = 'COM3'
        tool.ui.baudrates_list_combo.currentText.return_value = '115200'
        tool.ui.al_toolbar.count.return_value = 0
        return tool


    def test_reconnect_turns_needs_reset_into_needs_home(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_reset'
        port = make_port()
        answer = b"Grbl 1.1h ['$' for help]\r\n"
        port.in_waiting = len(answer)
        port.read.return_value = answer
        tool.grbl_ser_port = port
        tool._grbl_connect_attempt = object()
        tool._grbl_connect_finish('COM3', port, tool._grbl_connect_attempt)
        self.assertEqual(tool._grbl_recovery, 'needs_home')


    def test_disconnect_during_operation_waits_and_holds(self):
        tool = self._tool()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        op = tool._grbl_reserve('probe', motion=True)
        tool._grbl_disconnect()
        self.assertTrue(op.cancel.is_set())
        self.assertEqual(fake.commands(), ['!'])
        self.assertTrue(fake.is_open)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'cancelled')
        self.assertEqual(fake.commands(), ['!'])
        tool._grbl_release(op)
        self.assertFalse(fake.is_open)
        self.assertIsNone(tool.grbl_ser_port)
        self.assertFalse(tool._grbl_close_pending)

    def test_connect_blocked_while_operation_runs(self):
        tool = self._tool()
        tool.grbl_ser_port = FakeGrbl()
        tool._grbl_connected = True
        tool._grbl_reserve('probe', motion=True)
        tool._grbl_disconnect()
        with patch('appPlugins.Common.grbl.grbl_client.serial.serial_for_url') as opener:
            tool.on_grbl_connect()
        opener.assert_not_called()


class TestGrblRealtime(unittest.TestCase):
    def _tool(self, port=None):
        tool = make_audit_tool()
        tool.grbl_ser_port = port if port is not None else FakeGrbl()
        tool._grbl_connected = True
        return tool

    def test_stop_during_operation_cancels_and_blocks_next_write(self):
        tool = self._tool()
        fake = tool.grbl_ser_port
        op = tool._grbl_reserve('probe', motion=True)
        tool.on_grbl_stop()
        self.assertTrue(op.cancel.is_set())
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertEqual(tool._grbl_transact(op, 'G0 X1', timeout=1.0)[1], 'cancelled')
        self.assertEqual(fake.commands(), ['!'])

    def test_stop_between_steps_prevents_the_second_command(self):
        tool = self._tool()
        fake = tool.grbl_ser_port
        op = tool._grbl_reserve('probe', motion=True)
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'ok')
        tool.on_grbl_stop()
        self.assertEqual(tool._grbl_transact(op, 'G0 X1', timeout=1.0)[1], 'cancelled')
        self.assertEqual(fake.commands(), ['G0 Z2', '!'])


    def test_reset_during_operation_needs_home(self):
        tool = self._tool()
        op = tool._grbl_reserve('probe', motion=True)
        tool.on_grbl_reset()
        self.assertTrue(op.cancel.is_set())
        self.assertEqual(tool.grbl_ser_port.commands(), ['\x18'])
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_reset_pending)
        self.assertIsNone(tool._grbl_reserve('jog', motion=True))


class TestGrblCommands(unittest.TestCase):
    def _tool(self, port=None):
        tool = make_audit_tool()
        tool.grbl_ser_port = port if port is not None else FakeGrbl()
        tool._grbl_connected = True
        tool.ui.jog_step_entry.get_value.return_value = 1.0
        tool.ui.jog_fr_entry.get_value.return_value = 100.0
        tool.app.options = {'tools_grbl_travelz': 2.0}
        return tool


    def test_jog_blocked_in_recovery(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_home'
        tool.on_grbl_jog(direction='xplus')
        tool.app.worker_task.emit.assert_not_called()


    def test_bad_parameter_number_starts_nothing(self):
        tool = self._tool()
        tool.on_grbl_get_parameter('abc')
        tool.app.worker_task.emit.assert_not_called()

    def test_busy_port_blocks_every_command(self):
        tool = self._tool()
        tool._grbl_reserve('probe', motion=True)
        tool.ui.grbl_command_entry.get_value.return_value = '?'
        tool.on_grbl_jog(direction='xplus')
        tool.on_grbl_send_command()
        tool.on_grbl_report()
        tool.on_grbl_get_parameter('$10')
        tool.app.worker_task.emit.assert_not_called()
        self.assertEqual(tool.grbl_ser_port.commands(), [])


class TestGrblZero(unittest.TestCase):
    def test_zero_x_in_g55(self):
        fake = FakeGrbl(replies={'$G': ['[GC:G0 G55 G17 G21 G90 G94 M5 M9 T0 F0 S0]', 'ok']})
        tool = make_audit_tool()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.on_grbl_zero(axis='x')
        run_worker(tool)
        self.assertEqual(fake.commands(), ['$G', 'G10 L20 P2 X0', '?'])


    def test_bad_wcs_reply_sends_no_g10(self):
        fake = FakeGrbl(replies={'$G': ['garbage', 'ok']})
        tool = make_audit_tool()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.on_grbl_zero(axis='z')
        run_worker(tool)
        self.assertEqual(fake.commands(), ['$G', '?'])
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))

    def test_zero_blocked_in_recovery(self):
        tool = make_audit_tool()
        tool.grbl_ser_port = FakeGrbl()
        tool._grbl_connected = True
        tool._grbl_recovery = 'needs_home'
        tool.on_grbl_zero(axis='x')
        tool.app.worker_task.emit.assert_not_called()


class TestGrblHoming(unittest.TestCase):
    def test_stop_reset_home_ends_recovery(self):
        tool = make_audit_tool()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.on_grbl_stop()
        tool.on_grbl_reset()
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_reset_pending)
        run_worker(tool)  # the reset worker reads the banner: the stop needs Home
        self.assertEqual(tool._grbl_recovery, 'needs_home')
        self.assertFalse(tool._grbl_reset_pending)
        tool.on_grbl_homing()
        run_worker(tool)
        self.assertIn('$H', fake.commands())
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(tool._grbl_reset_pending)
        self.assertEqual(fake.commands()[-1], '?')

    def test_home_without_reset_is_rejected(self):
        tool = make_audit_tool()
        tool.grbl_ser_port = FakeGrbl()
        tool._grbl_connected = True
        tool._grbl_recovery = 'needs_reset'
        tool.on_grbl_homing()
        tool.app.worker_task.emit.assert_not_called()

    def test_failed_home_stays_blocked(self):
        tool = make_audit_tool()
        tool.grbl_ser_port = FakeGrbl(replies={'$H': ['ALARM:9']})
        tool._grbl_connected = True
        tool._grbl_recovery = 'needs_home'
        tool.on_grbl_homing()
        run_worker(tool)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertIsNone(tool._grbl_reserve('probe', motion=True))


    def test_homing_blocks_other_operations(self):
        tool = make_audit_tool()
        tool.grbl_ser_port = FakeGrbl()
        tool._grbl_connected = True
        tool.on_grbl_homing()
        self.assertIsNone(tool._grbl_reserve('probe', motion=True))
        self.assertIsNone(tool._grbl_reserve('parameter'))


class TestResultPublication(unittest.TestCase):
    def _tool(self):
        storage = {
            1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.5},
            2: {'point': Point(10.0, 0.0), 'geo': None, 'height': 0.5},
        }
        tool = make_audit_tool(storage=storage, heights_valid=False, cls=ToolLevelling)
        target = SimpleNamespace(obj_options={'name': 'job'}, kind='cncjob')
        tool._al_target_name = 'job'
        tool._al_target_obj = target
        tool.app.collection.get_by_name.return_value = target
        tool.apply_autolevel = MagicMock()
        return tool, target

    def test_probe_result_is_published_and_applied_to_the_owned_target(self):
        tool, target = self._tool()
        ctx = tool._al_capture_context()
        tool._on_al_result_ready({'kind': 'probe', 'context': ctx, 'heights': {1: -0.1, 2: 0.2}})
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], -0.1)
        self.assertEqual(tool.al_voronoi_geo_storage[2]['height'], 0.2)
        self.assertTrue(tool.al_heights_valid)
        tool.show_probing_geo_sig.emit.assert_called_once_with(True, True)
        tool.apply_autolevel.assert_called_once_with(target_obj=target)

    def test_stale_generation_is_discarded(self):
        tool, target = self._tool()
        ctx = tool._al_capture_context()
        tool._al_bump_generation()
        tool._on_al_result_ready({'kind': 'probe', 'context': ctx, 'heights': {1: -0.1, 2: 0.2}})
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], 0.5)
        self.assertFalse(tool.al_heights_valid)
        tool.apply_autolevel.assert_not_called()
        self.assertTrue(any('[WARNING_NOTCL]' in t for t in emitted(tool)))


    def test_units_change_is_discarded(self):
        tool, target = self._tool()
        ctx = tool._al_capture_context()
        tool.app.app_units = 'IN'
        tool._on_al_result_ready({'kind': 'probe', 'context': ctx, 'heights': {1: -0.1, 2: 0.2}})
        self.assertFalse(tool.al_heights_valid)

    def test_incomplete_heights_are_not_published(self):
        tool, target = self._tool()
        tool.al_heights_valid = True
        ctx = tool._al_capture_context()
        tool._on_al_result_ready({'kind': 'probe', 'context': ctx, 'heights': {1: -0.1}})
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], 0.5)
        self.assertFalse(tool.al_heights_valid)
        tool.apply_autolevel.assert_not_called()


class TestGrblProbeRun(unittest.TestCase):
    POINTS = {1: (0.0, 0.0), 2: (10.0, 0.0)}

    def _tool(self, fake, units='MM', heights_valid=False, old_height=0.0):
        storage = {k: {'point': Point(x, y), 'geo': None, 'height': old_height}
                   for k, (x, y) in self.POINTS.items()}
        tool = make_audit_tool(units=units, storage=storage, heights_valid=heights_valid)
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        target = SimpleNamespace(obj_options={'name': 'job'}, kind='cncjob')
        tool._al_target_name = 'job'
        tool._al_target_obj = target
        tool.app.collection.get_by_name.return_value = target
        tool.ui.ptravelz_entry.get_value.return_value = 2.0
        tool.ui.feedrate_probe_entry.get_value.return_value = 600.0
        tool.ui.pdepth_entry.get_value.return_value = -1.0
        tool._al_on_height_map_ready = MagicMock()
        return tool, target

    def _run(self, tool):
        tool.on_grbl_autolevel()
        run_worker(tool)

    def _after_last_probe(self, fake):
        cmds = fake.commands()
        last = max(i for i, c in enumerate(cmds) if c.startswith('G38.2'))
        return cmds[last + 1:]

    # ---- success ----
    def test_success_publishes_heights_and_applies(self):
        fake = FakeGrbl(heights={(0.0, 0.0): -0.1, (10.0, 0.0): 0.2})
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.1)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[2]['height'], 0.2)
        self.assertTrue(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_called_once_with(target, ANY)
        self.assertIsNone(tool._grbl_op)
        self.assertIsNone(tool._grbl_recovery)
        self.assertEqual(self._after_last_probe(fake), ['G0 Z2.0000', 'G4 P0'])
        self.assertNotIn('M2', fake.commands())


    def test_inch_project_sends_g20_and_scales(self):
        fake = FakeGrbl(heights={(0.0, 0.0): -2.54, (10.0, 0.0): 2.54})
        tool, target = self._tool(fake, units='IN')
        self._run(tool)
        self.assertEqual(fake.commands()[2], 'G20')
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.1, places=4)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[2]['height'], 0.1, places=4)


    # ---- preconditions: no motion ----
    def _assert_no_motion(self, fake):
        self.assertFalse(any(c.startswith('G0') or c.startswith('G38') for c in fake.commands()))

    def test_not_idle_sends_no_motion(self):
        fake = FakeGrbl(state='Alarm')
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertEqual(fake.commands(), ['?'])
        self.assertTrue(any('Alarm' in t for t in emitted(tool)))

    def test_missing_report_setting_aborts(self):
        fake = FakeGrbl(has_report_setting=False)
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertEqual(fake.commands(), ['?', '$$'])

    def test_failed_units_setup_sends_no_retract(self):
        fake = FakeGrbl(replies={'G21': ['error:20']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_no_motion(fake)


    def test_unconfirmed_modes_send_no_motion(self):
        fake = FakeGrbl(replies={'$G': ['[GC:G0 G54 G17 G21 G91 G94]', 'ok']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_no_motion(fake)

    def test_invalid_inputs_schedule_nothing(self):
        for field, value in (('pdepth_entry', float('nan')), ('feedrate_probe_entry', 'abc'),
                             ('pdepth_entry', 0.0), ('feedrate_probe_entry', 0.0),
                             ('ptravelz_entry', 0.0), ('ptravelz_entry', float('inf'))):
            fake = FakeGrbl()
            tool, target = self._tool(fake)
            getattr(tool.ui, field).get_value.return_value = value
            tool.on_grbl_autolevel()
            tool.app.worker_task.emit.assert_not_called()
            self.assertIsNone(tool._grbl_op)


    # ---- errors after the modes are confirmed ----
    def test_refused_move_retracts(self):
        fake = FakeGrbl(replies={'G0 X10': ['error:9']})
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertEqual(fake.commands()[-2:], ['G0 Z2.0000', 'G4 P0'])
        self.assertTrue(any('error:9' in t for t in emitted(tool)))
        self.assertIsNone(tool._grbl_recovery)
        tool._al_on_height_map_ready.assert_not_called()

    def test_failed_retract_is_reported_and_needs_recovery(self):
        fake = FakeGrbl(replies={'G0 Z2': ['error:9']})
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertEqual(fake.commands().count('G0 Z2.0000'), 2)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(any('retract' in t.lower() for t in emitted(tool)))

    # ---- each probe must prove success ----
    def _assert_stopped_at_probe(self, tool, fake, probes=1):
        self.assertEqual(len([c for c in fake.commands() if c.startswith('G38.2')]), probes)
        self.assertEqual([c for c in self._after_last_probe(fake) if c != '!'], [])
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertFalse(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_not_called()

    def test_alarm_on_probe_stops(self):
        fake = FakeGrbl(replies={'G38.2': ['ALARM:4']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_stopped_at_probe(tool, fake)


    def test_non_finite_probe_record_stops(self):
        fake = FakeGrbl(replies={'G38.2': ['[PRB:nan,0.000,0.000:1]', 'ok']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_stopped_at_probe(tool, fake)

    def test_probe_record_at_wrong_position_stops(self):
        fake = FakeGrbl(replies={'G38.2': ['[PRB:0.000,0.000,0.100:1]', 'ok']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_stopped_at_probe(tool, fake, probes=2)

    def test_timeout_holds_and_needs_recovery(self):
        fake = FakeGrbl(replies={'G38.2': []})
        tool, target = self._tool(fake)
        with patch('appPlugins.ToolGcodeSender.Sender.probe_timeout_s', return_value=0.05):
            self._run(tool)
        self._assert_stopped_at_probe(tool, fake)
        self.assertIn('!', fake.commands())


    # ---- recovery and ownership ----
    def test_timeout_blocks_next_probe_until_reset_and_home(self):
        fake = FakeGrbl(replies={'G38.2': []})
        tool, target = self._tool(fake)
        with patch('appPlugins.ToolGcodeSender.Sender.probe_timeout_s', return_value=0.05):
            self._run(tool)
        tool.on_grbl_autolevel()
        self.assertEqual(tool.app.worker_task.emit.call_count, 1)
        tool.on_grbl_reset()
        run_worker(tool)  # the reset worker
        tool.on_grbl_homing()
        run_worker(tool)
        self.assertIsNone(tool._grbl_recovery)
        fake.replies.clear()
        tool.on_grbl_autolevel()
        self.assertEqual(tool.app.worker_task.emit.call_count, 4)

    def test_cancel_between_steps_writes_nothing_after_stop(self):
        def hook():
            cmds = fake.commands()
            if cmds and cmds[-1].startswith('G0 X10') and '!' not in cmds:
                tool.on_grbl_stop()
        fake = FakeGrbl(read_hook=hook)
        tool, target = self._tool(fake)
        self._run(tool)
        cmds = fake.commands()
        self.assertEqual(cmds[cmds.index('!') + 1:], [])
        tool._al_on_height_map_ready.assert_not_called()

    def test_queued_sender_blocks_probe(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        tool.ui.grbl_command_entry.get_value.return_value = 'G0 X1'
        tool.on_grbl_send_command()
        tool.on_grbl_autolevel()
        self.assertEqual(tool.app.worker_task.emit.call_count, 1)

    def test_stale_result_after_points_regenerated(self):
        fake = FakeGrbl(heights={(0.0, 0.0): -0.1})
        tool, target = self._tool(fake)
        tool.on_grbl_autolevel()
        tool._al_bump_generation()
        run_worker(tool)
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], 0.0)
        self.assertFalse(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_not_called()


class TestGrblEdgeProbe(unittest.TestCase):
    def _tool(self, fake, units='MM'):
        tool = make_audit_tool(units=units)
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.ui.check_mode_cb.get_value.return_value = False
        tool.ui.edge_probe_axis_radio.get_value.return_value = 'xplus'
        tool.ui.edge_probe_distance_entry.get_value.return_value = 5.0
        tool.ui.edge_probe_feed_entry.get_value.return_value = 100.0
        tool.ui.edge_probe_pulloff_entry.get_value.return_value = 1.0
        tool.ui.edge_probe_travelz_entry.get_value.return_value = 2.0
        return tool

    def _result(self, tool):
        return tool.edge_probe_result_sig.emit.call_args.args[0]

    def test_spindle_must_be_explicitly_off_before_motion(self):
        for spindle in ('M3 S10000', 'M4 S10000', '', 'M5.1', 'M5 M3', 'M5 M4', 'M5 M03', 'M5 M04'):
            with self.subTest(spindle=spindle):
                fake = AxisFakeGrbl(replies={
                    '$G': ['[GC:G0 G54 G17 G21 G90 G94 %s]' % spindle, 'ok']})
                tool = self._tool(fake)
                tool.on_grbl_edge_probe()
                run_worker(tool)
                self.assertEqual([c for c in fake.commands() if c.startswith(('G0', 'G38'))], [])
                self.assertEqual(self._result(tool)['kind'], 'failed')
                self.assertTrue(any('spindle' in text.lower() for text in emitted(tool)))

    def test_stale_widget_units_refuse_before_reserving(self):
        fake = AxisFakeGrbl()
        tool = self._tool(fake)
        tool.app.app_units = 'IN'
        tool.on_grbl_edge_probe()
        tool.app.worker_task.emit.assert_not_called()
        self.assertIsNone(tool._grbl_op)
        self.assertEqual(fake.commands(), [])
        self.assertTrue(any('units' in text.lower() for text in emitted(tool)))

    def test_unit_change_while_queued_or_running_stops_subsequent_motion(self):
        for boundary in ('queued', 'G94', 'G38.2', 'G4 P0'):
            for owner in ('project', 'sender'):
                with self.subTest(boundary=boundary, owner=owner):
                    fake = AxisFakeGrbl()
                    tool = self._tool(fake)
                    changed_at = []

                    def change():
                        if owner == 'project':
                            tool.app.app_units = 'IN'
                        else:
                            tool.units = 'IN'
                        changed_at.append(len(fake.commands()))

                    def hook():
                        if not changed_at and fake.commands()[-1].startswith(boundary):
                            change()

                    tool.on_grbl_center_probe()
                    if boundary == 'queued':
                        change()
                    else:
                        fake.read_hook = hook
                    run_worker(tool)
                    self.assertTrue(changed_at)
                    subsequent = fake.commands()[changed_at[0]:]
                    self.assertEqual([c for c in subsequent if c.startswith(('G0', 'G38'))], [])
                    self.assertEqual(self._result(tool)['kind'], 'failed')
                    self.assertIsNone(tool._grbl_op)
                    self.assertTrue(any('units' in text.lower() for text in emitted(tool)))

    def test_directional_contact_is_published_in_work_units(self):
        fake = AxisFakeGrbl()
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'edge')
        self.assertEqual(result['contact'], (5.0, 0.0, 0.0))
        self.assertIn('G38.2 X5.0000 F100.0000', fake.commands())
        self.assertIn('G0 X4.0000', fake.commands())
        self.assertIsNone(tool._grbl_recovery)

    def test_inch_project_converts_the_controller_report(self):
        fake = AxisFakeGrbl()
        tool = self._tool(fake, units='IN')
        tool.on_grbl_edge_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertIn('G20', fake.commands())
        self.assertEqual(result['contact'], (5.0, 0.0, 0.0))

    def test_centre_is_the_midpoint_of_opposed_contacts(self):
        fake = AxisFakeGrbl(edges={'xplus': 3.2, 'xminus': -3.8, 'yplus': 2.5, 'yminus': -1.5})
        tool = self._tool(fake)
        tool.on_grbl_center_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'center')
        self.assertAlmostEqual(result['center'][0], -0.3, places=4)
        self.assertAlmostEqual(result['center'][1], 0.5, places=4)
        self.assertEqual(set(result['contacts']), {'xplus', 'xminus', 'yplus', 'yminus'})
        self.assertEqual(fake.commands().count('G38.2 X5.0000 F100.0000'), 1)
        self.assertEqual(fake.commands().count('G38.2 X-5.0000 F100.0000'), 1)
        self.assertEqual(fake.commands().count('G38.2 Y5.0000 F100.0000'), 1)
        self.assertEqual(fake.commands().count('G38.2 Y-5.0000 F100.0000'), 1)

    def test_failed_contact_publishes_no_result(self):
        fake = AxisFakeGrbl(fail_probe_at=1)
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'failed')
        self.assertNotIn('contact', result)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')

    def test_no_contact_publishes_no_result(self):
        fake = AxisFakeGrbl(no_contact_at=1)
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'failed')
        self.assertNotIn('contact', result)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')

    def test_cancel_between_contacts_publishes_no_partial_result(self):
        fake = AxisFakeGrbl(edges={'xplus': 3.2, 'xminus': -3.8, 'yplus': 2.5, 'yminus': -1.5})
        tool = self._tool(fake)

        def hook():
            cmds = fake.commands()
            if cmds and cmds[-1].startswith('G4 P0') and any(c.startswith('G38.2') for c in cmds) \
                    and '!' not in cmds:
                tool.on_grbl_stop()

        fake.read_hook = hook
        tool.on_grbl_center_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'failed')
        self.assertNotIn('center', result)
        self.assertEqual(fake.commands()[fake.commands().index('!') + 1:], [])

    def test_not_idle_sends_no_motion(self):
        fake = AxisFakeGrbl(state='Alarm')
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual([c for c in fake.commands() if c.startswith(('G0', 'G38'))], [])
        self.assertEqual(self._result(tool)['kind'], 'failed')

    def test_invalid_inputs_schedule_nothing(self):
        for field, value in (('edge_probe_distance_entry', 0.0),
                             ('edge_probe_feed_entry', float('nan')),
                             ('edge_probe_pulloff_entry', -1.0),
                             ('edge_probe_travelz_entry', 0.0)):
            fake = AxisFakeGrbl()
            tool = self._tool(fake)
            getattr(tool.ui, field).get_value.return_value = value
            tool.on_grbl_edge_probe()
            tool.app.worker_task.emit.assert_not_called()
            self.assertIsNone(tool._grbl_op)

    def test_default_pulloff_is_bounded_in_a_smaller_hole(self):
        # a 0.8 mm hole: contact at +0.4, the default 1.0 pull-off would reach -0.6
        fake = AxisFakeGrbl(edges={'xplus': 0.4})
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual(self._result(tool)['kind'], 'edge')
        self.assertIn('G0 X0.0000', fake.commands())
        self.assertEqual([c for c in fake.commands() if c.startswith('G0 X-')], [])

    def test_off_centre_start_pulls_off_to_the_start_not_the_opposite_wall(self):
        fake = MovingAxisFakeGrbl(edges={'xplus': 0.4})
        fake.x = 0.2
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual(self._result(tool)['kind'], 'edge')
        self.assertIn('G0 X0.2000', fake.commands())
        self.assertNotIn('G0 X-0.8000', fake.commands())

    def test_impossible_contact_publishes_no_result_and_no_pulloff(self):
        fake = AxisFakeGrbl(edges={'xplus': -1.0})
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual(self._result(tool)['kind'], 'failed')
        self.assertEqual([c for c in fake.commands() if c.startswith('G0')], [])
        self.assertEqual(tool._grbl_recovery, 'needs_reset')

    def test_oversize_pulloff_is_clamped_to_the_contact_to_start_travel(self):
        fake = AxisFakeGrbl(edges={'xplus': 3.2})
        tool = self._tool(fake)
        tool.ui.edge_probe_pulloff_entry.get_value.return_value = 10000.0
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual(self._result(tool)['kind'], 'edge')
        self.assertIn('G0 X0.0000', fake.commands())

    def test_nonzero_wcs_and_inch_report_convert_to_work_units(self):
        fake = OffsetAxisFakeGrbl(edges={'xplus': 3.2}, offset=(10.0, 20.0, 0.0),
                                  report_inches=True)
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        result = self._result(tool)
        self.assertEqual(result['kind'], 'edge')
        # GRBL's four-place inch report is quantized to 0.00254 mm.
        self.assertAlmostEqual(result['contact'][0], 3.2, delta=0.0026)
        self.assertAlmostEqual(result['contact'][1], 0.0, delta=0.0026)
        self.assertAlmostEqual(result['contact'][2], 0.0, delta=0.0026)
        self.assertIn('G21', fake.commands())
        self.assertTrue(any(c.startswith('G0 X2.2') for c in fake.commands()))

    def test_a_recoverable_failure_restores_the_original_modes_after_the_retract(self):
        fake = ModalFakeGrbl(distance='G91', feed='G93', fail_on='G0 X4.0000')
        fake.units_word = 'G20'
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        commands = fake.commands()
        self.assertEqual(self._result(tool)['kind'], 'failed')
        self.assertIsNone(tool._grbl_recovery)
        self.assertEqual([c for c in commands if c in ('G20', 'G21', 'G90', 'G91', 'G93', 'G94')],
                         ['G21', 'G90', 'G94', 'G20', 'G91', 'G93'])
        self.assertLess(commands.index('G0 Z2.0000'), commands.index('G20'))

    def test_an_alarm_with_changed_modes_requires_reset_and_restores_nothing(self):
        fake = ModalFakeGrbl(distance='G91', feed='G93', fail_probe_at=1)
        fake.units_word = 'G20'
        tool = self._tool(fake)
        tool.on_grbl_edge_probe()
        run_worker(tool)
        self.assertEqual(self._result(tool)['kind'], 'failed')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertEqual([c for c in fake.commands() if c in ('G20', 'G91', 'G93')], [])
        self.assertTrue(any('not restored' in text and 'Reset' in text for text in emitted(tool)))


class TestManualFinish(unittest.TestCase):
    def _tool(self, method):
        storage = {
            1: {'point': Point(1.0, 1.0), 'geo': None, 'height': 0.0},
            2: {'point': Point(5.0, 1.0), 'geo': None, 'height': 0.0},
        }
        tool = make_audit_tool(storage=storage)
        tool.app.options = {'global_selection_shape': False}
        tool.old_selection_state = 'OLD'
        tool.mouse_events_connected = True
        tool.ui.al_method_radio.get_value.return_value = method
        tool.probing_gcode = MagicMock(return_value='GCODE')
        tool.generate_voronoi_geometry = MagicMock()
        tool.generate_bilinear_geometry = MagicMock(
            side_effect=lambda pts: setattr(tool, 'al_bilinear_geo_storage', pts))
        tool.plot_probing_geo = MagicMock()
        tool.show_probing_geo = MagicMock()
        return tool

    def _check_released(self, tool):
        disconnected = [c.args for c in tool.app.plotcanvas.graph_event_disconnect.call_args_list]
        self.assertIn(('key_press', tool.on_key_press), disconnected)
        self.assertIn(('mouse_release', tool.on_mouse_click_release), disconnected)
        self.assertIn(('mouse_move', tool.on_mouse_move), disconnected)
        self.assertFalse(tool.mouse_events_connected)
        self.assertEqual(tool.app.options['global_selection_shape'], 'OLD')

    def test_right_click_bilinear_builds_probing_gcode(self):
        tool = self._tool('b')
        tool.on_mouse_click_release(SimpleNamespace(pos=(0.0, 0.0), button=2))
        self._check_released(tool)
        self.assertEqual(tool.al_bilinear_geo_storage, [(1.0, 1.0, 0.0), (5.0, 1.0, 0.0)])
        self.assertEqual(tool.probing_gcode_text, 'GCODE')
        tool.probing_gcode.assert_called_once_with(storage=tool.al_bilinear_geo_storage)


    def test_finish_with_no_points_clears_gcode(self):
        tool = self._tool('v')
        tool.al_voronoi_geo_storage.clear()
        tool.probing_gcode_text = 'OLD GCODE'
        tool._finish_manual_points()
        self.assertEqual(tool.probing_gcode_text, '')
        tool.probing_gcode.assert_not_called()


class TestSignalsOnce(unittest.TestCase):

    def test_machine_safety_stops_every_controller(self):
        from appCommon import MachineSafety
        tool = make_audit_tool()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        failing = MagicMock(side_effect=RuntimeError('broken controller'))
        for stopper in (failing, tool._grbl_shutdown):
            MachineSafety.register(stopper)
            self.addCleanup(MachineSafety.unregister, stopper)
        MachineSafety.stop_all()  # a failing stop function does not stop the others
        self.assertEqual(fake.written[-2:], [b'!', b'\x18'])
        self.assertFalse(fake.is_open)


class TestTargetOwnership(unittest.TestCase):
    @staticmethod
    def target(name='job_a', **options):
        opts = {'type': 'Geometry', 'name': name}
        opts.update(options)
        return SimpleNamespace(kind='cncjob', is_segmented_gcode=True, obj_options=opts)

    def _tool(self, owner_name, owner_obj, new_name, new_obj):
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.3}}
        tool = make_audit_tool(storage=storage, heights_valid=True)
        tool.al_bilinear_geo_storage = [(0.0, 0.0, 0.0)]
        tool.probing_gcode_text = 'OLD'
        tool._al_target_name = owner_name
        tool._al_target_obj = owner_obj
        tool.ui.object_combo.currentText.return_value = new_name
        tool.app.collection.get_by_name.return_value = new_obj
        tool._new_probing_shapes = MagicMock()
        tool._update_mode_radio_ui = MagicMock()
        tool._update_method_radio_ui = MagicMock()
        tool.show_probing_geo = MagicMock()
        return tool

    def test_other_object_clears_probe_data(self):
        a = self.target('job_a')
        tool = self._tool('job_a', a, 'job_b', self.target('job_b'))
        tool.on_object_changed()
        self.assertEqual(tool.al_voronoi_geo_storage, {})
        self.assertEqual(tool.al_bilinear_geo_storage, [])
        self.assertFalse(tool.al_heights_valid)
        self.assertEqual(tool.probing_gcode_text, '')
        self.assertIsNone(tool._al_target_name)
        self.assertEqual(tool._al_generation, 1)


class TestPolygonsAndAdd(unittest.TestCase):

    def test_excellon_single_polygon_is_checked(self):
        tool = make_audit_tool()
        hole = Point(0.0, 0.0).buffer(0.5)
        exc = SimpleNamespace(kind='excellon', obj_options={'plot': True}, solid_geometry=hole)
        tool.app.collection.get_list.return_value = [exc]
        tool.ui.avoid_exc_combo.get_value.return_value = 'drills'
        tool.app.collection.get_by_name.return_value = exc
        result = tool.check_point_over_excellon(Point(0.0, 0.0).buffer(0.1), True)
        self.assertFalse(result.is_empty)

    def _add_tool(self, gcode_parsed):
        tool = make_audit_tool()
        target = SimpleNamespace(gcode_parsed=gcode_parsed, obj_options={'name': 'job_a'})
        tool.ui.object_combo.get_value.return_value = 'job_a'
        tool.app.collection.get_by_name.return_value = target
        tool.ui.al_mode_radio.get_value.return_value = 'grid'
        tool.on_add_grid_points = MagicMock()
        tool.on_add_manual_points = MagicMock()
        tool.al_bilinear_geo_storage = [(0.0, 0.0, 0.0)]
        tool.probing_gcode_text = 'OLD'
        return tool, target


class TestResultSignalThread(unittest.TestCase):
    def test_result_from_worker_thread_is_published_in_gui_thread(self):
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.5}}
        tool = make_audit_tool(storage=storage)
        QtWidgets.QWidget.__init__(tool)          # make the QObject part valid
        del tool.al_result_ready_sig              # use the real class signal
        seen = []

        def slot(result):
            seen.append(threading.current_thread() is threading.main_thread())
            tool._on_al_result_ready(result)

        tool.al_result_ready_sig.connect(slot)
        ctx = tool._al_capture_context()
        worker = threading.Thread(target=lambda: tool.al_result_ready_sig.emit(
            {'kind': 'import', 'context': ctx, 'heights': {1: -0.2}}))
        worker.start()
        worker.join()

        deadline = time.monotonic() + 2.0
        while not seen and time.monotonic() < deadline:
            _qapp.processEvents()
        self.assertEqual(seen, [True])
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], -0.2)
        self.assertTrue(tool.al_heights_valid)


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


class TestNetworkConnection:
    def make_tool(self):
        tool = make_audit_tool()
        tool.ui.controller_combo.currentData.return_value = 'grblhal'
        tool.ui.connection_combo.currentData.return_value = 'network'
        tool.ui.network_host_entry.get_value.return_value = ' localhost '
        tool.ui.network_port_entry.get_value.return_value = 23
        tool.network_connect_result_sig = MagicMock()
        return tool


    def test_invalid_network_endpoints_never_schedule_open(self):
        for host, port in (('', 23), ('host/path', 23), ('host?x', 23), ('user@host', 23),
                           ('host\n', 23), ('socket://host', 23), ('host', 0), ('host', 65536),
                           ('host', '1.5'), ('::1%eth0?logging=debug', 23)):
            tool = self.make_tool()
            tool.ui.network_host_entry.get_value.return_value = host
            tool.ui.network_port_entry.get_value.return_value = port
            tool.on_grbl_connect()
            tool.app.worker_task.emit.assert_not_called()


class PrecisionGrbl(FakeGrbl):
    def __init__(self, offset=(0.0, 0.0, 0.0), coarse=-0.1, fine=-0.12, **kwargs):
        super().__init__(**kwargs)
        self.offset = offset
        self.coarse = coarse
        self.fine = fine

    def _answer(self, cmd):
        if cmd == '$#':
            self._send('[G54:%.4f,%.4f,%.4f]' % tuple(self._report(v) for v in self.offset),
                       '[G92:0,0,0]', '[TLO:0]', 'ok')
        elif cmd.startswith('G38.2') and self.fail_probe_at != self.probe_count + 1:
            self.probe_count += 1
            z = self.coarse if self.probe_count % 2 else self.fine
            self._send('[PRB:%.4f,%.4f,%.4f:1]' % (
                self._report(self.x + self.offset[0]), self._report(self.y + self.offset[1]),
                self._report(z + self.offset[2])), 'ok')
        else:
            super()._answer(cmd)


class TestPrecisionProbing(unittest.TestCase):
    def tool(self, fake=None, units='MM'):
        fake = fake or PrecisionGrbl()
        tool, target = TestGrblProbeRun()._tool(fake, units=units)
        tool.ui.precision_probe_cb.get_value.return_value = True
        tool.ui.precision_feed_entry.get_value.return_value = 10.0
        tool.ui.precision_retract_entry.get_value.return_value = 0.5
        return tool, target, fake

    def run_probe(self, tool):
        tool.on_grbl_autolevel()
        run_worker(tool)

    def test_sequence_publishes_only_fine_after_final_retract(self):
        tool, target, fake = self.tool()
        self.run_probe(tool)
        commands = fake.commands()
        self.assertEqual(fake.probe_count, 4)
        probes = [c for c in commands if c.startswith('G38.2')]
        self.assertEqual(probes, ['G38.2 Z-1.0000 F600.0000', 'G38.2 Z-1.0000 F10.0000'] * 2)
        first = commands.index(probes[0])
        fine = commands.index(probes[1])
        self.assertIn('G0 Z0.4000', commands[first + 1:fine])
        self.assertEqual(commands[fine - 1], 'G4 P0')
        lateral = commands.index('G0 X10.0000 Y0.0000')
        self.assertIn('G0 Z2.0000', commands[fine + 1:lateral])
        self.assertNotIn('G91', commands)
        self.assertTrue(tool.al_heights_valid)
        self.assertEqual([v['height'] for v in tool.al_voronoi_geo_storage.values()], [-0.12, -0.12])
        tool._al_on_height_map_ready.assert_called_once_with(target, ANY)
        self.assertEqual(sum(c.args[0]['kind'] == 'probe'
                             for c in tool.al_result_ready_sig.emit.call_args_list), 1)
        self.assertEqual(commands[-2:], ['G0 Z2.0000', 'G4 P0'])

    def test_work_offset_is_removed_before_contact_relative_retract(self):
        tool, target, fake = self.tool(PrecisionGrbl(offset=(10, 20, 30)))
        self.run_probe(tool)
        self.assertIn('G0 Z0.4000', fake.commands())
        self.assertNotIn('G0 Z30.4000', fake.commands())
        self.assertTrue(tool.al_heights_valid)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.12)


    def test_inch_units_and_inch_reports(self):
        for units, report_inches in (('MM', True), ('IN', False), ('IN', True)):
            with self.subTest(units=units, report_inches=report_inches):
                tool, target, fake = self.tool(PrecisionGrbl(report_inches=report_inches), units)
                self.run_probe(tool)
                self.assertTrue(tool.al_heights_valid)
                # GRBL's four-place inch report is quantized to 0.00254 mm.
                resolution = 0.00254 if report_inches and units == 'MM' else 0.0001
                self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.12, delta=resolution / 2)

    def test_fast_or_fine_failure_never_publishes_or_moves_laterally(self):
        for stage in (1, 2):
            with self.subTest(stage=stage):
                tool, target, fake = self.tool(PrecisionGrbl(fail_probe_at=stage))
                self.run_probe(tool)
                self.assertFalse(tool.al_heights_valid)
                self.assertEqual(fake.probe_count, stage)
                self.assertNotIn('G0 X10.0000 Y0.0000', fake.commands())
                self.assertEqual(tool._grbl_recovery, 'needs_reset')
                tool._al_on_height_map_ready.assert_not_called()

    def test_cancel_between_stages_has_no_subsequent_motion(self):
        tool, target, fake = self.tool()

        def stop():
            if fake.commands()[-1:] == ['G0 Z0.4000']:
                tool.on_grbl_stop()

        fake.read_hook = stop
        self.run_probe(tool)
        self.assertEqual(fake.probe_count, 1)
        self.assertEqual(fake.commands()[-1], '!')
        self.assertFalse(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_not_called()

    def test_invalid_precision_settings_schedule_no_motion(self):
        for field, value in (
                ('precision_feed_entry', 0), ('precision_feed_entry', -1),
                ('precision_feed_entry', 601), ('precision_feed_entry', float('nan')),
                ('precision_feed_entry', 'bad'), ('precision_feed_entry', 0.00001),
                ('precision_retract_entry', 0), ('precision_retract_entry', -1),
                ('precision_retract_entry', float('inf')), ('precision_retract_entry', 4),
                ('precision_retract_entry', 0.00001)):
            with self.subTest(field=field, value=value):
                tool, target, fake = self.tool()
                getattr(tool.ui, field).get_value.return_value = value
                tool.on_grbl_autolevel()
                tool.app.worker_task.emit.assert_not_called()
                self.assertEqual(fake.commands(), [])

    def test_contact_bounds_prevent_unsafe_intermediate_retract(self):
        for coarse in (-1.1, 1.8, 2.1):
            with self.subTest(coarse=coarse):
                tool, target, fake = self.tool(PrecisionGrbl(coarse=coarse))
                self.run_probe(tool)
                self.assertEqual(fake.probe_count, 1)
                self.assertFalse(tool.al_heights_valid)
                self.assertNotIn('G0 X10.0000 Y0.0000', fake.commands())
                self.assertTrue(any('clearance' in text.lower() or 'probe_invalid' in text
                                    for text in emitted(tool)))

    def test_changed_frame_between_touches_holds_without_guessed_retract(self):
        tool, target, fake = self.tool()
        original = fake._answer

        def answer(cmd):
            if cmd == '$G' and fake.probe_count:
                fake._send('[GC:G0 G55 G17 G21 G90 G94]', 'ok')
            else:
                original(cmd)

        fake._answer = answer
        self.run_probe(tool)
        self.assertEqual(fake.probe_count, 1)
        self.assertNotIn('G0 Z0.4000', fake.commands())
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertFalse(tool.al_heights_valid)


    def test_invalid_fine_measurements_never_publish(self):
        for record in ('[PRB:0,0,-0.12:0]', '[PRB:nan,0,0:1]', '[PRB:0,0,-2:1]',
                       '[PRB:0,0,0.8:1]', '[PRB:10,0,-0.12:1]', '',
                       '[PRB:0,0,-0.12:1]\n[PRB:0,0,-0.12:1]'):
            with self.subTest(record=record):
                tool, target, fake = self.tool()
                original = fake._answer

                def answer(cmd):
                    if cmd.startswith('G38.2') and fake.probe_count == 1:
                        fake.probe_count += 1
                        fake._send(record, 'ok')
                    else:
                        original(cmd)

                fake._answer = answer
                self.run_probe(tool)
                self.assertFalse(tool.al_heights_valid)
                self.assertEqual(fake.probe_count, 2)
                self.assertNotIn('G0 X10.0000 Y0.0000', fake.commands())
                tool._al_on_height_map_ready.assert_not_called()


    def test_unknown_modes_or_offset_after_touch_require_recovery(self):
        for command, reply in (('$G', ['error:9']), ('$G', ['[GC:G0 G54 G17 G21 G91 G94]', 'ok']),
                               ('$#', ['ok']), ('$#', ['[G54:0,0,1]', '[G92:0,0,0]', '[TLO:0]', 'ok'])):
            with self.subTest(command=command, reply=reply):
                tool, target, fake = self.tool()
                original = fake._answer

                def answer(cmd):
                    if cmd == command and fake.probe_count:
                        fake._send(*reply)
                    else:
                        original(cmd)

                fake._answer = answer
                self.run_probe(tool)
                coarse = fake.commands().index('G38.2 Z-1.0000 F600.0000')
                self.assertFalse(any(c.startswith('G0') for c in fake.commands()[coarse + 1:]))
                self.assertEqual(tool._grbl_recovery, 'needs_reset')
                self.assertFalse(tool.al_heights_valid)


def result_tool(units='MM'):
    fake = FakeGrbl(heights={(0.0, 0.0): 0.1, (10.0, 0.0): 0.2})
    tool, _ = TestGrblProbeRun()._tool(fake, units=units)
    QtWidgets.QWidget.__init__(tool)
    del tool.al_result_ready_sig
    tool.connect_signals_at_init()
    tool.ui.object_combo.get_value.return_value = 'job'
    # Create the points through production publication, rather than inventing
    # the name of the units metadata that GREEN will introduce.
    tool.al_voronoi_geo_storage.clear()
    tool.al_result_ready_sig.emit({
        'kind': 'import_points', 'context': tool._al_capture_context(),
        'points': [(0.0, 0.0, 0.5), (10.0, 0.0, 0.5)],
    })
    tool.build_al_table_sig.reset_mock()
    tool.show_probing_geo_sig.reset_mock()
    # the setup import applies the map (import now applies); tests count only their own applies
    tool._al_on_height_map_ready.reset_mock()
    return tool, fake


def in_worker(callback):
    errors = []

    def run():
        try:
            callback()
        except BaseException as err:
            errors.append(err)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), 'worker did not finish'
    if errors:
        raise errors[0]


def drain_results():
    _qapp.processEvents()
    _qapp.processEvents()


def heights(tool):
    return {key: value['height'] for key, value in tool.al_voronoi_geo_storage.items()}


def start_import(tool, path):
    with patch('appPlugins.ToolLevelling.QtWidgets.QFileDialog.getOpenFileName',
               return_value=(str(path), '')):
        tool.on_import_height_map()
    task = tool.app.worker_task.emit.call_args
    return task.args[0] if task is not None else None


def run_import(task):
    in_worker(lambda: task['fcn'](*task['params']))


@pytest.mark.parametrize('old_kind,complete_b', [('import', False), ('failed', True)], ids=['import-b_started', 'failed-b_completed'])
def test_old_import_result_cannot_change_replacement(tmp_path, old_kind, complete_b):
    """Expected RED: same-point replacement must retire both success and failure A."""
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    a = start_import(tool, path)
    old_context = a['params'][1]
    b = start_import(tool, path)
    if complete_b:
        run_import(b)
        drain_results()
        assert heights(tool) == {1: 0.3, 2: 0.4}
        assert tool.al_heights_valid
    before = heights(tool), tool.al_heights_valid
    publications = tool.build_al_table_sig.emit.call_count
    result = {'kind': old_kind, 'context': old_context, 'heights': {1: 9.0, 2: 9.0}}
    in_worker(lambda: tool.al_result_ready_sig.emit(result))
    drain_results()
    assert (heights(tool), tool.al_heights_valid) == before
    assert tool.build_al_table_sig.emit.call_count == publications


@pytest.mark.parametrize('replacement', ['import', 'probe'])
def test_queued_probe_is_rejected_after_replacement_starts(tmp_path, replacement):
    """Expected RED: released probe A must not publish after B starts."""
    tool, _ = result_tool()
    tool.on_grbl_autolevel()
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    if replacement == 'import':
        start_import(tool, path)
    else:
        tool.on_grbl_autolevel()
    before = heights(tool)
    drain_results()
    assert heights(tool) == before
    assert not tool.al_heights_valid
    tool._al_on_height_map_ready.assert_not_called()
    tool.build_al_table_sig.emit.assert_not_called()


@pytest.mark.parametrize('action', ['stop', 'reset', 'cancel'])
def test_released_probe_evidence_is_checked_before_gui_storage_update(action):
    """Expected RED: cancellation/safety evidence survives serial ownership release."""
    tool, _ = result_tool()
    tool.on_grbl_autolevel()
    op = tool._grbl_op
    assert op is not None
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    assert heights(tool) == {1: 0.5, 2: 0.5}, 'worker must not publish directly'
    if action == 'cancel':
        op.cancel.set()
    else:
        getattr(tool, 'on_grbl_' + action)()
        assert tool._grbl_safety_generation != op.safety_generation
    drain_results()
    assert heights(tool) == {1: 0.5, 2: 0.5}
    assert not tool.al_heights_valid
    tool._al_on_height_map_ready.assert_not_called()
    tool.build_al_table_sig.emit.assert_not_called()
    tool.show_probing_geo_sig.emit.assert_not_called()


def test_released_current_probe_publishes_atomically_on_gui_thread():
    """Expected PASS: no active operation is required for queued publication."""
    tool, _ = result_tool()
    seen = []
    tool.build_al_table_sig.emit.side_effect = lambda: seen.append(
        (threading.current_thread() is threading.main_thread(), heights(tool)))
    tool.on_grbl_autolevel()
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    assert not seen
    assert heights(tool) == {1: 0.5, 2: 0.5}
    drain_results()
    assert seen == [(True, {1: 0.1, 2: 0.2})]
    assert tool.al_heights_valid
    tool._al_on_height_map_ready.assert_called_once_with(tool._al_target_obj, ANY)


@pytest.mark.parametrize('entry', ['probe', 'import', 'gui_import'])
def test_existing_point_units_cannot_be_reinterpreted(tmp_path, entry):
    """Expected RED: points created in MM cannot be reused as IN coordinates."""
    tool, fake = result_tool()
    tool.app.app_units = 'IN'
    tool.units = 'IN'
    tool.app.worker_task.emit.reset_mock()
    if entry == 'probe':
        tool.on_grbl_autolevel()
        assert tool._grbl_op is None, 'The refused probe must release the port at once'
        scheduled = tool.app.worker_task.emit.call_args
        if scheduled:
            in_worker(lambda: run_worker(tool))
            drain_results()
        assert not any(cmd.startswith(('G0', 'G38.2')) for cmd in fake.commands())
        tool.app.worker_task.emit.assert_not_called()
    else:
        path = tmp_path / 'map.txt'
        path.write_text('0,0,0.3\n10,0,0.4\n')
        if entry == 'gui_import':
            start_import(tool, path)
            tool.app.worker_task.emit.assert_not_called()
        else:
            tool.import_height_map(str(path))
        assert not tool.al_heights_valid
        assert heights(tool) != {1: 0.3, 2: 0.4}
