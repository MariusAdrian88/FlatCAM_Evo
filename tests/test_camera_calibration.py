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

        # a camera capture needs the spindle reference of the same session
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

        # the viewing Z tolerance decides the observation
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

        # saved values round-trip and clear
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

        # report inches are converted to application units
        tool = make_camera_tool()
        tool._grbl_report_inches = True
        tool._on_camera_capture(payload(
            tool, 'spindle', report={'state': 'Idle', 'mpos': (4.0, 3.0, 0.0)}, report_inches=True))
        tool._on_camera_capture(payload(
            tool, 'camera', report={'state': 'Idle', 'mpos': (2.0, 4.0, -0.2)}, report_inches=True))
        self.assertAlmostEqual(tool.camera_calibration.offset[0], 50.8)
        self.assertAlmostEqual(tool.camera_calibration.offset[1], -25.4)

        # a WPos-only report adds the work offset
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

    def test_a_live_unit_change_converts_the_tolerances_for_the_controller(self):
        from appPlugins.ToolGcodeSender.SenderCameraUI import SenderCameraUI

        app = camera_app(units='MM')
        with patch('appPlugins.ToolGcodeSender.SenderCameraUI.QMediaDevices.videoInputs', return_value=[]):
            widget = SenderCameraUI(app)
        changed = []
        widget.tolerance_changed_sig.connect(lambda: changed.append(1))
        self.assertAlmostEqual(widget.verify_tolerance(), 0.05, places=3)
        app.app_units = 'IN'
        self.assertAlmostEqual(widget.verify_tolerance(), 0.05 / 25.4, places=3)
        self.assertAlmostEqual(widget.view_tolerance(), 0.5 / 25.4, places=3)
        self.assertEqual(len(changed), 1)
        self.assertIn('(IN)', widget.verify_tolerance_label.text())
        widget.remember_settings()
        self.assertEqual(app.options['tools_camera_tolerance_units'], 'IN')

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
        obj.tools[1]['solid_geometry'] = [LineString([(0, 0), (10, 0)])]
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

        # residuals above the warning limit are still reported, never refused
        model = self.make_model()
        model.add_design(0.0, 0.0)
        model.add_observation(self.observation(0.0, 0.0))
        model.add_design(10.0, 0.0)
        model.add_observation(self.observation(10.0, 0.0))
        model.add_design(0.0, 10.0)
        model.add_observation(self.observation(0.0, 10.5))
        fit = model.fit(0.05)
        self.assertIsNotNone(fit)
        self.assertIs(fit, model.preview)
        self.assertGreater(fit.max_error, 0.05)
        self.assertEqual(len(fit.errors), 3)
        self.assertEqual(fit.min_error, min(fit.errors))
        self.assertEqual(fit.max_error, max(fit.errors))
        self.assertAlmostEqual(fit.mean_error, sum(fit.errors) / len(fit.errors))

    def test_residuals_and_translation_are_in_the_project_units(self):
        model = self.make_model()
        for design, measured in (
                ((0.0, 0.0), (0.25, -0.10)),
                ((1.0, 0.0), (1.25, -0.10)),
                ((0.0, 1.0), (0.25, 0.92))):
            model.add_design(design[0], design[1], 'IN')
            self.assertEqual(
                model.add_observation(self.observation(measured[0], measured[1], units='IN')),
                (True, 'ok'))
        fit = model.fit(0.01)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit.translation[0], 0.25, delta=0.02)
        self.assertAlmostEqual(fit.translation[1], -0.10, delta=0.02)
        self.assertGreater(fit.max_error, 0.01)
        self.assertLess(fit.max_error, 0.02)
        self.assertLessEqual(fit.min_error, fit.mean_error)
        self.assertLessEqual(fit.mean_error, fit.max_error)

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

        # an observation without a current work offset is refused
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
        for x, y in ((float('nan'), 0.0), (0.0, float('inf'))):
            with self.subTest(x=x, y=y):
                self.assertEqual(model.add_observation(self.observation(x, y)), (False, 'invalid_position'))
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

