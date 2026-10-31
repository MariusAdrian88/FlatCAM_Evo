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

    def test_saved_method_wins_over_global_and_sets_minima(self):
        for saved, global_method in (('b', 'v'), ('v', 'b')):
            with self.subTest(saved=saved):
                target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method=saved)
                before = dict(target.obj_options)
                tool = self.open_tool(target, global_method)
                self.assertEqual(tool.ui.al_mode_radio.get_value(), 'grid')
                self.assertEqual(tool.ui.al_method_radio.get_value(), saved)
                tool.ui.al_rows_entry.setMinimum.assert_called_with(2 if saved == 'b' else 1)
                self.assertEqual(target.obj_options, before)

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

    def test_manual_transition_persists_effective_method_and_resets_once(self):
        target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method='b')
        tool = self.open_tool(target)
        tool.al_voronoi_geo_storage = {1: {'point': Point(0, 0), 'height': 0.2}}
        tool.al_heights_valid = True
        generation = tool._al_generation
        tool.build_al_table.reset_mock()
        tool.ui.al_mode_radio.set_value('manual')
        self.assertEqual(target.obj_options['tools_al_mode'], 'manual')
        self.assertEqual(target.obj_options['tools_al_method'], 'v')
        self.assertEqual(tool.ui.al_method_radio.get_value(), 'v')
        self.assertFalse(tool.ui.al_method_radio.isEnabled())
        self.assertEqual(tool.al_voronoi_geo_storage, {})
        self.assertEqual(tool._al_generation, generation + 1)
        tool.build_al_table.assert_called_once()
        tool.ui.al_mode_radio.set_value('grid')
        self.assertEqual(tool.ui.al_method_radio.get_value(), 'v')

    def test_initialization_preserves_valid_map_and_emits_no_radio_signals(self):
        target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method='b')
        storage = {1: {'point': Point(0, 0), 'geo': None, 'height': 0.2}}
        tool = self.open_tool(target, storage=storage)
        self.assertEqual(tool.al_voronoi_geo_storage, storage)
        self.assertTrue(tool.al_heights_valid)
        self.assertEqual(tool._al_generation, 0)
        tool.radio_events.assert_not_called()
        self.assertFalse(tool.ui.al_mode_radio.signalsBlocked())
        self.assertFalse(tool.ui.al_method_radio.signalsBlocked())
        events = MagicMock()
        tool.ui.al_mode_radio.activated_custom.connect(events)
        tool.ui.al_method_radio.activated_custom.connect(events)
        tool.ui.al_method_radio.blockSignals(True)
        tool._new_probing_shapes = MagicMock()
        tool.ui.object_combo.currentText.return_value = target.obj_options['name']
        tool.on_object_changed()
        events.assert_not_called()
        self.assertFalse(tool.ui.al_mode_radio.signalsBlocked())
        self.assertTrue(tool.ui.al_method_radio.signalsBlocked())
        self.assertTrue(tool.al_heights_valid)

    def test_legacy_and_manual_restoration_do_not_write_options(self):
        for options, mode, method in (({}, 'grid', 'v'),
                                      ({'tools_al_mode': 'manual', 'tools_al_method': 'b'}, 'manual', 'v')):
            target = TestTargetOwnership.target(**options)
            before = dict(target.obj_options)
            tool = self.open_tool(target, global_method='b')
            self.assertEqual(tool.ui.al_mode_radio.get_value(), mode)
            self.assertEqual(tool.ui.al_method_radio.get_value(), method)
            self.assertEqual(target.obj_options, before)

    def test_unsupported_and_invalid_changes_do_not_write(self):
        target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method='v')
        target.is_segmented_gcode = False
        before = dict(target.obj_options)
        tool = self.open_tool(target)
        tool.ui.al_frame.setDisabled.assert_called_with(True)
        tool.ui.al_mode_radio.set_value('grid')
        tool.ui.al_method_radio.set_value('b')
        tool.on_mode_radio('invalid')
        tool.on_method_radio('invalid')
        self.assertEqual(target.obj_options, before)

    def test_invalid_values_and_non_cncjob_do_not_persist(self):
        target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method='v')
        tool = self.open_tool(target)
        before = dict(target.obj_options)
        tool.on_mode_radio('invalid')
        tool.on_method_radio('invalid')
        self.assertEqual(target.obj_options, before)
        target.kind = 'geometry'
        tool.ui.al_method_radio.set_value('b')
        tool.ui.al_mode_radio.set_value('manual')
        self.assertEqual(target.obj_options, before)


    def test_same_name_replacement_clears_owner_even_without_points(self):
        target = TestTargetOwnership.target(tools_al_mode='grid', tools_al_method='b')
        replacement = TestTargetOwnership.target(tools_al_mode='manual', tools_al_method='v')
        tool = run_set_tool_ui({}, False, loaded_obj=replacement,
                               owner=(target.obj_options['name'], target), real_radios=True)
        self.assertIsNone(tool._al_target_obj)
        self.assertIsNone(tool._al_target_name)
        self.assertEqual(tool._al_generation, 1)
        self.assertEqual(tool.ui.al_mode_radio.get_value(), 'manual')


