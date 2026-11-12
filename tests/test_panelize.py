"""Merged from: test_panelize_core: Golden tests of appParsers/panelize_core.py (no Qt objects, no app: plain dicts and shapely geometry; the fixture; test_panelize_plugin: Panelize plugin (appPlugins/ToolPanelize.py), driven headless: Panelize.on_panelize runs on a stub instance (no
"""

import numpy as np
import pytest
import shapely
import ezdxf
from copy import deepcopy
from random import Random
from unittest.mock import Mock
from shapely import affinity
from shapely.ops import linemerge, snap, unary_union
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appParsers import panelize_core as pc
from tclCommands.TclCommandPanelize import TclCommandPanelize
from camlib import grace
from tests.helpers_objects import app, assert_coords_close, assert_poly_equiv, _headless_qt, make_excellon, make_gerber, parts, polygon_union, reparse_gerber
from io import StringIO
from types import SimpleNamespace
from PyQt6 import QtCore
from appHandlers.appEdit import appEditor
from appPlugins.ToolPanelize import Panelize


# ==============================================================================================================
# test_panelize_core: Golden tests of appParsers/panelize_core.py (no Qt objects, no app: plain dicts and shapely geometry; the fixture
# ==============================================================================================================

ROWS, COLS, SPACING = 2, 3, 5.0

