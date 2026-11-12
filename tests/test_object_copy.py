"""Stage 4: one ``copy_from(src)`` per object kind, used by both copy paths of appHandlers/appObjectOps.py
(``on_copy_command`` and ``on_copy_object2``).

The 2 real paths run headless with a factory that creates the object and calls ``initialize`` (as in
tests/helpers_objects.py). For each kind and path, every attribute that copy_from specifies must equal the
source, and the copy must not share data with the source (mutate either one, the other stays the same).

XFAIL_OLD lists the (kind, path, attribute) combinations the OLD per-path code lost (all fixed by copy_from, so it is empty now).
Old-code losses: aperture_macros in on_copy_command; follow_geometry and source_file in on_copy_object2 (Gerber);
multigeo / tooluid (Geometry); multigeo, int_digits, frac_digits (Gerber); the Excellon file format attributes.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PyQt6.QtCore import QObject
from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry

from appHandlers.appObjectOps import AppObjectOps
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject

from tests.helpers_objects import (  # noqa: F401,E402
    app, excellon, _headless_qt, make_gerber,
    make_same_diameter_excellon, pts, run_copy, slot_coords, snapshot_excellon,
)

CLASSES = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject,
           'script': ScriptObject, 'document': DocumentObject}

# ---------------------------------------------------------------------------------------------------------------
# running the 2 real paths headless
# ---------------------------------------------------------------------------------------------------------------

def make_new(kind, name, app):
    if kind in ('script', 'document'):
        return bare(CLASSES[kind], name, app, kind)
    return CLASSES[kind](name, app)


def run_path(app, source, kind, path):
    """Run on_copy_command() / on_copy_object2() headless; returns the new object (or 'fail')."""
    created = {}

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        assert obj_kind == kind
        new_obj = make_new(kind, name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        created[name] = new_obj
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.f_handlers = SimpleNamespace(export_excellon=Mock(return_value='REGENERATED\n'))
    app.collection.get_selected.return_value = [source]
    ops = AppObjectOps(app)
    if path == 'command':
        ops.on_copy_command()
        name = source.obj_options['name'] + '_copy'
    else:
        ops.on_copy_object2('_c2')
        name = source.obj_options['name'] + '_c2'
    assert list(created) == [name]
    return created[name]


def norm(value):
    """A comparable, deep snapshot of a value (shapely geometries as WKB)."""
    if isinstance(value, BaseGeometry):
        return value.wkb_hex
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [norm(v) for v in value]
    if hasattr(value, '__dict__') and not isinstance(value, type):
        return (type(value).__name__, norm(vars(value)))
    return value


# ---------------------------------------------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------------------------------------------

def gerber_source(app):
    obj = make_gerber(app)
    assert obj.aperture_macros and obj.follow_geometry
    obj.export_as_regions = True
    obj.multigeo = True
    obj.int_digits = 2
    obj.frac_digits = 5
    obj.units = 'MM'
    obj.obj_options['name'] = 'grb'
    return obj


def excellon_source(app):
    obj = make_same_diameter_excellon(app)
    obj.source_file = 'M48\nMETRIC\nT01C1.0\n%\nM30\n'
    obj.zeros = 'T'
    obj.excellon_format_upper_mm = 2
    obj.excellon_format_lower_mm = 5
    obj.excellon_format_upper_in = 1
    obj.excellon_format_lower_in = 6
    obj.excellon_units = 'MM'
    obj.obj_options['name'] = 'exc'
    return obj


def geometry_source(app):
    obj = GeometryObject('geo', app)
    obj.units = 'MM'
    obj.multigeo = True
    obj.tooluid = 2
    shape_a = [box(0, 0, 4, 4), box(10, 10, 12, 13)]
    shape_b = [LineString([(0, 0), (5, 5)]).buffer(0.5)]
    obj.tools = {
        1: {'tooldia': 1.0, 'data': {'cutz': -0.5, 'name': 'first'}, 'solid_geometry': shape_a},
        2: {'tooldia': 2.0, 'data': {'cutz': -1.5, 'name': 'second'}, 'solid_geometry': shape_b},
    }
    obj.solid_geometry = shape_a + shape_b
    obj.follow_geometry = [LineString([(0, 0), (5, 5)])]
    obj.source_file = 'geometry source text'
    obj.obj_options['name'] = 'geo'
    return obj


def bare(cls, name, app, kind):
    """ScriptObject / DocumentObject cannot be constructed headless: FlatCAMObj.__init__ calls
    super().__init__(app=app), which reaches QObject for the classes without a camlib base (PyQt6 raises
    TypeError). The copy paths only need obj_options['name'], kind and source_file."""
    obj = cls.__new__(cls)
    QObject.__init__(obj)
    obj.app = app
    obj.kind = kind
    obj.obj_options = {'name': name}
    obj.source_file = ''
    return obj


def script_source(app):
    obj = bare(ScriptObject, 'scr', app, 'script')
    obj.source_file = 'puts "hello"\nnew_geometry g\n'
    return obj


def document_source(app):
    obj = bare(DocumentObject, 'doc', app, 'document')
    obj.source_file = 'Some document text\nsecond line\n'
    return obj


SOURCES = {'gerber': gerber_source, 'excellon': excellon_source, 'geometry': geometry_source,
           'script': script_source, 'document': document_source}

# the attributes copy_from specifies, per kind
ATTRS = {
    'gerber': ['solid_geometry', 'follow_geometry', 'tools', 'aperture_macros', 'source_file', 'export_as_regions',
               'multigeo', 'int_digits', 'frac_digits', 'units'],
    'excellon': ['tools', 'solid_geometry', 'source_file', 'units', 'zeros', 'excellon_format_upper_mm',
                 'excellon_format_lower_mm', 'excellon_format_upper_in', 'excellon_format_lower_in',
                 'excellon_units'],
    'geometry': ['solid_geometry', 'follow_geometry', 'tools', 'multigeo', 'tooluid', 'source_file', 'units'],
    'script': ['source_file'],
    'document': ['source_file'],
}

# (kind, path) pairs that exist: on_copy_object2 handles only Gerber, Excellon and Geometry
COMBOS = [(k, 'command') for k in SOURCES] + [(k, 'object2') for k in ('gerber', 'excellon', 'geometry')]


# ---------------------------------------------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('kind,path', [pytest.param(k, p, id='%s-%s' % (k, p)) for k, p in COMBOS])
def test_copy_equals_source_on_every_attribute(app, kind, path):
    source = SOURCES[kind](app)
    attrs = [a for a in ATTRS[kind] if (kind, path, a) != ('excellon', 'object2', 'source_file')]
    before = {a: norm(getattr(source, a)) for a in attrs}
    clone = run_path(app, source, kind, path)
    assert {a: norm(getattr(clone, a, None)) for a in attrs} == before
    assert {a: norm(getattr(source, a)) for a in attrs} == before   # the copy did not touch the source


@pytest.mark.parametrize('kind,path', [pytest.param(k, p, id='%s-%s' % (k, p)) for k, p in COMBOS
                                       if k in ('gerber', 'excellon', 'geometry')])
def test_copy_is_independent(app, kind, path):
    """Mutate the source deeply, the copy stays; mutate the copy deeply, the source stays."""
    attrs = [a for a in ATTRS[kind] if a not in ('source_file', 'units')]
    source = SOURCES[kind](app)
    clone = run_path(app, source, kind, path)
    snap_clone = {a: norm(getattr(clone, a, None)) for a in attrs}
    snap_source = {a: norm(getattr(source, a, None)) for a in attrs}

    def mutate(obj):
        obj.solid_geometry.append(Point(100, 100).buffer(3))
        for tool in obj.tools.values():
            if 'geometry' in tool:
                tool['geometry'].append({'solid': Point(7, 7).buffer(1)})
            if 'drills' in tool:
                tool['drills'].append(Point(99, 99))
            if 'solid_geometry' in tool and isinstance(tool['solid_geometry'], list):
                tool['solid_geometry'].append(Point(98, 98).buffer(1))
            if 'data' in tool and isinstance(tool['data'], dict):
                tool['data']['mutated'] = True
            tool['mutated'] = True
        if getattr(obj, 'follow_geometry', None) is not None:
            obj.follow_geometry.append(Point(100, 100))
        if getattr(obj, 'aperture_macros', None):
            obj.aperture_macros[next(iter(obj.aperture_macros))].raw = 'MUTATED'
            obj.aperture_macros['EXTRA'] = 'x'
        obj.solid_geometry[0] = Point(-50, -50).buffer(2)
        del obj.tools[max(obj.tools)]

    # a copy that lost an attribute (None / empty) is covered by the equality test; skip the missing parts here
    mutate(source)
    assert {a: norm(getattr(clone, a, None)) for a in attrs} == snap_clone
    mutate(clone)
    # the source only gets its own mutation: re-apply to a fresh source and compare
    reference = SOURCES[kind](app)
    mutate(reference)
    assert {a: norm(getattr(source, a, None)) for a in attrs} == {a: norm(getattr(reference, a, None)) for a in attrs}
    assert snap_source != {a: norm(getattr(source, a, None)) for a in attrs}   # the mutation was effective


@pytest.mark.parametrize('kind', ['gerber', 'excellon', 'geometry'])
def test_transform_of_copy_leaves_source_geometry(app, kind):
    attrs = [a for a in ATTRS[kind] if a not in ('source_file', 'units')]
    source = SOURCES[kind](app)
    clone = run_path(app, source, kind, 'command')
    snap_source = {a: norm(getattr(source, a, None)) for a in attrs}
    snap_clone = {a: norm(getattr(clone, a, None)) for a in attrs}
    clone.offset((5, 7))
    clone.scale(2, 3, point=(0, 0))
    clone.mirror('X', (1, 1))
    assert {a: norm(getattr(clone, a, None)) for a in attrs} != snap_clone   # the transform was effective
    assert {a: norm(getattr(source, a, None)) for a in attrs} == snap_source
    snap_clone = {a: norm(getattr(clone, a, None)) for a in attrs}
    source.offset((-3, 2))
    assert {a: norm(getattr(clone, a, None)) for a in attrs} == snap_clone


def test_gerber_without_apertures_fails_in_object2(app):
    source = gerber_source(app)
    source.tools = {}
    assert run_path_or_fail(app, source, 'gerber', 'object2') == 'fail'


def test_excellon_without_tools_fails_in_both_paths(app):
    for path in ('command', 'object2'):
        source = excellon_source(app)
        source.tools = {}
        assert run_path_or_fail(app, source, 'excellon', path) == 'fail'


def run_path_or_fail(app, source, kind, path):
    results = []

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        new_obj = make_new(kind, name, app)
        res ='fail' if initialize(new_obj, app) == 'fail' else new_obj
        results.append(res)
        return res

    app.app_obj.new_object.side_effect = new_object
    app.f_handlers = SimpleNamespace(export_excellon=Mock(return_value='REGENERATED\n'))
    app.collection.get_selected.return_value = [source]
    ops = AppObjectOps(app)
    ops.on_copy_command() if path == 'command' else ops.on_copy_object2('_c2')
    return results[0]


def test_excellon_same_diameter_data_is_kept_by_both_paths(app):
    for path in ('command', 'object2'):
        clone = run_path(app, excellon_source(app), 'excellon', path)
        assert clone.tools[1]['data']['tag'] == 'first' and clone.tools[2]['data']['tag'] == 'second'
        assert [clone.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 1.0]


def _merge_source(name, solid=None, follow=None, tool_id=10, options=None):
    solid = solid if solid is not None else box(0, 0, 1, 1)
    follow = follow if follow is not None else LineString([(0, 0), (1, 1)])
    return SimpleNamespace(
        obj_options=options if options is not None else {'name': name, 'plot': True},
        solid_geometry=[solid],
        follow_geometry=[follow],
        tools={tool_id: {'type': 'C', 'size': 0.2, 'geometry': [{'solid': solid, 'follow': follow}]}},
    )


def _merge_final():
    return SimpleNamespace(obj_options={'name': 'Combo'}, solid_geometry=None, follow_geometry=None, tools=None)


def _call_merge(grb_list, grb_final, log=None):
    return GerberObject.merge(grb_list=grb_list, grb_final=grb_final, log=log)


class TestMerge_0B1:

    def test_nested_list_input_merges_every_source(self):
        a = _merge_source('a', solid=box(0, 0, 1, 1))
        b = _merge_source('b', solid=box(5, 0, 6, 1), tool_id=11)
        final = _merge_final()
        try:
            _call_merge([[a, b]], final, log=Mock())
        except TypeError as e:
            # the recursion drops a required argument today
            raise AssertionError("merge() of a nested list raised TypeError: %s" % e)
        assert final.solid_geometry.bounds == (0.0, 0.0, 6.0, 1.0)
        assert set(final.tools) == {10, 11}


    def test_tools_are_deep_copied(self):
        src = _merge_source('a')
        final = _merge_final()
        _call_merge([src], final, log=Mock())
        assert 10 in final.tools
        src.tools[10]['size'] = 9.9
        src.tools[10]['geometry'].append({'solid': box(9, 9, 10, 10)})
        assert final.tools[10]['size'] == 0.2
        assert len(final.tools[10]['geometry']) == 1


# ---------------------------------------------------------------------------------------------------------------
# the plot of a copy takes the triangulation of the source
# ---------------------------------------------------------------------------------------------------------------

PLOT_OPTS = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
PLOT_COMBOS = [pytest.param(k, p, id='%s-%s' % (k, p)) for k, p in COMBOS if k in ('gerber', 'excellon', 'geometry')]


@pytest.fixture
def canvas():
    # the merge of the buffers into the GL visuals needs a canvas; a fake one is enough
    from vispy.gloo.context import FakeCanvas
    return FakeCanvas()


def plot_into(obj, kind, collection, color='#112233FF', face_color='#44556677'):
    from appGUI.VisPyVisuals import ShapeGroup
    obj.shapes = ShapeGroup(collection)
    obj.outline_color, obj.fill_color = color, face_color
    obj.obj_options['solid'] = True       # the outline mode of the Excellon plots shape by shape: not reused
    if kind == 'gerber':
        obj.plot(color=color, face_color=face_color)
    else:
        obj.plot()


def shared_plot_app(app):
    """Every object of the app gets its shape group in ONE collection, as on the canvas."""
    from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
    collection = ShapeCollectionVisual(linewidth=1, layers=3, pool=None, fcoptions=PLOT_OPTS)
    app.plotcanvas.new_shape_group.side_effect = lambda *a, **k: ShapeGroup(collection)
    return collection


def plotted(obj):
    """The stored buffers of the shapes of an object, in plot order."""
    data = obj.shapes._collection.data
    return [{k: (np.array(v) if isinstance(v, np.ndarray) else v) for k, v in data[i].items()}
            for i in obj.shapes._indexes]


def assert_same_plot(got, want):
    assert len(got) == len(want) > 0
    for g, w in zip(got, want):
        assert g.keys() == w.keys()
        for k in g:
            if isinstance(w[k], np.ndarray):
                np.testing.assert_array_equal(g[k], w[k], err_msg=k)
            else:
                assert g[k] == w[k], k


COPY_COLORS = {'color': '#AA0000FF', 'face_color': '#00BB0099'}


def copy_plot(app, source, kind, path):
    """Copy through the real handler, then plot the copy; returns the copy and the number of triangulations."""
    from unittest.mock import patch
    import appGUI.VisPyVisuals as vv
    clone = run_path(app, source, kind, path)
    with patch.object(vv, '_update_shape_buffers', wraps=vv._update_shape_buffers) as built:
        plot_into(clone, kind, source.shapes._collection, **COPY_COLORS)
    return clone, built.call_count


@pytest.mark.parametrize('kind,path', PLOT_COMBOS)
def test_copy_plot_reuses_the_triangulation_of_the_source(app, canvas, kind, path):
    source = SOURCES[kind](app)
    plot_into(source, kind, shared_plot_app(app))
    source_plot = plotted(source)

    clone, triangulated = copy_plot(app, source, kind, path)
    assert triangulated == 0

    # the same buffers as a normal full plot of the copy (nothing to reuse any more), with the colors of the copy
    got = plotted(clone)
    plot_into(clone, kind, shared_plot_app(app), **COPY_COLORS)
    assert_same_plot(got, plotted(clone))
    assert_same_plot(plotted(source), source_plot)


def test_copy_plot_is_independent_of_the_source(app, canvas):
    from appObjects.AppObjectTemplate import plot_transformed_many
    collection = shared_plot_app(app)
    source = SOURCES['gerber'](app)
    plot_into(source, 'gerber', collection)
    clone, triangulated = copy_plot(app, source, 'gerber', 'command')
    assert triangulated == 0
    source_before, clone_before = plotted(source), plotted(clone)

    # move the source: the copy keeps its place
    source.offset((5.0, -3.0))
    plot_transformed_many([source], (1.0, 0.0, 0.0, 1.0, 5.0, -3.0))
    assert_same_plot(plotted(clone), clone_before)
    assert not np.array_equal(plotted(source)[0]['line_pts'], source_before[0]['line_pts'])

    # move the copy: the source keeps its place
    moved = plotted(source)
    clone.offset((1.0, 1.0))
    plot_transformed_many([clone], (1.0, 0.0, 0.0, 1.0, 1.0, 1.0))
    assert_same_plot(plotted(source), moved)

    # the source is deleted: the copy still has its shapes
    after_move = plotted(clone)
    source.shapes.clear(update=True)
    assert_same_plot(plotted(clone), after_move)
    assert all(i in collection.data for i in clone.shapes._indexes)


@pytest.mark.parametrize('kind', ['gerber', 'excellon', 'geometry'])
def test_copy_plot_falls_back_to_a_full_plot(app, canvas, kind):
    from appObjects.AppObjectTemplate import plot_transformed_many
    collection = shared_plot_app(app)

    def full_plot_of_moved_copy(clone):
        reference = SOURCES[kind](app)
        reference.offset((4.0, 2.0))
        plot_into(reference, kind, shared_plot_app(app), **COPY_COLORS)
        assert_same_plot(plotted(clone), plotted(reference))

    # the source was never plotted
    source = SOURCES[kind](app)
    source.offset((4.0, 2.0))
    clone, triangulated = copy_plot(app, source, kind, 'command')
    assert triangulated > 0
    full_plot_of_moved_copy(clone)

    # the geometry of the source changed after its plot, without a new plot: the copy of the new geometry
    # must not get the old buffers
    source = SOURCES[kind](app)
    plot_into(source, kind, collection)
    source.offset((4.0, 2.0))
    clone, triangulated = copy_plot(app, source, kind, 'command')
    assert triangulated > 0
    full_plot_of_moved_copy(clone)

    # the plot of the source was mapped by the move: no plot to trust for the copy
    source = SOURCES[kind](app)
    plot_into(source, kind, collection)
    source.offset((4.0, 2.0))
    plot_transformed_many([source], (1.0, 0.0, 0.0, 1.0, 4.0, 2.0))
    clone, triangulated = copy_plot(app, source, kind, 'command')
    assert triangulated > 0
    full_plot_of_moved_copy(clone)


# ---------------------------------------------------------------------------------------------------------------
# the plot of a panel (copies of the source moved by offsets) takes the triangulation of the source
# ---------------------------------------------------------------------------------------------------------------

PANEL_OFFSETS = [(0.0, 0.0), (30.0, 0.0), (0.0, 25.5), (30.0, 25.5)]


def panel_of(app, source, offsets, change=None):
    """A Gerber whose shapes are the plotted shapes of the source, copy-major, one copy per offset."""
    from shapely import affinity
    base = [g for g in shapely_parts(source.solid_geometry)]
    shapes = [affinity.translate(g, dx, dy) for dx, dy in offsets for g in base]
    if change:
        shapes = change(shapes)
    panel = gerber_source(app)
    panel.solid_geometry = shapes
    panel.reuse_plot_of(source, offsets=offsets)
    return panel


def shapely_parts(geometry):
    import shapely
    geoms = np.empty(1, dtype=object)
    geoms[0] = shapely.GeometryCollection(list(geometry) if isinstance(geometry, (list, tuple)) else [geometry])
    return list(shapely.get_parts(shapely.get_parts(geoms)))


def assert_close_plot(got, want, atol=2e-5):
    # a fresh triangulation of moved polygons may pick other (equally valid) triangles: only their count is compared
    assert len(got) == len(want) > 0
    for g, w in zip(got, want):
        for k in w:
            if k == 'mesh_tris' and atol:
                assert np.shape(g[k]) == np.shape(w[k])
            elif isinstance(w[k], np.ndarray) and w[k].dtype.kind == 'f':
                np.testing.assert_allclose(g[k], w[k], atol=atol, rtol=0, err_msg=k)
            elif isinstance(w[k], np.ndarray):
                np.testing.assert_array_equal(g[k], w[k], err_msg=k)
            else:
                assert g[k] == w[k], k


def panel_plot(app, source, offsets, change=None):
    """Plot the panel with reuse; returns the panel, the number of triangulations and a fresh plot of the same shapes."""
    from unittest.mock import patch
    import appGUI.VisPyVisuals as vv
    panel = panel_of(app, source, offsets, change)
    with patch.object(vv, '_update_shape_buffers', wraps=vv._update_shape_buffers) as built:
        plot_into(panel, 'gerber', source.shapes._collection, **COPY_COLORS)
    got = plotted(panel)
    panel.shapes.clear(update=False)
    plot_into(panel, 'gerber', shared_plot_app(app), **COPY_COLORS)
    return panel, built.call_count, got, plotted(panel)


def test_panel_plot_reuses_the_triangulation_of_the_source(app, canvas):
    source = gerber_source(app)
    plot_into(source, 'gerber', shared_plot_app(app))
    panel, triangulated, got, fresh = panel_plot(app, source, PANEL_OFFSETS)
    assert triangulated == 0
    assert len(got) == len(plotted(source)) * len(PANEL_OFFSETS)
    assert_close_plot(got, fresh)


def test_a_new_plot_with_other_colors_keeps_the_triangulation(app, canvas):
    # Multi-Color (or a color change) plots the same geometry again: no new triangulation, only new colors
    from unittest.mock import patch
    import appGUI.VisPyVisuals as vv
    source = gerber_source(app)
    plot_into(source, 'gerber', shared_plot_app(app))
    first = plotted(source)
    with patch.object(vv, '_update_shape_buffers', wraps=vv._update_shape_buffers) as built:
        source.plot(**COPY_COLORS)
    again = plotted(source)
    assert built.call_count == 0
    assert len(again) == len(first) > 0
    for new, old in zip(again, first):
        np.testing.assert_array_equal(new['mesh_vertices'], old['mesh_vertices'])
        np.testing.assert_array_equal(new['mesh_tris'], old['mesh_tris'])
        np.testing.assert_array_equal(new['line_pts'], old['line_pts'])
        assert not np.array_equal(new['mesh_rgba'], old['mesh_rgba'])

    # changed geometry: a normal plot, with the buffers of the new geometry
    from shapely import affinity
    source.solid_geometry = [affinity.translate(g, 50.0, 0.0) for g in shapely_parts(source.solid_geometry)]
    with patch.object(vv, '_update_shape_buffers', wraps=vv._update_shape_buffers) as built:
        source.plot(color='#112233FF', face_color='#44556677')
    assert built.call_count > 0
    assert plotted(source)[0]['mesh_vertices'][:, 0].min() > first[0]['mesh_vertices'][:, 0].min() + 40


def test_panel_plot_does_not_take_the_buffers_of_a_later_plot_of_the_source(app, canvas):
    # the source is plotted again, at another place, after the panel checked its shapes and before it takes the
    # buffers: the panel must show its own geometry, not the moved source
    from shapely import affinity
    source = gerber_source(app)
    plot_into(source, 'gerber', shared_plot_app(app))
    group = source.shapes
    snapshot = group.snapshot

    def plot_again_then_snapshot():
        group.snapshot = snapshot
        source.solid_geometry = [affinity.translate(g, 100.0, 0.0) for g in shapely_parts(source.solid_geometry)]
        group.clear(update=False)
        source.plot(color='#112233FF', face_color='#44556677')
        return snapshot()

    group.snapshot = plot_again_then_snapshot
    panel, triangulated, got, fresh = panel_plot(app, source, PANEL_OFFSETS)
    assert group.snapshot is snapshot           # the late plot of the source did run
    assert_close_plot(got, fresh)


def test_panel_plot_falls_back_when_the_shapes_do_not_match(app, canvas):
    from shapely import affinity

    def one_shape_changed(shapes):
        shapes[3] = affinity.scale(shapes[3], 1.5, 1.5)
        return shapes

    source = gerber_source(app)
    plot_into(source, 'gerber', shared_plot_app(app))
    for change in (one_shape_changed, lambda shapes: shapes[:-1]):
        panel, triangulated, got, fresh = panel_plot(app, source, PANEL_OFFSETS, change)
        assert triangulated > 0
        assert_close_plot(got, fresh, atol=0)
