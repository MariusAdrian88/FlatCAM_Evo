


# ======================================================================
# merged from tests/test_plot_visibility_toggle.py
# ======================================================================
"""
Tests for the fast plot toggle: show or hide the shapes of the last full plot without a new plot.

- ShapeGroup.visible and ShapeGroup.update_visibility() must update the canvas (the collection skips the
  update if it is not marked dirty).
- FlatCAMObj.show_plotted_shapes() must work only while the shapes of the last full plot are on the canvas.
"""

import sys
import os
import unittest
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fake_canvas = None


def setUpModule():
    # the collection update sets a GL state; gloo needs a current canvas for this, a fake one is enough
    global _fake_canvas
    from vispy.gloo.context import FakeCanvas
    _fake_canvas = FakeCanvas()


@pytest.mark.parametrize('solid', [False, True])
@pytest.mark.parametrize('cache', ['valid', 'uncached', 'deleted', 'mismatch', 'pending'])
def test_excellon_panel_cached_plot_matches_fresh(solid, cache):
    from copy import deepcopy
    from shapely.affinity import translate
    from tests.test_object_copy import plotted, assert_close_plot
    import appGUI.VisPyVisuals as visuals
    from unittest.mock import patch

    from vispy.gloo.context import FakeCanvas
    canvas = FakeCanvas()
    app = make_app()
    source = make_excellon(app)
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (1, 2), (2, 2), (2, 1)]])
    # 2 tools: the panel holds the copies tool after tool, not copy after copy
    drill = Polygon([(5, 0), (6, 0), (6, 1), (5, 1)])
    source.tools = {1: {'solid_geometry': [polygon], 'multicolor': (0.1, 0.2, 0.3, 1.0)},
                    2: {'solid_geometry': [drill], 'multicolor': (0.1, 0.2, 0.3, 1.0)}}
    source.solid_geometry = [polygon, drill]
    source.obj_options.update(solid=solid, multicolored=False, plot=True)
    source.shapes = ShapeGroup(_new_collection())
    source.plot()
    before = plotted(source)
    if cache == 'uncached':
        source.shapes.clear(update=True)
    elif cache == 'deleted':
        source.deleted = True
    elif cache == 'pending':
        source.shapes.snapshot = lambda: None
    panel = make_excellon(app)
    panel.tools = deepcopy(source.tools)
    panel.tools[1]['solid_geometry'] = [translate(polygon, xoff=x) for x in (0, 10)]
    panel.tools[1]['multicolor'] = (0.8, 0.4, 0.2, 1.0)
    panel.tools[2]['solid_geometry'] = [translate(drill, xoff=x) for x in (0, 10)]
    panel.tools[2]['multicolor'] = (0.8, 0.4, 0.2, 1.0)
    panel.solid_geometry = panel.tools[1]['solid_geometry'] + panel.tools[2]['solid_geometry']
    if cache == 'mismatch':
        panel.tools[1]['solid_geometry'][1] = translate(polygon.buffer(0.1), xoff=10)
    panel.obj_options.update(solid=solid, multicolored=False, plot=False)
    panel.shapes = ShapeGroup(_new_collection())
    panel.reuse_plot_of(source, offsets=[(0, 0), (10, 0)])
    with patch.object(visuals, '_update_shape_buffers', wraps=visuals._update_shape_buffers) as prepared:
        panel.plot()
    cached = plotted(panel)
    assert (prepared.call_count == 0) == (cache == 'valid')
    assert len(panel.shape_indexes_dict[1]) == (2 if solid else 4)
    assert all(not entry['visible'] for entry in cached)
    panel.shapes = ShapeGroup(_new_collection())
    panel.plot()
    assert_close_plot(cached, plotted(panel))
    if cache != 'uncached':
        assert_close_plot(before, plotted(source))
    assert panel.show_plotted_shapes(True)
    panel.shapes.update_visibility(False, indexes=panel.shape_indexes_dict[1] + panel.shape_indexes_dict[2])
    assert all(not entry['visible'] for entry in plotted(panel))


@pytest.mark.parametrize('kind,overflow', [('geometry', False), ('excellon', False),
                                         ('gerber', False), ('geometry', True)])
@pytest.mark.filterwarnings('error::RuntimeWarning')
def test_panel_cancellation_keeps_fresh_rendering_precision(kind, overflow):
    from shapely import LineString
    from shapely.affinity import translate
    from vispy.gloo.context import FakeCanvas
    from tests.test_object_copy import geometry_source, gerber_source, plotted, assert_close_plot
    canvas = FakeCanvas()
    app = make_app()
    polygon = translate(Polygon([(0, 0), (.02, 0), (.02, .01), (0, .01)]),
                        xoff=-1000.00003, yoff=-1000.00003)
    offset = (1000, 1000)
    if overflow:
        polygon = LineString([(-1e39, 0), (-1e39, 1)])
        offset = (1e39, 0)
    factory = {'geometry': geometry_source, 'excellon': make_excellon, 'gerber': gerber_source}[kind]
    source, panel = factory(app), factory(app)
    for obj, geometry in ((source, polygon), (panel, translate(polygon, *offset))):
        obj.multigeo = False
        obj.solid_geometry = [geometry]
        obj.tools = {1: {'solid_geometry': [geometry]}}
        obj.obj_options.update(solid=True, multicolored=False, plot=True)
        obj.shapes = ShapeGroup(_new_collection())
    original = polygon.wkb
    with np.errstate(over='ignore', invalid='ignore'):
        source.plot()
    panel.reuse_plot_of(source, offsets=[offset])
    panel.plot()
    got = plotted(panel)
    panel.shapes = ShapeGroup(_new_collection())
    panel.plot()
    assert_close_plot(got, plotted(panel), atol=1e-6)
    assert float(got[0]['line_pts'][:, 0].min()) == pytest.approx(0 if overflow else -.00003, abs=1e-9)
    assert polygon.wkb == original


@pytest.mark.parametrize('change', ['none', 'diameter', 'resolution', 'style', 'tolerance'])
def test_rebuilt_excellon_panel_uses_destination_rendering(change):
    from shapely import Point
    from vispy.gloo.context import FakeCanvas
    from unittest.mock import patch
    import appGUI.VisPyVisuals as visuals
    from appParsers import panelize_core
    from tests.test_object_copy import plotted, assert_close_plot
    canvas = FakeCanvas()
    app = make_app()
    source, panel = make_excellon(app), make_excellon(app)
    source.tools = {1: {'tooldia': .8, 'drills': [Point(1, 2)],
                        'slots': [(Point(3, 2), Point(4, 3))]}}
    source.circle_steps = lambda radius: 16
    source.create_geometry()
    for obj in (source, panel):
        obj.obj_options.update(solid=True, multicolored=False, plot=True)
        obj.shapes = ShapeGroup(_new_collection())
    if change == 'style':
        source.fill_color = None
    source.plot()
    before = plotted(source)
    offsets = [(10, 0)]
    panel.tools = panelize_core.panelize_excellon(source.tools, offsets)
    panel.circle_steps = lambda radius: 32 if change == 'resolution' else 16
    if change == 'diameter':
        panel.tools[1]['tooldia'] = 1.2
    if change == 'tolerance':
        panel.drawing_tolerance = source.drawing_tolerance / 2
    panelize_core.create_panel_excellon_geometry(panel, source.tools, offsets)
    geometry = [shape.wkb for shape in panel.solid_geometry]
    panel.reuse_plot_of(source, offsets=offsets)
    with patch.object(visuals, '_update_shape_buffers', wraps=visuals._update_shape_buffers) as prepared:
        panel.plot()
    got = plotted(panel)
    assert (prepared.call_count == 0) == (change == 'none')
    panel.shapes = ShapeGroup(_new_collection())
    panel.plot()
    assert_close_plot(got, plotted(panel))
    assert_close_plot(before, plotted(source))
    assert [shape.wkb for shape in panel.solid_geometry] == geometry


