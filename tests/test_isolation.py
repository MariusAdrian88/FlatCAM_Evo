"""Merged from: test_isolation_boundary: Isolation limited to a boundary (e.g. the board outline).; test_isolation_separate_passes: Isolation plugin: isolate() with the passes not combined and no specific geometry.; test_isolation_optimal_tooldia: Isolation: safe tool recommendations must agree with Check validity.; test_isolation_vbit_dia: Isolation plugin: V-shape tool diameter (on_update_tool_dia, on_tool_add, on_tool_default_add).; test_isolation_parallel_passes: Isolation passes buffered ahead in parallel threads (camlib.PassBuffers) give the same paths as one pass at a time.; test_isolation_leftovers: Isolation: clear the copper left between the passes ('Clear leftovers', Tcl isolate -clear_leftovers).; test_isolation_rest_index: Isolation rest machining uses bounded bulk queries for sparse passes and early exit for dense passes.; test_isolate_combine_passes: GerberObject.isolate(combine=True) must keep every pass.
"""

import math
import re
import pytest
import shapely
import functools
import os
import sys
import json
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch
from PyQt6 import QtWidgets, sip, QtCore, QtGui
from shapely.affinity import rotate
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from appObjects.GeometryObject import GeometryObject
from appPlugins.ToolIsolation import IsolationGen, ToolIsolation
from camlib import clip_paths_to_area, outline_area, PassBuffers, flatten_shapely_geometry, isolation_leftover_paths
from defaults import AppDefaults
from tclCommands.TclCommandIsolate import TclCommandIsolate
from test_plugin_geometry_bases import make_tool_app
from tests.helpers_objects import app, _headless_qt, make_gerber
from appTool import AppTool
from appMain import App
from appPlugins.ToolOptimal import ToolOptimal
from appCommon.Common import GracefulException
from shapely import affinity
from appObjects.GerberObject import GerberObject


# ==============================================================================================================
# test_isolation_boundary: Isolation limited to a boundary (e.g. the board outline).
# ==============================================================================================================

DIA = 0.5

OVERLAP = 0  # percent

PASSES = 100

NO_PATH_INSIDE = '[ERROR_NOTCL] %s: No isolation path is inside the boundary. Check that the boundary object is the ' \
                 'outline of this board.'

# The exact DipTrace 2.4.0.2 Gerber of the boundary report: 8 bodies, 1.04 mm minimum gap.
DIPTRACE_BOARD = '''G04 DipTrace 2.4.0.2*
%INBottom.gbr*%
%MOMM*%
%ADD13C,0.8*%
%ADD14C,1.27*%
%ADD15C,1.27*%
%ADD16R,1.5X1.5*%
%ADD17C,1.5*%
%FSLAX53Y53*%
G04*
G71*
G90*
G75*
G01*
%LNBottom*%
%LPD*%
X20779Y24462D2*
D13*
Y27002D1*
X18229D1*
X14429D1*
X18229D2*
X19509D1*
Y28272D1*
X32209Y14302D2*
X28399D1*
Y16842D1*
Y14302D2*
X18239D1*
X20779Y16842D2*
X23329D1*
Y28272D1*
X27129D1*
X20779Y19382D2*
X14429D1*
X20779Y21922D2*
X18239D1*
X28399D2*
X32209D1*
D14*
X27129Y28272D3*
D15*
X19509D3*
D14*
X14429Y19382D3*
D15*
Y27002D3*
D14*
X18239Y21922D3*
D15*
Y14302D3*
D14*
X32209Y21922D3*
D15*
Y14302D3*
D16*
X28399Y24462D3*
D17*
Y21922D3*
Y19382D3*
Y16842D3*
X20779D3*
Y19382D3*
Y21922D3*
Y24462D3*
M02*'''

def board_of(gerber, margin=1.0):
    minx, miny, maxx, maxy = unary_union(gerber.solid_geometry).bounds
    return box(minx - margin, miny - margin, maxx + margin, maxy + margin)

def far_box_of(gerber):
    # e.g. the outline of another board: not even the first pass reaches into it
    minx, miny, maxx, maxy = unary_union(gerber.solid_geometry).bounds
    return box(maxx + 50, maxy + 50, maxx + 90, maxy + 90)

def length_outside(paths, area):
    return unary_union(paths).difference(area.buffer(1e-9)).length

def test_edge_cuts_band_gives_the_board_area():
    # an outline drawn with a 0.1 mm line, as in an Edge Cuts Gerber: a band (a polygon with a hole)
    band = LinearRing([(0, 0), (40, 0), (40, 90), (0, 90)]).buffer(0.05)
    area = outline_area([band])
    assert area.area == pytest.approx(40.1 * 90.1, abs=0.5)
    assert area.contains(Point(20, 45))
    assert area.covers(band)

CHAMFERED_BOARD = [(0, 0), (50, 0), (50, 30), (40, 40), (0, 40), (0, 0)]

def test_the_segments_of_an_unbuffered_outline_give_the_board_area():
    # A Gerber loaded with Buffering = No keeps one polygon for each segment of the outline.
    segments = [LineString([a, b]).buffer(0.05) for a, b in zip(CHAMFERED_BOARD, CHAMFERED_BOARD[1:])]
    area = outline_area(segments)
    assert area.area == pytest.approx(Polygon(CHAMFERED_BOARD).buffer(0.05).area, abs=0.01)
    assert area.contains(Point(25, 20))

@pytest.mark.parametrize('kind', ['gerber-flashes', 'gerber-no-flashes', 'geometry'])
def test_boundary_hull_only_with_flashes_else_the_area_enclosed_by_the_outline(app, plugin, kind):
    # Pads inside the outline: the concave hull (the gap between the parts is inside). Without flashes (a normal
    # outline Gerber, a Geometry): the area enclosed by the outline, as before.
    parts = [box(0, 0, 1, 4), box(3, 0, 4, 1)]
    if kind == 'geometry':
        boundary_obj = GeometryObject('boundary', app)
        boundary_obj.solid_geometry = [[LineString(p.exterior.coords)] for p in parts]
    else:
        follow = Point(0.5, 2) if kind == 'gerber-flashes' else LineString(parts[0].exterior.coords)
        boundary_obj = make_gerber(app, name='boundary')
        boundary_obj.solid_geometry = parts
        boundary_obj.tools = {10: {'geometry': [{'solid': parts[0], 'follow': follow}]}}
    plugin.app.collection.get_by_name = Mock(return_value=boundary_obj)
    plugin.ui.boundary_obj_combo = SimpleNamespace(currentText=lambda: 'boundary')
    plugin.ui.boundary_cb.set_value(True)

    area = plugin.get_boundary_area()

    assert area.covers(unary_union(parts))
    assert area.contains(Point(2, 1)) == (kind == 'gerber-flashes')
    assert not area.covers(Point(2.5, 2.5))

@pytest.mark.parametrize('geometry', [[], [Point(0, 0)], [LineString([(0, 0), (1, 0)])]],
                         ids=['empty', 'point', 'collinear'])
def test_boundary_without_an_enclosing_area_is_rejected(geometry):
    assert outline_area(geometry) is None

def test_clip_keeps_the_paths_inside_and_cuts_the_crossing_ones():
    area = box(0, 0, 10, 10)
    inside = Point(5, 5).buffer(1)
    crossing = Point(10, 5).buffer(2)
    outside = Point(30, 30).buffer(1)
    paths = clip_paths_to_area([inside, crossing, outside], area)
    assert sum(1 for p in paths if p.equals(inside.exterior)) == 1
    assert length_outside(paths, area) == pytest.approx(0.0)
    assert unary_union(paths).length > inside.exterior.length

