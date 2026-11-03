import os
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtWidgets
from shapely import LineString, Point

from appPlugins.ToolMilling import ToolMilling
from defaults import AppDefaults
from camlib import Geometry


class MillingHarness(SimpleNamespace):
    """Run the real controller methods without constructing its application GUI."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))


class ForbiddenUI:
    def __getattr__(self, name):
        raise AssertionError('Worker/Tcl accessed widget: %s' % name)


def _cnc_harness(multigeo):
    options = deepcopy(AppDefaults.factory_defaults)
    data = {key: value for key, value in options.items() if key.startswith('tools_mill_')}
    data.update(tools_mill_tooldia=0.8, tools_mill_offset_type=3,
                tools_mill_offset_value=0.125, tools_mill_job_type=0, seg_x=2, seg_y=3)
    geometry = [LineString([(0, 0), (1, 0)])]
    tools = {uid: {'tooldia': 0.8, 'data': deepcopy(data), 'solid_geometry': geometry}
             for uid in (7, 2)}
    source = SimpleNamespace(
        multigeo=multigeo, tools=tools, solid_geometry=geometry,
        obj_options=dict(data, name='board', xmin=0, ymin=0, xmax=1, ymax=1))
    events, queued, created, calls = [], [], [], []
    app = SimpleNamespace(
        options=options, defaults={}, decimals=4, abort_flag=False,
        log=SimpleNamespace(debug=lambda *_: None, error=lambda *_: None),
        inform=SimpleNamespace(emit=events.append),
        dec_format=lambda value, decimals: round(value, decimals),
        proc_container=SimpleNamespace(new=lambda *_: nullcontext()),
        collection=SimpleNamespace(promise=lambda name: events.append(('promise', name))),
        worker_task=SimpleNamespace(emit=queued.append))
    milling = MillingHarness()
    milling.app, milling.target_obj, milling.sel_tools = app, source, tools
    milling.units, milling.decimals, milling.circle_steps = 'MM', 4, 32
    values = dict(toolchange_cb=True, offset_entry=0.125, toolchangez_entry=4.5,
                  toolchangexy_entry='2, 3', endz_entry=6.0, endxy_entry='8, 9',
                  pdepth_entry=-0.2, feedrate_probe_entry=12.0, exclusion_cb=True,
                  area_shape_radio='square', strategy_radio='over', over_z_entry=7.0,
                  pp_geo_name_cb='default')
    milling.ui = SimpleNamespace(**{
        key: SimpleNamespace(get_value=lambda key=key: values[key]) for key in values})

    def generate(*args, **kwargs):
        calls.append((args, deepcopy(kwargs)))
        return 'G1 X%d\n' % len(calls), 'HEADER\n'

    def new_object(kind, name, initializer, **kwargs):
        job = SimpleNamespace(kind=kind, obj_options={}, tools={}, gc_start='',
                              generate_from_geometry_2=generate, geometry_tool_gcode_gen=generate,
                              gcode_parse=lambda **kw: [('parsed', deepcopy(kw))])
        result = initializer(job, app)
        created.append((job, kwargs, result))
        return 'fail' if result == 'fail' else job

    milling.new_object = new_object
    return milling, values, queued, created, calls, events


@pytest.mark.parametrize('mill_type', ['both'])
@pytest.mark.parametrize('offset_type', [2])
def test_milling_excellon_extracted_paint_matches_per_feature_reference(mill_type, offset_type):
    tool = MillingHarness()
    progress = []
    tool.app = SimpleNamespace(abort_flag=False, log=SimpleNamespace(debug=lambda *_: None),
        proc_container=SimpleNamespace(update_view_text=progress.append))
    tool.clear_polygon_seed = Geometry.clear_polygon_seed.__get__(tool)
    record = dict(tooldia=2, drills=[Point(0, 0), Point(0, 0)],
                  slots=[((4, 0), (5, 0)), ((4, 0), (5, 0))], data=dict(
        tools_mill_milling_type=mill_type, tools_mill_milling_dia=0.5,
        tools_mill_milling_overlap=20, tools_mill_milling_connect=False,
        tools_mill_offset_type=offset_type, tools_mill_offset_value=0.1))
    offset = (0, -0.25, 0.25, 0.1)[offset_type]
    polygons = []
    if mill_type in ('drills', 'both'):
        polygons.extend(point.buffer(1 + offset, 8) for point in record['drills'])
    if mill_type in ('slots', 'both'):
        polygons.extend(LineString(slot).buffer(1 + offset, 8) for slot in record['slots'])
    expected = []
    for polygon in polygons:
        storage = tool.clear_polygon_seed(polygon, seedpoint=polygon.centroid, tooldia=0.5,
            overlap=0.2, steps_per_circle=32, connect=False, contour=True, prog_plot=False)
        if storage:
            expected.extend(g for g in storage.get_objects() if g and not g.is_empty)
    actual = tool._paint_excellon_tool_geometry(record, 32)
    assert len(actual) == len(expected)
    assert [g.wkb for g in actual] == [g.wkb for g in expected]
    assert progress[-1] == ' 100%'


@pytest.mark.parametrize('multigeo', [False])
@pytest.mark.parametrize('offset_type,expected', [(1, -0.4), (2, 0.4)])
@pytest.mark.parametrize('disable_offset', [False])
def test_milling_cnc_offset_modes(multigeo, offset_type, expected, disable_offset):
    milling, _, _, created, calls, _ = _cnc_harness(multigeo)
    for record in milling.sel_tools.values():
        record['data']['tools_mill_offset_type'] = offset_type
    milling.generate_cnc_job_handler(use_thread=False, disable_offset=disable_offset)
    expected = 0 if disable_offset else expected
    assert all(record['data']['tools_mill_offset_value'] == expected
               for record in created[0][0].tools.values())
    if not multigeo:
        assert all(kwargs['offset'] == expected for _, kwargs in calls)


@pytest.mark.parametrize('multigeo', [False, True])
def test_milling_custom_offset_of_each_tool_is_used(multigeo):
    # the form shows the offset of the selected row (0.125); tool 2 has a Custom offset of its own
    milling, _, _, created, calls, _ = _cnc_harness(multigeo)
    milling.sel_tools[2]['data']['tools_mill_offset_value'] = -0.2
    milling.generate_cnc_job_handler(use_thread=False)
    job = created[0][0]
    assert job.tools[7]['data']['tools_mill_offset_value'] == 0.125
    assert job.tools[2]['data']['tools_mill_offset_value'] == -0.2
    if multigeo:
        # geometry_tool_gcode_gen() reads the offset from the tool data
        assert milling.sel_tools[2]['data']['tools_mill_offset_value'] == -0.2
    else:
        assert [kwargs['offset'] for _, kwargs in calls] == [0.125, -0.2]


@pytest.mark.parametrize('multigeo', [False, True])
def test_milling_real_cnc_generation_matches_deferred_submission(qapp, multigeo):
    from camlib import CNCjob
    from preprocessors.default import default
    from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402
    outputs = []
    for deferred in (False, True):
        milling, _, _, _, _, _ = _cnc_harness(multigeo)
        app = make_app()
        app.preprocessors['default'] = default()
        app.dec_format = lambda value, decimals: round(value, decimals)
        app.exc_areas = SimpleNamespace(travel_coordinates=lambda **kw: [(None, kw['end_point'])])
        milling.app = app
        milling.sel_tools = {7: milling.sel_tools[7]}
        milling.target_obj.tools = milling.sel_tools
        data = milling.sel_tools[7]['data']
        data.update(tools_mill_offset_type=0, tools_mill_toolchange=False,
                    tools_mill_cutz=-0.5, tools_mill_feedrate=120)
        jobs = []

        def new_object(kind, name, initializer, **kwargs):
            job = CNCjob(app)
            job.kind, job.obj_options, job.tools, job.gc_start = kind, {'name': name}, {}, ''
            assert initializer(job, app) != 'fail'
            jobs.append(job)
            return job

        milling.new_object = new_object
        milling.generate_cnc_job_handler(use_thread=deferred, toolchange=False)
        if deferred:
            milling.ui = ForbiddenUI()
            app.options.update(global_tolerance=1, cncjob_coords_decimals=1)
            task = app.worker_task.emit.call_args.args[0]
            task['fcn'](*task['params'])
        job = jobs[0]
        assert 'X1.0000' in job.source_file
        assert job.tools[7]['gcode_parsed']
        outputs.append(job.source_file)
    assert outputs[0] == outputs[1]


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app


class GeometryObject:
    kind = 'geometry'

    def __init__(self):
        self.obj_options = {}
        self.solid_geometry = []


def _excellon_stub(tools, options=None, steps=32):
    """An ExcellonObject stand-in running the real generate_milling_* methods."""
    from appObjects.ExcellonObject import ExcellonObject
    created = {}
    emitted = []
    promised = []
    app = SimpleNamespace(
        options={'geometry_circle_steps_mode': 'fixed', 'geometry_circle_steps': steps, **(options or {})},
        defaults=SimpleNamespace(get=lambda *_a: None, factory_defaults={'tools_mill_multidepth': False}),
        log=SimpleNamespace(debug=lambda *_args: None),
        inform=SimpleNamespace(emit=lambda *_args: None),
        worker_task=SimpleNamespace(emit=emitted.append),
        collection=SimpleNamespace(promise=promised.append),
    )

    def new_object(kind, name, initializer, **kwargs):
        result = GeometryObject()
        if initializer(result, app) == 'fail':
            return 'fail'
        created['object'] = result
        created['name'] = name
        return result

    app.app_obj = SimpleNamespace(new_object=new_object)
    exc = SimpleNamespace(app=app, decimals=4, units='MM', tools=tools,
                          obj_options={'name': 'holes', 'slot_tooldia': 0.5},
                          get_selected_tools_list=lambda: list(tools))
    for name in ('_milling_tool_keys', '_create_milling_geometry', '_milling_multidepth', '_milling_steps',
                 'generate_milling_drills', 'generate_milling_slots'):
        setattr(exc, name, getattr(ExcellonObject, name).__get__(exc) if name != '_milling_multidepth'
                else ExcellonObject._milling_multidepth)
    return exc, created, emitted, promised


def test_milling_drills_equal_bit_tolerance_small_hole_and_circle_steps():
    tools = {
        1: {'tooldia': 0.8, 'drills': [Point(0, 0)], 'slots': []},
        2: {'tooldia': 2.0, 'drills': [Point(10, 0), Point(12, 1)], 'slots': []},
    }
    exc, created, _, _ = _excellon_stub(tools, steps=32)

    # 0.8000001 (inch/mm rounding) still fits the 0.8 hole: almost-zero ring, not an empty ring
    assert exc.generate_milling_drills(tools=[1, 2], tooldia=0.8000001) == (True, '')
    geometry = created['object'].solid_geometry
    assert len(geometry) == 3
    assert not any(ring.is_empty for ring in geometry)
    assert geometry[0].equals_exact(Point(0, 0).buffer(0.0000001, 8).exterior, 0.0)
    assert geometry[1].equals_exact(Point(10, 0).buffer(0.6, 8).exterior, 1e-9)
    # circle steps count the whole circle: 32 steps -> 8 quad segs -> 33 ring points
    assert len(geometry[1].coords) == 33

    # a bit larger than the hole is refused, no geometry
    created.clear()
    assert exc.generate_milling_drills(tools=[1], tooldia=0.9)[0] is False
    assert not created

    # a small tool with only slots has no drills to mill: it does not cancel the drills of the other tools
    tools[3] = {'tooldia': 0.5, 'drills': [], 'slots': [((0, 0), (1, 0))]}
    assert exc.generate_milling_drills(tools=[2, 3], tooldia=0.9) == (True, '')
    assert len(created['object'].solid_geometry) == 2


def test_milling_slots_circle_steps_and_multidepth_key():
    tools = {1: {'tooldia': 2.0, 'drills': [], 'slots': [((0, 0), (5, 0))]},
             2: {'tooldia': 1.0, 'drills': [], 'slots': []}}
    exc, created, _, _ = _excellon_stub(tools, options={'tools_mill_multidepth': True}, steps=32)

    assert exc.generate_milling_slots(tools=[1, 2], tooldia=1.0) == (True, '')
    geometry = created['object'].solid_geometry
    assert len(geometry) == 1
    expected = LineString([(0, 0), (5, 0)]).buffer(0.5001, 8).exterior
    assert geometry[0].equals_exact(expected, 1e-9)
    assert len(geometry[0].coords) == len(expected.coords) < 50
    # the CNC job reads tools_mill_multidepth
    assert created['object'].obj_options['tools_mill_multidepth'] is True
    assert 'multidepth' not in created['object'].obj_options

    # nothing to mill (only slotless tool) -> failure, not success
    created.clear()
    assert exc.generate_milling_slots(tools=[2], tooldia=1.0)[0] is False