@pytest.mark.parametrize('hidden', [False, True])
@pytest.mark.parametrize('no_mp', [False, True])
def test_excellon_outline_tools_match_individual_ring_rendering(hidden, no_mp):
    from vispy.gloo.context import FakeCanvas
    from tests.test_object_copy import plotted, assert_close_plot
    canvas = FakeCanvas()
    app = make_app()
    app.options['global_graphic_engine_3d_no_mp'] = no_mp
    obj = make_excellon(app)
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (1, 2), (2, 2), (2, 1)]])
    obj.tools = {1: {'solid_geometry': [polygon]},
                 2: {'solid_geometry': []},
                 3: {'solid_geometry': [polygon], 'multicolor': (0.1, 0.2, 0.3, 1.0)}}
    obj.obj_options.update(solid=False, multicolored=False, plot=not hidden)
    obj.shapes = ShapeGroup(_new_collection())
    obj.plot()
    expected = ShapeGroup(_new_collection())
    for color in (None, (0.1, 0.2, 0.3, 1.0)):
        expected.add(shape=polygon.exterior, color=color or 'red', visible=not hidden,
                     tolerance=obj.drawing_tolerance)
        expected.add(shape=polygon.interiors[0], color=color or 'orange', visible=not hidden,
                     tolerance=obj.drawing_tolerance)
    expected.redraw()
    assert_close_plot(plotted(obj), plotted(SimpleNamespace(shapes=expected)))
    keys = list(obj.shapes._indexes)
    assert obj.shape_indexes_dict == {1: keys[:2], 3: keys[2:]}
    obj.shapes.update_visibility(False, indexes=obj.shape_indexes_dict[1])
    obj.shapes.update_visibility(True, indexes=obj.shape_indexes_dict[3])
    assert [entry['visible'] for entry in plotted(obj)] == [False, False, True, True]
    obj.tools = {2: {'solid_geometry': []}}
    obj.plot()
    assert not obj.shapes._indexes
    assert obj.shape_indexes_dict == {}


@pytest.mark.parametrize('mode', ['single', 'multigeo_single', 'multi', 'fused', 'shared_border',
                                 'uncached', 'deleted', 'stale', 'partial', 'selected', 'own'])
def test_geometry_panel_reuse_or_fallback_matches_fresh(mode):
    from shapely import LineString
    from vispy.gloo.context import FakeCanvas
    from tests.test_object_copy import geometry_source, plotted, assert_close_plot
    from appParsers import panelize_core
    import appGUI.VisPyVisuals as visuals
    from unittest.mock import patch
    canvas = FakeCanvas()
    app = make_app()
    source = geometry_source(app)
    source.multigeo = mode in ('multigeo_single', 'multi', 'partial', 'selected', 'fused', 'shared_border', 'own')
    if mode in ('multigeo_single', 'shared_border'):
        source.tools = {1: source.tools[1]}
    paths = [LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)])]
    source.solid_geometry = paths
    for tool in source.tools.values():
        tool['solid_geometry'] = paths
    source.shapes = ShapeGroup(_new_collection())
    if mode != 'uncached':
        source.plot(plot_tool=1 if mode == 'partial' else None)
    if mode == 'deleted':
        source.deleted = True
    if mode == 'stale':
        source.solid_geometry = [LineString([(0, 0), (5, 1)])]
    panel = geometry_source(app)
    panel.multigeo = source.multigeo
    offsets = [(0, 0), (10, 0)]
    if mode == 'shared_border':
        offsets = [(0, 0), (1, 0)]
    if mode == 'own':
        # merged paths do not match the source, but every copy is the first copy moved
        offsets = [(10.0 * k, 3.0 * (k % 2)) for k in range(8)]
    builder = panelize_core.panelize_geometry_optimized if mode in ('fused', 'shared_border', 'own') \
        else panelize_core.panelize_geometry
    panel.tools, panel.solid_geometry = builder(
        source.tools, source.solid_geometry, source.multigeo, offsets)
    for tool in panel.tools.values():
        tool['data']['override_color'] = '#987654FF'
    panel.shapes = ShapeGroup(_new_collection())
    panel.reuse_plot_of(source, offsets=offsets)
    with patch.object(visuals, '_update_shape_buffers', wraps=visuals._update_shape_buffers) as prepared:
        panel.plot(plot_tool=1 if mode == 'selected' else None)
    got = plotted(panel)
    assert (prepared.call_count == 0) == (mode in ('single', 'multigeo_single', 'multi'))
    if mode == 'own':
        assert prepared.call_count * len(offsets) == len(got)
    panel.shapes = ShapeGroup(_new_collection())
    panel.plot(plot_tool=1 if mode == 'selected' else None)
    assert_close_plot(got, plotted(panel))


def test_draw_waits_for_all_buffers_of_a_rebuild():
    # The deferred updater sets the buffers in its thread. A draw between the positions and the colors dropped the
    # frame ("All attributes must have the same size"): a draw must see all the new buffers or none.
    import threading
    import time
    from unittest.mock import patch
    from vispy.gloo.context import FakeCanvas
    canvas = FakeCanvas()
    collection = _new_collection()
    _add_squares(ShapeGroup(collection), 3)
    mesh_type, line_type = type(collection._meshes[0]), type(collection._lines[0])
    seen, drawing = [], {}

    def record_mesh(visual):
        seen.append(('mesh', len(visual.vertices), len(visual.vertex_colors)))

    def record_line(visual):
        seen.append(('line', len(visual._pos), len(visual._color)))

    real_set_data = mesh_type.set_data

    def set_data_then_draw(visual, *args, **kwargs):
        real_set_data(visual, *args, **kwargs)
        if 'thread' not in drawing and kwargs.get('vertices') is not None:
            # a draw from the GUI thread right after the positions of the first visual
            drawing['thread'] = threading.Thread(target=collection.draw)
            drawing['thread'].start()
            time.sleep(0.05)
            drawing['during'] = list(seen)

    with patch.object(mesh_type, 'draw', record_mesh), patch.object(line_type, 'draw', record_line), \
            patch.object(mesh_type, 'set_data', set_data_then_draw):
        collection._rebuild_visuals()
        drawing['thread'].join(2)
    assert drawing['during'] == []
    assert seen and all(positions == colors for _kind, positions, colors in seen)


def test_unpickled_pool_buffers_keep_one_vertex_per_point():
    # The process pool returns the buffers unpickled: their float32 dtype is not builtin, and gloo read a (N, 2)
    # array of such a dtype as 2N vertices (128 positions for 64 colors: the frame and the HUD were not drawn).
    import pickle
    from vispy.gloo.context import FakeCanvas
    canvas = FakeCanvas()
    collection = _new_collection()
    # one shape in the layer: a single chunk kept the dtype through np.concatenate
    _add_squares(ShapeGroup(collection), 1)
    for key, d in collection.data.items():
        collection.data[key] = pickle.loads(pickle.dumps(d))
    assert collection.data[key]['line_pts'].dtype.isbuiltin != 1
    collection._rebuild_visuals()
    mesh, line = collection._meshes[1], collection._lines[1]
    assert mesh._positions.size == mesh._colors.size == len(mesh.vertices) > 0
    assert line._pos_vbo.size == line._color_vbo.size == len(line._pos) > 0
    assert mesh._prepare_draw(mesh) is not False


def test_bad_mesh_buffers_never_drop_the_frame():
    # A mesh with 64 positions and 32 colors failed every draw of the canvas (the HUD was gone with it).
    from vispy.gloo.context import FakeCanvas
    canvas = FakeCanvas()
    collection = _new_collection()
    mesh = collection._meshes[0]
    square = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32)
    faces = np.array([(0, 1, 2), (0, 2, 3)], dtype=np.uint32)
    mesh.set_data(vertices=square, faces=faces, vertex_colors=np.ones((4, 4)))
    assert mesh._colors.size == mesh._positions.size == 4
    # colors that do not fit: nothing changes
    mesh.set_data(vertices=np.vstack([square, square + 2]), faces=faces, vertex_colors=np.ones((4, 4)))
    assert mesh.vertices is None and mesh._prepare_draw(mesh) is False
    # buffers that differ anyway: the visual skips its draw, the other visuals still draw
    mesh.set_data(vertices=square, faces=faces, vertex_colors=np.ones((4, 4), dtype=np.float32))
    mesh._colors.set_data(np.ones((2, 4), dtype=np.float32))
    assert mesh._prepare_draw(mesh) is False
    line = collection._lines[0]
    line.set_data(pos=square, color=np.ones((4, 4), dtype=np.float32))
    line._color_vbo.set_data(np.ones((2, 4), dtype=np.float32))
    assert line._prepare_draw(line) is False


def _square(x):
    return Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)])


def _new_collection():
    from appGUI.VisPyVisuals import ShapeCollectionVisual
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None,
                                 fcoptions={"global_graphic_engine_3d_no_mp": True})


def _add_squares(group, n, start=0):
    return group.add_batch([{'shape': _square(start + i), 'color': '#FF0000FF', 'face_color': '#00FF00FF'}
                            for i in range(n)])


class TestShapeGroupVisibility(unittest.TestCase):

    def test_visible_setter_and_update_visibility_update_canvas(self):
        from appGUI.VisPyVisuals import ShapeGroup
        collection = _new_collection()
        group = ShapeGroup(collection)
        _add_squares(group, 3)
        group.redraw()
        line = collection._lines[1]   # layer 1 is the default layer of the shapes
        self.assertTrue(line.visible)

        group.visible = False
        self.assertFalse(line.visible)
        self.assertTrue(all(not d['visible'] for d in collection.data.values()))
        group.visible = True
        self.assertTrue(line.visible)

        keys = list(group._indexes)
        group.update_visibility(False, indexes=keys[:2])
        self.assertEqual([collection.data[k]['visible'] for k in keys], [False, False, True])
        self.assertTrue(line.visible)
        group.update_visibility(False, indexes=keys[2:])
        self.assertFalse(line.visible)