class TestCameraRegistrationWorkflow(unittest.TestCase):
    """The board registration tab correlates every async answer with the state that asked for it."""

    def test_backspace_requires_confirmation_before_removing_a_pair(self):
        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.refresh_table()
        ui.pairs_table.clearSelection()
        last = ui.registration.design[-1]
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
        self.assertNotIn(last, ui.registration.design)

    def test_remove_pair_needs_a_selected_row(self):
        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.refresh_table()
        self.assertFalse(ui.remove_pair_button.isEnabled())
        with patch('appPlugins.ToolGcodeSender.SenderRegistrationUI.QtWidgets.QMessageBox.question',
                   return_value=QtWidgets.QMessageBox.StandardButton.Yes) as confirm:
            ui.on_remove_pair()
            confirm.assert_not_called()
        self.assertEqual(len(ui.registration.design), 2)
        ui.pairs_table.selectRow(0)
        self.assertTrue(ui.remove_pair_button.isEnabled())

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

        # a live project unit change invalidates instead of mixing points
        app, ui, _obj = self.make_ui('MM')
        self.add_two_pairs(ui)
        ui.on_preview()
        app.app_units = 'IN'
        ui._sync_units()
        self.assertEqual(ui.units, 'IN')
        self.assertIsNone(ui.fit)
        self.assertEqual(ui.registration.design, [])
        self.assertEqual(ui.registration.measured, [])

    def test_an_inconsistent_extra_point_stays_visible_before_apply(self):
        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.registration.add_design(0.0, 10.0, 'MM')
        self.assertEqual(ui.registration.add_observation(self.observation(0.0, 10.5)), (True, 'ok'))
        ui.on_preview()
        fit = ui.fit
        self.assertIsNotNone(fit)
        self.assertGreater(fit.max_error, ui.tolerance())
        self.assertTrue(ui.apply_button.isEnabled())
        residuals = [ui.pairs_table.item(row, 5).text() for row in range(ui.pairs_table.rowCount())]
        self.assertEqual(len(residuals), 3)
        self.assertTrue(all(residuals))
        text = ui.result_label.text()
        for value in (fit.min_error, fit.mean_error, fit.max_error):
            self.assertIn('%.*f' % (ui.decimals, value), text)

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

    def test_registration_apply_asks_above_tolerance(self):
        box = 'appPlugins.ToolGcodeSender.SenderRegistrationUI.QtWidgets.QMessageBox.question'
        yes, no = QtWidgets.QMessageBox.StandardButton.Yes, QtWidgets.QMessageBox.StandardButton.No

        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.registration.add_design(0.0, 10.0, 'MM')
        self.assertEqual(ui.registration.add_observation(self.observation(0.0, 10.5)), (True, 'ok'))
        ui.on_preview()
        self.assertGreater(ui.fit.max_error, ui.tolerance())
        requested = []
        ui.validate_sig.connect(lambda: requested.append(True))
        with patch(box, return_value=no) as ask:
            ui.on_apply()
            ask.assert_called_once()
            text = ask.call_args.args[2]
            self.assertIn(ui._format_limit(ui.fit.max_error), text)
            self.assertIn(ui._format_limit(ui.fit.tolerance), text)
        self.assertEqual(requested, [])
        with patch(box, return_value=yes) as ask:
            ui.on_apply()
            ask.assert_called_once()
        self.assertEqual(requested, [True])

        _app, ui, _obj = self.make_ui()
        self.add_two_pairs(ui)
        ui.on_preview()
        self.assertLessEqual(ui.fit.max_error, ui.tolerance())
        requested = []
        ui.validate_sig.connect(lambda: requested.append(True))
        with patch(box, return_value=no) as ask:
            ui.on_apply()
            ask.assert_not_called()
        self.assertEqual(requested, [True])