class TestWordRegex(unittest.TestCase):
    def test_trailing_dot_number_is_replaced_whole(self):
        state = li.new_levelling_state()
        out = li.level_gcode_line('G1 X1 Y1 Z-1.', state, lambda x, y: 0.5)
        self.assertEqual(out, 'G1 X1 Y1 Z-0.5000')

    def test_leading_dot_number_still_parses(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G1 X.5 Y.25 Z-.1', state, lambda x, y: 0.0)
        self.assertAlmostEqual(state['X'], 0.5)
        self.assertAlmostEqual(state['Y'], 0.25)
        self.assertAlmostEqual(state['Z'], -0.1)


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

    def test_g90_restores_absolute_levelling(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G91', state, self.off)
        li.level_gcode_line('G90', state, self.off)
        self.assertEqual(li.level_gcode_line('G1 X1 Y1 Z-1', state, self.off), 'G1 X1 Y1 Z-0.5000')

    def test_g91_with_g28_sets_relative_mode(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G1 Z-1', state, self.off)
        self.assertEqual(li.level_gcode_line('G91 G28 Z0', state, self.off), 'G91 G28 Z0')
        self.assertFalse(state['abs'])
        self.assertEqual(li.level_gcode_line('G1 X5 Y5', state, self.off), 'G1 X5 Y5')
        self.assertEqual(state['relative'], 1)

    def test_g90_with_g53_then_absolute_motion_is_levelled(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G91', state, self.off)
        self.assertEqual(li.level_gcode_line('G90 G53 G0 Z0', state, self.off), 'G90 G53 G0 Z0')
        self.assertTrue(state['abs'])
        self.assertEqual(li.level_gcode_line('G1 X1 Y1 Z-1', state, self.off), 'G1 X1 Y1 Z-0.5000')

    def test_modal_setting_with_axis_words_under_modal_g1_is_levelled(self):
        state = li.new_levelling_state()
        li.level_gcode_line('G1 X0 Y0 Z-1', state, self.off)
        self.assertEqual(li.level_gcode_line('G90 X1 Y1', state, self.off), 'G90 X1 Y1 Z-0.5000')


    def test_tool_warns_about_relative_moves(self):
        tool = make_audit_tool()
        tool._al_prepare_interp = MagicMock(return_value=True)
        tool._al_offset_fn = self.off
        tool._al_decimals = 4
        tool._al_points = [(0.0, 0.0, 0.5)]
        target = SimpleNamespace(tools={1: {'gcode': 'G1 Z-1\nG91\nG1 X5 Y5\n'}})
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('G91' in t for t in emitted(tool)))


class TestAllG1Compensated(unittest.TestCase):
    def _z(self, line):
        return float(line.split('Z')[-1])


    def test_no_discontinuity_around_zero(self):
        for nominal in ('-0.0001', '0', '0.0001', '0.3', '0.3001'):
            state = li.new_levelling_state()
            out = li.level_gcode_line('G1 X1 Y1 Z' + nominal, state, lambda x, y: 0.3)
            self.assertAlmostEqual(self._z(out), float(nominal) + 0.3, places=4)

    def test_clearance_feed_keeps_its_clearance(self):
        state = li.new_levelling_state()
        # no default start position: position X/Y/Z first
        li.level_gcode_line('G0 X0 Y0 Z5', state, lambda x, y: -0.2)
        self.assertEqual(li.level_gcode_line('G1 Z5', state, lambda x, y: -0.2), 'G1 Z4.8000')



class TestGrblHelpers(unittest.TestCase):
    def test_reply_error(self):
        self.assertEqual(gp.grbl_reply_error('ok\nerror:9'), 'error:9')
        self.assertEqual(gp.grbl_reply_error('ALARM:5\n[PRB:0,0,0:0]'), 'ALARM:5')
        self.assertIsNone(gp.grbl_reply_error('[PRB:0,0,0:1]\nok'))
        self.assertIsNone(gp.grbl_reply_error(''))
        self.assertIsNone(gp.grbl_reply_error(None))

    def test_probe_timeout(self):
        # 3 mm at 6 mm/min = 30 s; x1.5 = 45 s; +5 s margin = 50 s
        self.assertAlmostEqual(gp.probe_timeout_s(2.0, -1.0, 6.0), 50.0)
        self.assertEqual(gp.probe_timeout_s(2.0, -1.0, 1000.0), 10.0)
        self.assertEqual(gp.probe_timeout_s(2.0, -1.0, 0.0), 10.0)
        self.assertEqual(gp.probe_timeout_s('x', -1.0, 50.0), 10.0)
        self.assertEqual(gp.probe_timeout_s(float('nan'), -1.0, 50.0), 10.0)

    def test_report_scale(self):
        self.assertEqual(gp.grbl_report_scale('MM', False), 1.0)
        self.assertEqual(gp.grbl_report_scale('IN', True), 1.0)
        self.assertAlmostEqual(gp.grbl_report_scale('IN', False), 1.0 / 25.4)
        self.assertAlmostEqual(gp.grbl_report_scale('MM', True), 25.4)

    def test_parse_setting(self):
        text = '$10=1\n$13=1\n$130=200.000\nok'
        self.assertEqual(gp.parse_grbl_setting(text, 13), 1.0)
        self.assertEqual(gp.parse_grbl_setting(text, 130), 200.0)
        self.assertIsNone(gp.parse_grbl_setting(text, 22))
        self.assertIsNone(gp.parse_grbl_setting('', 13))

    def test_status_state(self):
        self.assertEqual(gp.parse_grbl_status_state('<Idle|MPos:0.000,0.000,0.000|FS:0,0>'), 'Idle')
        self.assertEqual(gp.parse_grbl_status_state('<Hold:0|MPos:0,0,0>'), 'Hold')
        self.assertIsNone(gp.parse_grbl_status_state('ok'))
        self.assertIsNone(gp.parse_grbl_status_state(None))

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

    def test_reserve_needs_open_port(self):
        tool = make_audit_tool()
        self.assertIsNone(tool._grbl_reserve('x'))
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))

    def test_only_one_operation(self):
        tool = self._tool()
        op = tool._grbl_reserve('first')
        self.assertIsNotNone(op)
        self.assertIsNone(tool._grbl_reserve('second'))
        tool._grbl_release(op)
        self.assertIsNotNone(tool._grbl_reserve('third'))

    def test_recovery_blocks_motion_only(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_reset'
        self.assertIsNone(tool._grbl_reserve('jog', motion=True))
        self.assertIsNotNone(tool._grbl_reserve('param', motion=False))

    def test_recovery_operation_only_when_needs_home(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_reset'
        self.assertIsNone(tool._grbl_reserve('home', motion=True, allow_in_recovery=True))
        tool._grbl_recovery = 'needs_home'
        self.assertIsNotNone(tool._grbl_reserve('home', motion=True, allow_in_recovery=True))

    def test_transact_reads_until_ok(self):
        tool = self._tool()
        op = tool._grbl_reserve('t')
        text, status = tool._grbl_transact(op, '$$', timeout=1.0)
        self.assertEqual(status, 'ok')
        self.assertIn('$13=0', text)

    def test_transact_drops_stale_input(self):
        fake = FakeGrbl()
        fake.rx.append(b'[PRB:9.000,9.000,9.000:1]\r\n')
        tool = self._tool(fake)
        op = tool._grbl_reserve('t')
        text, status = tool._grbl_transact(op, 'G0 Z2', timeout=1.0)
        self.assertEqual(status, 'ok')
        self.assertNotIn('PRB', text)

    def test_transact_error_and_alarm(self):
        fake = FakeGrbl(replies={'G0 Z2': ['error:9'], 'G38.2': ['ALARM:5']})
        tool = self._tool(fake)
        op = tool._grbl_reserve('t')
        self.assertEqual(tool._grbl_transact(op, 'G0 Z2', timeout=1.0)[1], 'error')
        self.assertEqual(tool._grbl_transact(op, 'G38.2 Z-1 F50', timeout=1.0)[1], 'error')

    def test_transact_joins_partial_lines(self):
        port = make_port([b'[PR', b'B:1.0,2.0,-0.5:1]\r\n', b'o', b'k\r\n'])
        tool = self._tool(port)
        op = tool._grbl_reserve('t')
        text, status = tool._grbl_transact(op, 'G38.2 Z-1 F50', timeout=1.0)
        self.assertEqual(status, 'ok')
        self.assertIn('[PRB:1.0,2.0,-0.5:1]', text)

    def test_transact_timeout(self):
        tool = self._tool(make_port([]))
        op = tool._grbl_reserve('t')
        self.assertEqual(tool._grbl_transact(op, 'G38.2 Z-1 F50', timeout=0.05)[1], 'timeout')

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

    def test_query_status(self):
        tool = self._tool(FakeGrbl(state='Alarm'))
        op = tool._grbl_reserve('t')
        line = tool._grbl_query_status(op)
        self.assertEqual(gp.parse_grbl_status_state(line), 'Alarm')


    def test_start_op_releases_after_port_exception(self):
        tool = self._tool()

        def boom(op):
            raise serial.SerialException('gone')
        tool._grbl_start_op('x', boom)
        run_worker(tool)
        self.assertIsNone(tool._grbl_op)
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))



class TestGrblConnect(unittest.TestCase):
    def _tool(self):
        tool = make_audit_tool()
        tool.ui.com_list_combo.currentText.return_value = 'COM3'
        tool.ui.baudrates_list_combo.currentText.return_value = '115200'
        tool.ui.al_toolbar.count.return_value = 0
        return tool

    def test_bad_baudrate_opens_nothing(self):
        tool = self._tool()
        tool.ui.baudrates_list_combo.currentText.return_value = ''
        with patch('appPlugins.Common.grbl.grbl_client.serial.serial_for_url') as opener:
            tool.on_grbl_connect()
        opener.assert_not_called()
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))


    def test_open_failure_leaves_no_port(self):
        tool = self._tool()
        with patch('appPlugins.Common.grbl.grbl_client.serial.serial_for_url',
                   side_effect=serial.SerialException('busy')):
            tool.on_grbl_connect()
        self.assertIsNone(tool.grbl_ser_port)
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))

    def test_finish_without_answer_closes_port(self):
        tool = self._tool()
        port = make_port()
        port.in_waiting = len(b'garbage\r\n')
        port.read.return_value = b'garbage\r\n'
        tool.grbl_ser_port = port
        tool._grbl_connect_attempt = object()
        tool._grbl_connect_finish('COM3', port, tool._grbl_connect_attempt)
        port.close.assert_called_once()
        self.assertIsNone(tool.grbl_ser_port)
        tool.grbl_port_closed_sig.emit.assert_called_once()

    def test_finish_with_ok_or_banner_connects(self):
        for answer in (b'ok\r\n', b"Grbl 1.1h ['$' for help]\r\n"):
            tool = self._tool()
            port = make_port()
            port.in_waiting = len(answer)
            port.read.return_value = answer
            tool.grbl_ser_port = port
            tool._grbl_connect_attempt = object()
            tool._grbl_connect_finish('COM3', port, tool._grbl_connect_attempt)
            port.close.assert_not_called()
            tool.ui.controller_reset_button.setDisabled.assert_called_with(False)

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

    def test_click_while_connected_and_idle_disconnects(self):
        tool = self._tool()
        port = make_port()
        tool.grbl_ser_port = port
        tool._grbl_connected = True
        tool.on_grbl_connect()
        port.close.assert_called_once()
        self.assertIsNone(tool.grbl_ser_port)

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

    def test_force_close_closes_during_operation(self):
        tool = self._tool()
        fake = FakeGrbl()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        op = tool._grbl_reserve('probe', motion=True)
        tool._grbl_force_close()
        self.assertTrue(op.cancel.is_set())
        self.assertFalse(fake.is_open)
        self.assertIsNone(tool.grbl_ser_port)
        self.assertIn('!', fake.commands())


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

    def test_pause_blocked_during_operation(self):
        tool = self._tool()
        tool._grbl_reserve('probe', motion=True)
        tool.on_grbl_pause_resume(True)
        self.assertEqual(tool.grbl_ser_port.commands(), [])
        self.assertTrue(any('[WARNING_NOTCL]' in t for t in emitted(tool)))

    def test_resume_blocked_during_recovery(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_reset'
        tool.on_grbl_pause_resume(False)
        self.assertEqual(tool.grbl_ser_port.commands(), [])

    def test_pause_and_resume_when_idle(self):
        tool = self._tool()
        tool.on_grbl_pause_resume(True)
        tool.on_grbl_pause_resume(False)
        self.assertEqual(tool.grbl_ser_port.commands(), ['!', '~'])

    def test_reset_during_operation_needs_home(self):
        tool = self._tool()
        op = tool._grbl_reserve('probe', motion=True)
        tool.on_grbl_reset()
        self.assertTrue(op.cancel.is_set())
        self.assertEqual(tool.grbl_ser_port.commands(), ['\x18'])
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_reset_pending)
        self.assertIsNone(tool._grbl_reserve('jog', motion=True))


    def test_reset_when_idle_keeps_no_recovery(self):
        tool = self._tool()
        tool.on_grbl_reset()
        # Even an apparently Idle reset must prove its startup before motion.
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_reset_pending)
        self.assertIsNone(tool._grbl_reserve('jog', motion=True))

    def test_no_port_reports_error(self):
        tool = make_audit_tool()
        tool.on_grbl_stop()
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))