class TestShowPlottedShapes(unittest.TestCase):

    def test_after_full_plot(self):
        from appGUI.VisPyVisuals import ShapeGroup
        from appObjects.AppObjectTemplate import FlatCAMObj

        collection = _new_collection()
        obj = SimpleNamespace(app=SimpleNamespace(), shapes=ShapeGroup(collection), _full_plot_sig=None)
        for name in ('_shapes_signature', '_has_full_plot', 'mark_full_plot', 'show_plotted_shapes', 'bring_to_top'):
            setattr(obj, name, getattr(FlatCAMObj, name).__get__(obj))
        _add_squares(obj.shapes, 4)
        obj.shapes.redraw()

        def flags():
            return [collection.data[k]['visible'] for k in obj.shapes._indexes]

        buffers = {k: collection.data[k]['mesh_vertices'] for k in obj.shapes._indexes}
        obj.mark_full_plot()
        self.assertTrue(obj.show_plotted_shapes(False))
        self.assertEqual(flags(), [False] * 4)
        self.assertTrue(obj.show_plotted_shapes(True))
        self.assertEqual(flags(), [True] * 4)
        for key, vertices in buffers.items():
            self.assertIs(collection.data[key]['mesh_vertices'], vertices)


class TestHiddenObjectAnnotation(unittest.TestCase):

    def test_hiding_an_object_hides_the_first_text_group_of_its_collection(self):
        # TextGroup.visible tested 'if self._index:', so the text group with key 0 (each CNCJob has its own text
        # collection) kept its texts: the path numbers of a CNCJob that a plugin hides (Levelling hides its source)
        from appGUI.VisPyVisuals import ShapeGroup, TextCollectionVisual, TextGroup
        from appObjects.AppObjectTemplate import FlatCAMObj
        texts = TextCollectionVisual()
        annotation = TextGroup(texts)
        annotation.set(text=['1', '2'], pos=[(0, 0), (1, 1)])
        app = SimpleNamespace(log=Mock(), worker_task=SimpleNamespace(emit=lambda job: job['fcn'](*job['params'])))
        obj = SimpleNamespace(app=app, shapes=ShapeGroup(_new_collection()), annotation=annotation)

        FlatCAMObj.visible.fset(obj, False)
        self.assertFalse(texts.data[0]['visible'])
        self.assertFalse(texts.text)
        FlatCAMObj.visible.fset(obj, True)
        self.assertTrue(texts.data[0]['visible'])
        self.assertEqual(list(texts.text), ['1', '2'])


# ======================================================================
# merged from tests/test_object_plot_state.py
# ======================================================================
"""Stage 5 item 1 (free parts): plot() reads its state from obj_options, not from UI widgets.

Set the environment variable STAGE5_OLD_CODE=1 to run the batch-equality tests on the code before the change: the
widgets then get real values (Mock widgets) instead of the stub that raises on any access.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6 import QtGui, QtWidgets

from appGUI.GUIElements import FCButton, FCCheckBox
from appGUI.VisPyVisuals import ShapeGroup
from appObjects.ExcellonObject import ExcellonObject
from tests.helpers_objects import _headless_qt, make_app, make_excellon  # noqa: F401,E402

OLD_CODE = os.environ.get('STAGE5_OLD_CODE') == '1'


class RaisingUI:
    """Any access to the UI is a failure: plot() must not need it."""

    def __getattr__(self, name):
        raise AssertionError('plot() touched the UI: %s' % name)


def _widget(value):
    w = Mock()
    w.get_value.return_value = value
    return w


def _ui_for(settings):
    """The UI of the object: a raising stub on the new code, real widget values on the old code."""
    if not OLD_CODE:
        return RaisingUI()
    ui = Mock()
    ui.multicolored_cb = _widget(settings.get('multicolored', False))
    ui.solid_cb = _widget(settings.get('solid', False))
    ui.plot_cb = _widget(settings.get('plot', True))
    ui.follow_cb = _widget(settings.get('follow', False))
    return ui


def _counter_colors():
    state = {'n': 0}

    def random_color():
        state['n'] += 1
        return 'c%d' % state['n']
    return random_color


def _valid_counter_colors():
    state = {'n': 0}

    def random_color():
        state['n'] += 1
        return (state['n'] / 255, 0.0, 0.0, 1.0)
    return random_color


def _prepare_excellon(settings, with_ui=True):
    obj = make_excellon(make_app())
    obj.obj_options.update(settings)
    obj.ui = _ui_for(settings) if with_ui else None
    obj.random_color = _counter_colors()
    obj.fill_color = '#fill'
    obj.outline_color = '#line'
    obj.add_shapes_batch = Mock(side_effect=lambda batch, **kw: list(range(100, 100 + len(batch))))
    counter = {'n': 0}

    def add_shape(**kw):
        counter['n'] += 1
        return counter['n']
    obj.add_shape = Mock(side_effect=add_shape)
    obj.shapes = Mock(_indexes=[])
    return obj


def _batch_of(obj):
    return [(d['shape'].wkt, d['color'], d['face_color'], d['layer']) for d in obj.add_shapes_batch.call_args.args[0]]


def test_a_tool_color_survives_a_full_plot_and_an_invalid_one_is_replaced():
    obj = _prepare_excellon({'solid': True, 'multicolored': True, 'plot': True})
    obj.random_color = _valid_counter_colors()
    selected = (0.1, 0.2, 0.3, 1.0)
    obj.tools[1]['multicolor'] = selected

    obj.plot()
    random_colors = {tool: data['multicolor'] for tool, data in obj.tools.items() if tool != 1}
    obj.plot()

    # a color chosen with the tool swatch (and a random color of the first plot) is kept by a full replot
    assert obj.tools[1]['multicolor'] == selected
    assert all(obj.tools[tool]['multicolor'] == color for tool, color in random_colors.items())
    assert selected in [entry[1] for entry in _batch_of(obj)]

    # an invalid stored value gets a new random color instead of breaking the plot
    obj.tools[1]['multicolor'] = (2.0, 0.0, 0.0, 1.0)
    obj.plot()
    assert obj.tools[1]['multicolor'] != (2.0, 0.0, 0.0, 1.0)
    assert ExcellonObject._valid_tool_color(obj.tools[1]['multicolor'])
    assert obj.tools[1]['multicolor'] in [entry[1] for entry in _batch_of(obj)]


def _color_dialog(color, accepted):
    class Dialog:
        ColorDialogOption = QtWidgets.QColorDialog.ColorDialogOption

        def __init__(self, *args, **kwargs):
            pass

        def setOption(self, *args):
            pass

        def setCurrentColor(self, color):
            pass

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted if accepted else QtWidgets.QDialog.DialogCode.Rejected

        def selectedColor(self):
            return color
    return Dialog


@pytest.mark.parametrize('solid, multicolored, hidden', [(True, False, False), (False, True, True)])
@pytest.mark.parametrize('deferred', [True])
def test_tool_color_swatch_updates_the_tool_color_and_its_shapes(monkeypatch, solid, multicolored, hidden, deferred):
    from appGUI.VisPyVisuals import ShapeCollection
    from vispy.gloo.context import FakeCanvas

    canvas = FakeCanvas()
    obj = make_excellon(make_app())
    obj.obj_options.update(solid=solid, multicolored=multicolored, plot=True)
    obj.ui = None
    obj.fill_color = '#00FF00FF'
    obj.outline_color = '#000000FF'
    collection = ShapeCollection(pool=None, deferred=deferred,
                                 fcoptions={'global_graphic_engine_3d_no_mp': True})
    obj.shapes = ShapeGroup(collection)
    obj.plot()
    collection.flush()
    tool_no = list(obj.tools)[1]

    def painted_colors():
        # These are the merged arrays consumed by VisPy, not the per-shape storage.
        return ([line._color.copy() if line.visible else np.empty((0, 4)) for line in collection._lines],
                [mesh.vertex_colors.copy() if mesh.visible else np.empty((0, 4))
                 for mesh in collection._meshes])

    before_lines, before_meshes = painted_colors()
    expected_lines = [colors.copy() for colors in before_lines]
    expected_meshes = [colors.copy() for colors in before_meshes]
    line_offsets, mesh_offsets = [0] * 3, [0] * 3
    selected = np.array((10 / 255, 20 / 255, 30 / 255, 128 / 255))
    for key, data in collection.data.items():
        layer = data['layer']
        n_lines, n_faces = len(data['line_pts']), len(data['mesh_vertices'])
        if key in obj.shape_indexes_dict[tool_no]:
            expected_lines[layer][line_offsets[layer]:line_offsets[layer] + n_lines] = selected
            expected_meshes[layer][mesh_offsets[layer]:mesh_offsets[layer] + n_faces] = selected
        line_offsets[layer] += n_lines
        mesh_offsets[layer] += n_faces

    swatch = FCButton()
    monkeypatch.setattr(QtWidgets, 'QColorDialog', _color_dialog(QtGui.QColor(10, 20, 30, 128), True))
    if hidden:
        obj.shapes.visible = False
        collection.flush()

    ExcellonObject.on_tool_color_clicked(obj, tool_no, swatch)

    assert obj.tools[tool_no]['multicolor'] == pytest.approx(selected)
    assert 'rgba(10, 20, 30, 128)' in swatch.styleSheet()
    # a bare QPushButton rule would also style the buttons of the color dialog, which is parented to the swatch
    assert 'QPushButton {' not in swatch.styleSheet()

    def assert_painted_colors():
        lines, meshes = painted_colors()
        for actual, expected in zip(lines + meshes, expected_lines + expected_meshes):
            np.testing.assert_allclose(actual, expected, atol=1e-6)

    if hidden:
        assert not any(line.visible for line in collection._lines)
        assert not any(mesh.visible for mesh in collection._meshes)
        obj.shapes.visible = True
        collection.flush()
    assert_painted_colors()
    obj.shapes.visible = False
    obj.shapes.visible = True
    collection.flush()
    assert_painted_colors()
    obj.plot()
    collection.flush()
    assert_painted_colors()

    # Explicitly switching multicolor off still restores the ordinary object colors.
    checkbox = FCCheckBox()
    obj.form_fields['multicolored'] = checkbox
    obj.obj_options['multicolored'] = True
    checkbox.set_value(False)
    obj.muted_ui = False
    monkeypatch.setattr(obj, 'build_ui', Mock())
    obj.on_multicolored_cb_click(0)
    collection.flush()
    assert not obj.obj_options['multicolored']
    assert all(data['multicolor'] is None for data in obj.tools.values())
    lines, meshes = painted_colors()
    if solid:
        assert np.all(lines[2] == (0.0, 0.0, 0.0, 1.0))
        assert np.all(meshes[2] == (0.0, 1.0, 0.0, 1.0))
    else:
        assert np.all(lines[1] == (1.0, 0.0, 0.0, 1.0))


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------


# ======================================================================
# merged from tests/test_vispy_batch.py
# ======================================================================
#!/usr/bin/env python
"""
Automated tests for VisPy batch processing optimizations (Phase 2).

