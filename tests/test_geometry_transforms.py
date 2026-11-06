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


