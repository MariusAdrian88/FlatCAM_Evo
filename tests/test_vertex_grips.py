"""VertexGrips with a fake editor: display, hit test, drag threshold, preview, commit call, cancel."""
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtCore
from shapely import BufferJoinStyle
from shapely.geometry import LineString, MultiPolygon, Point, box

from appEditors.vertex_grips import VertexGrips, GRIP_COLOR, INVALID_COLOR

NO_MOD = QtCore.Qt.KeyboardModifier.NoModifier
CTRL = QtCore.Qt.KeyboardModifier.ControlModifier
PIXEL = 0.01  # canvas units for each screen pixel: the hit radius is 0.06


class FakeCollection:
    def __init__(self):
        self.items = []
        self.enabled = True

    def add(self, shape=None, **kwargs):
        self.items.append((shape, kwargs))
        return len(self.items)

    def clear(self, update=False):
        self.items = []

    def redraw(self):
        pass


def make_grips(geometry, grid=False, snap=None, busy=False):
    shape = SimpleNamespace(geo=geometry)
    app = SimpleNamespace(app_units='MM', grid_status=lambda: grid, inform=SimpleNamespace(emit=Mock()),
                          options={'global_sel_line': '#0000ffbf'},
                          plotcanvas=SimpleNamespace(new_shape_collection=lambda layers: FakeCollection()))
    editor = SimpleNamespace(selected=[shape], active_tool=SimpleNamespace(name='select'), app=app,
                             apply_vertex_edit=Mock(return_value=None), reject_busy=lambda: busy,
                             snap=snap or (lambda x, y: (x, y)), active_snap_type=None,
                             canvas=SimpleNamespace(pixel_size=lambda: PIXEL), tool_shape=FakeCollection(),
                             get_draw_color=lambda: '#00000080', get_sel_color=lambda: '#0000FF80')
    grips = VertexGrips(editor)
    grips.sync()
    return grips, editor, shape


def drag(grips, start, end):
    """Press at the canvas point start, move 20 px, release at the canvas point end."""
    assert grips.press((0, 0), start, NO_MOD)
    grips.move((20, 0), end)
    return grips.release((20, 0), end)


def test_sync_shows_grips_for_one_selected_shape_in_select_tool_only():
    grips, editor, shape = make_grips(LineString([(0, 0), (1, 0), (2, 0)]))
    assert grips.shape is shape
    assert len(grips.collection().items) == 5  # 3 vertices + 2 midpoints
    editor.active_tool = SimpleNamespace(name='move')
    grips.sync()
    assert grips.shape is None and grips.collection().items == []
    editor.active_tool = SimpleNamespace(name='select')
    editor.selected = [shape, SimpleNamespace(geo=LineString([(5, 5), (6, 6)]))]
    grips.sync()
    assert grips.shape is None


def test_drag_of_a_vertex_commits_one_move():
    grips, editor, shape = make_grips(LineString([(0, 0), (2, 0)]))
    assert drag(grips, (2, 0), (2, 1))
    editor.apply_vertex_edit.assert_called_once_with('move', (0, 0, 1), (2.0, 1.0), target=shape,
                                                     on_segment=False)
    assert editor.tool_shape.items == []


def test_drag_of_a_midpoint_inserts_a_vertex():
    grips, editor, shape = make_grips(LineString([(0, 0), (2, 0)]))
    assert drag(grips, (1, 0), (1, 1))
    editor.apply_vertex_edit.assert_called_once_with('insert', (0, 0, 0), (1.0, 1.0), target=shape,
                                                     on_segment=False)


def test_ctrl_click_removes_a_vertex():
    grips, editor, shape = make_grips(LineString([(0, 0), (1, 0), (2, 0)]))
    assert grips.press((0, 0), (1, 0), CTRL)
    assert grips.release((0, 0), (1, 0))
    editor.apply_vertex_edit.assert_called_once_with('remove', (0, 0, 1), target=shape)


def test_cancel_drops_the_drag():
    grips, editor, shape = make_grips(LineString([(0, 0), (2, 0)]))
    assert grips.press((0, 0), (2, 0), NO_MOD)
    grips.move((20, 0), (2, 1))
    assert editor.tool_shape.items
    grips.cancel()
    assert editor.tool_shape.items == []
    assert not grips.release((20, 0), (2, 1))
    editor.apply_vertex_edit.assert_not_called()


ROUNDED = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)


def grip_of(grips, kind):
    return next(grip for grip in grips.grips if grip[0] == kind)


def test_drag_of_an_arc_middle_bends_the_arc():
    grips, editor, shape = make_grips(ROUNDED)
    kind, address, middle = grip_of(grips, 'arc_mid')
    cursor = (middle[0] - 0.2, middle[1] - 0.2)
    assert drag(grips, middle, cursor)
    editor.apply_vertex_edit.assert_called_once_with('arc_mid', address, cursor, target=shape, on_segment=False)


def test_full_circle_radius_drag():
    grips, editor, shape = make_grips(Point(0, 0).buffer(2))
    assert [grip[0] for grip in grips.grips] == ['radius'] * 4
    kind, address, point = grips.grips[0]
    assert drag(grips, point, (3, 0))
    editor.apply_vertex_edit.assert_called_once_with('arc_radius', address, (3.0, 0.0), target=shape,
                                                     on_segment=False)