Tests cover:
1. add_batch() functionality
2. Synchronous add() behavior
3. Backface culling configuration
4. redraw() batch result collection
5. _collect_elements() method
6. Edge cases (empty batches, missing fc_options, etc.)

Run: python tests/test_vispy_batch.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import numpy as np
from unittest.mock import patch
from shapely.geometry import Polygon, LineString


class TestAddCopy(unittest.TestCase):
    """add_copy() reuses the position buffers of a shape and makes new color buffers."""

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        options = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
        self.source = ShapeCollectionVisual(linewidth=1, layers=1, pool=None, fcoptions=options)
        self.target = ShapeCollectionVisual(linewidth=1, layers=1, pool=None, fcoptions=options)
        # no GL canvas in the tests
        patcher = patch('appGUI.VisPyVisuals.set_state')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_two_shapes_get_their_own_color_per_vertex(self):
        collection = self.source
        line = LineString([(0, 0), (1, 1), (2, 0)])
        square = Polygon([(5, 0), (6, 0), (6, 1), (5, 1)])
        collection.add(shape=line, color='#FF0000FF', layer=0)
        collection.add(shape=square, color='#0000FFFF', face_color='#00FF00FF', layer=0)
        collection.redraw([])
        colors = collection._lines[0]._color
        n_line = 4
        self.assertEqual(colors.dtype.name, 'float32')
        self.assertEqual(colors.shape, (n_line + 8, 4))
        self.assertTrue(np.all(colors[:n_line] == (1.0, 0.0, 0.0, 1.0)))
        self.assertTrue(np.all(colors[n_line:] == (0.0, 0.0, 1.0, 1.0)))
        mesh = collection._meshes[0]
        mesh_colors = mesh.vertex_colors
        self.assertEqual(len(mesh_colors), len(mesh.vertices))
        self.assertEqual(mesh_colors.dtype.name, 'float32')
        self.assertEqual(mesh.faces.shape, (2, 3))
        self.assertLess(mesh.faces.max(), len(mesh.vertices))
        self.assertTrue(np.all(mesh_colors == (0.0, 1.0, 0.0, 1.0)))

    def test_update_color_changes_the_painted_shapes(self):
        collection = self.source
        k0 = collection.add(shape=LineString([(0, 0), (1, 1)]), color='#FF0000FF', layer=0)
        collection.add(shape=LineString([(2, 0), (3, 1)]), color='#FF0000FF', layer=0)
        collection.redraw([])
        collection.update_color(new_line_color='#0000FFFF', indexes=[k0])
        collection.redraw([])
        colors = collection._lines[0]._color
        self.assertTrue(np.all(colors[:2] == (0.0, 0.0, 1.0, 1.0)))
        self.assertTrue(np.all(colors[2:] == (1.0, 0.0, 0.0, 1.0)))


def _box(x, y, size=1.0):
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def _reference(c):
    """From-scratch join of the visible shapes in the order of c.data, per layer (the old rebuild)."""
    layers = len(c._meshes) + 1
    out = []
    for layer in range(layers):
        pts, cols, verts, tris, mcols = [], [], [], [], []
        count = 0
        for key, d in c.data.items():
            if not d['visible'] or 'line_pts' not in d:
                continue
            if (layers - 1 if key in c._top_keys else d['layer']) != layer:
                continue
            pts.append(d['line_pts'])
            cols.append(np.broadcast_to(d['line_rgba'], (len(d['line_pts']), 4)))
            if len(d['mesh_tris']) and len(d['mesh_vertices']):
                tris.append(d['mesh_tris'] + count)
                verts.append(d['mesh_vertices'])
                mcols.append(np.broadcast_to(d['mesh_rgba'], (len(d['mesh_vertices']), 4)))
                count += len(d['mesh_vertices'])
        line = (np.concatenate(pts), np.concatenate(cols)) if pts and len(np.concatenate(pts)) else None
        mesh = (np.concatenate(verts), np.concatenate(tris).reshape(-1, 3), np.concatenate(mcols)) if verts else None
        out.append((line, mesh))
    return out


def _submitted(c):
    """What the visuals got from the last rebuild, per layer."""
    out = []
    for line, mesh in zip(c._lines + [c._top_line], c._meshes + [c._top_mesh]):
        sub_line = (np.asarray(line._pos), np.asarray(line._color)) if line.visible else None
        sub_mesh = None
        if mesh.visible:
            sub_mesh = (mesh.vertices, mesh.faces, mesh.vertex_colors)
        out.append((sub_line, sub_mesh))
    return out


