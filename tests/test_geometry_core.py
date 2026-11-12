


# ======================================================================
# merged from tests/test_affine_geometry.py
# ======================================================================
import os
import sys
import time
import unittest

import pytest
import shapely.affinity as affinity
from shapely.geometry import (Point, LineString, LinearRing, Polygon, MultiPolygon, MultiLineString,
                              GeometryCollection, MultiPoint)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camlib  # noqa: E402
from camlib import (affine_geometry, affine_geometry_many, affine_rotate_matrix, affine_scale_matrix,  # noqa: E402
                    affine_skew_matrix, affine_mirror_matrix, affine_translate_matrix, map_geometry)

ORIGIN = (3.5, -2.25)

CASES = [
    ('rotate', lambda g: affinity.rotate(g, 37.0, origin=ORIGIN), affine_rotate_matrix(37.0, ORIGIN)),
    ('rotate90', lambda g: affinity.rotate(g, 90.0, origin=ORIGIN), affine_rotate_matrix(90.0, ORIGIN)),
    ('scale', lambda g: affinity.scale(g, 1.7, 0.4, origin=ORIGIN), affine_scale_matrix(1.7, 0.4, ORIGIN)),
    ('skew', lambda g: affinity.skew(g, 12.0, -7.0, origin=ORIGIN), affine_skew_matrix(12.0, -7.0, ORIGIN)),
    ('mirror_x', lambda g: affinity.scale(g, 1.0, -1.0, origin=ORIGIN), affine_mirror_matrix('X', ORIGIN)),
    ('mirror_y', lambda g: affinity.scale(g, -1.0, 1.0, origin=ORIGIN), affine_mirror_matrix('Y', ORIGIN)),
    ('translate', lambda g: affinity.translate(g, 4.0, -9.5), affine_translate_matrix(4.0, -9.5)),
]


def make_geoms():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (4, 2), (4, 4), (2, 4)]])
    return {
        'point': Point(1, 2),
        'line': LineString([(0, 0), (1, 1), (5, 2)]),
        'ring': LinearRing([(0, 0), (1, 0), (1, 1), (0, 0)]),
        'poly_holes': poly,
        'multipoly': MultiPolygon([poly, Polygon([(20, 20), (21, 20), (21, 21)])]),
        'multiline': MultiLineString([[(0, 0), (1, 1)], [(5, 5), (6, 7)]]),
        'multipoint': MultiPoint([(0, 0), (3, 4)]),
        'collection': GeometryCollection([Point(1, 1), LineString([(0, 0), (2, 2)]), poly]),
        'line3d': LineString([(0, 0, 5), (1, 1, 6), (5, 2, 7)]),
        'empty': Polygon(),
    }


class TestAffineGeometry(unittest.TestCase):

    def assert_same(self, got, exp):
        if exp is None:
            self.assertIsNone(got)
            return
        if isinstance(exp, list):
            self.assertIsInstance(got, list)
            self.assertEqual(len(got), len(exp))
            for g, e in zip(got, exp):
                self.assert_same(g, e)
            return
        if not hasattr(exp, 'geom_type'):
            self.assertIs(got, exp)
            return
        self.assertEqual(got.geom_type, exp.geom_type)
        self.assertEqual(got.has_z, exp.has_z)
        if exp.is_empty:
            self.assertTrue(got.is_empty)
        else:
            self.assertTrue(got.equals_exact(exp, 1e-9), '%s != %s' % (got.wkt, exp.wkt))
            if exp.has_z:
                self.assertEqual(got.wkt.count(' '), exp.wkt.count(' '))

    def test_single_geometries(self):
        for name, ref, matrix in CASES:
            for gname, g in make_geoms().items():
                with self.subTest(op=name, geom=gname):
                    self.assert_same(affine_geometry(g, matrix), ref(g))

    def test_nested_and_mixed_lists(self):
        g = make_geoms()
        obj = [g['poly_holes'], [g['line'], None, [g['point'], g['multipoly']]], None, 7, g['collection'], []]
        for name, ref, matrix in CASES:
            with self.subTest(op=name):
                self.assert_same(affine_geometry(obj, matrix), map_geometry(obj, ref))


# ======================================================================
# merged from tests/test_bounds_vectorized.py
# ======================================================================
"""Vectorized bounds() of Geometry / Gerber / Excellon must equal the old recursive walkers, edge cases included."""
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from shapely import Point, Polygon, LineString, MultiPolygon, GeometryCollection, box  # noqa: E402

from camlib import Geometry, list_total_bounds  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

INF = (np.inf, np.inf, -np.inf, -np.inf)


def old_rec(obj):
    """The old recursive walker (Geometry flavour)."""
    if type(obj) is list:
        r = list(INF)
        for k in obj:
            subs = [old_rec(v) for v in k.values()] if type(k) is dict else (
                [] if getattr(k, 'is_empty', False) else [old_rec(k)])
            for s in subs:
                r = [min(r[0], s[0]), min(r[1], s[1]), max(r[2], s[2]), max(r[3], s[3])]
        return tuple(r)
    return obj.bounds


