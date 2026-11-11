"""Editor deltas use the displayed click position and update both readouts."""

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from vispy.color import Color
from vispy.scene.visuals import Rectangle, Text
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from appEditors.appExcEditor import AppExcEditor
from appEditors.appGeoEditor import AppGeoEditor
from appEditors.appGerberEditor import AppGerberEditor
from appGUI.MainGUI import MainGUI
from appGUI.PlotCanvas import PlotCanvas
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from defaults import AppDefaults


@pytest.fixture
def editor_factory(monkeypatch):
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))
    monkeypatch.setattr(AppGeoEditor, 'snap_feedback', Mock())

    def make(kind, grid, raw_pick=False):
        cls = {'geo': AppGeoEditor, 'grb': AppGerberEditor, 'exc': AppExcEditor}[kind]
        tool = SimpleNamespace(name=('select' if raw_pick else 'test'), click=Mock(),
                               complete=False, uses_raw_pick=raw_pick)
        if kind == 'exc' and raw_pick:
            tool.name = 'drill_select'
        canvas = SimpleNamespace(translate_coords=lambda point: point, cursor_color='black',
                                 on_update_text_hud=Mock(), native=Mock(hasFocus=lambda: True))
        defaults = SimpleNamespace(get=lambda key, fallback: fallback, factory_defaults={})
        app = SimpleNamespace(grid_status=lambda: grid, plotcanvas=canvas, app_cursor=Mock(),
                              options={}, defaults=defaults, dx=91, dy=92,
                              ui=SimpleNamespace(popMenu=SimpleNamespace(), rel_position_label=Mock(),
                                                 update_location_labels=Mock()))
        editor = SimpleNamespace(app=app, canvas=canvas, active_tool=tool, pos=None, clicked_pos=None,
                                 history=Mock(ready=lambda: True), storage_dict={}, vertex_grips=Mock(),
                                 update_utility_geometry=Mock(), decimals=4)
        editor.vertex_grips.press.return_value = False
        editor.vertex_grips.move.return_value = False
        editor.snap = lambda x, y: (round(x), round(y)) if grid else (x, y)
        editor._tool_wants_raw_pick = lambda: raw_pick
        app.geo_editor = editor
        for name in ('on_canvas_click', 'on_canvas_move'):
            setattr(editor, name, MethodType(getattr(cls, name), editor))
        if kind == 'exc':
            editor.on_canvas_click_left_handler = MethodType(cls.on_canvas_click_left_handler, editor)
        editor._application = application
        return editor

    return make


def mouse(pos, button=1):
    return SimpleNamespace(pos=pos, button=button, is_dragging=False)


@pytest.mark.parametrize('kind, raw_pick', [('geo', False), ('grb', True)])
@pytest.mark.parametrize('grid', [True])
def test_click_resets_both_readouts_and_motion_measures_from_same_coordinates(editor_factory, kind, raw_pick, grid):
    editor = editor_factory(kind, grid, raw_pick)
    event = mouse((1.2, 2.3))
    editor.on_canvas_click(event)
    origin = (1, 2) if grid and not (kind == 'grb' and raw_pick) else event.pos
    assert (editor.app.dx, editor.app.dy) == (0, 0)
    editor.app.ui.update_location_labels.assert_called_with(0, 0, *origin)
    editor.canvas.on_update_text_hud.assert_called_with(0, 0, *origin)

    editor.on_canvas_move(event)
    assert (editor.app.dx, editor.app.dy) == pytest.approx((0, 0))
    editor.on_canvas_move(mouse((4.4, -1.2), button=0))
    current = (4, -1) if grid and not (kind == 'grb' and raw_pick) else (4.4, -1.2)
    expected = (current[0] - origin[0], current[1] - origin[1], *current)
    assert editor.app.ui.update_location_labels.call_args.args == pytest.approx(expected)
    assert editor.canvas.on_update_text_hud.call_args.args == pytest.approx(expected)

    editor.on_canvas_click(mouse((-3.2, 7.4)))
    editor.on_canvas_move(mouse((-3.2, 7.4)))
    assert (editor.app.dx, editor.app.dy) == pytest.approx((0, 0))


