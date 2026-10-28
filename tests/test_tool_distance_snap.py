"""Characterization of the 'app' snapping branch of ``ToolDistance.Distance.snap_handler``: the center of the Gerber
pad (flash) or of the Excellon drill / slot under the cursor. It must pass before and after the move to
``feature_access.iter_features``.
"""

from unittest.mock import MagicMock, Mock
from types import MethodType, SimpleNamespace

import pytest

from appPlugins.ToolDistance import Distance
from shapely import Point, MultiLineString, box

from tests.helpers_objects import app, excellon, gerber, _headless_qt, make_excellon, make_gerber  # noqa: F401,E402


def snap(app_objects, pos):
    """Run the unbound snap_handler with a fake tool; return (returned position, jump location or None)."""
    fake_app = MagicMock()
    fake_app.collection.get_list.return_value = app_objects
    fake_app.options = {}
    fake_app.defaults = {}
    fake_tool = Mock()
    fake_tool.original_call_source = 'app'
    fake_tool.app = fake_app
    fake_tool._snap_cache = {}
    fake_tool.resolve_snap = MethodType(Distance.resolve_snap, fake_tool)
    fake_tool.update_cursor = MethodType(Distance.update_cursor, fake_tool)
    fake_tool.ui.big_cursor_cb.get_value.return_value = False
    result = Distance.snap_handler(fake_tool, pos)
    jumps = fake_app.on_jump_to.call_args_list
    assert len(jumps) <= 1
    return result, (jumps[0].kwargs['custom_location'] if jumps else None)


@pytest.fixture
def objs(gerber, excellon):
    return [gerber, excellon]