class TestCameraPane(unittest.TestCase):
    """The camera pane beside the plot: a splitter only while the pane is open."""

    def setUp(self):
        from appGUI.GUIElements import FCDetachableTab
        from appPlugins.ToolGcodeSender.SenderCameraPane import CameraPane

        self.host = QtWidgets.QWidget()
        self.right_lay = QtWidgets.QVBoxLayout(self.host)
        self.right_lay.addWidget(QtWidgets.QLabel('above'))
        self.area = FCDetachableTab(protect=False)
        self.plot_tab = QtWidgets.QWidget()
        self.plot_tab.setObjectName('plotarea_tab')
        self.area.addTab(self.plot_tab, 'Plot Area')
        self.right_lay.addWidget(self.area, 1)
        self.host.resize(1000, 600)
        self.host.show()
        self.page = QtWidgets.QWidget()
        self.dock_layout = QtWidgets.QVBoxLayout(self.page)
        self.dock_layout.addWidget(QtWidgets.QLabel('controls'))
        self.view = QtWidgets.QWidget(self.page)
        self.dock_layout.addWidget(self.view, 1)
        self.dock_layout.addWidget(QtWidgets.QLabel('tabs'))
        self.app = SimpleNamespace(
            options={}, ui=SimpleNamespace(plot_tab_area=self.area, plot_tab=self.plot_tab, right_lay=self.right_lay))
        self.stops = []
        self.pane = CameraPane(self.app, self.view, lambda: self.stops.append(True))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.pane.close()
        for window in list(self.area.detachedTabs.values()):
            window.close()
        self.host.close()
        self.host.deleteLater()
        QtWidgets.QApplication.processEvents()

    def test_the_splitter_exists_only_while_the_pane_is_open(self):
        self.pane.open()
        splitter = self.pane.splitter
        self.assertEqual(splitter.count(), 2)
        self.assertIs(splitter.widget(0), self.area)
        self.assertIs(self.right_lay.itemAt(1).widget(), splitter)
        self.assertEqual(self.pane.pane.tabText(0), 'Camera')
        self.assertIs(self.view.parentWidget(), self.pane.page)
        self.pane.close()
        self.assertIsNone(self.pane.splitter)
        self.assertEqual(self.right_lay.indexOf(self.area), 1)
        self.assertEqual(self.right_lay.stretch(1), 1)
        self.assertIs(self.area.parentWidget(), self.host)
        # the view is never put back into the dock: the pane keeps it, hidden
        self.assertEqual(self.dock_layout.indexOf(self.view), -1)
        self.assertFalse(self.view.isVisible())
        self.assertEqual(self.stops, [True])

    def test_the_first_open_gives_the_pane_half_of_the_width_and_it_fills_it(self):
        self.pane.open()
        QtWidgets.QApplication.processEvents()
        sizes = self.pane.splitter.sizes()
        self.assertAlmostEqual(sizes[1] / sum(sizes), 0.5, delta=0.05)
        self.assertEqual(self.pane.pane.sizePolicy().horizontalPolicy(), QtWidgets.QSizePolicy.Policy.Expanding)
        self.assertGreaterEqual(self.view.width(), sizes[1] - 40)
        self.pane.splitter.setSizes([100, 900])    # a saved width wider than the splitter is clamped
        self.pane.close()
        self.app.options['global_camera_pane_width'] = 5000
        self.pane.open()
        QtWidgets.QApplication.processEvents()
        self.assertLess(self.pane.splitter.sizes()[1], self.pane.splitter.width())
        self.assertGreater(self.pane.splitter.sizes()[0], 0)

    def test_two_opens_make_one_pane(self):
        self.pane.open()
        first = self.pane.pane
        self.pane.open()
        self.assertIs(self.pane.pane, first)
        self.assertEqual(self.right_lay.count(), 2)

    def test_the_resized_width_is_kept_for_the_next_open(self):
        self.pane.open()
        QtWidgets.QApplication.processEvents()
        self.pane.splitter.setSizes([700, 250])
        width = self.pane.splitter.sizes()[1]
        self.assertNotEqual(width, 400)
        self.pane.close()
        self.assertEqual(self.app.options['global_camera_pane_width'], width)
        self.pane.open()
        QtWidgets.QApplication.processEvents()
        self.assertAlmostEqual(self.pane.splitter.sizes()[1], width, delta=10)

    def test_a_detached_pane_has_no_splitter_and_returns_into_a_new_one(self):
        self.pane.open()
        self.pane.pane.detachTab(0, QtCore.QPoint(50, 50))
        self.assertIsNone(self.pane.splitter)
        self.assertEqual(self.right_lay.indexOf(self.area), 1)
        window = next(iter(self.pane.pane.detachedTabs.values()))
        self.assertTrue(window.isVisible())
        self.assertIs(self.view.window(), window)
        self.assertEqual(self.stops, [])    # the camera keeps running in the window
        window.close()
        self.assertIsNotNone(self.pane.splitter)
        self.assertIs(self.pane.splitter.widget(1), self.pane.pane)
        self.assertEqual(self.pane.pane.count(), 1)

    def test_closing_the_pane_while_detached_closes_its_window_and_stops_the_camera(self):
        self.pane.open()
        self.pane.pane.detachTab(0, QtCore.QPoint(50, 50))
        window = next(iter(self.pane.pane.detachedTabs.values()))
        self.pane.close()
        self.assertFalse(window.isVisible())
        self.assertIsNone(self.pane.splitter)
        self.assertEqual(self.dock_layout.indexOf(self.view), -1)
        self.assertEqual(self.stops, [True])

    def test_the_pane_tab_close_button_closes_the_pane(self):
        self.pane.open()
        self.pane.pane.tabCloseRequested.emit(0)
        self.assertFalse(self.pane.is_open())
        self.assertEqual(self.stops, [True])

    def test_a_detached_plot_tab_gets_the_splitter_in_its_window(self):
        self.area.detachTab(0, QtCore.QPoint(80, 80))
        window = next(iter(self.area.detachedTabs.values()))
        self.pane.open()
        self.assertIs(self.pane.splitter.widget(0), self.plot_tab)
        self.assertIs(window.central_layout.itemAt(0).widget(), self.pane.splitter)
        self.pane.close()
        self.assertIs(window.central_layout.itemAt(0).widget(), self.plot_tab)
        self.pane.open()
        window.close()      # the plot tab returns: the splitter is made around the tab area
        self.assertIs(self.pane.splitter.widget(0), self.area)
        self.assertIs(self.pane.splitter.widget(1), self.pane.pane)
        self.assertIs(self.plot_tab.parentWidget().parentWidget(), self.area)

    def test_the_registration_pick_works_with_the_pane_open(self):
        from appPlugins.ToolGcodeSender.SenderRegistrationUI import SenderRegistrationUI
        from tests.helpers_objects import make_app, make_gerber

        app = make_app()
        app.plotcanvas = MagicMock()
        app.on_mouse_click_release_over_plot = MagicMock()
        app.ui.grid_snap_btn.isChecked.return_value = False
        obj = make_gerber(app)
        app.collection.get_selected.return_value = [obj]
        registration = SenderRegistrationUI(app)
        registration.on_use_selected()
        self.pane.open()
        registration.start_pick()
        calls = {c.args[0]: c.args[1] for c in app.plotcanvas.graph_event_connect.call_args_list
                 if c.args[0] in ('mouse_release', 'key_press')}
        self.assertEqual(calls['mouse_release'], registration._on_canvas_click)
        app.event_is_dragging = False
        calls['mouse_release'](SimpleNamespace(button=2, pos=(0, 0)))
        self.assertFalse(registration._pick_active)


if __name__ == '__main__':
    unittest.main()
