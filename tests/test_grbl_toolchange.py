#!/usr/bin/env python
"""
Behavior tests of the calibrated manual tool change (GRBL fixed or removable
touchplate). Run:

    .venv/Scripts/python.exe -m pytest tests/test_grbl_toolchange.py -q
"""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from defaults import AppDefaults
from appPlugins.ToolGcodeSender.ToolChange import (
    PLATE_NONE, PLATE_FIXED, PLATE_REMOVABLE, ToolChangeSettings, ToolChangeMeasurement,
    probe_target, check_contact, STATE_OFF, STATE_REFERENCE, STATE_MEASURED,
    STATE_NEEDS_REMOVAL, STATE_READY)
from tests.test_levelling_audit import FakeGrbl, make_audit_tool


def options(mode=PLATE_FIXED, **changes):
    data = dict(AppDefaults.factory_defaults)
    data.update({
        'tools_grbl_toolchange_plate': mode,
        'tools_grbl_toolchange_probe_distance': 50.0,
        'tools_grbl_toolchange_clearance': 2.0,
        'tools_grbl_toolchange_probe_feed': 50.0,
        'tools_grbl_toolchange_xy_tolerance': 1.0,
        'tools_grbl_toolchange_thickness': 1.5,
    })
    data.update(changes)
    return data


def make_settings(mode=PLATE_FIXED, **changes):
    settings = ToolChangeSettings.from_options(options(mode, **changes))
    assert settings is not None
    return settings


class TestToolChangeMath(unittest.TestCase):

    def test_invalid_imported_settings_are_rejected(self):
        for changes in ({'tools_grbl_toolchange_probe_distance': 0.0},
                        {'tools_grbl_toolchange_clearance': -1.0},
                        {'tools_grbl_toolchange_probe_feed': float('nan')},
                        {'tools_grbl_toolchange_probe_feed': float('inf')},
                        {'tools_grbl_toolchange_probe_distance': 'nonsense'}):
            data = options(**changes)
            self.assertIsNone(ToolChangeSettings.from_options(data), changes)
        self.assertIsNone(ToolChangeSettings.from_options(
            options(PLATE_FIXED, tools_grbl_toolchange_xy_tolerance=0.0)))
        self.assertIsNone(ToolChangeSettings.from_options(
            options(PLATE_REMOVABLE, tools_grbl_toolchange_thickness=0.0)))
        data = options('nonsense')
        self.assertIsNone(ToolChangeSettings.from_options(data))
        data = options(PLATE_REMOVABLE)
        mm = ToolChangeSettings.from_options(data, units='MM')
        inch = ToolChangeSettings.from_options(data, units='IN')
        for name in ('probe_distance', 'clearance', 'probe_feed', 'xy_tolerance', 'plate_thickness'):
            self.assertAlmostEqual(getattr(mm, name), getattr(inch, name) * 25.4)
        data['tools_grbl_toolchange_units'] = 'invalid'
        self.assertIsNone(ToolChangeSettings.from_options(data))

    def test_a_measurement_on_another_plate_or_frame_is_refused(self):
        settings = make_settings()
        measurement = ToolChangeMeasurement()
        measurement.configure(PLATE_FIXED, settings)
        measurement.start_session(1)
        measurement.record_reference(-40.0, (10.0, 20.0), -50.0, 'G54', 'MM', 1, settings)
        measurement.record_measurement(-38.0, (14.0, 20.0), -50.0, 'G54', 'MM', 1)
        self.assertEqual(measurement.fixed_delta(settings, 1, 'MM')[1:2], ('plate_moved',))
        measurement.record_measurement(-38.0, (10.0, 20.0), -49.0, 'G54', 'MM', 1)
        self.assertEqual(measurement.fixed_delta(settings, 1, 'MM')[1], 'frame_changed')
        measurement.record_measurement(-38.0, (10.0, 20.0), -50.0, 'G55', 'MM', 1)
        self.assertEqual(measurement.fixed_delta(settings, 1, 'MM')[1], 'frame_changed')

    def test_bounded_contact_checks(self):
        self.assertEqual(check_contact(10.0, 0.0, 5.0, (1.0, 2.0), (1.0, 2.0)), (True, 'ok'))
        self.assertEqual(check_contact(10.0, 0.0, 10.0, (1.0, 2.0), (1.0, 2.0)),
                         (False, 'already_triggered'))
        self.assertEqual(check_contact(10.0, 0.0, -1.0, (1.0, 2.0), (1.0, 2.0)),
                         (False, 'probe_invalid'))
        self.assertEqual(check_contact(10.0, 0.0, 5.0, (9.0, 2.0), (1.0, 2.0)),
                         (False, 'invalid_position'))
        self.assertEqual(check_contact(10.0, 0.0, float('nan'), (1.0, 2.0), (1.0, 2.0)),
                         (False, 'invalid_position'))
        self.assertAlmostEqual(probe_target(10.0, make_settings()), -40.0)


