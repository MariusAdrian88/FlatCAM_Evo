"""Excellon scale / offset / mirror / skew / rotate / buffer: the vectorized path must equal per element shapely."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets  # noqa: E402
from shapely import Point, LineString, affinity  # noqa: E402

from defaults import AppDefaults  # noqa: E402
from appHandlers.appHistory import HistoryManager, apply_matrix_to_object, transform_metadata  # noqa: E402
from appHandlers.appIO import appIO  # noqa: E402
from appObjects.ExcellonObject import ExcellonObject  # noqa: E402
from tests.helpers_objects import (  # noqa: F401,E402
    AFFINE_TRANSFORMS, app, apply_to, assert_poly_equiv, assert_xy_close, drill_xy, excellon, export_excellon_text,
    GERBER_C_FLASH, GERBER_P_FLASH, _headless_qt, make_app, make_app_0b, make_excellon, make_excellon_0b,
    make_gerber_0b, make_same_diameter_excellon, polygon_union, pts, reparse_excellon, slot_coords, slot_flat,
    slot_xy, snapshot_excellon, TRANSFORM_NAMES,
)
import numpy as np
import pytest
from tclCommands.TclCommandBuffer import TclCommandBuffer

_QAPP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

STEPS = int(AppDefaults().defaults["excellon_circle_steps"])


def _make(app):
    obj = ExcellonObject('exc', app)
    obj.units = 'MM'
    obj.tools = {
        1: {'tooldia': 0.8, 'drills': [Point(1, 2), Point(3.5, -4), Point(10, 10)], 'slots': [], 'solid_geometry': [],
            'data': {'cutz': -1.7}},
        2: {'tooldia': 1.2, 'drills': [Point(0, 0)], 'slots': [(Point(5, 5), Point(9, 6)), (Point(-2, 3), Point(4, 3))],
            'solid_geometry': [], 'data': {'cutz': -2.0}},
        3: {'tooldia': 2.0, 'drills': [], 'solid_geometry': [], 'data': {}},
    }
    assert obj.create_geometry() is None
    return obj


def _reference(obj, op, tooldia_fn=lambda d: d):
    """What the old code produced: transform each point with shapely.affinity, then a fresh per element buffer."""
    out = {}
    for key, tool in obj.tools.items():
        r = tooldia_fn(tool['tooldia']) / 2.0
        drills = [op(p) for p in tool['drills']]
        slots = [(op(a), op(b)) for a, b in tool.get('slots', [])]
        geo = [p.buffer(r, STEPS // 4) for p in drills] + [LineString([a, b]).buffer(r, STEPS // 4) for a, b in slots]
        out[key] = (drills, slots, geo)
    return out


class ExcellonTransformsTest(unittest.TestCase):
    def setUp(self):
        patcher = patch('appObjects.AppObjectTemplate.ShapeCollection')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = make_app()
        self.app.options['excellon_circle_steps_mode'] = 'fixed'     # STEPS segments for every hole
        self.obj = _make(self.app)

    def check(self, ref):
        total = []
        for key, (drills, slots, geo) in ref.items():
            tool = self.obj.tools[key]
            self.assertEqual(len(tool['drills']), len(drills))
            for got, want in zip(tool['drills'], drills):
                self.assertTrue(got.equals_exact(want, 1e-9))
            self.assertEqual(len(tool.get('slots', [])), len(slots))
            for got, want in zip(tool.get('slots', []), slots):
                self.assertIs(type(got), tuple)
                self.assertTrue(got[0].equals_exact(want[0], 1e-9) and got[1].equals_exact(want[1], 1e-9))
            self.assertEqual(len(tool['solid_geometry']), len(geo))
            for got, want in zip(tool['solid_geometry'], geo):
                self.assertTrue(got.equals_exact(want, 1e-9))
            total.extend(geo)
        self.assertEqual(len(self.obj.solid_geometry), len(total))
        for got, want in zip(self.obj.solid_geometry, total):
            self.assertTrue(got.equals_exact(want, 1e-9))


    def test_rotate(self):
        ref = _reference(self.obj, lambda p: affinity.rotate(p, 30, origin=(5, 7)))
        self.obj.rotate(30, (5, 7))
        self.check(ref)


    def test_scale_moves_holes_keeps_diameters(self):
        ref = _reference(self.obj, lambda p: affinity.scale(p, 1.5, 0.5, origin=(1, 2)))
        self.obj.scale(1.5, 0.5, (1, 2))
        self.check(ref)
        self.assertEqual([t['tooldia'] for t in self.obj.tools.values()], [0.8, 1.2, 2.0])


    def test_skew(self):
        ref = _reference(self.obj, lambda p: affinity.skew(p, 10, 20, origin=(3, 4)))
        self.obj.skew(10, 20, (3, 4))
        self.check(ref)

    def test_mirror(self):
        for axis, (sx, sy) in (('X', (1, -1)), ('Y', (-1, 1))):
            obj = _make(self.app)
            ref = _reference(obj, lambda p: affinity.scale(p, sx, sy, origin=(2, 2)))
            self.obj = obj
            obj.mirror(axis, (2, 2))
            self.check(ref)

    def test_offset(self):
        ref = _reference(self.obj, lambda p: affinity.translate(p, 1.5, -2))
        self.obj.offset((1.5, -2))
        self.check(ref)


    def test_rigid_matrix_replay_keeps_aggregate_and_tools_in_sync(self):
        before = list(self.obj.solid_geometry)
        matrix = (0, -1, 1, 0, 4, 7)
        apply_matrix_to_object(self.obj, matrix)
        for got, original in zip(self.obj.solid_geometry, before):
            self.assertLess(got.hausdorff_distance(affinity.affine_transform(original, matrix)), 1e-9)
        total = [g for tool in self.obj.tools.values() for g in tool['solid_geometry']]
        for aggregate, pertool in zip(self.obj.solid_geometry, total):
            self.assertTrue(aggregate.equals_exact(pertool, 1e-9))

    def test_buffer_distance_and_factor(self):
        ref = _reference(self.obj, lambda p: p, lambda d: d + 0.2)
        self.obj.buffer(0.1, 'round', False)
        self.check(ref)
        self.assertAlmostEqual(self.obj.tools[1]['tooldia'], 1.0)

        obj = _make(self.app)
        self.obj = obj
        ref = _reference(obj, lambda p: p, lambda d: d * 1.5)
        obj.buffer(1.5, 'round', True)
        self.check(ref)


if __name__ == '__main__':
    unittest.main()


class TestExcellonParse:
    def test_coordinates(self, excellon):
        tools = excellon.tools
        assert pts(tools[1]['drills']) == [(5, 5), (10, 5), (15, 5)]
        assert pts(tools[2]['drills']) == [(5, 12), (10, 12)]
        assert slot_coords(tools[2]['slots']) == [((20, 12), (28, 12))]
        assert pts(tools[3]['drills']) == [(25, 22)]
        assert slot_coords(tools[3]['slots']) == [((5, 20), (5, 26)), ((12, 20), (18, 20))]


@pytest.mark.parametrize('name', TRANSFORM_NAMES)
class TestExcellonTransforms:
    def test_drill_and_slot_coordinates(self, app, name):
        obj = make_excellon(app)
        before = snapshot_excellon(obj)
        AFFINE_TRANSFORMS[name][0](obj)
        fn = AFFINE_TRANSFORMS[name][1]
        assert sorted(obj.tools) == sorted(before)
        for t, tool in before.items():
            now = obj.tools[t]
            assert len(now['drills']) == len(tool['drills'])
            assert len(now['slots']) == len(tool['slots'])
            for p_before, p_now in zip(tool['drills'], now['drills']):
                expected = fn(p_before)
                assert (p_now.x, p_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)
            for s_before, s_now in zip(tool['slots'], now['slots']):
                for end_before, end_now in zip(s_before, s_now):
                    expected = fn(end_before)
                    assert (end_now.x, end_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)


def test_excellon_skew_rebuilds_round_holes_at_skewed_centres(app):
    """Characterization: skew moves the hole centres, it does not shear the hole shape."""
    obj = make_excellon(app)
    before = polygon_union(obj.solid_geometry)
    AFFINE_TRANSFORMS['skew'][0](obj)
    sheared = polygon_union(apply_to(AFFINE_TRANSFORMS['skew'][1], before))
    # the rebuilt geometry keeps round holes of the same size: its area is unchanged, the sheared copy differs
    assert abs(polygon_union(obj.solid_geometry).area - before.area) < 1e-6 * before.area + 0.5
    assert abs(sheared.area - before.area) > 0.1   # the sheared copy has another area, the rebuilt holes do not


class TestExcellonFileRoundTrip:
    def test_excellon_round_trip(self, app, tmp_path):
        exc = make_excellon(app)
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        assert again.units == 'MM'
        assert sorted(again.tools) == [1, 2, 3]
        for t, tool in exc.tools.items():
            assert again.tools[t]['tooldia'] == pytest.approx(tool['tooldia'], abs=1e-9)
            assert pts(again.tools[t]['drills']) == pytest.approx(np.array(pts(tool['drills'])), abs=1e-9)
            assert slot_flat(again.tools[t]['slots']) == pytest.approx(np.array(slot_flat(tool['slots'])), abs=1e-9)
        assert_poly_equiv(again.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-6, msg='excellon file')


    def test_excellon_equal_diameter_tools_stay_separate_in_the_file(self, app, tmp_path):
        exc = make_same_diameter_excellon(app)
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        assert sorted(again.tools) == [1, 2]
        assert [again.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 1.0]
        assert pts(again.tools[1]['drills']) == [(5, 5), (9, 9)]
        assert pts(again.tools[2]['drills']) == [(15, 5)]
        assert slot_coords(again.tools[2]['slots']) == [((20, 5), (26, 5))]


class TestExcellonScaleUnits_0B3:
    # A drill is a physical tool: scale() moves the holes and keeps the diameters (the behavior before the
    # unification). A user corrects a file read with a wrong number format with a scale of the positions.
    @pytest.mark.parametrize('factor', [2.0, -2.0])
    def test_uniform_scale_moves_the_holes_and_keeps_tooldia(self, factor):
        ex = make_excellon_0b(default_data={})
        before = [(d.x, d.y) for d in ex.tools[1]['drills']]
        ex.scale(factor, factor, point=(0, 0))
        assert ex.tools[1]['tooldia'] == pytest.approx(1.0)
        assert [(d.x, d.y) for d in ex.tools[1]['drills']] == pytest.approx(
            np.array([(x * factor, y * factor) for x, y in before]))
        minx, _, maxx, _ = ex.tools[1]['solid_geometry'][0].bounds
        assert maxx - minx == pytest.approx(1.0, abs=1e-3)

    def test_unit_conversion_then_scale_keeps_the_converted_diameters(self):
        # an inch file (M72) read with the wrong format, converted to MM by the app, then scaled by 1/10
        ex = make_excellon_0b(default_data={})
        ex.units = 'IN'
        ex.convert_units('MM')
        assert ex.tools[1]['tooldia'] == pytest.approx(25.4)
        ex.scale(0.1, 0.1, point=(0, 0))
        assert ex.tools[1]['tooldia'] == pytest.approx(25.4)

    def test_gerber_scale_keeps_p_aperture_nvertices(self):
        g = make_gerber_0b(GERBER_P_FLASH)
        assert g.tools[11]['nVertices'] == 6
        g.scale(2.0)
        assert g.tools[11]['nVertices'] == 6
        assert g.tools[11]['diam'] == pytest.approx(2.0)


def _tcl_buffer(ex, *argv):
    app = make_app_0b()
    app.collection.get_by_name.return_value = ex
    cmd = TclCommandBuffer(app)
    args, unnamed = cmd.check_args(('ex',) + argv)
    cmd.execute(args, unnamed)


class TestTransformBugs_0B4:
    def test_excellon_rotate_without_point_uses_bounds_center(self):
        ex = make_excellon_0b(default_data={})
        minx, miny, maxx, maxy = ex.bounds()
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        old_drills = drill_xy(ex)
        old_slots = slot_xy(ex)

        def rot90(p):
            # 90 degrees counter-clockwise about (cx, cy)
            return cx - (p[1] - cy), cy + (p[0] - cx)

        ex.rotate(90)
        assert_xy_close(drill_xy(ex), [rot90(p) for p in old_drills], tol=1e-6)
        assert_xy_close([s[0] for s in slot_xy(ex)], [rot90(s[0]) for s in old_slots], tol=1e-6)
        assert_xy_close([s[1] for s in slot_xy(ex)], [rot90(s[1]) for s in old_slots], tol=1e-6)

    def test_gerber_buffer_factor_false_is_distance_mode(self):
        g = make_gerber_0b(GERBER_C_FLASH)
        g.buffer(0.1, 'round', False)
        assert g.tools[10]['size'] == pytest.approx(1.2)

    def test_gerber_distance_buffer_keeps_p_aperture_nvertices(self):
        g = make_gerber_0b(GERBER_P_FLASH)
        g.buffer(0.1, 'round', None)
        assert g.tools[11]['nVertices'] == 6

    def test_excellon_create_geometry_keeps_existing_tool_data(self):
        ex = make_excellon_0b(default_data={'from_default': 1})
        ex.tools[1]['data'] = {'kept': 5}
        ex.create_geometry()
        assert ex.tools[1]['data'] == {'kept': 5}


    def test_tcl_buffer_factor_true_multiplies_diameter(self):
        ex = make_excellon_0b(default_data={}, tooldia=1.0)
        _tcl_buffer(ex, '-dist', '2', '-factor', 'True')
        assert ex.tools[1]['tooldia'] == pytest.approx(2.0)


class TestGuardsAlreadyPassing_0B:
    def test_excellon_scale_negative_x_uses_signed_factor(self):
        ex = make_excellon_0b(default_data={}, drills=((2.0, 1.0), (4.0, 3.0)), slots=(((2.0, 5.0), (6.0, 5.0)),))
        ex.scale(-1, 1, point=(1, 1))
        assert_xy_close(drill_xy(ex), [(0.0, 1.0), (-2.0, 3.0)])
        assert_xy_close([s[0] for s in slot_xy(ex)], [(0.0, 5.0)])
        assert_xy_close([s[1] for s in slot_xy(ex)], [(-4.0, 5.0)])


    def test_excellon_convert_units_round_trip_scales_once(self):
        ex = make_excellon_0b(default_data={}, units='IN', tooldia=1.0, drills=((1.0, 2.0),),
                           slots=(((0.0, 0.0), (1.0, 0.0)),))
        ex.convert_units('MM')
        assert ex.tools[1]['tooldia'] == pytest.approx(25.4, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(25.4, 50.8)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(25.4, 0.0)], tol=1e-9)
        ex.convert_units('IN')
        assert ex.tools[1]['tooldia'] == pytest.approx(1.0, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(1.0, 2.0)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(1.0, 0.0)], tol=1e-9)


    def test_gerber_buffer_factor_true_is_multiplicative_mode(self):
        g = make_gerber_0b(GERBER_C_FLASH)
        g.buffer(2, 'round', True)
        assert g.tools[10]['size'] == pytest.approx(2.0)

class _RecordingShapes:
    """The ShapeGroup API used by ExcellonObject.plot(), keeping the shapes it was given."""

    def __init__(self):
        self.data = {}
        self._indexes = []
        self._collection = self
        self.visible = True

    def add(self, shape, **kwargs):
        key = len(self._indexes)
        self._indexes.append(key)
        self.data[key] = shape
        return key

    def add_batch(self, shapes_data, **kwargs):
        return [self.add(item['shape']) for item in shapes_data]

    def transform(self, matrix, update=True):
        for key in self._indexes:
            self.data[key] = affinity.affine_transform(self.data[key], matrix)
        return True

    def redraw(self, *args, **kwargs):
        pass

    def clear(self, update=False):
        self.data.clear()
        del self._indexes[:]




def test_matrix_undo_redo_keeps_excellon_source_and_plot_in_sync(app, tmp_path):
    obj = make_same_diameter_excellon(app)
    app.f_handlers = appIO(app)
    obj.shapes = _RecordingShapes()
    obj.plot()

    before_tools = snapshot_excellon(obj)
    assert obj.regenerate_source() is True

    matrix = (2, 0, 0, 1, 0, 0)
    before_meta = transform_metadata(obj)
    obj.scale(2, 1, point=(0, 0))
    obj.regenerate_source()
    scaled_tools = snapshot_excellon(obj)

    history = HistoryManager(app, SimpleNamespace(get_list=lambda: [obj]))
    app.history = history
    history.record_matrix('Scale', [(obj, matrix, before_meta)])

    assert history.undo() is True
    undone = reparse_excellon(app, obj.source_file, tmp_path, 'undone')
    assert pts(undone.tools[1]['drills']) == pytest.approx(np.array(pts(before_tools[1]['drills'])), abs=1e-9)
    assert slot_coords(undone.tools[2]['slots']) == pytest.approx(
        np.array(slot_coords(before_tools[2]['slots'])), abs=1e-9)
    assert obj.shapes.data[0].bounds == pytest.approx((4.5, 4.5, 5.5, 5.5), abs=1e-6)

    assert history.redo() is True
    redone = reparse_excellon(app, obj.source_file, tmp_path, 'redone')
    assert pts(redone.tools[1]['drills']) == pytest.approx(np.array(pts(scaled_tools[1]['drills'])), abs=1e-9)
    assert slot_coords(redone.tools[2]['slots']) == pytest.approx(
        np.array(slot_coords(scaled_tools[2]['slots'])), abs=1e-9)
    assert obj.shapes.data[0].bounds == pytest.approx((9.5, 4.5, 10.5, 5.5), abs=1e-6)


def _buffer_state(obj):
    return ({key: tool['tooldia'] for key, tool in obj.tools.items()},
            list(obj.solid_geometry),
            {key: list(tool['solid_geometry']) for key, tool in obj.tools.items()},
            {key: dict(tool['data']) for key, tool in obj.tools.items()})


def _assert_buffer_state(obj, state):
    diameters, geometry, tool_geometry, data = state
    assert {key: tool['tooldia'] for key, tool in obj.tools.items()} == diameters
    assert obj.solid_geometry == geometry
    for key, tool in obj.tools.items():
        assert tool['solid_geometry'] == tool_geometry[key]
        assert tool['data'] == data[key]


def test_buffer_create_geometry_exception_restores_the_exact_state(app, monkeypatch):
    obj = make_same_diameter_excellon(app)
    before = _buffer_state(obj)

    def raise_after_mutation():
        obj.solid_geometry = []
        for tool in obj.tools.values():
            tool['solid_geometry'] = []
        raise RuntimeError('create_geometry failed')

    monkeypatch.setattr(obj, 'create_geometry', raise_after_mutation)
    assert obj.buffer(0.25, 'round', False) == 'fail'
    _assert_buffer_state(obj, before)


def test_buffer_validates_every_tool_before_changing_any(app):
    obj = _make(app)
    before = _buffer_state(obj)
    # tool 1 (0.8 mm) would become negative, tools 2 and 3 would stay valid
    assert obj.buffer(-0.5, 'round', None) == 'fail'
    _assert_buffer_state(obj, before)


@pytest.mark.parametrize('distance,factor', [
    ('x', None), (float('nan'), True),
])
def test_buffer_rejects_a_malformed_distance(app, distance, factor):
    obj = make_same_diameter_excellon(app)
    before = _buffer_state(obj)
    assert obj.buffer(distance, 'round', factor) == 'fail'
    _assert_buffer_state(obj, before)
