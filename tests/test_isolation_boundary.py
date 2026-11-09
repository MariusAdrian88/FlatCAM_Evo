"""Isolation limited to a boundary (e.g. the board outline).

camlib.outline_area() makes a concave enclosure around the selected Gerber or Geometry,
clip_paths_to_area() cuts the paths to it. GerberObject.isolate() (the isolate Tcl command) and the Isolation Plugin
clip each pass to the boundary and stop at the first pass that is completely outside it; when that is the first pass,
no isolation is made. The isolation tests use the headless fixtures of tests.helpers_objects and its mixed
Gerber (about 31 x 34 mm of copper).
"""

import math
import re
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import shapely
from PyQt6 import QtWidgets, sip
from shapely.affinity import rotate
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from appObjects.GeometryObject import GeometryObject
from appPlugins.ToolIsolation import IsolationGen, ToolIsolation
from camlib import clip_paths_to_area, outline_area
from defaults import AppDefaults
from tclCommands.TclCommandIsolate import TclCommandIsolate

from test_plugin_geometry_bases import make_tool_app
from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

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


# ---------------------------------------------------------------------------------------------------------- camlib

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


# --------------------------------------------------------------------------------------------- GerberObject.isolate

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


# ------------------------------------------------------------------------------------------------ Tcl isolate command

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


# -------------------------------------------------------------------------------------------------- Isolation Plugin

@pytest.fixture
def plugin(_headless_qt):  # noqa: F811
    tool = ToolIsolation(make_tool_app())
    tool.decimals = 4
    yield tool
    # delete the widgets now, while this module's QApplication is alive
    sip.delete(tool)


def test_polygon_selection_highlights_and_toggles_copper(plugin, monkeypatch):
    from appGUI.VisPyVisuals import ShapeCollectionVisual

    monkeypatch.setattr('appGUI.VisPyVisuals.set_state', Mock())  # No OpenGL canvas in this test.
    shapes = ShapeCollectionVisual(layers=1, pool=None,
                                   fcoptions={'global_graphic_engine_3d_no_mp': True})
    plugin.app.tool_shapes = shapes
    plugin.app.plotcanvas.translate_coords.side_effect = lambda pos: pos
    plugin.app.grid_status.return_value = False
    plugin.app.selection_type = None
    plugin.grb_obj = SimpleNamespace(solid_geometry=[box(0, 0, 2, 2)])
    event = SimpleNamespace(pos=(1, 1), button=1)

    plugin.on_poly_mouse_click_release(event)

    assert len(shapes.data) == 1
    buffers = next(iter(shapes.data.values()))
    assert len(buffers['mesh_tris']) > 0
    assert len(buffers['line_pts']) > 0

    plugin.on_poly_mouse_click_release(event)

    assert not shapes.data


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


# ---------------------------------------------------------------------------------------------- Voronoi isolation

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


def test_voronoi_site_budget_is_enforced():
    with pytest.raises(IsolationGen.VoronoiIsolationError, match='site budget'):
        IsolationGen.voronoi_isolation(_two_bodies(), box(-2, -2, 12, 12), DIA, spacing=0.001, max_sites=10)


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


def test_voronoi_large_translation_keeps_the_border_certificate():
    offset = 1_000_000.0
    bodies = [shapely.affinity.translate(body, xoff=offset, yoff=offset) for body in _two_bodies()]
    boundary = box(-2 + offset, -2 + offset, 12 + offset, 12 + offset)
    paths = IsolationGen.voronoi_isolation(bodies, boundary, DIA, spacing=0.1)
    _assert_separated(boundary, bodies, paths, DIA / 2.0)


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
    from tests.test_cncjob_gcode_speed import multi_geo_gcode, validated_data, gcode_job

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