def freeze(obj):
    """Hashable-free deep snapshot: geometry -> WKB, containers -> same containers, everything else -> repr."""
    if isinstance(obj, shapely.geometry.base.BaseGeometry):
        return ('geo', obj.wkb)
    if isinstance(obj, dict):
        return {k: freeze(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return (type(obj).__name__, [freeze(v) for v in obj])
    return repr(obj)

def expected_offsets(bounds):
    w, h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    return [(col * (w + SPACING), row * (h + SPACING)) for row in range(ROWS) for col in range(COLS)]

def moved(geo, offset):
    return affinity.translate(polygon_union(geo), *offset)

class TestGrid:
    def test_grid_offsets_2x3(self):
        offs = pc.grid_offsets((1.0, 2.0, 11.0, 5.0), 2, 3, 5.0, 5.0)
        assert offs == pytest.approx(np.array([(0, 0), (15, 0), (30, 0), (0, 8), (15, 8), (30, 8)]))

@pytest.mark.parametrize('path', [None], ids=['mixed'])
class TestGerber:
    def build(self, app, path):
        src = make_gerber(app, name='g') if path is None else make_gerber(app, path, name='g')
        offs = pc.grid_offsets(src.bounds(), ROWS, COLS, SPACING, SPACING)
        assert offs == pytest.approx(np.array(expected_offsets(src.bounds())))
        return src, offs

    def test_every_feature_appears_once_per_offset(self, app, path):
        src, offs = self.build(app, path)
        tools, solid = pc.panelize_gerber(src.tools, src.solid_geometry, offs)
        assert list(tools) == list(src.tools)
        for apid, tool in src.tools.items():
            records = tool['geometry']
            n = len(records)
            assert len(tools[apid]['geometry']) == n * len(offs)
            for copy, off in enumerate(offs):
                for rec_idx, rec in enumerate(records):
                    new_rec = tools[apid]['geometry'][copy * n + rec_idx]
                    assert set(new_rec) == set(rec)
                    for key in ('solid', 'clear', 'follow'):
                        if key in rec:
                            assert_coords_close(new_rec[key], pc.translate_geometry(rec[key], *off),
                                                msg='%s %s' % (apid, key))
        # the composed copper of each copy is the source composed copper moved
        flat_src = parts(src.solid_geometry)
        assert len(solid) == len(flat_src) * len(offs)
        for copy, off in enumerate(offs):
            chunk = solid[copy * len(flat_src):(copy + 1) * len(flat_src)]
            assert_poly_equiv(chunk, moved(src.solid_geometry, off), msg='copy %d' % copy)

def test_gerber_dark_clear_dark_keeps_composed_geometry_per_copy():
    dark1 = box(0, 0, 10, 10)
    clear = box(2, 2, 8, 8)
    dark2 = box(4, 4, 6, 6)
    composed = dark1.difference(clear).union(dark2)
    tools = {
        10: {'type': 'R', 'size': 1.0, 'geometry': [{'solid': dark1, 'follow': Point(5, 5)},
                                                    {'solid': dark2, 'follow': Point(5, 5)}]},
        11: {'type': 'C', 'size': 1.0, 'geometry': [{'clear': clear, 'follow': Point(5, 5)}]},
    }
    offs = [(0.0, 0.0), (20.0, 0.0), (0.0, 30.0)]
    new_tools, solid = pc.panelize_gerber(tools, [composed], offs)
    assert len(solid) == 6 and all(isinstance(g, Polygon) for g in solid)  # ring + dot per copy
    for index, off in enumerate(offs):
        chunk = solid[2 * index:2 * index + 2]
        assert_poly_equiv(shapely.MultiPolygon(chunk), affinity.translate(composed, *off))
        # the ring (dark minus clear) has a hole, the inner dot fills it again: area 100 - 36 + 4
        assert polygon_union(chunk).area == pytest.approx(68.0)
    # the clear record stays a clear record, with no 'solid' key
    assert [list(r) for r in new_tools[11]['geometry']] == [['clear', 'follow']] * 3
    assert [list(r) for r in new_tools[10]['geometry']] == [['solid', 'follow']] * 6
    # record order: copy 1 of both dark records, then copy 2, ...
    assert new_tools[10]['geometry'][1]['solid'].bounds == (4, 4, 6, 6)
    assert new_tools[10]['geometry'][2]['solid'].bounds == (20, 0, 30, 10)

def test_gerber_panel_solid_is_a_flat_list_of_simple_geometries():
    """A MultiPolygon in the solid would not plot: the panel solid holds its parts."""
    multi = MultiPolygon([box(0, 0, 1, 1), box(3, 3, 4, 4)])
    tools = {10: {'type': 'C', 'size': 1.0, 'geometry': [{'solid': [box(0, 0, 1, 1), [Point(5, 5)]], 'follow': None}]}}
    new_tools, solid = pc.panelize_gerber(tools, multi, [(0, 0), (10, 0)])
    assert len(solid) == 4 and all(isinstance(g, Polygon) for g in solid)
    assert solid[2].bounds == (10, 0, 11, 1) and solid[3].bounds == (13, 3, 14, 4)
    # a list-valued field stays a flat list, None stays None
    records = new_tools[10]['geometry']
    assert [g.bounds for g in records[1]['solid']] == [(10, 0, 11, 1), (15, 5, 15, 5)]
    assert records[1]['follow'] is None

class TestExcellon:
    def build(self, app):
        src = make_excellon(app, name='e')
        return src, pc.grid_offsets(src.bounds(), ROWS, COLS, SPACING, SPACING)

    def test_drills_and_slots_at_every_offset(self, app):
        src, offs = self.build(app)
        tools = pc.panelize_excellon(src.tools, offs)
        assert list(tools) == list(src.tools)
        for tid, tool in src.tools.items():
            nd, ns = len(tool['drills']), len(tool['slots'])
            assert len(tools[tid]['drills']) == nd * len(offs) and len(tools[tid]['slots']) == ns * len(offs)
            for copy, (dx, dy) in enumerate(offs):
                for i, drill in enumerate(tool['drills']):
                    new = tools[tid]['drills'][copy * nd + i]
                    assert (new.x, new.y) == pytest.approx((drill.x + dx, drill.y + dy))
                for i, (start, stop) in enumerate(tool['slots']):
                    new_start, new_stop = tools[tid]['slots'][copy * ns + i]
                    assert (new_start.x, new_start.y, new_stop.x, new_stop.y) == \
                        pytest.approx((start.x + dx, start.y + dy, stop.x + dx, stop.y + dy))

@pytest.mark.parametrize('slots', [
    [(Point(0, 0), Point(2, 1)), (Point(3, 2), Point(3, 2))],
    [(Point(0, 0, 4), Point(2, 1, 5))],
])
def test_excellon_panel_polygons_match_rebuild(app, slots):
    src = {1: {'tooldia': 0.8, 'drills': [Point(1, 2)], 'slots': slots,
               'drills_to': [7], 'slots_to': [8] * len(slots), 'data': {'feed': 12},
               'solid_geometry': [box(-100, -100, 100, 100)]},
           2: {'tooldia': 1.3, 'slots': [(Point(1, 1), Point(2, 3))]},
           3: {'tooldia': 0.4, 'drills': [Point(4, 5)]}, 4: {'tooldia': 2.0}}
    before = freeze(src)
    offsets = [(0, 0), (10, 0), (0, 12)]
    got, want = ExcellonObject('got', app), ExcellonObject('want', app)
    got.tools = pc.panelize_excellon(src, offsets)
    want.tools = pc.panelize_excellon(src, offsets)
    # Destination settings, not the stale source polygons, determine the seed.
    got.circle_steps = want.circle_steps = lambda radius: 28
    got.tools[1]['tooldia'] = want.tools[1]['tooldia'] = 1.2
    assert want.create_geometry() is None
    pc.create_panel_excellon_geometry(got, src, offsets)
    assert freeze(src) == before
    assert len(got.solid_geometry) == len(want.solid_geometry)
    for tid in want.tools:
        assert got.tools[tid]['data'] == want.tools[tid]['data']
        assert len(got.tools[tid]['solid_geometry']) == len(want.tools[tid]['solid_geometry'])
    assert all(a.equals_exact(b, 1e-12) for a, b in zip(got.solid_geometry, want.solid_geometry))
    assert got.tools[1]['drills_to'] == [7] * 3
    assert got.tools[1]['slots_to'] == [8] * (3 * len(slots))
    old = freeze((got.tools, got.solid_geometry))
    with pytest.raises(grace):
        pc.create_panel_excellon_geometry(got, src, offsets, abort=lambda: True)
    assert freeze((got.tools, got.solid_geometry)) == old

def test_excellon_panel_last_batch_abort_and_failure_publish_nothing(app, monkeypatch):
    src = {1: {'tooldia': 0.8, 'slots': [(Point(0, 0), Point(2, 1))]}}
    dst = ExcellonObject('dst', app)
    dst.tools = pc.panelize_excellon(src, [(0, 0)])
    before = freeze((dst.tools, dst.solid_geometry))
    state = {'abort': False}
    translate = pc.translate_geometry

    def late_abort(*args, **kwargs):
        result = translate(*args, **kwargs)
        state['abort'] = True
        return result

    monkeypatch.setattr(pc, 'translate_geometry', late_abort)
    with pytest.raises(grace):
        pc.create_panel_excellon_geometry(dst, src, [(0, 0)], abort=lambda: state['abort'])
    assert freeze((dst.tools, dst.solid_geometry)) == before
    # An invalid later tool must not publish the completed first tool either.
    dst.tools[2] = {'tooldia': 'invalid', 'drills': [], 'slots': []}
    before = freeze((dst.tools, dst.solid_geometry))
    with pytest.raises(TypeError):
        pc.create_panel_excellon_geometry(dst, src, [(0, 0)])
    assert freeze((dst.tools, dst.solid_geometry)) == before

@pytest.mark.parametrize('slots', [[], [(Point(), Point(1, 1))], [((0, 0), (1, 1))]])
def test_excellon_panel_unusual_slots_keep_builder_semantics(app, slots):
    src = {1: {'tooldia': 0.8, 'slots': slots}}
    dst, ref = ExcellonObject('dst', app), ExcellonObject('ref', app)
    dst.tools, ref.tools = deepcopy(src), deepcopy(src)
    result = ref.create_geometry()
    if result == 'fail':
        with pytest.raises(Exception):
            pc.create_panel_excellon_geometry(dst, src, [(0, 0)])
    else:
        pc.create_panel_excellon_geometry(dst, src, [(0, 0)])
        assert freeze(dst.solid_geometry) == freeze(ref.solid_geometry)
    dst.tools = pc.panelize_excellon({1: {'tooldia': 0.8}}, [])
    pc.create_panel_excellon_geometry(dst, src, [])
    assert dst.solid_geometry == []

class TestGeometry:
    def tools_multigeo(self):
        line = LineString([(0, 0), (10, 0)])
        return {1: {'tooldia': 1.0, 'data': {'cutz': -1.0},
                    'solid_geometry': [line, MultiLineString([[(0, 5), (10, 5)], [(0, 6), (10, 6)]]),
                                       LineString()]}}

    def test_multigeo_tools_and_solid(self):
        tools_in = self.tools_multigeo()
        solid_in = [LineString([(0, 0), (10, 0)]), [LineString([(1, 1), (2, 2)])]]
        tools, solid = pc.panelize_geometry(tools_in, solid_in, True, [(0, 0), (20, 0)])
        geos = tools[1]['solid_geometry']
        assert len(geos) == 6  # 3 non-empty parts x 2 copies, the empty one dropped
        assert all(isinstance(g, LineString) for g in geos)
        assert geos[3].bounds == (20, 0, 30, 0)
        assert tools[1]['data'] == {'cutz': -1.0} and tools[1]['data'] is not tools_in[1]['data']
        # flat composed solid: nested list flattened
        assert len(solid) == 4 and all(isinstance(g, LineString) for g in solid)
        assert solid[2].bounds == (20, 0, 30, 0) and solid[3].bounds == (21, 1, 22, 2)

    def test_non_multigeo_keeps_tools_and_is_flat(self):
        tools_in = {1: {'tooldia': 1.0, 'solid_geometry': [box(0, 0, 1, 1)]}}
        poly = MultiPolygon([box(0, 0, 1, 1), box(3, 3, 4, 4)])
        tools, solid = pc.panelize_geometry(tools_in, poly, False, [(0, 0), (10, 0), (0, 10)])
        assert tools == tools_in and tools is not tools_in and tools[1] is not tools_in[1]
        assert len(solid) == 6 and all(isinstance(g, Polygon) for g in solid)  # Multi split

    def test_merge_tool_lines_fuses_shared_borders(self):
        # 2 copies of a square outline side by side share the vertical border x = 10
        outline = [LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])]
        tools, _ = pc.panelize_geometry({1: {'solid_geometry': outline}}, [], True, [(0, 0), (10, 0)])
        assert len(tools[1]['solid_geometry']) == 2
        pc.merge_tool_lines(tools)
        fused = tools[1]['solid_geometry']
        assert len(fused) == 1
        assert fused[0].length == pytest.approx(70.0)  # 2 x 40 - the shared border (10) once
        pc.merge_tool_lines(tools, snap_overlaps=True)
        assert tools[1]['solid_geometry'][0].length == pytest.approx(70.0)