class TestGerberPads:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), (5.0, 5.0)),         # C flash
        ((10.3, 5.1), (10.0, 5.0)),       # R flash
        ((15.4, 5.0), (15.0, 5.0)),       # O flash (also near drill T1 at 15,5: same center)
        ((20.1, 5.1), (20.0, 5.0)),       # P flash
    ])
    def test_click_inside_a_pad_snaps_to_its_center(self, objs, click, center):
        result, jump = snap([objs[0]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

    def test_click_on_a_track_does_not_snap(self, objs):
        result, jump = snap([objs[0]], (20.0, 10.1))      # inside the C1 track, not a flash
        assert result == (20.0, 10.1) and jump is None

class TestExcellon:
    @pytest.mark.parametrize('click, center', [
        ((5.1, 12.1), (5.0, 12.0)),       # T2 drill
    ])
    def test_click_inside_a_drill(self, objs, click, center):
        result, jump = snap([objs[1]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

    def test_click_inside_a_slot_snaps_to_the_slot_centroid(self, objs):
        result, jump = snap([objs[1]], (24.0, 12.2))      # T2 slot (20,12)-(28,12)
        assert result == pytest.approx((24.0, 12.0))
        assert jump == pytest.approx((24.0, 12.0))

class TestBothObjects:
    def test_overlapping_pad_and_drill_snap_to_the_same_center(self, objs):
        result, jump = snap(objs, (5.2, 5.1))             # C flash and T1 drill both at (5, 5)
        assert result == pytest.approx((5.0, 5.0))

    def test_the_closest_center_wins(self, app, objs):
        # a second Excellon drill whose disc covers the click but whose center is further away
        exc = make_excellon(app, name='exc2')
        from shapely.geometry import Point
        exc.tools = {1: {'tooldia': 1.0, 'drills': [Point(5.3, 5.0)], 'slots': [], 'solid_geometry': [], 'data': {}}}
        assert exc.create_geometry() is None
        result, jump = snap([objs[0], exc], (5.1, 5.0))   # gerber pad center (5, 5) is closer than (5.3, 5)
        assert result == pytest.approx((5.0, 5.0))
        result, jump = snap([objs[0], exc], (5.25, 5.0))  # now the drill center (5.3, 5) is closer
        assert result == pytest.approx((5.3, 5.0))


def test_snap_with_big_cursor_keeps_the_user_cursor_color(objs):
    # The cursor color from Preferences must survive a snap, so that on_exit restores it (not black).
    fake_app = MagicMock()
    fake_app.collection.get_list.return_value = [objs[0]]
    fake_app.options = {}
    fake_app.defaults = {}
    fake_app.plotcanvas = SimpleNamespace(cursor_color='#FF0000FF')
    fake_tool = Mock()
    fake_tool.original_call_source = 'app'
    fake_tool.app = fake_app
    fake_tool._snap_cache = {}
    fake_tool.resolve_snap = MethodType(Distance.resolve_snap, fake_tool)
    fake_tool.update_cursor = MethodType(Distance.update_cursor, fake_tool)
    fake_tool.cursor_color_memory = '#FF0000FF'
    fake_tool.ui.big_cursor_cb.get_value.return_value = True

    assert Distance.snap_handler(fake_tool, (5.2, 5.1)) == pytest.approx((5.0, 5.0))
    assert fake_app.plotcanvas.cursor_color == '#FF0000FF'
    assert fake_tool.cursor_color_memory == '#FF0000FF'
    assert fake_app.app_cursor.set_data.call_args.kwargs['edge_color'] == '#FF0000FF'


def test_multipoint_motion_replaces_the_first_preview_without_losing_committed_lines(_headless_qt):
    from vispy.gloo.context import FakeCanvas
    from appGUI.VisPyVisuals import ShapeCollectionVisual
    from shapely.geometry import LineString

    _canvas = FakeCanvas()
    tool = Mock()
    tool.decimals = 4
    tool.active = True
    tool.app.call_source = 'measurement'
    tool.sel_shapes = ShapeCollectionVisual(pool=None)
    tool.ui.multipoint_cb.get_value.return_value = True
    tool.app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    tool.update_position_info.side_effect = lambda pos: pos
    tool.points = [(0, 0), (1, 1)]
    tool.total_distance = 1.0
    tool.update_distance.return_value = 1.0
    tool.add_utility_shape = MethodType(Distance.add_utility_shape, tool)
    tool.delete_utility_shape = MethodType(Distance.delete_utility_shape, tool)
    tool.add_utility_shape((1, 1), (10, 10))
    # The first preview has key zero. Committed segments must survive its replacement.
    tool.sel_shapes.add(LineString([(0, 0), (1, 1)]), color='blue', update=True, layer=0)
    Distance.on_mouse_move(tool, SimpleNamespace(pos=(2, 3), is_dragging=False))
    assert tool.sel_shapes.bounds(axis=0) == pytest.approx((0, 2))
    assert tool.sel_shapes.bounds(axis=1) == pytest.approx((0, 3))
    tool.app.log.error.assert_not_called()


@pytest.fixture
def measuring(_headless_qt):
    from PyQt6 import QtCore
    from vispy.gloo.context import FakeCanvas
    from appGUI.VisPyVisuals import ShapeCollectionVisual

    class ObjectSignals(QtCore.QObject):
        object_changed = QtCore.pyqtSignal(object)

    canvas = FakeCanvas()
    fake_app = MagicMock()
    fake_app.decimals = 4
    fake_app.app_units = 'MM'
    fake_app.options = {}
    fake_app.defaults = {}
    fake_app.call_source = 'app'
    fake_app.dec_format.side_effect = lambda value, decimals: round(value, decimals)
    fake_app.plotcanvas.new_shape_collection.return_value = ShapeCollectionVisual(pool=None)
    fake_app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    fake_app.plotcanvas.cursor_color = '#FF0000FF'
    fake_app.collection.get_list.return_value = []
    fake_app.grid_status.return_value = False
    fake_app.ui.grid_snap_btn.isChecked.return_value = False
    fake_app.app_obj = ObjectSignals()
    tool = Distance(fake_app)
    tool.init_plugin()
    tool.active = True
    tool.ui_connect()
    fake_app.call_source = 'measurement'
    yield tool
    tool.on_exit()
    tool.deleteLater()
    del canvas


def release(tool, pos=(0, 0), button=1, dragging=False):
    tool.on_mouse_click_release(SimpleNamespace(pos=pos, button=button, is_dragging=dragging))


def move(tool, pos):
    tool.on_mouse_move(SimpleNamespace(pos=pos, is_dragging=False))
    tool.app.log.error.assert_not_called()


def flash(center=(0, 0), solid=None):
    center = Point(center)
    solid = center.buffer(1) if solid is None else solid
    return SimpleNamespace(kind='gerber', obj_options={'plot': True}, solid_geometry=[solid],
                           tools={1: {'geometry': [{'solid': solid, 'follow': center}]}})


@pytest.mark.parametrize('points,total', [([(0, 0), (3, 4)], 5)])
def test_multipoint_finish_discards_uncommitted_preview(measuring, points, total):
    measuring.ui.multipoint_cb.set_value(True)
    for pos in points:
        release(measuring, pos)
    move(measuring, (6, 8))
    release(measuring, button=2)
    assert float(measuring.ui.total_distance_entry.get_value()) == total
    assert str(points[-1][0] if points else 0) in measuring.ui.stop_entry.get_value()
    assert int(measuring.ui.point_count_entry.get_value()) == len(points)
    assert not measuring.active



def test_excellon_editor_selects_containing_drill_not_nearest_arm(measuring):
    from appEditors.appGeoEditor import DrawToolShape

    def cross(x, y, diameter):
        r = diameter / 2
        return DrawToolShape(MultiLineString([[(x-r, y), (x+r, y)], [(x, y-r), (x, y+r)]]))

    empty = Distance.make_storage()
    drills = Distance.make_storage()
    drills.insert(cross(0, 0, 20))
    drills.insert(cross(2, 2, .2))
    measuring.original_call_source = 'exc_editor'
    measuring.app.exc_editor.storage_dict = {1: empty, 2: drills}
    assert measuring.snap_handler((.2, .2)) == pytest.approx((0, 0))
    assert measuring.snap_handler((10, 0)) == pytest.approx((0, 0))
    measuring.app.exc_editor.storage_dict = {1: empty}
    assert measuring.snap_handler((.2, .2)) == (.2, .2)


def test_gerber_editor_overlaps_choose_nearest_center_including_boundary(measuring):
    measuring.original_call_source = 'grb_editor'
    measuring.app.grb_editor.storage_dict = {1: {'geometry': [
        SimpleNamespace(geo={'solid': box(-2, -2, 2, 2), 'follow': Point(0, 0)}),
        SimpleNamespace(geo={'solid': box(-1, -1, 3, 3), 'follow': Point(1, 1)})]}}
    assert measuring.snap_handler((.8, .8)) == pytest.approx((1, 1))
    assert measuring.snap_handler((-2, 0)) == pytest.approx((0, 0))
    assert measuring.active


def test_app_snap_respects_boundary_visibility_and_composed_copper(measuring):
    obj = flash(solid=box(-1, -1, 1, 1))
    measuring.app.collection.get_list.return_value = [obj]
    assert measuring.snap_handler((1, 0)) == (0, 0)
    obj.obj_options['plot'] = False
    assert measuring.snap_handler((.2, .2)) == (.2, .2)
    obj.obj_options['plot'] = True
    obj.solid_geometry = [box(-1, -1, 0, 1)]
    assert measuring.snap_handler((.2, .2)) == (.2, .2)
    assert measuring.snap_handler((-.2, .2)) == (0, 0)
    obj.solid_geometry = []
    assert measuring.snap_handler((-.2, .2)) == (-.2, .2)


def test_undo_multipoint_removes_segment_and_updates_feedback(measuring):
    measuring.ui.multipoint_cb.set_value(True)
    for pos in [(0, 0), (3, 4), (6, 8)]:
        release(measuring, pos)
    measuring.undo_last_point()
    assert measuring.points == [(0, 0), (3, 4)]
    assert float(measuring.ui.total_distance_entry.get_value()) == 5
    assert float(measuring.ui.segment_distance_entry.get_value()) == 5
    assert int(measuring.ui.point_count_entry.get_value()) == 2
    assert measuring.sel_shapes.bounds(axis=0) == pytest.approx((0, 3))
    move(measuring, (3, 8))
    assert float(measuring.ui.total_distance_entry.get_value()) == 9
    measuring.undo_last_point()
    measuring.undo_last_point()
    measuring.undo_last_point()
    assert measuring.points == []
    assert float(measuring.ui.total_distance_entry.get_value()) == 0
    assert int(measuring.ui.point_count_entry.get_value()) == 0


def test_in_place_object_change_invalidates_snap_geometry(measuring):
    obj = flash()
    measuring.app.collection.get_list.return_value = [obj]
    assert measuring.snap_handler((.2, .2)) == (0, 0)
    obj.tools[1]['geometry'][0] = {'solid': box(4, 4, 6, 6), 'follow': Point(5, 5)}
    obj.solid_geometry[:] = [box(4, 4, 6, 6)]
    # The application's object-changed signal is the invalidation boundary for in-place edits.
    measuring.app.app_obj.object_changed.emit(obj)
    assert measuring.snap_handler((.2, .2)) == (.2, .2)
    assert measuring.snap_handler((5.2, 5.2)) == (5, 5)






def test_pending_gerber_transform_updates_measurement_snap_and_history(measuring, _headless_qt):
    from concurrent.futures import ThreadPoolExecutor
    from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
    from test_gerber_editor_pick import make_editor, aperture, pad

    editor = make_editor({10: aperture([pad(0, 0, 1)])})
    editor.selected = list(editor.storage_dict[10]['geometry'])
    # Queue an ordinary editor transform before starting measurement. Its GUI-thread
    # result can arrive after Distance has disconnected the editor's mouse handlers.
    measuring.on_exit()
    assert queue_transform(SimpleNamespace(app=editor.app, draw_app=editor), 'offset', ('X', 5)) != 'fail'
    job = editor.app.worker_task.emit.call_args.args[0]
    measuring.app.grb_editor = editor
    measuring.app.call_source = 'grb_editor'
    measuring.app.ui.notebook.count.return_value = 0
    measuring.on_start_measuring()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job['fcn'], *job['params']).result()
    _headless_qt.processEvents()
    assert measuring.resolve_snap((.2, .2)) == (.2, .2)
    assert measuring.resolve_snap((5.2, .2)) == pytest.approx((5, 0))
    assert editor.undo()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    assert measuring.resolve_snap((5.2, .2)) == (5.2, .2)
    assert editor.redo()
    assert measuring.resolve_snap((5.2, .2)) == pytest.approx((5, 0))
    measuring.on_exit()
    assert editor.undo()
    measuring.on_start_measuring()
    assert measuring.resolve_snap((.2, .2)) == (0, 0)
    assert measuring.resolve_snap((5.2, .2)) == (5.2, .2)


@pytest.fixture
def object_distance(_headless_qt):
    from appPlugins.ToolObjectDistance import ObjectDistance

    fake_app = MagicMock()
    fake_app.decimals = 4
    fake_app.app_units = 'MM'
    fake_app.resource_location = 'assets/resources'
    fake_app.call_source = 'app'
    fake_app.plugin_tab_locked = False
    fake_app.dec_format.side_effect = lambda value, decimals: round(value, decimals)
    fake_app.ui.notebook.count.return_value = 0
    tool = ObjectDistance(fake_app)
    yield tool
    tool.deleteLater()


def object_distance_shift_m(tool, *selected):
    tool.app.collection.get_selected.return_value = list(selected)
    tool.run()
    return float(tool.ui.total_distance_entry.get_value())


def test_object_distance_shift_m_works_after_a_wrong_selection_and_a_reset(object_distance):
    first, second, third = flash((0, 0)), flash((5, 0)), flash((0, 4))
    # 1 object selected: only a warning, but the plugin stayed flagged as running and Shift+M did nothing after it
    object_distance_shift_m(object_distance, first)
    assert object_distance_shift_m(object_distance, first, second) == pytest.approx(3)
    # Reset Tool flagged it as running too
    object_distance.ui.reset_button.click()
    assert object_distance_shift_m(object_distance, first, third) == pytest.approx(2)


def test_object_distance_measure_button_shows_zero_for_touching_objects(object_distance):
    first, second, overlapping = flash((0, 0)), flash((0, 5)), flash((1.5, 0))
    assert object_distance_shift_m(object_distance, first, second) == pytest.approx(3)
    # the Measure button does not reset the fields: the 0 results were not written and the 3 mm stayed
    object_distance.app.collection.get_selected.return_value = [first, overlapping]
    object_distance.ui.measure_btn.click()
    ui = object_distance.ui
    assert [float(e.get_value()) for e in (ui.total_distance_entry, ui.distance_y_entry, ui.angle_entry)] == [0, 0, 0]
