"""
camlib.Geometry / GeometryObject / CNCjob transforms (scale, mirror, rotate, skew): the vectorized versions give the
same coordinates as the shapely.affinity reference and keep the container shape.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

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


class AbortAfterOneRead:
    """False on the first read, True on the next: the abort arrives while the batch runs."""

    def __init__(self):
        self.reads = 0

    def __bool__(self):
        self.reads += 1
        return self.reads > 1


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

    def test_multigeo_transforms_main_solid_geometry_too(self):
        geo = build(True)
        before = geo.solid_geometry
        geo.rotate(30, (0, 0))
        same(self, reference(before, lambda g: affinity.rotate(g, 30, origin=(0, 0)), True),
             geo.solid_geometry)

    def test_single_geometry_stays_single(self):
        geo = build(False)
        geo.solid_geometry = MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])
        geo.rotate(45, (1, 1))
        self.assertIsInstance(geo.solid_geometry, MultiPolygon)
        same(self, affinity.rotate(MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]), 45, origin=(1, 1)),
             geo.solid_geometry)

    def test_none_stays_none(self):
        geo = build(False)
        geo.solid_geometry = None
        geo.mirror('X', (0, 0))
        self.assertIsNone(geo.solid_geometry)

    def test_scale_by_one_is_a_noop(self):
        geo = build(False)
        before = geo.solid_geometry
        GeometryObject.scale(geo, 1, 1)
        self.assertIs(geo.solid_geometry, before)

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
                    tools={}, multitool=False, gcode='', steps_per_circle=16)
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
    assert job.gcode_parsed[0]['geom'].coords[:] == pytest.approx([(0, 10), (-5, 10)])
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


@pytest.mark.parametrize('imported', [False, True])
def test_regenerate_source_after_a_move_gives_the_moved_coordinates(tmp_path, imported):
    job = _imported_job(tmp_path)[0] if imported else _native_job()
    job.source_file = 'old'

    job.offset((5.0, 2.0))
    text = job.regenerate_source(store=False)
    assert job.source_file == 'old'
    assert job.regenerate_source() is True
    assert job.source_file == text
    assert ('G1 X15.0000 Y3.0000' if imported else 'G01 X15.0000 Y7.0000') in text
    if imported:
        assert text == job.gcode


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


def test_menu_rotate_and_skew_of_a_cncjob_update_the_saved_gcode(monkeypatch):
    import appGUI.GUIElements as gui

    class Spinner:
        def __init__(self, *a, **k):
            pass

        def setWindowIcon(self, *a):
            pass

        def get_value(self):
            return 45.0, True

    monkeypatch.setattr(gui, 'FCInputDoubleSpinner', Spinner)
    # the last move of the L after the transform; the menu rotates clockwise
    for handler, kwargs, last in (('on_rotate', {'silent': True, 'preset': 90}, 'G01 X14.5000 Y0.5000'),
                                  ('on_skewx', {}, 'G01 X19.0000 Y5.0000'),
                                  ('on_skewy', {}, 'G01 X14.0000 Y9.0000')):
        job, ops = _menu_job()
        getattr(ops, handler)(**kwargs)
        assert last in job.tools[1]['gcode'] and last in job.source_file, handler



def test_an_erosion_that_removes_everything_fails_without_a_change():
    geo = Geometry(make_app())
    geo.kind, geo.multigeo = 'geometry', False
    geo.geo_steps_per_circle = 64
    geo.solid_geometry = box(0, 0, 1, 1)
    assert geo.buffer(-1, 2, None) == 'fail'
    assert shapely.unary_union(geo.solid_geometry).area == pytest.approx(1.0)




def test_multigeo_buffer_fails_atomically_when_one_tool_erodes_away():
    geo = Geometry(make_app())
    geo.kind, geo.multigeo = 'geometry', True
    geo.geo_steps_per_circle = 64
    geo.solid_geometry = box(0, 0, 10, 10)
    geo.tools = {1: {'solid_geometry': [box(0, 0, 10, 10)], 'data': {}},
                 2: {'solid_geometry': [box(20, 20, 21, 21)], 'data': {}}}
    aggregate, tool = geo.solid_geometry, geo.tools[1]['solid_geometry'][0]
    assert geo.buffer(-1, 'mitre', None) == 'fail'
    assert geo.solid_geometry is aggregate
    assert geo.tools[1]['solid_geometry'][0] is tool
    assert geo.tools[2]['solid_geometry'][0].equals(box(20, 20, 21, 21))


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




def test_factor_buffer_scales_each_element_about_its_own_center():
    geo = Geometry(make_app())
    geo.kind, geo.multigeo = 'geometry', False
    geo.geo_steps_per_circle = 64
    geo.solid_geometry = [box(0, 0, 2, 2), box(10, 0, 12, 2)]
    assert geo.buffer(2, 'mitre', True) is None
    assert [part.bounds for part in geo.solid_geometry] == pytest.approx([(-1, -1, 3, 3), (9, -1, 13, 3)])


