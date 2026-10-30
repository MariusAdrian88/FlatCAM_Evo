"""Golden tests of appParsers/panelize_core.py (no Qt objects, no app: plain dicts and shapely geometry; the fixture
files are only parsed to get realistic input)."""

from copy import deepcopy

import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box

from appParsers import panelize_core as pc
from camlib import grace

from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_coords_close, assert_poly_equiv, _headless_qt, make_excellon, make_gerber, NATIVE_GBR, parts,
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

    def test_constrain_cannot_fit_one_board(self):
        assert pc.constrain_grid((0, 0, 10, 10), 3, 3, 5, 5, 9, 9) == (0, 0, True)

    def test_merge_lines_honors_abort_without_snapping(self):
        tools = {1: {'solid_geometry': [LineString([(0, 0), (1, 0)])]}}
        with pytest.raises(grace):
            pc.merge_tool_lines(tools, abort=lambda: True)


class TestTranslate:
    def test_flat_result_for_nested_lists(self):
        a, b, c = Point(0, 0), Point(1, 1), Point(2, 2)
        out = pc.translate_flat([a, [b, [c]], None], 10, 20)
        assert isinstance(out, list) and len(out) == 3
        assert [(p.x, p.y) for p in out] == [(10, 20), (11, 21), (12, 22)]

# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('path', [None, NATIVE_GBR], ids=['mixed', 'native'])
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
    assert len(solid) == 3
    for off, geo in zip(offs, solid):
        assert_poly_equiv(geo, affinity.translate(composed, *off))
        # the ring (dark minus clear) has a hole, the inner dot fills it again: area 100 - 36 + 4
        assert polygon_union(geo).area == pytest.approx(68.0)
    # the clear record stays a clear record, with no 'solid' key
    assert [list(r) for r in new_tools[11]['geometry']] == [['clear', 'follow']] * 3
    assert [list(r) for r in new_tools[10]['geometry']] == [['solid', 'follow']] * 6
    # record order: copy 1 of both dark records, then copy 2, ...
    assert new_tools[10]['geometry'][1]['solid'].bounds == (4, 4, 6, 6)
    assert new_tools[10]['geometry'][2]['solid'].bounds == (20, 0, 30, 10)


def test_copy_gerber_attributes(app):
    src = make_gerber(app, name='g')
    src.export_as_regions = True
    dst = type(src)('dst', app)
    pc.copy_gerber_attributes(dst, src)
    assert dst.export_as_regions is True
    assert set(dst.aperture_macros) == set(src.aperture_macros) and dst.aperture_macros
    assert all(dst.aperture_macros[k] is not src.aperture_macros[k] for k in src.aperture_macros)
    src.export_as_regions = False
    pc.copy_gerber_attributes(dst, src)
    assert dst.export_as_regions is False


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

    def test_each_copy_of_a_hole_keeps_its_object_attribute_id(self):
        tools = {1: {'tooldia': 1.0, 'drills': [Point(0, 0), Point(1, 0)], 'slots': [(Point(0, 1), Point(1, 1))],
                     'drills_to': [2, 0], 'slots_to': [3]},
                 2: {'tooldia': 2.0, 'drills': [Point(5, 5)], 'slots': []}}
        new = pc.panelize_excellon(tools, [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)])
        assert new[1]['drills_to'] == [2, 0] * 3 and new[1]['slots_to'] == [3] * 3
        assert len(new[1]['drills']) == 6
        # a tool without ids gets no keys; the source is not changed
        assert 'drills_to' not in new[2] and 'slots_to' not in new[2]
        assert tools[1]['drills_to'] == [2, 0]

    def test_tool_data_kept_and_independent(self, app):
        src, offs = self.build(app)
        src.tools[1]['data'] = {'cutz': -1.7, 'tag': 'first'}
        tools = pc.panelize_excellon(src.tools, offs)
        assert tools[1]['tooldia'] == src.tools[1]['tooldia']
        assert tools[1]['data'] == {'cutz': -1.7, 'tag': 'first'} and tools[1]['data'] is not src.tools[1]['data']
        tools[1]['data']['cutz'] = 5
        assert src.tools[1]['data']['cutz'] == -1.7

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

# ---------------------------------------------------------------------------------------------------------------
# core == Tcl command
# ---------------------------------------------------------------------------------------------------------------

from test_panelize_tcl_characterization import run_panelize  # noqa: E402


@pytest.mark.parametrize('path', [None, NATIVE_GBR], ids=['mixed', 'native'])
def test_gerber_core_equals_tcl(app, path):
    src = make_gerber(app, name='g') if path is None else make_gerber(app, path, name='g')
    _, created = run_panelize(app, src, 'g', '-rows', str(ROWS), '-columns', str(COLS),
                              '-spacing_columns', '5', '-spacing_rows', '5', '-outname', 'p')
    pan = created['p']
    offs = pc.grid_offsets(src.bounds(), ROWS, COLS, SPACING, SPACING)
    tools, solid = pc.panelize_gerber(src.tools, src.solid_geometry, offs)
    assert list(pan.tools) == list(tools)
    for apid, tool in tools.items():
        assert len(pan.tools[apid]['geometry']) == len(tool['geometry'])
        for rec_t, rec_c in zip(pan.tools[apid]['geometry'], tool['geometry']):
            assert set(rec_t) == set(rec_c)
            for key in rec_c:
                assert_coords_close(rec_t[key], rec_c[key], msg='%s %s' % (apid, key))
        assert {k: v for k, v in pan.tools[apid].items() if k not in ('geometry', 'macro')} == \
            {k: v for k, v in tool.items() if k not in ('geometry', 'macro')}
    assert_coords_close(pan.solid_geometry, solid)


def test_excellon_core_equals_tcl(app):
    src = make_excellon(app, name='e')
    _, created = run_panelize(app, src, 'e', '-rows', str(ROWS), '-columns', str(COLS),
                              '-spacing_columns', '5', '-spacing_rows', '5', '-outname', 'q')
    pan = created['q']
    offs = pc.grid_offsets(src.bounds(), ROWS, COLS, SPACING, SPACING)
    tools = pc.panelize_excellon(src.tools, offs)
    assert list(pan.tools) == list(tools)
    for tid, tool in tools.items():
        assert pan.tools[tid]['tooldia'] == tool['tooldia']
        assert [(p.x, p.y) for p in pan.tools[tid]['drills']] == pytest.approx(np.array([(p.x, p.y) for p in tool['drills']]))
        assert [(a.x, a.y, b.x, b.y) for a, b in pan.tools[tid]['slots']] == \
            pytest.approx(np.array([(a.x, a.y, b.x, b.y) for a, b in tool['slots']]))


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