@pytest.mark.parametrize('offsets, snap_overlaps', [
    ([(0, 0), (30, 0), (0, 30), (30, 30)], False),   # copies apart: the source is merged once
    ([(0, 0), (30, 0), (0, 30), (30, 30)], True),
    ([(0, 0), (10, 0), (20, 0)], True),              # copies touch: the lines of all copies are merged
    ([(0, 0), (0, 0)], False),                       # 2 copies at the same place: their paths are cut once
])
def test_optimize_panel_lines_equals_merge_tool_lines(offsets, snap_overlaps):
    ring = LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    src_tools = {1: {'solid_geometry': [ring, LineString([(2, 2), (8, 2)]), box(4, 4, 5, 5)]}}
    expected, _ = pc.panelize_geometry(src_tools, [], True, offsets)
    result, _ = pc.panelize_geometry(src_tools, [], True, offsets)
    pc.merge_tool_lines(expected, snap_overlaps=snap_overlaps)
    pc.optimize_panel_lines(src_tools, result, offsets, snap_overlaps=snap_overlaps)
    want, got = expected[1]['solid_geometry'], result[1]['solid_geometry']
    assert len(got) == len(want) == 1 + len(offsets) and got[0].equals(want[0])
    assert len(got[0].geoms) == len(want[0].geoms)
    assert got[0].length == pytest.approx(want[0].length)                # no path is cut 2 times
    assert all(a.equals_exact(b, 0) for a, b in zip(got[1:], want[1:]))  # the polygons are kept


@pytest.mark.parametrize('offsets', [[(0, 0), (30, 0)], [(0, 0), (10, 0)],
                                      [(0, 0), (5, 0)], [(0, 0), (10 + 5e-8, 0)], [], [(0, 0)]])