class TestBlockCache(unittest.TestCase):
    """The rebuild joins only the changed groups; the result is the same as a join of all the shapes."""

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
        self.options = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
        self.c = ShapeCollectionVisual(linewidth=1, layers=2, pool=None, fcoptions=self.options)
        self.groups = [ShapeGroup(self.c) for _ in range(3)]
        patcher = patch('appGUI.VisPyVisuals.set_state')
        patcher.start()
        self.addCleanup(patcher.stop)

    def fill(self, group, x0, layers=(0, 1)):
        for i, layer in enumerate(layers):
            group.add(shape=_box(x0 + 2 * i, 0), color='#FF0000FF', face_color='#00FF00FF', layer=layer)
        group.add(shape=LineString([(x0, 5), (x0 + 1, 6), (x0 + 2, 5)]), color='#0000FFFF', layer=layers[0])

    def check(self):
        self.c.redraw([])
        for layer, (got, want) in enumerate(zip(_submitted(self.c), _reference(self.c))):
            for g, w in zip(got, want):
                self.assertEqual(g is None, w is None, "layer %d" % layer)
                if w is not None:
                    for a, b in zip(g, w):
                        np.testing.assert_array_equal(a, b, err_msg="layer %d" % layer)

    def test_mixed_operations_equal_a_full_join(self):
        a, b, c3 = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.fill(c3, 20)
        self.check()
        b.visible = False
        self.check()
        b.visible = True
        a.move_to_top()
        self.check()
        c3.transform((1, 0, 0, -1, 3, 4))      # a mirror: the triangles are flipped
        self.check()
        b.redraw(update_colors=('#112233FF', '#445566FF'))
        self.check()
        c3.move_to_top()                        # the selection changes: a is normal again
        self.check()
        b.remove(b._indexes[1])
        self.check()
        a.clear()
        self.check()
        self.fill(a, 30)                        # a again, after the others
        self.check()
        a.update_visibility(False, indexes=a._indexes[:2])
        self.check()

    def test_hidden_then_recolored_then_shown(self):
        a, b, _ = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.check()
        a.visible = False
        self.check()
        a.redraw(update_colors=('#AA0000FF', '#00AA00FF'))
        a.visible = True
        self.check()
        mesh = self.c._meshes[0].vertex_colors
        np.testing.assert_allclose(mesh[0], (2 / 3, 0.0, 0.0, 1.0), atol=1e-6)

    def test_update_color_without_rebuild_changes_only_the_color_buffer(self):
        a, b, _ = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.check()
        mesh = self.c._meshes[0]
        positions, faces = mesh.vertices, mesh.faces
        self.c.update_color(new_mesh_color='#0000FFFF', indexes=b._indexes)
        colors = mesh.vertex_colors
        self.assertIs(mesh.vertices, positions)
        self.assertIs(mesh.faces, faces)
        self.assertEqual(len(colors), len(positions))
        in_b = positions[:, 0] >= 10
        self.assertTrue(np.all(colors[in_b] == (0.0, 0.0, 1.0, 1.0)))
        self.assertTrue(np.all(colors[~in_b] == (0.0, 1.0, 0.0, 1.0)))
        # the outlines too: new colors for the shapes of b, the positions are the same arrays
        line = self.c._lines[0]
        pos = line._pos
        self.c.update_color(new_line_color='#FF00FFFF', indexes=b._indexes)
        self.assertIs(line._pos, pos)
        in_b = pos[:, 0] >= 10
        self.assertTrue(np.all(line._color[in_b] == (1.0, 0.0, 1.0, 1.0)))
        self.assertFalse(np.any(np.all(line._color[~in_b] == (1.0, 0.0, 1.0, 1.0), axis=1)))
        self.check()

    def test_segment_line_visual_ignores_a_wrong_color_count_and_draws_no_old_data(self):
        from appGUI.VisPyVisuals import _new_line_visual
        line = _new_line_visual()
        pos = np.zeros((4, 2), dtype=np.float32)
        line.set_data(pos=pos, color=np.ones((4, 4), dtype=np.float32), width=1)
        line.set_colors(np.zeros((6, 4), dtype=np.float32))      # the count does not fit: ignored
        self.assertTrue(np.all(np.asarray(line._color) == 1.0))
        line.set_colors(np.zeros((4, 4), dtype=np.float32))
        self.assertTrue(np.all(np.asarray(line._color) == 0.0))

    def test_pending_pool_results_are_collected_later(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup, _update_shape_buffers

        class Result:
            def __init__(self, items):
                self.items = items

            def ready(self):
                return True

            def wait(self):
                pass

            def get(self):
                return [_update_shape_buffers(dict(d)) for d in self.items]

        class Pool:
            def map_async(self, func, items):
                return Result(list(items))

        c = ShapeCollectionVisual(linewidth=1, layers=2, pool=Pool(), fcoptions={"global_backface_culling": True})
        self.c = c
        g1, g2 = ShapeGroup(c), ShapeGroup(c)
        g1.add_batch([{'shape': _box(0, 0), 'color': '#FF0000FF', 'face_color': '#00FF00FF', 'layer': 0}])
        g2.add_batch([{'shape': _box(5, 0), 'color': '#FF0000FF', 'face_color': '#0000FFFF', 'layer': 1}])
        # the shapes are placeholders until the results are collected
        c._update_deferred()
        self.assertTrue(all(sub[1] is None for sub in _submitted(c)))
        self.check()
        self.assertIsNotNone(_submitted(c)[1][1])

    def test_failed_rebuild_is_repaired_by_the_next_one(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        a, b, c3 = self.groups
        for g, x in zip(self.groups, (0, 10, 20)):
            self.fill(g, x)
        self.check()
        # no rebuild yet: two blocks are dirty
        self.c.update_visibility(False, indexes=a._indexes + b._indexes)
        real = ShapeCollectionVisual._join_block
        calls = []

        def flaky(self_, block):
            calls.append(block)
            if len(calls) == 2:
                raise RuntimeError("join failed")
            return real(self_, block)

        with patch.object(ShapeCollectionVisual, '_join_block', flaky):
            with self.assertRaises(RuntimeError):
                self.c.redraw([])
        self.check()


# ======================================================================
# merged from tests/test_vispy_only_settings.py
# ======================================================================
"""Retired renderer selection must not survive persistence boundaries."""
import json
from unittest.mock import MagicMock, patch

import pytest

from defaults import AppDefaults, AppOptions


@pytest.mark.parametrize('engine', [None, '2D'])
def test_settings_migration(tmp_path, engine):
    payload = {'global_graphic_engine_3d_no_mp': True, 'unrelated_setting': {'value': 7}}
    if engine is not None:
        payload['global_graphic_engine'] = engine
    filename = tmp_path / 'settings.FlatConfig'
    filename.write_text(json.dumps(payload))
    inform = MagicMock()
    defaults = AppDefaults(beta=False)
    defaults.load(str(filename), inform)
    options = AppOptions(version='test', baseline=payload)
    for snapshot in (AppDefaults.factory_defaults, defaults.defaults, defaults.current_defaults,
                     options.baseline, options.options, options.current_options):
        assert 'global_graphic_engine' not in snapshot
    options.load(str(filename), inform, baseline=payload)
    for snapshot in (defaults.defaults, defaults.current_defaults, options.baseline,
                     options.options, options.current_options):
        assert 'global_graphic_engine' not in snapshot
        assert snapshot['global_graphic_engine_3d_no_mp'] is True
        assert snapshot['unrelated_setting'] == {'value': 7}
    assert ('global_graphic_engine' in payload) is (engine is not None)
    options.load(str(filename), inform)
    assert 'global_graphic_engine' not in options.current_options
    assert 'global_graphic_engine' not in AppOptions(version='test').baseline


@pytest.mark.parametrize('cli', [False])
def test_project_open_loads_only_project_options(cli):
    from PyQt6 import QtWidgets
    from appHandlers.appIO import appIO

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    defaults = AppDefaults(beta=False)
    defaults['global_appearance'] = 'light'
    app = MagicMock()
    app.defaults = defaults
    app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    app.resource_location = 'assets/resources'
    app.app_units = 'MM'
    handler = appIO(app)
    handler.on_file_new_project = MagicMock()
    project = {'objs': [], 'options': {'global_graphic_engine': '2D', 'global_appearance': 'dark',
                                     'gerber_bboxmargin': 0.75, 'units': 'inch'}}
    with patch('appHandlers.appIO.FCMessageBox') as message:
        assert handler._restore_project_handler(project, 'board.FlatPrj', True, True, cli, False) is True
    message.assert_not_called()
    # the units of the project become the units of the app
    assert app.options['units'] == 'IN'
    assert app.app_units == 'IN'
    assert defaults['global_appearance'] == 'light'
    assert app.options['global_appearance'] == 'light'
    assert app.options['gerber_bboxmargin'] == 0.75
    assert app.options['gerber_bboxrounded'] == defaults['gerber_bboxrounded']
    assert 'global_graphic_engine' not in list(defaults)
    # the Preferences form reads the project when it is shown; a load does not rewrite every field
    app.preferencesUiManager.defaults_write_form.assert_not_called()
    assert project['options']['global_graphic_engine'] == '2D'


# ======================================================================
# merged from tests/test_vispy_png_export.py
# ======================================================================
"""PNG export keeps the VisPy pixels without requiring a GPU or backend flag."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from appHandlers.appIO import appIO


def make_handler(open_style=False):
    app = SimpleNamespace(
        options={'global_open_style': open_style}, defaults={},
        get_last_save_folder=lambda: 'saved', file_opened=Mock(), file_saved=Mock())
    return SimpleNamespace(app=app, log=Mock(), inform=Mock())


@pytest.mark.parametrize('channels,open_style', [(3, False), (4, True)])
def test_export_preserves_pixels_without_backend_flag(tmp_path, channels, open_style):
    handler = make_handler(open_style)
    pixels = np.array([[[255, 0, 31, 255], [0, 255, 63, 128]],
                       [[17, 32, 255, 0], [91, 73, 55, 64]]], dtype=np.uint8)[:, :, :channels]
    filename = str(tmp_path / 'capture.png')
    with patch('appHandlers.appIO._screenshot', return_value=pixels) as screenshot, \
            patch('appHandlers.appIO.FCFileSaveDialog.get_saved_filename',
                  return_value=(filename, '')) as dialog:
        appIO.on_file_export_png(handler)
    screenshot.assert_called_once_with(alpha=False)
    assert dialog.call_args.kwargs['directory'].startswith('saved/png_')
    with Image.open(filename) as image:
        np.testing.assert_array_equal(np.asarray(image), pixels)
    handler.app.file_saved.emit.assert_called_once_with('png', filename)
    if open_style:
        handler.app.file_opened.emit.assert_not_called()
    else:
        handler.app.file_opened.emit.assert_called_once_with('png', filename)




# ======================================================================
# merged from tests/test_cncjob_plot_checkboxes.py
# ======================================================================
"""CNCJobObject: the object 'Plot' checkbox and the plot checkboxes of the tool table follow each other."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from appObjects.CNCJobObject import CNCJobObject
from tests.helpers_objects import _headless_qt, make_app_0b  # noqa: F401,E402


class FakeCheckBox:
    def __init__(self, checked):
        self.checked = checked
        self.clicked = Mock()
        self.stateChanged = Mock()

    def isChecked(self):
        return self.checked

    def setChecked(self, val):
        self.checked = bool(val)


class FakeTable:
    def __init__(self, rows):
        # rows: list of (diameter text, tool id text, checkbox or None)
        self.rows = rows

    def rowCount(self):
        return len(self.rows)

    def item(self, row, col):
        dia, tid, _cb = self.rows[row]
        return Mock(text=Mock(return_value=dia if col == 1 else tid))

    def cellWidget(self, row, col):
        return self.rows[row][2] if col == 6 else None


def make_job(kind, rows, plot=True):
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', make_app_0b())
    job.obj_options['type'] = kind
    job.obj_options['plot'] = plot
    job.muted_ui = False
    job.read_form_item = Mock()
    job.show_plotted_shapes = Mock(return_value=True)
    job.plot = Mock()
    job.plot2 = Mock()
    job.shapes = Mock()
    table = FakeTable(rows)
    empty = FakeTable([])
    job.ui = SimpleNamespace(
        plot_cb=FakeCheckBox(plot), cncplot_method_combo=Mock(get_value=Mock(return_value='all')),
        exc_cnc_tools_table=table if kind == 'excellon' else empty,
        cnc_tools_table=empty if kind == 'excellon' else table)
    job.tools = {int(tid): {'tooldia': float(dia), 'gcode_parsed': ['x']} for dia, tid, _cb in rows}
    return job, table


def test_tool_without_diameter_or_cut_z_does_not_break_the_plot_checkbox_or_the_drill_table():
    rows = [('0.8000', '1', FakeCheckBox(True))]
    job, _ = make_job('geometry', rows)
    job.tools = {1: {'gcode_parsed': ['x']}}
    job.on_plot_cb_click_table()
    job.plot2.assert_called_once()

    job, table = make_job('excellon', rows)
    job.app.dec_format = round
    job.used_tools = [1]
    job.z_cut = -1.5
    job.tools = {1: {'tooldia': 0.8, 'data': {}, 'gcode_parsed': []}}
    job.ui.exc_cnc_tools_table = MagicMock()
    job.build_excellon_cnc_tools()
    cut_z_item = job.ui.exc_cnc_tools_table.setItem.call_args_list[-1].args[2]
    assert float(cut_z_item.text()) == pytest.approx(-1.5)


class _SelTable:
    """The tool table of the GCode Editor: rows of (display index, uid) and the selected rows."""
    def __init__(self, rows, selected):
        self.rows = rows
        self.selected = selected

    def selectionModel(self):
        return SimpleNamespace(selectedIndexes=lambda: [SimpleNamespace(row=lambda r=r: r) for r in self.selected])

    def item(self, row, col):
        if row < 3 or col not in (0, 4, 5):
            return Mock(text=Mock(return_value='0.8'))
        index, uid = self.rows[row - 3]
        return Mock(text=Mock(return_value=index if col == 0 else uid))


def test_gcode_editor_rows_with_empty_header_start_and_tools_keyed_by_uid():
    from PyQt6 import QtWidgets
    from appEditors.appGCodeEditor import AppGCodeEditor

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    blocks = ['T7\nG1 X1\n', 'T9\nG1 X2\nM5\n']
    edit_area = QtWidgets.QPlainTextEdit()
    edit_area.setPlainText(''.join(blocks))
    editor = AppGCodeEditor.__new__(AppGCodeEditor)
    editor.edit_area = edit_area
    editor.decimals = 4
    editor.app = SimpleNamespace(dec_format=round)
    editor.gcode_obj = SimpleNamespace(
        obj_options={'type': 'Geometry'}, gc_header='', gc_start='', gcode_imported=True,
        tools={7: {'tool': 7, 'gcode': blocks[0]}, 9: {'tool': 9, 'gcode': blocks[1]}})
    # the display index (1, 2) is neither the key of the tool nor its T number
    editor.ui = SimpleNamespace(cnc_tools_table=_SelTable([('1', '7'), ('2', '9')], [4]))

    editor.on_row_selection_change()

    assert edit_area.textCursor().selectedText().replace(' ', '\n') == blocks[1]


# ======================================================================
# merged from tests/test_cncjob_plot_preparation.py
# ======================================================================
"""Plot preparation regressions and a repeatable CPU plotting benchmark.

Run from the repository root:
    .venv/Scripts/python tests/test_cncjob_plot_preparation.py
    .venv/Scripts/python tests/test_cncjob_plot_preparation.py --benchmark --buffers

The benchmark includes real tessellation and VisPy buffer submission with --buffers.
It doesn't measure GPU drawing or application startup. Input construction isn't timed.
"""
import argparse
import ast
import cProfile
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import LineString, Polygon

from camlib import CNCjob


COLORS = {'T': ['#F0C0404C', '#B080304C'], 'C': ['#4080FFFF', '#204080FF']}


def make_job_m7(paths, source='geometry'):
    job = SimpleNamespace(
        gcode_parsed=paths, tooldia=0.8, steps_per_circle=16,
        obj_options={'type': source}, annotations_dict={}, batch=[])
    job._app_option = lambda key: 'G90'
    job.add_shapes_batch = lambda batch, **kwargs: job.batch.extend(batch)
    return job


def make_paths(count):
    paths = []
    for i in range(count):
        x, y = (i % 200) * 2.0, (i // 200) * 2.0
        coords = [(x, y), (x + 0.3, y + 0.2), (x + 0.8, y + 0.1), (x + 1, y + 0.5)]
        paths.append({'geom': LineString(coords), 'kind': ['T' if i % 2 == 0 else 'C', 'F']})
    return paths


def plot(job, dia=0.8, kind='all'):
    return CNCjob.plot2(job, tooldia=dia, obj=job, visible=True, kind=kind, color=COLORS)


def fingerprint(job):
    digest = hashlib.sha256()
    for item in job.batch:
        digest.update(item['shape'].wkb)
        digest.update(repr({k: v for k, v in item.items() if k != 'shape'}).encode())
    digest.update(json.dumps(job.annotations_dict, sort_keys=True).encode())
    return digest.hexdigest()


class TestCNCJobPlotPreparation(unittest.TestCase):

    def test_native_tessellation_preserves_holes_and_counterclockwise_faces(self):
        import numpy as np
        import shapely
        from appGUI.VisPyTesselators import GLUTess

        if not hasattr(shapely, 'constrained_delaunay_triangles'):
            self.skipTest('Native constrained triangulation requires Shapely 2.1+')
        polygon = Polygon([(0, 0), (0, 6), (6, 6), (6, 0), (3, 2)],
                          holes=[[(1, 2), (1, 3), (2, 3), (2, 2)]])
        with patch('appGUI.VisPyTesselators.GLU.gluNewTess',
                   side_effect=AssertionError('Valid polygons should avoid Python GLU callbacks')):
            indices, vertices = GLUTess().triangulate(polygon)
        triangles = np.asarray(vertices)[np.asarray(indices).reshape(-1, 3)]
        a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        self.assertTrue((cross > 0).all())
        self.assertAlmostEqual(cross.sum() / 2, polygon.area)
        for triangle in triangles:
            self.assertTrue(polygon.covers(Polygon(triangle)))

    def test_geometry_colors_layers_and_annotations(self):
        paths = make_paths(12)
        for source in ('geometry', 'excellon'):
            if source == 'excellon':
                paths = [dict(p, geom=LineString(list(p['geom'].coords) + [p['geom'].coords[0]]))
                         if p['kind'][0] == 'C' else p for p in paths]
            for kind in ('all', 'travel', 'cut'):
                for dia in (0, 0.8):
                    with self.subTest(source=source, kind=kind, dia=dia):
                        job = make_job_m7(paths, source)
                        plot(job, dia, kind)
                        selected = [p for p in paths if kind == 'all' or p['kind'][0] == kind[0].upper()]
                        self.assertEqual(len(job.batch), len(selected))
                        for item, path in zip(job.batch, selected):
                            code = path['kind'][0]
                            expected = path['geom']
                            if dia:
                                expected = (Polygon(expected) if source == 'excellon' and code == 'C'
                                            else expected.buffer(dia / 1.99999999, 16 // 4))
                                expected = expected.simplify(0.0005)
                                self.assertEqual(item['face_color'], COLORS[code][0])
                                self.assertEqual(item['layer'], 1 if code == 'C' else 2)
                            self.assertTrue(item['shape'].equals_exact(expected, 0))
                            self.assertEqual(item['color'], COLORS[code][1])
                        positions = [pos for p in paths if p['kind'][0] == 'T'
                                     for pos in (p['geom'].coords[0], p['geom'].coords[-1])]
                        if dia:
                            self.assertEqual(job.annotations_dict[dia]['pos'], positions)
                            self.assertEqual(job.annotations_dict[dia]['text'],
                                             [str(i + 1) for i in range(len(positions))])
                        else:
                            self.assertEqual(job.annotations_dict, {})

def benchmark(args):
    plot_method = CNCjob.plot2
    if args.buffers:
        import appGUI.VisPyVisuals as visuals
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        from vispy.gloo.context import FakeCanvas
    if args.baseline_ref:
        import camlib
        import appGUI.VisPyTesselators as tessellators

        def historical_symbols(path, names, namespace):
            source = subprocess.check_output(['git', 'show', f'{args.baseline_ref}:{path}'],
                                             cwd=Path(__file__).resolve().parents[1], encoding='utf-8')
            tree = ast.parse(source)
            nodes = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
            return namespace

        namespace = historical_symbols('camlib.py', {'plot2'}, dict(vars(camlib)))
        plot_method = namespace['plot2']
        if args.buffers:
            old_tess = historical_symbols('appGUI/VisPyTesselators.py', {'GLUTess'}, dict(vars(tessellators)))
            namespace = dict(vars(visuals), GLUTess=old_tess['GLUTess'])
            historical_symbols('appGUI/VisPyVisuals.py',
                               {'_update_shape_buffers', '_linearring_to_segments', '_linestring_to_segments'}, namespace)
            visuals._update_shape_buffers = namespace['_update_shape_buffers']
    rows = []
    for count in args.sizes:
        paths = make_paths(count)
        for dia, kind in ((0.8, 'all'), (0.8, 'travel'), (0.8, 'cut'), (0, 'all')):
            times = []
            for repeat in range(args.repeat + 1):
                job = make_job_m7(paths)
                if args.buffers:
                    # Don't accumulate submission commands between independent repetitions.
                    canvas = FakeCanvas()
                start = perf_counter()
                plot_method(job, tooldia=dia, obj=job, visible=True, kind=kind, color=COLORS)
                prepared = perf_counter()
                if args.buffers:
                    collection = ShapeCollectionVisual(fcoptions={'global_graphic_engine_3d_no_mp': True})
                    collection.add_batch(job.batch, tolerance=0.001, visible=True)
                buffered = perf_counter()
                if args.buffers:
                    collection.redraw()
                end = perf_counter()
                if repeat:
                    times.append((prepared - start, buffered - prepared, end - buffered, end - start))
            row = dict(paths=count, diameter=dia, kind=kind, shapes=len(job.batch), fingerprint=fingerprint(job))
            row.update(zip(('prepare_s', 'buffers_s', 'submit_s', 'total_s'),
                           (statistics.median(t[i] for t in times) for i in range(4))))
            rows.append(row)
            print(json.dumps(row), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')
    if args.profile:
        job = make_job_m7(make_paths(max(args.sizes)))
        profiler = cProfile.Profile()
        profiler.runcall(plot_method, job, tooldia=0.8, obj=job, visible=True, color=COLORS)
        profiler.print_stats(sort='cumulative')


# ======================================================================
# merged from tests/test_plot_manager_color_action.py
# ======================================================================
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appHandlers.appPlotManager import AppPlotManager


class ColorActionApp(QtCore.QObject):
    def __init__(self, selected):
        super().__init__()
        for name in ('log', 'inform', 'worker_task', 'plotcanvas', 'clear_pool',
                     'app_obj', 'proc_container'):
            setattr(self, name, Mock())
        self.options = {}
        self.defaults = {'gerber_plot_fill': '#00FF00BF', 'gerber_plot_line': '#006600BF'}
        self.collection = Mock()
        self.collection.get_selected.return_value = [selected]
        self.collection.get_list.return_value = [selected]
        self.collection.group_items = {'gerber': Mock()}
        self.plot_manager = AppPlotManager(self)
        self.errors = []

    def on_set_color_action_triggered(self):
        try:
            self.plot_manager.on_set_color_action_triggered()
        except Exception as error:
            self.errors.append(error)


class PlotManagerColorActionTests(unittest.TestCase):
    def test_action_through_app_facade_updates_selected_object(self):
        qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        selected = SimpleNamespace(kind='excellon', alpha_level='BF', shapes=Mock())
        app = ColorActionApp(selected)
        action = QtGui.QAction('Red', app)
        action.triggered.connect(app.on_set_color_action_triggered)

        action.trigger()

        self.assertEqual(app.errors, [])
        self.assertEqual(selected.fill_color, '#FF0000BF')
        selected.shapes.redraw.assert_called_once_with(
            update_colors=(selected.fill_color, selected.outline_color))
        self.assertEqual(app.options['excellon_color'],
                         (selected.outline_color, selected.fill_color))


# ======================================================================
# merged from tests/test_shape_collection_layer_check.py
# ======================================================================
"""ShapeCollectionVisual.add / add_batch reject a layer outside 0..len(meshes)-1 with ValueError.

A rejected call changes nothing: data, last_key and the line width stay as they were and key_lock stays free.
"""

import pytest
from shapely.geometry import Polygon

from appGUI.VisPyVisuals import ShapeCollectionVisual

SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
OPTIONS = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}


@pytest.fixture
def coll():
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None, fcoptions=OPTIONS)


def _state(c):
    return dict(c.data), c.last_key, c._line_width


def _assert_lock_free(c):
    assert c.key_lock.acquire(False), 'key_lock is still held'
    c.key_lock.release()


@pytest.mark.parametrize('layer', [3, None])
def test_add_rejects_bad_layer(coll, layer):
    before = _state(coll)
    with pytest.raises(ValueError):
        coll.add(shape=SQUARE, layer=layer)
    assert _state(coll) == before
    _assert_lock_free(coll)
    key = coll.add(shape=SQUARE, layer=2)
    assert coll.data[key]['layer'] == 2


class _FakeBatch:
    """Stands in for the pool AsyncResult. The worker runs when the batch is submitted, on copies, like a pickled call.

    forbid_block: wait() and get() fail the test, so a method that must not block shows up as an error.
    """

    def __init__(self, results, forbid_block=False):
        self._results = results
        self._forbid_block = forbid_block
        self._ready = False

    def ready(self):
        return self._ready

    def finish(self):
        self._ready = True

    def wait(self, timeout=None):
        assert not self._forbid_block, 'wait() blocks the GUI thread'
        self._ready = True

    def get(self, timeout=None):
        assert not self._forbid_block, 'get() blocks the GUI thread'
        return self._results


class _FakePool:
    def __init__(self, forbid_block=False):
        self.forbid_block = forbid_block
        self.batches = []

    def map_async(self, fn, items):
        batch = _FakeBatch([fn(dict(d)) for d in items], self.forbid_block)
        self.batches.append(batch)
        return batch


def _pooled(pool):
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=pool, fcoptions={'global_backface_culling': True})


def test_pending_ready_is_false_while_batch_pending_and_true_after_it_finishes_without_blocking():
    pool = _FakePool(forbid_block=True)
    c = _pooled(pool)
    c.add_batch([{'shape': SQUARE, 'color': '#000000', 'face_color': '#ff0000'}])
    assert c.pending_ready() is False
    pool.batches[-1].finish()
    assert c.pending_ready() is True


def test_pending_ready_is_true_without_a_pending_batch():
    c = _pooled(_FakePool())
    assert c.pending_ready() is True


def test_shape_hidden_while_its_batch_is_pending_stays_hidden_after_collect():
    c = _pooled(_FakePool())
    keys = c.add_batch([{'shape': SQUARE, 'color': '#000000', 'face_color': '#ff0000'}], visible=True)
    c.update_visibility(False, indexes=keys)
    assert c.data[keys[0]]['visible'] is False

    c._collect_results()

    assert 'geometry' not in c.data[keys[0]], 'the batch result did not replace the record'
    assert c.data[keys[0]]['visible'] is False


# ======================================================================
# merged from tests/test_shape_transform.py
# ======================================================================
#!/usr/bin/env python
"""
Tests for ShapeCollectionVisual.transform_keys(): the plot of a transformed object is made by mapping the stored
vertex arrays, with the same result as a new triangulation of the transformed geometry.
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, LineString

from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
from appObjects.AppObjectTemplate import plot_transformed_many
from tests.helpers_objects import make_app, make_same_diameter_excellon

OPTS = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}


def new_collection():
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None, fcoptions=OPTS)


def sample_shapes():
    holed = Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(2, 2), (4, 2), (4, 4), (2, 4)]])
    tri = Polygon([(12, 1), (15, 1), (13, 5)])
    line = LineString([(0, 0), (3, 4), (6, 1)])
    return [holed, tri, line]


def to_affine(matrix, geo):
    return affinity.affine_transform(geo, matrix)


def sorted_pts(arr):
    arr = np.round(np.asarray(arr, dtype=np.float64), 3)
    return arr[np.lexsort((arr[:, 1], arr[:, 0]))]


def tri_area_signed(verts, tris):
    t = tris.reshape(-1, 3)
    p0, p1, p2 = verts[t[:, 0]], verts[t[:, 1]], verts[t[:, 2]]
    cross = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    return cross / 2.0


class TestTransformKeys(unittest.TestCase):
    def setUp(self):
        # the merge of the buffers into the GL visuals needs a canvas; only the stored arrays are tested here
        p = patch.object(ShapeCollectionVisual, '_ShapeCollectionVisual__update')
        p.start()
        self.addCleanup(p.stop)

    MATRICES = {
        'rotate': (0.0, -1.0, 1.0, 0.0, 0.0, 0.0),
        'rotate30': (np.cos(0.5236), -np.sin(0.5236), np.sin(0.5236), np.cos(0.5236), 3.0, -2.0),
        'mirror_x': (1.0, 0.0, 0.0, -1.0, 0.0, 20.0),
        'mirror_y': (-1.0, 0.0, 0.0, 1.0, 20.0, 0.0),
        'skew': (1.0, 0.5, 0.2, 1.0, 0.0, 0.0),
        'scale': (2.0, 0.0, 0.0, 3.0, 0.0, 0.0),
        'offset': (1.0, 0.0, 0.0, 1.0, 100.0, -50.0),
    }

    def build(self):
        c = new_collection()
        keys = c.add_batch([{'shape': s, 'color': 'red', 'face_color': 'blue'} for s in sample_shapes()])
        c.redraw([])
        return c, keys

    def test_matches_fresh_triangulation(self):
        for name, m in self.MATRICES.items():
            with self.subTest(name):
                c, keys = self.build()
                self.assertTrue(c.transform_keys(keys, m))

                ref = new_collection()
                rkeys = ref.add_batch([{'shape': to_affine(m, s), 'color': 'red', 'face_color': 'blue'}
                                       for s in sample_shapes()])
                ref.redraw([])

                for k, rk, shape in zip(keys, rkeys, sample_shapes()):
                    d, r = c.data[k], ref.data[rk]
                    self.assertEqual(d['line_pts'].dtype, np.float32)
                    self.assertEqual(d['line_pts'].shape, r['line_pts'].shape)
                    np.testing.assert_allclose(sorted_pts(d['line_pts']), sorted_pts(r['line_pts']), atol=2e-3)
                    if len(r['mesh_vertices']):
                        np.testing.assert_allclose(sorted_pts(d['mesh_vertices']), sorted_pts(r['mesh_vertices']),
                                                   atol=2e-3)
                        area = to_affine(m, shape).area
                        areas = tri_area_signed(d['mesh_vertices'].astype(np.float64), d['mesh_tris'])
                        ref_areas = tri_area_signed(r['mesh_vertices'].astype(np.float64), r['mesh_tris'])
                        # the winding is the same as that of a fresh triangulation: all the same sign
                        self.assertEqual(np.sign(areas.sum()), np.sign(ref_areas.sum()))
                        self.assertTrue(np.all(np.sign(areas) == np.sign(areas.sum())))
                        self.assertAlmostEqual(abs(areas.sum()), area, delta=1e-3 * max(1.0, area))
                    # colors are kept
                    np.testing.assert_array_equal(d['mesh_rgba'], r['mesh_rgba'])
                    np.testing.assert_array_equal(d['line_rgba'], r['line_rgba'])


    def test_excellon_scale_replots_round_holes_in_the_cached_shapes(self):
        # the cached plot of a scaled Excellon must be made again from the rebuilt geometry, not mapped:
        # a 1 mm hole at x=10 keeps the 9.5 .. 10.5 bounds instead of becoming 9 .. 11
        app = make_app()
        obj = make_same_diameter_excellon(app)
        collection = new_collection()
        obj.shapes = ShapeGroup(collection)

        obj.scale(2, 1, point=(0, 0))
        self.assertFalse(obj.plot_transformed((2.0, 0.0, 0.0, 1.0, 0.0, 0.0)))

        hole = collection.data[0]['line_pts']
        self.assertAlmostEqual(float(hole[:, 0].min()), 9.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 0].max()), 10.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 1].min()), 4.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 1].max()), 5.5, delta=0.02)

        slot = collection.data[3]['line_pts']
        self.assertAlmostEqual(float(slot[:, 0].min()), 39.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 0].max()), 52.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 1].min()), 4.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 1].max()), 5.5, delta=0.02)


class TestPlotTransformedMany(unittest.TestCase):
    MATRIX = (1.0, 0.0, 0.0, 1.0, 3.0, -2.0)

    @classmethod
    def setUpClass(cls):
        # the merge of the buffers into the GL visuals needs a canvas; a fake one is enough
        from vispy.gloo.context import FakeCanvas
        cls._canvas = FakeCanvas()

    def test_batch_maps_all_objects_with_one_redraw(self):
        app = make_app()
        shared = new_collection()
        objs = [make_same_diameter_excellon(app, name=n) for n in ('a', 'b')]
        for obj in objs:
            obj.shapes = ShapeGroup(shared)
            obj.plot()
        objs[1].offset((20, 0))
        objs[1].plot()
        for obj in objs:
            obj.offset((3, -2))

        with patch.object(ShapeCollectionVisual, '_ShapeCollectionVisual__update') as update:
            self.assertEqual(plot_transformed_many(objs, self.MATRIX), [True, True])
        self.assertEqual(update.call_count, 1)

        ref = new_collection()
        for obj in objs:
            obj.shapes = ShapeGroup(ref)
            obj.plot()
        got = sorted_pts(np.concatenate([shared.data[k]['line_pts'] for k in sorted(shared.data)]))
        want = sorted_pts(np.concatenate([ref.data[k]['line_pts'] for k in sorted(ref.data)]))
        np.testing.assert_allclose(got, want, atol=2e-3)

    def test_cncjob_with_annotations_is_mapped_without_a_new_plot(self):
        from collections import defaultdict
        from unittest.mock import Mock
        from appGUI.VisPyVisuals import TextCollectionVisual, TextGroup
        from appObjects.CNCJobObject import CNCJobObject

        app = make_app()
        app.preprocessors = defaultdict(Mock)
        job = CNCJobObject('job', app)
        paths = [Polygon([(0, 0), (4, 0), (4, 3), (3.8, 3), (3.8, 0.2), (0, 0.2)]), Polygon([(8, 1), (8.4, 1), (9, 5)])]
        collection = new_collection()
        job.shapes = ShapeGroup(collection)
        job.shapes.add_batch([{'shape': p, 'color': '#FF0000FF', 'face_color': '#00FF00FF'} for p in paths])
        job.mark_full_plot()
        positions = [(0.0, 0.0), (4.0, 3.0), (8.0, 1.0)]
        job.annotation = TextGroup(TextCollectionVisual())
        job.annotation.set(text=['1', '2', '3'], pos=list(positions))
        job.annotations_dict = {0.2: {'pos': list(positions), 'text': ['1', '2', '3']}}
        job.plot = Mock()

        self.assertTrue(job.plot_transformed(self.MATRIX))
        job.plot.assert_not_called()

        ref = new_collection()
        ref.add_batch([{'shape': to_affine(self.MATRIX, p), 'color': '#FF0000FF', 'face_color': '#00FF00FF'}
                       for p in paths])
        for k, rk in zip(sorted(collection.data), sorted(ref.data)):
            np.testing.assert_allclose(sorted_pts(collection.data[k]['mesh_vertices']),
                                       sorted_pts(ref.data[rk]['mesh_vertices']), atol=2e-3)
        want = [(x + 3.0, y - 2.0) for x, y in positions]
        np.testing.assert_allclose(job.annotation._collection.data[0]['pos'], want)
        np.testing.assert_allclose(job.annotations_dict[0.2]['pos'], want)
