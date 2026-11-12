"""Editor tests shared by all editors: coordinate deltas, shutdown layout, click-pick consumers."""
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
from unittest.mock import Mock
from PyQt6 import QtCore, QtWidgets
from appMain import App
from appHandlers.appLifecycle import AppLifecycle
from tests.test_shutdown_routing import shutdown, qt_app  # noqa: F401
from shapely.geometry import LineString, Point, box
from appPlugins.ToolAlignObjects.AlignGen import build_snap_index, snap
from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolPunchGerber import ToolPunchGerber
from tests.helpers_objects import app, excellon, gerber, _headless_qt, make_excellon, make_gerber  # noqa: F401,E402


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


SOURCES = ['geo_editor']


def toolbar_states(window):
    tools = QtWidgets.QToolBar('Tools', window)
    tools.setObjectName('test_tools')
    window.addToolBar(tools)
    editor = QtWidgets.QToolBar('Editor', window)
    editor.setObjectName('test_editor')
    window.addToolBar(editor)
    tools.show()
    editor.hide()
    normal = window.saveState(0)
    tools.hide()
    editor.show()
    return tools, editor, normal


@pytest.mark.parametrize('source', SOURCES)
def test_window_close_saves_normal_layout_without_disturbing_an_editor_if_exit_stops(shutdown, source):
    s = shutdown
    tools, editor, normal = toolbar_states(s.window)
    s.app.call_source = source
    s.app._editor_gui_state = normal
    s.app.should_we_save = False
    current = s.window.saveState(0)
    s.action.trigger()
    saved = next(call.args[1] for call in s.settings.setValue.call_args_list
                 if call.args[0] == 'saved_gui_state')
    assert saved == (current if source == 'app' else normal)
    assert s.window.saveState(0) == current
    assert tools.isHidden() and not editor.isHidden()


@pytest.mark.parametrize('source', SOURCES)
def test_confirmed_quit_restores_and_saves_layout_before_editor_cleanup_can_replace_it(shutdown, monkeypatch, source):
    s = shutdown
    tools, editor, normal = toolbar_states(s.window)
    s.app.call_source = source
    s.app._editor_gui_state = normal
    current = s.window.saveState(0)
    expected = current if source == 'app' else normal
    s.app.headless = False
    s.app.tools_db_tab = None
    s.app.geo_editor = None
    s.window.general_pref_form = Mock()
    monkeypatch.setattr(QtCore, 'QSettings', Mock(return_value=s.settings))
    monkeypatch.setattr(QtWidgets.QApplication, 'quit', Mock())
    monkeypatch.setattr(QtCore.QTimer, 'singleShot', Mock())

    def deactivate(kind):
        # Editor shutdown can change both toolbar visibility and call_source.
        tools.hide()
        editor.hide()
        s.app.call_source = 'app'

    s.app._deactivate_editor.side_effect = deactivate
    assert AppLifecycle.quit_application(s.lifecycle) is True
    saved = next(call.args[1] for call in s.settings.setValue.call_args_list
                 if call.args[0] == 'saved_gui_state')
    assert saved == expected
    assert s.window.saveState(0) == expected


@pytest.mark.parametrize('kind,attribute,method,source', [
    ('gerber', 'grb_editor', 'edit_fcgerber', 'grb_editor'),
])
def test_entering_an_editor_captures_the_layout_before_toolbars_change(qt_app, kind, attribute, method, source):
    window = QtWidgets.QMainWindow()
    window.show()
    tools, toolbar, normal = toolbar_states(window)
    window.restoreState(normal)
    ui = Mock(toolbarplugins=tools, saveState=window.saveState,
              geo_edit_toolbar=toolbar, grb_edit_toolbar=toolbar, exc_edit_toolbar=toolbar)
    ui.notebook.count.return_value = 0
    ui.splitter.sizes.return_value = [120, 400]
    obj = Mock(kind=kind, multigeo=False)
    app = Mock(call_source='app', ui=ui)
    app.collection.get_active.return_value = obj
    getattr(getattr(app, attribute), method).side_effect = lambda *args: toolbar.show()
    try:
        App.on_editing_start(app)
        assert app.call_source == source
        assert isinstance(app._editor_gui_state, QtCore.QByteArray)
        assert app._editor_gui_state == normal
        assert tools.isHidden() and not toolbar.isHidden()
    finally:
        window.close()