@pytest.mark.parametrize('snap_overlaps', [False, True])
def test_panelize_geometry_optimized_matches_two_stage(offsets, snap_overlaps):
    line = LineString([(0, 0), (5, 5), (10, 0)])
    source = {1: {'data': {'depth': [-1]}, 'solid_geometry': [line, line,
                  LineString([(0, 4), (10, 4)]), LineString([(0, 4 + 5e-8), (10, 4 + 5e-8)]),
                  box(-50, -50, 50, 50), LineString()]},
              2: {'solid_geometry': [LineString([(0, 0), (40, 0)]), Point(2, 3)]},
              3: {'solid_geometry': [box(0, 0, 1, 1), LineString(),
                                      shapely.GeometryCollection([line, Point(1, 2)])]},
              4: {'solid_geometry': [LineString()]}}
    solid = [line, LineString()]
    before = freeze(source)
    expected, expected_solid = pc.panelize_geometry(source, solid, True, offsets)
    pc.optimize_panel_lines(source, expected, offsets, snap_overlaps=snap_overlaps)
    result, result_solid = pc.panelize_geometry_optimized(
        source, solid, True, iter(offsets), snap_overlaps=snap_overlaps)
    assert freeze(result_solid) == freeze(expected_solid)
    for tid in source:
        got, want = result[tid]['solid_geometry'], expected[tid]['solid_geometry']
        assert len(got) == len(want)
        for a, b in zip(got, want):
            assert a.geom_type == b.geom_type
            assert a.equals_exact(b, 1e-12)
            assert a.length == pytest.approx(b.length)
        assert got is not source[tid]['solid_geometry']
    result[1]['data']['depth'].append(-2)
    assert freeze(source) == before
    plain = pc.panelize_geometry_optimized(source, solid, False, offsets)
    assert freeze(plain) == freeze(pc.panelize_geometry(source, solid, False, offsets))
    with pytest.raises(grace):
        pc.panelize_geometry_optimized(source, solid, True, offsets, abort=lambda: True)

    cancelled = {'value': False}
    def progress(*args):
        cancelled['value'] = True
    if offsets:
        with pytest.raises(grace):
            pc.panelize_geometry_optimized(source, solid, True, offsets,
                                          abort=lambda: cancelled['value'], progress=progress)

class TestAbortAndProgress:
    def test_abort_raises_grace(self, app):
        src = make_gerber(app, name='g')
        offs = pc.grid_offsets(src.bounds(), 2, 2, 5, 5)
        with pytest.raises(grace):
            pc.panelize_gerber(src.tools, src.solid_geometry, offs, abort=lambda: True)
        exc = make_excellon(app, name='e')
        with pytest.raises(grace):
            pc.panelize_excellon(exc.tools, offs, abort=lambda: True)
        with pytest.raises(grace):
            pc.panelize_geometry({}, [box(0, 0, 1, 1)], False, offs, abort=lambda: True)

    def test_abort_during_the_last_records_of_the_last_copy_gives_no_panel(self, app):
        src = make_gerber(app, name='g')
        offs = pc.grid_offsets(src.bounds(), 2, 2, 5, 5)
        last_apid = list(src.tools)[-1]
        state = {'abort': False}

        class LateAbort(dict):
            # a record value that is copied for each copy: the abort arrives while the last record is rebuilt
            def __deepcopy__(self, memo):
                state['abort'] = state.get('copies', 0) + 1 == len(offs)
                state['copies'] = state.get('copies', 0) + 1
                return LateAbort(self)

        tools = {apid: dict(tool) for apid, tool in src.tools.items()}
        records = [dict(r) for r in tools[last_apid]['geometry']]
        records[-1]['mark'] = LateAbort()
        tools[last_apid]['geometry'] = records
        with pytest.raises(grace):
            pc.panelize_gerber(tools, src.solid_geometry, offs, abort=lambda: state['abort'])

def test_gerber_tools_from_geometry_single_geo():
    ring = LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    pad = box(20, 0, 22, 2)
    src_tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}}
    src = [MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)]), [pad], ring]
    before = deepcopy(src)
    tools, solid = pc.gerber_tools_from_geometry(src_tools, src, False)
    assert list(tools) == [0] and tools[0]['type'] == 'REG' and tools[0]['size'] == 0.0
    records = tools[0]['geometry']
    # the MultiPolygon is split; the closed line stays a line, as in the returned solid
    assert len(records) == 4 and len(solid) == 4
    assert [type(r['solid']) for r in records] == [Polygon, Polygon, Polygon, LineString]
    assert all(r['follow'].equals(r['solid'].exterior) for r in records[:3])
    assert records[3]['solid'].equals(ring) and records[3]['follow'].equals(ring)
    # every record has the same geometry as its solid entry: the export and the plot show the same copper
    assert all(r['solid'].equals(s) for r, s in zip(records, solid))
    assert freeze(src) == freeze(before)

def test_gerber_tools_from_geometry_multigeo_fills_lines_like_the_former_panel_code():
    open_line = LineString([(0, 0), (4, 0), (4, 4)])
    two_points = LineString([(10, 0), (12, 0)])
    src_tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [open_line, two_points]}}
    tools, solid = pc.gerber_tools_from_geometry(src_tools, [], True)
    records = tools[0]['geometry']
    assert [type(r['solid']) for r in records] == [Polygon, LineString]
    assert records[0]['solid'].area == pytest.approx(8.0)
    assert records[0]['follow'].equals(records[0]['solid'].exterior)
    assert solid.symmetric_difference(shapely.unary_union([r['solid'] for r in records])).area < 1e-9

TOLERANCE = 1e-7

def pairwise_merge(lines):
    """The original snapping loop: the oracle of the indexed one."""
    lines = list(lines)
    for idx, line in enumerate(lines):
        for idx_s in range(idx + 1, len(lines)):
            result = snap(lines[idx_s], line, tolerance=TOLERANCE)
            if result and not result.is_empty:
                lines[idx_s] = result
    return unary_union(linemerge(lines))

def indexed_merge(lines):
    tools = {1: {'solid_geometry': list(lines)}}
    pc.merge_tool_lines(tools, snap_overlaps=True)
    return tools[1]['solid_geometry'][0]

@pytest.mark.parametrize('seed', range(1))
def test_snapping_matches_pairwise_order_for_clustered_paths(seed):
    rng = Random(seed)
    lines = [LineString([(rng.randrange(3) + rng.uniform(-1, 1) * TOLERANCE, 0),
                         (rng.randrange(3) + rng.uniform(-1, 1) * TOLERANCE, 1)]) for _ in range(25)]
    assert indexed_merge(lines).equals_exact(pairwise_merge(lines), 0)