class ToolChangeFake(FakeGrbl):
    """FakeGrbl with the G10 L2 work-offset command and a configurable position."""

    def __init__(self, wcs_z=0.0, start_z=0.0, wcs_x=0.0, wcs_y=0.0, **kwargs):
        super().__init__(**kwargs)
        self.wcs_z = float(wcs_z)
        self.start_z = float(start_z)
        self.wcs_x = float(wcs_x)
        self.wcs_y = float(wcs_y)
        self.wcs = 'G54'
        self.pins = ''
        self.modal_extra = ''
        self.command_hook = None
        self.worker_threads = set()

    def _answer(self, cmd):
        if cmd not in ('!', '~', '\x18'):
            self.worker_threads.add(threading.get_ident())
        if self.command_hook is not None:
            self.command_hook(cmd)
        for prefix, lines in self.replies.items():
            if cmd.startswith(prefix):
                self._send(*lines)
                return
        scale = 25.4 if self.report_inches else 1.0
        command_scale = 25.4 if self.units_word == 'G20' else 1.0
        if cmd == '$G':
            self._send('[GC:G0 %s G17 %s G90 G94 M5 %s]' %
                       (self.wcs, self.units_word, self.modal_extra), 'ok')
            return
        if cmd.startswith('G10'):
            if 'P%d' % (int(self.wcs[1:]) - 53) not in cmd.split():
                self._send('error:20')
                return
            for word in cmd.split():
                if word.startswith('Z'):
                    self.wcs_z = float(word[1:]) * (25.4 if self.units_word == 'G20' else 1.0)
            self._send('ok')
            return
        if cmd == '$#':
            self._send('[%s:%.4f,%.4f,%.4f]' % ((self.wcs,) + tuple(v / scale for v in
                       (self.wcs_x, self.wcs_y, self.wcs_z))),
                       '[G92:0.000,0.000,0.000]', '[TLO:0.000]', 'ok')
            return
        if cmd == '?':
            self._send('<%s|MPos:%.4f,%.4f,%.4f|WCO:%.4f,%.4f,%.4f|FS:0,0|Pn:%s>' %
                       (self.state, self.x / scale, self.y / scale, self.start_z / scale,
                        self.wcs_x / scale, self.wcs_y / scale, self.wcs_z / scale, self.pins))
            return
        if cmd.startswith('G0 Z'):
            self.start_z = float(cmd.split('Z')[1]) * command_scale + self.wcs_z
            self._send('ok')
            return
        if cmd.startswith('G38.2'):
            self.probe_count += 1
            target = float(next(w[1:] for w in cmd.split() if w.startswith('Z'))) * command_scale + self.wcs_z
            hit = self.heights.get((round(self.x, 3), round(self.y, 3)), 0.0)
            success = target <= hit < self.start_z
            self.start_z = hit if success else target
            self._send('[PRB:%.4f,%.4f,%.4f:%d]' %
                       (self.x / scale, self.y / scale, self.start_z / scale, success), 'ok')
            return
        super()._answer(cmd)


