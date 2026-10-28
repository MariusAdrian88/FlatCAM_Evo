"""Stage 4 merge contract: ExcellonObject.merge fuses tools by diameter only if their data is equal.

Rule under test: with ``fuse_tools=True`` two tools fuse when their rounded diameters match AND their tool
parameters are equal: the ``data`` keys that start with 'tools_'. The tool data is a copy of the object options, so
it also holds per-object keys (name, bounds, zeros, plot ...) that differ between source objects; they are not
compared. Tools of the same diameter with different tool parameters stay separate, each with its own data, drills and
slots. Nothing is lost silently.

Headless: real ExcellonObject instances, ShapeCollection patched out (fixtures come from the characterization
tests).
"""

import copy
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtWidgets
from shapely.geometry import Point

from appObjects.ExcellonObject import ExcellonObject

from tests.helpers_objects import (  # noqa: F401,E402
    app, excellon, _headless_qt, make_excellon, snapshot_excellon,
)

DATA_A = {'tools_drill_cutz': -1.7, 'tools_drill_multidepth': True, 'tools_drill_depthperpass': 0.4,
          'tools_drill_ppname_e': 'first'}
DATA_B = {'tools_drill_cutz': -2.4, 'tools_drill_multidepth': False, 'tools_drill_depthperpass': 0.8,
          'tools_drill_ppname_e': 'second'}


def object_keys(name):
    """Per-object option keys that the tool data holds; they differ from 1 source object to the next."""
    n = float(len(name)) + sum(map(ord, name)) / 1000.0
    return {'name': name, 'xmin': -n, 'ymin': -2 * n, 'xmax': n, 'ymax': 2 * n,
            'zeros': 'L' if len(name) % 2 else 'T', 'plot': len(name) % 2 == 0, 'units': 'MM'}


def make_exc(app, name, specs):
    """specs: list of (dia, drill xy list, slot xy-pair list, data dict). Tool ids are 1..n."""
    obj = ExcellonObject(name, app)
    obj.units = 'MM'
    obj.tools = {}
    for idx, (dia, drills, slots, data) in enumerate(specs, start=1):
        obj.tools[idx] = {
            'tooldia': dia,
            'drills': [Point(p) for p in drills],
            'slots': [(Point(a), Point(b)) for a, b in slots],
            'solid_geometry': [],
            # as in practice, the data is a copy of the object options: it holds per-object keys too
            'data': dict(data, **object_keys(name)),
        }
    assert obj.create_geometry() is None
    return obj


def merge(app, objs, fuse=True):
    final = ExcellonObject('combo', app)
    ExcellonObject.merge(objs, final, decimals=4, fuse_tools=fuse, log=Mock())
    return final


def pts(items):
    return [(p.x, p.y) for p in items]


def slots_xy(slots):
    return [((s[0].x, s[0].y), (s[1].x, s[1].y)) for s in slots]


def strip_name(data):
    """The tool parameters of a tool data (the 'tools_' keys)."""
    return {k: v for k, v in data.items() if k.startswith('tools_')}