class TestGrblCommands(unittest.TestCase):
    def _tool(self, port=None):
        tool = make_audit_tool()
        tool.grbl_ser_port = port if port is not None else FakeGrbl()
        tool._grbl_connected = True
        tool.ui.jog_step_entry.get_value.return_value = 1.0
        tool.ui.jog_fr_entry.get_value.return_value = 100.0
        tool.app.options = {'tools_grbl_travelz': 2.0}
        return tool

    def test_jog_step_is_a_number(self):
        tool = self._tool()
        tool.on_grbl_jog(direction='xplus')
        run_worker(tool)
        self.assertEqual(tool.grbl_ser_port.commands(), ['$J=G91 G21 X1.0 F100.0', '?'])

    def test_jog_origin_sends_2_commands(self):
        tool = self._tool()
        tool.on_grbl_jog(direction='origin')
        run_worker(tool)
        self.assertEqual(tool.grbl_ser_port.commands(),
                         ['$J=G90 G21 Z2.0 F100.0', '?', '$J=G90 G21 X0.0 Y0.0 F100.0', '?'])

    def test_jog_blocked_in_recovery(self):
        tool = self._tool()
        tool._grbl_recovery = 'needs_home'
        tool.on_grbl_jog(direction='xplus')
        tool.app.worker_task.emit.assert_not_called()

    def test_typed_command_runs_in_worker(self):
        tool = self._tool()
        tool.ui.grbl_command_entry.get_value.return_value = 'G0 X1'
        tool.on_grbl_send_command()
        self.assertEqual(tool.grbl_ser_port.commands(), [])
        run_worker(tool)
        self.assertEqual(tool.grbl_ser_port.commands(), ['G0 X1', '?'])

    def test_report_reads_status(self):
        tool = self._tool()
        tool.on_grbl_report()
        run_worker(tool)
        self.assertEqual(tool.grbl_ser_port.commands(), ['?'])

    def test_parameter_tolerates_bad_bytes(self):
        port = make_port([b'\xff\xfe junk\r\n', b'$10=1\r\n', b'ok\r\n'])
        tool = self._tool(port)
        tool.on_grbl_get_parameter('$10')
        run_worker(tool)
        shell_texts = [c.args[0] for c in tool.console_line_sig.emit.call_args_list]
        self.assertTrue(any('$10 = 1.0' in t for t in shell_texts))

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

    def test_no_port_blocks_every_command(self):
        tool = make_audit_tool()
        tool.ui.jog_step_entry.get_value.return_value = 1.0
        tool.ui.jog_fr_entry.get_value.return_value = 100.0
        tool.ui.grbl_command_entry.get_value.return_value = '?'
        tool.on_grbl_jog(direction='xplus')
        tool.on_grbl_send_command()
        tool.on_grbl_report()
        tool.on_grbl_get_parameter('$10')
        tool.app.worker_task.emit.assert_not_called()


