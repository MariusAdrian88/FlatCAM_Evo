"""Golden tests of appParsers/panelize_core.py (no Qt objects, no app: plain dicts and shapely geometry; the fixture
files are only parsed to get realistic input)."""

from copy import deepcopy
from random import Random
from unittest.mock import Mock

import numpy as np
import pytest
import shapely
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

from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_coords_close, assert_poly_equiv, _headless_qt, make_excellon, make_gerber, parts,
    polygon_union,
)

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


# ---------------------------------------------------------------------------------------------------------------
# grid
# ---------------------------------------------------------------------------------------------------------------

class TestGrid:
    def test_grid_offsets_2x3(self):
        offs = pc.grid_offsets((1.0, 2.0, 11.0, 5.0), 2, 3, 5.0, 5.0)
        assert offs == pytest.approx(np.array([(0, 0), (15, 0), (30, 0), (0, 8), (15, 8), (30, 8)]))

    def test_constrain_reduces(self):
        # width of n columns: 10n + 5(n-1): 3 -> 40, 2 -> 25; height of n rows: 2 -> 25, 3 -> 40
        assert pc.constrain_grid((0, 0, 10, 10), 3, 3, 5, 5, 30, 100) == (3, 2, True)
        assert pc.constrain_grid((0, 0, 10, 10), 3, 3, 5, 5, 100, 30) == (2, 3, True)
        assert pc.constrain_grid((0, 0, 10, 10), 4, 4, 5, 5, 25, 25) == (2, 2, True)
        assert pc.constrain_grid((0, 0, 10, 10), 3, 3, 5, 5, 9, 9) == (0, 0, True)

# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# Excellon
# ---------------------------------------------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# abort / progress
# ---------------------------------------------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------------------------------------------
# gerber_tools_from_geometry
# ---------------------------------------------------------------------------------------------------------------

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



# ---------------------------------------------------------------------------------------------------------------
# snapping of the panel paths (merge_tool_lines with snap_overlaps)
# ---------------------------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------------------------
# the Tcl command panelize (the headless driver is shared with test_panelize_plugin)
# ---------------------------------------------------------------------------------------------------------------

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