def _event(pos):
    ev = Mock()
    ev.pos = pos
    ev.button = 1
    return ev


def _fake_app():
    fake_app = MagicMock()
    fake_app.selection_type = None
    fake_app.dec_format = lambda value, decimals: round(value, decimals)
    return fake_app


def _gerber_like(tools):
    obj = Mock()
    obj.kind = 'gerber'
    obj.tools = tools
    return obj


def _custom_excellon(app, tools):
    exc = make_excellon(app, name='exc_custom')
    exc.tools = tools
    assert exc.create_geometry() is None
    return exc


def align_click(target, pos):
    """Return the list of snapped points for one click (0 or 1 point)."""
    pt = snap(build_snap_index(target), pos[0], pos[1], 0.01)
    return [] if pt is None else [list(pt)]


class TestAlignGerber:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), [5.0, 5.0]),         # C flash
        ((15.2, 35.1), [15.0, 35.0]),     # flash after the clear track
    ])
    def test_flash_gives_the_flash_point(self, gerber, click, center):
        assert align_click(gerber, click) == [pytest.approx(center)]

    def test_center_is_the_follow_point_not_the_solid_centroid(self):
        obj = _gerber_like({1: {'geometry': [{'solid': box(0, -1, 2, 1), 'follow': Point(0, 0)}]}})
        assert align_click(obj, (0.5, 0.0)) == [pytest.approx([0.0, 0.0])]


class TestAlignExcellon:
    def test_drill_gives_its_center(self, excellon):
        assert align_click(excellon, (5.1, 12.1)) == [pytest.approx([5.0, 12.0])]

    def test_slot_gives_its_centroid(self, excellon):
        assert align_click(excellon, (24.0, 12.2)) == [pytest.approx([24.0, 12.0])]


def dbl_click(exc, pos):
    """Return the list of reference points set in the point entry and the number of on_exit calls."""
    fake = Mock()
    fake.app = _fake_app()
    fake.canvas.translate_coords = lambda p: p
    fake.exc_hole_obj = exc
    fake.decimals = 4
    DblSidedTool.on_mouse_click_release(fake, _event(pos))
    return [c.args[0] for c in fake.ui.point_entry.set_value.call_args_list], fake.on_exit.call_count


class TestDblSided:
    def test_drill_gives_its_center(self, excellon):
        points, exits = dbl_click(excellon, (5.1, 12.1))
        assert points == [pytest.approx((5.0, 12.0))] and exits == 1

    def test_stops_after_the_first_hit_inside_a_tool(self, app):
        exc = _custom_excellon(app, {
            1: {'tooldia': 2.0, 'drills': [Point(5, 5), Point(5, 5.2)], 'slots': [], 'solid_geometry': [],
                'data': {}},
        })
        points, exits = dbl_click(exc, (5.0, 5.1))
        assert points == [pytest.approx((5.0, 5.0))] and exits == 1


def find_pad(grb, point, selected_apids):
    fake = Mock()
    fake.grb_obj = grb
    items = []
    for apid in selected_apids:
        it = Mock()
        it.text.return_value = str(apid)
        items.append(it)
    fake.ui.apertures_table.selectedItems.return_value = items
    return ToolPunchGerber.find_pad(fake, point)


class TestFindPad:
    def test_flash_in_a_selected_aperture(self, gerber):
        assert find_pad(gerber, (5.2, 5.1), [10]) == [{'apid': 10, 'idx': 0}]

    def test_track_and_region_are_not_pads(self, gerber):
        assert find_pad(gerber, (20.0, 10.1), [10, 11, 0]) == []
        assert find_pad(gerber, (5.0, 24.0), [0, 10]) == []

    def test_index_is_the_record_position_after_other_records(self):
        grb = _gerber_like({7: {'geometry': [
            {'solid': box(0, 0, 1, 1), 'follow': LineString([(0, 0), (1, 1)])},
            {'clear': box(5, 5, 6, 6), 'follow': Point(5.5, 5.5)},
            {'solid': box(2, 2, 4, 4), 'follow': Point(3, 3)},
        ]}})
        assert find_pad(grb, (3.0, 3.0), [7]) == [{'apid': 7, 'idx': 2}]
        assert find_pad(grb, (5.5, 5.5), [7]) == []      # clear record
        assert find_pad(grb, (0.5, 0.5), [7]) == []      # route