class TestFuseEqualData:
    def test_per_object_keys_do_not_block_the_fuse(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        bb = make_exc(app, 'bb', [(1.0, [(5, 0)], [], DATA_A)])
        # name, bounds, zeros and plot differ between the 2 objects; the tool parameters are equal
        assert a.tools[1]['data'] != bb.tools[1]['data']
        final = merge(app, [a, bb])
        assert list(final.tools) == [1]
        assert pts(final.tools[1]['drills']) == [(0, 0), (5, 0)]

    def test_same_diameter_equal_data_fuses_into_one_tool(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [((0, 5), (3, 5))], DATA_A),
                                (0.8, [(9, 9)], [], DATA_B)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        big = [t for t in final.tools.values() if t['tooldia'] == 1.0]
        assert len(big) == 1
        assert pts(big[0]['drills']) == [(0, 0), (1, 0), (10, 0)]
        assert slots_xy(big[0]['slots']) == [((0, 5), (3, 5)), ((10, 5), (13, 5))]
        assert strip_name(big[0]['data']) == DATA_A
        assert len(big[0]['solid_geometry']) == 5
        small = [t for t in final.tools.values() if t['tooldia'] == 0.8]
        assert len(small) == 1 and strip_name(small[0]['data']) == DATA_B


    def test_diameters_equal_after_rounding_fuse(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.00001, [(5, 0)], [], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 1
        assert final.tools[1]['tooldia'] == 1.0
        assert pts(final.tools[1]['drills']) == [(0, 0), (5, 0)]


class TestKeepDifferentData:
    def test_same_diameter_different_data_stays_two_tools(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_B)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        first, second = final.tools[1], final.tools[2]
        assert strip_name(first['data']) == DATA_A and strip_name(second['data']) == DATA_B
        assert pts(first['drills']) == [(0, 0), (1, 0)] and first['slots'] == []
        assert pts(second['drills']) == [(10, 0)]
        assert slots_xy(second['slots']) == [((10, 5), (13, 5))]
        assert len(final.solid_geometry) == 4


    def test_mixed_groups_fuse_per_data(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A), (1.0, [(1, 0)], [], DATA_B)])
        b = make_exc(app, 'b', [(1.0, [(2, 0)], [], DATA_A), (1.0, [(3, 0)], [], DATA_B)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        by_tag = {t['data']['tools_drill_ppname_e']: t for t in final.tools.values()}
        assert pts(by_tag['first']['drills']) == [(0, 0), (2, 0)]
        assert pts(by_tag['second']['drills']) == [(1, 0), (3, 0)]

    def test_tool_ids_are_contiguous_in_order_of_first_appearance(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(1, 0)], [], DATA_A)])
        c = make_exc(app, 'c', [(0.8, [(2, 0)], [], DATA_A)])
        final = merge(app, [a, b, c])
        assert sorted(final.tools) == [1, 2]
        assert [final.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 0.8]
        assert pts(final.tools[1]['drills']) == [(0, 0), (1, 0)]


class TestBuildUiKeepsToolData:
    def test_build_ui_keeps_per_tool_values_and_fills_missing_keys(self, app):
        exc = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A),
                                  (0.8, [(9, 9)], [], {k: v for k, v in DATA_B.items()
                                                       if k != 'tools_drill_depthperpass'})])
        exc.obj_options = {'tools_drill_cutz': -9.9, 'tools_drill_multidepth': False,
                           'tools_drill_depthperpass': 0.2, 'tools_drill_ppname_e': 'object'}
        class ToolsTable(QtWidgets.QTableWidget):
            # the 2 methods the real table widget adds in ExcellonObjectUI
            def getHeight(self):
                return 100

            def setupContextMenu(self):
                pass

        exc.ui = MagicMock()
        exc.ui.tools_table = ToolsTable(0, 6)
        exc.on_row_selection_change = Mock()    # the selection of the table row is not under test
        exc.build_ui()
        assert strip_name(exc.tools[1]['data']) == DATA_A
        assert exc.tools[2]['data']['tools_drill_cutz'] == -2.4
        assert exc.tools[2]['data']['tools_drill_depthperpass'] == 0.2


class TestIndependenceAndTypes:
    @pytest.mark.parametrize('fuse', [True, False])
    def test_result_does_not_share_data_with_the_sources(self, app, fuse):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [((0, 1), (1, 1))], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])
        snap_a, snap_b = copy.deepcopy(a.tools), copy.deepcopy(b.tools)
        final = merge(app, [a, b], fuse=fuse)
        before = copy.deepcopy(final.tools)

        # change the sources after the merge: the result stays
        a.tools[1]['drills'].append(Point(99, 99))
        a.tools[1]['slots'].append((Point(8, 8), Point(9, 9)))
        a.tools[1]['data']['tools_drill_cutz'] = 5.0
        b.tools[1]['drills'][0] = Point(77, 77)
        b.tools[1]['data']['tools_drill_ppname_e'] = 'changed'
        assert pts(sum((t['drills'] for t in final.tools.values()), [])) == \
            pts(sum((t['drills'] for t in before.values()), []))
        assert all(t['data'].get('tools_drill_cutz') == -1.7 and t['data'].get('tools_drill_ppname_e') == 'first' for t in final.tools.values())

        # change the result: the sources stay (compare against the snapshot taken before the merge)
        a2 = make_exc(app, 'a', [(1.0, [(0, 0)], [((0, 1), (1, 1))], DATA_A)])
        final2 = merge(app, [a2, make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])], fuse=fuse)
        final2.tools[1]['drills'].append(Point(1, 1))
        final2.tools[1]['data']['tools_drill_cutz'] = 9.9
        assert pts(a2.tools[1]['drills']) == [(0, 0)]
        assert a2.tools[1]['data']['tools_drill_cutz'] == -1.7
        assert pts(snap_a[1]['drills']) == [(0, 0)] and pts(snap_b[1]['drills']) == [(5, 0)]

    @pytest.mark.parametrize('fuse', [True, False])
    def test_slots_stay_tuples_of_two_points(self, app, fuse):
        a = make_exc(app, 'a', [(1.0, [], [((0, 1), (1, 1))], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [], [((5, 1), (6, 1))], DATA_A)])
        final = merge(app, [a, b], fuse=fuse)
        slots = [s for t in final.tools.values() for s in t['slots']]
        assert len(slots) == 2
        for slot in slots:
            assert type(slot) is tuple and len(slot) == 2
            assert all(isinstance(p, Point) for p in slot)



