"""Finite 3D workspace and camera interaction checks.

Run: .venv/Scripts/python.exe tests/test_plotcanvas3d.py
"""

import os
import sys
import unittest
from itertools import product
from types import SimpleNamespace

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtCore, QtWidgets
from vispy import app
from vispy.util import keys

app.use_app('pyqt6')

from appGUI.PlotCanvas3d import PlotCanvas3d
from appGUI.PlotCanvas import PlotCanvas


class CanvasApp(QtCore.QObject):
    def __init__(self, units='MM', custom_cursor=True):
        super().__init__()
        self.ui = QtWidgets.QWidget()
        self.pool = None
        self.app_units = units
        self.options = {
            'global_cursor_color_enabled': custom_cursor,
            'global_cursor_color': '#FF0000',
        }


class TestPlotCanvas3d(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from appGUI.VisPyPatches import apply_patches
        apply_patches()
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_plot_shapes_survive_detach_and_reattach(self):
        from shapely.geometry import Polygon
        from appGUI.GUIElements import FCDetachableTab
        from appGUI.VisPyVisuals import ShapeCollection

        for canvas_type, title in ((PlotCanvas, 'Plot Area'), (PlotCanvas3d, 'Plot 3D Area')):
            with self.subTest(title=title):
                fcapp = CanvasApp()
                fcapp.options['global_axis_color'] = '#FF0000'
                fcapp.cursor_color_3D = '#FF0000'
                tabs = FCDetachableTab()
                self.addCleanup(tabs.deleteLater)
                self.addCleanup(tabs.close)
                layout = QtWidgets.QVBoxLayout(fcapp.ui)
                if canvas_type is PlotCanvas:
                    canvas = canvas_type(fcapp)
                    layout.addWidget(canvas.native)
                    canvas.view.camera.rect = (0, 0, 400, 400)
                else:
                    canvas = canvas_type(layout, fcapp)
                self.addCleanup(canvas.close)
                self.addCleanup(tabs.closeDetachedTabs)
                tabs.addTab(fcapp.ui, title)
                tabs.resize(800, 600)
                tabs.show()
                self.qapp.processEvents()
                shapes = ShapeCollection(parent=canvas.view.scene)

                def blue_pixels():
                    image = canvas.render()[:, :, :3].astype(int)
                    return np.count_nonzero((image[:, :, 2] - image[:, :, 0] > 150) &
                                            (image[:, :, 2] - image[:, :, 1] > 150))

                shapes.add(shape=Polygon([(50, 50), (150, 50), (150, 150), (50, 150)]),
                           color='blue', face_color='blue', update=True)
                self.assertGreater(blue_pixels(), 100)
                tabs.detachTab(tabs.indexOf(fcapp.ui), QtCore.QPoint(100, 100))
                self.qapp.processEvents()
                self.assertGreater(blue_pixels(), 100, 'Existing shapes disappeared after detaching')
                shapes.clear(update=True)
                self.assertEqual(blue_pixels(), 0)
                shapes.add(shape=Polygon([(200, 200), (300, 200), (300, 300), (200, 300)]),
                           color='blue', face_color='blue', update=True)
                self.assertGreater(blue_pixels(), 100, 'New shapes did not render in detached window')
                tabs.closeDetachedTabs()
                self.qapp.processEvents()
                self.assertGreater(blue_pixels(), 100, 'Shapes disappeared after reattaching')

    def make_canvas(self, units='MM', custom_cursor=True, camera_reference=None):
        fcapp = CanvasApp(units, custom_cursor)
        layout = QtWidgets.QVBoxLayout(fcapp.ui)
        options = {} if camera_reference is None else {'camera_reference': camera_reference}
        canvas = PlotCanvas3d(layout, fcapp, **options)
        self.addCleanup(fcapp.ui.deleteLater)
        self.addCleanup(canvas.close)
        canvas.size = (800, 600)
        canvas.central_widget.size = (800, 600)
        canvas.view.size = (800, 600)
        canvas.fit_view()
        return canvas

    @staticmethod
    def mouse_event(kind, pos=(400, 300), start=None, buttons=(), delta=(0, 0), modifiers=()):
        pos = np.array(pos, dtype=float)
        press = None if start is None else SimpleNamespace(pos=np.array(start, dtype=float))
        mouse = SimpleNamespace(pos=pos, press_event=press, modifiers=modifiers)
        return SimpleNamespace(
            type=kind, pos=pos, mouse_event=mouse, press_event=press,
            buttons=buttons, delta=delta, handled=False,
        )

    def test_fit_toolpath_bounds_with_canvas_padding(self):
        canvas = self.make_canvas()
        points = np.array(list(product((1000, 1020), (2000, 2010), (-3, 7))))
        canvas.view.camera.set_view('iso')
        canvas.fit_view(points=points, margin=0.1)
        projected = canvas.view.camera._scene_transform.map(points)
        screen = projected[:, :2] / projected[:, 3:]
        width, height = canvas.view.size
        self.assertGreaterEqual(screen[:, 0].min(), width * 0.1 - 1e-3)
        self.assertLessEqual(screen[:, 0].max(), width * 0.9 + 1e-3)
        self.assertGreaterEqual(screen[:, 1].min(), height * 0.1 - 1e-3)
        self.assertLessEqual(screen[:, 1].max(), height * 0.9 + 1e-3)
        self.assertTrue(np.any(np.isclose(np.ptp(screen, axis=0), (width * 0.8, height * 0.8))))

    def test_rectangular_grid_updates_both_axes_and_fits(self):
        for units, scale in (('MM', 1), ('IN', 1 / 25.4)):
            with self.subTest(units=units):
                canvas = self.make_canvas(units=units)
                canvas.set_grid_size(300, 150)
                np.testing.assert_allclose(canvas.get_grid_size(), (300, 150))
                np.testing.assert_allclose(canvas.grid.grid_bounds, (0, 300 * scale, 0, 150 * scale))
                np.testing.assert_allclose(canvas.workspace_line.bounds(0), (0, 300 * scale))
                np.testing.assert_allclose(canvas.workspace_line.bounds(1), (0, 150 * scale))
                np.testing.assert_allclose(canvas.xaxis.domain, (0, 300 * scale))
                np.testing.assert_allclose(canvas.yaxis.domain, (0, 150 * scale))
                points = canvas.view.camera._scene_transform.map(np.array([
                    (0, 0, 0), (300 * scale, 0, 0), (0, 150 * scale, 0), (300 * scale, 150 * scale, 0)
                ]))
                screen = points[:, :2] / points[:, 3:]
                self.assertGreaterEqual(screen[:, 0].min(), canvas.axis_padding - 1e-3)
                self.assertGreaterEqual(screen[:, 1].min(), 20 - 1e-3)
                self.assertLessEqual(screen[:, 0].max(), 800 - 20 + 1e-3)
                self.assertLessEqual(screen[:, 1].max(), 600 - canvas.axis_padding + 1e-3)
                canvas.set_grid_size(250)
                np.testing.assert_allclose(canvas.get_grid_size(), (250, 250))

    def test_wheel_matches_2d_and_keeps_the_cursor_anchor(self):
        canvas = self.make_canvas()
        camera = canvas.view.camera
        scale = camera.scale_factor
        center = camera.center
        point = np.array((250, 150, 0, 1), dtype=float)
        projected = camera._scene_transform.map(point)
        pos = projected[:2] / projected[3]
        event = self.mouse_event('mouse_wheel', pos=pos, delta=(0, 1))
        camera.viewbox_mouse_event(event)
        self.assertAlmostEqual(camera.scale_factor / scale, (1 + camera.zoom_factor) ** -30)
        after = camera._scene_transform.map(point)
        np.testing.assert_allclose(after[:2] / after[3], pos, atol=1e-5)
        self.assertTrue(event.handled)
        camera.viewbox_mouse_event(self.mouse_event('mouse_wheel', pos=pos, delta=(0, -1)))
        self.assertAlmostEqual(camera.scale_factor, scale)
        np.testing.assert_allclose(camera.center, center, atol=1e-5)
        # Shift makes the wheel step 10 times finer; some platforms send the Shift wheel as a horizontal delta
        for delta in ((0, 1), (1, 0)):
            camera.viewbox_mouse_event(self.mouse_event('mouse_wheel', pos=pos, delta=delta, modifiers=(keys.SHIFT,)))
            self.assertAlmostEqual(camera.scale_factor / scale, (1 + camera.zoom_factor) ** -3)
            after = camera._scene_transform.map(point)
            np.testing.assert_allclose(after[:2] / after[3], pos, atol=1e-5)
            camera.viewbox_mouse_event(self.mouse_event('mouse_wheel', pos=pos, delta=(0, -1), modifiers=(keys.SHIFT,)))
            self.assertAlmostEqual(camera.scale_factor, scale)


    def test_drag_tilts_in_screen_direction_from_any_start(self):
        canvas = self.make_canvas()
        camera = canvas.view.camera
        initial = camera.get_state()
        points = np.array([(200, 200, 0, 1), (200, 200, 40, 1)])
        for start in ((100, 100), (400, 300), (700, 500)):
            for dx, dy in ((-40, 0), (40, 0), (0, -40), (0, 40)):
                with self.subTest(start=start, delta=(dx, dy)):
                    camera.set_state(initial)
                    camera.viewbox_mouse_event(self.mouse_event('mouse_release'))
                    camera.viewbox_mouse_event(self.mouse_event(
                        'mouse_move', (start[0] + dx, start[1] + dy), start, (1,)
                    ))
                    projected = camera._scene_transform.map(points)
                    normal = projected[1, :2] / projected[1, 3] - projected[0, :2] / projected[0, 3]
                    self.assertGreater(np.dot(normal, (dx, dy)), 0)
                    self.assertAlmostEqual(normal[1 if dx else 0], 0, places=5)

    def test_center_reference_rotates_about_loaded_object_bounds_and_can_change(self):
        canvas = self.make_canvas(camera_reference='center')
        camera = canvas.view.camera
        bounds = [1000, 2000, 1200, 2100]
        canvas.fcapp.collection = SimpleNamespace(get_bounds=lambda: bounds)
        canvas.content_bounds = ((1000, 2000, -2), (1020, 2010, 4))
        camera.set_view('iso')
        canvas.fit_view()
        camera.center = np.array(camera.center) + (40, -20, 0)
        for reference in ('center', 'lmb', 'center'):
            with self.subTest(reference=reference):
                canvas.camera_reference = reference
                pivot = np.array(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, 1, 1))
                before = camera._scene_transform.map(pivot)
                camera.viewbox_mouse_event(self.mouse_event('mouse_move', (160, 140), (100, 100), (1,)))
                after = camera._scene_transform.map(pivot)
                before, after = before[:2] / before[3], after[:2] / after[3]
                if reference == 'center':
                    np.testing.assert_allclose(after, before, atol=1e-5)
                    canvas.view_cube.rotation_requested.emit(0.1, 0.15)
                    after = camera._scene_transform.map(pivot)
                    np.testing.assert_allclose(after[:2] / after[3], before, atol=1e-5)
                else:
                    self.assertGreater(np.linalg.norm(after - before), 1)
                camera.viewbox_mouse_event(self.mouse_event('mouse_release'))
                bounds[2] += 100
        canvas.content_bounds = None
        camera.center = np.array(camera.center) + (0, 0, 99)
        pivot = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, 0, 1)
        before = camera._scene_transform.map(pivot)
        camera.viewbox_mouse_event(self.mouse_event('mouse_move', (160, 140), (100, 100), (1,)))
        after = camera._scene_transform.map(pivot)
        np.testing.assert_allclose(after[:2] / after[3], before[:2] / before[3], atol=1e-5)

if __name__ == '__main__':
    unittest.main()
