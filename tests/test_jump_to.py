"""Run: .venv/Scripts/python.exe tests/test_jump_to.py"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from vispy import app
from vispy.geometry import Rect

app.use_app('pyqt6')

from appGUI.VisPyCanvas import VisPyCanvas
from appGUI.PlotCanvas import PlotCanvas
from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appCanvasEvents import AppCanvasEvents


class TestJumpTo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_handler(self):
        canvas = VisPyCanvas()
        self.addCleanup(canvas.close)
        canvas.size = (800, 600)
        canvas.unfreeze()
        canvas.shape_collection = Mock()
        canvas.fit_center = PlotCanvas.fit_center.__get__(canvas)
        canvas.on_update_text_hud = Mock()
        ui = QtWidgets.QWidget()
        self.addCleanup(ui.deleteLater)
        setattr(ui, 'update_location_labels', Mock())
        fcapp = SimpleNamespace(
            log=Mock(), inform=Mock(), defaults=Mock(), options={}, ui=ui,
            plotcanvas=canvas, clipboard=SimpleNamespace(text=lambda: ''), resource_location='',
            mouse_pos=[12.3456789, -8.7654321], rel_point1=(1, 2),
            jump_signal=Mock(), locate_signal=Mock(), grid_status=lambda: False,
        )
        return fcapp, AppObjectOps(fcapp)

    def test_relative_reference_is_captured_before_dialog(self):
        fcapp, handler = self.make_handler()
        start = tuple(fcapp.mouse_pos)

        def dialog(**kwargs):
            # Moving to the dialog must not move the relative reference.
            fcapp.mouse_pos[:] = [90, 80]
            return SimpleNamespace(ok=True, location='0.125, -0.25', reference='rel')

        with patch('appGUI.GUIElements.DialogBoxRadio', side_effect=dialog), \
                patch.object(QtGui.QCursor, 'setPos'):
            target = handler.on_jump_to()
        self.assertEqual(target, (start[0] + 0.125, start[1] - 0.25))

    def test_jump_click_matches_target_and_canvas(self):
        fcapp, handler = self.make_handler()
        canvas = fcapp.plotcanvas
        events = AppCanvasEvents(fcapp)
        events.on_plugin_mouse_move = Mock()
        fcapp.options['global_selection_shape'] = False
        fcapp.ui.shell_dock = Mock()
        fcapp.ui.popMenu = SimpleNamespace(mouse_is_panning=False)
        for target in ((3.4, 4.5), (15.2, 13.4)):
            with self.subTest(target=target), patch.object(QtGui.QCursor, 'setPos'):
                handler.on_jump_to(custom_location=target)
                pixel = np.round(canvas.translate_coords_2(target)[:2])
                canvas.events.mouse_move(pos=pixel)
                event = SimpleNamespace(pos=pixel, button=1)
                events.on_mouse_click_over_plot(event)
                # 0.00001 mm is 100 times finer than the requested one-micron precision.
                np.testing.assert_allclose(fcapp.mouse_click_pos, target, rtol=0, atol=1e-5)
                np.testing.assert_allclose(fcapp.mouse_pos, target, rtol=0, atol=1e-5)
                np.testing.assert_allclose(fcapp.ui.update_location_labels.call_args.args,
                                           (0.0, 0.0, *target), rtol=0, atol=1e-5)
                np.testing.assert_allclose(canvas.on_update_text_hud.call_args.args,
                                           (0.0, 0.0, *target), rtol=0, atol=1e-5)
                actual = canvas.grid.get_transform('canvas', 'visual').map(pixel)
                np.testing.assert_array_equal(canvas.translate_coords(pixel), actual)

    def test_jump_centers_target_without_changing_zoom(self):
        fcapp, handler = self.make_handler()
        canvas = fcapp.plotcanvas
        canvas.view.camera.rect = Rect(100, -80, 0.04, 0.03)
        before = canvas.view.camera.rect.size
        target = (3.2, 4.3)
        with patch.object(QtGui.QCursor, 'setPos'):
            handler.on_jump_to(custom_location=target)
        np.testing.assert_allclose(canvas.view.camera.rect.size, before, rtol=0, atol=1e-12)
        # Centering may shift less than a pixel to align the integer OS cursor.
        np.testing.assert_allclose(canvas.view.camera.rect.center, target, rtol=0,
                                   atol=canvas.pixel_size())

    def test_canvas_pixel_roundtrip_after_pan_zoom_and_resize(self):
        fcapp, _ = self.make_handler()
        canvas = fcapp.plotcanvas
        for size, rect, target in (
                ((800, 600), (-20, -20, 40, 40), (3.123456, -8.765432)),
                ((1200, 400), (120, -80, 0.4, 0.4), (120.123456, -79.765432)),
                ((400, 900), (-900, 200, 600, 600), (-743.23456, 359.12345))):
            with self.subTest(size=size, rect=rect):
                canvas.size = size
                canvas.view.camera.rect = Rect(*rect)
                screen = canvas.translate_coords_2(target)
                np.testing.assert_allclose(canvas.translate_coords(screen[:2])[:2], target, atol=1e-8)
                # The OS cursor has integer logical pixels: half a pixel is the unavoidable limit.
                pixel = np.round(screen[:2])
                np.testing.assert_array_less(np.abs(screen[:2] - pixel), 0.500001)
                np.testing.assert_allclose(canvas.translate_coords_2(canvas.translate_coords(pixel)[:2])[:2],
                                           pixel, atol=1e-5)

if __name__ == '__main__':
    unittest.main()
