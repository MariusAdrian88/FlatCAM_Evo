"""GEO-03: construction input must not mutate committed geometry on rejection."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import STRtree
from shapely.geometry import LineString, Polygon, box

from appEditors.cursor_text import show_cursor_text, hide_cursor_text
from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape, DrawToolUtilityShape, FCPath, FCCircle, FCRectangle, FCMove, FCCopy
from appEditors.geo_plugins.GeoCirclePlugin import CircleEditorTool
from appEditors.geo_plugins.GeoPathPlugin import PathEditorTool
from appEditors.geo_plugins.GeoCopyPlugin import CopyEditorTool
from appEditors.geo_plugins.GeoRectanglePlugin import RectangleEditorTool
from appEditors.geo_plugins.GeoTextPlugin import TextInputTool
from appGUI.GUIElements import FCDoubleSpinner, NumericalEvalEntry
from appGUI.MainGUI import MainGUI
from tests.test_geo_editor_history import editor_with_history, run_worker


NAMES = dict(zip([FCPath, FCCircle, FCRectangle, FCMove, FCCopy], ['path', 'circle', 'rectangle', 'move', 'copy']))
TOOLS = [FCPath, FCCircle, FCMove]


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def value(number):
    return SimpleNamespace(get_value=lambda: number, set_value=Mock())


def input_tool(cls, points=((0.0, 0.0),), length=5.0):
    original = DrawToolShape(LineString([(0, 0), (1, 0)]))
    editor = editor_with_history([original], [original])
    editor.app.mouse_pos, editor.app.on_jump_to = (3.0, 4.0), Mock()
    editor.app.jump_signal = Mock()
    editor.app.plotcanvas = SimpleNamespace(text_cursor=SimpleNamespace(parent=object()),
                                          view=SimpleNamespace(camera=SimpleNamespace(zoom_callback=Mock())))
    editor.app.ui = SimpleNamespace(notebook=Mock())
    editor.app.call_source, editor.app.geo_editor, editor.app.collection = 'geo_editor', editor, Mock()
    editor.app.options['geometry_editor_milling_type'] = 'cv'
    editor.x, editor.y = editor.app.mouse_pos
    editor.snap_x, editor.snap_y = editor.app.mouse_pos
    editor.snap = lambda x, y: (x, y)
    editor.in_action = bool(points)
    editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
    editor.draw_utility_geometry = Mock()
    editor.select_tool = Mock()
    tool = cls.__new__(cls)
    tool.draw_app, tool.app, tool.storage = editor, editor.app, editor.storage
    tool.name, tool.points, tool.complete = NAMES[cls], list(points), False
    tool.geometry, tool.util_geo = None, None
    tool.new_segment, tool.cursor_data_control = False, True
    tool.draw_cursor_data = Mock()
    tool.origin = points[0] if points else None
    tool.destination, tool.has_selection, tool.sel_limit = None, True, 30
    tool.steps_per_circ = 16
    ui = SimpleNamespace(x_entry=value(0.0), y_entry=value(0.0), angle_entry=value(0.0),
                         radius_link_btn=SimpleNamespace(isChecked=lambda: True),
                         radius_x_entry=value(length), radius_y_entry=value(length),
                         mode_radio=value('n'), corner_radio=value('s'), radius_entry=value(0.0),
                         add_button=QtWidgets.QPushButton(), clear_btn=QtWidgets.QPushButton(),
                         path_tool_frame=QtWidgets.QWidget(), copy_frame=QtWidgets.QWidget(),
                         circle_frame=QtWidgets.QWidget(), rect_frame=QtWidgets.QWidget())
    panel = SimpleNamespace(length=length, width=length, radius_x=length, radius_y=length, ui=ui,
                            on_tab_close=Mock(), disconnect_signals=Mock())
    setattr(tool, {'path': 'path_tool', 'circle': 'circle_tool', 'rectangle': 'rect_tool',
                   'move': 'move_tool', 'copy': 'copy_tool'}[tool.name], panel)
    tool.ui = ui
    editor.active_tool = tool
    return editor, tool, panel, original


def dispatch(editor, key, monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))
    gui = SimpleNamespace(app=editor.app, geo_select_btn=SimpleNamespace(isChecked=lambda: False))
    event = key if isinstance(key, int) else SimpleNamespace(key=key, native=SimpleNamespace())
    return MainGUI.keyPressEvent(gui, event)


def assert_uncommitted(editor, tool, original):
    assert list(editor.storage.get_objects()) == [original]
    assert original.geo.equals_exact(LineString([(0, 0), (1, 0)]), 0)
    assert not tool.complete
    assert not editor.undo()




@pytest.mark.parametrize('cls', TOOLS)
@pytest.mark.parametrize('number', [None, float('nan')])
def test_invalid_numeric_input_keeps_construction_and_history(cls, number, monkeypatch):
    editor, tool, panel, original = input_tool(cls, length=number)
    dispatch(editor, 'Enter', monkeypatch)
    assert_uncommitted(editor, tool, original)
    assert tool.points == [(0.0, 0.0)]
    editor.app.on_jump_to.assert_not_called()
    editor.select_tool.assert_not_called()
    assert editor.app.inform.emit.called






@pytest.mark.parametrize('cls', [FCPath, FCMove, FCCopy])
def test_valid_numeric_projection_and_coordinate_jump(cls, monkeypatch):
    editor, tool, panel, original = input_tool(cls, length=10.0)
    # Path has no width and move/copy use Path/CopyEditorTool, which have no width property.
    del panel.width
    dispatch(editor, 'Enter', monkeypatch)
    editor.app.on_jump_to.assert_called_once_with(custom_location=(6.0, 8.0), fit_center=False)
    if cls is FCPath:
        assert tool.points[-1] == (6.0, 8.0)
        assert_uncommitted(editor, tool, original)
    else:
        assert editor.selected[0].geo.bounds == (6.0, 8.0, 7.0, 8.0) if cls is FCMove else \
            len(list(editor.storage.get_objects())) == 2
        assert editor.undo()
        assert list(editor.storage.get_objects()) == [original]


@pytest.mark.parametrize('cls', TOOLS)
def test_coordinate_jump_is_preserved(cls, monkeypatch):
    editor, tool, panel, original = input_tool(cls)
    dispatch(editor, 'J', monkeypatch)
    editor.app.on_jump_to.assert_called_once_with()
    assert_uncommitted(editor, tool, original)


@pytest.mark.parametrize('cls', TOOLS)
def test_backspace_only_removes_construction_points(cls, monkeypatch):
    editor, tool, panel, original = input_tool(cls)
    dispatch(editor, 'Backspace', monkeypatch)
    assert_uncommitted(editor, tool, original)
    assert tool.points == ([] if cls is FCPath else [(0.0, 0.0)])
    if cls is FCPath:
        assert not editor.in_action


@pytest.mark.parametrize('cls', TOOLS)
@pytest.mark.parametrize('route', ['escape', 'switch'])
def test_cancel_or_switch_cleans_preview_and_connections_without_committing(cls, route, monkeypatch):
    editor, tool, panel, original = input_tool(cls)
    editor.utility = [DrawToolUtilityShape(box(0, 0, 3, 4))]
    stale_callback = Mock()
    panel.ui.add_button.clicked.connect(stale_callback)
    panel.ui.clear_btn.clicked.connect(stale_callback)
    if cls in (FCPath, FCMove, FCCopy):
        disconnect = CopyEditorTool.disconnect_signals if cls is FCCopy else PathEditorTool.disconnect_signals
        panel.disconnect_signals = lambda: disconnect(panel)
    editor.tools = {}
    for name in ['select', 'path']:
        button = QtWidgets.QPushButton()
        button.setCheckable(True)
        editor.tools[name] = dict(button=button, constructor=lambda e: SimpleNamespace(name='select'))
    editor.on_tool_select = lambda name: AppGeoEditor.on_tool_select(editor, name)
    editor.select_tool = lambda name: AppGeoEditor.select_tool(editor, name)
    if route == 'escape':
        dispatch(editor, 'Escape', monkeypatch)
    else:
        editor.select_tool('select')
    assert list(editor.storage.get_objects()) == [original]
    assert not editor.undo()
    assert editor.active_tool.name == 'select'
    assert not editor.in_action
    editor.tool_shape.clear.assert_called()
    editor.app.jump_signal.disconnect.assert_called()
    assert editor.app.plotcanvas.text_cursor.parent is None
    assert tool.points == []
    assert editor.selected == [original]
    assert not editor.utility
    button = panel.ui.add_button if cls in (FCCircle, FCRectangle) else panel.ui.clear_btn
    button.click()
    stale_callback.assert_not_called()






@pytest.mark.parametrize('cls', TOOLS)
def test_busy_enter_retains_construction_until_retry(cls, monkeypatch):
    editor, tool, panel, original = input_tool(cls)
    editor.submit_worker(Mock(), [])
    assert dispatch(editor, 'Enter', monkeypatch) == 'fail'
    assert tool.points == [(0.0, 0.0)]
    assert not tool.complete
    assert list(editor.storage.get_objects()) == [original]
    editor.app.on_jump_to.assert_not_called()
    run_worker(editor)








@pytest.mark.parametrize('cls', [FCPath, FCCopy])
@pytest.mark.parametrize('qt_keys', [False, True])
@pytest.mark.parametrize('text, expected', [('2*5', 10.0)])
def test_numeric_expression_keys_use_existing_evaluator(cls, qt_keys, text, expected):
    editor, tool, panel, original = input_tool(cls, length=0.0)
    tool.new_segment = True
    panel.ui.project_line_entry = NumericalEvalEntry()
    panel.ui.project_line_entry.set_value(0.0)

    class LengthPanel:
        length = PathEditorTool.length

    numeric_panel = LengthPanel()
    numeric_panel.ui = panel.ui
    setattr(tool, {'path': 'path_tool', 'move': 'move_tool', 'copy': 'copy_tool'}[tool.name], numeric_panel)
    for char in text:
        tool.on_key(ord(char) if qt_keys else char)
    assert numeric_panel.length == expected
    tool.on_key('Enter')
    location = editor.app.on_jump_to.call_args.kwargs['custom_location']
    assert location == pytest.approx((0.6 * expected, 0.8 * expected))


@pytest.mark.parametrize('cls', [FCCircle, FCRectangle])
@pytest.mark.parametrize('point', [(0.0, 0.0), (float('nan'), 3.0)])
def test_invalid_completion_click_is_non_destructive(cls, point):
    editor, tool, panel, original = input_tool(cls)
    assert tool.click(point) == 'fail'
    assert_uncommitted(editor, tool, original)
    assert tool.points == [(0.0, 0.0)]


@pytest.mark.parametrize('cls', [FCCircle, FCRectangle])
def test_completion_click_without_mouse_move_builds_at_current_point(cls):
    editor, tool, panel, original = input_tool(cls)
    tool.util_geo = box(100, 100, 101, 101)
    tool.click((3.0, 4.0))
    editor.on_shape_complete()
    added = [s for s in editor.storage.get_objects() if s is not original]
    assert len(added) == 1
    assert added[0].geo.bounds == ((-5.0, -5.0, 5.0, 5.0) if cls is FCCircle else (0.0, 0.0, 3.0, 4.0))
    assert editor.undo()


def test_zero_length_path_enter_cannot_commit_degenerate_geometry(monkeypatch):
    editor, tool, panel, original = input_tool(FCPath, length=0.0)
    editor.x, editor.y = (0.0, 0.0)
    assert dispatch(editor, 'Enter', monkeypatch) == 'fail'
    assert_uncommitted(editor, tool, original)
    editor.select_tool.assert_not_called()


def cursor_text_editor(*obstacles):
    # 10 pixels for 1 unit; the pixel y axis points down as on the canvas
    canvas = SimpleNamespace(dpi=72, view=SimpleNamespace(scene=object(), camera=SimpleNamespace()),
                             text_cursor=SimpleNamespace(text='', pos=None, anchors=None, font_size=None, parent=None),
                             translate_coords_2=lambda p: (500 + 10 * p[0], 500 - 10 * p[1], 0, 1),
                             translate_coords=lambda p: ((p[0] - 500) / 10, (500 - p[1]) / 10, 0, 1))
    app = SimpleNamespace(plotcanvas=canvas, decimals=4, app_units='MM', dec_format=lambda v, d: round(v, d))
    tree = STRtree(list(obstacles)) if obstacles else None
    return SimpleNamespace(app=app, get_pick_index=lambda: (tree, list(obstacles))), canvas.text_cursor


@pytest.mark.parametrize('ref, obstacles, anchors, x', [
    (None, (), ('left', 'top'), 3.0),                                   # free: right of the cursor, above it
    ((10.0, 10.0), (), ('right', 'bottom'), -3.0),                      # away from the last point
    (None, (box(4, 0.5, 5, 1.5),), ('left', 'bottom'), 3.0),            # a shape is above: go below
    ((-10.0, -10.0), (box(-1000, -1000, 1000, 1000),), ('left', 'top'), 3.0),  # no free side: away from the point
])
def test_cursor_text_avoids_the_drawn_shape_and_the_shapes_under_it(ref, obstacles, anchors, x):
    draw_app, text = cursor_text_editor(*obstacles)
    show_cursor_text(draw_app, (0.0, 0.0), ref=ref)
    assert text.parent is draw_app.app.plotcanvas.view.scene
    assert text.anchors == anchors
    assert text.pos[0] == pytest.approx(x) and text.pos[1] == pytest.approx(0.0)
    length = '14.1421' if ref else '0.0'
    assert text.text == 'X:   0.0 [mm]\nY:   0.0 [mm]\n\nL:   %s [mm]' % length
    hide_cursor_text(draw_app.app)
    assert text.parent is None


def text_tool(bold=False, italic=False, size='12', parser=None):
    app = SimpleNamespace(app_units='MM', inform=Mock(), log=Mock())
    tool = SimpleNamespace(app=app, font_bold=bold, font_italic=italic, font_name='Arial', text_path=None,
                           f_parse=SimpleNamespace(
                               font_to_geometry=parser or (lambda **kwargs: box(0, 0, 2, 1))),
                           ui=SimpleNamespace(text_input_entry=value('A'), font_size_cb=value(size),
                                              bitmap_smoothing_entry=value(0.5)))
    tool._valid_font_size = lambda: TextInputTool._valid_font_size(tool)
    return tool


STYLE_GEOMETRY = {'regular': box(0, 0, 1, 1), 'bold': box(0, 0, 2, 1),
                  'italic': box(0, 0, 1, 2), 'bi': box(0, 0, 2, 2)}


@pytest.mark.parametrize('bold, italic, style', [(True, False, 'bold')])
def test_geo_text_apply_renders_the_selected_style(bold, italic, style):
    tool = text_tool(bold=bold, italic=italic,
                     parser=lambda **kwargs: STYLE_GEOMETRY[kwargs['font_type']])
    TextInputTool.on_apply_button(tool)
    assert tool.text_path.equals(STYLE_GEOMETRY[style])


@pytest.mark.parametrize('smoothing, height', [(0.5, 1.5)])
def test_geo_text_apply_passes_the_bitmap_smoothing(smoothing, height):
    tool = text_tool(parser=lambda **kwargs: box(0, 0, 2, 1 + kwargs['bitmap_smoothing']))
    tool.ui.bitmap_smoothing_entry = value(smoothing)
    TextInputTool.on_apply_button(tool)
    assert tool.text_path.bounds == (0, 0, 2, height)


def test_geo_text_apply_clears_the_previous_geometry_on_failure():
    def parser(**kwargs):
        raise ValueError('bad size')

    tool = text_tool(parser=parser)
    tool.text_path = box(0, 0, 9, 9)
    TextInputTool.on_apply_button(tool)
    assert tool.text_path is None
    assert tool.app.inform.emit.called


def transformation_tool(geometry=None):
    from appEditors.geo_plugins.GeoTransformationPlugin import TransformEditorTool
    shape = DrawToolShape(box(2, 3, 6, 9) if geometry is None else geometry)
    editor = editor_with_history([shape], [shape])
    editor.transform_shapes = lambda *a, **kw: AppGeoEditor.transform_shapes(editor, *a, **kw)
    editor.app.decimals, editor.app.app_units = 4, 'MM'
    editor.app.options = {'tools_transform_reference': 'Point', 'tools_transform_ref_point': '(10, 20)'}
    defaults = __import__('defaults').AppDefaults()
    editor.app.defaults = defaults
    editor.app.ui = QtWidgets.QWidget()
    editor.app.resource_location = ''
    tool = TransformEditorTool(editor.app, editor)
    return editor, tool, shape


@pytest.mark.parametrize('method', ['on_offy_key'])
def test_transform_y_shortcut_changes_y_only_after_worker(method, monkeypatch):
    import appEditors.geo_plugins.GeoTransformationPlugin as plugin
    editor, tool, shape = transformation_tool()
    monkeypatch.setattr(plugin, 'FCInputDoubleSpinner', lambda **kw: SimpleNamespace(
        set_icon=lambda icon: None, get_value=lambda: (10.0, True)))
    editor.app.call_source, editor.app.geo_editor = 'geo_editor', editor
    editor.app.collection = Mock()
    editor.transform_tool = tool
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers', staticmethod(lambda:
        QtCore.Qt.KeyboardModifier.AltModifier if method == 'on_offy_key'
        else QtCore.Qt.KeyboardModifier.ShiftModifier))
    MainGUI.keyPressEvent(SimpleNamespace(app=editor.app), SimpleNamespace(key='Y', native=SimpleNamespace()))
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    run_worker(editor)
    changed = editor.selected[0].geo
    assert changed.bounds[0::2] == pytest.approx(shape.geo.bounds[0::2])
    assert changed.bounds[1::2] != shape.geo.bounds[1::2]
    assert any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    assert editor.undo() and editor.selected == [shape]


def test_transform_reference_origin_corners_and_point_visibility():
    editor, tool, shape = transformation_tool()
    assert not tool.ui.point_entry.isHidden()
    assert tool.on_calculate_reference(ref_index=0) == (0, 0)
    assert tool.ui.ref_combo.count() == 7
    for index, point in [(3, (2, 3)), (4, (6, 3)), (5, (6, 9)), (6, (2, 9))]:
        tool.ui.ref_combo.setCurrentIndex(index)
        assert tool.ui.point_entry.isHidden()
        assert tool.on_calculate_reference() == point
    tool.ui.ref_combo.setCurrentIndex(2)
    assert not tool.ui.point_entry.isHidden()


@pytest.mark.parametrize('action,args,failure', [
    ('on_rotate_action', (20, (0, 0)), 'fail')])
def test_transform_rejection_never_announces_success(action, args, failure):
    editor, tool, shape = transformation_tool()
    def reject(*args, **kwargs):
        if failure == 'exception':
            raise ValueError('Rejected')
        return 'fail'
    editor.transform_shapes = reject
    assert getattr(tool, action)(*args) == 'fail'
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)
    assert editor.selected == [shape]


@pytest.mark.parametrize('method,expected', [('on_rotate', (3, -6, 9, -2))])
def test_transform_rotation_is_clockwise_and_mirror_reflects_opposite_coordinate(method, expected):
    editor, tool, shape = transformation_tool()
    if method == 'on_rotate':
        tool.on_rotate(val=90, ref=0)
    else:
        getattr(tool, method)(ref=0)
    run_worker(editor)
    assert editor.selected[0].geo.bounds == pytest.approx(expected)


def test_transform_buffer_offsets_a_polygon_outline():
    # the editor loads a polygon as its outline (a closed line): 'Buffer D' moves the outline by the distance
    for distance, expected in ((1, (1, 2, 7, 10)), (-1, (3, 4, 5, 8))):
        editor, tool, shape = transformation_tool(box(2, 3, 6, 9).exterior)
        assert tool.on_buffer_action(distance, 2) is None
        assert list(editor.storage.get_objects()) == editor.selected
        assert len(editor.selected) == 1 and editor.selected[0].geo.geom_type == 'LinearRing'
        assert editor.selected[0].geo.bounds == pytest.approx(expected)


def test_transform_buffer_that_erodes_a_shape_away_keeps_it():
    editor, tool, shape = transformation_tool(box(2, 3, 6, 9).exterior)
    assert tool.on_buffer_action(-3, 2) == 'fail'
    assert list(editor.storage.get_objects()) == [shape] and editor.selected == [shape]
    assert not any('[success]' in c.args[0] for c in editor.app.inform.emit.call_args_list)


def panel_add(cls, linked=True, **values):
    editor = editor_with_history()
    ui = SimpleNamespace(**{name: value(number) for name, number in values.items()})
    ui.radius_link_btn = SimpleNamespace(isChecked=lambda: linked)
    cls.on_add(SimpleNamespace(draw_app=editor, app=editor.app, ui=ui, mode='add'))
    return [shape.geo for shape in editor.storage.get_objects()]


def test_rectangle_panel_keeps_the_size_when_the_corner_radius_does_not_fit():
    # the default corners are Round with radius 1; when they do not fit the corners are square (as with the mouse)
    for length, width, rounded in ((10, 1, False), (-10, 4, True), (10, 4, True)):
        added = panel_add(RectangleEditorTool, anchor_radio='c', x_entry=0, y_entry=0, corner_radio='r',
                          radius_entry=1, length_entry=length, width_entry=width)
        assert len(added) == 1
        assert added[0].bounds == pytest.approx((-5, -width / 2, 5, width / 2))
        assert (Polygon(added[0]).area < 10 * width) == rounded


def test_circle_panel_adds_a_circle_with_the_y_radius_field_left_at_0():
    # in the default (linked) circle mode the Y radius field is disabled and does not follow the X field
    circle = dict(x_entry=10, y_entry=10, radius_y_entry=0, angle_entry=0)
    added = panel_add(CircleEditorTool, radius_x_entry=5, **circle)
    assert len(added) == 1 and added[0].bounds == pytest.approx((5, 5, 15, 15))
    assert panel_add(CircleEditorTool, radius_x_entry=0, **circle) == []


def test_circle_panel_rotates_an_ellipse_like_the_circle_tool():
    from shapely import Point
    from shapely.affinity import rotate, scale
    added = panel_add(CircleEditorTool, linked=False, x_entry=0, y_entry=0, radius_x_entry=4, radius_y_entry=1,
                      angle_entry=30)
    # the preview and the Enter path of FCCircle, which write the angle field, rotate by +angle (counter-clockwise)
    expected = rotate(scale(Point(0, 0).buffer(1), 4, 1), 30)
    assert len(added) == 1 and Polygon(added[0]).symmetric_difference(expected).area < 1e-9
