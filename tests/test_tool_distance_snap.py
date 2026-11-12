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
    ])
    def test_click_inside_a_pad_snaps_to_its_center(self, objs, click, center):
        result, jump = snap([objs[0]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

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

@pytest.fixture
def measuring(_headless_qt):
    from PyQt6 import QtCore
    from vispy.gloo.context import FakeCanvas

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


def test_pending_gerber_transform_updates_measurement_snap_and_history(measuring, _headless_qt):
    from concurrent.futures import ThreadPoolExecutor
    from appEditors.grb_plugins.GrbTransformationPlugin import queue_transform
    from test_gerber_editor import make_editor, aperture, pad

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


class FakeDimensionLines:
    """Stands in for appGUI.DimensionVisual.DimensionLines; keeps what would be on the canvas."""

    def __init__(self):
        self.segments = []

    def set_data(self, segments):
        self.segments = list(segments)

    def clear(self):
        self.segments = []

    def set_color(self, color):
        pass

    def set_style(self, line_width=None, font_size=None, bold=None):
        pass

    def refresh(self):
        pass

    def delete(self):
        self.segments = []


@pytest.fixture
def dist(measuring):
    measuring.dims = FakeDimensionLines()
    return measuring


def total(tool):
    return tool.ui.total_distance_entry.text()


def test_right_click_closes_the_measurement_and_keeps_the_tab_and_the_tool(dist):
    release(dist, (0, 0))
    release(dist, (3, 4))
    release(dist, button=2)
    assert dist.active
    assert total(dist) == '5.0000'
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000')]


def test_second_right_click_stops_the_tool_and_the_tab_keeps_the_last_result(dist):
    notebook = dist.app.ui.notebook
    dist.app.ui.plugin_scroll_area.widget.return_value = dist
    notebook.indexOf.return_value = 2
    release(dist, (0, 0))
    release(dist, (3, 4))
    release(dist, button=2)
    notebook.removeTab.assert_not_called()
    release(dist, button=2)
    notebook.removeTab.assert_called_once_with(2)      # the Plugin tab closes
    assert not dist.active
    assert total(dist) == '5.0000'
    assert dist.dims.segments == []
    assert dist.ui.measure_btn.isEnabled()


def test_left_click_after_a_close_clears_the_tab_and_previews_the_live_length(dist):
    release(dist, (0, 0))
    release(dist, (3, 4))
    release(dist, button=2)
    release(dist, (10, 10))
    assert total(dist) == '0.0000'
    assert dist.ui.point_count_entry.text() == '1'
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000')]
    move(dist, (10, 13))
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000'), ((10, 10), (10, 13), '3.0000')]
    assert total(dist) == '3.0000'
    move(dist, (16, 18))
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000'), ((10, 10), (16, 18), '10.0000')]


def test_single_mode_third_left_click_starts_a_new_measurement(dist):
    release(dist, (0, 0))
    release(dist, (3, 4))
    move(dist, (9, 9))
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000')]
    release(dist, (1, 1))
    assert dist.active
    assert dist.ui.point_count_entry.text() == '1'
    release(dist, (1, 3))
    assert total(dist) == '2.0000'
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000'), ((1, 1), (1, 3), '2.0000')]
    # a cancelled start removes only its own graphics; the stop of the tool removes all
    release(dist, (7, 7))
    release(dist, button=2)
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000'), ((1, 1), (1, 3), '2.0000')]
    release(dist, button=2)
    assert not dist.active
    assert dist.dims.segments == []


def test_right_click_on_a_lone_start_point_cancels_it(dist):
    release(dist, (0, 0))
    move(dist, (3, 4))
    release(dist, button=2)
    assert dist.active
    assert total(dist) == '0.0000'
    assert dist.dims.segments == []


def test_multipoint_right_click_keeps_committed_segments_and_drops_the_preview(dist):
    dist.ui.multipoint_cb.set_value(True)
    release(dist, (0, 0))
    release(dist, (3, 4))
    move(dist, (3, 10))
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000'), ((3, 4), (3, 10), '6.0000')]
    assert total(dist) == '11.0000'
    release(dist, button=2)
    assert dist.active
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000')]
    assert total(dist) == '5.0000'


def test_right_drag_pan_neither_closes_nor_stops(dist):
    release(dist, (0, 0))
    release(dist, (3, 4))
    dist.on_mouse_move(SimpleNamespace(pos=(5, 5), is_dragging=True))
    release(dist, (6, 6), button=2)
    release(dist, button=2)
    assert dist.active
    assert dist.dims.segments == [((0, 0), (3, 4), '5.0000')]


def test_editor_exit_stops_a_running_measurement(dist):
    from appMain import App
    dist.on_exit()
    app = dist.app
    app.call_source = 'grb_editor'
    app.distance_tool = dist
    app.ui.notebook.count.return_value = 0
    dist.on_start_measuring()
    release(dist, (0, 0))
    release(dist, (3, 4))
    app.defaults = MagicMock()
    App.on_editing_finished(app, cleanup=True)
    assert not dist.active
    assert dist.dims.segments == []
    assert app.command_active is None



def trace_obj(points, size=0.2, clear=None, copper=None):
    from shapely import LineString
    line = LineString(points)
    solid = line.buffer(size / 2.0)
    records = [{'solid': solid, 'follow': line}]
    if clear is not None:
        records.append({'clear': clear})
    return SimpleNamespace(kind='gerber', obj_options={'plot': True},
                           solid_geometry=[solid.difference(clear)] if clear is not None else [solid],
                           tools={1: {'type': 'C', 'size': size, 'geometry': records}})


class TestVertexSnap:
    def test_snaps_to_end_points_and_inner_vertices(self, measuring):
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        measuring.app.collection.get_list.return_value = [trace_obj([(0, 0), (10, 0), (10, 5)])]
        assert measuring.resolve_snap((0.05, 0.02), center=False, vertex=True) == pytest.approx((0, 0))
        assert measuring.resolve_snap((10.04, -0.03), center=False, vertex=True) == pytest.approx((10, 0))
        assert measuring.resolve_snap((9.98, 5.02), center=False, vertex=True) == pytest.approx((10, 5))
        # beyond the half width (0.1) and the screen tolerance (0.01): no snap
        assert measuring.resolve_snap((5, 0.02), center=False, vertex=True) == (5, 0.02)
        assert measuring.resolve_snap((0.5, 0), center=False, vertex=True) == (0.5, 0)

    def test_screen_tolerance_widens_the_radius_for_thin_traces(self, measuring):
        measuring.app.collection.get_list.return_value = [trace_obj([(0, 0), (10, 0)], size=0.02)]
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        assert measuring.resolve_snap((0.05, 0), center=False, vertex=True) == (0.05, 0)
        measuring.app.plotcanvas.pixel_size.return_value = 0.01      # 10 px = 0.1
        assert measuring.resolve_snap((0.05, 0), center=False, vertex=True) == pytest.approx((0, 0))

    def test_off_means_no_vertex_snap(self, measuring):
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        measuring.app.collection.get_list.return_value = [trace_obj([(0, 0), (10, 0)])]
        assert measuring.resolve_snap((0.05, 0), center=True, vertex=False) == (0.05, 0)

    def test_a_vertex_erased_by_a_clear_object_is_not_a_target(self, measuring):
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        measuring.app.collection.get_list.return_value = [
            trace_obj([(0, 0), (10, 0)], clear=box(-1, -1, 1, 1))]
        assert measuring.resolve_snap((0.05, 0), center=False, vertex=True) == (0.05, 0)
        assert measuring.resolve_snap((9.95, 0), center=False, vertex=True) == pytest.approx((10, 0))

    def test_the_pad_center_wins_over_a_vertex(self, measuring):
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        trace = trace_obj([(0, 0), (10, 0)])
        pad = flash(center=(0.5, 0), solid=Point(0.5, 0).buffer(0.6))
        pad.solid_geometry = trace.solid_geometry + pad.solid_geometry
        pad.tools[2] = trace.tools[1]
        measuring.app.collection.get_list.return_value = [pad]
        assert measuring.resolve_snap((0.05, 0), center=True, vertex=True) == pytest.approx((0.5, 0))
        assert measuring.resolve_snap((0.05, 0), center=False, vertex=True) == pytest.approx((0, 0))

    def test_the_toggles_gate_the_snap_and_release_the_grid(self, measuring):
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        measuring.app.collection.get_list.return_value = [trace_obj([(0, 0), (10, 0)])]
        measuring.ui.snap_center_cb.set_value(False)
        measuring.ui.snap_vertex_cb.set_value(False)
        assert measuring.update_position_info((0.05, 0)) == (0.05, 0)
        measuring.ui.snap_vertex_cb.set_value(True)
        assert measuring.update_position_info((0.05, 0)) == pytest.approx((0, 0))
        measuring.app.ui.grid_snap_btn.isChecked.return_value = True
        measuring.on_snap_vertex_toggled(True)
        measuring.app.ui.grid_snap_btn.trigger.assert_called()

    def test_grb_editor_trace_vertices_and_edits(self, measuring, _headless_qt):
        from shapely import LineString
        from test_gerber_editor import make_editor, aperture
        from appEditors.appGerberEditor import DrawToolShape
        line = LineString([(0, 0), (10, 0)])
        trace = DrawToolShape({'solid': line.buffer(0.1), 'follow': line})
        editor = make_editor({10: aperture([trace])})
        measuring.app.grb_editor = editor
        measuring.original_call_source = 'grb_editor'
        measuring.app.plotcanvas.pixel_size.return_value = 0.001
        assert measuring.resolve_snap((9.97, 0.02), center=False, vertex=True) == pytest.approx((10, 0))
        assert measuring.resolve_snap((9.97, 0.02), center=False, vertex=False) == (9.97, 0.02)
        editor.storage_dict[10]['geometry'].clear()
        editor.geometry_revision += 1
        assert measuring.resolve_snap((9.97, 0.02), center=False, vertex=True) == (9.97, 0.02)


@pytest.mark.parametrize('bad', ['', '#12', 'nonsense', None])
def test_a_bad_measure_color_falls_back_to_the_default(measuring, bad):
    from vispy.color import Color
    for key in ('tools_dist_app_color_light', 'tools_dist_app_color_dark',
                'tools_dist_editor_color_light', 'tools_dist_editor_color_dark'):
        measuring.app.options[key] = bad
    color = measuring.dimension_color()
    Color(color)
    from defaults import AppDefaults
    assert color == AppDefaults.factory_defaults[
        'tools_dist_app_color_dark' if color.upper().startswith('#FFFFFF') else 'tools_dist_app_color_light']