class ListTotalBoundsTest(unittest.TestCase):
    def test_matches_old_walker(self):
        cases = [
            [], [[], []], [Polygon()], [GeometryCollection()],
            [box(0, 0, 1, 2)],
            [Polygon(), [Polygon(), box(0, 0, 1, 2)], {'a': [box(-1, -1, 0, 0)], 'b': box(5, 5, 6, 7)}],
            [LineString([(0, 0), (5, -2)]), [LineString([(1, 1), (2, 9)])]],
            [GeometryCollection([box(0, 0, 1, 1), Point(9, 9)]), Point(-3, 2)],
            [MultiPolygon([box(0, 0, 1, 1), box(4, 4, 5, 5)])],
        ]
        for case in cases:
            got = list_total_bounds(case)
            self.assertEqual(got, old_rec(case), case)
            self.assertTrue(all(type(v) is float for v in got))

class ObjectBoundsTest(unittest.TestCase):
    def geometry(self):
        g = Geometry(make_app())
        g.tools = {}
        return g

    def test_geometry_single(self):
        g = self.geometry()
        g.multigeo = False
        expect = {None: (0, 0, 0, 0)}
        g.solid_geometry = None
        self.assertEqual(g.bounds(), expect[None])
        g.solid_geometry = []
        self.assertEqual(g.bounds(), INF)
        g.solid_geometry = box(0, 0, 3, 4)
        self.assertEqual(g.bounds(), (0.0, 0.0, 3.0, 4.0))
        g.solid_geometry = [Polygon(), [box(0, 0, 1, 2)], {'k': [box(5, 5, 6, 7)]}]
        self.assertEqual(g.bounds(), (0.0, 0.0, 6.0, 7.0))
        g.solid_geometry = [[], None]
        with self.assertRaises(AttributeError):
            g.bounds()

    def test_excellon(self):
        e = Excellon(make_app())
        self.assertEqual(e.bounds(), (0, 0, 0, 0))
        e.solid_geometry = []
        e.tools = {1: {'solid_geometry': [Point(1, 1).buffer(1), Polygon()]},
                   2: {'solid_geometry': [[Point(5, 5).buffer(2)], {'x': [box(9, 9, 10, 10)]}]}}
        self.assertEqual(e.bounds(), (0.0, 0.0, 10.0, 10.0))
        e.tools = {1: {'solid_geometry': []}}
        self.assertEqual(e.bounds(), INF)
        e.tools = {1: {'solid_geometry': [Point(1, 1).buffer(1), None]}}
        with self.assertRaises(AttributeError):
            e.bounds()


# ======================================================================
# merged from tests/test_rtree_storage_ties.py
# ======================================================================
"""AppRTreeStorage.nearest(): on ties the first inserted point wins, whatever way the index was built."""
import unittest

from shapely.geometry import LineString, Point

from camlib import AppRTreeStorage


def make_storage(objs, bulk):
    storage = AppRTreeStorage()
    storage.get_points = lambda g: [g.coords[0]]
    if bulk:
        storage.insert_many(objs)
    else:
        for o in objs:
            storage.insert(o)
    return storage


def walk(storage, start):
    order = []
    pt = start
    while True:
        try:
            pt, obj = storage.nearest(pt)
        except StopIteration:
            return order
        order.append(pt)
        storage.remove(obj)


class TestRTreeStorageTies(unittest.TestCase):

    def test_ties_give_lowest_id(self):
        # 4 points at distance 1 from the origin
        pts = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        for bulk in (False, True):
            objs = [Point(p) for p in pts]
            storage = make_storage(objs, bulk)
            pt, obj = storage.nearest((0, 0))
            self.assertIs(obj, objs[0], 'bulk=%s' % bulk)
            self.assertEqual(pt, (1, 0))
            objs = [LineString([p, (p[0] + 5, p[1] + 5)]) for p in pts]
            storage = make_storage(objs, bulk)
            self.assertIs(storage.nearest((0, 0))[1], objs[0])

    def test_grid_walk_same_for_both_builds(self):
        pts = [(x * 1.0, y * 1.0) for y in range(30) for x in range(30)]
        orders = [walk(make_storage([Point(p) for p in pts], bulk), (0.0, 0.0)) for bulk in (False, True)]
        self.assertEqual(len(orders[0]), 900)
        self.assertEqual(orders[0], orders[1])


