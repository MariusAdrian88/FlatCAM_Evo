#!/usr/bin/env python
"""
Behavior tests of the calibrated manual tool change (GRBL fixed or removable
touchplate). Run:

    .venv/Scripts/python.exe -m pytest tests/test_grbl_toolchange.py -q
"""
import os
import re
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets
from shapely import Point

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from defaults import AppDefaults
from appPlugins.ToolGcodeSender.ToolChange import (
    PLATE_NONE, PLATE_FIXED, PLATE_REMOVABLE, ToolChangeSettings, ToolChangeMeasurement,
    probe_target, check_contact, STATE_OFF, STATE_REFERENCE, STATE_MEASURED,
    STATE_NEEDS_REMOVAL, STATE_READY)
from appPlugins.Common.grbl.grbl_protocol import parse_grbl_modal
from appPlugins.ToolGcodeSender import SenderGen
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


def map_frame(fake):
    """The height-map frame of `fake`: raw report units and report-unit flag."""
    scale = 25.4 if fake.report_inches else 1.0
    return fake.wcs, (fake.wcs_x / scale, fake.wcs_y / scale, fake.wcs_z / scale), fake.report_inches


def attach_map(tool, frame, height, units='MM'):
    tool.al_voronoi_geo_storage = {1: {'point': Point((0.0, 0.0)), 'geo': None, 'height': float(height)}}
    tool._al_data_units = units
    tool.al_heights_valid = True
    tool._al_map_imported = False
    tool._al_map_frame = frame
    tool.job_map_generation = 99
    tool.job_lines = ['G1 Z-1.0']
    tool.job_line_numbers = [1]
    tool._start_seq = 5


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
            p_word = 'P%d' % ({'G59.1': 7, 'G59.2': 8, 'G59.3': 9}.get(self.wcs) or int(self.wcs[1:]) - 53)
            if p_word not in cmd.split():
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

    def test_ecut_with_probe_capability_is_not_refused_by_the_toolchange_probe(self):
        fake = ToolChangeFake(heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_REMOVABLE))
        tool._ecut_settings_capable = lambda port: True
        for caps, refused in (({'probe': True}, False), ({'probe': None}, True)):
            tool._grbl_caps = caps
            with patch.object(tool, '_toolchange_message') as message:
                tool._toolchange_ready()
            texts = [str(call.args[0]) for call in message.call_args_list]
            self.assertEqual(any('E-Cut' in text for text in texts), refused, caps)

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

    def test_supported_height_map_survives_fixed_plate_correction_and_is_reused(self):
        fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
        tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
        tool.on_toolchange_calibrate()
        self.assertIsNotNone(tool.toolchange.reference)

        # the map was probed in the calibrated frame; its height is a work-coordinate contact
        attach_map(tool, map_frame(fake), 0.2)
        target = SimpleNamespace(units='MM', coords_decimals=4, is_segmented_gcode=True,
                                 tools={1: {'gcode': 'G21 G54\nG0 X0 Y0 Z5\nG1 X0 Y0 Z-0.1 F100\n'}})
        for contact, expected_offset in ((-38.0, -48.0), (-37.0, -47.0), (-41.0, -51.0)):
            fake.heights = {(0.0, 0.0): contact}
            fake.start_z = 0.0
            tool.on_toolchange_measure()
            self.assertEqual(tool.toolchange.state(), STATE_MEASURED)
            tool.on_toolchange_apply()
            self.assertAlmostEqual(fake.wcs_z, expected_offset)
            self.assertTrue(tool.al_heights_valid)
            self.assertEqual(tool._al_map_frame, ('G54', (0.0, 0.0, expected_offset), False))
            result = tool.autolevell_gcode(target, 'v', strict=True)
            self.assertIsNotNone(result)
            cut = result[1].splitlines()[-1]
            commanded_work_z = float(cut.split('Z')[1].split()[0])
            # The board contact and WCS both follow the tool-length change.
            board_machine_z = -49.8 + (contact - (-40.0))
            self.assertAlmostEqual(commanded_work_z + fake.wcs_z - board_machine_z, -0.1)
            self.assertIsNone(tool.job_map_generation)
            self.assertNotEqual(tool._start_seq, 5)
            self.assertAlmostEqual(tool.toolchange.reference['offset_z'], expected_offset)
            self.assertAlmostEqual(tool.toolchange.reference['contact_machine_z'], contact)

    def test_removable_plate_with_a_map_refuses_an_unprovable_board_zero(self):
        fake = ToolChangeFake(wcs_z=0.0, start_z=0.0, heights={(0.0, 0.0): -5.0})
        tool = make_tool(fake, options(PLATE_REMOVABLE,
                                       tools_grbl_toolchange_thickness=1.5,
                                       tools_grbl_toolchange_probe_distance=50.0))
        # the map was probed on the board surface, 1.5 below the top of the plate
        attach_map(tool, map_frame(fake), -6.5)
        tool.on_toolchange_measure()
        tool.on_toolchange_apply()
        self.assertEqual(fake.commands(), [])
        self.assertIsNone(tool.toolchange.measurement)
        self.assertTrue(tool.al_heights_valid)
        self.assertEqual(tool._al_map_frame, ('G54', (0.0, 0.0, 0.0), False))
        self.assertTrue(any('removable' in str(call).lower() and 'map' in str(call).lower()
                            for call in tool.ui.toolchange_widget.set_message.call_args_list))

    def test_height_map_with_unknown_provenance_or_another_frame_refuses_the_measurement(self):
        variants = {
            'imported': lambda tool: setattr(tool, '_al_map_imported', True),
            'no_frame': lambda tool: setattr(tool, '_al_map_frame', None),
            'units': lambda tool: setattr(tool, '_al_data_units', 'IN'),
            'other_wcs': lambda tool: setattr(
                tool, '_al_map_frame', ('G55', tool._al_map_frame[1], tool._al_map_frame[2])),
            'other_offset': lambda tool: setattr(
                tool, '_al_map_frame', (tool._al_map_frame[0],
                                        (tool._al_map_frame[1][0], tool._al_map_frame[1][1],
                                         tool._al_map_frame[1][2] + 1.0), tool._al_map_frame[2])),
            'other_report_units': lambda tool: setattr(
                tool, '_al_map_frame', (tool._al_map_frame[0], tool._al_map_frame[1],
                                        not tool._al_map_frame[2])),
            'incomplete': lambda tool: tool.al_voronoi_geo_storage[1].__setitem__('height', None),
            'live_wcs': lambda tool: setattr(fake, 'wcs', 'G55'),
            'live_report_units': lambda tool: setattr(fake, 'report_inches', True),
        }
        for name, mutate in variants.items():
            with self.subTest(variant=name):
                fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
                tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
                tool.on_toolchange_calibrate()
                attach_map(tool, map_frame(fake), 10.0)
                mutate(tool)
                fake.heights = {(0.0, 0.0): -38.0}
                fake.start_z = 0.0
                count = fake.probe_count
                tool.on_toolchange_measure()
                self.assertEqual(fake.probe_count, count, name)
                self.assertIsNone(tool.toolchange.measurement, name)
                self.assertFalse(any(command.startswith('G10') for command in fake.commands()), name)

    def test_failed_contact_or_offset_readback_leaves_no_usable_map(self):
        for fault in ('contact', 'readback'):
            with self.subTest(fault=fault):
                fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
                tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
                tool.on_toolchange_calibrate()
                attach_map(tool, map_frame(fake), 10.0)
                if fault == 'contact':
                    fake.heights = {}
                    fake.start_z = 0.0
                    tool.on_toolchange_measure()
                else:
                    fake.heights = {(0.0, 0.0): -38.0}
                    fake.start_z = 0.0
                    tool.on_toolchange_measure()
                    def fail(cmd):
                        if cmd.startswith('G10'):
                            fake.start_z += 1.0
                    fake.command_hook = fail
                    tool.on_toolchange_apply()
                    self.assertTrue(any(command.startswith('G10') for command in fake.commands()))

                self.assertIsNone(tool.toolchange.measurement, fault)
                self.assertFalse(tool.al_heights_valid, fault)
                self.assertIsNone(tool._al_map_frame, fault)
                self.assertIsNone(tool.job_map_generation, fault)
                before = len(fake.commands())
                tool.on_toolchange_measure()
                self.assertEqual(len(fake.commands()), before, fault)

    def test_stop_and_zero_invalidate_the_active_height_map(self):
        for action in ('stop', 'zero'):
            with self.subTest(action=action):
                fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
                tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
                tool.on_toolchange_calibrate()
                attach_map(tool, map_frame(fake), 10.0)
                if action == 'stop':
                    tool.__dict__['_toolchange_busy'] = True
                    tool.__dict__['_toolchange_action'] = 'measure'
                    tool.on_grbl_stop()
                else:
                    tool.on_grbl_zero('z')
                self.assertFalse(tool.al_heights_valid, action)
                self.assertIsNone(tool._al_map_frame, action)
                self.assertIsNone(tool.job_map_generation, action)

    def test_zero_and_reset_invalidate_imported_or_unbound_maps(self):
        for action in ('zero', 'reset'):
            for imported in (True, False):
                with self.subTest(action=action, imported=imported):
                    fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
                    tool = make_tool(fake, options(PLATE_FIXED))
                    attach_map(tool, None, 10.0)
                    tool._al_map_imported = imported
                    start_seq = tool._start_seq
                    if action == 'zero':
                        tool.on_grbl_zero('z')
                    else:
                        tool._toolchange_new_session('reset')
                    self.assertFalse(tool.al_heights_valid, action)
                    self.assertIsNone(tool._al_map_frame, action)
                    self.assertFalse(tool._al_map_imported, action)
                    self.assertIsNone(tool.job_map_generation, action)
                    self.assertNotEqual(tool._start_seq, start_seq, action)

    def test_a_repeated_apply_result_does_not_shift_the_map_twice(self):
        fake = ToolChangeFake(wcs_z=-50.0, start_z=0.0, heights={(0.0, 0.0): -40.0})
        tool = make_tool(fake, options(PLATE_FIXED, tools_grbl_toolchange_probe_distance=50.0))
        tool.on_toolchange_calibrate()
        attach_map(tool, map_frame(fake), 10.0)
        fake.heights = {(0.0, 0.0): -38.0}
        fake.start_z = 0.0
        tool.on_toolchange_measure()
        payloads = []
        tool.toolchange_result_sig.emit.side_effect = lambda payload: (
            payloads.append(payload), tool._on_toolchange_result(payload))
        tool.on_toolchange_apply()
        applied = [payload for payload in payloads if payload.get('action') == 'apply'][0]
        height = tool.al_voronoi_geo_storage[1]['height']
        frame = tool._al_map_frame

        tool._on_toolchange_result(applied)
        self.assertEqual(tool.al_voronoi_geo_storage[1]['height'], height)
        self.assertEqual(tool._al_map_frame, frame)
        self.assertAlmostEqual(height, 10.0)

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

    def test_g59_1_apply_writes_p7_and_keeps_the_machine_usable(self):
        fake = ToolChangeFake(wcs_z=-50, wcs_x=2, wcs_y=3, heights={(0.0, 0.0): -40})
        fake.wcs = 'G59.1'
        tool = make_tool(fake, options())
        tool.on_toolchange_calibrate()
        fake.start_z = 0.0
        fake.heights = {(0.0, 0.0): -38}
        tool.on_toolchange_measure()
        tool.on_toolchange_apply()
        self.assertIn('G10 L2 P7 Z-48.0000', fake.commands())
        self.assertAlmostEqual(fake.wcs_z, -48)
        self.assertNotEqual(tool._grbl_recovery, 'needs_reset')

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
            from tests.grbl_stream_helpers import drop_console_event_filters
            drop_console_event_filters()
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
                    # Home is refused during an operation: its session change is tested directly
                    tool._toolchange_new_session('home')
                elif action == 'ui_reset':
                    with patch('appPlugins.Common.levelling_core.ShapeCollection'), \
                            patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                                  return_value=[]):
                        tool.set_tool_ui()
                elif action == 'close':
                    tool.on_plugin_cleanup()
                else:
                    tool._reset_now()
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


