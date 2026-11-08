"""Corner (bend) modes of the Geometry Editor Path/Polygon tools and the function shared with the Gerber Editor."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, LinearRing

from appEditors.appGeoEditor import FCPath, FCPolygon, bend_corner_point
from appEditors.appGerberEditor import RegionEditorGrb, TrackEditorGrb
from appEditors.editor_corner import corner_point, next_bend_mode
from tests.test_geo_editor_input import input_tool


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_modifier(monkeypatch):
    monkeypatch.setattr(QtWidgets.QApplication, 'keyboardModifiers',
                        staticmethod(lambda: QtCore.Qt.KeyboardModifier.NoModifier))


def corner(mode, old, new):
    return corner_point(mode, old, new)


# ---------------------------------------------------------------------------------------------------------------------
# the shared function
# ---------------------------------------------------------------------------------------------------------------------
# (mode, new point) -> corner, starting from (0, 0); every quadrant (left/right, up/down), X-major and Y-major moves
SHARED = [
    # mode 1: 45 degrees, the diagonal part is last
    (1, (5, 2), (3, 0)), (1, (-5, 2), (-3, 0)), (1, (5, -2), (3, 0)), (1, (-5, -2), (-3, 0)),
    (1, (2, 5), (0, 3)), (1, (-2, 5), (0, 3)), (1, (2, -5), (0, -3)), (1, (-2, -5), (0, -3)),
    # mode 2: reverse 45 degrees, the diagonal part is first
    (2, (5, 2), (2, 2)), (2, (-5, 2), (-2, 2)), (2, (5, -2), (2, -2)), (2, (-5, -2), (-2, -2)),
    (2, (2, 5), (2, 2)), (2, (-2, 5), (-2, 2)), (2, (2, -5), (2, -2)), (2, (-2, -5), (-2, -2)),
    # mode 3: 90 degrees, horizontal first
    (3, (5, 2), (5, 0)), (3, (-5, 2), (-5, 0)), (3, (5, -2), (5, 0)), (3, (-2, -5), (-2, 0)),
    # mode 4: reverse 90 degrees, vertical first
    (4, (5, 2), (0, 2)), (4, (-5, 2), (0, 2)), (4, (5, -2), (0, -2)), (4, (-2, -5), (0, -5)),
    # mode 5: free angle, no corner
    (5, (5, 2), None), (5, (-2, -5), None),
    # a pure diagonal needs no corner in the 45 degrees modes
    (1, (3, 3), None), (2, (-3, 3), None),
]


@pytest.mark.parametrize('mode, new, expected', SHARED[::8])
def test_shared_corner_all_modes_and_directions(mode, new, expected):
    assert corner(mode, (0, 0), new) == expected


# ---------------------------------------------------------------------------------------------------------------------
# Gerber tools keep the same points (golden values recorded before the corner math was moved to the shared function)
# ---------------------------------------------------------------------------------------------------------------------
def grb_ui(snap=True):
    return SimpleNamespace(grid_snap_btn=SimpleNamespace(isChecked=lambda: snap),
                           grid_gap_x_entry=SimpleNamespace(get_value=lambda: 1.0),
                           grid_gap_y_entry=SimpleNamespace(get_value=lambda: 1.0))


def grb_tool(cls, mode, points, snap=True):
    tool = cls.__new__(cls)
    tool.dont_execute = False
    tool.draw_app = SimpleNamespace(bend_mode=mode, app=SimpleNamespace(ui=grb_ui(snap)))
    tool.points = list(points)
    tool.buf_val = 0.001
    tool.quad_segs = 2
    tool.gridx_size = tool.gridy_size = 1.0
    tool.temp_points = []
    tool.inter_point = None
    return tool


def test_gerber_region_click_adds_corner_and_point():
    tool = grb_tool(RegionEditorGrb, 1, [(0.0, 0.0)])
    tool.utility_geometry(data=(5.0, 2.0))
    tool.draw_app.in_action = False
    tool.reg_tool = SimpleNamespace(length=0)
    tool.ui = SimpleNamespace(x_entry=Mock(), y_entry=Mock())
    tool.draw_app.last_length = 0
    tool.draw_app.snap_x = tool.draw_app.snap_y = 0.0
    tool.draw_app.app.inform = Mock()
    tool.click((5.0, 2.0))
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0)]


# ---------------------------------------------------------------------------------------------------------------------
# Geometry Editor Path
# ---------------------------------------------------------------------------------------------------------------------
def path_tool(mode=5, points=()):
    editor, tool, panel, original = input_tool(FCPath, points=points)
    editor.bend_mode = mode
    tool.points = list(points)
    tool.set_bend_cursor = Mock()
    return editor, tool


def click_all(tool, *clicks):
    for point in clicks:
        tool.click(point)


def test_path_mode_1_click_adds_corner_and_point():
    editor, tool = path_tool(1)
    click_all(tool, (0.0, 0.0), (5.0, 2.0))
    assert tool.points == [(0.0, 0.0), (3.0, 0.0), (5.0, 2.0)]
    click_all(tool, (5.0, 8.0))   # vertical move: no corner
    assert tool.points[-1] == (5.0, 8.0) and len(tool.points) == 4
    click_all(tool, (9.0, 10.0))  # 45 degrees mode again
    assert tool.points[-3:] == [(5.0, 8.0), (7.0, 8.0), (9.0, 10.0)]


def test_path_made_geometry_has_the_corner_points():
    editor, tool = path_tool(3)
    click_all(tool, (0.0, 0.0), (5.0, 2.0))
    assert tool.make() != 'fail'
    assert list(tool.geometry.geo.coords) == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]


# ---------------------------------------------------------------------------------------------------------------------
# Geometry Editor Polygon
# ---------------------------------------------------------------------------------------------------------------------
def polygon_tool(mode=5, points=()):
    editor, path, _panel, _original = input_tool(FCPath, points=points)
    editor.bend_mode = mode
    tool = FCPolygon.__new__(FCPolygon)
    tool.draw_app, tool.app, tool.name, tool.points = editor, editor.app, 'polygon', list(points)
    tool.complete, tool.geometry, tool.new_segment, tool.cursor_data_control = False, None, True, True
    tool.plugin_name = 'Polygon'
    tool.draw_cursor_data = Mock()
    tool.polygon_tool = SimpleNamespace(length=0.0)
    return editor, tool


def test_polygon_mode_3_preview_and_click():
    editor, tool = polygon_tool(3)
    click_all(tool, (0.0, 0.0))
    preview = tool.utility_geometry(data=(5.0, 2.0)).geo
    assert isinstance(preview, LineString)
    assert list(preview.coords) == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]
    click_all(tool, (5.0, 2.0))
    assert tool.points == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0)]
    ring = tool.utility_geometry(data=(1.0, 6.0)).geo
    assert isinstance(ring, LinearRing)
    assert list(ring.coords)[:-1] == [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0), (1.0, 2.0), (1.0, 6.0)]