def make_tool(fake, options_data, units='MM'):
    tool = make_audit_tool(units=units)
    tool.app.app_units = units
    tool.app.options = options_data
    tool.units = units
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool._grbl_report_inches = None
    tool._ecut_settings_capable = lambda port: False
    tool.toolchange = ToolChangeMeasurement()
    tool._toolchange_session_id = 1
    tool._toolchange_request_id = 0
    tool._toolchange_busy = False
    tool._toolchange_action = None
    tool.ui = MagicMock()
    tool.ui.toolchange_widget = MagicMock()
    tool.toolchange_result_sig = MagicMock()
    tool.toolchange_result_sig.emit.side_effect = tool._on_toolchange_result
    tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
    return tool


class TestToolChangeSender(unittest.TestCase):

    def test_apply_requires_idle_before_g10(self):
        for state in ('Run', 'Hold'):
            fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
            tool = make_tool(fake, options(PLATE_REMOVABLE))
            tool.on_toolchange_measure()
            fake.state = state
            tool.on_toolchange_apply()
            self.assertFalse(any(c.startswith('G10') for c in fake.commands()), state)

    def test_changed_frame_after_contact_never_retracts(self):
        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_REMOVABLE))
        def change(cmd):
            if cmd.startswith('G38.2'):
                fake.modal_extra = 'G91'
        fake.command_hook = change
        tool.on_toolchange_measure()
        self.assertFalse(any(c.startswith('G0 Z') for c in fake.commands()))
        self.assertIsNone(tool.toolchange.measurement)
        count = fake.probe_count
        fake.command_hook = None
        fake.modal_extra = ''
        tool.on_toolchange_measure()
        self.assertEqual(fake.probe_count, count)

    def test_probe_pin_spindle_tlo_and_invalid_report_units_refuse_motion(self):
        for fault in ('pin', 'M3', 'M4', 'G43.1', 'report_units', 'missing_spindle', 'tlo_x', 'tlo_y',
                      'tlo_scalar'):
            fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
            if fault.startswith('tlo_'):
                value = {'tlo_x': '1,0,0', 'tlo_y': '0,1,0', 'tlo_scalar': '2.000'}[fault]
                fake.replies['$#'] = ['[G54:0,0,0]', '[G92:0,0,0]', '[TLO:%s]' % value, 'ok']
            elif fault == 'pin':
                fake.pins = 'P'
            elif fault == 'report_units':
                fake.replies['$$'] = ['$13=2', 'ok']
            elif fault == 'missing_spindle':
                fake.replies['$G'] = ['[GC:G0 G54 G17 G21 G90 G94]', 'ok']
            else:
                fake.modal_extra = fault
            tool = make_tool(fake, options(PLATE_REMOVABLE))
            tool.on_toolchange_measure()
            self.assertFalse(any(c.startswith('G38') for c in fake.commands()), fault)

    def test_off_mode_unit_mismatch_ecut_and_height_map_refuse_the_measurement(self):
        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_NONE))
        self.assertEqual(tool.toolchange.state(), STATE_OFF)
        tool.on_toolchange_measure()
        self.assertEqual(fake.commands(), [])

        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_REMOVABLE))
        tool.app.app_units = 'IN'
        tool.on_toolchange_measure()
        self.assertEqual(fake.commands(), [])
        tool.app.app_units = 'MM'
        tool._ecut_settings_capable = lambda port: True
        tool.on_toolchange_measure()
        self.assertEqual(fake.commands(), [])
        tool._ecut_settings_capable = lambda port: False
        tool.al_heights_valid = True
        tool.on_toolchange_measure()
        self.assertEqual(fake.commands(), [])

    def test_retract_never_goes_above_the_start_height(self):
        fake = ToolChangeFake(heights={(0.0, 0.0): -0.1})
        tool = make_tool(fake, options(PLATE_REMOVABLE))
        tool.on_toolchange_measure()
        self.assertLessEqual(fake.start_z, 0.0)
        self.assertIsNotNone(tool.toolchange.measurement)

    def test_unacknowledged_probe_or_retract_is_not_accepted(self):
        for replies, retract_allowed in (({'G38.2': ['[PRB:0,0,-5:1]']}, False),
                                         ({'G0 Z': ['ok']}, True)):
            with self.subTest(replies=list(replies)):
                fake = ToolChangeFake(heights={(0.0, 0.0): -5.0}, replies=replies)
                tool = make_tool(fake, options(PLATE_REMOVABLE))
                with patch('appPlugins.ToolGcodeSender.Sender.probe_timeout_s', return_value=0.02):
                    tool.on_toolchange_measure()
                self.assertIsNone(tool.toolchange.measurement)
                self.assertNotEqual(tool.toolchange.state(), STATE_MEASURED)
                if not retract_allowed:
                    self.assertFalse(any(c.startswith('G0 Z') for c in fake.commands()))

    def test_wrong_fixed_xy_is_refused_before_probe(self):
        fake = ToolChangeFake(wcs_z=-50, heights={(0.0, 0.0): -40})
        tool = make_tool(fake, options())
        tool.on_toolchange_calibrate()
        count = fake.probe_count
        fake.x = 10.0
        fake.start_z = 0.0
        tool.on_toolchange_measure()
        self.assertEqual(fake.probe_count, count)

    def test_apply_readback_failures_retire_measurement_and_cached_coordinates(self):
        for fault in ('malformed', 'moved', 'stale_wco', 'unacknowledged'):
            fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
            tool = make_tool(fake, options(PLATE_REMOVABLE))
            tool.on_toolchange_measure()
            tool._last_wco = (0.0, 0.0, 0.0)
            def fail(cmd):
                if cmd.startswith('G10'):
                    if fault == 'malformed':
                        fake.replies['$#'] = ['[G54:bad]', 'ok']
                    elif fault == 'moved':
                        fake.start_z += 1.0
                    elif fault == 'stale_wco':
                        fake.replies['?'] = ['<Idle|MPos:0,0,-3|WCO:0,0,0>']
                    else:
                        fake.replies['G10'] = ['error:20']
            fake.command_hook = fail
            tool.on_toolchange_apply()
            self.assertIsNone(tool.toolchange.measurement, fault)
            self.assertNotEqual(tool.toolchange.state(), STATE_NEEDS_REMOVAL, fault)
            before = len(fake.commands())
            tool.on_toolchange_apply()
            self.assertEqual(len(fake.commands()), before)

    def test_fixed_calibration_and_correction_apply_verified_g10(self):
        fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
        tool = make_tool(fake, options(PLATE_FIXED,
                                       tools_grbl_toolchange_probe_distance=50.0))
        tool.on_toolchange_calibrate()
        self.assertIsNotNone(tool.toolchange.reference)
        self.assertAlmostEqual(tool.toolchange.reference['offset_z'], -50.0)
        self.assertAlmostEqual(tool.toolchange.reference['contact_machine_z'], -40.0)

        fake.heights = {(0.0, 0.0): -38.0}
        fake.start_z = 0.0  # operator raises and replaces the tool
        tool.on_toolchange_measure()
        self.assertEqual(tool.toolchange.state(), STATE_MEASURED)
        tool.on_toolchange_apply()

        self.assertIn('G10 L2 P1 Z-48.0000', fake.commands())
        self.assertAlmostEqual(fake.wcs_z, -48.0)
        self.assertAlmostEqual(tool.toolchange.reference['offset_z'], -48.0)
        self.assertAlmostEqual(tool.toolchange.reference['contact_machine_z'], -38.0)
        g10 = [command for command in fake.commands() if command.startswith('G10')]
        self.assertEqual(len(g10), 1)
        self.assertNotIn('X', g10[0])
        self.assertNotIn('Y', g10[0])
        for contact, expected in ((-37.0, -47.0), (-41.0, -51.0)):
            fake.start_z = 0.0
            fake.heights = {(0.0, 0.0): contact}
            tool.on_toolchange_measure()
            tool.on_toolchange_apply()
            self.assertAlmostEqual(fake.wcs_z, expected)

    def test_inch_report_units_convert_and_preserve_the_wcs_xy(self):
        # report in inches, work offset -2 in; contact -1 in for the reference tool
        fake = ToolChangeFake(wcs_z=-2.0 * 25.4, start_z=0.0, wcs_x=1.0, wcs_y=2.0,
                              report_inches=True, heights={(0.0, 0.0): -25.4})
        tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=25.4), units='IN')
        tool.on_toolchange_calibrate()
        self.assertAlmostEqual(tool.toolchange.reference['offset_z'], -2.0)
        self.assertAlmostEqual(tool.toolchange.reference['contact_machine_z'], -1.0)

        fake.heights = {(0.0, 0.0): -22.86}  # -0.9 in, the replacement protrudes 0.1 in farther
        fake.start_z = 0.0
        tool.on_toolchange_measure()
        tool.on_toolchange_apply()
        self.assertIn('G10 L2 P1 Z-1.9000', fake.commands())
        self.assertAlmostEqual(fake.wcs_z, -1.9 * 25.4)
        self.assertAlmostEqual(fake.wcs_x, 1.0)
        self.assertAlmostEqual(fake.wcs_y, 2.0)
        g10 = [command for command in fake.commands() if command.startswith('G10')][0]
        self.assertNotIn('X', g10)
        self.assertNotIn('Y', g10)

    def test_nondefault_wcs_is_adjusted_without_selecting_another_frame(self):
        fake = ToolChangeFake(wcs_z=-50, wcs_x=2, wcs_y=3, heights={(0.0, 0.0): -40})
        fake.wcs = 'G55'
        tool = make_tool(fake, options())
        tool.on_toolchange_calibrate()
        fake.start_z = 0.0
        fake.heights = {(0.0, 0.0): -38}
        tool.on_toolchange_measure()
        tool.on_toolchange_apply()
        self.assertIn('G10 L2 P2 Z-48.0000', fake.commands())
        self.assertAlmostEqual(fake.wcs_z, -48)
        self.assertEqual((fake.wcs_x, fake.wcs_y), (2, 3))
        self.assertFalse(any(c in ('G54', 'G55') for c in fake.commands()))

    def test_removable_measurement_sets_the_board_zero_and_needs_removal(self):
        fake = ToolChangeFake(wcs_z=0.0, start_z=0.0, heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_REMOVABLE,
                                       tools_grbl_toolchange_thickness=1.5,
                                       tools_grbl_toolchange_probe_distance=50.0))
        tool.on_toolchange_measure()
        self.assertEqual(tool.toolchange.state(), STATE_MEASURED)
        tool.on_toolchange_apply()
        self.assertIn('G10 L2 P1 Z-6.5000', fake.commands())
        self.assertAlmostEqual(fake.wcs_z, -6.5)
        self.assertEqual(tool.toolchange.state(), STATE_NEEDS_REMOVAL)
        tool.on_toolchange_acknowledge()
        self.assertEqual(tool.toolchange.state(), STATE_READY)