class TestHostToolChangePreamble(unittest.TestCase):
    """The pure resume build of a host tool change (pause policy): preamble and the rest of the stream."""

    @staticmethod
    def start(**changes):
        data = dict(pos=(0.0, 0.0, 5.0), units='MM', dist=90, motion=0, feed_mode=94, plane=17, wcs=54,
                    feed=None, spindle=5, speed=0.0, coolant=())
        data.update(changes)
        return SenderGen.JobStart(**data)

    def resume(self, lines, m6_index, next_index=None, dwell=0.0, **changes):
        preamble, tail = SenderGen.tool_change_resume(
            lines, m6_index, m6_index + 1 if next_index is None else next_index, 5.0, 'MM', 4, dwell,
            self.start(**changes))
        # '10.0000' -> '10'
        return [re.sub(r'(?<=\d)\.0+\b', '', line) for line in preamble], tail

    def test_preamble_and_tail_of_the_cases(self):
        absolute = ['G21 G90', 'G0 X10 Y10', 'G1 Z-1 F300', 'G1 X20']
        cases = [
            # name, lines, m6 index, next index, JobStart changes, tail of the preamble, tail
            ('a mixed block', absolute + ['T2 M6 G0 X0', 'M0', 'X30'], 4, 6, {},
             ['G1 Z-1 F300', 'G90', 'G1', 'G0 X0'], ['X30']),
            ('b G91 prefix', ['G91', 'G1 X5 F300', 'T2 M6', 'X5'], 2, 3, dict(pos=(5.0, 5.0, 10.0)),
             ['G0 Z10', 'G0 X10 Y5', 'M5 S0', 'G1 Z10 F300', 'G91', 'G1'], ['X5']),
            ('i G91 at the job start', ['G1 X10 F300', 'T2 M6', 'X5'], 1, 2, dict(pos=(100.0, 0.0, 5.0), dist=91),
             ['G0 X110 Y0', 'M5 S0', 'G1 Z5 F300', 'G91', 'G1'], ['X5']),
            ('h M6 first block', ['T2 M6', 'G1 Z-1 F300', 'G1 X20'], 0, 1, dict(pos=(5.0, 5.0, 10.0)),
             ['G0 Z10', 'G0 X5 Y5', 'M5 S0', 'G90', 'G0'], ['G1 Z-1 F300', 'G1 X20']),
            ('j G1 inherited', ['X1', 'T2 M6', 'X2'], 1, 2, dict(motion=1), ['G90', 'G1'], ['X2']),
            ('g G2 in the M6 block', ['G17', 'G0 X10 Y0 Z5', 'G1 F300', 'G2 X0 Y10 I-10 J0',
                                      'T2 M6 X0 Y0 I-5 J0', 'M0', 'G1 X1'], 4, 6, {},
             ['G1 Z5 F300', 'G90', 'G2 X0 Y0 I-5 J0'], ['G1 X1']),
            ('g G3 after the M6', ['G17', 'G0 X10 Y0 Z5', 'G1 F300', 'G3 X0 Y10 I-10 J0',
                                   'T2 M6', 'M0', 'X5 Y5 I1 J0'], 4, 6, {},
             ['G1 Z5 F300', 'G90'], ['G3 X5 Y5 I1 J0']),
        ]
        for name, lines, m6, nxt, changes, end, tail in cases:
            with self.subTest(name):
                preamble, rest = self.resume(lines, m6, nxt, **changes)
                self.assertEqual(preamble[-len(end):], end)
                self.assertEqual(rest, tail)
                self.assertFalse([line for line in preamble + rest if 'M6' in line or 'T2' in line])

    def test_states_that_the_program_does_not_set(self):
        # the initial plane and WCS are always written, the program's own ones win
        preamble, _rest = self.resume(['G0 X1 Y1 Z5', 'T2 M6'], 1, plane=18, wcs=55)
        self.assertEqual(preamble[8:10], ['G18', 'G55'])
        preamble, _rest = self.resume(['G19 G56', 'G0 X1 Y1 Z5', 'T2 M6'], 2, plane=18, wcs=55)
        self.assertEqual(preamble[8:10], ['G19', 'G56'])
        # the tool length offset line is not written
        preamble, _rest = self.resume(['G43.1 Z1', 'G0 X1 Y1 Z5', 'T2 M6'], 2)
        self.assertFalse([line for line in preamble if line.startswith(('G43', 'G49'))])

    def test_spindle_and_coolant_are_restored_from_the_program(self):
        on = ['G0 X1 Y1 Z5', 'M3 S10000', 'M8', 'T2 M6', 'G1 X2 F100']
        preamble, _rest = self.resume(on, 3, dwell=3.0)
        self.assertEqual(preamble[:2], ['M5', 'M9'])
        index = preamble.index('M3 S10000')
        self.assertEqual(preamble[index:index + 3], ['M3 S10000', 'G4 P3', 'M8'])
        off = ['G0 X1 Y1 Z5', 'M3 S10000', 'M5', 'M9', 'T2 M6']
        preamble, _rest = self.resume(off, 4, dwell=3.0, spindle=3, coolant=(7,))
        self.assertEqual(preamble[:2], ['M5', 'M9'])
        self.assertFalse([line for line in preamble if line.split()[0] in ('M3', 'M4', 'M7', 'M8', 'G4')])

    def test_unknown_modes_refuse_with_the_mode_name(self):
        lines = ['G21 X1', 'T2 M6']    # G21 sets no group of these modes
        for field, value, word in (('feed_mode', None, 'feed mode'), ('feed_mode', 93, 'G93'),
                                   ('dist', None, 'distance mode'), ('motion', None, 'motion mode'),
                                   ('plane', None, 'plane'), ('wcs', None, 'work coordinate'),
                                   ('spindle', None, 'spindle')):
            with self.subTest(field, value=value):
                with self.assertRaises(ValueError) as caught:
                    self.resume(lines, 1, **{field: value})
                self.assertIn(word, str(caught.exception))
        # a mode that the program sets itself is not needed from the controller
        self.resume(['G90 G0 G17 G54 G94 M5', 'G0 X1 Y1 Z5', 'T2 M6'], 2, dist=None, motion=None, feed_mode=None,
                    plane=None, wcs=None, spindle=None)

    def test_check_at_start(self):
        numbers = [10, 11, 12, 13]
        ok = ['G0 X1 Y1 Z5', 'M3 S1000', 'T2 M6', 'G1 X2 F100']
        self.assertIsNone(SenderGen.check_tool_change_job(ok, numbers, 5.0, 'MM', 4, 0.0, self.start()))
        probe = ['G0 X1 Y1 Z5', 'G38.2 Z-5 F10', 'T2 M6', 'G1 X2 F100']
        message = SenderGen.check_tool_change_job(probe, numbers, 5.0, 'MM', 4, 0.0, self.start())
        self.assertIn('12', message)
        message = SenderGen.check_tool_change_job(ok, numbers, 5.0, 'MM', 4, 0.0, self.start(feed_mode=None))
        self.assertIn('feed mode', message)

    def test_from_live_reads_the_modal_answer(self):
        modal = parse_grbl_modal('[GC:G1 G55 G18 G21 G91 G94 M5 T0 F100 S0]')
        start = SenderGen.JobStart.from_live((1.0, 2.0, 3.0), 'MM', modal, coolant=(8,))
        self.assertEqual((start.pos, start.units, start.dist, start.motion, start.feed_mode, start.plane, start.wcs),
                         ((1.0, 2.0, 3.0), 'MM', 91, 1, 94, 18, 55))
        self.assertEqual((start.feed, start.spindle, set(start.coolant)), (None, None, {8}))
        start = SenderGen.JobStart.from_live((0, 0, 0), 'MM', parse_grbl_modal('[GC:G21]'))
        self.assertEqual((start.dist, start.motion, start.feed_mode, start.plane, start.wcs),
                         (None, None, None, None, None))