def test_snapping_honors_cancellation_between_candidates():
    lines = [LineString([(x, row * 10), (x, row * 10 + 1)]) for row in range(3) for x in (0, TOLERANCE / 2)]
    tools = {1: {'solid_geometry': lines}}
    with pytest.raises(grace):
        pc.merge_tool_lines(tools, snap_overlaps=True, abort=lambda: True)
    assert tools[1]['solid_geometry'] is lines

def run_panelize(app, src, *argv):
    """Runs the command. Returns (result, created) where created = {name: object built by the init callback}."""
    app.f_handlers = appIO(app)
    created = {}

    def new_object(kind, name, initialize, plot=True, **kwargs):
        cls = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind]
        obj = cls(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    app.collection.get_by_name = Mock(side_effect=lambda n: src if n == src.obj_options['name'] else None)
    app.app_obj.new_object = Mock(side_effect=new_object)
    cmd = TclCommandPanelize(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed), created

def test_gerber_panel_inherits_export_as_regions_and_macros(app):
    src = make_gerber(app, name='g')
    src.export_as_regions = True
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-outname', 'p')
    pan = created['p']
    assert pan.export_as_regions is True
    assert set(pan.aperture_macros) == set(src.aperture_macros) and pan.aperture_macros

def test_excellon_panel_keeps_the_table_of_the_hole_ids(app):
    from appParsers.ParseExcellon import hole_ids, to_state
    src = ExcellonObject('e', app)
    text = "M48\nMETRIC\nT1C1.0\n%\nG05\nT1\n; #@! TO.C,R1\nX1.0Y1.0\n; #@! TD\nX2.0Y2.0\nM30\n"
    assert src.parse_lines(text.splitlines(keepends=True)) is None
    src.create_geometry()
    src.obj_options['name'] = 'e'
    res, created = run_panelize(app, src, 'e', '-rows', '1', '-columns', '2', '-outname', 'p')
    pan = created['p']
    ids = hole_ids(pan.tools[1], 'drills')
    assert ids is not None and len(ids) == 4
    assert [to_state(pan.to_attributes, i) for i in ids].count({'.C': ['R1']}) == 2
    assert pan.to_attributes == src.to_attributes and pan.to_attributes is not src.to_attributes

@pytest.mark.parametrize('value', ['1', '0'])
def test_use_thread_values_start_a_worker_task_or_run_at_once(app, value):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-columns', '2', '-rows', '1', '-use_thread', value)
    assert res is None
    if value == '0':
        assert list(created) == ['g_panelized']
        app.worker_task.emit.assert_not_called()
    else:
        assert not created
        app.worker_task.emit.assert_called_once()
        task = app.worker_task.emit.call_args[0][0]
        task['fcn'](*task['params'])
        assert list(created) == ['g_panelized']

def test_missing_box_object_fails_and_creates_nothing(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-box', 'no_such_box')
    assert res == 'fail' and not created
    app.app_obj.new_object.assert_not_called()

def test_only_rows_means_one_column(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-rows', '3', '-outname', 'p')
    # the aperture 11 of the mixed fixture holds 1 record
    assert res is None and len(created['p'].tools[11]['geometry']) == 3
    assert created['p'].bounds()[2] == pytest.approx(src.bounds()[2])
    assert created['p'].bounds()[3] > src.bounds()[3] + 2 * 5.0

@pytest.mark.parametrize('argv', [(), ('-rows', '0', '-columns', '2')])
def test_missing_zero_or_negative_counts_fail_instead_of_an_empty_panel(app, argv):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', *argv, '-outname', 'p')
    assert res == 'fail' and not created


# ==============================================================================================================
# test_panelize_plugin: Panelize plugin (appPlugins/ToolPanelize.py), driven headless: Panelize.on_panelize runs on a stub instance (no
# ==============================================================================================================

class Value:
    """Minimal stand-in of an FC widget."""

    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def currentText(self):
        return self.value

    def isChecked(self):
        return bool(self.value)

def run_plugin(app, src, box_obj=None, rows=2, cols=3, spacing=5.0, panel_type='gerber', optimize=False,
               constrain=None, before=None, name=None, box_name=None, fail_job=False, reference=None,
               step_repeat=False):
    """Returns (created, informs). constrain = (max_x, max_y) switches the constrain option on. before: a list that
    receives the messages shown before the worker task runs (the GUI-thread part)."""
    app.f_handlers = appIO(app)
    app.edit_class = appEditor(app)
    created = {}
    informs = []

    def new_object(kind, name, initialize, plot=True, **kwargs):
        if fail_job:
            raise RuntimeError('boom')
        cls = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind]
        obj = cls(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    objects = {src.obj_options['name']: src}
    if box_obj is not None:
        objects[box_obj.obj_options['name']] = box_obj
    app.collection.get_by_name = Mock(side_effect=lambda n: objects.get(n))
    app.app_obj.new_object = Mock(side_effect=new_object)
    app.delete_selection_shape = Mock()
    app.inform = SimpleNamespace(emit=lambda text, *a, **k: informs.append(text))
    tasks = []
    app.worker_task = SimpleNamespace(emit=lambda task: tasks.append(task))

    plugin = QtCore.QObject.__new__(Panelize)
    plugin.app = app
    plugin.constrain_flag = False
    plugin.outname = ''
    bx_name = (box_obj or src).obj_options['name']
    plugin.ui = SimpleNamespace(
        object_combo=Value(name or src.obj_options['name']), box_combo=Value(box_name or bx_name),
        reference_radio=Value(reference or ('bbox' if box_obj is None else 'object')),
        spacing_columns=Value(spacing), spacing_rows=Value(spacing), rows=Value(rows), columns=Value(cols),
        x_width_entry=Value(constrain[0] if constrain else 0.0), y_height_entry=Value(constrain[1] if constrain else 0.0),
        constrain_cb=Value(bool(constrain)), optimization_cb=Value(optimize), panel_type_radio=Value(panel_type),
        step_repeat_cb=Value(step_repeat))
    plugin.on_panelize()
    if before is not None:
        before.extend(informs)
    if not tasks:
        return created, informs
    assert len(tasks) == 1
    tasks[0]['fcn'](*tasks[0]['params'])
    return created, informs

def same_tools(pan, other, key):
    assert list(pan.tools) == list(other.tools)
    for apid in pan.tools:
        a, b = pan.tools[apid][key], other.tools[apid][key]
        assert len(a) == len(b)
        for rec_a, rec_b in zip(a, b):
            if isinstance(rec_a, dict):
                assert set(rec_a) == set(rec_b)
                for k in rec_a:
                    assert_coords_close(rec_a[k], rec_b[k], msg='%s %s' % (apid, k))
            else:
                assert_coords_close(rec_a, rec_b)

def test_gerber_plugin_equals_tcl(app):
    src = make_gerber(app, name='g')
    _, tcl = run_panelize(app, src, 'g', '-rows', '2', '-columns', '3', '-spacing_columns', '5',
                          '-spacing_rows', '5', '-outname', 'p')
    created, informs = run_plugin(app, src, panel_type='gerber')
    pan = created['g_panelized']
    same_tools(pan, tcl['p'], 'geometry')
    assert_coords_close(pan.solid_geometry, tcl['p'].solid_geometry)
    assert pan.export_as_regions == tcl['p'].export_as_regions
    assert set(pan.aperture_macros) == set(src.aperture_macros)
    assert pan.source_file.startswith('G04*')

def test_excellon_plugin_equals_tcl(app):
    src = make_excellon(app, name='e')
    src.tools[1]['slots'].append((Point(1, 1), Point(3, 2)))
    src.tools[1]['solid_geometry'] = [box(-100, -100, 100, 100)]
    _, tcl = run_panelize(app, src, 'e', '-rows', '2', '-columns', '3', '-spacing_columns', '5',
                          '-spacing_rows', '5', '-outname', 'q')
    created, _ = run_plugin(app, src, panel_type='gerber')
    pan, ref = created['e_panelized'], tcl['q']
    assert list(pan.tools) == list(ref.tools)
    for tid in ref.tools:
        assert_coords_close(pan.tools[tid]['drills'], ref.tools[tid]['drills'])
        assert [(a.x, a.y, b.x, b.y) for a, b in pan.tools[tid]['slots']] == \
            pytest.approx(np.array([(a.x, a.y, b.x, b.y) for a, b in ref.tools[tid]['slots']]))
    assert pan.bounds() == pytest.approx(ref.bounds())
    assert_coords_close(pan.solid_geometry, ref.solid_geometry)
    assert pan.source_file.startswith('M48')
    for obj in (pan, ref):
        again = ExcellonObject('again', app)
        assert again.parse_lines(obj.source_file.splitlines(keepends=True)) is None
        assert again.create_geometry() is None
        for tid, tool in obj.tools.items():
            assert again.tools[tid]['tooldia'] == pytest.approx(tool['tooldia'])
            assert_coords_close(again.tools[tid]['drills'], tool['drills'])
            assert len(again.tools[tid]['slots']) == len(tool['slots'])
            for got, want in zip(again.tools[tid]['slots'], tool['slots']):
                assert_coords_close(list(got), list(want))
        assert_poly_equiv(again.solid_geometry, obj.solid_geometry)

def test_constrain_reduces_the_grid(app):
    src = make_gerber(app, name='g')
    xmin, ymin, xmax, ymax = src.bounds()
    w, h = xmax - xmin, ymax - ymin
    # room for 2 columns and 1 row only (spacing 5)
    created, informs = run_plugin(app, src, rows=3, cols=4, constrain=(2 * w + 5.5, h + 1.0))
    pan = created['g_panelized']
    assert len(parts(pan.solid_geometry)) == 2 * len(parts(src.solid_geometry))

def make_geometry(app, multigeo):
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    outline = LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    src.solid_geometry = [outline, [box(2, 2, 3, 3)]]
    src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [outline]}}
    return src

def test_geometry_plugin_multigeo_merges_the_shared_borders(app):
    src = make_geometry(app, True)
    created, informs = run_plugin(app, src, panel_type='geometry', rows=1, cols=2, spacing=0.0, optimize=True)
    pan = created['src_panelized']
    assert pan.multigeo is True
    fused = pan.tools[1]['solid_geometry']
    assert len(fused) == 1 and fused[0].length == pytest.approx(70.0)
    # the composed solid_geometry is flat
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry) and len(pan.solid_geometry) == 4