@pytest.mark.parametrize('entry', ['plugin', 'gerber', 'tcl'])
def test_diptrace_source_copper_as_boundary_certifies_the_rounded_gcode(app, plugin, entry):
    # Removing only internal dividers must fail: outside material is not an artificial hull-clipped island.
    from appObjects.GerberObject import GerberObject
    from tests.test_cncjob_gcode_speed import gcode_job

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
    if entry == 'plugin':
        created, __ = run_plugin(plugin, gerber, combine=True, passes=1, tooldia=2 * radius,
                                 boundary=boundary, strategy='voronoi', voronoi_spacing=0.05,
                                 iso_simplification=True, simplification_tol=0.001, pad_isolation=False, overlap=10)
    elif entry == 'gerber':
        created, __ = run_isolate(app, gerber, dia=2 * radius, passes=1, boundary=boundary,
                                  strategy='voronoi', voronoi_spacing=0.05, simplify=0.001,
                                  pad_isolation=False, overlap=10)
    else:
        created = tcl_isolate(app, 'Bottom', '-dia', '0.1', '-passes', '1', '-boundary', 'Bottom',
                              '-strategy', 'voronoi', '-voronoi_spacing', '0.05', '-simplify', '0.001',
                              '-overlap', '10', '-pad_isolation', '0', objects={'Bottom': gerber})
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


def test_voronoi_plugin_late_cancel_leaves_no_partial_objects(app, plugin, monkeypatch):
    gerber = make_gerber(app, name='g')
    gerber.solid_geometry = _two_bodies()
    generate = IsolationGen.voronoi_isolation

    def cancel_after_generation(*args, **kwargs):
        paths = generate(*args, **kwargs)
        plugin.app.abort_flag = True
        return paths

    monkeypatch.setattr(IsolationGen, 'voronoi_isolation', cancel_after_generation)
    created, __ = run_plugin(plugin, gerber, combine=True, passes=1, boundary=box(-2, -2, 12, 12),
                             strategy='voronoi', voronoi_spacing=0.1)
    assert not created


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
    from tests.test_cncjob_gcode_speed import validated_data, multi_geo_gcode, gcode_job

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
    from tests.test_cncjob_gcode_speed import multi_geo_gcode, validated_data, gcode_job

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


def test_voronoi_clearance_failure_names_its_location():
    # the gap of 0.15 between the bodies is narrower than the tool diameter 0.2 (see the narrow-gap tests)
    bodies = [box(0, 0, 1, 1), box(1.15, 0, 2.15, 1)]
    with pytest.raises(IsolationGen.VoronoiIsolationError, match=r'near \(') as raised:
        IsolationGen.voronoi_isolation(bodies, box(-0.5, -0.5, 2.65, 1.5), 0.2, 0.05)
    assert raised.value.diagnostics['region'] is not None

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


def test_voronoi_ui_does_not_offer_an_ignored_boundary_trace(plugin):
    assert plugin.ui.voronoi_pads_cb.get_value() is False
    plugin.ui.boundary_cb.set_value(True)
    plugin.ui.strategy_combo.set_value('voronoi')
    assert not plugin.ui.trace_boundary_cb.isEnabled()
    assert plugin.ui.voronoi_pads_cb.isEnabled()
    plugin.ui.boundary_cb.set_value(False)
    plugin.ui.boundary_cb.set_value(True)
    assert not plugin.ui.trace_boundary_cb.isEnabled()
    plugin.ui.strategy_combo.set_value('offset')
    assert plugin.ui.trace_boundary_cb.isEnabled()
    assert not plugin.ui.voronoi_pads_cb.isEnabled()


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


def test_old_saved_tool_without_a_strategy_shows_offset(plugin):
    plugin.ui.strategy_combo.set_value('voronoi')
    plugin.ui.voronoi_spacing_entry.set_value(0.2)
    plugin.ui.voronoi_pads_cb.set_value(True)
    saved = {key: value for key, value in AppDefaults().defaults.items() if key.startswith('tools_')}
    saved.pop('tools_iso_strategy', None)
    saved.pop('tools_iso_voronoi_spacing', None)
    saved.pop('tools_iso_voronoi_pads', None)
    plugin.storage_to_form(saved)
    assert plugin.ui.strategy_combo.get_value() == 'offset'
    assert plugin.ui.voronoi_spacing_entry.get_value() == pytest.approx(0.05)
    assert plugin.ui.voronoi_pads_cb.get_value() is False


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
    from tests.test_cncjob_gcode_speed import gcode_job

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
