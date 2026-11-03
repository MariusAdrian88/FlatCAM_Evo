"""Isolation: clear the copper left between the passes ('Clear leftovers', Tcl isolate -clear_leftovers).

Where the passes around 2 features meet, the band between the last passes can be too narrow for one more pass and
wider than what the last pass cuts: strips and triangles of copper stay, however many passes are made.
camlib.isolation_leftover_paths() finds them from 2 consecutive passes; GerberObject.isolate() and the Isolation Plugin
add 1 path along each of them when the option is on. With the option off (the default) nothing changes.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from shapely import LinearRing, affinity
from shapely.geometry import MultiPolygon, Point, box
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry, isolation_leftover_paths
from defaults import AppDefaults
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appPlugins.ToolIsolation import ToolIsolation

from test_plugin_geometry_bases import make_tool_app
from tests.helpers_objects import app, _headless_qt  # noqa: F401,E402

DIA = 0.1
OVERLAP = 10  # percent
R = DIA / 2


def offset_of(k):
    # the pass offsets of the isolation: 0.05, 0.14, 0.23, ...
    return DIA * ((2 * k + 1) / 2.0) - k * (OVERLAP / 100.0) * DIA


def two_strips(gap):
    return [box(-5, 0, 0, 10), box(gap, 0, gap + 5, 10)]


def passes_of(copper, count, scale=1.0):
    """The isolation geometry of each pass, as Geometry.isolation_geometry() makes it (flat list of polygons)."""
    return [flatten_shapely_geometry(unary_union([c.buffer(offset_of(k) * scale, 16) for c in copper]))
            for k in range(count)]


def leftovers_of(copper, count=6, scale=1.0):
    passes = passes_of(copper, count, scale)
    return passes, [ring for k in range(1, count)
                    for ring in isolation_leftover_paths(passes[k - 1], passes[k], R * scale, 16)]


def tool_paths(passes):
    """What the tool follows: the rings of the pass polygons."""
    return [ring for geo in passes for p in geo for ring in [p.exterior, *p.interiors]]


def rings(geometries):
    """The paths of a Geometry object: the rings of its polygons and its LinearRings."""
    return [r for g in geometries for r in ([g.exterior, *g.interiors] if g.geom_type == 'Polygon' else [g])]


def unburned(copper, paths, window):
    burned = unary_union([p.buffer(R, 16) for p in paths])
    return window.difference(unary_union(copper)).difference(burned)


def test_band_between_the_last_passes_is_found_and_cleared():
    # gap 0.42: passes at 0.05 and 0.14 from each strip, the next one (0.23) is past the middle (0.21); the passes cut
    # up to 0.19 from each strip and a 0.04 wide band is left in the middle
    copper = two_strips(0.42)
    passes, left = leftovers_of(copper)
    window = box(0, 1, 0.42, 9)

    assert unburned(copper, tool_paths(passes), window).area == pytest.approx(0.04 * 8, rel=0.05)
    assert left and all(isinstance(ring, LinearRing) for ring in left)
    for ring in left:
        # farther from the copper than the inner pass
        assert ring.distance(unary_union(copper)) > offset_of(1)
    assert unburned(copper, tool_paths(passes) + left, window).area < 1e-4


def test_no_leftover_where_the_passes_meet():
    # gap 0.38: the second passes cut up to 0.19 from each strip, exactly the middle. Beyond the ends of the strips,
    # between the passes around their corners, triangles are left.
    copper = two_strips(0.38)
    passes, left = leftovers_of(copper)
    window = box(0, 1, 0.38, 9)

    assert unburned(copper, tool_paths(passes), window).area < 1e-4
    assert not [ring for ring in left if ring.intersects(window)]
    assert [ring for ring in left if ring.bounds[3] <= 0 or ring.bounds[1] >= 10]


def island_in_a_ring(gap):
    # the same gap everywhere: the leftover is 1 annulus, (gap - 0.38) wide, with no triangles
    island = Point(0, 0).buffer(1, quad_segs=256)
    ring = Point(0, 0).buffer(3, quad_segs=256).difference(Point(0, 0).buffer(1 + gap, quad_segs=256))
    return [island, ring]


# ---------------------------------------------------------------------------------------------------------------
# GerberObject.isolate() (Tcl isolate)
# ---------------------------------------------------------------------------------------------------------------

def make_strips_gerber(app, name='g'):
    obj = GerberObject(name, app)
    obj.solid_geometry = MultiPolygon(two_strips(0.42))
    return obj


def run_isolate(app, gerber, dia=DIA, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=dia, overlap=OVERLAP, passes=4, plot=False, outname='iso', **kwargs)
    return created


def wkb_list(geometries):
    return [g.wkb_hex for g in geometries]


def test_isolate_adds_the_leftover_paths_after_the_passes(app):
    gerber = make_strips_gerber(app)
    off = run_isolate(app, gerber, combine=True, iso_type=2, clear_leftovers=False)['iso']
    on = run_isolate(app, gerber, combine=True, iso_type=2, clear_leftovers=True)['iso']

    extra = on.solid_geometry[len(off.solid_geometry):]
    assert wkb_list(on.solid_geometry[:len(off.solid_geometry)]) == wkb_list(off.solid_geometry)
    assert extra and all(isinstance(g, LinearRing) for g in extra)
    assert on.tools[1]['solid_geometry'] == on.solid_geometry

    copper = list(gerber.solid_geometry.geoms)
    window = box(0, 1, 0.42, 9)
    assert unburned(copper, rings(off.solid_geometry), window).area == pytest.approx(0.04 * 8, rel=0.05)
    assert unburned(copper, rings(on.solid_geometry), window).area < 1e-4


# ---------------------------------------------------------------------------------------------------------------
# Isolation Plugin, combined passes
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def tool(_headless_qt):  # noqa: F811
    return ToolIsolation(make_tool_app())


def tools_storage(passes):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_passes=passes, tools_iso_overlap=OVERLAP, tools_iso_isotype='full',
                tools_iso_milling_type='cl')
    return {1: {'tooldia': DIA, 'data': data, 'solid_geometry': []}}


def run_plugin(tool, app, gerber, **kwargs):
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    storage = tools_storage(4)
    storage[1]['data']['tools_iso_milling_type'] = kwargs.pop('milling_type', 'cl')
    tool.isolate(isolated_obj=gerber, sel_tools=[1], tools_storage=storage, plot=False,
                 combine=True, iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False,
                 sel_type=0, tip_shape='C1', iso_simplification=False, simplification_tol=0.001, **kwargs)
    (obj,) = created.values()
    return obj


def test_plugin_combined_passes_clear_the_leftovers(tool, app):
    gerber = make_strips_gerber(app)
    unchecked = run_plugin(tool, app, gerber)
    off = run_plugin(tool, app, gerber, clear_leftovers=False)
    on = run_plugin(tool, app, gerber, clear_leftovers=True)

    assert tool.ui.clear_leftovers_cb.get_value() is False
    assert wkb_list(unchecked.solid_geometry) == wkb_list(off.solid_geometry)
    assert not [g for g in off.solid_geometry if isinstance(g, LinearRing)]

    extra = [g for g in on.solid_geometry if isinstance(g, LinearRing)]
    assert extra
    assert len(on.solid_geometry) == len(off.solid_geometry) + len(extra)

    copper = list(gerber.solid_geometry.geoms)
    window = box(0, 1, 0.42, 9)
    assert unburned(copper, rings(off.solid_geometry), window).area == pytest.approx(0.04 * 8, rel=0.05)
    assert unburned(copper, rings(on.solid_geometry), window).area < 1e-4


@pytest.mark.parametrize('entry_point, copper', [('gerber', two_strips(0.42)), ('plugin', island_in_a_ring(0.39))],
                         ids=['gerber-strips', 'plugin-annulus'])
def test_leftover_paths_follow_the_milling_direction(tool, app, entry_point, copper):
    gerber = make_strips_gerber(app)
    gerber.solid_geometry = MultiPolygon(copper)
    paths = {}
    for milling_type in ('cl', 'cv'):
        if entry_point == 'gerber':
            obj = run_isolate(app, gerber, combine=True, iso_type=2, clear_leftovers=True,
                              milling_type=milling_type)['iso']
        else:
            obj = run_plugin(tool, app, gerber, clear_leftovers=True, milling_type=milling_type)
        paths[milling_type] = [g for g in obj.solid_geometry if isinstance(g, LinearRing)]

    assert paths['cl'] and len(paths['cl']) == len(paths['cv'])
    for climb, conventional in zip(paths['cl'], paths['cv']):
        assert climb.hausdorff_distance(conventional) < 1e-9
        assert climb.is_ccw != conventional.is_ccw