@pytest.mark.parametrize('spacing, optimize', [(5.0, False), (0.0, True)])
def test_geometry_panel_dxf_roundtrip_and_move(app, spacing, optimize):
    from appParsers.DXF import dxf_to_shapes
    app.options['geometry_dxf_format'] = 'R2010'
    src = make_geometry(app, True)
    src.tools[1]['solid_geometry'].append(LineString([(2, 2), (3, 4), (4, 2)]))
    created, _ = run_plugin(app, src, panel_type='geometry', rows=1, cols=2,
                            spacing=spacing, optimize=optimize)
    pan = created['src_panelized']

    def check(doc):
        got = dxf_to_shapes(doc)
        want = list(parts(pan.tools[1]['solid_geometry']))
        assert len(got) == len(want)
        assert all(a.equals_exact(b, 1e-9) for a, b in zip(got, want))
        assert unary_union(got).bounds == pytest.approx(unary_union(want).bounds)

    doc = ezdxf.read(StringIO(pan.source_file))
    assert bool(doc.modelspace().query('INSERT')) == (not optimize)
    check(doc)
    pan.offset((17.5, -4.0))
    pan.regenerate_source()
    moved_doc = ezdxf.read(StringIO(pan.source_file))
    assert not moved_doc.modelspace().query('INSERT')
    check(moved_doc)