class TestToolChangeWidget(unittest.TestCase):
    """The real Qt buttons, the real signals and the real worker round trip."""

    def setUp(self):
        self.tool = None

    def tearDown(self):
        if self.tool is not None:
            self.tool._grbl_port_ready = lambda: False
            self.tool.on_plugin_cleanup()
            self.tool.app.ui.grbl_console_dock.close()
            self.tool.app.ui.grbl_console_dock.deleteLater()
            self.tool.close()
            self.tool.deleteLater()
            QtWidgets.QApplication.processEvents()

    def make_tool(self, options_data):
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        from tests.test_camera_calibration import make_public_app

        app = make_public_app()
        app.ui.grbl_console_dock = QtWidgets.QDockWidget()
        app.options = options_data
        app.options['tools_grbl_console_visible'] = True
        tool = ToolGCodeSender(app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'), \
                patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                      return_value=[]):
            tool.run()
        self.tool = tool
        return tool

    def wire(self, tool, fake):
        self.fake = fake
        tool.grbl_ser_port = fake
        tool._grbl_connected = True
        tool._grbl_report_inches = False
        tool._toolchange_new_session('test')
        tool._grbl_set_ui_connected(True)
        jobs = []

        tool.app.worker_task.emit.side_effect = lambda task: jobs.append(task['fcn'])
        tool._status_poll_timer.stop()
        return jobs

    def run_job(self, jobs):
        port = self.fake
        errors = []
        def run():
            try:
                jobs.pop(0)()
            except BaseException as err:
                errors.append(err)
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 10.0
        while worker.is_alive() and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.001)
        worker.join(1.0)
        self.assertFalse(worker.is_alive(), 'worker did not finish')
        self.assertEqual(errors, [])
        QtWidgets.QApplication.processEvents()
        self.assertNotIn(threading.get_ident(), port.worker_threads)

    def test_real_buttons_calibrate_measure_and_apply(self):
        fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
        tool = self.make_tool(options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
        jobs = self.wire(tool, fake)
        widget = tool.ui.toolchange_widget

        widget.calibrate_button.click()
        self.assertEqual(len(jobs), 1)
        self.run_job(jobs)
        self.assertIsNotNone(tool.toolchange.reference)

        fake.heights = {(0.0, 0.0): -38.0}
        fake.start_z = 0.0
        widget.measure_button.click()
        self.run_job(jobs)
        self.assertEqual(tool.toolchange.state(), STATE_MEASURED)
        widget.apply_button.click()
        self.run_job(jobs)
        self.assertAlmostEqual(fake.wcs_z, -48.0)
        self.assertEqual(tool.toolchange.state(), STATE_REFERENCE)
        self.assertTrue(widget.apply_button.isEnabled() is False)

    def test_queued_work_is_retired_by_clear_stop_and_settings(self):
        for action in ('clear', 'stop', 'disable', 'settings', 'units', 'home', 'reset', 'ui_reset', 'close'):
            with self.subTest(action=action):
                fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
                tool = self.make_tool(options(PLATE_REMOVABLE))
                jobs = self.wire(tool, fake)
                tool.ui.toolchange_widget.measure_button.click()
                if action == 'clear':
                    tool.on_toolchange_clear()
                elif action == 'stop':
                    tool.ui.toolchange_widget.stop_button.click()
                elif action == 'disable':
                    tool.app.options['tools_grbl_toolchange_plate'] = 'disabled'
                elif action == 'settings':
                    tool.app.options['tools_grbl_toolchange_probe_feed'] = 20.0
                elif action == 'units':
                    tool.app.app_units = 'IN'
                elif action == 'home':
                    tool.on_grbl_homing()
                elif action == 'ui_reset':
                    with patch('appPlugins.Common.levelling_core.ShapeCollection'), \
                            patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                                  return_value=[]):
                        tool.set_tool_ui()
                elif action == 'close':
                    tool.on_plugin_cleanup()
                else:
                    tool.on_grbl_reset()
                self.run_job(jobs)
                self.assertFalse(any(c.startswith(('G38', 'G10')) for c in fake.commands()))
                self.assertIsNone(tool.toolchange.measurement)
                self.tearDown()
                self.tool = None

    def test_stop_during_probe_immediately_retires_measurement_before_worker_finishes(self):
        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = self.make_tool(options(PLATE_REMOVABLE))
        jobs = self.wire(tool, fake)
        tool.ui.toolchange_widget.measure_button.click()
        self.run_job(jobs)
        self.assertTrue(tool.ui.toolchange_widget.apply_button.isEnabled())
        entered, release = threading.Event(), threading.Event()
        def pause():
            if fake.commands()[-1].startswith('G38'):
                entered.set()
                release.wait(5.0)
        fake.read_hook = pause
        tool.ui.toolchange_widget.measure_button.click()
        worker = threading.Thread(target=jobs.pop(0))
        worker.start()
        try:
            self.assertTrue(entered.wait(2.0))
            tool.ui.toolchange_widget.stop_button.click()
            self.assertFalse(tool.ui.toolchange_widget.apply_button.isEnabled())
            self.assertIsNone(tool.toolchange.measurement)
        finally:
            release.set()
            worker.join(5.0)
        QtWidgets.QApplication.processEvents()
        self.assertFalse(worker.is_alive())
        self.assertFalse(any(c.startswith('G10') for c in fake.commands()))
        self.assertIsNone(tool.toolchange.measurement)

    def test_live_options_callback_retires_fixed_reference(self):
        from appHandlers.appLifecycle import AppLifecycle
        from appCommon.Common import LoudDict
        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = self.make_tool(options())
        jobs = self.wire(tool, fake)
        tool.ui.toolchange_widget.calibrate_button.click()
        self.run_job(jobs)
        tool.app.grbl_sender_tool = tool
        tool.app.options = LoudDict(tool.app.options)
        lifecycle = AppLifecycle(tool.app)
        tool.app.options.set_change_callback(lifecycle.on_options_value_changed)
        tool.app.options['tools_grbl_toolchange_plate'] = 'disabled'
        self.assertIsNone(tool.toolchange.reference)
        self.assertFalse(tool.ui.toolchange_widget.apply_button.isEnabled())

    def test_imported_inch_preferences_restore_in_mm_and_persist_physical_values(self):
        from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
        from appGUI.preferences.tools.ToolsGrblSenderPrefGroupUI import ToolsGrblSenderPrefGroupUI
        from tests.test_grbl_sender_construct import make_app
        app = make_app()
        group = ToolsGrblSenderPrefGroupUI(app)
        manager = PreferencesUIManager.__new__(PreferencesUIManager)
        manager.ui = SimpleNamespace(app=app)
        manager.defaults = AppDefaults()
        manager.alpha_color_keys = set()
        manager.defaults_form_fields = {'tools_grbl_toolchange_thickness': group.toolchange_thickness_entry}
        manager.defaults_write_form_field('tools_grbl_toolchange_thickness', defaults_dict={
            'tools_grbl_toolchange_thickness': 0.1, 'tools_grbl_toolchange_units': 'IN'})
        self.assertAlmostEqual(group.toolchange_thickness_entry.get_value(), 2.54)
        manager.defaults_read_form()
        self.assertEqual(manager.defaults['tools_grbl_toolchange_units'], 'MM')
        self.assertAlmostEqual(manager.defaults['tools_grbl_toolchange_thickness'], 2.54)
        manager.defaults_form_fields = {'tools_grbl_toolchange_plate': group.toolchange_plate_radio}
        for legacy in ('none', None, 'disabled'):
            manager.defaults_write_form_field('tools_grbl_toolchange_plate',
                                              defaults_dict={'tools_grbl_toolchange_plate': legacy})
            self.assertEqual(group.toolchange_plate_radio.get_value(), 'disabled')
            manager.defaults_read_form()
            self.assertEqual(manager.defaults['tools_grbl_toolchange_plate'], 'disabled')


if __name__ == '__main__':
    unittest.main()
