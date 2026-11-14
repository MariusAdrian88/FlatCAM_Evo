"""The clearing direction through the shared engine: the kernels get it, the laser lines do not, and the final G-code
cuts every ring of a polygon with a hole (and every depth) in the chosen direction, with Connect off and on."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shapely.geometry import Point, box  # noqa: E402

from appPlugins.Common.clearing import engine  # noqa: E402
from appPlugins.Common.clearing.methods import NCC_METHODS, PAINT_METHODS  # noqa: E402
from test_clearing_paths import make_geometry, turn  # noqa: E402
from test_cncjob_export_gcode import gcode_tokens, multi_geo_gcode  # noqa: E402

CENTER = (10.0, 10.0)
AREA = box(0, 0, 20, 20).difference(Point(CENTER).buffer(4, quad_segs=16))


def fill(method, direction, connect, **kwargs):
    geometry = make_geometry()
    geometry.app.units = 'MM'
    geometry.app.options = {'global_tolerance': 0}
    kernels = engine.FillKernels(shrink=geometry.clear_polygon_shrink, seed=geometry.clear_polygon_seed,
                                 lines=geometry.clear_polygon_lines, crosshatch=geometry.clear_polygon_crosshatch,
                                 fill_lines=getattr(geometry, 'fill_with_lines', None))
    result = engine.fill_polygon(AREA, 1.0, method, 0.5, connect, True, False, kernels, NCC_METHODS,
                                 lambda dia, kind: 32, direction=direction, **kwargs)
    return list(result.get_objects())


def rings(paths):
    return [np.asarray(path.coords) for path in paths if len(path.coords) > 3 and path.coords[0] == path.coords[-1]]


def role(ring):
    """'outer' for a ring of the outer boundary of the area, 'hole' for a ring around the round hole."""
    dist = np.hypot(*(ring - CENTER).T)
    return 'hole' if dist.max() < 7.0 else 'outer' if dist.min() > 7.0 else None


def ring_runs(segment, ring):
    """The run of a cut along the ring: from its start point to the next time the cut is there (a ring is closed)."""
    at = np.flatnonzero(np.all(np.isclose(segment, ring[0], atol=2e-4), axis=1))
    return [segment[first:second + 1] for first, second in zip(at[::2], at[1::2])]


@pytest.mark.parametrize('connect', [False, True])
@pytest.mark.parametrize('method', [0, 1], ids=['standard', 'seed'])
@pytest.mark.parametrize('direction', ['climb', 'conv'])
def test_every_ring_is_cut_in_the_direction_at_every_depth(direction, method, connect):
    paths = fill(method, direction, connect)
    expected = {'outer': 1 if direction == 'climb' else -1}
    expected['hole'] = -expected['outer']
    # the rings as the kernel made them, before the join
    kernel_rings = [ring for ring in rings(fill(method, direction, False)) if role(ring) is not None]
    assert len(kernel_rings) >= 2
    __, gcode = multi_geo_gcode(paths, data=dict(
        tools_mill_keep_direction=True, tools_mill_multidepth=True, tools_mill_depthperpass=0.1, tools_mill_cutz=-0.2))
    tokens, cuts, low_rapids = gcode_tokens(gcode)
    assert low_rapids == []
    found = 0
    for ring in kernel_rings:
        for cut in cuts:
            for run in ring_runs(np.asarray(cut), ring):
                assert turn(run) == expected[role(ring)], (direction, method, connect, role(ring))
                found += 1
    # every ring is cut at both depths
    assert found == 2 * len(kernel_rings)


def test_direction_none_calls_the_kernels_as_before_and_the_laser_lines_get_none():
    seen = {}

    def kernel(pol, tooldia, **kwargs):
        seen.update(kwargs)
        return None

    def laser(*args):
        seen['laser_args'] = len(args)
        return None

    kernels = engine.FillKernels(shrink=kernel, seed=kernel, lines=kernel, crosshatch=kernel, fill_lines=kernel)
    for direction in ('none', 'climb'):
        for method in range(5):
            seen.clear()
            engine.fill_polygon(AREA, 1.0, method, 0.5, False, True, False, kernels, PAINT_METHODS,
                                lambda dia, kind: 32, laser=laser, direction=direction)
            has = 'direction' in seen
            if PAINT_METHODS.kernel(method) == 'laser_lines':
                assert not has and seen['laser_args'] == 13
            else:
                assert has is (direction == 'climb')


# ---------------------------------------------------------------------------------------------------------------
# the canvas selection of the NCC and Paint plugins: Escape, a complete selection, the next selection
# ---------------------------------------------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock, call  # noqa: E402

from PyQt6 import QtGui  # noqa: E402
from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from camlib import Geometry  # noqa: E402
from appPlugins.Common.clearing import regions  # noqa: E402
from appPlugins.ToolNCC.NccGen import click_faces, picked_faces  # noqa: E402
from test_clearing_golden import Plugin, qapp  # noqa: E402,F401  (the real-plugin harness of the golden tests)

COPPER = [box(0, 0, 2, 4), box(5, 0, 7, 4), box(10, 0, 12, 4)]
# the points that pick a polygon: NCC picks the copper-free faces (the gaps), Paint the copper polygons
CLICKS = {'ncc': (3, 8), 'paint': (1, 6)}


class FakeCanvas:
    """The event bus of the VisPy canvas: the handlers connected to each event."""

    def __init__(self, app):
        self.handlers = {'mouse_press': [app.on_mouse_click_over_plot], 'mouse_move': [app.on_mouse_move_over_plot],
                         'mouse_release': [app.on_mouse_click_release_over_plot], 'key_press': []}
        self.initial = {name: list(handlers) for name, handlers in self.handlers.items()}
        app.plotcanvas.graph_event_connect.side_effect = lambda name, callback: self.handlers[name].append(callback)
        app.plotcanvas.graph_event_disconnect.side_effect = self.disconnect

    def disconnect(self, name, callback=None):
        if callback is None:
            self.handlers[name].clear()
        elif callback in self.handlers[name]:
            self.handlers[name].remove(callback)

    def emit(self, name, event):
        for callback in list(self.handlers[name]):
            callback(event)


class FakeShapes:
    """A shape collection that keeps what is drawn."""

    def __init__(self):
        self.items = {}
        self.count = 0

    def add(self, shape=None, **kwargs):
        self.count += 1
        self.items[self.count] = shape
        return self.count

    def remove(self, shape_id):
        self.items.pop(shape_id, None)

    def clear(self, *args, **kwargs):
        self.items.clear()

    def redraw(self, *args, **kwargs):
        pass


def qt_key(name):
    key = {'Escape': Qt.Key.Key_Escape, 'Return': Qt.Key.Key_Return}[name]
    return QtGui.QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


# the position, the drag flag and the key of the events of the 2D VisPy canvas, of an int based one and of a Qt key
EVENT_FORMATS = {
    'vispy': dict(pos=lambda x, y: np.array([float(x), float(y)]), drag=False,
                  key=lambda name: SimpleNamespace(key=name)),
    'int': dict(pos=lambda x, y: (int(x), int(y)), drag=0, key=lambda name: int(getattr(Qt.Key, 'Key_' + name))),
    'qt': dict(pos=lambda x, y: [float(x), float(y)], drag=False, key=qt_key),
}


class Canvas:
    """A running plugin with a fake canvas; the events are made in one of the EVENT_FORMATS."""

    def __init__(self, qapp, tmp_path, kind, fmt):
        self.kind, self.format = kind, EVENT_FORMATS[fmt]
        self.plugin = Plugin(qapp, tmp_path, kind, None, [0.4])
        self.tool, self.app = self.plugin.tool, self.plugin.app
        self.board = Geometry(app=self.app)
        self.board.kind, self.board.obj_options, self.board.solid_geometry = 'geometry', {'name': 'board'}, COPPER
        self.app.collection.objects['board'] = self.board
        self.canvas = FakeCanvas(self.app)
        self.app.tool_shapes, self.app.sel_shapes = FakeShapes(), FakeShapes()
        self.app.event_is_dragging = False
        self.sink = Mock()
        gen = self.tool.gen
        if kind == 'ncc':
            gen.clear_ncc_area_selection_option = gen.clear_ncc_click_option = self.sink
        else:
            gen.paint_area_selection_option = gen.paint_poly = self.sink

    def event(self, button, x=0, y=0, drag=None):
        return SimpleNamespace(button=button, pos=self.format['pos'](x, y),
                               is_dragging=self.format['drag'] if drag is None else drag)

    def click(self, x, y, button=1):
        self.canvas.emit('mouse_move', self.event(0, x, y))
        self.canvas.emit('mouse_release', self.event(button, x, y))

    def move(self, x, y):
        self.canvas.emit('mouse_move', self.event(0, x, y))

    def key(self, name):
        self.canvas.emit('key_press', self.format['key'](name))

    def start(self, mode):
        """Start the selection the way the user does: the Generate button, or Add exclusion."""
        tool = self.tool
        if mode == 'exclusion':
            tool.ui.excl_shape_radio.set_value('square')
            return tool.on_add_exclusion()
        if mode == 'click':
            tool.ui.select_method_combo.set_value(3 if self.kind == 'ncc' else 1)
        else:
            tool.ui.select_method_combo.set_value(1 if self.kind == 'ncc' else 2)
            tool.ui.area_shape_radio.set_value(mode)
        (tool.gen.on_ncc_click if self.kind == 'ncc' else tool.gen.on_paint_button_click)()

    def committed(self, mode):
        """The regions that the last selection handed over (and forget them)."""
        if mode == 'exclusion':
            zones = list(self.tool.exclusion_list)
            self.tool.exclusion_list.clear()
            return zones
        args, kwargs = self.sink.call_args if self.sink.called else ((), {})
        self.sink.reset_mock()
        if mode != 'click':
            return list(args[0]) if args else []
        if self.kind == 'ncc':
            return picked_faces(click_faces(self.board), args[0]) if args else []
        return list(kwargs['poly_list']) if kwargs else []

    def face_at(self, x, y):
        faces = click_faces(self.board) if self.kind == 'ncc' else COPPER
        return regions.face_at(faces, (x, y))

    def assert_idle(self):
        """The canvas belongs to the app again and nothing of the selection is left on it."""
        assert self.canvas.handlers == self.canvas.initial
        assert not self.app.tool_shapes.items and not self.app.sel_shapes.items
        assert self.app.ui.notebook.setDisabled.call_args == call(False)

    def assert_selecting(self):
        assert self.canvas.handlers['key_press']
        assert self.app.ui.notebook.setDisabled.call_args == call(True)

    def close(self):
        self.plugin.close()


def same_region(found, expected):
    return len(found) == len(expected) and unary_union(found).symmetric_difference(
        unary_union(expected)).area < 1e-9


def make_selection(session, flow, which):
    """A complete selection; `which` (0 or 1) moves it, so 2 selections differ. Returns the expected regions."""
    if flow == 'polygon':
        for x, y in ((0, 0), (4, 0), (4, 4)):
            session.click(x + which, y)
        session.click(which, 4, button=2)       # adds the last point and the polygon
        session.click(which, 4, button=2)       # ends the selection
        return [box(which, 0, 4 + which, 4)]
    if flow == 'click':
        x = CLICKS[session.kind][which]
        session.click(x, 2)
        session.click(x, 2, button=2)
        return [session.face_at(x, 2)]
    session.click(1 + which, 1)
    session.click(3 + which, 3)
    session.click(0, 0, button=2)
    return [box(1 + which, 1, 3 + which, 3)]


def make_partial_selection(session, flow):
    if flow == 'polygon':
        session.click(0, 0)
        session.click(4, 0)
        session.move(4, 4)
    elif flow == 'click':
        session.click(CLICKS[session.kind][0], 2)
    else:
        session.click(1, 1)
        session.click(3, 3)         # a zone is added and drawn
        session.click(5, 1)         # the start of the next one
        session.move(6, 2)
    session.assert_selecting()


@pytest.mark.parametrize('fmt', list(EVENT_FORMATS))
@pytest.mark.parametrize('mode', ['square', 'polygon', 'exclusion', 'click'])
@pytest.mark.parametrize('kind', ['ncc', 'paint'])
def test_canvas_selection_escape_commit_and_the_next_selection(qapp, tmp_path, kind, mode, fmt):
    session = Canvas(qapp, tmp_path, kind, fmt)
    flow = mode if mode in ('polygon', 'click') else 'square'
    try:
        # Escape after a partial input: nothing is handed over and nothing is left on the canvas
        session.start(mode)
        make_partial_selection(session, flow)
        session.key('Escape')
        assert session.committed(mode) == []
        session.assert_idle()

        # a complete selection hands over exactly its regions, with the same cleanup
        session.start(mode)
        expected = make_selection(session, flow, 0)
        assert same_region(session.committed(mode), expected)
        session.assert_idle()

        # the next selection starts empty: no point or zone of the 2 before is carried over
        session.start(mode)
        expected = make_selection(session, flow, 1)
        assert same_region(session.committed(mode), expected)
        session.assert_idle()

        # the canvas works as before: the events reach the app handlers only
        session.canvas.emit('mouse_release', session.event(1, 1, 1))
        session.app.on_mouse_click_release_over_plot.assert_called()
        session.key('Escape')
        assert session.committed(mode) == []
    finally:
        session.close()


@pytest.mark.parametrize('drag', [True, 1])
@pytest.mark.parametrize('kind', ['ncc', 'paint'])
def test_canvas_selection_is_not_ended_by_a_right_button_drag(qapp, tmp_path, kind, drag):
    session = Canvas(qapp, tmp_path, kind, 'vispy')
    try:
        session.start('square')
        session.click(1, 1)
        session.canvas.emit('mouse_move', session.event(2, 2, 2, drag=drag))
        session.canvas.emit('mouse_release', session.event(2, 2, 2))
        session.assert_selecting()
        session.key('Escape')
        session.assert_idle()
    finally:
        session.close()


@pytest.mark.parametrize('fmt', list(EVENT_FORMATS))
@pytest.mark.parametrize('kind', ['ncc', 'paint'])
def test_canvas_click_selection_is_ended_by_enter(qapp, tmp_path, kind, fmt):
    session = Canvas(qapp, tmp_path, kind, fmt)
    try:
        session.start('click')
        x = CLICKS[kind][0]
        session.click(x, 2)
        session.key('Return')
        assert same_region(session.committed('click'), [session.face_at(x, 2)])
        session.assert_idle()
    finally:
        session.close()
