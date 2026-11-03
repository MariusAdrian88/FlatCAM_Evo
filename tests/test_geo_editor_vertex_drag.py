"""Vertex edits with an address and vertex drags go through apply_vertex_edit: 1 drag is 1 Undo step."""
import math
from types import SimpleNamespace

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import BufferJoinStyle
from shapely.geometry import MultiPolygon, Polygon, box

from appEditors.appGeoEditor import AppGeoEditor, AppGeoEditorUI
from appEditors.vertex_ops import circle_center, find_arcs, vertex_grips
from tests.test_geo_editor_vertices import vertex_editor


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_address_edits_a_hole_with_one_undo_step():
    hole = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]
    editor, original = vertex_editor(Polygon([(0, 0), (6, 0), (6, 6), (0, 6)], [hole]))
    assert editor.apply_vertex_edit('move', (0, 1, 0), (1.5, 0.5)) != 'fail'
    assert list(editor.selected[0].geo.interiors[0].coords)[0] == (1.5, 0.5)
    assert editor.undo()
    assert editor.selected == [original]


from appEditors.vertex_grips import VertexGrips
from tests.test_vertex_grips import FakeCollection

PIXEL = 0.01


def drag_editor(geometry):
    """A history editor with real apply_vertex_edit, real VertexGrips and a VisPy-like 4-value translate_coords."""
    editor, original = vertex_editor(geometry)
    editor.active_tool = SimpleNamespace(name='select')
    editor.canvas = SimpleNamespace(pixel_size=lambda: PIXEL, translate_coords=lambda pos: (pos[0], pos[1], 0, 1))
    editor.tool_shape = FakeCollection()
    editor.get_draw_color = lambda: '#00000080'
    editor.get_sel_color = lambda: '#0000FF80'
    editor.reject_busy = lambda: AppGeoEditor.reject_busy(editor)
    editor.snap = lambda x, y: (x, y)
    editor.app.grid_status = lambda: False
    editor.app.plotcanvas = SimpleNamespace(new_shape_collection=lambda layers: FakeCollection())
    editor.vertex_grips = VertexGrips(editor)
    editor.vertex_grips.sync()
    return editor, original


def test_one_drag_is_one_undo_step():
    editor, original = drag_editor(box(0, 0, 4, 4))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (4, 0), QtCore.Qt.KeyboardModifier.NoModifier)
    grips.move((20, 0), (5, -1))
    assert grips.release((20, 0), (5, -1))
    assert (5, -1) in list(editor.selected[0].geo.exterior.coords)
    assert len(editor._history_undo) == 1
    assert grips.shape is editor.selected[0]  # plot_changes() ran sync(): grips on the new shape
    assert editor.undo()
    assert editor.selected == [original]


def test_invalid_drag_does_not_change_the_shape():
    editor, original = drag_editor(box(0, 0, 4, 4))
    grips = editor.vertex_grips
    assert grips.press((0, 0), (4, 0), QtCore.Qt.KeyboardModifier.NoModifier)
    grips.move((20, 0), (0, 4))  # across the square: self-intersection
    assert grips.release((20, 0), (0, 4))
    assert editor.selected == [original]
    assert not editor._history_undo
    assert editor.app.inform.emit.called


ROUNDED = box(1, 1, 9, 5).buffer(1, join_style=BufferJoinStyle.round)


def on_some_arc(ring, point):
    """True if point is on the circle of 1 of the arcs of the closed ring."""
    vertices = list(ring.coords)[:-1]
    for arc in find_arcs(vertices, True):
        center = circle_center(vertices[arc.start], arc.middle, vertices[arc.end])
        if abs(math.dist(center, point) - math.dist(center, vertices[arc.start])) < 1e-9:
            return True
    return False


