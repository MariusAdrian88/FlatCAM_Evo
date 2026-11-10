"""Finite 3D workspace and camera interaction checks.

Run: .venv/Scripts/python.exe tests/test_plotcanvas3d.py
"""

import os
import sys
import unittest
from contextlib import ExitStack
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtCore, QtWidgets
from vispy import app
from vispy.util import keys
from vispy.scene.node import Node
from vispy.scene.widgets.axis import AxisWidget
from vispy.visuals.axis import AxisVisual
from vispy.visuals.transforms import MatrixTransform
from vispy.visuals.visual import BaseVisual, CompoundVisual

_axis_update_subvisuals = AxisVisual._update_subvisuals
_axis_prepare_draw = AxisVisual._prepare_draw
_matrix_set_ortho = MatrixTransform.set_ortho
# the VisPy versions of the functions that the canvas patches replace around the axes and the camera step
_vispy_axes = ((AxisWidget, '_view_changed', AxisWidget._view_changed),
               (AxisVisual, 'domain', AxisVisual.__dict__['domain']),
               (Node, 'update', Node.update), (BaseVisual, 'update', BaseVisual.update),
               (BaseVisual, '_transform_changed', BaseVisual._transform_changed),
               (CompoundVisual, '_transform_changed', CompoundVisual._transform_changed))

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

    def make_2d_canvas(self):
        from appGUI.VisPyCanvas import VisPyCanvas

        canvas = VisPyCanvas()
        self.addCleanup(canvas.close)
        canvas.size = (800, 600)
        canvas.view.camera.aspect = 1
        canvas.view.camera.rect = (0, 0, 400, 300)
        canvas.render()
        return canvas

    def mouse_event_2d(self, kind, pos=(110, 105), last=(100, 100), button=2,
                       start=(100, 100), modifiers=(), delta=(0, 0)):
        event = self.mouse_event(kind, pos=pos, start=start, modifiers=modifiers,
                                 delta=np.array(delta, dtype=float))
        event.button = button
        event.last_event = SimpleNamespace(pos=np.array(last, dtype=float))
        return event

    def test_2d_rapid_pan_moves_keep_every_displacement_for_configured_buttons(self):
        for button in (1, 2, 3):
            with self.subTest(button=button):
                canvas = self.make_2d_canvas()
                camera = canvas.view.camera
                camera.pan_button_setting = str(button)
                point = (150, 150, 0, 1)
                before = camera._scene_transform.map(point)[:2]
                size = camera.rect.size
                with patch('appGUI.VisPyCanvas.time.time', return_value=1.0) as clock:
                    camera.viewbox_mouse_event(self.mouse_event_2d('mouse_press', button=button))
                    for step in range(1, 7):
                        clock.return_value = 1.0 + step * 0.001
                        camera.viewbox_mouse_event(self.mouse_event_2d(
                            'mouse_move', pos=(100 + step * 10, 100 + step * 5),
                            last=(90 + step * 10, 95 + step * 5), button=button
                        ))
                        camera.flush_pan()
                        after = camera._scene_transform.map(point)[:2]
                        np.testing.assert_allclose(after - before, (step * 10, step * 5), atol=1e-5)
                        np.testing.assert_allclose(camera.rect.size, size)
                    for handled, interactive in ((True, True), (False, False)):
                        camera.interactive = interactive
                        event = self.mouse_event_2d('mouse_move', button=button)
                        event.handled = handled
                        camera.viewbox_mouse_event(event)
                        camera.flush_pan()
                        np.testing.assert_allclose(camera._scene_transform.map(point)[:2], after)

    def test_2d_fast_pan_ends_at_the_cursor_and_wheel_zoom_uses_the_flushed_view(self):
        canvas = self.make_2d_canvas()
        camera = canvas.view.camera
        point = (150, 150, 0, 1)
        before = camera._scene_transform.map(point)[:2]
        with patch('appGUI.VisPyCanvas.time.time', return_value=1.0) as clock:
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_press'))
            for step in range(1, 41):  # many moves, no draw between them
                camera.viewbox_mouse_event(self.mouse_event_2d(
                    'mouse_move', pos=(100 + step * 3, 100 + step * 2), last=(97 + step * 3, 98 + step * 2)))
            clock.return_value = 1.1
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_release', pos=(220, 180)))
            np.testing.assert_allclose(camera._scene_transform.map(point)[:2] - before, (120, 80), atol=1e-4)

            # a second pan, then a wheel zoom before any draw; compared with the same input flushed after every move
            reference = self.make_2d_canvas().view.camera
            reference.viewbox_mouse_event(self.mouse_event_2d('mouse_press'))
            for step in range(1, 41):
                reference.viewbox_mouse_event(self.mouse_event_2d(
                    'mouse_move', pos=(100 + step * 3, 100 + step * 2), last=(97 + step * 3, 98 + step * 2)))
                reference.flush_pan()
            reference.viewbox_mouse_event(self.mouse_event_2d('mouse_release', pos=(220, 180)))
            clock.return_value = 1.2
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_press'))
            reference.viewbox_mouse_event(self.mouse_event_2d('mouse_press'))
            for step in range(41, 61):
                move = self.mouse_event_2d(
                    'mouse_move', pos=(100 + step * 3, 100 + step * 2), last=(97 + step * 3, 98 + step * 2))
                camera.viewbox_mouse_event(move)
                reference.viewbox_mouse_event(self.mouse_event_2d(
                    'mouse_move', pos=(100 + step * 3, 100 + step * 2), last=(97 + step * 3, 98 + step * 2)))
                reference.flush_pan()
            clock.return_value = 1.3
            for cam in (camera, reference):
                cam.viewbox_mouse_event(self.mouse_event_2d('mouse_wheel', pos=(400, 300), delta=(0, 1)))
            np.testing.assert_allclose(
                camera._scene_transform.map(point)[:2], reference._scene_transform.map(point)[:2], atol=1e-4)

    def test_2d_non_pan_input_keeps_the_existing_throttle(self):
        canvas = self.make_2d_canvas()
        camera = canvas.view.camera
        before = camera.rect.pos + camera.rect.size
        with patch('appGUI.VisPyCanvas.time.time', return_value=1.0) as clock:
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_press'))
            clock.return_value = 1.001
            for kind, button, modifiers, start in (
                    ('mouse_move', 2, (), None),
                    ('mouse_move', 1, (), (100, 100)),
                    ('mouse_move', 2, ('Control',), (100, 100)),
                    ('mouse_move', 2, ('Shift',), (100, 100)),
                    ('mouse_wheel', 2, (), None),
                    ('mouse_press', 2, (), (100, 100)),
                    ('mouse_release', 2, (), (100, 100))):
                with self.subTest(kind=kind, button=button, modifiers=modifiers, start=start):
                    event = self.mouse_event_2d(kind, button=button, modifiers=modifiers,
                                                start=start, delta=(0, 1))
                    camera.viewbox_mouse_event(event)
                    np.testing.assert_array_equal(camera.rect.pos + camera.rect.size, before)
                    self.assertTrue(event.handled)
            clock.return_value = 1.0155
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_move', modifiers=('Shift',)))
            np.testing.assert_allclose(np.array(camera.rect.size) / before[2:], (1 + camera.zoom_factor) ** 5)
            size = camera.rect.size
            clock.return_value = 1.021
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_wheel', delta=(0, 1)))
            np.testing.assert_array_equal(camera.rect.size, size)
            clock.return_value = 1.040
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_wheel', delta=(0, 1)))
            np.testing.assert_allclose(np.array(camera.rect.size) / size, (1 + camera.zoom_factor) ** -30)
            clock.return_value = 1.041
            event = self.mouse_event_2d('mouse_release')
            camera.viewbox_mouse_event(event)
            self.assertTrue(event.handled)
            clock.return_value = 1.060
            event = self.mouse_event_2d('mouse_release')
            camera.viewbox_mouse_event(event)
            self.assertFalse(event.handled)

    def test_2d_zoom_pan_zoom_does_not_reuse_a_throttled_zoom_anchor(self):
        canvas = self.make_2d_canvas()
        camera = canvas.view.camera
        with patch('appGUI.VisPyCanvas.time.time', return_value=1.0) as clock:
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_move', modifiers=('Shift',)))
            size = camera.rect.size
            clock.return_value = 1.001
            camera.viewbox_mouse_event(self.mouse_event_2d(
                'mouse_move', pos=(120, 110), last=(110, 105), modifiers=('Shift',)
            ))
            before = camera._scene_transform.map((150, 150, 0, 1))[:2]
            clock.return_value = 1.002
            camera.viewbox_mouse_event(self.mouse_event_2d('mouse_move', pos=(130, 115), last=(120, 110)))
            camera.flush_pan()
            after = camera._scene_transform.map((150, 150, 0, 1))[:2]
            np.testing.assert_allclose(after - before, (10, 5), atol=1e-5)
            np.testing.assert_array_equal(camera.rect.size, size)
            clock.return_value = 1.003
            camera.viewbox_mouse_event(self.mouse_event_2d(
                'mouse_move', pos=(140, 120), last=(130, 115), modifiers=('Shift',)
            ))
            np.testing.assert_array_equal(camera.rect.size, size)
            clock.return_value = 1.016
            camera.viewbox_mouse_event(self.mouse_event_2d(
                'mouse_move', pos=(150, 125), last=(140, 120), modifiers=('Shift',)
            ))
            np.testing.assert_array_equal(camera.rect.size, size)
            anchor = camera._transform.imap((100, 100))
            anchor_before = camera._scene_transform.map(anchor)
            clock.return_value = 1.018
            camera.viewbox_mouse_event(self.mouse_event_2d(
                'mouse_move', pos=(160, 130), last=(150, 125), modifiers=('Shift',)
            ))
            np.testing.assert_allclose(np.array(camera.rect.size) / size, (1 + camera.zoom_factor) ** 15)
            np.testing.assert_allclose(camera._scene_transform.map(anchor), anchor_before, atol=1e-5)

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

    def test_pan_zoom_resize_and_axis_styles_match_vispy_rendering(self):
        from vispy import scene

        def frames(canvas_type):
            fcapp = CanvasApp()
            fcapp.cursor_color_3D = '#FF0000'
            fcapp.options['global_axis_color'] = '#808080FF'
            layout = QtWidgets.QVBoxLayout(fcapp.ui)
            canvas = canvas_type(fcapp) if canvas_type is PlotCanvas else canvas_type(layout, fcapp)
            try:
                canvas.size = (800, 600)
                canvas.central_widget.size = canvas.size
                camera = canvas.view.camera
                if canvas_type is PlotCanvas:
                    camera.rect = (0, 0, 400, 400)
                scene.visuals.Line(pos=np.array([(20, 30, 0), (300, 200, 10), (150, 250, 0)]),
                                   color='#4070D0A0', antialias=True, parent=canvas.view.scene)
                images, mapped = [], []
                for step in range(12):
                    camera.center = tuple(np.array(camera.center) + (0.2 if step < 2 else 35, 0.1, 0))
                    if step == 2:
                        camera.zoom(0.45)
                    elif step == 3:
                        canvas.size = (900, 650)
                        canvas.central_widget.size = canvas.size
                    elif step == 4:
                        if canvas_type is PlotCanvas3d:
                            camera.set_view('iso')
                        for axis in (canvas.xaxis, canvas.yaxis):
                            axis = getattr(axis, 'axis', axis)
                            axis.axis_label = 'Distance (mm)'
                            axis.tick_font_size = 14
                            axis.axis_font_size = 16
                            axis.text_color = '#C04080'
                            axis.tick_direction = (1, -1)
                    elif step == 5:
                        canvas.xaxis.visible = False
                    elif step == 6:
                        canvas.xaxis.visible = True
                    elif step == 7:
                        for axis in (canvas.xaxis, canvas.yaxis):
                            axis = getattr(axis, 'axis', axis)
                            axis.tick_font_size = 10
                            axis.axis_font_size = 12
                            axis.text_color = '#208050'
                            axis.axis_width = 2
                            axis.tick_width = 1
                    elif step == 8:
                        axis = getattr(canvas.xaxis, 'axis', canvas.xaxis)
                        axis.axis_label = 'Changed distance'
                    elif canvas_type is PlotCanvas3d:
                        if step == 9:
                            canvas.view_cube.rotation_requested.emit(0.1, 0.15)
                        else:
                            camera.fov = 35 if step == 10 else 0
                    images.append(canvas.render())
                    transform = canvas.view.scene.transform
                    points = np.array([(20, 30, 0, 1), (300, 200, 10, 1)])
                    projected = transform.map(points)
                    inverse = transform.imap(projected)
                    mapped.append((projected, inverse))
                    # Perspective depth precision is checked against VisPy below, not an ideal inverse.
                    if canvas_type is PlotCanvas or camera.fov == 0:
                        np.testing.assert_allclose(inverse, points, atol=1e-5)
                return images, mapped
            finally:
                canvas.close()
                fcapp.ui.deleteLater()

        for canvas_type in (PlotCanvas, PlotCanvas3d):
            with self.subTest(canvas=canvas_type.__name__):
                actual_images, actual_mapped = frames(canvas_type)
                with ExitStack() as stack:
                    for owner, name, original in _vispy_axes:
                        stack.enter_context(patch.object(owner, name, original))
                    stack.enter_context(patch.object(AxisVisual, '_update_subvisuals', _axis_update_subvisuals))
                    stack.enter_context(patch.object(AxisVisual, '_prepare_draw', _axis_prepare_draw))
                    stack.enter_context(patch.object(MatrixTransform, 'set_ortho', _matrix_set_ortho))
                    expected_images, expected_mapped = frames(canvas_type)
                for actual, expected in zip(actual_images, expected_images):
                    np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(actual_mapped, expected_mapped)

    def test_2d_ruler_ticks_and_labels_match_vispy_for_many_views(self):
        fcapp = CanvasApp()
        fcapp.options['global_axis_color'] = '#808080FF'
        fcapp.cursor_color_3D = '#FF0000'
        canvas = PlotCanvas(fcapp)
        self.addCleanup(fcapp.ui.deleteLater)
        self.addCleanup(canvas.close)
        canvas.size = (800, 600)
        canvas.central_widget.size = canvas.size
        views = [(-1000.0, -2000.0, 150.0, 90.0), (-5.5, -5.5, 3.1, 3.1), (123456.0, -98765.0, 40.0, 40.0),
                 (-4999.0, -4999.0, 5000.0, 5000.0)]
        for width in (0.01, 0.2, 3.3, 47.0, 333.0, 2500.0, 5000.0):
            views += [(0.0, 0.0, width, width), (-0.37 * width, 0.21 * width, 1.3 * width, 0.8 * width)]

        def rulers():
            result = []
            for view in views:
                canvas.view.camera.rect = view
                canvas.render()
                result.append([(tuple(widget.axis.domain), np.array(widget.axis._ticks._pos),
                                list(widget.axis._text.text), np.array(widget.axis._text.pos))
                               for widget in (canvas.xaxis, canvas.yaxis)])
            return result

        rulers()  # the layout settles on the first render
        actual = rulers()
        with ExitStack() as stack:
            for owner, name, original in _vispy_axes:
                stack.enter_context(patch.object(owner, name, original))
            expected = rulers()
        for view, got, want in zip(views, actual, expected):
            for axis_got, axis_want in zip(got, want):
                with self.subTest(view=view):
                    self.assertEqual(axis_got[0], axis_want[0])
                    np.testing.assert_array_equal(axis_got[1], axis_want[1])
                    self.assertEqual(axis_got[2], axis_want[2])
                    np.testing.assert_array_equal(axis_got[3], axis_want[3])

    def test_orthographic_projection_replaces_mutated_matrix_and_perspective(self):
        transform = MatrixTransform()
        point = (1, 2, 3, 1)
        for bounds in ((-10, 10, -20, 20, -100, 100), (-5, 15, -10, 30, -50, 200)):
            reference = MatrixTransform()
            _matrix_set_ortho(reference, *bounds)
            for change in ('ortho', 'mutated', 'perspective'):
                with self.subTest(bounds=bounds, change=change):
                    if change == 'mutated':
                        transform.matrix[3, 0] += 10
                    elif change == 'perspective':
                        transform.set_perspective(45, 1.5, 1, 100)
                    transform.set_ortho(*bounds)
                    np.testing.assert_array_equal(transform.map(point), reference.map(point))
                    np.testing.assert_array_equal(transform.imap(transform.map(point)),
                                                  reference.imap(reference.map(point)))

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
        canvas = self.make_canvas(camera_reference='lmb')
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

    def test_trackball_tumbles_and_rolls_without_moving_the_pivot(self):
        canvas = self.make_canvas()
        camera = canvas.view.camera
        camera.set_view('top')
        canvas.content_bounds = ((0, 0, 0), (250, 250, 4))
        camera.center = np.array(camera.center) + (40, -20, 0)
        pivot = np.r_[canvas.geometry_center(), 1]
        points = np.array([pivot, pivot + (10, 0, 0, 0), pivot + (0, 0, 10, 0)])
        initial = camera.get_state()
        for start, end, roll in (((400, 300), (440, 300), False),
                                 ((760, 300), (650, 560), True)):
            with self.subTest(roll=roll):
                if roll:
                    canvas.camera_reference = 'lmb'
                    canvas.camera_reference = 'center_rotate'
                camera.set_state(initial)
                camera.viewbox_mouse_event(self.mouse_event('mouse_release'))
                projected = camera._scene_transform.map(points)
                before = projected[:, :2] / projected[:, 3:]
                camera.viewbox_mouse_event(self.mouse_event('mouse_move', end, start, (1,)))
                projected = camera._scene_transform.map(points)
                after = projected[:, :2] / projected[:, 3:]
                np.testing.assert_allclose(after[0], before[0], atol=1e-5)
                if roll:
                    self.assertGreater(abs(after[1, 1] - after[0, 1]), 1)
                    np.testing.assert_allclose(after[2], after[0], atol=1e-5)
                else:
                    self.assertGreater(np.linalg.norm(after[2] - after[0]), 1)
                camera.viewbox_mouse_event(self.mouse_event('mouse_move', start, start, (1,)))
                projected = camera._scene_transform.map(points)
                np.testing.assert_allclose(projected[:, :2] / projected[:, 3:], before, atol=1e-5)
                if roll:
                    canvas.camera_reference = 'center'
                    camera.set_state(initial)
                    camera.viewbox_mouse_event(self.mouse_event('mouse_move', end, start, (1,)))
                    projected = camera._scene_transform.map(points)
                    screen = projected[:, :2] / projected[:, 3:]
                    self.assertGreater(np.linalg.norm(screen[2] - screen[0]), 1)

    def test_many_rotate_moves_compose_in_order_and_hold_the_pivot(self):
        from vispy.scene.cameras.arcball import _arcball
        from vispy.util.quaternion import Quaternion

        rng = np.random.default_rng(5)
        for reference in ('center', 'center_rotate'):
            with self.subTest(reference=reference):
                canvas = self.make_canvas(camera_reference=reference)
                camera = canvas.view.camera
                camera.set_view('iso')
                canvas.content_bounds = ((0, 0, 0), (250, 250, 4))
                canvas.fit_view()
                pivot = np.r_[canvas.geometry_center(), 1]
                before = camera._scene_transform.map(pivot)
                before = before[:2] / before[3]
                start = np.array((400.0, 300.0))
                size = min(canvas.view.size)
                expected = camera.get_state()['_quaternion']
                previous = start
                for _ in range(40):
                    pos = previous + rng.normal(0, 25, 2)
                    camera.viewbox_mouse_event(self.mouse_event('mouse_move', pos, start, (1,)))
                    # the rotation of each move is applied to the orientation of the moves before it
                    if reference == 'center':
                        delta = (pos - previous) * np.pi / size
                        horizontal = Quaternion.create_from_axis_angle(-delta[0], 0, 1, 0)
                        vertical = Quaternion.create_from_axis_angle(-delta[1], 1, 0, 0)
                        expected = vertical * horizontal * expected
                    else:
                        expected = (Quaternion(*_arcball(pos, canvas.view.size)) *
                                    Quaternion(*_arcball(previous, canvas.view.size)) * expected)
                    previous = pos
                camera.viewbox_mouse_event(self.mouse_event('mouse_release'))
                actual = camera.get_state()['_quaternion']
                rotation = np.array((actual.w, actual.x, actual.y, actual.z))
                wanted = np.array((expected.w, expected.x, expected.y, expected.z))
                self.assertAlmostEqual(abs(rotation @ wanted), 1.0, places=5)
                after = camera._scene_transform.map(pivot)
                np.testing.assert_allclose(after[:2] / after[3], before, atol=1e-3)

    def test_center_reference_rotates_about_loaded_object_bounds_and_can_change(self):
        canvas = self.make_canvas(camera_reference='center')
        camera = canvas.view.camera
        bounds = [1000, 2000, 1200, 2100]
        canvas.fcapp.collection = SimpleNamespace(get_bounds=lambda: bounds)
        canvas.content_bounds = ((1000, 2000, -2), (1020, 2010, 4))
        camera.set_view('iso')
        canvas.fit_view()
        camera.center = np.array(camera.center) + (40, -20, 0)
        for reference in ('center', 'center_rotate', 'lmb', 'center'):
            with self.subTest(reference=reference):
                canvas.camera_reference = reference
                pivot = np.array(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, 1, 1))
                before = camera._scene_transform.map(pivot)
                camera.viewbox_mouse_event(self.mouse_event('mouse_move', (160, 140), (100, 100), (1,)))
                after = camera._scene_transform.map(pivot)
                before, after = before[:2] / before[3], after[:2] / after[3]
                if reference != 'lmb':
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