class TestGrblZero(unittest.TestCase):
    def test_zero_x_in_g55(self):
        fake = FakeGrbl(replies={'$G': ['[GC:G0 G55 G17 G21 G90 G94 M5 M9 T0 F0 S0]', 'ok']})
        tool = make_audit_tool()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.on_grbl_zero(axis='x')
        run_worker(tool)
        self.assertEqual(fake.commands(), ['$G', 'G10 L20 P2 X0', '?'])

    def test_zero_all_in_g54(self):
        fake = FakeGrbl()
        tool = make_audit_tool()
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool.on_grbl_zero(axis='all')
        run_worker(tool)
        self.assertEqual(fake.commands(), ['$G', 'G10 L20 P1 X0 Y0 Z0', '?'])

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

    def test_port_list_does_not_open_ports(self):
        infos = [SimpleNamespace(device='COM3'), SimpleNamespace(device='COM7')]
        with patch('appPlugins.Common.grbl.grbl_client.serial.tools.list_ports.comports', return_value=infos), \
                patch('appPlugins.Common.grbl.grbl_client.serial.Serial') as serial_cls:
            self.assertEqual(ToolGCodeSender.on_grbl_list_serial_ports(), ['COM3', 'COM7'])
        serial_cls.assert_not_called()



class TestShowSignalSlot(unittest.TestCase):
    def test_slot_skips_show_when_checkbox_off(self):
        tool = make_audit_tool()
        tool.show_probing_geo = MagicMock()
        tool.ui.plot_probing_pts_cb.get_value.return_value = False
        tool._on_show_probing_geo_sig(True, True)
        tool.show_probing_geo.assert_not_called()

    def test_slot_shows_when_checkbox_on(self):
        tool = make_audit_tool()
        tool.show_probing_geo = MagicMock()
        tool.ui.plot_probing_pts_cb.get_value.return_value = True
        tool._on_show_probing_geo_sig(True, True)
        tool.show_probing_geo.assert_called_once_with(state=True, reset=True)

    def test_slot_always_clears(self):
        tool = make_audit_tool()
        tool.show_probing_geo = MagicMock()
        tool._on_show_probing_geo_sig(False, False)
        tool.show_probing_geo.assert_called_once_with(state=False, reset=False)



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

    def test_failed_result_invalidates(self):
        tool, target = self._tool()
        tool.al_heights_valid = True
        tool._on_al_result_ready({'kind': 'failed', 'context': tool._al_capture_context()})
        self.assertFalse(tool.al_heights_valid)

    def test_import_points_create_storage_and_bump_generation(self):
        tool, target = self._tool()
        tool.al_voronoi_geo_storage.clear()
        ctx = tool._al_capture_context()
        tool._on_al_result_ready({'kind': 'import_points', 'context': ctx,
                                  'points': [(0.0, 0.0, 0.1), (5.0, 0.0, 0.2)]})
        self.assertEqual(len(tool.al_voronoi_geo_storage), 2)
        self.assertTrue(tool.al_heights_valid)
        self.assertEqual(tool._al_generation, 1)
        tool.apply_autolevel.assert_called_once_with(target_obj=target)

    def test_apply_refuses_a_removed_target(self):
        tool, target = self._tool()
        tool.apply_autolevel = ToolLevelling.apply_autolevel.__get__(tool)
        tool.app.collection.get_by_name.return_value = None
        tool.apply_autolevel(target_obj=target)
        tool.app.worker_task.emit.assert_not_called()
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))


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

    def test_startup_order(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertEqual(fake.commands()[:7], ['?', '$$', 'G21', 'G90', 'G94', '$G', '$#'])

    def test_g4_wait_before_each_probe(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        self._run(tool)
        cmds = fake.commands()
        for i, c in enumerate(cmds):
            if c.startswith('G38.2'):
                self.assertEqual(cmds[i - 1], 'G4 P0')

    def test_inch_project_sends_g20_and_scales(self):
        fake = FakeGrbl(heights={(0.0, 0.0): -2.54, (10.0, 0.0): 2.54})
        tool, target = self._tool(fake, units='IN')
        self._run(tool)
        self.assertEqual(fake.commands()[2], 'G20')
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.1, places=4)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[2]['height'], 0.1, places=4)

    def test_inch_reports_are_scaled_for_mm_project(self):
        fake = FakeGrbl(heights={(0.0, 0.0): -0.254, (10.0, 0.0): 0.254}, report_inches=True)
        tool, target = self._tool(fake)
        self._run(tool)
        self.assertAlmostEqual(tool.al_voronoi_geo_storage[1]['height'], -0.254, places=3)

    def test_new_pass_invalidates_old_map_at_start(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake, heights_valid=True, old_height=0.5)
        tool.on_grbl_autolevel()
        self.assertFalse(tool.al_heights_valid)

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

    def test_no_points_or_no_port_schedule_nothing(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        tool.al_voronoi_geo_storage.clear()
        tool.on_grbl_autolevel()
        tool2, target2 = self._tool(FakeGrbl())
        tool2.grbl_ser_port = None
        tool2.on_grbl_autolevel()
        tool.app.worker_task.emit.assert_not_called()
        tool2.app.worker_task.emit.assert_not_called()

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

    def test_missing_probe_record_stops(self):
        fake = FakeGrbl(replies={'G38.2': ['ok']})
        tool, target = self._tool(fake)
        self._run(tool)
        self._assert_stopped_at_probe(tool, fake)
        self.assertIn('!', fake.commands())


    def test_failed_probe_flag_stops(self):
        fake = FakeGrbl(replies={'G38.2': ['[PRB:0.000,0.000,-1.000:0]', 'ok']})
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

    def test_failed_reprobe_leaves_no_valid_mixed_map(self):
        fake = FakeGrbl(fail_probe_at=2, heights={(0.0, 0.0): -0.3})
        tool, target = self._tool(fake, heights_valid=True, old_height=0.5)
        self._run(tool)
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], 0.5)
        self.assertEqual(tool.al_voronoi_geo_storage[2]['height'], 0.5)
        self.assertFalse(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_not_called()

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

    def test_stale_result_after_target_replaced(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        tool.on_grbl_autolevel()
        tool.app.collection.get_by_name.return_value = SimpleNamespace(obj_options={'name': 'job'})
        run_worker(tool)
        self.assertFalse(tool.al_heights_valid)
        tool._al_on_height_map_ready.assert_not_called()

    def test_worker_reads_no_widgets(self):
        fake = FakeGrbl()
        tool, target = self._tool(fake)
        tool.on_grbl_autolevel()
        tool.ui = None
        run_worker(tool)
        self.assertTrue(tool.al_heights_valid)


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


    def test_escape_finishes_like_right_click(self):
        tool = self._tool('b')
        tool.on_key_press(SimpleNamespace(key='Escape'))
        self._check_released(tool)
        self.assertEqual(tool.probing_gcode_text, 'GCODE')

    def test_finish_with_no_points_clears_gcode(self):
        tool = self._tool('v')
        tool.al_voronoi_geo_storage.clear()
        tool.probing_gcode_text = 'OLD GCODE'
        tool._finish_manual_points()
        self.assertEqual(tool.probing_gcode_text, '')
        tool.probing_gcode.assert_not_called()


    def test_left_click_add_bumps_generation(self):
        tool = self._tool('v')
        tool.solid_geo = box(0.0, 0.0, 10.0, 10.0)
        tool.ui.avoid_exc_holes_cb.get_value.return_value = False
        tool.ui.probe_tip_dia_entry.get_value.return_value = 0.5
        tool.app.plotcanvas.translate_coords.return_value = (2.0, 2.0)
        tool.app.geo_editor.snap.return_value = (2.0, 2.0)
        tool.on_mouse_click_release(SimpleNamespace(pos=(2.0, 2.0), button=1))
        self.assertEqual(len(tool.al_voronoi_geo_storage), 3)
        self.assertEqual(tool._al_generation, 1)


class TestManualStart(unittest.TestCase):
    def _tool(self):
        tool = make_audit_tool()
        tool.solid_geo = box(5.0, 10.0, 20.0, 30.0)
        tool.app.options = {'global_selection_shape': 'USER'}
        tool.plot_probing_geo = MagicMock()
        tool.show_probing_geo = MagicMock()
        return tool

    def test_no_phantom_point(self):
        tool = self._tool()
        tool.on_add_manual_points()
        self.assertEqual(tool.al_voronoi_geo_storage, {})
        self.assertTrue(tool.mouse_events_connected)

    def test_selection_state_is_captured_at_start(self):
        tool = self._tool()
        tool.on_add_manual_points()
        self.assertEqual(tool.old_selection_state, 'USER')
        self.assertFalse(tool.app.options['global_selection_shape'])



class TestPlaceholderHeights(unittest.TestCase):
    @staticmethod
    def storage():
        return {
            1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.0},
            2: {'point': Point(1.0, 0.0), 'geo': None, 'height': 0.0},
        }

    def test_unprobed_points_stay_invalid(self):
        tool = run_set_tool_ui(self.storage(), heights_valid=False)
        self.assertFalse(tool.al_heights_valid)
        tool.build_al_table.assert_not_called()



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


class TestProbingShapes(unittest.TestCase):

    def test_kept_map_is_replotted_when_plotting_is_on(self):
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.2}}
        tool = run_set_tool_ui(storage, heights_valid=True, plot_points=True)
        tool.show_probing_geo.assert_called_once_with(state=True, reset=True)

    def test_kept_map_is_not_plotted_when_plotting_is_off(self):
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.2}}
        tool = run_set_tool_ui(storage, heights_valid=True, plot_points=False)
        tool.show_probing_geo.assert_not_called()


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


    def test_same_object_keeps_and_replots(self):
        a = self.target('job_a')
        tool = self._tool('job_a', a, 'job_a', a)
        tool.ui.plot_probing_pts_cb.get_value.return_value = True
        tool.on_object_changed()
        self.assertEqual(len(tool.al_voronoi_geo_storage), 1)
        self.assertTrue(tool.al_heights_valid)
        tool.show_probing_geo.assert_called_once_with(state=True, reset=True)


    def test_reopen_on_another_object_clears(self):
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.3}}
        with patch.object(ToolLevelling, '_update_mode_radio_ui'), \
                patch.object(ToolLevelling, 'on_method_radio'), \
                patch.object(ToolLevelling, 'on_controller_change'):
            tool = run_set_tool_ui(storage, heights_valid=True, loaded_obj=self.target('job_b'),
                                   owner=('job_a', self.target('job_a')))
        self.assertEqual(tool.al_voronoi_geo_storage, {})
        self.assertFalse(tool.al_heights_valid)

    def test_reopen_on_the_same_object_keeps(self):
        a = self.target('job_a')
        storage = {1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.3}}
        with patch.object(ToolLevelling, '_update_mode_radio_ui'), \
                patch.object(ToolLevelling, 'on_method_radio'), \
                patch.object(ToolLevelling, 'on_controller_change'):
            tool = run_set_tool_ui(storage, heights_valid=True, loaded_obj=a, owner=('job_a', a))
        self.assertTrue(tool.al_heights_valid)



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

    def test_empty_cut_geometry_fails_cleanly(self):
        tool, target = self._add_tool([])
        self.assertEqual(tool.on_add_al_probepoints(), 'fail')
        tool.on_add_grid_points.assert_not_called()
        self.assertTrue(any('[ERROR_NOTCL]' in t for t in emitted(tool)))

    def test_bad_parsed_entry_fails_cleanly(self):
        tool, target = self._add_tool([{'kind': ['C']}])
        self.assertEqual(tool.on_add_al_probepoints(), 'fail')

    def test_add_resets_old_state_and_sets_owner(self):
        tool, target = self._add_tool([{'geom': box(0, 0, 10, 10), 'kind': ['C']}])
        tool.on_add_al_probepoints()
        tool.on_add_grid_points.assert_called_once()
        self.assertEqual(tool.al_bilinear_geo_storage, [])
        self.assertEqual(tool.probing_gcode_text, '')
        self.assertEqual(tool._al_target_name, 'job_a')
        self.assertIs(tool._al_target_obj, target)
        self.assertEqual(tool._al_generation, 1)


