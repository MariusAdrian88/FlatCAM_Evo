"""Isolation limited to a boundary (e.g. the board outline).

camlib.outline_area() makes the area enclosed by an outline (an Edge Cuts band, closed lines, a drawn polygon),
clip_paths_to_area() cuts the paths to it. GerberObject.isolate() (the isolate Tcl command) and the Isolation Plugin
clip each pass to the boundary and stop at the first pass that is completely outside it; when that is the first pass,
no isolation is made. The isolation tests use the headless fixtures of tests.helpers_objects and its mixed
Gerber (about 31 x 34 mm of copper).
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import shapely
from PyQt6 import sip
from shapely.geometry import LinearRing, LineString, Point, Polygon, box
from shapely.ops import unary_union

from appObjects.GeometryObject import GeometryObject
from appPlugins.ToolIsolation import ToolIsolation
from camlib import area_outline_paths, boundary_passes_message, clip_paths_to_area, outline_area
from defaults import AppDefaults
from tclCommands.TclCommandIsolate import TclCommandIsolate

from test_plugin_geometry_bases import make_tool_app
from tests.helpers_objects import app, _headless_qt, make_gerber  # noqa: F401,E402

DIA = 0.5
OVERLAP = 0  # percent
PASSES = 100
NO_PATH_INSIDE = '[ERROR_NOTCL] %s: No isolation path is inside the boundary. Check that the boundary object is the ' \
                 'outline of this board.'


def board_of(gerber, margin=1.0):
    minx, miny, maxx, maxy = unary_union(gerber.solid_geometry).bounds
    return box(minx - margin, miny - margin, maxx + margin, maxy + margin)


def far_box_of(gerber):
    # e.g. the outline of another board: not even the first pass reaches into it
    minx, miny, maxx, maxy = unary_union(gerber.solid_geometry).bounds
    return box(maxx + 50, maxy + 50, maxx + 90, maxy + 90)


def length_outside(paths, area):
    return unary_union(paths).difference(area.buffer(1e-9)).length


def reports(app):
    return [c.args[0] for c in app.inform.emit.call_args_list if 'boundary' in str(c.args[0])]


# ---------------------------------------------------------------------------------------------------------- camlib

def test_edge_cuts_band_gives_the_board_area():
    # an outline drawn with a 0.1 mm line, as in an Edge Cuts Gerber: a band (a polygon with a hole)
    band = LinearRing([(0, 0), (40, 0), (40, 90), (0, 90)]).buffer(0.05)
    area = outline_area([band])
    assert area.area == pytest.approx(40.1 * 90.1, abs=0.5)
    assert area.contains(Point(20, 45))


CHAMFERED_BOARD = [(0, 0), (50, 0), (50, 30), (40, 40), (0, 40), (0, 0)]


def test_the_segments_of_an_unbuffered_outline_give_the_board_area():
    # a Gerber loaded with Buffering = No keeps one polygon for each segment of the outline; the chamfer segment
    # alone covers less than OUTLINE_BAND_RATIO of its bounding box
    segments = [LineString([a, b]).buffer(0.05) for a, b in zip(CHAMFERED_BOARD, CHAMFERED_BOARD[1:])]
    area = outline_area(segments)
    assert area.area == pytest.approx(Polygon(CHAMFERED_BOARD).buffer(0.05).area, abs=0.01)
    assert area.contains(Point(25, 20))


def test_clip_keeps_the_paths_inside_and_cuts_the_crossing_ones():
    area = box(0, 0, 10, 10)
    inside = Point(5, 5).buffer(1)
    crossing = Point(10, 5).buffer(2)
    outside = Point(30, 30).buffer(1)
    paths = clip_paths_to_area([inside, crossing, outside], area)
    assert sum(1 for p in paths if p.equals(inside.exterior)) == 1
    assert length_outside(paths, area) == pytest.approx(0.0)
    assert unary_union(paths).length > inside.exterior.length


def test_the_report_tells_if_the_area_is_cleared():
    assert boundary_passes_message('iso', 7, 50) == \
        '[success] iso: 7 of 50 passes, the area inside the boundary is cleared.'
    assert boundary_passes_message('iso', 3, 3).startswith('[WARNING_NOTCL] iso: all 3 passes made')
    # not even the first pass reaches into the boundary: an error, not "cleared"
    assert boundary_passes_message('iso', 0, 50) == NO_PATH_INSIDE % 'iso'


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
    gerber.isolate(dia=DIA, overlap=OVERLAP, iso_type=2, plot=False, **kwargs)
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
    assert reports(app) == ['[success] iso: %d of %d passes, the area inside the boundary is cleared.' %
                            (made, PASSES)]


def test_without_a_boundary_all_the_passes_are_made(app):
    gerber = make_gerber(app, name='g')
    created, computed = run_isolate(app, gerber, passes=3, combine=True, outname='iso')
    assert computed == [0, 1, 2]
    assert reports(app) == []
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
    assert reports(app) == [NO_PATH_INSIDE % ('iso' if combine else 'g')]


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
    assert reports(app)[0].startswith('[success] g_iso: ')


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


def run_plugin(plugin, gerber, combine, passes=PASSES, e_passes=0, boundary=None, trace_boundary=False,
               envelope=None, **kwargs):
    """Calls isolate(); envelope: a stand-in for generate_envelope(), else the real one is used."""
    data = {k: deepcopy(v) for k, v in AppDefaults().defaults.items() if k.startswith('tools_')}
    data.update(tools_iso_isotype='full', tools_iso_passes=passes, tools_iso_overlap=OVERLAP,
                tools_iso_milling_type='cl')
    storage = {1: {'tooldia': DIA, 'data': data, 'solid_geometry': []}}
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


@pytest.mark.parametrize('mode', ['combined', 'rest'])
@pytest.mark.parametrize('restriction', ['selection', 'exclusion'])
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

