import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore

from appEditors.appGerberEditor import RegionEditorGrb, TrackEditorGrb
from appEditors.editor_corner import corner_point


# ---------------------------------------------------------------------------------------------------------------------
# corner_point
# ---------------------------------------------------------------------------------------------------------------------


def test_corner_point_equal_to_an_endpoint_is_dropped():
    assert corner_point(3, (0.0, 0.0), (0.0, 4.0)) is None
    assert corner_point(4, (0.0, 0.0), (4.0, 0.0)) is None
    assert corner_point(3, (0.0, 0.0), (4.0, 0.0)) is None
    assert corner_point(1, (0.0, 0.0), (4.0, 1e-12)) is None


# ---------------------------------------------------------------------------------------------------------------------
# Gerber Region and Track
# ---------------------------------------------------------------------------------------------------------------------
def grb_tool(cls, mode, points, snap=True):
    tool = cls.__new__(cls)
    tool.dont_execute = False
    ui = SimpleNamespace(grid_snap_btn=SimpleNamespace(isChecked=lambda: snap),
                         grid_gap_x_entry=SimpleNamespace(get_value=lambda: 1.0),
                         grid_gap_y_entry=SimpleNamespace(get_value=lambda: 1.0))
    tool.draw_app = SimpleNamespace(bend_mode=mode, app=SimpleNamespace(ui=ui, inform=Mock()),
                                    in_action=False, last_length=0, snap_x=0.0, snap_y=0.0,
                                    tool_shape=Mock(), draw_utility_geometry=Mock())
    tool.points = list(points)
    tool.quad_segs = 2
    tool.buf_val = 0.001
    tool.gridx_size = tool.gridy_size = 1.0
    tool.temp_points = []
    tool.inter_point = None
    if cls is RegionEditorGrb:
        tool.reg_tool = SimpleNamespace(length=0)
        tool.ui = SimpleNamespace(x_entry=Mock(), y_entry=Mock())
    return tool


def region_click(tool, target):
    tool.utility_geometry(data=target)
    tool.click(target)


@pytest.mark.parametrize('mode', [1])
def test_region_diagonal_gives_no_extra_vertex(mode):
    tool = grb_tool(RegionEditorGrb, mode, [(0.0, 0.0)])
    # a stale corner from a previous mouse position must not survive
    tool.utility_geometry(data=(5.0, 2.0))
    assert tool.inter_point is not None
    tool.utility_geometry(data=(3.0, 3.0))
    assert tool.inter_point is None
    assert tool.temp_points == [(0.0, 0.0), (3.0, 3.0)]
    tool.click((3.0, 3.0))
    assert tool.points == [(0.0, 0.0), (3.0, 3.0)]






def test_region_modes_work_without_grid_snap_on_off_grid_points():
    tool = grb_tool(RegionEditorGrb, 1, [(0.25, 0.25)], snap=False)
    region_click(tool, (4.5, 2.125))
    assert tool.points[0] == (0.25, 0.25)
    assert len(tool.points) == 3
    corner = tool.points[1]
    assert corner[1] == pytest.approx(0.25)
    assert abs(4.5 - corner[0]) == pytest.approx(abs(2.125 - corner[1]))


def test_region_backspace_pops_exactly_what_the_click_added():
    tool = grb_tool(RegionEditorGrb, 1, [])
    tool.click((0.0, 0.0))                       # 1 point
    region_click(tool, (5.0, 2.0))               # corner + point
    region_click(tool, (8.0, 2.0))               # aligned: 1 point
    region_click(tool, (10.0, 4.0))              # diagonal from (8, 2): 1 point
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0), (8.0, 2.0), (10.0, 4.0)]
    assert tool.click_sizes == [1, 2, 1, 1]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0), (8.0, 2.0)]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0)]
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0)]














def track_tool_with_storage(mode):
    tool = grb_tool(TrackEditorGrb, mode, [])
    storage = []
    tool.draw_app.storage_dict = {10: {'geometry': storage}}
    tool.draw_app.last_aperture_selected = 10
    tool.draw_app.add_gerber_shape = lambda shape, store: store.append(shape)
    tool.draw_app.plot_all = Mock()
    tool.track_tool = SimpleNamespace(length=0)
    tool.ui = SimpleNamespace(x_entry=Mock(), y_entry=Mock())
    tool.click_shapes = []
    return tool, storage


def track_click(tool, target):
    tool.utility_geometry(data=target)
    tool.click(target)


def test_track_backspace_removes_the_last_point_and_its_segment():
    tool, storage = track_tool_with_storage(1)
    track_click(tool, (0.0, 0.0))
    track_click(tool, (5.0, 2.0))
    track_click(tool, (8.0, 2.0))
    assert len(storage) == 3
    kept = storage[:2]

    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [(0.0, 0.0), (5.0, 2.0)]
    assert storage == kept

    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert tool.points == [] and storage == [] and tool.temp_points == []
    # nothing left to remove: no error
    tool.on_key(QtCore.Qt.Key.Key_Backspace)
    assert storage == []
