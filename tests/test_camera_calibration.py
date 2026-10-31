#!/usr/bin/env python
"""
Drill-referenced camera calibration: offset conversion, capture sequence
lifecycle and the calibrated observation used by fiducial registration.

Run: .venv/Scripts/python.exe -m pytest tests/test_camera_calibration.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtWidgets, QtGui, QtCore

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from defaults import AppDefaults
from appPlugins.ToolGcodeSender.CameraCalibration import (
    CameraCalibration, CAMERA_STORED, CAMERA_VERIFIED,
    CAMERA_STALE, CAMERA_Z_MISMATCH)


def calibrated(**changes):
    """A calibration captured from a (100, 80) spindle reference and a (70, 90) camera reference."""
    values = {
        'tools_camera_calibrated': True,
        'tools_camera_offset_x': 30.0,
        'tools_camera_offset_y': -10.0,
        'tools_camera_view_z': -5.0,
        'tools_camera_reference_x': 100.0,
        'tools_camera_reference_y': 80.0,
        'tools_camera_units': 'MM',
        'tools_camera_cal_device': 'cam-A',
        'tools_camera_cal_rotation': 0,
        'tools_camera_cal_mirror': False,
    }
    values.update(changes)
    return CameraCalibration.from_options(values)


class TestCameraCalibrationMath(unittest.TestCase):

    def test_capture_rejects_unknown_units_and_non_finite_positions(self):
        for units in (None, '', '??'):
            with self.subTest(units=units):
                calibration = CameraCalibration()
                ok, _message = calibration.capture('spindle', (100.0, 80.0, 0.0), units, 1, 'cam-A', 0, False)
                self.assertFalse(ok)
                self.assertFalse(calibration.has_calibration)

        for position in ((float('nan'), 80.0, 0.0), (100.0, float('inf'), 0.0)):
            with self.subTest(position=position):
                calibration = CameraCalibration()
                ok, _message = calibration.capture('spindle', position, 'MM', 1, 'cam-A', 0, False)
                self.assertFalse(ok)
                self.assertFalse(calibration.has_calibration)

    def test_camera_capture_needs_the_spindle_reference_of_the_same_session(self):
        calibration = CameraCalibration()
        ok, _message = calibration.capture('camera', (70.0, 90.0, -5.0), 'MM', 1, 'cam-A', 0, False)
        self.assertFalse(ok)
        self.assertFalse(calibration.has_calibration)

        calibration.capture('spindle', (100.0, 80.0, 0.0), 'MM', 1, 'cam-A', 0, False)
        calibration.start_session(2)
        ok, _message = calibration.capture('camera', (70.0, 90.0, -5.0), 'MM', 2, 'cam-A', 0, False)
        self.assertFalse(ok)
        self.assertFalse(calibration.has_calibration)

class TestCameraCalibrationValidity(unittest.TestCase):

    def test_saved_calibration_is_stored_until_verified_in_this_session(self):
        calibration = calibrated()
        state, _reason = calibration.state(1, 'MM', 'cam-A', 0, False, machine_z=-5.0)
        self.assertEqual(state, CAMERA_STORED)

        ok, _message, distance = calibration.verify(
            (70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)
        self.assertTrue(ok)
        self.assertAlmostEqual(distance, 0.0)
        state, _reason = calibration.state(1, 'MM', 'cam-A', 0, False, machine_z=-5.0)
        self.assertEqual(state, CAMERA_VERIFIED)

        calibration.start_session(2)
        state, _reason = calibration.state(2, 'MM', 'cam-A', 0, False, machine_z=-5.0)
        self.assertEqual(state, CAMERA_STORED)

    def test_camera_or_orientation_change_makes_the_calibration_stale(self):
        for camera, rotation, mirror in (('cam-B', 0, False), ('cam-A', 90, False), ('cam-A', 0, True)):
            with self.subTest(camera=camera, rotation=rotation, mirror=mirror):
                calibration = calibrated()
                state, _reason = calibration.state(1, 'MM', camera, rotation, mirror, machine_z=-5.0)
                self.assertEqual(state, CAMERA_STALE)

    def test_user_repeatability_tolerance_decides_the_verification(self):
        calibration = calibrated()
        ok, message, _distance = calibration.verify(
            (70.0, 90.1), -5.0, 'MM', 1, 'cam-A', 0, False, tolerance=0.2)
        self.assertTrue(ok, message)
        ok, message, distance = calibration.verify(
            (70.0, 90.1), -5.0, 'MM', 1, 'cam-A', 0, False, tolerance=0.01)
        self.assertFalse(ok)
        self.assertGreater(distance, 0.0)
        self.assertEqual(message, 'repeatability_failed')

    def test_user_viewing_z_tolerance_decides_the_observation(self):
        calibration = calibrated()
        calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)
        state, reason = calibration.state(
            1, 'MM', 'cam-A', 0, False, machine_z=-5.4, view_tolerance=0.1)
        self.assertEqual((state, reason), (CAMERA_Z_MISMATCH, 'view_z_mismatch'))
        state, _reason = calibration.state(
            1, 'MM', 'cam-A', 0, False, machine_z=-5.4, view_tolerance=0.5)
        self.assertEqual(state, CAMERA_VERIFIED)
        result = calibration.observe(
            (70.0, 90.0), -5.4, 'MM', 1, 'cam-A', 0, False, view_tolerance=0.1)
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], CAMERA_Z_MISMATCH)

    def test_corrupt_stored_values_load_as_no_calibration(self):
        base = {
            'tools_camera_calibrated': True,
            'tools_camera_offset_x': 30.0,
            'tools_camera_offset_y': -10.0,
            'tools_camera_view_z': -5.0,
            'tools_camera_reference_x': 100.0,
            'tools_camera_reference_y': 80.0,
            'tools_camera_units': 'MM',
            'tools_camera_cal_device': 'cam-A',
            'tools_camera_cal_rotation': 0,
            'tools_camera_cal_mirror': False,
        }
        for changes in (
                {'tools_camera_offset_x': 'not-a-number'},
                {'tools_camera_offset_y': float('nan')},
                {'tools_camera_view_z': float('inf')},
                {'tools_camera_reference_x': None},
                {'tools_camera_units': '??'},
                {'tools_camera_cal_device': ''},
                {'tools_camera_calibrated': 'false'}):
            with self.subTest(changes=changes):
                values = dict(base)
                values.update(changes)
                calibration = CameraCalibration.from_options(values)
                self.assertFalse(calibration.has_calibration)
                self.assertFalse(calibration.verified)

    def test_saved_values_round_trip_and_clear(self):
        calibration = calibrated()
        options = {}
        calibration.save(options)
        loaded = CameraCalibration.from_options(options)
        self.assertTrue(loaded.has_calibration)
        self.assertEqual(loaded.offset, (30.0, -10.0))
        self.assertEqual(loaded.reference, (100.0, 80.0))
        self.assertEqual(loaded.units, 'MM')
        self.assertEqual(loaded.camera_id, 'cam-A')

        loaded.reset()
        loaded.save(options)
        self.assertFalse(CameraCalibration.from_options(options).has_calibration)

def make_camera_tool():
    """ToolGCodeSender with only the state the camera handlers touch."""
    from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender

    tool = ToolGCodeSender.__new__(ToolGCodeSender)
    tool.app = MagicMock()
    tool.camera_reference_sig = MagicMock()
    tool.camera_request_sig = MagicMock()
    tool.app.decimals = 4
    tool.app.app_units = 'MM'
    tool.app.options = dict(AppDefaults.factory_defaults)
    tool.app.defaults = tool.app.options
    tool.units = 'MM'
    tool.decimals = 4
    tool._grbl_report_inches = False
    tool._last_wco = None
    tool._camera_last_z = None
    tool._camera_session_id = 1
    tool._camera_request_id = 1
    tool._grbl_connected = True
    tool._grbl_port_ready = MagicMock(return_value=True)
    tool._grbl_start_op = MagicMock(return_value=True)
    tool.camera_calibration = CameraCalibration()
    tool.ui = MagicMock()
    tool.ui.camera_widget.current_camera_id.return_value = 'cam-A'
    tool.ui.camera_widget.rotation.return_value = 0
    tool.ui.camera_widget.mirror.return_value = False
    tool.ui.camera_widget.preview_usable.return_value = True
    tool.ui.camera_widget.verify_tolerance.return_value = 0.05
    tool.ui.camera_widget.view_tolerance.return_value = 0.5
    tool.camera_observation_sig = MagicMock()
    return tool


def payload(tool, role='spindle', report=None, **changes):
    data = {
        'role': role,
        'report': report if report is not None else {'state': 'Idle', 'mpos': (100.0, 80.0, 0.0)},
        'session': tool._camera_session_id,
        'request': tool._camera_request_id,
        'units': tool.units,
        'report_inches': tool._grbl_report_inches,
        'camera_id': 'cam-A',
        'rotation': 0,
        'mirror': False,
        'wco': None,
    }
    data.update(changes)
    return data


class TestCameraSenderBehaviour(unittest.TestCase):

    def capture_both(self, tool):
        tool._on_camera_capture(payload(tool, 'spindle'))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'mpos': (70.0, 90.0, -5.0)}))
        return tool.camera_calibration

    def test_capture_is_refused_without_a_fresh_idle_position(self):
        tool = make_camera_tool()
        tool._on_camera_capture(payload(tool, 'spindle', report={'state': 'Run', 'mpos': (100.0, 80.0, 0.0)}))
        self.assertFalse(tool.camera_calibration.has_calibration)
        self.assertTrue(tool.app.inform.emit.called)

    def test_capture_uses_the_reported_machine_position_and_units(self):
        tool = make_camera_tool()
        calibration = self.capture_both(tool)
        self.assertTrue(calibration.has_calibration)
        self.assertEqual(calibration.offset, (30.0, -10.0))
        self.assertEqual(tool.app.options['tools_camera_calibrated'], True)
        self.assertEqual(tool.app.options['tools_camera_offset_x'], 30.0)
        self.assertEqual(tool.app.options['tools_camera_offset_y'], -10.0)
        self.assertEqual(tool.app.options['tools_camera_view_z'], -5.0)

    def test_capture_converts_report_inches_to_application_units(self):
        tool = make_camera_tool()
        tool._grbl_report_inches = True
        tool._on_camera_capture(payload(
            tool, 'spindle', report={'state': 'Idle', 'mpos': (4.0, 3.0, 0.0)}, report_inches=True))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'mpos': (2.0, 4.0, -0.2)}, report_inches=True))
        self.assertAlmostEqual(tool.camera_calibration.offset[0], 50.8)
        self.assertAlmostEqual(tool.camera_calibration.offset[1], -25.4)

    def test_a_wpos_only_report_adds_the_work_offset(self):
        tool = make_camera_tool()
        wco = (10.0, 20.0, 0.0)
        tool._on_camera_capture(payload(
            tool, 'spindle', report={'state': 'Idle', 'wpos': (90.0, 60.0, 0.0), 'wco': wco}))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'wpos': (60.0, 70.0, -5.0), 'wco': wco}))
        self.assertTrue(tool.camera_calibration.has_calibration)
        self.assertEqual(tool.camera_calibration.reference, (100.0, 80.0))
        self.assertEqual(tool.camera_calibration.offset, (30.0, -10.0))

    def test_a_measurement_without_a_fresh_z_is_refused_by_the_sender(self):
        tool = make_camera_tool()
        self.capture_both(tool)
        tool.camera_calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)
        result = tool.camera_observe(70.0, 90.0)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'no_fresh_z')

    def test_polling_refuses_movement_before_a_later_idle_wco(self):
        tool = make_camera_tool()
        tool._grbl_query_status = MagicMock(side_effect=[
            '<Idle|MPos:1,2,3>', '<Jog|MPos:2,2,3>', '<Idle|MPos:2,2,3|WCO:10,20,0>'])
        op = SimpleNamespace(cancel=threading.Event(), failed=False)
        report = tool._camera_wait_report(op, True)
        self.assertEqual(report['state'], 'Jog')

    def test_a_wpos_only_capture_never_uses_a_cached_work_offset(self):
        tool = make_camera_tool()
        tool._last_wco = (10.0, 20.0, 0.0)
        tool._on_camera_capture(payload(
            tool, 'spindle', report={'state': 'Idle', 'wpos': (90.0, 60.0, 0.0)}))
        self.assertFalse(tool.camera_calibration.has_calibration)

class _FakeCameraDevice:
    def __init__(self, ident, description):
        self._ident = ident
        self._description = description

    def id(self):
        return self._ident

    def description(self):
        return self._description


def camera_app(units='MM'):
    app = MagicMock()
    app.decimals = 4
    app.app_units = units
    app.options = dict(AppDefaults.factory_defaults)
    return app


class TestCameraAvailabilityFeedback(unittest.TestCase):

    def test_no_camera_disables_preview_and_says_so(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs', return_value=[]):
            widget = SenderCameraUI(camera_app())
        self.assertFalse(widget.preview_button.isEnabled())
        self.assertIn('No camera', widget.status_label.text())

    def test_camera_identity_is_the_stable_id_and_a_refresh_keeps_it(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        front = _FakeCameraDevice(b'\x01\x02', 'Front camera')
        rear = _FakeCameraDevice(b'\x03\x04', 'Rear camera')
        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                   return_value=[front, rear]):
            widget = SenderCameraUI(camera_app())
            self.assertEqual(widget.camera_combo.currentText(), 'Front camera')
            self.assertEqual(widget.current_camera_id(), '0102')
            widget.camera_combo.setCurrentIndex(1)
            self.assertEqual(widget.current_camera_id(), '0304')
            widget.refresh_cameras()
            self.assertEqual(widget.current_camera_id(), '0304')

    def test_tolerances_are_shown_in_application_units_and_persisted(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        app = camera_app(units='IN')
        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs', return_value=[]):
            widget = SenderCameraUI(app)
        widget.restore(dict(AppDefaults.factory_defaults))
        self.assertAlmostEqual(widget.verify_tolerance(), 0.05 / 25.4, places=3)
        self.assertAlmostEqual(widget.view_tolerance(), 0.5 / 25.4, places=3)
        widget.remember_settings()
        self.assertEqual(app.options['tools_camera_tolerance_units'], 'IN')
        self.assertAlmostEqual(app.options['tools_camera_verify_tolerance'], 0.05 / 25.4, places=3)

    def test_a_malformed_stored_rotation_is_reset_instead_of_crashing(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs', return_value=[]):
            widget = SenderCameraUI(camera_app())
        for value in (float('inf'), float('nan'), 'nan', 'not-a-number', None):
            with self.subTest(value=value):
                widget.restore({'tools_camera_rotation': value})
                self.assertEqual(widget.rotation(), 0)

    def test_a_failed_start_reports_and_clears_the_preview(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs', return_value=[]):
            widget = SenderCameraUI(camera_app())
        widget._devices = [MagicMock()]
        widget.camera_combo.addItem('camera')
        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QCamera',
                   side_effect=RuntimeError('no device')):
            self.assertFalse(widget.start_preview())
        self.assertIsNone(widget.camera)
        self.assertIsNone(widget.session)
        self.assertFalse(widget.preview_usable())
        self.assertFalse(widget.preview_button.isChecked())
        self.assertIn('no device', widget.app.inform.emit.call_args.args[0])

        class Camera(QtCore.QObject):
            errorOccurred = QtCore.pyqtSignal(object, str)

            def start(self):
                self.errorOccurred.emit(None, 'sync start failed')

            def stop(self):
                pass

        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QCamera', return_value=Camera()), \
                patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaCaptureSession'):
            self.assertFalse(widget.start_preview())
        self.assertIsNone(widget.camera)
        self.assertIsNone(widget.session)
        self.assertFalse(widget.preview_usable())
        self.assertFalse(widget.preview_button.isChecked())
        self.assertIn('sync start failed', widget.status_label.text())
        self.assertIn('sync start failed', widget.app.inform.emit.call_args.args[0])

class _Defaults(dict):
    """The sender calls defaults.report_usage() and reads defaults.get()."""

    def report_usage(self, name):
        pass


def make_public_app():
    from tests.test_grbl_sender_construct import FakeCollection

    app = MagicMock()
    app.collection = FakeCollection()
    for index, name in enumerate(('Gerber', 'Excellon', 'Geometry', 'CNCJob', 'Document')):
        app.collection.setItem(index, QtGui.QStandardItem(name))
    app.decimals = 4
    app.options = dict(AppDefaults.factory_defaults)
    app.defaults = _Defaults(app.options)
    app.resource_location = 'assets/resources'
    app.app_units = 'MM'
    app.plugin_tab_locked = False
    app.ui.notebook.count.return_value = 0
    app.ui.splitter.sizes.return_value = [1, 1]
    app.ui.plugin_scroll_area.widget.return_value = MagicMock()
    app.ui.plot_tab_area.indexOf.return_value = -1
    return app


class TestCameraPublicWorkflow(unittest.TestCase):
    """The public launchers run the sender in a camera-focused mode."""

    def setUp(self):
        self.tool = None

    def tearDown(self):
        if self.tool is not None:
            self.tool._grbl_port_ready = lambda: False
            self.tool.on_plugin_cleanup()
            self.tool.close()
            self.tool.deleteLater()
            QtWidgets.QApplication.processEvents()

    def make_tool(self):
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender

        app = make_public_app()
        app.ui.grbl_console_dock = QtWidgets.QDockWidget()
        self.addCleanup(app.ui.grbl_console_dock.deleteLater)
        app.options['tools_grbl_console_visible'] = True
        tool = ToolGCodeSender(app)
        tool._grbl_start_op = MagicMock()
        with patch('appPlugins.Common.levelling_core.ShapeCollection'), \
                patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs',
                      return_value=[]):
            tool.run()
        self.tool = tool
        return tool

    def assert_camera_only_surfaces(self, tool):
        from appPlugins.ToolGcodeSender.Sender import GrblConsole

        ui = tool.ui
        self.assertTrue(ui.gr_send_tab.isHidden())
        self.assertFalse(ui.al_toolbar.tabBar.isTabEnabled(ui.al_toolbar.indexOf(ui.gr_send_tab)))
        self.assertTrue(ui.run_frame.isHidden())
        self.assertTrue(ui.autolevel_frame.isHidden())
        self.assertTrue(ui.console_cb.isHidden())
        self.assertFalse(ui.spindle_button.isVisible())
        self.assertTrue(ui.zero_axs_wdg.grbl_zerox_button.isHidden())
        self.assertFalse(ui.zero_axs_wdg.grbl_homing_button.isHidden())
        self.assertTrue(ui.al_toolbar.tabBar.isTabEnabled(ui.al_toolbar.indexOf(ui.gr_ctrl_tab)))
        self.assertTrue(ui.al_toolbar.tabBar.isTabEnabled(ui.al_toolbar.indexOf(ui.camera_tab)))
        self.assertTrue(ui.al_toolbar.tabBar.isTabEnabled(ui.al_toolbar.indexOf(ui.gr_conn_tab)))
        # the one set of jog controls is in the dock's Jog page, and the raw Console stays gated
        console = tool._console()
        self.assertIsInstance(console, GrblConsole)
        self.assertIs(console.jog_page, ui.jog_page)
        for control in (ui.jog_wdg, ui.jog_step_entry, ui.jog_fr_entry):
            self.assertFalse(control.isHidden())
        self.assertTrue(console.jog_page.isAncestorOf(ui.jog_wdg))
        self.assertTrue(console.jog_page.isAncestorOf(ui.jog_step_entry))
        self.assertTrue(console.jog_page.isAncestorOf(ui.jog_fr_entry))
        self.assertTrue(console.tabs.tabBar().isTabEnabled(0))
        self.assertFalse(console.tabs.tabBar().isTabEnabled(console.tabs.indexOf(console.console_tab)))
        self.assertTrue(ui.jog_step_entry.isEnabled())
        self.assertTrue(ui.jog_fr_entry.isEnabled())
        self.assertTrue(ui.gr_ctrl_tab.isAncestorOf(ui.zero_axs_wdg))
        self.assertTrue(ui.camera_tab.isAncestorOf(ui.registration_widget.stop_button))

    def test_opening_the_camera_workflow_needs_no_cncjob_and_reaches_camera_and_jog(self):
        tool = self.make_tool()
        self.assertIsNotNone(tool.ui.camera_widget)
        self.assertEqual(tool.ui.object_combo.count(), 0)
        self.assertEqual(tool.job_lines, [])
        self.assertIsNone(tool.view3d)
        self.assertEqual(tool.app.options['tools_grbl_console_visible'], True)
        self.tool._grbl_start_op.assert_not_called()

        tool._grbl_set_ui_connected(True)
        self.assert_camera_only_surfaces(tool)

    def test_real_sender_widget_signal_roundtrip_capture_apply_and_cancel(self):
        from appHandlers.appHistory import HistoryManager
        from appObjects.GeometryObject import GeometryObject
        from tests.helpers_objects import make_app
        from shapely.geometry import LineString
        from shapely import affinity

        tool = self.make_tool()
        tool._grbl_connected = True
        tool._grbl_port_ready = MagicMock(return_value=True)
        tool._grbl_report_inches = False
        tool._camera_session_id = 1
        tool.camera_calibration = calibrated()
        tool.camera_calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)
        camera = tool.ui.camera_widget
        camera.current_camera_id = lambda: 'cam-A'
        camera.preview_usable = lambda: True
        obj = GeometryObject('board', make_app())
        obj.solid_geometry = [LineString([(0, 0), (10, 0)])]
        obj.plot = MagicMock()
        obj.plot_transformed = MagicMock()
        tool.app.collection.get_list = lambda: [obj]
        tool.app.headless = False
        tool.app.call_source = 'app'
        tool.app.projects = None
        tool.app.history = HistoryManager(tool.app, collection=tool.app.collection)
        ui = tool.ui.registration_widget
        ui.registration.set_objects([obj])
        ui.set_connected(True)
        before = obj.solid_geometry[0]
        jobs = []
        tool._status_poll_timer.stop()
        tool._grbl_start_op = lambda kind, task: jobs.append(task) or True
        tool._grbl_query_status = MagicMock()

        def complete(line):
            tool._grbl_query_status.return_value = line
            task = jobs.pop(0)
            worker = threading.Thread(target=task, args=(
                SimpleNamespace(cancel=threading.Event(), failed=False),))
            worker.start()
            worker.join(timeout=2.0)
            self.assertFalse(worker.is_alive())
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.processEvents()

        # Immediate refusal travels through the real connected signals too.
        ui.registration.add_design(0, 0, 'MM')
        tool._grbl_port_ready.return_value = False
        ui.on_capture()
        self.assertTrue(ui.use_selected_button.isEnabled())
        self.assertEqual(ui.registration.measured, [])
        tool._grbl_port_ready.return_value = True
        for design, machine, expected in (
                ((0, 0), (-17, 34), (3.0, 4.0)),
                ((10, 0), (-17, 44), (3.0, 14.0))):
            if ui.registration.pending_design is None:
                ui.registration.add_design(*design, 'MM')
            ui.on_capture()
            self.assertFalse(ui.use_selected_button.isEnabled())
            complete('<Idle|MPos:%s,%s,-5|WCO:10,20,0>' % machine)
            self.assertTrue(ui.use_selected_button.isEnabled())
            self.assertEqual(ui.registration.measured[-1], expected)
        ui.on_preview()
        self.assertIsNotNone(ui.fit)
        ui.on_apply()
        ui.on_cancel()
        complete('<Idle|MPos:-17,44,-5|WCO:10,20,0>')
        self.assertTrue(obj.solid_geometry[0].equals_exact(before, 1e-8))
        self.assertFalse(tool.app.history.can_undo())
        ui.on_preview()
        tool._grbl_port_ready.return_value = False
        ui.on_apply()
        self.assertTrue(ui.use_selected_button.isEnabled())
        self.assertFalse(tool.app.history.can_undo())
        tool._grbl_port_ready.return_value = True
        ui.on_apply()
        complete('<Idle|MPos:-17,44,-5|WCO:10,20,0>')
        self.assertTrue(ui.use_selected_button.isEnabled())
        self.assertIsNone(ui.fit)
        self.assertEqual(len(tool.app.history.undo_stack), 1, ui.result_label.text())
        expected = affinity.affine_transform(before, [0, -1, 1, 0, 3, 4])
        self.assertTrue(obj.solid_geometry[0].equals_exact(expected, 1e-8))
        ui.on_apply()
        self.assertEqual(jobs, [])
        self.assertTrue(tool.app.history.undo())
        self.assertTrue(obj.solid_geometry[0].equals_exact(before, 1e-8))
        self.assertTrue(tool.app.history.redo())
        self.assertTrue(obj.solid_geometry[0].equals_exact(expected, 1e-8))
        # A cancelled capture cannot pair a later design point.
        ui.registration.add_design(0, 0, 'MM')
        ui.on_capture()
        ui.on_restart()
        ui.registration.add_design(5, 5, 'MM')
        complete('<Idle|MPos:-17,44,-5|WCO:10,20,0>')
        self.assertEqual(ui.registration.measured, [])
        self.assertEqual(ui.registration.pending_design, (5.0, 5.0))
        ui.on_capture()
        tool.app.app_units = 'IN'
        complete('<Idle|MPos:-17,44,-5|WCO:10,20,0>')
        self.assertTrue(ui.use_selected_button.isEnabled())
        self.assertEqual(ui.registration.measured, [])
        self.assertIsNone(ui.registration.pending_design)
        self.assertFalse(tool.camera_calibration.verified)


class TestCameraRegistration(unittest.TestCase):
    """The board registration model and its strict work-frame contract."""

    @staticmethod
    def observation(x, y, **changes):
        data = {
            'ok': True, 'frame': 'work', 'x': x, 'y': y, 'wco': (10.0, 20.0),
            'session': 1, 'units': 'MM', 'camera_id': 'cam-A', 'rotation': 0, 'mirror': False,
            'offset': (30.0, -10.0), 'view_z': -5.0, 'coarse_tolerance': 0.05,
        }
        data.update(changes)
        return data

    @staticmethod
    def rotated(x, y):
        import math
        radians = math.radians(30.0)
        return (math.cos(radians) * x - math.sin(radians) * y + 5.0,
                math.sin(radians) * x + math.cos(radians) * y - 3.0)

    def make_model(self):
        from appPlugins.ToolGcodeSender.CameraRegistration import CameraRegistration
        return CameraRegistration()

    def test_fit_recovers_rotation_and_translation_with_a_signed_offset_and_wco(self):
        import math

        calibration = calibrated()
        self.assertTrue(calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)[0])
        wco = (10.0, 20.0)
        model = self.make_model()

        for x, y in ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)):
            measured = self.rotated(x, y)
            # camera position that puts the spindle at the measured work point
            camera = (measured[0] + wco[0] - 30.0, measured[1] + wco[1] + 10.0)
            observation = calibration.observe(
                camera, -5.0, 'MM', 1, 'cam-A', 0, False, wco=wco)
            self.assertTrue(observation['ok'])
            self.assertAlmostEqual(observation['x'], measured[0])
            self.assertAlmostEqual(observation['y'], measured[1])
            model.add_design(x, y)
            self.assertEqual(model.add_observation(observation), (True, 'ok'))

        fit = model.fit(1e-6)
        a, b, d, e, xoff, yoff = fit.matrix
        self.assertAlmostEqual(math.degrees(math.atan2(d, a)), 30.0, places=6)
        self.assertAlmostEqual(xoff, 5.0, places=6)
        self.assertAlmostEqual(yoff, -3.0, places=6)
        self.assertAlmostEqual(math.hypot(a, d), 1.0, places=9)
        self.assertAlmostEqual(math.hypot(b, e), 1.0, places=9)
        self.assertAlmostEqual(a * e - b * d, 1.0, places=9)
        self.assertLess(fit.max_error, 1e-9)

    def test_a_residual_above_the_acceptance_tolerance_is_refused(self):
        from appPlugins.ToolGcodeSender.CameraRegistration import RegistrationError

        model = self.make_model()
        model.add_design(0.0, 0.0)
        model.add_observation(self.observation(0.0, 0.0))
        model.add_design(10.0, 0.0)
        model.add_observation(self.observation(10.5, 0.0))
        with self.assertRaises(RegistrationError):
            model.fit(0.05)

    def test_a_session_or_calibration_change_invalidates_the_pending_registration(self):
        for changes, reason in (
                ({'session': 2}, 'session_changed'),
                ({'camera_id': 'cam-B'}, 'camera_changed'),
                ({'units': 'IN'}, 'units_changed'),
                ({'offset': (31.0, -10.0)}, 'calibration_changed')):
            with self.subTest(reason=reason):
                model = self.make_model()
                model.add_design(0.0, 0.0)
                self.assertEqual(model.add_observation(self.observation(0.0, 0.0)), (True, 'ok'))
                model.add_design(10.0, 0.0)
                self.assertEqual(model.add_observation(self.observation(10.0, 0.0, **changes)),
                                 (False, reason))
                self.assertEqual(model.measured, [])

    def test_the_observation_signal_derives_the_offset_from_mpos_and_wpos(self):
        tool = make_camera_tool()
        tool._on_camera_capture(payload(tool, 'spindle'))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'mpos': (70.0, 90.0, -5.0)}))
        tool.camera_calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)

        report = {'state': 'Idle', 'mpos': (70.0, 90.0, -5.0), 'wpos': (60.0, 70.0, -5.0)}
        tool._on_camera_capture(payload(tool, 'observe', report=report))
        result = tool.camera_observation_sig.emit.call_args.args[0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['frame'], 'work')
        self.assertEqual(result['wco'], (10.0, 20.0))
        self.assertAlmostEqual(result['x'], 90.0)
        self.assertAlmostEqual(result['y'], 60.0)

    def test_an_observation_without_a_current_work_offset_is_refused(self):
        tool = make_camera_tool()
        tool._on_camera_capture(payload(tool, 'spindle'))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'mpos': (70.0, 90.0, -5.0)}))
        tool.camera_calibration.verify((70.0, 90.0), -5.0, 'MM', 1, 'cam-A', 0, False)
        tool._on_camera_capture(payload(
            tool, 'observe', report={'state': 'Idle', 'mpos': (70.0, 90.0, -5.0)}))
        result = tool.camera_observation_sig.emit.call_args.args[0]
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'no_work_offset')

    def test_duplicate_or_non_finite_points_and_tolerances_are_rejected(self):
        from appPlugins.ToolGcodeSender.CameraRegistration import RegistrationError

        model = self.make_model()
        with self.assertRaises(RegistrationError):
            model.add_design(float('nan'), 0.0)
        model.add_design(0.0, 0.0)
        with self.assertRaises(RegistrationError):
            model.add_design(0.0, 0.0)
        with self.assertRaises(RegistrationError):
            model.add_design(1.0, 0.0)  # a second design needs the pending camera point first
        self.assertEqual(model.add_observation(self.observation(0.0, 0.0)), (True, 'ok'))
        model.add_design(5.0, 0.0)
        self.assertEqual(
            model.add_observation(self.observation(0.0, 0.0)), (False, 'duplicate_point'))
        for limit in (0.0, -1.0, float('nan'), float('inf')):
            with self.subTest(limit=limit):
                with self.assertRaises(RegistrationError):
                    model.fit(limit)

    def test_apply_context_is_refused_after_the_reference_changed(self):
        model = self.make_model()
        model.add_design(0.0, 0.0)
        model.add_observation(self.observation(0.0, 0.0))
        model.add_design(10.0, 0.0)
        model.add_observation(self.observation(10.0, 0.0))
        self.assertIsNotNone(model.fit(0.05))
        self.assertEqual(model.check_context(self.observation(5.0, 5.0)), (True, 'ok'))
        for changes, reason in (
                ({'wco': (11.0, 20.0)}, 'work_offset_changed'),
                ({'session': 2}, 'session_changed'),
                ({'offset': (31.0, -10.0)}, 'calibration_changed'),
                ({'frame': 'machine'}, 'work_frame_required')):
            with self.subTest(reason=reason):
                self.assertEqual(model.check_context(self.observation(5.0, 5.0, **changes)),
                                 (False, reason))

    def test_preview_and_cancel_leave_the_objects_and_history_unchanged(self):
        from unittest.mock import MagicMock
        from appPlugins.ToolGcodeSender.SenderRegistrationUI import SenderRegistrationUI
        from tests.helpers_objects import make_app, make_gerber

        app = make_app()
        app.plotcanvas = MagicMock()
        app.on_mouse_click_release_over_plot = MagicMock()
        app.ui.grid_snap_btn.isChecked.return_value = False
        obj = make_gerber(app)
        before = obj.bounds()
        ui = SenderRegistrationUI(app)
        ui.registration.set_objects([obj])
        ui.registration.add_design(0.0, 0.0)
        ui.registration.add_observation(self.observation(3.0, 4.0))
        ui.registration.add_design(10.0, 0.0)
        ui.registration.add_observation(self.observation(3.0, 14.0))
        ui.on_preview()
        self.assertIsNotNone(ui.fit)
        self.assertEqual(obj.bounds(), before)
        ui.on_cancel()
        self.assertIsNone(ui.fit)
        self.assertEqual(obj.bounds(), before)
        app.history.record_matrix.assert_not_called()

class TestCameraRegistrationWorkflow(unittest.TestCase):
    """The board registration tab correlates every async answer with the state that asked for it."""

    def test_backspace_requires_confirmation_before_removing_a_pair(self):
        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        event = SimpleNamespace(key='Backspace')
        with patch('appPlugins.ToolGcodeSender.SenderRegistrationUI.QtWidgets.QMessageBox.question',
                   return_value=QtWidgets.QMessageBox.StandardButton.No) as confirm:
            ui._on_key_press(event)
            confirm.assert_called_once()
        self.assertEqual(len(ui.registration.design), 2)
        with patch('appPlugins.ToolGcodeSender.SenderRegistrationUI.QtWidgets.QMessageBox.question',
                   return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            ui._on_key_press(event)
        self.assertEqual(len(ui.registration.design), 1)

    @staticmethod
    def observation(x, y, **changes):
        data = {
            'ok': True, 'frame': 'work', 'x': x, 'y': y, 'wco': (10.0, 20.0),
            'session': 1, 'units': 'MM', 'camera_id': 'cam-A', 'rotation': 0, 'mirror': False,
            'offset': (30.0, -10.0), 'view_z': -5.0, 'coarse_tolerance': 0.05,
        }
        data.update(changes)
        return data

    def make_ui(self, units='MM'):
        from unittest.mock import MagicMock
        from appPlugins.ToolGcodeSender.SenderRegistrationUI import SenderRegistrationUI
        from tests.helpers_objects import make_app, make_gerber

        app = make_app()
        app.app_units = units
        app.plotcanvas = MagicMock()
        app.on_mouse_click_release_over_plot = MagicMock()
        app.ui.grid_snap_btn.isChecked.return_value = False
        obj = make_gerber(app)
        app.collection.get_selected.return_value = [obj]
        ui = SenderRegistrationUI(app)
        ui.on_use_selected()
        return app, ui, obj

    def add_two_pairs(self, ui):
        for design, measured in (((0.0, 0.0), (3.0, 4.0)), ((10.0, 0.0), (3.0, 14.0))):
            ui.registration.add_design(design[0], design[1], 'MM')
            self.assertEqual(ui.registration.add_observation(
                self.observation(measured[0], measured[1])), (True, 'ok'))

    def test_a_changed_selected_object_invalidates_the_pending_registration(self):
        _app, ui, obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.on_preview()
        self.assertIsNotNone(ui.fit)
        ui._on_object_changed(obj)
        self.assertIsNone(ui.fit)
        self.assertEqual(ui.registration.design, [])
        self.assertEqual(ui.registration.measured, [])

    def test_a_live_project_unit_change_invalidates_instead_of_mixing_points(self):
        app, ui, _obj = self.make_ui('MM')
        self.add_two_pairs(ui)
        ui.on_preview()
        app.app_units = 'IN'
        ui._sync_units()
        self.assertEqual(ui.units, 'IN')
        self.assertIsNone(ui.fit)
        self.assertEqual(ui.registration.design, [])
        self.assertEqual(ui.registration.measured, [])

    def test_a_draw_failure_leaves_no_partial_preview(self):
        from unittest.mock import MagicMock

        _app, ui, obj = self.make_ui()
        self.add_two_pairs(ui)
        original = list(obj.solid_geometry)
        bad = MagicMock()
        bad.add.side_effect = RuntimeError('boom')
        ui.app.plotcanvas.new_shape_collection.return_value = bad
        ui.on_preview()
        self.assertTrue(bad.clear.called)
        self.assertFalse(ui.apply_button.isEnabled())
        self.assertTrue(all(a.equals_exact(b, 1e-8) for a, b in zip(obj.solid_geometry, original)))

    def test_apply_is_refused_when_a_chosen_object_disappeared(self):
        _app, ui, obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.on_preview()
        obj.deleted = True
        requested = []
        ui.validate_sig.connect(lambda: requested.append(True))
        ui.on_apply()
        self.assertEqual(requested, [])
        self.assertIsNone(ui.fit)
        self.assertTrue(ui.use_selected_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