@pytest.mark.parametrize('kind', ['geo'])
def test_right_click_does_not_move_delta_origin(editor_factory, kind):
    editor = editor_factory(kind, False)
    editor.on_canvas_click(mouse((1, 2)))
    editor.on_canvas_click(mouse((20, 30), button=2))
    editor.on_canvas_move(mouse((4, 6), button=0))
    assert (editor.app.dx, editor.app.dy) == (3, 4)


def test_excellon_numeric_click_resets_delta_origin(editor_factory):
    editor = editor_factory('exc', False)
    editor.clicked_pos = (100, 200)
    editor.on_canvas_click_left_handler(custom_pos=(1, 2))
    editor.on_canvas_move(mouse((4, 6), button=0))
    assert (editor.app.dx, editor.app.dy) == (3, 4)


def test_geometry_shift_path_uses_current_constrained_point_for_both_readouts(editor_factory, monkeypatch):
    editor = editor_factory('geo', False)
    editor.on_canvas_click(mouse((1, 2)))
    editor.active_tool.name = 'path'
    editor.active_tool.close_x, editor.active_tool.close_y = -100, -200
    editor.active_tool.draw_cursor_data = Mock()

    def update(data):
        editor.active_tool.close_x, editor.active_tool.close_y = data[0], 2

    editor.update_utility_geometry.side_effect = update
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.ShiftModifier))
    editor.on_canvas_move(mouse((4, 2.1), button=0))
    editor.app.ui.update_location_labels.assert_called_with(3, 0, 4, 2)
    editor.canvas.on_update_text_hud.assert_called_with(3, 0, 4, 2)


@pytest.fixture(scope='session')
def coordinate_application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def coordinate_window(monkeypatch, coordinate_application):
    app = MagicMock(decimals=4, resource_location='assets/resources')
    app.defaults = AppDefaults(beta=False)
    app.options = dict(app.defaults)
    app.dec_format = lambda value, decimals: round(value, decimals)
    monkeypatch.setattr(OptionsGroupUI, 'app', app)
    window = MainGUI(app)
    yield window
    window.close()
    window.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
    coordinate_application.processEvents()


def label_text(label):
    document = QtGui.QTextDocument()
    document.setHtml(label.text())
    return document.toPlainText()


def test_delta_context_menu_and_left_click(coordinate_window):
    window = coordinate_window
    clicked = []
    window.delta_coords_toolbar.clicked.connect(clicked.append)
    for widget in (window.delta_coords_toolbar, window.rel_position_label):
        menus = []

        def close_menu():
            for menu in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(menu, QtWidgets.QMenu) and menu.isVisible():
                    menus.append([(action.text(), action.isCheckable(), action.isChecked())
                                  for action in menu.actions()])
                    menu.close()

        QtCore.QTimer.singleShot(50, close_menu)
        point = QtCore.QPoint(5, 5)
        event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Reason.Mouse,
                                       point, widget.mapToGlobal(point))
        QtWidgets.QApplication.sendEvent(widget, event)
        assert menus == [[('Polar Coords', True, False)]]
        QtTest.QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton, pos=point)
    assert len(clicked) == 2