class ToolPauseFake(FakeGrbl):
    """A FakeGrbl whose $G answer, G92 offset and machine position the test sets."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gc = None
        self.g92 = (0.0, 0.0, 0.0)
        self.mpos = (0.0, 0.0, 0.0)
        self.g54 = (0.0, 0.0, 0.0)

    def _answer(self, cmd):
        if cmd == '$G' and self.gc is not None:
            self._send(self.gc, 'ok')
        elif cmd == '$#':
            self._send('[G54:%.3f,%.3f,%.3f]' % self.g54,'[G92:%.3f,%.3f,%.3f]' % self.g92, '[TLO:0.000]', 'ok')
        elif cmd == '?':
            self._send('<%s|MPos:%.3f,%.3f,%.3f|FS:0,0>' % ((self.state,) + self.mpos))
        else:
            super()._answer(cmd)


class TestHostToolChangePolicy(unittest.TestCase):
    """The M6 policy of a controller with no tool change (stock GRBL), through the real Start."""
    PROGRAM = 'G21 G90\nG0 X10 Y10\nG1 Z-1 F300\nG1 X20\nT2 M6\nM0\nX30\n'

    def start(self, policy, program=None, fake=None):
        from tests.grbl_stream_helpers import make_stream_tool
        fake = fake or ToolPauseFake()
        tool = make_stream_tool(fake, source=program or self.PROGRAM)
        tool.app.collection.get_by_name.return_value.z_move = 5.0
        tool.app.options['tools_grbl_m6_policy'] = policy
        tool.on_start()
        return tool, fake

    def resume(self, tool, fake):
        """Press Resume; the lines that the port got from it."""
        before = len(fake.written)
        tool._on_resume_button()
        return [c for c in fake.commands()[before:] if c != '?']

    def test_host_tool_change_pauses_and_resumes(self):
        tool, fake = self.start('pause')
        commands = [c for c in fake.commands() if c != '?']
        self.assertEqual(tool.stream_ended_sig.emit.call_args.args[0], 'tool_change')
        self.assertFalse([c for c in commands if 'M6' in c or c.strip() == 'M0'])
        self.assertNotIn('X30', commands)
        self.assertEqual(commands[-1], 'M5')
        self.assertEqual(commands[-2], 'G1 X20')
        self.assertEqual(tool._tool_pause.index, 6)
        self.assertEqual(tool._tool_pause.lines[tool._tool_pause.index], 'X30')
        self.assertEqual(tool._stream_result['done'], 6)
        self.assertIn('T2', tool.ui.status_strip.set_recovery.call_args.args[0])
        self.assertIsNone(tool.action_allowed('jog'))

        # the probe and a jog: the controller is in other modes, at another position
        fake.gc = '[GC:G0 G54 G17 G20 G91 G94 M5 M9 T0 F0 S0]'
        fake.mpos = (100.0, 100.0, 0.0)
        written = self.resume(tool, fake)
        at = written.index('X30')
        self.assertEqual(written[:2], ['$#', '$$'])
        steps = ['G21', 'G90', 'G0 Z5.0000', 'G0 X20.0000 Y10.0000', 'G1 Z-1.0000 F300.0000']
        positions = [written.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('G1', written[positions[-1] + 1:at])
        self.assertTrue(all(w not in written[:at] for w in ('G1 X20', 'G0 X10 Y10')))
        self.assertEqual(tool._stream_result['done'], 7)
        self.assertIsNone(tool._tool_pause)

    def test_resume_rejected_by_the_profile_recheck_writes_nothing(self):
        tool, fake = self.start('pause')
        profile = object()
        tool._profile_compute = MagicMock(return_value=SenderGen._ProfileOutcome(out='', refused=SimpleNamespace()))
        tool._profile_present = MagicMock(return_value=False)
        with patch('appPlugins.ToolGcodeSender.Sender.active_profile_of', return_value=profile):
            written = self.resume(tool, fake)
        self.assertEqual(written, ['$#'])
        self.assertIs(tool._profile_compute.call_args.kwargs['correct'], False)
        tool._profile_present.assert_called_once()
        self.assertIsNotNone(tool._tool_pause)
        self.assertIsNone(tool._grbl_op)

    def test_resume_checks_the_offsets_that_the_operator_set_during_the_pause(self):
        import tempfile
        from appCommon.machine_profile.profile import MachineProfile, save_profile
        with tempfile.TemporaryDirectory() as folder:
            save_profile(MachineProfile(name='Small', bed_x=100.0, bed_y=100.0), folder)
            fake = ToolPauseFake()
            fake.gc = '[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]'
            from tests.grbl_stream_helpers import make_stream_tool
            tool = make_stream_tool(fake, source=self.PROGRAM)
            tool.app.collection.get_by_name.return_value.z_move = 5.0
            tool.app.options.update({'tools_grbl_m6_policy': 'pause', 'global_machine_profile': 'Small',
                                     'global_machine_profiles_folder': folder})
            tool._grbl_report_inches = False
            tool._confirm_profile_stream = MagicMock(return_value=True)
            tool._confirm_profile_resume = MagicMock(return_value=True)
            tool.on_start()
            self.assertIsNotNone(tool._tool_pause)
            # G10 L2 P1 X250 of the operator: the work X moves 250 mm out of the 100 mm bed
            fake.g54 = (250.0, 0.0, 0.0)
            written = self.resume(tool, fake)
            self.assertFalse([c for c in written if c.startswith(('G0', 'G1', 'X'))], written)
            self.assertIsNotNone(tool._tool_pause)
            self.assertIsNone(tool._grbl_op)

    def test_levelled_job_resumes_only_with_its_height_map_and_frame(self):
        from tests.grbl_stream_helpers import make_levelled_stream_tool
        text = ('G21\nG90\nG94\nG00 Z2.0000\nG00 X1.0000 Y1.0000\nG01 Z-0.1000 F300.0000\nG01 X3.0000 Y1.0000\n'
                'T2 M6\nM0\nG01 X5.0000 Y2.0000\nM05\nM2\n')
        # (name, the controller or the map changes during the pause, the Resume is refused)
        for name, change, refuse in (('same', None, False), ('zero', 'zero', True), ('g54_x', (3.0, 0.0, 0.0), True),
                                     ('g54_y', (0.0, 3.0, 0.0), True), ('g54_z', (0.0, 0.0, -4.0), False)):
            with self.subTest(name):
                fake = ToolPauseFake()
                tool, target = make_levelled_stream_tool(fake, text=text)
                target.z_move = 5.0
                tool.app.options['tools_grbl_m6_policy'] = 'pause'
                tool.on_start()
                self.assertIsNotNone(tool._tool_pause, [str(c) for c in tool.app.inform.emit.call_args_list])
                if change == 'zero':
                    tool.on_grbl_zero('x')
                    self.assertFalse(tool.al_heights_valid)
                elif change is not None:
                    fake.g54 = change       # G10 L2 P1 of the operator in the console
                written = self.resume(tool, fake)
                moves = [c for c in written if c.startswith(('G0', 'G1', 'X'))]
                if refuse:
                    self.assertEqual(moves, [], written)
                    self.assertIsNotNone(tool._tool_pause)
                    self.assertIsNone(tool._grbl_op)
                    self.assertIn('height map', str(tool.app.inform.emit.call_args.args[0]))
                else:
                    self.assertTrue(moves, written)
                    self.assertIsNone(tool._tool_pause)

    def test_host_tool_change_cases(self):
        spindle_on = 'G21 G90\nG0 X10 Y10\nM3 S10000\nG1 Z-1 F300\nG1 X20\nT2 M6\nM0\nX30\n'
        probe = 'G21 G90\nG0 X10 Y10\nG38.2 Z-5 F50\nG1 X20\nT2 M6\nM0\nX30\n'
        no_feed_mode = '[GC:G0 G54 G17 G21 G90 M5 M9 T0 F0 S0]'
        for name, program, fake_state, refused in (
                ('g92_at_resume', None, {}, None),
                ('spindle_on', spindle_on, {}, None),
                ('probe_line', probe, {}, 'Line 5'),
                ('no_feed_mode', None, {'gc': no_feed_mode}, 'feed mode'),
                ('g92_at_start', None, {'g92': (1.0, 0.0, 0.0)}, 'G92')):
            with self.subTest(name):
                fake = ToolPauseFake()
                for key, value in fake_state.items():
                    setattr(fake, key, value)
                tool, fake = self.start('pause', program, fake)
                if refused is not None:
                    texts = ' '.join(str(call.args[0]) for call in tool.app.inform.emit.call_args_list)
                    self.assertIn(refused.lower(), texts.lower())
                    self.assertIsNone(tool._tool_pause)
                    self.assertFalse([c for c in fake.commands() if c.startswith(('G0', 'G1', 'M'))])
                    self.assertIsNone(tool._grbl_op)
                    self.assertEqual(tool.ui.start_button.setDisabled.call_args.args[0], False)
                    continue
                if name == 'g92_at_resume':
                    fake.g92 = (5.0, 0.0, 0.0)
                    self.assertEqual(self.resume(tool, fake), ['$#'])
                    self.assertIn('Clear G92', str(tool.app.inform.emit.call_args.args[0]))
                    self.assertIsNotNone(tool._tool_pause)
                    self.assertIsNone(tool._grbl_op)
                else:
                    written = self.resume(tool, fake)
                    at = next(i for i, c in enumerate(written) if c.startswith('M3 S10000'))
                    self.assertRegex(written[at + 1], r'^G4 P3')
                    plunge = written.index('G1 Z-1.0000 F300.0000')
                    self.assertLess(at, plunge)
                    self.assertLess(plunge, written.index('X30'))

    def test_m6_policy_skip_and_send(self):
        for policy, expected in (('skip', ['T2', 'M0']), ('send', ['T2 M6', 'M0'])):
            with self.subTest(policy=policy):
                tool, fake = self.start(policy)
                commands = [c for c in fake.commands() if c != '?']
                at =commands.index(expected[0])
                self.assertEqual(commands[at:at + 2], expected)
                self.assertIsNone(tool._tool_pause)


if __name__ == '__main__':
    unittest.main()