def run_isolate(app, gerber, **kwargs):
    """Calls isolate(); returns ({object name: Geometry object}, the pass numbers of the computed envelopes)."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    envelope = Mock(wraps=gerber.generate_envelope)
    gerber.generate_envelope = envelope
    gerber.isolate(dia=kwargs.pop('dia', DIA), overlap=kwargs.pop('overlap', OVERLAP),
                   iso_type=2, plot=False, **kwargs)
    return created, [c.kwargs['nr_passes'] for c in envelope.call_args_list]

def test_combined_passes_stop_at_the_boundary(app):
    gerber = make_gerber(app, name='g')
    board = board_of(gerber)
    created, computed = run_isolate(app, gerber, passes=PASSES, combine=True, outname='iso', boundary=board)

    # the last pass computed is the first one completely outside the board; it is not kept
    made = len(computed) - 1
    assert 1 < made < PASSES
    paths = created['iso'].solid_geometry
    assert paths and all(isinstance(p, (LineString, LinearRing)) for p in paths)
    assert length_outside(paths, board) == pytest.approx(0.0)
    assert created['iso'].tools[1]['solid_geometry'] == paths

def test_without_a_boundary_all_the_passes_are_made(app):
    gerber = make_gerber(app, name='g')
    created, computed = run_isolate(app, gerber, passes=3, combine=True, outname='iso')
    assert computed == [0, 1, 2]
    # the envelopes are kept as they are (polygons), not clipped to paths
    assert all(isinstance(g, Polygon) for g in created['iso'].solid_geometry)

@pytest.mark.parametrize('combine', [True], ids=['combined'])
def test_no_pass_inside_the_boundary_fails(app, combine):
    gerber = make_gerber(app, name='g')
    initialized = []

    def new_object(kind, name, initialize, plot=True, **kw):
        initialized.append(initialize(GeometryObject(name, app), app))
        return 'fail' if initialized[-1] == 'fail' else 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    # with trace_boundary an object holding only the outline must not be made either
    result = gerber.isolate(dia=DIA, passes=PASSES, overlap=OVERLAP, iso_type=2, plot=False, combine=combine,
                            outname='iso' if combine else None, boundary=far_box_of(gerber), trace_boundary=True)
    assert result == 'fail'

def run_tcl(app, gerber, edge, *argv):
    app.shell = Mock()
    objects = {'g': gerber, 'edge': edge}
    app.collection.get_by_name = Mock(side_effect=objects.get)
    cmd = TclCommandIsolate(app)
    args, unnamed = cmd.check_args(('g', '-dia', str(DIA), '-overlap', str(OVERLAP)) + argv)
    return cmd.execute(args, unnamed)

def test_tcl_boundary_is_the_area_enclosed_by_the_outline_object(app):
    gerber = make_gerber(app, name='g')
    board = board_of(gerber)
    # the outline as drawn in an Edge Cuts Gerber: a 0.1 mm band
    edge = SimpleNamespace(obj_options={'name': 'edge'}, solid_geometry=[board.exterior.buffer(0.05)])
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj

    app.app_obj.new_object = Mock(side_effect=new_object)
    run_tcl(app, gerber, edge, '-passes', str(PASSES), '-boundary', 'edge', '-trace_boundary', '1')

    area = outline_area(edge.solid_geometry)
    paths = created['g_iso'].solid_geometry
    assert length_outside(paths, area) == pytest.approx(0.0)
    assert sum(1 for g in paths if isinstance(g, LinearRing) and Polygon(g).equals(area)) == 1

def tcl_isolate(app, *argv, objects=None):
    """Runs the Tcl isolate command; returns {object name: Geometry object}. Fails if an object is plotted."""
    app.shell = Mock()
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        assert plot is False and kw.get('record_history', True)
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    objects = objects or {'g': make_gerber(app, name='g'), 'h': make_gerber(app, name='h')}
    app.collection.get_by_name = Mock(side_effect=objects.get)
    cmd = TclCommandIsolate(app)
    args, unnamed = cmd.check_args(argv)
    result = cmd.execute(args, unnamed)
    assert result in (None, 'ok')
    return created

def test_tcl_milling_direction_pad_passes_and_simplify_change_the_paths(app):
    def ccw(milling):
        iso = tcl_isolate(app, 'g', '-dia', '0.2', '-milling_type', milling, '-simplify', '0')['g_iso']
        return [p.exterior.is_ccw for p in iso.solid_geometry]

    assert ccw('cl') == [not flag for flag in ccw('cv')]

    plain = tcl_isolate(app, 'g', '-dia', '0.2')['g_iso']
    padded = tcl_isolate(app, 'g', '-dia', '0.2', '-pad_passes', '2')['g_iso']
    assert len(padded.solid_geometry) > len(plain.solid_geometry)

    def vertices(tolerance):
        iso = tcl_isolate(app, 'g', '-dia', '0.2', '-simplify', tolerance)['g_iso']
        return sum(len(p.exterior.coords) for p in iso.solid_geometry)

    assert vertices('0.3') < vertices('0')

def test_tcl_except_object_is_left_without_isolation(app):
    gerber = make_gerber(app, name='g')
    minx, miny, maxx, maxy = unary_union(gerber.solid_geometry).bounds
    half = box(minx - 5, miny - 5, (minx + maxx) / 2, maxy + 5)
    keep_out = SimpleNamespace(obj_options={'name': 'keep'}, solid_geometry=[half], kind='geometry')
    objects = {'g': gerber, 'keep': keep_out}
    iso = tcl_isolate(app, 'g', '-dia', '0.2', '-except_obj', 'keep', objects=objects)['g_iso']
    assert iso.solid_geometry
    assert unary_union([g.boundary if isinstance(g, Polygon) else g for g in iso.solid_geometry]).intersection(
        half.buffer(-1e-6)).length == pytest.approx(0.0)

@pytest.mark.parametrize('mode', ['rest'])
def test_tcl_traced_boundary_is_left_out_of_the_excluded_area(app, mode):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = [Point(4, 5).buffer(0.5)]
    board = box(0, 0, 10, 10)
    excluded = box(5, -1, 11, 11)
    objects = {
        'g': gerber,
        'board': SimpleNamespace(obj_options={'name': 'board'}, solid_geometry=[board], kind='geometry'),
        'keep': SimpleNamespace(obj_options={'name': 'keep'}, solid_geometry=[excluded], kind='geometry'),
    }
    options = ['-combine', '0'] if mode == 'separate' else ['-combine', '1']
    if mode == 'rest':
        options += ['-rest', '1']
    created = tcl_isolate(app, 'g', '-dia', '0.2', '-passes', '1', '-boundary', 'board',
                          '-trace_boundary', '1', '-except_obj', 'keep', *options, objects=objects)
    paths = unary_union([g.boundary if isinstance(g, Polygon) else g
                         for obj in created.values() for g in obj.solid_geometry])
    assert paths.intersection(excluded.buffer(-1e-6)).length == pytest.approx(0.0)
    # the trace along the boundary is still made, outside the excluded area
    assert paths.intersection(board.boundary).length > 0

@pytest.fixture
def plugin(_headless_qt):  # noqa: F811
    tool = ToolIsolation(make_tool_app())
    tool.decimals = 4
    yield tool
    # delete the widgets now, while this module's QApplication is alive
    sip.delete(tool)

@pytest.mark.parametrize('panning', [False, True])
def test_polygon_selection_right_click_finishes_unless_panning(plugin, panning):
    polygon = box(0, 0, 2, 2)
    plugin.app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    plugin.app.grid_status.return_value = False
    # The normal press handler stays connected; even a stationary press sets this flag.
    plugin.app.mouse_down = True
    plugin.app.event_is_dragging = True
    plugin.app.ui.popMenu.mouse_is_panning = panning
    plugin.poly_sel_disconnect_flag = True
    plugin.poly_dict[1] = polygon
    plugin._selected_tool_ids = lambda: [1]
    plugin.iso_tools = {1: {'tooldia': DIA, 'data': {}}}
    plugin.run_settings = lambda: {}
    submitted = []
    plugin.isolate_in_worker = lambda **kwargs: submitted.append(kwargs)

    plugin.on_poly_mouse_click_release(SimpleNamespace(pos=(1, 1), button=2))

    if panning:
        assert not submitted
        assert plugin.poly_sel_disconnect_flag
    else:
        assert len(submitted) == 1
        assert submitted[0]['geometry'][0].equals(polygon)
        assert submitted[0]['sel_tools'] == [1]
        assert not plugin.poly_sel_disconnect_flag

def run_plugin(plugin, gerber, combine, passes=PASSES, e_passes=0, boundary=None, trace_boundary=False,
               envelope=None, tooldia=DIA, **kwargs):
    """Calls isolate(); envelope: a stand-in for generate_envelope(), else the real one is used."""
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_isotype='full', tools_iso_passes=passes, tools_iso_overlap=kwargs.pop('overlap', OVERLAP),
                tools_iso_milling_type='cl')
    storage = {1: {'tooldia': tooldia, 'data': data, 'solid_geometry': []}}
    created = {}

    def new_object(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name})
        if initialize(obj, plugin.app) == 'fail':
            return 'fail'
        created[name] = obj

    plugin.app.app_obj.new_object.side_effect = new_object
    spy = Mock(side_effect=envelope) if envelope else Mock(wraps=plugin.generate_envelope)
    plugin.generate_envelope = spy
    # geometry: the whole object (with None the separate passes need the fix of the 'All' selection)
    options = dict(iso_except=False, e_passes=e_passes, area_except=False, area_shape='square', rest=False,
                   sel_type=0, tip_shape='C1', iso_simplification=False, simplification_tol=0.001,
                   boundary=boundary, trace_boundary=trace_boundary)
    options.update(kwargs)
    plugin.isolate(gerber, [1], storage, geometry=gerber.solid_geometry, plot=False, combine=combine, **options)
    return created, [c.kwargs['nr_passes'] for c in spy.call_args_list]

@pytest.mark.parametrize('mode, restriction', [('combined', 'selection'), ('rest', 'exclusion')])
def test_boundary_trace_respects_machining_scope(app, plugin, mode, restriction):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = [Point(4, 5).buffer(0.5)]
    board = box(0, 0, 10, 10)
    allowed = box(0, 0, 5, 10)
    options = dict(limited_area=[allowed])
    if restriction == 'exclusion':
        excluded = box(5, -1, 11, 11)
        plugin.app.collection.get_by_name = Mock(return_value=SimpleNamespace(solid_geometry=[excluded]))
        plugin.ui.except_cb.set_value(True)
        options = dict(iso_except=True)
    if mode == 'rest':
        plugin.ui.tools_table = SimpleNamespace(
            selectedItems=lambda: [SimpleNamespace(row=lambda: 0)],
            item=lambda row, col: SimpleNamespace(text=lambda: str(DIA)))
    created, __ = run_plugin(plugin, gerber, combine=mode != 'separate', passes=3,
                             boundary=board, trace_boundary=True, rest=mode == 'rest', **options)
    assert created
    for obj in created.values():
        assert length_outside(obj.solid_geometry, allowed) == pytest.approx(0.0)
        for tool in obj.tools.values():
            assert length_outside(tool['solid_geometry'], allowed) == pytest.approx(0.0)

def _polygon_parts(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, (MultiPolygon, shapely.GeometryCollection)):
        return [part for child in geom.geoms for part in _polygon_parts(child)]
    return []

def _sweep_of(paths, radius):
    return unary_union([path.buffer(radius, quad_segs=256, cap_style='round', join_style='round')
                        for path in paths])

def _assert_separated(boundary, bodies, paths, radius, tol=1e-10, isolation_area=None):
    copper = unary_union(bodies)
    centerline = unary_union(paths)
    assert boundary.covers(centerline)
    assert centerline.distance(copper) >= radius - tol
    assert centerline.distance(boundary.boundary) >= radius - tol
    sweep = _sweep_of(paths, radius)
    area_tol = tol * max(1.0, boundary.length)
    assert sweep.difference(boundary).area <= area_tol
    assert sweep.intersection(copper).area <= area_tol
    # A separately oriented inscribed disk includes a true tangency without enlarging the cutter.
    disks = [sweep]
    for path in paths:
        for xy in (path.coords[0], path.coords[-1]):
            end = Point(xy)
            contact = shapely.shortest_line(end, boundary.boundary)
            if abs(contact.length - radius) < tol:
                bx, by = contact.coords[-1]
                angle = math.atan2(by - end.y, bx - end.x)
                disks.append(rotate(end.buffer(radius, quad_segs=256), angle, origin=xy, use_radians=True))
    sweep = unary_union(disks)
    remaining = (boundary if isolation_area is None else isolation_area).difference(sweep.buffer(tol))
    for component in _polygon_parts(remaining):
        touched = [nr for nr, body in enumerate(bodies) if component.buffer(tol).intersects(body)]
        assert len(touched) <= 1, "the cutter leaves two copper bodies connected"

def _assert_complete_islands(area, bodies, paths, radius):
    reserve = 2 * math.sqrt(0.5) * 10 ** -4
    offset = (radius + reserve) / math.cos(math.pi / 64)
    working = area.buffer(offset).buffer(offset)
    world = box(*working.buffer(1).bounds)
    _assert_separated(working, bodies, paths, radius, isolation_area=world)
    islands = [part for part in _polygon_parts(world.difference(_sweep_of(paths, radius)))
               if part.intersects(unary_union(bodies))]
    assert len(islands) == len(bodies)
    assert all(not island.intersects(world.boundary) for island in islands)

def _two_bodies():
    return [box(0, 0, 10, 4), box(0, 6, 10, 10)]

@pytest.mark.parametrize('simplify_tol', [0, 10], ids=['unsimplified', 'simplified'])
def test_voronoi_curved_and_long_edges_are_separated(simplify_tol):
    bodies = [Point(0, 0).buffer(3), box(6, -5, 40, 5)]
    boundary = box(-5, -8, 45, 8)
    paths = IsolationGen.voronoi_isolation(bodies, boundary, 0.4, spacing=0.25, simplify_tol=simplify_tol)
    assert paths
    _assert_separated(boundary, bodies, paths, 0.2)

def test_voronoi_does_not_cut_the_holes_of_one_owner():
    body = Polygon([(0, 0), (20, 0), (20, 10), (0, 10)],
                   holes=[[(5, 3), (15, 3), (15, 7), (5, 7)]])
    other = box(0, 12, 20, 16)
    boundary = box(-2, -2, 22, 18)
    paths = IsolationGen.voronoi_isolation([body, other], boundary, DIA, spacing=0.1)
    hole = Polygon([(5, 3), (15, 3), (15, 7), (5, 7)])
    assert not unary_union(paths).intersection(hole.buffer(-1e-6))
    _assert_separated(boundary, [body, other], paths, DIA / 2.0)

def test_voronoi_island_in_a_copper_hole_is_separated():
    ring = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)],
                   holes=[[(6, 6), (14, 6), (14, 14), (6, 14)]])
    island = box(9, 9, 11, 11)
    boundary = box(-2, -2, 22, 22)
    paths = IsolationGen.voronoi_isolation([ring, island], boundary, DIA, spacing=0.1)
    assert paths
    _assert_separated(boundary, [ring, island], paths, DIA / 2.0)

@pytest.mark.parametrize('bodies', [
    [box(0, 0, 5, 5), box(5, 0, 10, 5)],
    [box(0, 0, 6, 5), box(4, 0, 10, 5)],
])
def test_voronoi_touching_or_overlapping_copper_is_one_body(bodies):
    with pytest.raises(IsolationGen.VoronoiIsolationError, match='one copper body'):
        IsolationGen.voronoi_isolation(bodies, box(-2, -2, 12, 12), DIA, spacing=0.1)

def test_voronoi_disconnected_board_regions_are_independent():
    boundary = MultiPolygon([box(0, 0, 10, 10), box(20, 0, 30, 10)])
    bodies = [box(1, 1, 4, 4), box(6, 6, 9, 9), box(21, 1, 24, 4), box(26, 6, 29, 9)]
    paths = IsolationGen.voronoi_isolation(bodies, boundary, DIA, spacing=0.1)
    assert paths
    _assert_separated(boundary, bodies, paths, DIA / 2.0)
    # no path is made across the empty space between the two board regions
    assert not unary_union(paths).intersection(box(10.1, -1, 19.9, 11))

def test_diptrace_boundary_encloses_source_copper(app, plugin):
    from appObjects.GerberObject import GerberObject

    gerber = GerberObject('Bottom', app)
    assert gerber.parse_lines(DIPTRACE_BOARD.splitlines()) is None
    assert gerber.units == 'MM'
    copper = unary_union(gerber.solid_geometry)
    bodies = list(copper.geoms)
    assert len(bodies) == 8
    assert min(a.distance(b) for i, a in enumerate(bodies) for b in bodies[i + 1:]) > 1.0

    plugin.app.collection.get_by_name = Mock(return_value=gerber)
    plugin.ui.boundary_obj_combo = SimpleNamespace(currentText=lambda: gerber.obj_options['name'])
    plugin.ui.boundary_cb.set_value(True)
    boundary = plugin.get_boundary_area()
    assert boundary.covers(copper)
    assert boundary.difference(copper).area > 0

@pytest.mark.parametrize('gap', [DIA - 0.001, DIA, DIA + 0.2], ids=['below-diameter', 'at-diameter', 'above'])
def test_voronoi_curved_gap_is_separated_by_the_cutter_sweeps(gap):
    bodies = [Point(0, 0).buffer(1), Point(2 + gap, 0).buffer(1)]
    boundary = box(-2, -2, 5, 2)
    paths = IsolationGen.voronoi_isolation(bodies, boundary, DIA, spacing=0.05)
    _assert_separated(boundary, bodies, paths, DIA / 2)

def _bottleneck_bodies():
    lower = Polygon([(0, 0), (10, 0), (10, 4), (5.2451, 4), (5.2451, 5), (4.7549, 5), (4.7549, 4), (0, 4)])
    return [lower, Polygon([(x, 10.4999 - y) for x, y in lower.exterior.coords])], box(-2, -2, 12, 12)

@pytest.mark.parametrize('case', ['empty-center', 'physical-board', 'saved-islands', 'short-bottleneck'])
def test_voronoi_gap_narrower_than_the_tool_fails_instead_of_reporting_success(case):
    area = box(-2, -2, 12, 12)
    bodies, boundary, tooldia, spacing, isolation_area = [box(0, 0, 10, 5), box(0, 5.2, 10, 10)], area, DIA, 0.1, None
    if case == 'empty-center':
        bodies, boundary, tooldia = [box(0, 0, 1, 1), box(1.04, 0, 2.04, 1)], box(-0.01, -0.01, 2.05, 1.01), 0.1
    elif case == 'saved-islands':
        boundary, isolation_area = area.buffer(DIA), area
    elif case == 'short-bottleneck':
        (bodies, boundary), spacing = _bottleneck_bodies(), 0.05
    with pytest.raises(IsolationGen.VoronoiIsolationError) as raised:
        IsolationGen.voronoi_isolation(bodies, boundary, tooldia, spacing, isolation_area=isolation_area)
    if case == 'empty-center':
        assert raised.value.diagnostics['reason'] == 'copper_clearance'

def test_voronoi_coarse_sampling_does_not_allow_copper_outside_board():
    bodies = [box(-2.001, 0, 4, 4), box(6, 6, 10, 10)]
    with pytest.raises(IsolationGen.VoronoiIsolationError, match='outside'):
        IsolationGen.voronoi_isolation(bodies, box(-2, -2, 12, 12), DIA, spacing=10)

def test_voronoi_respects_boundary_cutouts():
    boundary = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)],
                       holes=[[(12, 8), (18, 8), (18, 12), (12, 12)]])
    bodies = [box(2, 2, 10, 18), box(20, 2, 28, 18)]
    paths = IsolationGen.voronoi_isolation(bodies, boundary, DIA, spacing=0.1)
    assert paths
    _assert_separated(boundary, bodies, paths, DIA / 2.0)

def test_voronoi_invalid_inputs_are_reported():
    bodies = _two_bodies()
    boundary = box(-2, -2, 12, 12)
    for args in [(bodies, None, DIA, 0.1), (bodies, boundary, 0, 0.1), (bodies, boundary, DIA, 0.0),
                 (bodies, boundary, DIA, float('nan')), ([], boundary, DIA, 0.1)]:
        with pytest.raises(IsolationGen.VoronoiIsolationError):
            IsolationGen.voronoi_isolation(*args)

    # Repairing invalid topology must not silently discard copper with non-finite coordinates.
    for value in (float('nan'), float('inf')):
        invalid = Polygon([(5, 0), (6, 0), (value, 1), (5, 0)])
        with pytest.raises(IsolationGen.VoronoiIsolationError):
            IsolationGen.voronoi_isolation([box(0, 0, 1, 1), box(3, 0, 4, 1), invalid],
                                           box(-2, -2, 8, 4), 0.2, spacing=0.2)

def test_voronoi_local_refinement_resolves_a_coarse_curved_corridor():
    bodies = [rotate(box(-1, -0.4, 1, 0.4), 147.74),
              Point(2.07, 0.13).buffer(0.89, quad_segs=7),
              Point(-0.27, 3.16).buffer(0.6, quad_segs=4)]
    boundary = box(-4, -4, 7, 7)
    paths = IsolationGen.voronoi_isolation(bodies, boundary, 0.2, spacing=2)
    _assert_separated(boundary, bodies, paths, 0.1)

def test_voronoi_site_budget_coarsens_a_fine_spacing_within_the_limit_else_fails():
    boundary = box(-2, -2, 12, 12)
    report = {}
    paths = IsolationGen.voronoi_isolation(_two_bodies(), boundary, DIA, spacing=1e-5, max_sites=2000, report=report)
    assert 1e-5 < report['spacing'] <= DIA / 2
    _assert_separated(boundary, _two_bodies(), paths, DIA / 2)
    # a spacing that would have to be coarser than half the tool diameter is refused, even an extremely small one
    with pytest.raises(IsolationGen.VoronoiIsolationError, match='site budget'):
        IsolationGen.voronoi_isolation(_two_bodies(), boundary, DIA, spacing=1e-320, max_sites=50)

def test_voronoi_cancellation_is_reported():
    calls = {'n': 0}

    def abort():
        calls['n'] += 1
        return calls['n'] > 2

    with pytest.raises(IsolationGen.VoronoiAborted):
        IsolationGen.voronoi_isolation(_two_bodies(), box(-2, -2, 12, 12), DIA, spacing=0.05, abort=abort)

def _gcode_cut_paths(code):
    """The continuous modal G01 X-Y sequences with the tool below the surface, starting at the travel point."""
    paths = []
    current = []
    last_rapid = None
    cutting = False
    xy_pattern = r'X(-?\d+(?:\.\d+)?)\s+Y(-?\d+(?:\.\d+)?)'
    z_pattern = r'Z(-?\d+(?:\.\d+)?)'

    def close():
        if len(current) >= 2:
            paths.append(LineString(current))
        current.clear()

    for line in code.splitlines():
        if line.startswith('G00 ') or line.startswith('G01 '):
            depth = re.search(z_pattern, line)
            if depth:
                cutting = float(depth.group(1)) < 0
            point = re.search(xy_pattern, line)
            if line.startswith('G00 '):
                close()
                if point:
                    last_rapid = (float(point.group(1)), float(point.group(2)))
                continue
            if not point:
                continue
            if not cutting:
                close()
                last_rapid = (float(point.group(1)), float(point.group(2)))
                continue
            if not current and last_rapid is not None:
                current.append(last_rapid)
            current.append((float(point.group(1)), float(point.group(2))))
    close()
    return paths

@pytest.mark.parametrize('single', [False, True])
def test_voronoi_gcode_keeps_the_validated_clearance_after_output_rounding(single):
    from tests.test_cncjob_export_gcode import multi_geo_gcode, validated_data, gcode_job

    bodies = [Point(0, 0).buffer(3), box(6, -5, 40, 5)]
    boundary = box(-5, -8, 45, 8)
    radius = 0.2
    paths = IsolationGen.voronoi_isolation(bodies, boundary, 2 * radius, spacing=0.25, coord_decimals=4)

    # the real cutter diameter of this job, not the test fixture default
    data = validated_data(paths, diameter=2 * radius, units='MM')
    data.update(tools_mill_tooldia=2 * radius, tools_mill_offset_value=0.0)
    job = gcode_job('default')
    job.units = 'MM'
    if single:
        geo = SimpleNamespace(solid_geometry=paths, obj_options={'name': 'g'}, tools={1: {'data': data}})
        code, __ = job.generate_from_geometry_2(
            geo, tooldia=2 * radius, tolerance=0.005, z_cut=-0.05, z_move=2.0,
            feedrate=300, feedrate_z=100, feedrate_rapid=1000, pp_geometry_name='default')
    else:
        job, code = multi_geo_gcode(paths, data=data, job=job)
    emitted = _gcode_cut_paths(code)
    assert emitted
    # the rounded G-code paths still stay in the board, clear the copper and separate the bodies
    _assert_separated(boundary, bodies, emitted, radius)
    assert unary_union(emitted).length == pytest.approx(unary_union(paths).length, rel=1e-9)

def test_diptrace_source_copper_as_boundary_certifies_the_rounded_gcode(app, plugin):
    # Removing only internal dividers must fail: outside material is not an artificial hull-clipped island.
    from appObjects.GerberObject import GerberObject
    from tests.test_cncjob_export_gcode import gcode_job

    gerber = GerberObject('Bottom', app)
    assert gerber.parse_lines(DIPTRACE_BOARD.splitlines()) is None
    bodies = list(unary_union(gerber.solid_geometry).geoms)
    assert len(bodies) == 8
    radius = 0.05

    plugin.app.collection.get_by_name = Mock(return_value=gerber)
    plugin.ui.boundary_obj_combo = SimpleNamespace(currentText=lambda: gerber.obj_options['name'])
    plugin.ui.boundary_cb.set_value(True)
    boundary = plugin.get_boundary_area()

    plugin.units = 'MM'
    created, __ = run_isolate(app, gerber, dia=2 * radius, passes=1, boundary=boundary,
                              strategy='voronoi', voronoi_spacing=0.05, simplify=0.001,
                              pad_isolation=False, overlap=10)
    assert created, (app.inform.emit.call_args_list, plugin.app.inform.emit.call_args_list)
    obj = next(iter(created.values()))
    paths = obj.solid_geometry
    assert paths
    assert obj.tools[1]['data']['tools_iso_validated_paths'] is True
    assert all(round(value, 4) == value for path in paths for coord in path.coords for value in coord[:2])
    concave_only_gap = boundary.convex_hull.difference(boundary.buffer(2 * radius))
    assert concave_only_gap.area > 0
    boundary = boundary.convex_hull
    world = box(*boundary.buffer(2).bounds)
    sweep = _sweep_of(paths, radius)
    enclosed = unary_union([part for part in _polygon_parts(world.difference(sweep))
                            if not part.intersects(world.boundary)])
    assert concave_only_gap.difference(sweep).difference(enclosed).area < 1e-8
    _assert_complete_islands(boundary, bodies, paths, radius)
    assert sum(path.length for path in paths) == pytest.approx(unary_union(paths).length, abs=1e-8)

    job = gcode_job('default')
    job.units = 'MM'
    result = job.geometry_tool_gcode_gen(1, obj.tools, (0, 0), (0, 0), 0.005, is_first=True, is_last=True)
    assert result != 'fail', job.app.inform.emit.call_args_list
    emitted = _gcode_cut_paths(result[0])
    assert emitted
    _assert_complete_islands(boundary, bodies, emitted, radius)

def test_voronoi_rejects_grid_intrusion_instead_of_shrinking_the_vbit():
    diameter = 0.1 + 0.5 / 3 ** 0.5
    boundary = box(-2, -2, 12, 12)
    unrounded = IsolationGen.voronoi_isolation(_two_bodies(), boundary, diameter, spacing=0.1)
    _assert_separated(boundary, _two_bodies(), unrounded, diameter / 2)
    with pytest.raises(IsolationGen.VoronoiIsolationError, match='coordinate decimals'):
        IsolationGen.voronoi_isolation(_two_bodies(), boundary, diameter, spacing=0.1, coord_decimals=4)

def test_voronoi_plugin_needs_a_boundary(app, plugin):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    created, __ = run_plugin(plugin, gerber, combine=True, passes=1, boundary=None,
                             strategy='voronoi', voronoi_spacing=0.1)
    assert not created

@pytest.mark.parametrize('pad_isolation', [False, True])
def test_voronoi_plugin_failed_second_tool_leaves_no_partial_objects(app, plugin, pad_isolation):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    gerber.tools = _pad_source(gerber.solid_geometry).tools
    data = dict(tools_iso_passes=1, tools_iso_isotype='full', tools_mill_tool_shape=0)
    storage = {1: {'tooldia': DIA, 'data': deepcopy(data)},
               2: {'tooldia': 20, 'data': deepcopy(data)}}
    progress_active = []

    @contextmanager
    def progress(*args):
        progress_active.append(True)
        try:
            yield
        finally:
            progress_active.pop()

    plugin.app.proc_container.new = progress
    plugin.pending_validation = None
    plugin.isolate_in_worker(
        isolated_obj=gerber, sel_tools=[1, 2], tools_storage=storage, plot=False, combine=True,
        iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False, sel_type=0,
        tip_shape=0, iso_simplification=False, simplification_tol=0.001, boundary=box(-2, -2, 12, 12),
        trace_boundary=False, strategy='voronoi', voronoi_spacing=0.1, pad_isolation=pad_isolation)
    job = plugin.app.worker_task.emit.call_args.args[0]
    job['fcn'](*job['params'])
    assert not progress_active
    plugin.app.app_obj.new_object.assert_not_called()

@pytest.mark.parametrize('angle', [60, 180], ids=['effective-width', 'invalid-vbit'])
def test_voronoi_plugin_uses_validated_vbit_width(app, plugin, angle):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    data = dict(tools_iso_passes=1, tools_iso_isotype='full', tools_mill_tool_shape=5,
                tools_mill_vtipdia=0.1, tools_mill_vtipangle=angle, tools_mill_cutz=-0.25)
    storage = {1: {'tooldia': DIA, 'data': data}}
    created = []

    def new_object(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name})
        assert initialize(obj, plugin.app) != 'fail'
        created.append(obj)

    plugin.app.app_obj.new_object.side_effect = new_object
    diameter = 0.1 + 0.5 / 3 ** 0.5
    # The working collar must use the effective V-bit width, not the nominal tool diameter.
    boundary = box(-2 + diameter / 2, -2, 12 - diameter / 2, 12)
    result = plugin.isolate(
        gerber, [1], storage, plot=False, combine=True, iso_except=False, e_passes=0, area_except=False,
        area_shape='square', rest=False, sel_type=0, tip_shape=0, iso_simplification=False,
        simplification_tol=0.001, boundary=boundary, trace_boundary=False,
        strategy='voronoi', voronoi_spacing=0.1)
    if angle == 180:
        assert result == 'fail' and not created
    else:
        assert created[0].tools[1]['tooldia'] == pytest.approx(diameter)
        _assert_complete_islands(boundary, gerber.solid_geometry, created[0].solid_geometry, diameter / 2)

def test_voronoi_plugin_rejects_partial_copper_selection(app, plugin):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    created, __ = run_plugin(plugin, gerber, combine=True, passes=1, boundary=box(-2, -2, 12, 12),
                             strategy='voronoi', voronoi_spacing=0.1, sel_type=2)
    assert not created

def test_voronoi_cocircular_bodies_with_reversed_duplicate_vertices_are_separated():
    bodies = []
    for x, y in [(0, 0), (4, 0), (4, 4), (0, 4)]:
        coords = list(Point(x, y).buffer(0.5).exterior.coords)[::-1]
        bodies.append(Polygon([coord for coord in coords for _ in range(2)]))
    boundary = box(-2, -2, 6, 6)
    paths = IsolationGen.voronoi_isolation(bodies, boundary, DIA, spacing=0.1)
    _assert_separated(boundary, bodies, paths, DIA / 2)

def test_voronoi_generated_tool_spacing_converts_with_geometry_units(app):
    from tests.test_cncjob_export_gcode import validated_data, multi_geo_gcode, gcode_job

    obj = GeometryObject('iso', app)
    obj.units = 'MM'
    obj.ui_disconnect = Mock()
    obj.multigeo = True
    obj.solid_geometry = [LineString([(0, 0), (10, 0)])]
    data = validated_data(obj.solid_geometry, diameter=DIA, units='MM')
    data.update(tools_iso_strategy='voronoi', tools_iso_voronoi_spacing=0.05, tools_mill_tooldia=DIA)
    obj.tools = {1: {'tooldia': DIA, 'solid_geometry': list(obj.solid_geometry), 'data': data}}
    obj.convert_units('IN')
    assert obj.tools[1]['data']['tools_iso_voronoi_spacing'] == pytest.approx(0.05 / 25.4)
    job = gcode_job('default')
    job.units = 'IN'
    __, code = multi_geo_gcode(obj.tools[1]['solid_geometry'], data=obj.tools[1]['data'], job=job)
    assert code == 'fail'
    obj.convert_units('MM')
    assert obj.tools[1]['data']['tools_iso_voronoi_spacing'] == pytest.approx(0.05)

def test_each_tool_runs_with_its_own_strategy_spacing_and_name(app, plugin):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    base = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    base.update(tools_iso_passes=1, tools_iso_isotype='full', tools_iso_overlap=0, tools_iso_milling_type='cl')
    voronoi = dict(base, tools_iso_strategy='voronoi', tools_iso_voronoi_pads=False)
    # tools 1 and 2 have the same diameter in their names; tool 3 is an Offset tool of the same table
    storage = {1: {'tooldia': DIA, 'data': dict(voronoi, tools_iso_voronoi_spacing=0.1)},
               2: {'tooldia': DIA + 1e-7, 'data': dict(voronoi, tools_iso_voronoi_spacing=0.2)},
               3: {'tooldia': 0.3, 'data': dict(base, tools_iso_strategy='offset')}}
    created = {}

    def new_object(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name})
        if initialize(obj, plugin.app) == 'fail':
            return 'fail'
        created[name] = obj

    plugin.app.app_obj.new_object.side_effect = new_object
    plugin.isolate(gerber, [1, 2, 3], storage, geometry=gerber.solid_geometry, plot=False, combine=True,
                   iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False, sel_type=0,
                   tip_shape=0, iso_simplification=False, simplification_tol=0.001, boundary=box(-2, -2, 12, 12),
                   trace_boundary=False)

    by_strategy = {}
    for obj in created.values():
        data = next(iter(obj.tools.values()))['data']
        by_strategy.setdefault(data['tools_iso_strategy'], []).append((obj.obj_options['name'], data))
    assert len(by_strategy['offset']) == 1 and len(by_strategy['voronoi']) == 2
    assert len({name for name, __ in by_strategy['voronoi']}) == 2
    assert sorted(data['tools_iso_voronoi_spacing'] for __, data in by_strategy['voronoi']) == [0.1, 0.2]

def test_voronoi_paths_stay_certified_after_a_units_change_but_not_after_an_edit(app):
    from tests.test_cncjob_export_gcode import multi_geo_gcode, validated_data, gcode_job

    def converted(edit):
        obj = GeometryObject('iso', app)
        obj.units = 'MM'
        obj.ui_disconnect = Mock()
        obj.multigeo = True
        obj.solid_geometry = [LineString([(0, 0), (10, 0)])]
        data = validated_data(obj.solid_geometry, diameter=DIA, units='MM')
        data.update(tools_iso_strategy='voronoi', tools_iso_voronoi_spacing=0.05, tools_mill_tooldia=DIA)
        obj.tools = {1: {'tooldia': DIA, 'solid_geometry': list(obj.solid_geometry), 'data': data}}
        if edit:
            obj.tools[1]['solid_geometry'] = [LineString([(0, 0), (9, 0)])]
        obj.convert_units('IN')
        job = gcode_job('default')
        job.units = 'IN'
        job.coords_decimals = 6
        return obj, multi_geo_gcode(obj.tools[1]['solid_geometry'], data=obj.tools[1]['data'], job=job)[1]

    obj, code = converted(edit=False)
    assert obj.tools[1]['data']['tools_iso_voronoi_spacing'] == pytest.approx(0.05 / 25.4)
    assert obj.tools[1]['data']['tools_iso_voronoi_tooldia'] == pytest.approx(DIA / 25.4)
    assert code != 'fail'
    assert converted(edit=True)[1] == 'fail'

def test_legacy_isolation_tool_can_save_a_new_strategy_and_spacing(plugin):
    plugin.iso_tools = {1: {'tooldia': DIA, 'data': {'tools_iso_passes': 1}}}
    plugin.ui_disconnect()
    plugin.ui.tools_table.setRowCount(1)
    plugin.ui.tools_table.setItem(0, 0, QtWidgets.QTableWidgetItem('1'))
    plugin.ui.tools_table.setItem(0, 3, QtWidgets.QTableWidgetItem('1'))
    plugin.ui.tools_table.selectRow(0)
    plugin.ui_connect()
    plugin.ui.strategy_combo.set_value('voronoi')
    plugin.ui.voronoi_spacing_entry.set_value(0.2)
    plugin.ui.voronoi_spacing_entry.returnPressed.emit()
    plugin.ui.voronoi_pads_cb.set_value(True)
    saved = deepcopy(plugin.iso_tools[1]['data'])
    assert (saved['tools_iso_strategy'], saved['tools_iso_voronoi_pads']) == ('voronoi', True)
    plugin.ui.strategy_combo.set_value('offset')
    plugin.storage_to_form(saved)
    assert plugin.ui.strategy_combo.get_value() == 'voronoi'
    assert plugin.ui.voronoi_spacing_entry.get_value() == pytest.approx(0.2)
    assert plugin.ui.voronoi_pads_cb.get_value() is True

def test_tcl_voronoi_strategy_is_the_area_enclosed_by_the_outline_object(app):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    board = box(-2, -2, 12, 12)
    edge = SimpleNamespace(obj_options={'name': 'edge'}, solid_geometry=[board.exterior.buffer(0.05)])
    created = tcl_isolate(app, 'g', '-dia', str(DIA), '-passes', '1', '-boundary', 'edge',
                          '-strategy', 'voronoi', '-voronoi_spacing', '0.1',
                          objects={'g': gerber, 'edge': edge})
    assert created
    obj = next(iter(created.values()))
    assert obj.solid_geometry
    _assert_complete_islands(outline_area(edge.solid_geometry), gerber.solid_geometry, obj.solid_geometry, DIA / 2.0)

def _pad_source(pads, copper=None, extra=()):
    records = [{'follow': pad.representative_point(), 'solid': pad} for pad in pads]
    return SimpleNamespace(solid_geometry=unary_union(pads) if copper is None else copper,
                           tools={10: {'geometry': records + list(extra)}})

@pytest.mark.parametrize('pad', [Point(2, 2).buffer(0.8),
                                  Polygon([(1, 1), (3, 1), (3, 2), (2, 2), (2, 3), (1, 3)])],
                         ids=['round', 'macro-outline'])
def test_pad_isolation_partial_halo_preserves_connected_trace(pad):
    trace = box(2, 1.9, 6, 2.1)
    copper = unary_union([pad, trace])
    source = _pad_source([pad], copper)
    board = box(-2, -2, 8, 6)
    paths, report = IsolationGen.voronoi_pad_isolation([], source, board, DIA, coord_decimals=4)
    cut = unary_union(paths)
    assert report == {'candidates': 1, 'cut': 1, 'skipped': 0}
    assert cut.length > pad.length / 2
    assert cut.distance(copper) >= DIA / 2 - 1e-12
    assert board.covers(cut) and cut.distance(board.boundary) >= DIA / 2 - 1e-12
    assert not cut.intersects(LineString([(2, 2), (6, 2)]))
    assert cut.buffer(DIA / 2, quad_segs=128).intersection(copper).area < 1e-12
    assert all(x == round(x, 4) and y == round(y, 4) for p in paths for x, y in p.coords)

@pytest.mark.parametrize('case', ['pour', 'no-flashes', 'cleared-flash', 'negative-image'])
def test_pad_isolation_no_safe_pad_keeps_base_paths_with_explicit_summary(case):
    pad = box(1, 1, 2, 2)
    source = _pad_source([pad], box(0, 0, 5, 5))
    if case == 'no-flashes':
        source.tools[10]['geometry'] = [{'follow': pad.exterior, 'solid': pad}]
    elif case == 'cleared-flash':
        source.solid_geometry = source.solid_geometry.difference(pad)
        source.tools[10]['geometry'].append({'follow': Point(1.5, 1.5), 'clear': pad})
    elif case == 'negative-image':
        source.image_domain = box(0, 0, 5, 5)
    base = [LineString([(-1, -1), (6, -1)])]
    paths, report = IsolationGen.voronoi_pad_isolation(base, source, box(-2, -2, 7, 7), DIA,
                                                     coord_decimals=4)
    assert paths == base
    assert report['cut'] == 0
    assert report['skipped'] == report['candidates']
    assert report['candidates'] == (1 if case == 'pour' else 0)

def test_pad_isolation_overlapping_flashes_holes_and_board_edge_have_no_retrace():
    pad = Point(0, 0).buffer(1).difference(Point(0, 0).buffer(0.3))
    other = Point(1.5, 0).buffer(1)
    source = _pad_source([pad, pad, other])
    board = box(-1.1, -2, 4, 2)
    paths, report = IsolationGen.voronoi_pad_isolation([], source, board, DIA, coord_decimals=4)
    cut = unary_union(paths)
    assert report['candidates'] == 2 and report['cut'] == 2
    assert sum(p.length for p in paths) == pytest.approx(cut.length, abs=1e-10)
    assert cut.distance(source.solid_geometry) >= DIA / 2 - 1e-12
    assert board.covers(cut) and cut.distance(board.boundary) >= DIA / 2 - 1e-12
    assert not cut.intersects(Point(0, 0).buffer(0.3))

def test_pad_isolation_too_wide_for_pad_surroundings_is_reported():
    pad = box(-0.1, -0.1, 0.1, 0.1)
    frame = box(-3, -3, 3, 3).difference(box(-0.4, -0.4, 0.4, 0.4))
    source = _pad_source([pad], unary_union([pad, frame]))
    paths, report = IsolationGen.voronoi_pad_isolation([], source, box(-4, -4, 4, 4), DIA,
                                                     coord_decimals=4)
    assert not paths and report == {'candidates': 1, 'cut': 0, 'skipped': 1}

def test_pad_isolation_composed_clear_flash_preserves_surviving_pad_and_trace():
    pad = box(1, 1, 3, 3)
    clear = box(0, 0, 2, 4)
    trace = box(2.5, 1.9, 5, 2.1)
    copper = unary_union([pad, trace]).difference(clear)
    source = _pad_source([pad], copper, extra=[{'follow': Point(1, 2), 'clear': clear},
                                              {'follow': LineString([(2, 2), (5, 2)]), 'solid': trace}])
    paths, report = IsolationGen.voronoi_pad_isolation([], source, box(-2, -2, 7, 6), DIA,
                                                     coord_decimals=4)
    cut = unary_union(paths)
    assert report == {'candidates': 1, 'cut': 1, 'skipped': 0}
    assert cut.distance(copper) >= DIA / 2 - 1e-12
    # The halo follows only the surviving half, not the original cleared flash outline at x=1.
    assert cut.bounds[0] > 1.7
    assert not cut.intersects(LineString([(2.5, 2), (5, 2)]))

def test_pad_isolation_cancel_is_not_returned_as_a_partial_halo():
    source = _pad_source([box(1, 1, 3, 3)])
    with pytest.raises(IsolationGen.VoronoiAborted):
        IsolationGen.voronoi_pad_isolation([], source, box(-2, -2, 7, 6), DIA, coord_decimals=4,
                                           abort=lambda: True)

@pytest.mark.parametrize('entry', ['gerber', 'plugin', 'tcl'])
def test_voronoi_pad_option_reaches_final_certified_gcode(app, plugin, entry):
    from tests.test_cncjob_export_gcode import gcode_job

    gerber = make_gerber(app, name='g')
    bodies = _two_bodies()
    gerber.solid_geometry = bodies
    gerber.tools = _pad_source(bodies).tools
    board = box(-2, -2, 12, 12)
    __, working = IsolationGen.voronoi_working_bounds(board, DIA, 4)
    base = IsolationGen.voronoi_isolation(bodies, working, DIA, spacing=0.1, coord_decimals=4, isolation_area=board)
    if entry == 'plugin':
        plugin.units = 'MM'  # Normally initialized by set_tool_ui()/build_ui().
        created, __ = run_plugin(plugin, gerber, combine=True, passes=1, boundary=board,
                                 strategy='voronoi', voronoi_spacing=0.1, pad_isolation=True)
    elif entry == 'tcl':
        edge = SimpleNamespace(obj_options={'name': 'edge'}, solid_geometry=[board.exterior])
        created = tcl_isolate(app, 'g', '-dia', str(DIA), '-passes', '1', '-boundary', 'edge',
                              '-strategy', 'voronoi', '-voronoi_spacing', '0.1', '-pad_isolation', '1',
                              objects={'g': gerber, 'edge': edge})
    else:
        created, __ = run_isolate(app, gerber, passes=1, strategy='voronoi', voronoi_spacing=0.1,
                                  boundary=board, pad_isolation=True)
    obj = next(iter(created.values()))
    data = obj.tools[1]['data']
    assert data['tools_iso_voronoi_pads'] is True
    assert data['tools_iso_voronoi_pad_report'] == {'candidates': 2, 'cut': 2, 'skipped': 0}
    assert unary_union(obj.solid_geometry).length > unary_union(base).length + 20
    assert unary_union(base).difference(unary_union(obj.solid_geometry)).length < 1e-12
    assert sum(p.length for p in obj.solid_geometry) == pytest.approx(unary_union(obj.solid_geometry).length)
    job = gcode_job('default')
    job.units = 'MM'
    result = job.geometry_tool_gcode_gen(1, obj.tools, (0, 0), (0, 0), 0.005, is_first=True, is_last=True)
    assert result != 'fail', job.app.inform.emit.call_args_list
    emitted = _gcode_cut_paths(result[0])
    _assert_complete_islands(board, bodies, emitted, DIA / 2)
    assert unary_union(emitted).distance(unary_union(bodies)) >= DIA / 2 - 1e-12


# ==============================================================================================================
# test_isolation_separate_passes: Isolation plugin: isolate() with the passes not combined and no specific geometry.
# ==============================================================================================================

DIA_isolation_separate_passes = 0.2

def tools_storage(passes):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_passes=passes, tools_iso_overlap=10, tools_iso_isotype='full',
                tools_iso_milling_type='cl')
    return {1: {'tooldia': DIA_isolation_separate_passes, 'data': data, 'solid_geometry': []}}

@pytest.fixture(scope='module')
def tool(_headless_qt):  # noqa: F811
    return ToolIsolation(make_tool_app())

class Created(dict):
    """{object name: Geometry object built by the init callback}; .result is what isolate() returned."""
    result = None

def run_isolate_isolation_separate_passes(tool, app, gerber, passes, storage=None, sel_tools=(1,), **kwargs):
    """Calls the plugin isolate() (combine off unless given)."""
    created = Created()

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[name] = obj
        return 'ok'

    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    options = dict(combine=False, iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False,
                   sel_type=0, tip_shape='C1', iso_simplification=False, simplification_tol=0.001)
    options.update(kwargs)
    created.result = tool.isolate(isolated_obj=gerber, sel_tools=list(sel_tools), plot=False,
                                  tools_storage=storage if storage is not None else tools_storage(passes), **options)
    return created

def two_tools(passes=1):
    """Tool 1 (0.2) and tool 2 (0.5)."""
    storage = tools_storage(passes)
    storage[2] = deepcopy(storage[1])
    storage[2]['tooldia'] = 0.5
    return storage

def pads_gerber(app):
    """2 pads 20 mm apart (the second one is not selected in the tests) and a clear flash without 'solid'."""
    gerber = make_gerber(app, name='g')
    pad_a, pad_b = Point(0, 0).buffer(1), Point(20, 0).buffer(1)
    gerber.solid_geometry = [pad_a, pad_b]
    gerber.tools = {10: {'geometry': [{'solid': pad_a, 'follow': Point(0, 0)},
                                      {'solid': pad_b, 'follow': Point(20, 0)},
                                      {'follow': Point(5, 5)}]}}
    return gerber, pad_a, pad_b

def wkb_set(geometries):
    return sorted(g.wkb_hex for g in geometries if not g.is_empty)

@pytest.mark.parametrize('passes', [3])
def test_whole_object_makes_one_geometry_per_pass(tool, app, passes):
    created = run_isolate_isolation_separate_passes(tool, app, make_gerber(app, name='g'), passes)

    names = ['g_0.2000_iso'] if passes == 1 else ['g_0.2000_iso%d' % (i + 1) for i in range(passes)]
    assert sorted(created) == names
    for obj in created.values():
        assert len(obj.solid_geometry) > 0
        assert obj.tools[1]['tooldia'] == DIA_isolation_separate_passes
        assert obj.tools[1]['solid_geometry'] == obj.solid_geometry

def test_area_selection_with_separate_passes(tool, app):
    gerber = make_gerber(app, name='g')
    xmin, ymin, xmax, ymax = unary_union(gerber.solid_geometry).bounds
    area = box(xmin - 1, ymin - 1, (xmin + xmax) / 2, ymax + 1)

    created = run_isolate_isolation_separate_passes(tool, app, gerber, 2, limited_area=area)

    assert sorted(created) == ['g_0.2000_iso1', 'g_0.2000_iso2']
    for obj in created.values():
        assert len(obj.solid_geometry) > 0
        assert unary_union(obj.solid_geometry).within(area.buffer(1e-6))

def test_no_object_is_made_for_an_empty_selection_a_stop_or_an_empty_area(tool, app, monkeypatch):
    gerber = make_gerber(app, name='g')
    # an empty selection is not the whole board
    for geometry in ([], Polygon()):
        for mode in (dict(), dict(combine=True), dict(combine=True, rest=True)):
            created = run_isolate_isolation_separate_passes(tool, app, gerber, 1, geometry=geometry, **mode)
            assert created.result == 'fail'
            assert not created
    # no geometry inside the area
    for combine in (False, True):
        assert not run_isolate_isolation_separate_passes(tool, app, gerber, 1, combine=combine, limited_area=box(900, 900, 901, 901))
    # a stop by the user
    monkeypatch.setattr(tool.app, 'abort_flag', True)
    for combine in (False, True):
        created = run_isolate_isolation_separate_passes(tool, app, gerber, 2, combine=combine)
        assert created.result == 'fail'
        assert not created

def test_pad_passes_follow_only_the_isolated_pads_and_start_after_the_last_pass(tool, app):
    gerber, pad_a, pad_b = pads_gerber(app)

    for combine in (False, True):
        (obj,) = run_isolate_isolation_separate_passes(tool, app, gerber, 1, geometry=[pad_a], combine=combine, e_passes=2).values()
        # the pass and the 2 pad rings of the first pad, nothing around the second pad
        assert len(obj.solid_geometry) == 3
        assert unary_union(obj.solid_geometry).bounds[2] < 3

    created = run_isolate_isolation_separate_passes(tool, app, gerber, 3, geometry=[pad_a], e_passes=1)
    first, second, last = (created['g_0.2000_iso%d' % n] for n in (1, 2, 3))
    assert len(first.solid_geometry) == len(second.solid_geometry) == 1
    assert len(last.solid_geometry) == 2
    # pass offsets 0.1, 0.28, 0.46 and the pad ring after them 0.64
    assert unary_union(second.solid_geometry).bounds[2] == pytest.approx(1.28, abs=0.02)
    assert unary_union(last.solid_geometry).bounds[2] == pytest.approx(1.64, abs=0.02)

def test_rest_machining_uses_the_selected_tools_largest_first_and_survives_a_failed_geometry(tool, app, monkeypatch):
    gerber, __, __ = pads_gerber(app)

    # the smaller tool first, as in the Tool Table: the 0.5 tool still goes first and isolates both pads
    for geometry in (None, MultiPolygon([Point(0, 0).buffer(1), Point(20, 0).buffer(1)])):
        created = run_isolate_isolation_separate_passes(tool, app, gerber, 1, storage=two_tools(), sel_tools=[1, 2], geometry=geometry,
                              combine=True, rest=True, forced_rest=False, order=1)

        (obj,) = created.values()
        assert list(obj.tools) == [2]
        assert unary_union(obj.solid_geometry).bounds[2] == pytest.approx(21.25, abs=0.02)

    monkeypatch.setattr(tool, 'generate_rest_geometry', Mock(return_value=('fail', 'fail')))
    created = run_isolate_isolation_separate_passes(tool, app, gerber, 1, combine=True, rest=True, forced_rest=False, order=0)
    assert created.result == 'fail'
    assert not created

@pytest.mark.parametrize('multi, selection, expected', [
    (True, 3, ['a_0.2000_iso', 'b_0.2000_iso']),
    (False, 0, ['a_0.2000_iso']),
])
def test_selected_gerbers_are_isolated_in_one_run(tool, app, multi, selection, expected):
    gerber_a, gerber_b = make_gerber(app, name='a'), make_gerber(app, name='b')
    created = Created()

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[name] = obj
        return 'ok'

    objects = {'a': gerber_a, 'b': gerber_b}
    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    tool.app.worker_task.emit = lambda task: task['fcn'](*task['params'])
    tool.app.collection.get_by_name = lambda name: objects.get(name)
    tool.app.collection.get_selected = lambda: [gerber_a, gerber_b]
    tool.iso_tools = tools_storage(1)
    tool.build_ui()
    tool.ui.tools_table.selectAll()
    tool.ui.object_combo.currentText = lambda: 'a'
    tool.ui.valid_cb.set_value(False)
    tool.ui.combine_passes_cb.set_value(False)
    tool.ui.rest_cb.set_value(False)
    tool.ui.except_cb.set_value(False)
    tool.ui.simplify_cb.set_value(False)
    tool.ui.pad_passes_entry.set_value(0)
    tool.ui.select_combo.set_value(selection)
    tool.ui.multi_objects_cb.set_value(multi)
    if selection == 3:
        tool.ui.reference_combo.get_value = lambda: 'ref'
        objects['ref'] = make_gerber(app, name='ref')
        objects['ref'].solid_geometry = []

    tool.on_iso_button_click()

    assert sorted(created) == expected

def isolate_native(entries):
    """Isolates a native board region (a record with a 'solid' only) or a pad (with a 'follow' point)."""
    solid = entries[0]['solid']
    envelope = solid.buffer(0.1).exterior
    app = SimpleNamespace(options={'gerber_circle_steps': 64}, app_units='MM', inform=Mock(), app_obj=Mock(),
                          abort_flag=False)
    tool = SimpleNamespace(app=app, decimals=4, validation_status=True,
                           generate_envelope=Mock(return_value=[envelope]))
    tool.new_object = lambda *args, **kwargs: AppTool.new_object(tool, *args, **kwargs)
    source = SimpleNamespace(obj_options={'name': 'native'}, solid_geometry=[solid],
                             tools={0: {'type': 'REG', 'size': 0, 'geometry': entries}},
                             isolation_buffers=Mock())
    storage = {1: {'tooldia': 0.2, 'data': {
        'tools_iso_isotype': 'full', 'tools_iso_passes': 1,
        'tools_iso_overlap': 0, 'tools_iso_milling_type': 'cl'}}}
    created = []

    def create(kind, name, initialize, **kwargs):
        obj = SimpleNamespace(obj_options={'name': name})
        assert initialize(obj, app) != 'fail'
        created.append(obj)
        return obj

    app.app_obj.new_object.side_effect = create
    ToolIsolation.isolate(
        tool, source, [1], storage, geometry=source.solid_geometry, plot=False,
        combine=False, iso_except=False, e_passes=1, area_except=False,
        area_shape='square', rest=False, sel_type=0, tip_shape='C1',
        iso_simplification=False, simplification_tol=0.001, boundary=None)
    assert len(created) == 1
    assert created[0].multigeo
    assert created[0].tools[1]['tooldia'] == 0.2
    return unary_union(created[0].solid_geometry), envelope, source

def test_native_solid_only_region_keeps_isolation_without_fake_follow():
    actual, envelope, source = isolate_native([{'solid': box(0, 0, 2, 2)}])
    assert actual.equals(envelope)
    assert set(source.tools[0]['geometry'][0]) == {'solid'}

def test_native_point_flash_still_receives_extra_pad_geometry():
    pad = Point(0, 0).buffer(1)
    actual, envelope, source = isolate_native([{'solid': pad, 'follow': Point(0, 0)}])
    # the pad ring starts after the pass (offset of the 2nd pass); 64 steps of the whole circle
    extra = pad.buffer(0.2 * 3 / 2.0000001, 64 // 4)
    assert actual.symmetric_difference(unary_union([envelope, extra])).area < 1e-8
    assert actual.area > 0
    assert isinstance(source.tools[0]['geometry'][0]['follow'], Point)


# ==============================================================================================================
# test_isolation_optimal_tooldia: Isolation: safe tool recommendations must agree with Check validity.
# ==============================================================================================================

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEC = 4

UNIT = 10 ** -DEC

def gerber_tools(gap):
    # 3 pads; the smallest gap is between the first 2
    pads = [box(0, 0, 1, 1), box(1 + gap, 0, 2, 1), box(0, 1 + 3 * gap, 1, 2)]
    return {10: {'geometry': [{'solid': p} for p in pads]}}

@pytest.fixture(scope="session")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

@pytest.fixture
def tool_isolation_optimal_tooldia(qapp):
    app = make_tool_app()
    app.abort_flag = False
    app.dec_format = functools.partial(App.dec_format, app)
    # run the jobs in place of the worker thread and of the process pool
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    app.pool.apply_async.side_effect = lambda fcn, args: MagicMock(get=lambda: fcn(*args))
    t = ToolIsolation(app)
    t.decimals = DEC
    app.collection = MagicMock()
    return t

def find_optimal(t, gap):
    t.app.collection.get_by_name.return_value = MagicMock(tools=gerber_tools(gap))
    found = []
    t.optimal_found_sig.connect(found.append)
    t.on_find_optimal_tooldia()
    t.optimal_found_sig.disconnect(found.append)
    return found[0] if found else None

def messages(t):
    return [c.args[0] for c in t.app.inform.emit.call_args_list]

@pytest.mark.parametrize('gap, decimals, units', [(0.15996, 4, 'MM'), (0.0079945, 4, 'IN')])
def test_optimal_tool_passes_the_check_validity(tool_isolation_optimal_tooldia, gap, decimals, units):
    tool_isolation_optimal_tooldia.decimals = decimals
    tool_isolation_optimal_tooldia.app.decimals = decimals
    tool_isolation_optimal_tooldia.app.app_units = units
    dia = find_optimal(tool_isolation_optimal_tooldia, gap)
    assert dia is not None
    assert 0 < dia < gap
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    setattr(tool_isolation_optimal_tooldia, 'iso_tools', {1: {'tooldia': dia, 'data': data, 'solid_geometry': []}})
    tool_isolation_optimal_tooldia.build_ui()
    tool_isolation_optimal_tooldia.app.inform.emit.reset_mock()
    tool_isolation_optimal_tooldia.validation_status = True

    tool_isolation_optimal_tooldia.find_safe_tooldia_multiprocessing()

    assert tool_isolation_optimal_tooldia.validation_status is True
    assert 'Tool validation passed.' in messages(tool_isolation_optimal_tooldia)
    assert not any(m.startswith('[WARNING]') for m in messages(tool_isolation_optimal_tooldia))

def test_gap_below_the_resolution_gives_no_tool(tool_isolation_optimal_tooldia):
    assert find_optimal(tool_isolation_optimal_tooldia, 0.2) is not None
    tool_isolation_optimal_tooldia.app.inform.emit.reset_mock()
    assert find_optimal(tool_isolation_optimal_tooldia, 0.00004) is None
    assert tool_isolation_optimal_tooldia.safe_tooldia is None
    assert any(m.startswith('[ERROR_NOTCL]') for m in messages(tool_isolation_optimal_tooldia))

def test_validation_rejects_oversized_tool(tool_isolation_optimal_tooldia):
    dia = find_optimal(tool_isolation_optimal_tooldia, 0.15996)
    assert dia is not None
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    setattr(tool_isolation_optimal_tooldia, 'iso_tools', {1: {'tooldia': dia + UNIT, 'data': data, 'solid_geometry': []}})
    tool_isolation_optimal_tooldia.build_ui()
    tool_isolation_optimal_tooldia.app.inform.emit.reset_mock()
    tool_isolation_optimal_tooldia.find_safe_tooldia_multiprocessing()
    assert any(m.startswith('[WARNING]') for m in messages(tool_isolation_optimal_tooldia))
    assert 'Tool validation passed.' not in messages(tool_isolation_optimal_tooldia)

@pytest.mark.parametrize('gap', [0.15996, 0.00004])
def test_standalone_optimal_reports_only_positive_safe_diameters(qapp, gap):
    app = make_tool_app()
    app.abort_flag = False
    app.dec_format = functools.partial(App.dec_format, app)
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    t = ToolOptimal(app)
    t.ui.precision_spinner.set_value(DEC)
    app.collection = MagicMock()
    obj = app.collection.index.return_value.internalPointer.return_value.obj
    obj.kind = 'gerber'
    obj.tools = gerber_tools(0.2)
    t.find_minimum_distance()
    assert float(t.ui.result_entry.get_value()) > 0
    app.inform.emit.reset_mock()
    app.proc_container.new.return_value.done.reset_mock()
    obj.tools = gerber_tools(gap)
    t.find_minimum_distance()
    if gap > UNIT:
        assert 0 < float(t.ui.result_entry.get_value()) < gap
    else:
        assert t.ui.result_entry.get_value() == ''
        assert any(m.startswith('[ERROR_NOTCL]') for m in messages(t))
        assert not any(m.startswith('[success]') for m in messages(t))
    app.proc_container.new.return_value.done.assert_called_once()

def test_isolation_runs_after_the_check_even_if_tasks_are_reordered(qapp):
    # the check and the isolation are one worker task: the isolation always reads the result of the check
    from tests.helpers_objects import make_app, make_gerber
    gerber = make_gerber(make_app(), name='g')
    app = make_tool_app()
    app.dec_format = functools.partial(App.dec_format, app)
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    t = ToolIsolation(app)
    t.decimals = DEC
    app.collection = MagicMock()
    app.collection.get_by_name.return_value = gerber
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    t.iso_tools = {1: {'tooldia': 5.0, 'data': data, 'solid_geometry': []}}
    t.build_ui()
    t.ui.tools_table.selectAll()
    t.ui.valid_cb.set_value(True)

    t.on_iso_button_click()
    for task in reversed(tasks):
        task['fcn'](*task['params'])

    # a 5 mm tool does not fit between the copper features: the object is not selected
    assert any(m.startswith('[WARNING]') for m in messages(t))
    assert app.app_obj.new_object.call_args.kwargs['autoselected'] is False

def standalone_optimal(tools):
    # the Find Optimal plugin with the job run in place of the worker thread
    app = make_tool_app()
    app.dec_format = functools.partial(App.dec_format, app)
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    t = ToolOptimal(app)
    t.ui.precision_spinner.set_value(DEC)
    app.collection = MagicMock()
    obj = app.collection.index.return_value.internalPointer.return_value.obj
    obj.kind = 'gerber'
    obj.tools = tools
    return t

def click_line(text_box, move):
    with QtCore.QSignalBlocker(text_box):
        text_box.moveCursor(move)

def test_standalone_optimal_click_on_the_empty_last_line_of_the_distances(qapp):
    t = standalone_optimal(gerber_tools(0.2))
    t.find_minimum_distance()
    distances = t.ui.distances_textb
    assert distances.toPlainText().endswith('\n')
    click_line(distances, QtGui.QTextCursor.MoveOperation.Start)
    t.on_distances_textb_clicked()
    shown = t.ui.locations_sec_textb.toPlainText()
    assert shown.count('\n') == 1
    # the empty line after the last distance raised ValueError: the 'Critical Error' dialog
    click_line(distances, QtGui.QTextCursor.MoveOperation.End)
    t.on_distances_textb_clicked()
    assert t.ui.locations_sec_textb.toPlainText() == shown

def test_standalone_optimal_with_a_multi_part_solid(qapp):
    # a shape split by a negative Etch Compensation is 1 MultiPolygon solid: the search stopped without a message
    tools = gerber_tools(0.2)
    tools[10]['geometry'].append({'solid': MultiPolygon([box(0, 5, 1, 6), box(1.15, 5, 2, 6)])})
    t = standalone_optimal(tools)
    t.find_minimum_distance()
    assert (t.ui.result_entry.get_value(), t.ui.freq_entry.get_value()) == ('0.1499', '1')
    assert any(m.startswith('[success]') for m in messages(t))


# ==============================================================================================================
# test_isolation_vbit_dia: Isolation plugin: V-shape tool diameter (on_update_tool_dia, on_tool_add, on_tool_default_add).
# ==============================================================================================================

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

V = ToolIsolation.V_SHAPE_INDEX if hasattr(ToolIsolation, 'V_SHAPE_INDEX') else 5

def v_dia(tip, angle, cut_z):
    return round(tip + 2 * abs(cut_z) * math.tan(math.radians(angle / 2)), 4)

def v_data(tip=0.1, angle=60, cut_z=-0.1, shape=V):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_mill_tool_shape=shape, tools_mill_vtipdia=tip, tools_mill_vtipangle=angle,
                tools_mill_cutz=cut_z)
    return data

@pytest.fixture(scope="module")
def qapp_isolation_vbit_dia():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

@pytest.fixture
def tool_isolation_vbit_dia(qapp_isolation_vbit_dia, tmp_path):
    app = make_tool_app()
    app.dec_format = lambda value, decimals=4: round(float(value), decimals)
    db_path = tmp_path / 'tools.FlatDB'
    db_path.write_text('{}', encoding='utf-8')
    app.tools_database_path = lambda: str(db_path)
    t = ToolIsolation(app)
    t.db_path = db_path
    t.decimals = 4
    t.default_data = v_data()
    t.ui.v_frame.show()
    # the frame is only "visible" when its parents are shown
    t.ui.v_frame.isVisible = lambda: True
    yield t
    # Destroy the signal-connected widgets while their QApplication and Python owners are still alive.
    sip.delete(t)

def add_tool_row(t, uid, dia, data=None):
    t.iso_tools[uid] = {'tooldia': dia, 'data': deepcopy(data if data is not None else t.default_data),
                        'solid_geometry': []}

def select_uid(t, uid):
    t.build_ui()
    for row in range(t.ui.tools_table.rowCount()):
        if int(t.ui.tools_table.item(row, 3).text()) == uid:
            t.ui.tools_table.setCurrentCell(row, 1)
            return row
    raise AssertionError('uid not in table')

def test_update_changes_diameter(tool_isolation_vbit_dia):
    add_tool_row(tool_isolation_vbit_dia, 1, 0.5)
    row = select_uid(tool_isolation_vbit_dia, 1)
    tool_isolation_vbit_dia.ui.tipdia_entry.set_value(0.2)
    tool_isolation_vbit_dia.ui.tipangle_entry.set_value(60)
    tool_isolation_vbit_dia.ui.cutz_entry.set_value(-0.1)
    tool_isolation_vbit_dia.on_update_tool_dia()
    expected = v_dia(0.2, 60, -0.1)
    assert tool_isolation_vbit_dia.iso_tools[1]['tooldia'] == expected
    assert tool_isolation_vbit_dia.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert float(tool_isolation_vbit_dia.ui.tools_table.item(row, 1).text()) == expected

def test_update_clash_with_other_tool_warns_and_keeps(tool_isolation_vbit_dia):
    original = v_dia(0.1, 60, -0.1)
    clash = v_dia(0.2, 60, -0.1)
    add_tool_row(tool_isolation_vbit_dia, 1, original, v_data(tip=0.1))
    add_tool_row(tool_isolation_vbit_dia, 2, clash, v_data(tip=0.2))
    row = select_uid(tool_isolation_vbit_dia, 1)
    tool_isolation_vbit_dia.ui.tipdia_entry.set_value(0.2)
    tool_isolation_vbit_dia.ui.tipangle_entry.set_value(60)
    tool_isolation_vbit_dia.ui.cutz_entry.set_value(-0.1)
    tool_isolation_vbit_dia.app.inform.emit.reset_mock()
    tool_isolation_vbit_dia.ui.tipdia_entry.returnPressed.emit()
    assert tool_isolation_vbit_dia.app.inform.emit.call_count == 1
    assert 'WARNING' in tool_isolation_vbit_dia.app.inform.emit.call_args[0][0]
    assert tool_isolation_vbit_dia.iso_tools[1]['tooldia'] == original
    assert float(tool_isolation_vbit_dia.ui.tools_table.item(row, 1).text()) == original
    # the rejected V parameters are not kept: the tool data and its diameter still describe the same cutter
    assert tool_isolation_vbit_dia.iso_tools[1]['data']['tools_mill_vtipdia'] == 0.1
    assert tool_isolation_vbit_dia.app.dec_format(tool_isolation_vbit_dia.v_effective_diameter(tool_isolation_vbit_dia.iso_tools[1]['data']), tool_isolation_vbit_dia.decimals) == original
    # the V edit left the plugin free to pass the Optimal result to the UI
    tool_isolation_vbit_dia.optimal_found_sig.emit(0.25)
    assert tool_isolation_vbit_dia.ui.new_tooldia_entry.get_value() == pytest.approx(0.25)

def test_default_add_zero_dia_v_shape(tool_isolation_vbit_dia):
    tool_isolation_vbit_dia.on_tool_default_add(dia=0)
    assert list(tool_isolation_vbit_dia.iso_tools) == [1]
    expected = v_dia(0.1, 60, -0.1)
    assert tool_isolation_vbit_dia.iso_tools[1]['tooldia'] == expected
    assert tool_isolation_vbit_dia.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert float(tool_isolation_vbit_dia.ui.tools_table.item(0, 1).text()) == expected

def test_default_add_zero_dia_not_v_shape_warns(tool_isolation_vbit_dia):
    tool_isolation_vbit_dia.default_data = v_data(shape=0)
    tool_isolation_vbit_dia.app.inform.emit.reset_mock()
    tool_isolation_vbit_dia.on_tool_default_add(dia=0)
    assert tool_isolation_vbit_dia.iso_tools == {}
    assert 'WARNING' in tool_isolation_vbit_dia.app.inform.emit.call_args[0][0]

def test_add_zero_dia_final_diameter_from_matched_record(tool_isolation_vbit_dia):
    provisional = v_dia(0.1, 60, -0.1)
    record = {'name': 'v', 'tooldia': provisional,
              'data': v_data(tip=0.2, angle=90, cut_z=-0.3)}
    record['data'].update(tool_target=3, tol_min=0.0, tol_max=0.0)
    other = {'name': 'other', 'tooldia': 5.0, 'data': v_data()}
    other['data'].update(tool_target=3, tol_min=0.0, tol_max=0.0)
    tool_isolation_vbit_dia.db_path.write_text(json.dumps({'1': record, '2': other}), encoding='utf-8')

    tool_isolation_vbit_dia.on_tool_add(custom_dia=0)

    expected = v_dia(0.2, 90, -0.3)
    assert expected != provisional
    assert tool_isolation_vbit_dia.iso_tools[1]['tooldia'] == expected
    assert tool_isolation_vbit_dia.iso_tools[1]['data']['tools_iso_tooldia'] == expected
    assert tool_isolation_vbit_dia.iso_tools[1]['data']['tools_mill_vtipdia'] == 0.2
    assert float(tool_isolation_vbit_dia.ui.tools_table.item(0, 1).text()) == expected

def test_effective_diameter_rejects_impossible_tools():
    from appPlugins.ToolIsolation import IsolationGen
    for tip, angle, cut_z in [(0.1, 0, -0.1), (0.1, 180, -0.1), (0.1, 200, -0.1), (0.1, -30, -0.1),
                              (-0.1, 60, -0.1), (0.0, 60, 0.0)]:
        assert IsolationGen.v_effective_diameter(v_data(tip, angle, cut_z)) is None, (tip, angle, cut_z)
    assert IsolationGen.v_effective_diameter(v_data(0.1, 60, -0.1)) == pytest.approx(v_dia(0.1, 60, -0.1), abs=1e-4)

def table_dias(t):
    return [float(t.ui.tools_table.item(r, 1).text()) for r in range(t.ui.tools_table.rowCount())]

def selected_uids(t):
    return sorted({int(t.ui.tools_table.item(i.row(), 3).text()) for i in t.ui.tools_table.selectedItems()})

@pytest.fixture
def plain(tool_isolation_vbit_dia):
    tool_isolation_vbit_dia.default_data = v_data(shape=0)
    tool_isolation_vbit_dia.app.options = dict(AppDefaults.factory_defaults, global_app_level='a')
    tool_isolation_vbit_dia.ui.iso_order_combo.set_value(2)  # Reverse: the table is sorted by diameter
    return tool_isolation_vbit_dia

def test_added_tools_are_selected_under_a_sorted_table_and_duplicates_are_refused(plain):
    plain.on_tool_add(custom_dia=0.1)
    plain.on_tool_add(custom_dia=0.5)
    assert table_dias(plain) == [0.5, 0.1]
    uids = selected_uids(plain)
    assert len(uids) == 1 and plain.iso_tools[uids[0]]['tooldia'] == 0.5
    plain.on_tool_default_add(dia=0.3)
    uids = selected_uids(plain)
    assert len(uids) == 1 and plain.iso_tools[uids[0]]['tooldia'] == 0.3

    for add in (lambda: plain.on_tool_add(custom_dia=0.1), lambda: plain.on_tool_default_add(dia=0.1)):
        plain.app.inform.emit.reset_mock()
        add()
        assert len(plain.iso_tools) == 3
        assert 'Tool already in Tool Table' in plain.app.inform.emit.call_args[0][0]

    db_tool = {'tooldia': 0.4, 'data': {'tool_target': 3, 'tools_iso_passes': 4}}
    uid = plain.on_tool_from_db_inserted(db_tool)
    assert uid not in ('fail', True) and plain.iso_tools[uid]['tooldia'] == 0.4
    assert selected_uids(plain) == [uid]
    data = plain.iso_tools[uid]['data']
    assert data['tools_iso_passes'] == 4
    assert data['tools_iso_overlap'] == plain.default_data['tools_iso_overlap']
    assert data['tools_iso_tooldia'] == 0.4
    assert plain.on_tool_from_db_inserted(db_tool) == 'fail'
    assert len(plain.iso_tools) == 4


# ==============================================================================================================
# test_isolation_parallel_passes: Isolation passes buffered ahead in parallel threads (camlib.PassBuffers) give the same paths as one pass at a time.
# ==============================================================================================================

DIA_isolation_parallel_passes = 0.2

OVERLAP_isolation_parallel_passes = 10  # percent

OFFSETS = [0.1 * ((2 * nr + 1) / 2.0) - nr * 0.1 * 0.1 for nr in range(20)]

def features():
    # pads and traces close to each other, so the buffers of the passes merge
    geo = [Point(x * 1.3, y * 1.1).buffer(0.4, 16) for x in range(12) for y in range(9)]
    geo += [LineString([(0, y * 1.1), (14, y * 1.1 + 0.3)]).buffer(0.1, 16) for y in range(9)]
    geo.append(box(20, 0, 26, 3).difference(box(21, 1, 25, 2)))
    return geo

def one_pass(geo, offset, quad_segs=16):
    """The loop of Geometry.isolation_geometry() over the features."""
    return unary_union([g.buffer(offset, quad_segs, join_style='round') for g in geo])

def wkb_list(geometries):
    return [g.wkb for g in flatten_shapely_geometry(geometries)]

@pytest.fixture
def three_threads():
    """3 threads (batches of 3 passes); yields the mock that counts the passes taken from PassBuffers."""
    real_get = PassBuffers.get
    with patch('camlib.os.cpu_count', return_value=3), \
            patch.object(PassBuffers, 'get', autospec=True, side_effect=real_get) as get:
        yield get

@pytest.mark.parametrize('workers', [1, 3])
def test_same_geometry_as_one_pass_at_a_time(workers):
    geo = features()
    buffers = PassBuffers(geo, OFFSETS, 16, workers=workers)
    for nr_pass, offset in enumerate(OFFSETS):
        assert buffers.get(nr_pass).wkb == one_pass(geo, offset).wkb, 'pass %d' % nr_pass

def test_abort_is_raised_in_the_caller():
    buffers = PassBuffers(features(), OFFSETS, 16, workers=4, abort=lambda: True)
    with pytest.raises(GracefulException):
        buffers.get(0)

def run_isolate_isolation_parallel_passes(app, gerber, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=DIA_isolation_parallel_passes, overlap=OVERLAP_isolation_parallel_passes, iso_type=2, milling_type='cl', plot=False, **kwargs)
    return created

def make_tool(app):
    tool = SimpleNamespace(app=app, decimals=4, validation_status=True, temp_shapes=Mock())
    tool.generate_envelope = lambda *args, **kwargs: ToolIsolation.generate_envelope(tool, *args, **kwargs)
    return tool

def tools_storage_isolation_parallel_passes(passes, milling_type):
    return {1: {'tooldia': DIA_isolation_parallel_passes, 'data': {
        'tools_iso_isotype': 'full', 'tools_iso_passes': passes, 'tools_iso_overlap': OVERLAP_isolation_parallel_passes,
        'tools_iso_milling_type': milling_type, 'tools_mill_offset_type': 0, 'tools_mill_offset_value': 0.0,
        'tools_mill_tool_shape': 0}}}

def plugin_one_pass_at_a_time(tool, gerber, passes, mill_dir, negative_dia=False):
    expected = []
    for nr in range(passes):
        offset = DIA_isolation_parallel_passes * ((2 * nr + 1) / 2.0000001) - nr * (OVERLAP_isolation_parallel_passes / 100.0) * DIA_isolation_parallel_passes
        if negative_dia:
            offset = -offset
        expected += wkb_list(ToolIsolation.generate_envelope(tool, gerber, offset, mill_dir,
                                                             geometry=gerber.solid_geometry, nr_passes=nr))
    return expected

@pytest.mark.parametrize('negative_dia', [False, True])
def test_plugin_combined_passes_are_the_same_as_one_pass_at_a_time(app, three_threads, negative_dia):
    app.options['gerber_circle_steps_mode'] = 'fixed'     # the reference passes use the fixed steps of the object
    gerber = make_gerber(app, name='g')
    tool = make_tool(app)
    tool.new_object = Mock()
    passes = 7
    expected = plugin_one_pass_at_a_time(tool, gerber, passes, 0, negative_dia=negative_dia)

    storage = tools_storage_isolation_parallel_passes(passes, 'cl')
    ToolIsolation.combined_normal(tool, iso_obj=gerber, iso2geo=gerber.solid_geometry, tools_storage=storage,
                                  lim_area=None, sel_tools=[1], iso_except=False, extra_passes=0,
                                  negative_dia=negative_dia, plot=False, prog_plot='normal')

    assert three_threads.call_count == passes
    assert wkb_list(storage[1]['solid_geometry']) == expected
    tool.new_object.assert_called_once()

def test_isolation_geometry_edge_cases(app):
    gerber = make_gerber(app, name='g')
    # an empty selection is not the whole object
    assert gerber.isolation_geometry(0.1, geometry=[]) == []
    assert gerber.isolation_buffers([0.1], geometry=[]).get(0).is_empty
    assert gerber.isolation_geometry(0.1, geometry=None)
    # an unsupported isolation type fails
    assert gerber.isolation_geometry(0.1, iso_type=7) == 'fail'
    assert IsolationGen.orient_envelope('fail', True, Mock(), Mock()) == 'fail'
    # the circle steps count the whole circle
    for steps in (16, 32):
        gerber.geo_steps_per_circle = steps
        pad = [box(0, 0, 0.01, 0.01)]
        rings = [gerber.isolation_geometry(0.1, geometry=pad, iso_type=0)[0],
                 gerber.isolation_geometry(0.1, geometry=pad, iso_type=0,
                                           buffers=gerber.isolation_buffers([0.1], pad))[0]]
        for ring in rings:
            # 4 rounded corners of steps / 4 segments, the ring closes on its first point
            assert len(ring.coords) == 4 * (steps // 4 + 1) + 1


# ==============================================================================================================
# test_isolation_leftovers: Isolation: clear the copper left between the passes ('Clear leftovers', Tcl isolate -clear_leftovers).
# ==============================================================================================================

DIA_isolation_leftovers = 0.1

OVERLAP_isolation_leftovers = 10  # percent

R = DIA_isolation_leftovers / 2

def offset_of(k):
    # the pass offsets of the isolation: 0.05, 0.14, 0.23, ...
    return DIA_isolation_leftovers * ((2 * k + 1) / 2.0) - k * (OVERLAP_isolation_leftovers / 100.0) * DIA_isolation_leftovers

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

def make_strips_gerber(app, name='g'):
    obj = GerberObject(name, app)
    obj.solid_geometry = MultiPolygon(two_strips(0.42))
    return obj

def run_isolate_isolation_leftovers(app, gerber, dia=DIA_isolation_leftovers, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=dia, overlap=OVERLAP_isolation_leftovers, passes=4, plot=False, outname='iso', **kwargs)
    return created

def wkb_list_isolation_leftovers(geometries):
    return [g.wkb_hex for g in geometries]

def test_isolate_adds_the_leftover_paths_after_the_passes(app):
    gerber = make_strips_gerber(app)
    off = run_isolate_isolation_leftovers(app, gerber, combine=True, iso_type=2, clear_leftovers=False)['iso']
    on = run_isolate_isolation_leftovers(app, gerber, combine=True, iso_type=2, clear_leftovers=True)['iso']

    extra = on.solid_geometry[len(off.solid_geometry):]
    assert wkb_list_isolation_leftovers(on.solid_geometry[:len(off.solid_geometry)]) == wkb_list_isolation_leftovers(off.solid_geometry)
    assert extra and all(isinstance(g, LinearRing) for g in extra)
    assert on.tools[1]['solid_geometry'] == on.solid_geometry

    copper = list(gerber.solid_geometry.geoms)
    window = box(0, 1, 0.42, 9)
    assert unburned(copper, rings(off.solid_geometry), window).area == pytest.approx(0.04 * 8, rel=0.05)
    assert unburned(copper, rings(on.solid_geometry), window).area < 1e-4

def tools_storage_isolation_leftovers(passes):
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_passes=passes, tools_iso_overlap=OVERLAP_isolation_leftovers, tools_iso_isotype='full',
                tools_iso_milling_type='cl')
    return {1: {'tooldia': DIA_isolation_leftovers, 'data': data, 'solid_geometry': []}}

def run_plugin_isolation_leftovers(tool, app, gerber, **kwargs):
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    tool.app.app_obj.new_object = Mock(side_effect=new_object)
    storage = tools_storage_isolation_leftovers(4)
    storage[1]['data']['tools_iso_milling_type'] = kwargs.pop('milling_type', 'cl')
    tool.isolate(isolated_obj=gerber, sel_tools=[1], tools_storage=storage, plot=False,
                 combine=True, iso_except=False, e_passes=0, area_except=False, area_shape='square', rest=False,
                 sel_type=0, tip_shape='C1', iso_simplification=False, simplification_tol=0.001, **kwargs)
    (obj,) = created.values()
    return obj

def test_plugin_combined_passes_clear_the_leftovers(tool, app):
    gerber = make_strips_gerber(app)
    unchecked = run_plugin_isolation_leftovers(tool, app, gerber)
    off = run_plugin_isolation_leftovers(tool, app, gerber, clear_leftovers=False)
    on = run_plugin_isolation_leftovers(tool, app, gerber, clear_leftovers=True)

    assert tool.ui.clear_leftovers_cb.get_value() is False
    assert wkb_list_isolation_leftovers(unchecked.solid_geometry) == wkb_list_isolation_leftovers(off.solid_geometry)
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
            obj = run_isolate_isolation_leftovers(app, gerber, combine=True, iso_type=2, clear_leftovers=True,
                              milling_type=milling_type)['iso']
        else:
            obj = run_plugin_isolation_leftovers(tool, app, gerber, clear_leftovers=True, milling_type=milling_type)
        paths[milling_type] = [g for g in obj.solid_geometry if isinstance(g, LinearRing)]

    assert paths['cl'] and len(paths['cl']) == len(paths['cv'])
    for climb, conventional in zip(paths['cl'], paths['cv']):
        assert climb.hausdorff_distance(conventional) < 1e-9
        assert climb.is_ccw != conventional.is_ccw


# ==============================================================================================================
# test_isolation_rest_index: Isolation rest machining uses bounded bulk queries for sparse passes and early exit for dense passes.
# ==============================================================================================================

TOOLDIA = 0.2

OVERLAP_isolation_rest_index = 0.1

def polygons():
    """Pairs of pads at gaps from 0.05 mm to 1.5 mm, a pad with 2 holes too small for the tool and 1 with 2 large
    holes."""
    pads = []
    for column, gap in enumerate((0.05, 0.3, 0.45, 0.6, 0.8, 1.0, 1.5, 0.35, 0.7, 0.25)):
        x = column * 3.0
        pads.append(box(x, 0, x + 1, 1))
        pads.append(box(x, 1 + gap, x + 1, 2 + gap))
    pads.append(Polygon(box(0, 10, 3, 13).exterior.coords,
                        [box(0.5, 11, 0.6, 11.1).exterior.coords, box(2, 11, 2.1, 11.1).exterior.coords]))
    pads.append(Polygon(box(10, 10, 16, 14).exterior.coords,
                        [box(11, 11, 13, 13).exterior.coords, box(14, 11, 15, 13).exterior.coords]))
    return pads

def rest_passes_pairwise(geometry, tooldia, passes, overlap, negative_dia, forced_rest):
    """The passes of generate_rest_geometry() tested against every other polygon (the code before the index)."""
    work_geo, not_isolated = [], []
    for idx, geo in enumerate(geometry):
        good = []
        for nr_pass in range(passes):
            iso_offset = tooldia * ((2 * nr_pass + 1) / 2.0) - (nr_pass * overlap * tooldia)
            if negative_dia:
                iso_offset = -iso_offset
            check_geo = geo.buffer(iso_offset * 1.99999)
            if any(check_geo.intersects(other) for i, other in enumerate(geometry) if i != idx):
                continue
            temp_geo = geo.buffer(iso_offset)
            if nr_pass == 0 and forced_rest is False and len(geo.interiors) >= 1:
                parts = [temp_geo] if isinstance(temp_geo, Polygon) else list(temp_geo.geoms)
                if len(parts) != 1 or len(parts[0].interiors) != len(geo.interiors):
                    break
            good.append(temp_geo)
        if good:
            work_geo += good
        else:
            not_isolated.append(geo)
    isolated = []
    for geo in work_geo:
        for poly in getattr(geo, 'geoms', [geo]):
            if poly.is_empty:
                continue
            isolated += [poly.exterior] + list(poly.interiors)
    return isolated, not_isolated

def run_rest(geometry, passes, negative_dia=False, forced_rest=False, tooldia=TOOLDIA):
    tool = SimpleNamespace(app=SimpleNamespace(log=Mock()))
    return ToolIsolation.generate_rest_geometry(tool, geometry, tooldia, passes, OVERLAP_isolation_rest_index, invert=False,
                                                env_iso_type=2, negative_dia=negative_dia, forced_rest=forced_rest)

@pytest.mark.parametrize('passes, forced_rest, negative_dia', [(3, True, True)])
def test_same_passes_as_the_pairwise_test(passes, forced_rest, negative_dia):
    geometry = polygons()
    isolated, not_isolated = run_rest(geometry, passes, negative_dia, forced_rest)
    expected_isolated, expected_not_isolated = rest_passes_pairwise(
        geometry, TOOLDIA, passes, OVERLAP_isolation_rest_index, negative_dia, forced_rest)
    assert [g.wkb for g in isolated] == [g.wkb for g in expected_isolated]
    assert [g.wkb for g in not_isolated] == [g.wkb for g in expected_not_isolated]

def test_close_pads_and_small_holes_are_left_for_a_smaller_tool():
    geometry = polygons()
    __, not_isolated = run_rest(geometry, 1)
    # the 2 pads 0.05 mm apart (the check buffer of the first pass is 0.2 mm) and the pad with the small holes
    assert [g.wkb for g in not_isolated] == [g.wkb for g in geometry[0:2] + geometry[20:21]]
    __, not_isolated = run_rest(geometry, 1, forced_rest=True)
    assert [g.wkb for g in not_isolated] == [g.wkb for g in geometry[0:2]]

@pytest.mark.parametrize('negative_dia, forced_rest', [(True, True)])
def test_dense_passes_preserve_paths_and_abandoned_polygons(negative_dia, forced_rest):
    geometry = [box((i % 30) * 0.1, (i // 30) * 0.1,
                    (i % 30) * 0.1 + 0.02, (i // 30) * 0.1 + 0.02) for i in range(900)]
    geometry += [Polygon(box(10, 0, 13, 3).exterior.coords,
                         [box(10.5, 1, 10.6, 1.1).exterior.coords, box(12, 1, 12.1, 1.1).exterior.coords]),
                 box(20, 0, 22, 2)]
    actual = run_rest(geometry, 3, negative_dia, forced_rest)
    expected = rest_passes_pairwise(geometry, TOOLDIA, 3, OVERLAP_isolation_rest_index, negative_dia, forced_rest)
    assert [[g.wkb for g in part] for part in actual] == [[g.wkb for g in part] for part in expected]

def test_pad_with_one_hole_that_the_tool_cannot_enter_goes_to_the_smaller_tool():
    pad = Polygon(box(0, 0, 3, 3).exterior.coords, [box(1, 1, 1.2, 1.2).exterior.coords])
    isolated, not_isolated = run_rest([pad], 1, tooldia=0.5)
    assert isolated == [] and [g.wkb for g in not_isolated] == [pad.wkb]

    isolated, not_isolated = run_rest([pad], 1, tooldia=0.05)
    assert not_isolated == []
    assert len(isolated) == 2  # the outside and the hole


# ==============================================================================================================
# test_isolate_combine_passes: GerberObject.isolate(combine=True) must keep every pass.
# ==============================================================================================================

DIA_isolate_combine_passes = 0.2

OVERLAP_isolate_combine_passes = 10  # percent

def run_isolate_isolate_combine_passes(app, gerber, **kwargs):
    """Calls isolate() and returns {object name: Geometry object built by the init callback}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kw):
        assert kind == 'geometry'
        obj = GeometryObject(name, app)
        assert initialize(obj, app) != 'fail'
        created[name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    gerber.isolate(dia=DIA_isolate_combine_passes, overlap=OVERLAP_isolate_combine_passes, iso_type=2, plot=False, **kwargs)
    return created

def expected_offsets(passes):
    """Offset of the centre line of every pass from the copper, as isolate() computes it."""
    return [DIA_isolate_combine_passes * ((2 * n + 1) / 2.0) - n * (OVERLAP_isolate_combine_passes / 100.0) * DIA_isolate_combine_passes for n in range(passes)]

@pytest.mark.parametrize('passes', [3])
def test_combined_result_is_the_union_of_the_separate_passes(app, passes):
    gerber = make_gerber(app, name='g')
    combined = run_isolate_isolate_combine_passes(app, gerber, passes=passes, combine=True, outname='combined')
    separate = run_isolate_isolate_combine_passes(app, gerber, passes=passes, combine=False)

    combined_geo = combined['combined'].solid_geometry
    separate_geo = [g for obj in separate.values() for g in obj.solid_geometry]

    assert len(separate) == passes
    assert wkb_set(combined_geo) == wkb_set(separate_geo)

def test_tool_data_of_the_combined_object_holds_all_passes(app):
    gerber = make_gerber(app, name='g')
    combined = run_isolate_isolate_combine_passes(app, gerber, passes=2, combine=True, outname='combined')['combined']
    assert combined.tools[1]['solid_geometry'] == combined.solid_geometry
    assert len(combined.solid_geometry) > 0

def test_the_marker_of_the_real_isolation_output_is_consumed_by_milling(app):
    # the real output carries the key Milling reads: a would-be second offset is dropped
    from tests.test_milling import milling_job, xy_cuts

    created = run_isolate_isolate_combine_passes(app, make_gerber(app, name='g'), passes=1, combine=True)
    (obj,) = created.values()
    data = dict(obj.tools[1]['data'], tools_mill_offset_type=1)
    __, job = milling_job(1, multigeo=False, from_tcl=True, data=data)
    __, plain = milling_job(1, multigeo=False, from_tcl=True, data=dict(tools_mill_offset_type=0))
    assert job != 'fail' and plain != 'fail'
    assert xy_cuts(job.tools[1]['gcode']) == xy_cuts(plain.tools[1]['gcode'])