class TestImportStaging(unittest.TestCase):
    def _file(self, text):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(text)
            name = f.name
        self.addCleanup(lambda: os.unlink(name))
        return name

    def _tool(self, heights_valid=True):
        storage = {
            1: {'point': Point(0.0, 0.0), 'geo': None, 'height': 0.5},
            2: {'point': Point(10.0, 0.0), 'geo': None, 'height': 0.5},
        }
        tool = make_audit_tool(storage=storage, heights_valid=heights_valid)
        return tool


    def test_stale_import_is_discarded(self):
        tool = self._tool(heights_valid=False)
        ctx = tool._al_capture_context()
        points = {1: (0.0, 0.0), 2: (10.0, 0.0)}
        tool._al_bump_generation()
        tool.import_height_map(self._file('0.0,0.0,0.1\n10.0,0.0,0.2\n'), ctx, points)
        self.assertEqual(tool.al_voronoi_geo_storage[2]['height'], 0.5)
        self.assertFalse(tool.al_heights_valid)



class TestSupportedTarget(unittest.TestCase):
    def test_missing_attributes_return_false(self):
        self.assertFalse(ToolLevelling._is_supported_al_target(None))
        self.assertFalse(ToolLevelling._is_supported_al_target(SimpleNamespace(obj_options={'type': 'Geometry'})))
        self.assertFalse(ToolLevelling._is_supported_al_target(SimpleNamespace(is_segmented_gcode=True, obj_options={})))
        self.assertFalse(ToolLevelling._is_supported_al_target(SimpleNamespace(is_segmented_gcode=True)))

    def test_supported_types(self):
        for kind in ('Geometry', 'Excellon'):
            self.assertTrue(ToolLevelling._is_supported_al_target(
                SimpleNamespace(is_segmented_gcode=True, obj_options={'type': kind})))


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


if __name__ == '__main__':
    unittest.main()