@pytest.mark.parametrize('multigeo', [False, True])
def test_geometry_source_to_gerber_panel_solid(app, multigeo):
    src = make_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2, spacing=0.0)
    pan = created['src_panelized']
    assert pan.multigeo is True and pan.source_file.startswith('G04*')
    if multigeo:
        # rebuilt from the tool lines: the 2 outlines fused into 1 polygon
        assert polygon_union(pan.solid_geometry).area == pytest.approx(200.0)
    else:
        assert len(parts(pan.solid_geometry)) == 4

def test_source_excellon_without_slots_or_drills_keys_is_not_changed(app):
    src = make_excellon(app, name='e')
    for tool in src.tools.values():
        del tool['slots']
    del src.tools[2]['drills']
    keys_before = {tid: sorted(tool) for tid, tool in src.tools.items()}
    created, _ = run_plugin(app, src)
    assert {tid: sorted(tool) for tid, tool in src.tools.items()} == keys_before
    # the panel still has both keys everywhere
    assert all('drills' in t and 'slots' in t for t in created['e_panelized'].tools.values())

def test_gerber_to_geometry_panel_is_the_translated_copper_with_tools_and_a_dxf_source(app):
    # The panel gets the tool table of the Geometry (like convert_any2geo); every tool holds the translated copper.
    src = make_gerber(app, name='g')
    created, _ = run_plugin(app, src, panel_type='geometry', rows=2, cols=2)
    pan = created['g_panelized']
    src_area = polygon_union(src.solid_geometry).area
    assert pan.multigeo is True
    assert pan.tools
    for tool in pan.tools.values():
        assert polygon_union(tool['solid_geometry']).area == pytest.approx(4 * src_area)
        assert 'tooldia' in tool and isinstance(tool['data'], dict) and tool['data']
    assert polygon_union(pan.solid_geometry).area == pytest.approx(4 * src_area)
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry)
    assert all(not isinstance(g, (list, tuple)) for tool in pan.tools.values() for g in tool['solid_geometry'])
    assert pan.obj_options['tools_mill_tooldia'] == pan.tools[min(pan.tools)]['tooldia']
    # a Geometry keeps its source as DXF text
    assert pan.source_file and len(ezdxf.read(StringIO(pan.source_file)).modelspace()) > 0

def make_poly_geometry(app, multigeo):
    """A Geometry made of polygons only: 3 elements."""
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    polys = [box(0, 0, 4, 4), box(6, 0, 8, 2), box(10, 0, 12, 2)]
    src.solid_geometry = list(polys)
    if multigeo:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': polys[:2]},
                     2: {'tooldia': 2.0, 'data': {}, 'solid_geometry': polys[2:]}}
    else:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}}
    return src

@pytest.mark.parametrize('multigeo', [False, True], ids=['single-geo', 'multigeo'])
def test_geometry_to_gerber_panel_keeps_the_records_and_has_a_source(app, multigeo):
    src = make_poly_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=2, cols=2)
    pan = created['src_panelized']
    assert pan.tools[0]['type'] == 'REG'
    records = pan.tools[0]['geometry']
    assert len(records) == 4 * 3
    assert polygon_union([r['solid'] for r in records]).symmetric_difference(
        polygon_union(pan.solid_geometry)).area < 1e-6
    assert pan.multigeo is True and pan.multitool is False and pan.follow is False
    assert pan.export_as_regions is False
    assert isinstance(pan.source_file, str) and pan.source_file.strip()

def test_geometry_to_gerber_panel_with_a_line_exports_as_regions(app, tmp_path):
    src = make_poly_geometry(app, False)
    src.solid_geometry = [box(0, 0, 4, 4), LineString([(6, 0), (9, 3)])]
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2)
    pan = created['src_panelized']
    assert pan.export_as_regions is True
    assert isinstance(pan.source_file, str) and pan.source_file.strip()
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).area == pytest.approx(2 * 16)

def test_constraint_smaller_than_one_board_does_not_schedule_a_panel(app):
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, constrain=(0.001, 0.001))
    assert not created
    app.collection.promise.assert_not_called()

@pytest.mark.parametrize('spacing', [5.0, -1.0])       # copies apart (polygons of the source, moved) / overlapping
def test_gerber_panel_exported_as_regions_has_the_copper_of_the_panel(app, tmp_path, spacing):
    from appParsers.ParseGerber import Gerber
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, spacing=spacing)
    pan = created['g_panelized']
    assert Gerber.needs_region_export(pan)
    again = reparse_gerber(app, pan.source_file, tmp_path)
    want = polygon_union(pan.solid_geometry)
    assert polygon_union(again.solid_geometry).symmetric_difference(want).area < 1e-6 * want.area
    assert 'region_polygons_once' not in vars(pan)      # a later export of a changed panel makes its own union

CLEAR_GBR = 'tests/fixtures/orcad_17.2/Copper_Bottom.art'         # dark and clear objects, small

def flash_count(obj):
    return sum(1 for tool in obj.tools.values() if tool.get('type') != 'REG' for record in tool.get('geometry', ())
               if record.get('follow') is not None and record['follow'].geom_type == 'Point'
               and not getattr(record.get('solid'), 'is_empty', True))