# ======================================================================
# merged from tests/test_geometry_transforms.py
# ======================================================================
"""
camlib.Geometry / GeometryObject / CNCjob transforms (scale, mirror, rotate, skew): the vectorized versions give the
same coordinates as the shapely.affinity reference and keep the container shape.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import shapely
from shapely import affinity
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from appObjects.GeometryObject import GeometryObject
from camlib import CNCjob, Geometry
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402


def sample():
    return [box(0, 0, 2, 3), None,
            [Polygon([(1, 1), (4, 1), (4, 5)]), [LineString([(0, 0, 1), (3, 4, 2)]), Point(2, 2)]],
            MultiPolygon([box(5, 5, 6, 6), box(7, 7, 9, 9)]),
            LineString([(0, 0, 5), (1, 1, 6), (2, 0, 7)])]


def reference(obj, fn, any_iterable):
    if type(obj) is list or (any_iterable and isinstance(obj, tuple)):
        return [reference(g, fn, any_iterable) for g in obj]
    if obj is None:
        return None
    return fn(obj)


def same(test, a, b):
    if isinstance(a, list):
        test.assertIsInstance(b, list)
        test.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            same(test, x, y)
    elif a is None:
        test.assertIsNone(b)
    else:
        test.assertEqual(type(a), type(b))
        test.assertEqual(shapely.has_z(a), shapely.has_z(b))
        test.assertTrue(shapely.equals_exact(a, b, tolerance=1e-9), (a.wkt, b.wkt))
        if shapely.has_z(a):
            test.assertEqual(shapely.get_coordinates(a, include_z=True).tolist().__len__(),
                             shapely.get_coordinates(b, include_z=True).tolist().__len__())
            for ca, cb in zip(shapely.get_coordinates(a, include_z=True), shapely.get_coordinates(b, include_z=True)):
                test.assertAlmostEqual(ca[2], cb[2], places=9)


def build(multigeo):
    geo = Geometry(make_app())
    geo.kind, geo.multigeo = 'geometry', multigeo
    geo.solid_geometry = sample()
    if multigeo:
        geo.tools = {1: {'solid_geometry': sample(), 'data': {}}, 2: {'solid_geometry': box(1, 1, 3, 3)},
                     3: {'solid_geometry': []}}
    return geo


OPS = {
    'rotate': (lambda g: g.rotate(30, (5, 7)), lambda s: affinity.rotate(s, 30, origin=(5, 7)), True),
    'skew': (lambda g: g.skew(10, 20, (3, 4)), lambda s: affinity.skew(s, 10, 20, origin=(3, 4)), True),
    'mirror_x': (lambda g: g.mirror('X', (2, 2)), lambda s: affinity.scale(s, 1, -1, origin=(2, 2)), False),
    'mirror_y': (lambda g: g.mirror('Y', (2, 2)), lambda s: affinity.scale(s, -1, 1, origin=(2, 2)), False),
    'obj_scale': (lambda g: GeometryObject.scale(g, 1.5, 0.5, (1, 2)),
                  lambda s: affinity.scale(s, 1.5, 0.5, origin=(1, 2)), False),
}


class TestGeometryTransforms(unittest.TestCase):

    def test_single_and_multigeo_match_shapely(self):
        for name, (op, ref, any_it) in OPS.items():
            for multigeo in (False, True):
                with self.subTest(op=name, multigeo=multigeo):
                    geo = build(multigeo)
                    op(geo)
                    same(self, reference(sample(), ref, any_it), geo.solid_geometry if not multigeo
                         else geo.tools[1]['solid_geometry'])
                    if multigeo:
                        same(self, ref(box(1, 1, 3, 3)), geo.tools[2]['solid_geometry'])
                        self.assertEqual(geo.tools[3]['solid_geometry'], [])
                        self.assertEqual(geo.tools[1]['data'], {})

    def test_cncjob_transforms(self):
        for name, method, ref in (
                ('rotate', lambda j: CNCjob.rotate(j, 30, (5, 7)), lambda s: affinity.rotate(s, 30, origin=(5, 7))),
                ('skew', lambda j: CNCjob.skew(j, 10, 20, (3, 4)),
                 lambda s: affinity.skew(s, 10, 20, origin=(3, 4))),
                ('mirror', lambda j: CNCjob.mirror(j, 'Y', (2, 2)),
                 lambda s: affinity.scale(s, -1, 1, origin=(2, 2)))):
            with self.subTest(op=name):
                geoms = [LineString([(0, 0), (3, 4)]), box(1, 1, 2, 2), Point(2, 2)]
                job = SimpleNamespace(
                    app=make_app(), gcode_parsed=[{'geom': g, 'kind': ['C', 'F']} for g in geoms],
                    create_geometry=lambda: None, _affine_gcode_parsed=CNCjob._affine_gcode_parsed,
                    tools={}, multitool=False, gcode='', steps_per_circle=16, xy_toolchange=None, xy_end=None)
                job._fixed_points = lambda j=job: CNCjob._fixed_points(j)
                job._transform = lambda matrix, j=job: CNCjob._transform(j, matrix)
                job._transform_native = lambda matrix, j=job: CNCjob._transform_native(j, matrix)
                method(job)
                for d, g in zip(job.gcode_parsed, geoms):
                    same(self, ref(g), d['geom'])
                    self.assertEqual(d['kind'], ['C', 'F'])


# ------------------------------------------------------------------------------------------------------------
# CNCJob: the G-code text follows the geometry
# ------------------------------------------------------------------------------------------------------------

def _imported_job(tmp_path, units='MM'):
    from tests.test_cncjob_export_gcode import _open_gcode, MILL_FILE
    return _open_gcode(tmp_path, MILL_FILE, units=units)[1], MILL_FILE


def _native_job(gcode='G00 X10.0000 Y0.0000\nG01 X10.0000 Y5.0000\n'):
    from unittest.mock import patch
    from appObjects.CNCJobObject import CNCJobObject

    geo = [{'geom': LineString([(10, 0), (10, 5)]), 'kind': ['C', 'S']}]
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', make_app())
    job.multitool = True
    job.obj_options['type'] = 'Geometry'
    job.tools = {1: {'gcode': gcode, 'gcode_parsed': geo, 'solid_geometry': [],
                     'data': {'tools_mill_ppname_g': 'default'}}}
    job.gcode_parsed = geo
    return job


def test_offset_of_an_imported_job_moves_the_text_and_the_plot_together(tmp_path):
    from appParsers.ParseGCode import parse_gcode

    job, original = _imported_job(tmp_path)

    job.offset((5.0, 2.0))

    # X10 has no decimal point and still moves; the line endings and the order of the lines are kept
    assert 'G1 X15.0000 Y3.0000 F200\r\n' in job.gcode
    assert job.gcode.count('\r\n') == original.count('\r\n')
    assert job.tools[1]['gcode'] + job.tools[2]['gcode'] == job.gcode
    assert job.gcode_parsed == job.tools[1]['gcode_parsed'] + job.tools[2]['gcode_parsed']
    reparsed = parse_gcode(job.gcode)['tools'][1]['solid_geometry']
    assert shapely.unary_union(reparsed).bounds == shapely.unary_union(job.tools[1]['solid_geometry']).bounds
    # the cutting moves of the file (1, 1)-(10, 1) and (3, 3)-(3, 9), moved
    plotted = shapely.unary_union([g['geom'] for g in job.gcode_parsed if g['kind'][0] == 'C']).bounds
    assert plotted == pytest.approx((1 + 5, 1 + 2, 10 + 5, 9 + 2))


def test_rotate_of_a_native_job_changes_the_exported_gcode_and_the_plot():
    job = _native_job()

    job.rotate(90, (0, 0))

    text = job.export_gcode(to_file=True).getvalue()
    assert 'G00 X0.0000 Y10.0000' in text and 'G01 X-5.0000 Y10.0000' in text
    assert job.gcode_parsed[0]['geom'].coords[:] == pytest.approx(np.array([(0, 10), (-5, 10)]))
    assert shapely.get_coordinates(job.solid_geometry[0]).ravel().tolist() == pytest.approx([0, 10, -5, 10])


def test_imported_job_with_a_subprogram_is_not_moved_and_tells_the_user(tmp_path):
    job, _ = _imported_job(tmp_path)
    job.gcode = job.gcode + 'M98 P1000\r\n'
    text, plotted = job.gcode, [g['geom'].wkt for g in job.gcode_parsed]
    job.app.inform.reset_mock()

    job.rotate(90, (0, 0))
    job.offset((5.0, 0.0))

    # the text and the plot stay together, unchanged
    assert job.gcode == text and [g['geom'].wkt for g in job.gcode_parsed] == plotted
    errors = [c.args[0] for c in job.app.inform.emit.call_args_list if c.args[0].startswith('[ERROR_NOTCL]')]
    assert len(errors) == 2 and 'subprograms' in errors[0]


def _menu_job(history=False):
    """
    A native job selected in a stub app and the AppObjectOps of that app (Edit -> Flip / Rotate / Skew). The path is
    an L: (10, 0) -> (10, 5) -> (14, 5), bounding box center (12, 2.5), lower left corner (10, 0).
    """
    from unittest.mock import MagicMock
    from appHandlers.appHistory import HistoryManager
    from appHandlers.appObjectOps import AppObjectOps

    path = [(10, 0), (10, 5), (14, 5)]
    job = _native_job('G00 X10.0000 Y0.0000\nG01 X10.0000 Y5.0000\nG01 X14.0000 Y5.0000\n')
    job.gcode_parsed = job.tools[1]['gcode_parsed'] = [{'geom': LineString(path), 'kind': ['C', 'S']}]
    job.tools[1]['solid_geometry'] = [LineString(path)]
    job.plot = Mock()
    job.set_offset_values = Mock()
    assert job.regenerate_source() is True
    app = job.app
    app.collection = SimpleNamespace(get_selected=lambda: [job], get_list=lambda: [job])
    app.ui, app.resource_location = MagicMock(), ''
    if history:
        app.headless, app.projects = False, SimpleNamespace(is_busy=lambda: False)
        app.history = HistoryManager(app, collection=app.collection)
    return job, AppObjectOps(app)


def test_menu_flip_of_a_cncjob_updates_the_saved_gcode_also_after_undo_redo():
    # 'Save CNC Code' writes source_file: it must follow the flip, and the Undo step must keep the matching text
    job, ops = _menu_job(history=True)
    old = job.source_file

    ops.on_flipy()
    flipped = job.source_file
    assert 'G01 X14.0000 Y0.0000' in job.tools[1]['gcode']
    assert 'G01 X14.0000 Y0.0000' in flipped and flipped == job.regenerate_source(store=False)

    assert job.app.history.undo() and job.source_file == old
    assert job.app.history.redo() and job.source_file == flipped


def test_a_closed_contour_erosion_that_splits_keeps_every_piece():
    geo = Geometry(make_app())
    geo.kind, geo.multigeo = 'geometry', False
    geo.geo_steps_per_circle = 64
    geo.solid_geometry = LinearRing([(0, 0), (4, 0), (4, 1), (6, 1), (6, 0), (10, 0), (10, 4),
                                     (6, 4), (6, 3), (4, 3), (4, 4), (0, 4)])
    assert geo.buffer(-1, 'mitre', None) is None
    assert len(geo.solid_geometry) == 2
    assert all(isinstance(part, LinearRing) for part in geo.solid_geometry)
    assert sorted(Polygon(part).area for part in geo.solid_geometry) == pytest.approx([4.0, 4.0])


def test_geometry_unit_conversion_preserves_each_tool_geometry_and_data():
    from copy import deepcopy
    from unittest.mock import patch

    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        geo = GeometryObject('board', make_app())
    geo.units, geo.multigeo = 'MM', True
    geo.ui_disconnect = Mock()
    geo.tools = {
        1: {'tooldia': 6.0, 'solid_geometry': [LineString([(0, 0), (25.4, 12.7)])],
            'data': {'tools_mill_tooldia': 6.0, 'tools_mill_vtipdia': 0.2,
                     'tools_mill_feedrate': 254.0, 'tools_mill_feedrate_z': 25.4,
                     'tools_mill_travelz': 5.0, 'tools_mill_startz': None,
                     'tools_mill_endxy': '25.4, 50.8', 'tools_mill_toolchangexy': '',
                     'cutz': -1.0, 'extra': {'preserve': [1]}}, 'custom': {'uid': 1}},
        2: {'tooldia': 3.0, 'solid_geometry': [Point(50.8, 25.4)],
            'data': {'tools_mill_cutz': -2.54, 'tools_mill_vtipangle': 60,
                     'tools_mill_endxy': None, 'tools_mill_toolchangexy': '(50.8, 25.4)'},
            'custom': {'uid': 2}},
    }
    original = deepcopy(geo.tools)
    assert geo.convert_units('IN') == pytest.approx(1 / 25.4)
    assert list(geo.tools) == [1, 2]
    assert geo.tools[1]['solid_geometry'][0].coords[:] == pytest.approx(np.array([(0, 0), (1, 0.5)]))
    assert geo.tools[2]['solid_geometry'][0].coords[:] == pytest.approx(np.array([(2, 1)]))
    first, second = geo.tools[1]['data'], geo.tools[2]['data']
    assert first['tools_mill_tooldia'] == pytest.approx(6 / 25.4)
    assert first['tools_mill_vtipdia'] == pytest.approx(0.2 / 25.4)
    assert first['tools_mill_feedrate'] == pytest.approx(10)
    assert first['tools_mill_feedrate_z'] == pytest.approx(1)
    assert first['tools_mill_travelz'] == pytest.approx(5 / 25.4)
    assert first['cutz'] == pytest.approx(-1 / 25.4)
    assert first['tools_mill_startz'] is None and first['tools_mill_toolchangexy'] == ''
    assert [float(v) for v in first['tools_mill_endxy'].split(',')] == pytest.approx([1, 2])
    assert second['tools_mill_cutz'] == pytest.approx(-0.1)
    assert second['tools_mill_vtipangle'] == 60 and second['tools_mill_endxy'] is None
    assert [float(v) for v in second['tools_mill_toolchangexy'].split(',')] == pytest.approx([2, 1])
    assert geo.tools[1]['custom'] == {'uid': 1} and geo.tools[2]['custom'] == {'uid': 2}
    first['extra']['preserve'].append(2)
    assert original[1]['data']['extra']['preserve'] == [1]


def test_cncjob_unit_conversion_scales_canonical_tool_metadata_and_round_trips():
    import re
    from unittest.mock import patch
    from appObjects.CNCJobObject import CNCJobObject

    mill_lengths = {'tools_mill_tooldia': 3.0, 'tools_mill_cutz': -2.0, 'tools_mill_depthperpass': 0.5,
                    'tools_mill_travelz': 2.0, 'tools_mill_endz': 15.0, 'tools_mill_toolchangez': 10.0,
                    'tools_mill_feedrate': 100.0, 'tools_mill_feedrate_z': 50.0,
                    'tools_mill_feedrate_rapid': 500.0, 'tools_mill_vtipdia': 0.2,
                    'tools_mill_offset_value': 1.0, 'tools_mill_startz': None}
    drill_lengths = {'tools_drill_cutz': -1.7, 'tools_drill_depthperpass': 0.7, 'tools_drill_travelz': 2.0,
                     'tools_drill_endz': 15.0, 'tools_drill_toolchangez': 10.0, 'tools_drill_feedrate_z': 300.0,
                     'tools_drill_feedrate_rapid': 1500.0, 'tools_drill_peck_depth': 0.5,
                     'tools_drill_peck_retract': 0.2, 'tools_drill_spot_dia': 1.0,
                     'tools_drill_spot_cutz': -0.3, 'tools_drill_spot_feedrate_z': 200.0,
                     'tools_drill_offset': 0.5, 'tools_drill_startz': None}
    # percentages are not lengths: a change of the unit must leave them as they are
    percentages = {'tools_drill_drill_overlap': 25.0, 'tools_mill_milling_overlap': 10.0}
    xy = {'tools_mill_endxy': '(25.4, 50.8)', 'tools_mill_toolchangexy': [12.7, -6.35],
          'tools_drill_job_offset_xy': [25.4, -12.7], 'tools_drill_mirror_point': '(50.8, 25.4)',
          'tools_drill_array_pitch': (25.4, 12.7),
          'tools_drill_toolchangexy': '0.0, 0.0'}
    # a value that cannot be read stays as it is: a conversion must not fail the job
    malformed = {'tools_drill_endxy': 'unknown'}

    def pair(value):
        if isinstance(value, str):
            value = value.replace('(', '').replace(')', '').replace('[', '').replace(']', '')
            value = value.split(',')
        return [float(part) for part in value]

    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        job = CNCJobObject('job', make_app())
    job.units, job.multitool, job.gcode_parsed = 'MM', True, []
    job.tools = {
        1: {'tooldia': 3.0, 'offset': 0.5, 'gcode': 'G21\nG1 X10 Z-1 F100\n',
            'gcode_parsed': [], 'solid_geometry': [],
            'data': dict(mill_lengths, **percentages, **{'tools_mill_vtipangle': 30,
                                                         'tools_mill_multidepth': True},
                         **{k: v for k, v in xy.items() if k.startswith('tools_mill_')},
                         **{k: v for k, v in malformed.items() if k.startswith('tools_mill_')})},
        2: {'tooldia': 1.0, 'offset': 'Path', 'gcode': '', 'gcode_parsed': [], 'solid_geometry': [],
            'data': dict(drill_lengths, **percentages, **{'tools_drill_tool_order': 'no',
                                                          'tools_drill_dwelltime': 1},
                         **{k: v for k, v in xy.items() if k.startswith('tools_drill_')},
                         **{k: v for k, v in malformed.items() if k.startswith('tools_drill_')})},
    }
    job.obj_options['tooldia'] = 3.0
    factor = 1 / 25.4

    job.convert_units('IN')

    assert job.units == 'IN'
    for key, value in mill_lengths.items():
        if value is None:
            assert job.tools[1]['data'][key] is None
        else:
            assert job.tools[1]['data'][key] == pytest.approx(value * factor)
    for key, value in drill_lengths.items():
        if value is None:
            assert job.tools[2]['data'][key] is None
        else:
            assert job.tools[2]['data'][key] == pytest.approx(value * factor)
    for key, value in xy.items():
        if value is None:
            assert job.tools[1 if key.startswith('tools_mill_') else 2]['data'][key] is None
        else:
            tool = job.tools[1 if key.startswith('tools_mill_') else 2]
            assert pair(tool['data'][key]) == pytest.approx([part * factor for part in pair(value)])
    for key, value in percentages.items():
        assert job.tools[1 if key.startswith('tools_mill_') else 2]['data'][key] == value
    for key, value in malformed.items():
        assert job.tools[1 if key.startswith('tools_mill_') else 2]['data'][key] == value
    assert job.tools[1]['offset'] == pytest.approx(0.5 * factor)
    assert job.tools[2]['offset'] == 'Path'
    assert job.tools[1]['data']['tools_mill_vtipangle'] == 30
    assert job.tools[1]['data']['tools_mill_multidepth'] is True
    assert job.tools[2]['data']['tools_drill_tool_order'] == 'no'
    assert job.tools[2]['data']['tools_drill_dwelltime'] == 1
    words = dict(re.findall(r'([XYZF])([-+]?\d*\.?\d+)', job.tools[1]['gcode'].splitlines()[1]))
    assert 'G20' in job.tools[1]['gcode'] and float(words['X']) == pytest.approx(10 * factor, abs=1e-3)

    job.convert_units('MM')

    assert job.units == 'MM'
    for key, value in mill_lengths.items():
        if value is None:
            assert job.tools[1]['data'][key] is None
        else:
            assert job.tools[1]['data'][key] == pytest.approx(value)
    for key, value in drill_lengths.items():
        if value is None:
            assert job.tools[2]['data'][key] is None
        else:
            assert job.tools[2]['data'][key] == pytest.approx(value)
    for key, value in xy.items():
        if value is not None:
            tool = job.tools[1 if key.startswith('tools_mill_') else 2]
            assert pair(tool['data'][key]) == pytest.approx(pair(value))
    for key, value in percentages.items():
        assert job.tools[1 if key.startswith('tools_mill_') else 2]['data'][key] == value
    for key, value in malformed.items():
        assert job.tools[1 if key.startswith('tools_mill_') else 2]['data'][key] == value
    assert job.tools[1]['offset'] == pytest.approx(0.5)
    words = dict(re.findall(r'([XYZF])([-+]?\d*\.?\d+)', job.tools[1]['gcode'].splitlines()[1]))
    assert 'G21' in job.tools[1]['gcode'] and float(words['X']) == pytest.approx(10, abs=1e-3)


# camlib.map_geometry, the shared coordinate walker of the Gerber / Excellon transforms


# Geometry draws on the top canvas layer (above the filled Gerber) and keeps the visible flag


# ======================================================================
# merged from tests/test_localized_geometry_fixes.py
# ======================================================================
"""Behavioral regressions for milling, isolation, deletion, export and editor cuts."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtWidgets
from shapely.geometry import LineString, LinearRing, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from appEditors.appGeoEditor import AppGeoEditor
from appPlugins.ToolIsolation import ToolIsolation
from camlib import CNCjob, Geometry
from defaults import AppDefaults
from tclCommands.TclCommandInteriors import TclCommandInteriors
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402


class TestLocalizedGeometryFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_milling_uses_current_tool_depths(self):
        app = make_app()
        job = CNCjob(app)
        job.obj_options = {'name': 'job'}
        job.z_cut, job.z_move, job.z_depthpercut = 0, 0, 99
        job.doformat = Mock(return_value='')
        job.create_gcode_single_pass = Mock(return_value='cut\n')
        job.create_gcode_multi_pass = Mock(side_effect=lambda geo, *a, **k: ('cut\n', geo))
        data = dict(AppDefaults().defaults)
        data.update(tools_mill_tooldia=1, tools_mill_offset_value=0,
                    tools_mill_optimization_type='R', tools_mill_cutz=0.8,
                    tools_mill_travelz=-2, tools_mill_depthperpass=2,
                    tools_mill_multidepth=True)
        tools = {1: {'data': data, 'solid_geometry': [LineString([(0, 0), (2, 0)])]}}

        def generate(tool=1):
            return job.geometry_tool_gcode_gen(tool, tools, (0, 0), (0, 0), 0.001)

        self.assertNotEqual(generate(), 'fail')
        self.assertEqual((job.z_cut, job.z_move, job.z_depthpercut), (-0.8, 2, 0.8))
        job.create_gcode_multi_pass.assert_called_once()
        self.assertEqual(job.create_gcode_multi_pass.call_args.kwargs['z_move'], 2)
        data = dict(data)
        tools[2] = {'data': data, 'solid_geometry': tools[1]['solid_geometry']}
        data.update(tools_mill_cutz=-0.3, tools_mill_travelz=3, tools_mill_depthperpass=0.1)
        self.assertNotEqual(generate(2), 'fail')
        self.assertEqual((job.z_cut, job.z_move, job.z_depthpercut), (-0.3, 3, 0.1))
        tools[1]['data'] = data
        data['tools_mill_travelz'] = 0
        self.assertEqual(generate(), 'fail')
        data.update(tools_mill_travelz=2, tools_mill_cutz=0)
        self.assertEqual(generate(), 'fail')
        data['tools_mill_ppname_g'] = 'default_laser'
        self.assertNotEqual(generate(), 'fail')
        data['tools_mill_cutz'] = None
        self.assertNotEqual(generate(), 'fail')
        data['tools_mill_ppname_g'] = 'default'
        self.assertEqual(generate(), 'fail')
        data.update(tools_mill_cutz=-1, tools_mill_travelz=None)
        self.assertEqual(generate(), 'fail')

    def test_interiors_and_isolation_direction(self):
        geo = Geometry(make_app())
        polygon = Polygon(box(0, 0, 10, 10).exterior,
                          [box(1, 1, 2, 2).exterior.coords, box(5, 5, 7, 7).exterior.coords])
        geo.solid_geometry = [polygon, box(20, 20, 21, 21)]
        rings = geo.get_interiors()
        self.assertEqual(len(rings), 2)
        self.assertTrue(all(isinstance(ring, LinearRing) for ring in rings))
        self.assertEqual(geo.get_interiors(box(0, 0, 1, 1)), [])
        self.assertEqual(len(geo.get_interiors(MultiPolygon(geo.solid_geometry))), 2)
        tool = SimpleNamespace(app=geo.app)
        normal = ToolIsolation.generate_envelope(tool, geo, 0, False, env_iso_type=1)
        reversed_geo = ToolIsolation.generate_envelope(tool, geo, 0, True, env_iso_type=1)
        self.assertEqual(len(reversed_geo.geoms), 2)
        for ring, poly in zip(normal, reversed_geo.geoms):
            self.assertEqual(list(ring.coords)[::-1], list(poly.exterior.coords))
        geo.app.collection.get_by_name.return_value = geo
        output = SimpleNamespace()
        geo.app.app_obj.new_object.side_effect = lambda kind, name, init, **kwargs: init(output, geo.app)
        TclCommandInteriors.execute(SimpleNamespace(app=geo.app), {'name': 'source'}, [])
        self.assertTrue(all(isinstance(ring, LinearRing) for ring in output.solid_geometry))
        self.assertEqual(len(output.solid_geometry), 2)

    def cut(self, target, cutter):
        shapes = [SimpleNamespace(geo=target), SimpleNamespace(geo=cutter)]
        app = make_app()
        app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
        editor = SimpleNamespace(app=app, selected=shapes, get_selected=lambda: shapes,
                                 delete_shape=Mock(), plot_changes=Mock(),
                                 build_ui_sig=Mock(), flat_geo=[])
        editor.flatten = lambda *a, **k: AppGeoEditor.flatten(editor, *a, **k)
        editor._collect_shape = lambda shape, dest: AppGeoEditor._collect_shape(editor, shape, dest)
        AppGeoEditor.cutpath(editor)
        editor.delete_shape.assert_called_once()
        self.assertIs(editor.delete_shape.call_args.args[0], shapes[0])
        self.assertEqual(shapes[1].geo, cutter)
        return [shape.geo for shape in editor.delete_shape.call_args.kwargs['inserted']]

    def test_cut_closed_masks_and_multipart_results(self):
        target = LineString([(-2, 0), (2, 0)])
        mask = box(-1, -1, 1, 1)
        for cutter in (mask, mask.exterior, LineString(mask.exterior.coords)):
            result = self.cut(target, cutter)
            self.assertEqual(len(result), 2)
            self.assertTrue(all(isinstance(part, LineString) for part in result))
            self.assertTrue(unary_union(result).equals(target.difference(mask)))
        multi = MultiLineString([target, LineString([(-2, 0.5), (2, 0.5)])])
        self.assertTrue(unary_union(self.cut(multi, mask.exterior)).equals(multi.difference(mask)))
    def test_circle_steps_count_the_whole_circle(self):
        steps = 32
        geo = Geometry(make_app(), geo_steps_per_circle=steps)
        geo.tools = {1: {'solid_geometry': []}}
        geo.add_circle((0, 0), 1.0)
        self.assertAlmostEqual(len(geo.solid_geometry[0].exterior.coords) - 1, steps, delta=2)

        geo.solid_geometry = [LineString([(0, 0), (4, 0)])]
        geo.multigeo = False
        geo.buffer(1.0, 'round', None)
        # a buffered line is two round caps: about one full circle of vertices, not four
        self.assertLessEqual(len(geo.solid_geometry[0].exterior.coords) - 1, steps + 4)