class TestExcellonMerge:
    def test_merge_without_fusing_keeps_every_tool_and_is_independent(self, app, excellon):
        other = make_excellon(app, name='exc_b')
        other.offset((100.0, 0.0))
        src_a, src_b = snapshot_excellon(excellon), snapshot_excellon(other)
        final = ExcellonObject('combo', app)
        ExcellonObject.merge([excellon, other], final, decimals=4, fuse_tools=False, log=Mock())
        assert sorted(final.tools) == [1, 2, 3, 4, 5, 6]
        assert sum(len(t['drills']) for t in final.tools.values()) == 12
        assert sum(len(t['slots']) for t in final.tools.values()) == 6
        assert len(final.solid_geometry) == 18
        assert pts(final.tools[4]['drills']) == [(105, 5), (110, 5), (115, 5)]
        # sources unchanged
        assert pts(excellon.tools[1]['drills']) == pts(src_a[1]['drills'])
        assert pts(other.tools[1]['drills']) == pts(src_b[1]['drills'])
        # the result owns its data
        final.tools[1]['drills'].append(Point(1, 1))
        final.tools[1]['tooldia'] = 7.0
        assert len(excellon.tools[1]['drills']) == 3 and excellon.tools[1]['tooldia'] == 0.8

    def test_merge_fusing_tools_of_the_same_diameter(self, app, excellon):
        other = make_excellon(app, name='exc_b')
        other.offset((100.0, 0.0))
        final = ExcellonObject('combo', app)
        ExcellonObject.merge([excellon, other], final, decimals=4, fuse_tools=True, log=Mock())
        assert sorted(round(t['tooldia'], 4) for t in final.tools.values()) == [0.8, 1.0, 1.5]
        assert sum(len(t['drills']) for t in final.tools.values()) == 12
        assert sum(len(t['slots']) for t in final.tools.values()) == 6
        assert len(excellon.tools[1]['drills']) == 3
        assert len(other.tools[1]['drills']) == 3


def join(app, objs, inform=None):
    final = ExcellonObject('combo', app)
    ExcellonObject.merge(objs, final, decimals=4, fuse_tools=True, log=Mock(), inform=inform)
    return final


def placed(final):
    """(tool diameter, x, y) of every drill of the joined object, sorted."""
    return sorted((round(t['tooldia'], 4), x, y) for t in final.tools.values() for x, y in pts(t['drills']))


class TestJoinDuplicateCheck:
    def test_same_drill_from_two_sources_is_kept_once(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (5, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(0, 0), (10, 0)], [], DATA_A)])
        assert placed(join(app, [a, b])) == [(1.0, 0, 0), (1.0, 5, 0), (1.0, 10, 0)]

    def test_same_center_other_diameter_keeps_both_and_informs(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(0.8, [(0, 0)], [], DATA_B)])
        inform = Mock()
        assert placed(join(app, [a, b], inform=inform)) == [(0.8, 0, 0), (1.0, 0, 0)]
        assert inform.called

    def test_tangent_drills_are_silent_and_overlapping_drills_inform(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        tangent = make_exc(app, 'b', [(1.0, [(1.0, 0)], [], DATA_A)])
        inform = Mock()
        assert placed(join(app, [a, tangent], inform=inform)) == [(1.0, 0, 0), (1.0, 1.0, 0)]
        assert not inform.called

        overlap = make_exc(app, 'c', [(1.0, [(0.9, 0)], [], DATA_A)])
        inform = Mock()
        assert placed(join(app, [a, overlap], inform=inform)) == [(1.0, 0, 0), (1.0, 0.9, 0)]
        assert inform.called

    def test_duplicates_inside_one_source_are_kept(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])
        assert placed(join(app, [a, b])) == [(1.0, 0, 0), (1.0, 0, 0), (1.0, 5, 0)]
