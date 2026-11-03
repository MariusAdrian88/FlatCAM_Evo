"""Finite 3D workspace and camera interaction checks.

Run: .venv/Scripts/python.exe tests/test_plotcanvas3d.py
"""

import os
import sys
import unittest
from itertools import product
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets
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

    def make_canvas(self, units='MM', custom_cursor=True):
        fcapp = CanvasApp(units, custom_cursor)
        layout = QtWidgets.QVBoxLayout(fcapp.ui)
        canvas = PlotCanvas3d(layout, fcapp)
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

    def test_grid_menu_and_shortcut_follow_the_active_plot(self):
        from appGUI.MainGUI import MainGUI
        from appHandlers.appSignalConnector import AppSignalConnector

        class Window(QtWidgets.QMainWindow):
            eventFilter = MainGUI.eventFilter
            app_focus_in_text = staticmethod(MainGUI.app_focus_in_text)

            def on_toggle_grid_lines(self):
                MainGUI.on_toggle_grid_lines(self)

            def on_toggle_grid(self):
                pass

        canvas3d = self.make_canvas()
        fcapp = CanvasApp()
        fcapp.options['global_axis_color'] = '#FF0000'
        fcapp.cursor_color_3D = '#FF0000'
        fcapp.inform = MagicMock()
        canvas2d = PlotCanvas(fcapp)
        canvas2d.on_toggle_grid_lines(silent=True)
        self.addCleanup(fcapp.ui.deleteLater)
        self.addCleanup(canvas2d.close)
        window = Window()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.plot_tab_area = QtWidgets.QTabWidget()
        window.setCentralWidget(window.plot_tab_area)
        window.plot_tab_area.addTab(canvas2d.native, 'Plot Area')
        window.plot_tab_area.addTab(canvas3d.fcapp.ui, 'Plot 3D')
        window.app = Mock(ui=window, plotcanvas=canvas2d, plotcanvas3d=canvas3d,
                          area_3d_tab=canvas3d.fcapp.ui, options={'global_toggle_tooltips': True})
        for name in ('menuviewenable', 'menuviewdisableall', 'menuviewenableother', 'menuviewdisableother',
                     'menuview_zoom_fit', 'menuview_zoom_in', 'menuview_zoom_out', 'menuview_replot',
                     'menuview_toggle_code_editor', 'menuview_toggle_fscreen', 'menuview_toggle_parea',
                     'menuview_toggle_notebook', 'menu_toggle_nb', 'menuview_toggle_grid',
                     'menuview_toggle_workspace', 'menuview_toggle_grid_lines', 'menuview_toggle_axis',
                     'menuview_toggle_hud', 'menuview_show_log'):
            setattr(window, name, QtGui.QAction(name, window))
        window.on_full_screen_toggled = Mock()
        window.on_toggle_plotarea = Mock()
        window.on_toggle_notebook = Mock()
        AppSignalConnector(window.app).connect_menuview_signals()
        self.qapp.installEventFilter(window)
        self.addCleanup(self.qapp.removeEventFilter, window)
        for index, canvas, other in ((1, canvas3d, canvas2d), (0, canvas2d, canvas3d)):
            with self.subTest(tab=index):
                window.plot_tab_area.setCurrentIndex(index)
                canvas.native.setFocus()
                self.qapp.processEvents()
                before, other_before = canvas.grid.visible, other.grid.visible
                window.menuview_toggle_grid_lines.trigger()
                self.assertEqual(canvas.grid.visible, not before)
                self.assertEqual(other.grid.visible, other_before)
                QtTest.QTest.keyClick(canvas.native, QtCore.Qt.Key.Key_G,
                                     QtCore.Qt.KeyboardModifier.ShiftModifier)
                self.assertEqual(canvas.grid.visible, before)
                self.assertEqual(other.grid.visible, other_before)
        before = canvas3d.grid.visible
        canvas3d.context_menu.actions()[0].trigger()
        self.assertEqual(canvas3d.grid.visible, not before)
        self.assertEqual(canvas3d.grid_lines_enabled, not before)
        window.app.plotcanvas3d = None
        before = canvas2d.grid.visible
        window.menuview_toggle_grid_lines.trigger()
        self.assertEqual(canvas2d.grid.visible, not before)

    def test_plot_cursor_is_only_visible_inside_canvas(self):
        fcapp = CanvasApp()
        fcapp.options['global_axis_color'] = '#FF0000'
        fcapp.cursor_color_3D = '#FF0000'
        canvas = PlotCanvas(fcapp)
        self.addCleanup(fcapp.ui.deleteLater)
        self.addCleanup(canvas.close)
        for big in (False, True):
            with self.subTest(big=big):
                cursor = canvas.new_cursor(big=big)
                cursor.enabled = True
                cursor.set_data(np.array([(10., 20.)]))
                visuals = (canvas.cursor_h_line, canvas.cursor_v_line) if big else (cursor,)
                QtWidgets.QApplication.sendEvent(canvas.native, QtCore.QEvent(QtCore.QEvent.Type.Leave))
                self.assertTrue(all(not visual.visible for visual in visuals))
                # Grid/editor updates outside the canvas must not reveal the cursor.
                cursor.enabled = False
                cursor.enabled = True
                cursor.set_data(np.array([(20., 30.)]))
                self.assertTrue(all(not visual.visible for visual in visuals))
                QtWidgets.QApplication.sendEvent(
                    canvas.native, QtGui.QEnterEvent(QtCore.QPointF(), QtCore.QPointF(), QtCore.QPointF())
                )
                self.assertTrue(all(visual.visible and visual.parent is canvas.view.scene for visual in visuals))
                # A Leave with no Enter after it (a modal dialog, an editor save): a mouse move shows the cursor.
                QtWidgets.QApplication.sendEvent(canvas.native, QtCore.QEvent(QtCore.QEvent.Type.Leave))
                QtTest.QTest.qWait(20)  # VisPy drops a mouse move within 10 ms of the last one
                QtWidgets.QApplication.sendEvent(canvas.native, QtGui.QMouseEvent(
                    QtCore.QEvent.Type.MouseMove, QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
                    QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.NoButton,
                    QtCore.Qt.KeyboardModifier.NoModifier))
                self.assertTrue(all(visual.visible for visual in visuals))
                cursor.enabled = False
                QtWidgets.QApplication.sendEvent(canvas.native, QtCore.QEvent(QtCore.QEvent.Type.Leave))
                QtWidgets.QApplication.sendEvent(
                    canvas.native, QtGui.QEnterEvent(QtCore.QPointF(), QtCore.QPointF(), QtCore.QPointF())
                )
                self.assertTrue(all(visual.parent is None for visual in visuals))

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

    def test_notebook_resize_keeps_grid_fixed_on_screen(self):
        fcapp, canvas = self.make_hud_canvas()
        layout = QtWidgets.QVBoxLayout(fcapp.ui)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter)
        splitter.addWidget(QtWidgets.QTabWidget())
        right_widget = QtWidgets.QWidget()
        splitter.addWidget(right_widget)
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        plot_tabs = QtWidgets.QTabWidget()
        right_layout.addWidget(plot_tabs)
        plot_tab = QtWidgets.QWidget()
        plot_tabs.addTab(plot_tab, 'Plot Area')
        QtWidgets.QVBoxLayout(plot_tab).addWidget(canvas.native)
        fcapp.ui.resize(1000, 600)
        splitter.setSizes([0, 1000])
        fcapp.ui.show()
        self.addCleanup(fcapp.ui.close)
        QtTest.QTest.qWait(30)
        canvas.view.camera.rect = (3, 4, 0.04, 0.04)
        pointer = canvas.native.mapToGlobal(QtCore.QPoint(650, 250))
        point = canvas.translate_coords((650, 250))[:2]
        scale = canvas.pixel_size()
        grid_points = np.array([point, point + (0.001, 0.001)])
        origin = canvas.native.mapToGlobal(QtCore.QPoint())
        screen = np.array([canvas.translate_coords_2(p)[:2] for p in grid_points])
        screen += (origin.x(), origin.y())
        for sizes in ([250, 750], [350, 650], [800, 200], [0, 1000]):
            with self.subTest(sizes=sizes):
                splitter.setSizes(sizes)
                QtTest.QTest.qWait(30)
                local = canvas.native.mapFromGlobal(pointer)
                actual = canvas.translate_coords((local.x(), local.y()))[:2]
                np.testing.assert_allclose(actual, point, rtol=0, atol=1e-6)
                self.assertAlmostEqual(canvas.pixel_size() / scale, 1, places=5)
                origin = canvas.native.mapToGlobal(QtCore.QPoint())
                actual_screen = np.array([canvas.translate_coords_2(p)[:2] for p in grid_points])
                actual_screen += (origin.x(), origin.y())
                np.testing.assert_allclose(actual_screen, screen, rtol=0, atol=0.1)
        # An explicit new view takes precedence over a pending layout adjustment.
        splitter.setSizes([250, 750])
        canvas.view.camera.rect = (10, 20, 1, 1)
        QtTest.QTest.qWait(30)
        self.assertEqual(canvas.view.camera.rect.pos, (10, 20))
        self.assertEqual(canvas.view.camera.rect.size, (1, 1))

    def make_hud_canvas(self, **options):
        fcapp = CanvasApp()
        fcapp.options.update({'global_axis_color': '#FF0000', 'global_backface_culling': True})
        fcapp.options.update(options)
        fcapp.defaults = {}
        fcapp.cursor_color_3D = '#FF0000'
        fcapp.decimals = 4
        fcapp.dec_format = lambda value, decimals: round(value, decimals)
        fcapp.ui.hud_label = QtWidgets.QLabel()
        fcapp.inform = MagicMock()
        canvas = PlotCanvas(fcapp)
        self.addCleanup(fcapp.ui.deleteLater)
        self.addCleanup(canvas.close)
        canvas.view.size = (800, 600)
        canvas.on_toggle_hud(state=True, silent=True)
        return fcapp, canvas

    def test_hud_sits_in_the_chosen_corner_at_the_offset(self):
        fcapp, canvas = self.make_hud_canvas()
        rect = canvas.rect_hud
        self.assertAlmostEqual(rect.center[0] - rect.width / 2, 10)
        self.assertAlmostEqual(rect.center[1] - rect.height / 2, 10)
        for position, offset in (('TR', 12), ('BL', 7), ('BR', 20)):
            with self.subTest(position=position):
                fcapp.options['global_hud_position'] = position
                fcapp.options['global_hud_offset'] = offset
                canvas.apply_hud_options()
                left, top = rect.center[0] - rect.width / 2, rect.center[1] - rect.height / 2
                right, bottom = left + rect.width, top + rect.height
                if 'R' in position:
                    self.assertAlmostEqual(800 - right, offset)
                else:
                    self.assertAlmostEqual(left, offset)
                if 'B' in position:
                    self.assertAlmostEqual(600 - bottom, offset)
                else:
                    self.assertAlmostEqual(top, offset)
                self.assertGreaterEqual(canvas.text_hud.pos[:, 0].min(), left)
                self.assertLessEqual(canvas.text_hud.pos[:, 1].max(), bottom)
        # a resize keeps the HUD against the same corner
        canvas.view.size = (1000, 700)
        canvas._update_text_hud()
        self.assertAlmostEqual(1000 - (rect.center[0] + rect.width / 2), 20)
        self.assertAlmostEqual(700 - (rect.center[1] + rect.height / 2), 20)

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

    def test_initial_view_is_top_down_and_fills_height_with_label_space(self):
        canvas = self.make_canvas()
        camera = canvas.view.camera
        points = np.array([(0, 0, 0, 1), (400, 0, 0, 1), (0, 400, 0, 1), (400, 400, 0, 1)])
        projected = camera._scene_transform.map(points)
        screen = projected[:, :2] / projected[:, 3:]
        self.assertGreater(screen[1, 0], screen[0, 0])
        self.assertLess(screen[2, 1], screen[0, 1])
        self.assertAlmostEqual(screen[1, 1], screen[0, 1])
        self.assertAlmostEqual(screen[2, 0], screen[0, 0])
        self.assertAlmostEqual(screen[1, 0] - screen[0, 0], screen[0, 1] - screen[2, 1], places=3)
        self.assertGreaterEqual(screen[:, 0].min(), canvas.axis_padding - 1e-3)
        self.assertGreaterEqual(screen[:, 1].min(), 20 - 1e-3)
        self.assertLessEqual(screen[:, 0].max(), 800 - 20 + 1e-3)
        self.assertLessEqual(screen[:, 1].max(), 600 - canvas.axis_padding + 1e-3)
        self.assertAlmostEqual(screen[0, 1] - screen[2, 1], 600 - canvas.axis_padding - 20, places=3)

    def test_zoom_in_stops_at_one_third_of_ten_millimetres(self):
        for units, limit in (('MM', 10 / 3), ('IN', 10 / 25.4 / 3)):
            with self.subTest(units=units):
                camera = self.make_canvas(units=units).view.camera
                camera.zoom(1e-6)
                self.assertAlmostEqual(camera.scale_factor, limit)
                camera.zoom(2)
                self.assertAlmostEqual(camera.scale_factor, limit * 2)

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

if __name__ == '__main__':
    unittest.main()