# ======================================================================
# merged from tests/test_feature_access.py
# ======================================================================
"""Tests of the Stage 2 read accessors (``appParsers/feature_access.py``) on the unification fixtures.

Everything is headless. The helpers come from ``tests.helpers_objects``.
"""

import pytest
import shapely
from shapely.errors import GEOSException
from shapely.geometry import (
    GeometryCollection, LineString, MultiPolygon, Point, Polygon, box,
)
from shapely.ops import unary_union

from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.feature_access import (
    FeatureRecord, fused_solid, is_empty, iter_features, iter_items, normalize_polygonal,
)

from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_poly_equiv, excellon, gerber, _headless_qt, make_excellon, make_same_diameter_excellon,
    sym_diff_area,
)


def feats(obj):
    return list(iter_features(obj))


def of_item(obj, key):
    return [f for f in iter_features(obj) if f.item_key == key]


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

class TestGerberFeatures:

    def test_aperture_10_flash_track_clear_track(self, gerber):
        got = of_item(gerber, 10)
        assert [(f.kind, f.polarity) for f in got] == [('flash', 'dark'), ('route', 'dark'), ('route', 'clear')]
        assert isinstance(got[0].follow, Point) and isinstance(got[1].follow, LineString)
        # the clear feature carries its 'clear' geometry as solid
        assert got[2].solid is gerber.tools[10]['geometry'][2]['clear']
        assert 'solid' not in gerber.tools[10]['geometry'][2]


    def test_region(self, gerber):
        got = of_item(gerber, 0)
        assert [(f.kind, f.polarity) for f in got] == [('region', 'dark')]
        assert isinstance(got[0].solid, Polygon) and got[0].solid.area == pytest.approx(80.0)


