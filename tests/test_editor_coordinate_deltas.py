"""Editor deltas use the displayed click position and update both readouts."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets

from appEditors.appExcEditor import AppExcEditor
from appEditors.appGeoEditor import AppGeoEditor
from appEditors.appGerberEditor import AppGerberEditor


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