def same_copper(app, pan, tmp_path):
    again = reparse_gerber(app, pan.source_file, tmp_path)
    want = polygon_union(pan.solid_geometry)
    assert polygon_union(again.solid_geometry).symmetric_difference(want).area <= 2e-3 * want.area
    return again

@pytest.mark.parametrize('spacing', [5.0, -0.5, 0.01])
def test_normal_panel_keeps_patched_copper_and_flashes(app, tmp_path, spacing):
    src = GerberObject('g', app)
    src.tools = {10: {'type': 'R', 'size': 2.0, 'width': 2.0, 'height': 2.0,
                     'geometry': [{'solid': box(0, 0, 2, 2), 'follow': Point(1, 1), 'seq': 0}]}}
    # Edited copper: the record needs both a clear patch and a dark patch. Keep its flash in the file.
    src.solid_geometry = [box(0.1, 0, 2, 2).difference(box(0.5, 0.5, 1.5, 1.5)),
                          box(2.5, 0, 3, 0.5), box(0, 2.5, 0.5, 3)]
    src.export_as_regions = True
    src.records_in_order = True
    # Overlapping copies or grown correction patches reaching a neighbour need the whole-panel check.
    created, _ = run_plugin(app, src, rows=1, cols=2, spacing=spacing)
    pan = created['g_panelized']
    assert '%SR' not in pan.source_file
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).symmetric_difference(polygon_union(pan.solid_geometry)).area < 1e-8
    assert flash_count(again) == 2
    # A later export after moving the panel must use its new geometry.
    pan.offset((10, 20))
    pan.regenerate_source()
    same_copper(app, pan, tmp_path)

@pytest.mark.parametrize('spacing', [5.0, -1.0])
def test_panel_of_a_gerber_with_clear_objects_keeps_its_apertures(app, tmp_path, spacing):
    # copies apart: the records are written in the order of the file, copy after copy, with their apertures.
    # overlapping copies: a clear object of a copy erases another copy; the export adds that copper back as regions
    # at the end. In both cases each flash stays a flash.
    src = make_gerber(app, CLEAR_GBR, name='g')
    created, informs = run_plugin(app, src, spacing=spacing)
    pan = created['g_panelized']
    assert '%ADD' in pan.source_file and '%LPC*%' in pan.source_file
    again = same_copper(app, pan, tmp_path)
    assert flash_count(again) == flash_count(pan) == 6 * flash_count(src)

@pytest.mark.parametrize('spacing, used', [(5.0, True), (-1.0, False)])
def test_step_and_repeat_option_writes_one_copy_in_a_block(app, tmp_path, spacing, used):
    for path in (CLEAR_GBR, None):                    # native records with clear objects / block apertures (AM)
        src = make_gerber(app, path, name='g') if path else make_gerber(app, name='g')
        plain = run_plugin(app, src, spacing=spacing)[0]['g_panelized']
        pan = run_plugin(app, src, spacing=spacing, step_repeat=True)[0]['g_panelized']
        assert ('%SRX3Y2I' in pan.source_file and pan.source_file.count('%SR*%') == 1) is used
        if path:
            assert (len(pan.source_file) < len(plain.source_file) / 3) is used
        # the block apertures are defined before the Step and Repeat block, not in it
        assert pan.source_file.find('%ABD') < pan.source_file.find('%SRX') or '%SRX' not in pan.source_file or \
            '%ABD' not in pan.source_file
        same_copper(app, pan, tmp_path)
        assert_coords_close(pan.solid_geometry, plain.solid_geometry)       # the panel itself is the same

@pytest.mark.parametrize('answer, made', [('Yes', True), ('No', False)])
def test_panel_larger_than_the_free_memory_asks_the_user(app, monkeypatch, answer, made):
    import sys
    module = sys.modules[Panelize.__module__]
    shown = []

    class Box:
        def __init__(self, parent=None):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

        def setInformativeText(self, text):
            shown.append(text)

        def addButton(self, text, role):
            return text

        def clickedButton(self):
            return answer

    monkeypatch.setattr(module, 'FCMessageBox', Box)
    monkeypatch.setattr(module, 'free_memory', lambda: 1)
    created, informs = run_plugin(app, make_gerber(app, name='g'))
    assert len(shown) == 1 and '6' in shown[0]          # the question names the 2 x 3 copies
    assert bool(created) is made

    # the free memory is not known: no question, the panel is made
    monkeypatch.setattr(module, 'free_memory', lambda: None)
    created, informs = run_plugin(app, make_gerber(app, name='g'))
    assert len(shown) == 1 and created

def test_free_memory_of_linux_and_macos_text():
    from appCommon.memory import free_memory, parse_meminfo, parse_vm_stat
    meminfo = "MemTotal:       16303428 kB\nMemFree:          812340 kB\nMemAvailable:    9876543 kB\nBuffers: 1 kB\n"
    assert parse_meminfo(meminfo) == 9876543 * 1024
    assert parse_meminfo("MemTotal: 100 kB\nMemFree: 40 kB\n") == 40 * 1024      # kernel without MemAvailable
    assert parse_meminfo("nothing") is None
    vm_stat = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
               "Pages free:                               10000.\n"
               "Pages active:                            500000.\n"
               "Pages inactive:                           20000.\n"
               "Pages speculative:                         3000.\n"
               "Pages purgeable:                           4000.\n")
    assert parse_vm_stat(vm_stat) == 33000 * 16384      # purgeable pages are already active or inactive
    assert parse_vm_stat("nothing") is None
    assert free_memory() is None or free_memory() > 0      # the branch of this operating system

def test_worker_exception_clears_promise_and_reports_error(app):
    src = make_gerber(app, name='g')
    app.collection.promises = set()
    app.collection.promise.side_effect = app.collection.promises.add
    created, informs = run_plugin(app, src, fail_job=True)
    assert not created
    assert not app.collection.promises