# ---------------------------------------------------------------------------------------------------------------
# Excellon
# ---------------------------------------------------------------------------------------------------------------

class TestExcellonFeatures:
    def test_solids_equal_tool_solid_geometry(self, excellon):
        for t, tool in excellon.tools.items():
            got = [f.solid for f in of_item(excellon, t)]
            assert len(got) == len(tool['solid_geometry']) == 3
            for mine, parsed in zip(got, tool['solid_geometry']):
                assert mine.equals_exact(parsed, 0.0), 'solid of tool %s differs from create_geometry' % t


# ---------------------------------------------------------------------------------------------------------------
# normalize_polygonal
# ---------------------------------------------------------------------------------------------------------------

def bowtie():
    return Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])


class TestNormalizePolygonal:
    def test_bowtie_with_overlapping_rectangle(self):
        bad = bowtie()
        rect = box(1, -1, 3, 5)
        assert not bad.is_valid
        # meaningful only if the plain union is broken: it raises or returns an invalid result
        try:
            plain = unary_union([bad, rect])
            plain_broken = (not plain.is_valid)
        except GEOSException:
            plain_broken = True
        assert plain_broken, 'plain unary_union handled the bow-tie, the test would prove nothing'

        result = normalize_polygonal([bad, rect])
        assert isinstance(result, (Polygon, MultiPolygon)) and result.is_valid and not result.is_empty
        expected = unary_union([shapely.make_valid(bad), rect])
        assert result.symmetric_difference(expected).area < 1e-9
        assert result.contains(Point(2, 4.5))   # the rectangle
        assert result.contains(Point(0.5, 2))  # a lobe of the bow-tie

    def test_only_lines_gives_empty_polygon(self):
        result = normalize_polygonal([LineString([(0, 0), (1, 1)]), Point(1, 1)])
        assert isinstance(result, Polygon) and result.is_empty