def test_polar_toggle_refreshes_latest_deltas_and_preserves_clearing(coordinate_window):
    window = coordinate_window
    action = next(action for action in window.delta_coords_menu.actions() if action.text() == 'Polar Coords')
    assert not action.isChecked()
    action.trigger()  # Safe even before the first mouse position.
    for dx, dy, radius, theta in [(3, 4, '5.0', '53.1301'), (-3, -4, '5.0', '233.1301'),
                                  (0, -2, '2.0', '270.0'), (-0.0, -0.0, '0.0', '0.0')]:
        action.setChecked(False)
        window.update_location_labels(dx, dy, 12, 34)
        action.trigger()  # No timer or mouse movement required.
        relative = label_text(window.rel_position_label)
        assert f'R: {radius}' in relative
        assert f'θ: {theta}°' in relative
        assert 'X: 12' in label_text(window.position_label)
        assert 'Y: 34' in label_text(window.position_label)
        action.trigger()
        assert f'Dx: {dx if dx else 0.0}' in label_text(window.rel_position_label)
        assert f'Dy: {dy if dy else 0.0}' in label_text(window.rel_position_label)

    action.setChecked(True)
    window.update_location_labels(6, 8, 12, 34)
    assert 'R: 10.0' not in label_text(window.rel_position_label)
    QtTest.QTest.qWait(60)
    assert 'R: 10.0' in label_text(window.rel_position_label)
    window.update_location_labels(None, 8, 12, 34)
    action.setChecked(False)
    assert window.rel_position_label.text() == ''
    assert 'X: 12' in label_text(window.position_label)
    window.update_location_labels(None, None, None, None)
    assert window.position_label.text() == window.rel_position_label.text() == ''
    action.setChecked(True)
    QtTest.QTest.qWait(60)
    assert window.position_label.text() == window.rel_position_label.text() == ''


@pytest.mark.parametrize('units', ['MM', 'IN'])
def test_polar_hud_refreshes_on_toggle_and_motion_without_changing_coordinates(coordinate_window, units):
    window = coordinate_window
    app = window.app
    app.ui = window
    app.app_units = units
    canvas = SimpleNamespace(
        fcapp=app, hud_enabled=True, native=window, view=SimpleNamespace(size=(800, 600), bgcolor=Color('white')),
        text_hud=Text('', color='black', method='cpu'),
        rect_hud=Rectangle(center=(20, 20), width=10, height=10),
        _hud_update_timer=QtCore.QTimer(window))
    canvas._hud_update_timer.setSingleShot(True)
    canvas._hud_update_timer.setInterval(33)
    for name in ('on_update_text_hud', '_update_text_hud', 'apply_hud_options'):
        setattr(canvas, name, MethodType(getattr(PlotCanvas, name), canvas))
    canvas._hud_update_timer.timeout.connect(lambda: canvas._update_text_hud())
    app.plotcanvas = canvas
    action = window.polar_coords_action
    for dx, dy, radius, theta in [(3, 4, '5.0', '53.1301'), (-3, -4, '5.0', '233.1301'),
                                  (0, -2, '2.0', '270.0'), (-0.0, -0.0, '0.0', '0.0')]:
        canvas.on_update_text_hud(dx, dy, 12, 34)
        action.setChecked(True)
        rows = [' '.join(row.split()) for row in canvas.text_hud.text]
        assert rows == [f'R: {radius} [{units.lower()}]', f'θ: {theta}°',
                        f'X: 12 [{units.lower()}]', f'Y: 34 [{units.lower()}]']
        action.setChecked(False)
        assert canvas.text_hud.text[0].startswith('Dx:')
        assert canvas.text_hud.text[1].startswith('Dy:')

    action.setChecked(True)
    canvas.on_update_text_hud(6, 8, 20, 30)
    QtTest.QTest.qWait(60)
    assert '10.0' in canvas.text_hud.text[0]
    assert '20' in canvas.text_hud.text[2]
    canvas.on_update_text_hud()
    QtTest.QTest.qWait(60)
    assert [' '.join(row.split()) for row in canvas.text_hud.text][:2] == [
        f'R: 0.0 [{units.lower()}]', 'θ: 0.0°']
    canvas.hud_enabled = False
    previous = canvas.text_hud.text[:]
    action.setChecked(False)
    assert not canvas.hud_enabled
    assert canvas.text_hud.text == previous
    app.plotcanvas = SimpleNamespace()  # Canvases without a HUD must still support the status toggle.
    action.setChecked(True)