# ---------------------------------------------------------------------------------------------------------------
# fused_solid, is_empty
# ---------------------------------------------------------------------------------------------------------------

class TestFusedSolid:
    def test_dark_clear_dark_equals_composed_geometry(self, gerber):
        fused = fused_solid(gerber)
        assert isinstance(fused, (Polygon, MultiPolygon)) and fused.is_valid
        composed = unary_union([shapely.make_valid(g) for g in gerber.solid_geometry])
        assert fused.symmetric_difference(composed).area < 1e-6
        assert_poly_equiv(fused, gerber.solid_geometry, msg='fused vs parser')
        # the clear gap is applied: a union of the dark records would cover it
        assert not fused.contains(Point(15, 33))
        assert fused.contains(Point(15, 35))     # the dark flash after the clear
        assert fused.contains(Point(8, 35))      # the track away from the gap
        dark_union = unary_union([f.solid for f in iter_features(gerber) if f.polarity == 'dark'])
        assert dark_union.contains(Point(15, 33))

    def test_excellon(self, excellon):
        fused = fused_solid(excellon)
        assert isinstance(fused, (Polygon, MultiPolygon)) and fused.is_valid
        parts = [g for t in excellon.tools.values() for g in t['solid_geometry']]
        assert fused.symmetric_difference(unary_union(parts)).area < 1e-9
        assert fused.contains(Point(5, 5)) and not fused.contains(Point(1, 1))


class TestIsEmpty:
    def test_new_objects_are_empty(self, app):
        grb = GerberObject('g', app)
        exc = ExcellonObject('e', app)
        assert grb.tools == {} and is_empty(grb) is True
        assert is_empty(exc) is True


