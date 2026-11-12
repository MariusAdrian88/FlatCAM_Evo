"""Merged from: test_milling_single_geometry_tools: A single-geometry Milling job with 2 or more tools had the paths of the earlier tools in its G-code again.; test_milling_lifecycle: ; test_milling_old_tool_data: The CNC job of the Milling plugin (and of the Tcl 'cncjob' command) reads the 'tools_mill_*' options from the tool; test_milling_target_settings: Issue #41: the Milling plugin took the general job settings of a multi-geometry job (preprocessor, End Z, toolchange,; test_milling_apply_param_to_all: Milling Tool: 'Apply parameters to all tools' reads the tools table of the current target.
"""

import importlib
import re
import pytest
import os
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock, patch
from shapely.geometry import LineString, Polygon
from appObjects.CNCJobObject import CNCJobObject
from camlib import deepcopy_keep_geometry, Geometry, CNCjob
from appPlugins.ToolMilling import ToolMilling
from tests.helpers_objects import make_app_0b as make_app
from contextlib import nullcontext
from copy import deepcopy
from PyQt6 import QtWidgets, QtCore
from shapely import Point
from defaults import AppDefaults
from preprocessors.GRBL_11 import GRBL_11
from preprocessors.GRBL_laser import GRBL_laser
from preprocessors.GRBL_laser_z import GRBL_laser_z
from preprocessors.Line_xyz import Line_xyz
from preprocessors.default import default
from appGUI.GUIElements import FCTable


# ==============================================================================================================
# test_milling_single_geometry_tools: A single-geometry Milling job with 2 or more tools had the paths of the earlier tools in its G-code again.
# ==============================================================================================================

# 3 paths with 9 X-Y cut moves in all (4 + 4 + 1)
PATHS = [Polygon([(0, 0), (10, 0), (10, 5), (0, 5)]).exterior,
         Polygon([(20, 0), (30, 0), (30, 5), (20, 5)]).exterior,
         LineString([(0, 10), (30, 10)])]

N_PATHS = 3

CUT_MOVES = 9

@pytest.mark.parametrize('pp_name, toolchange, startz', [('default', False, ''), ('default', True, 1.0),
                                                         ('GRBL_laser', True, '')])
def test_two_tool_milling_golden(pp_name, toolchange, startz):
    import hashlib
    from tests.test_cncjob_export_gcode import gcode_job, milling_tools
    job = gcode_job(pp_name)
    tools = milling_tools([LineString([(1, 2), (3, 4)])], tools_mill_ppname_g=pp_name,
                          tools_mill_startz=startz, tools_mill_toolchangez=15,
                          tools_mill_toolchangexy='10, 20')
    tools[2] = milling_tools([LineString([(5, 6), (7, 8)])], **tools[1]['data'])[1]
    output = []
    for uid in tools:
        code, header = job.geometry_tool_gcode_gen(uid, tools, (0, 0), (9, 10), 0,
                                                  is_first=uid == 1, is_last=uid == 2,
                                                  toolchange=toolchange)
        output.append(header + code)
    digest = hashlib.sha256(''.join(output).encode()).hexdigest()
    if pp_name == 'GRBL_laser':
        expected = ('c5e42e6787274ea43ab8a24ece313b81f601a6e9f02b4c3c393b768bd7f7ecac' if toolchange else
                    'bbf5910546ea708b8e86bd44ac3eec7207963f3874298ad7c72c19c6b03df7af')
    else:
        expected = {
            (False, ''): '21bbb21e36dbfdca718637f6f189ec75a6678e6a116862bdf5daef181bfb52b3',
            (True, ''): '4a40c95fb8763341b195161d1ae5f182784d64869c968ab85d2bb774117812ac',
            (False, 1.0): '067a74c13e3c14b3d0bfbffd49e6ab35fc5ac1b80d7b34442e115d50f97b63ab',
            (True, 1.0): '73eea86b50265ba98ce232ae3c6929d9a9157c4eb527530af763a9d30f3a2c84',
        }[toolchange, startz]
    assert digest == expected

class MillingHarness(SimpleNamespace):
    """The real ToolMilling methods, without the application GUI."""
    def __getattr__(self, name):
        member = getattr(ToolMilling, name)
        # class constants (Geometry.SCAN_BAND_LINES) are values, not methods
        return member.__get__(self, type(self)) if hasattr(member, '__get__') else member

def preprocessor(name):
    return getattr(importlib.import_module('preprocessors.' + name), name)()

def tool_data(app, uid, pp, min_power=0):
    data = {key: value for key, value in app.options.items() if key.startswith('tools_mill_')}
    data.update(tools_mill_tooldia=0.1 * uid, tools_mill_offset_type=0, tools_mill_ppname_g=pp,
                tools_mill_travelz=2.0, tools_mill_multidepth=False, tools_mill_toolchange=False,
                tools_mill_optimization_type='R', tools_mill_feedrate=100.0 * uid, tools_mill_min_power=min_power,
                # each tool is told apart by its Cut Z (mill) or by its power (laser)
                tools_mill_cutz=0.0 if 'laser' in pp else -0.05 * uid, tools_mill_spindlespeed=100 * uid)
    return data

def milling_job(n_tools, pp='default', multigeo=False, min_power=(), from_tcl=False, data=None, per_tool=None,
                attrs=None, separate_selection=False, ui=None, travels=None, drop=()):
    """The job of the real Milling code. data / per_tool: tool data of every tool / of the tool uid; attrs: methods
    of the plugin to replace; separate_selection: the selected tools are a copy of the tools of the object, as in the
    plugin; travels: receives the start point of every travel. A job that fails comes back as 'fail'."""
    app = make_app()
    app.preprocessors = {name: preprocessor(name) for name in ('default', pp)}
    app.dec_format = lambda value, decimals: round(value, decimals)

    def travel_coordinates(**kw):
        if travels is not None:
            travels.append(kw['start_point'])
        return [(None, kw['end_point'])]

    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[], travel_coordinates=travel_coordinates)
    app.regFK, app.ui, app.inform_no_echo = Mock(), Mock(), Mock()
    tools = {uid: {'tooldia': 0.1 * uid, 'solid_geometry': list(PATHS),
                   'data': dict(tool_data(app, uid, pp, min_power[uid - 1] if min_power else 0),
                                **(data or {}), **(per_tool or {}).get(uid, {}))}
             for uid in range(1, n_tools + 1)}
    for tool in tools.values():
        for key in drop:
            tool["data"].pop(key, None)
    source = SimpleNamespace(multigeo=multigeo, tools=tools, solid_geometry=list(PATHS),
                             obj_options=dict(tools[1]['data'], name='board', xmin=0, ymin=0, xmax=30, ymax=10))
    jobs = []

    def new_object(kind, name, initializer, **kwargs):
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            job = CNCJobObject(name, app)
        result = initializer(job, app)
        if result == 'fail' or result is False:
            return 'fail'
        assert result is None
        jobs.append(job)
        return job

    milling = MillingHarness(app=app, target_obj=source, units='MM', decimals=4, circle_steps=64,
                             sel_tools=deepcopy_keep_geometry(tools) if separate_selection else tools,
                             new_object=new_object, **(attrs or {}),
                             ui=ui or SimpleNamespace(toolchange_cb=SimpleNamespace(get_value=lambda: False)))
    result = milling.generate_cnc_job_handler(from_tcl=from_tcl, use_thread=False, plot=False)
    if not jobs:
        assert result == 'fail'
        return app, 'fail'
    job = jobs[0]
    job.gc_header = ''
    return app, job

def exported(job):
    """The text of the exported file (File -> Save CNC Code, Tcl write_gcode, View Source)."""
    return job.export_gcode(to_file=True).getvalue()

def tool_marks(gcode, laser=False):
    """How many paths each tool has in the G-code: the plunges to its Cut Z (mill) or the laser on lines with its
    power (laser)."""
    if laser:
        return Counter(re.findall(r'^M0?3 S(\d+)$', gcode, re.MULTILINE))
    return Counter(re.findall(r'^G0?1 Z(-\d+\.\d+)$', gcode, re.MULTILINE))

def cut_moves(gcode):
    return len(re.findall(r'^G0?1 X', gcode, re.MULTILINE))

def expected_marks(tool_numbers, laser=False):
    return Counter({('%d' % (100 * k) if laser else '%.4f' % (-0.05 * k)): N_PATHS for k in tool_numbers})

@pytest.mark.parametrize('pp', ['GRBL_laser'])
@pytest.mark.parametrize('n_tools', [2])
def test_the_exported_file_has_every_path_once(n_tools, pp):
    __, job = milling_job(n_tools, pp)
    laser = 'laser' in pp
    body = ''.join(tool['gcode'] for tool in job.tools.values())
    for text in (exported(job), job.source_file):
        assert tool_marks(text, laser) == expected_marks(range(1, n_tools + 1), laser)
        assert cut_moves(text) == CUT_MOVES * n_tools
        # the start code, then the tools in their order, each one once
        assert text.count(body) == 1
        assert text.index(job.gc_start) < text.index(body)
    assert job.source_file == job.gc_start + body

@pytest.mark.parametrize('n_tools', [2])
def test_multi_geometry_job_has_every_path_once(n_tools):
    # the multi-geometry path makes each tool's G-code apart (geometry_tool_gcode_gen()); not changed
    __, job = milling_job(n_tools, multigeo=True, from_tcl=True)
    body = ''.join(tool['gcode'] for tool in job.tools.values())
    for k, tool in job.tools.items():
        assert tool_marks(tool['gcode']) == expected_marks([k]), k
    assert job.source_file == body
    assert tool_marks(exported(job)) == expected_marks(range(1, n_tools + 1))
    assert exported(job).count(body) == 1

def xy_cuts(gcode):
    return [(float(x), float(y)) for x, y in re.findall(r'^G0?1 X(-?[\d.]+) Y(-?[\d.]+)', gcode, re.MULTILINE)]

@pytest.mark.parametrize('multigeo', [True])
@pytest.mark.parametrize('offset', ['abc'])
def test_custom_offset_without_a_usable_value_is_a_failed_job(multigeo, offset):
    app, job = milling_job(2, multigeo=multigeo, from_tcl=True,
                           data=dict(tools_mill_offset_type=3, tools_mill_offset_value=offset))
    assert job == 'fail'
    assert not any('CNCjob created' in str(c.args[0]) for c in app.inform.emit.call_args_list)

@pytest.mark.parametrize('multigeo', [False, True])
@pytest.mark.parametrize('offset_type, offset_value', [(1, None), (3, 0.125)])
def test_isolation_centerline_marker_gets_no_second_offset(multigeo, offset_type, offset_value):
    # the generated Isolation geometry carries the centerline marker: the job must not offset it again
    isolation = dict(tools_mill_offset_type=offset_type, tools_iso_centerline=True)
    if offset_value is not None:
        isolation['tools_mill_offset_value'] = offset_value
    __, plain = milling_job(1, multigeo=multigeo, from_tcl=True, data=dict(tools_mill_offset_type=0))
    __, job = milling_job(1, multigeo=multigeo, from_tcl=True, data=isolation)
    assert job != 'fail'
    assert xy_cuts(job.tools[1]['gcode']) == xy_cuts(plain.tools[1]['gcode'])

@pytest.mark.parametrize('multigeo', [False, True])
@pytest.mark.parametrize('labels', [{'tools_mill_job_type': 2, 'tools_iso_isotype': 'full'}])
def test_an_ordinary_tool_keeps_its_offset_despite_isolation_like_labels(multigeo, labels):
    # job type 2 (the form sets it for a V tool) and the generic tools_iso_* copy are not provenance
    __, offset = milling_job(1, multigeo=multigeo, from_tcl=True,
                             data=dict(tools_mill_offset_type=1, **labels))
    __, plain = milling_job(1, multigeo=multigeo, from_tcl=True, data=dict(tools_mill_offset_type=0))
    assert offset != 'fail' and plain != 'fail'
    assert xy_cuts(offset.tools[1]['gcode']) != xy_cuts(plain.tools[1]['gcode'])

@pytest.mark.parametrize('method', [0])
def test_polishing_paints_the_box_with_margin_and_cuts_it(method):
    # the plugin works on a copy of the tools of the object: the plotted geometry of the job is the painted one
    __, job = milling_job(1, multigeo=True, from_tcl=True, separate_selection=True,
                          data=dict(tools_mill_job_type=3, tools_mill_polish_method=method, tools_mill_polish_margin=2,
                                    tools_mill_polish_overlap=10, tools_mill_tooldia=0.5))
    xs = [x for x, __ in xy_cuts(job.tools[1]['gcode'])]
    assert min(xs) < 0 and max(xs) > 30
    plotted = job.tools[1]['solid_geometry']
    assert min(g.bounds[0] for g in plotted) < 0 and max(g.bounds[2] for g in plotted) > 30

@pytest.mark.parametrize('multigeo', [False])
def test_old_tool_without_optimization_or_probe_settings_makes_a_job(multigeo):
    # Geometry objects of older versions: no optimization in the tool, no probe settings in the object
    drop = ("tools_mill_optimization_type", "tools_mill_z_p_depth", "tools_mill_probe_plate",
            "tools_mill_feedrate_probe",
            "tools_mill_search_time")
    app, job = milling_job(1, multigeo=multigeo, from_tcl=True, drop=drop)
    assert job != 'fail'

@pytest.mark.parametrize('multigeo', [False])
def test_each_tool_has_its_own_spindle_direction(multigeo):
    __, job = milling_job(2, multigeo=multigeo, from_tcl=True, per_tool={
        1: dict(tools_mill_spindledir='CW'), 2: dict(tools_mill_spindledir='CCW')})
    assert 'M03' in job.tools[1]['gcode'] and 'M04' not in job.tools[1]['gcode']
    assert 'M04' in job.tools[2]['gcode'] and 'M03' not in job.tools[2]['gcode']

def test_the_next_tool_starts_its_travel_where_the_previous_one_ended():
    travels = []
    __, job = milling_job(2, multigeo=True, from_tcl=True, travels=travels,
                          data=dict(tools_mill_area_exclusion=True))
    # 3 paths for each tool: 3 travels to the start of a path, and 1 to the end position of the last tool
    end_of_tool_1 = job.tools[1]['gcode_parsed'][-1]['geom'].coords[-1]
    assert travels[0] == (0, 0)
    assert travels[3] == pytest.approx(end_of_tool_1)
    assert end_of_tool_1 != (0, 0)

HOLES_1, HOLES_2 = [(0.0, 0.0), (10.0, 0.0)], [(20.0, 0.0)]

def excellon_paint(sizes=(2.0, 0.4), use_thread=False, mill_dia=0.5):
    """paint_excellon_cncjpb() of the real Milling code on an Excellon with 1 tool for each hole size (tool 1: the
    holes HOLES_1; tool 2: HOLES_2). Returns (app, milling, jobs, result, seed_calls)."""
    from shapely.geometry import Point
    from appPlugins.ToolMilling import MillingGen  # noqa: F401
    app = make_app()
    app.preprocessors = {'default': preprocessor('default')}
    app.dec_format = lambda value, decimals: round(value, decimals)
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[], travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    app.regFK, app.ui, app.inform_no_echo = Mock(), Mock(), Mock()
    tools = {}
    for uid, (dia, holes) in enumerate(zip(sizes, (HOLES_1, HOLES_2)), 1):
        data = dict(tool_data(app, uid, 'default'), tools_mill_milling_type='drills', tools_mill_milling_dia=mill_dia,
                    tools_mill_milling_overlap=10.0, tools_mill_milling_connect=False, tools_mill_cutz=-1.0)
        tools[uid] = {'tooldia': dia, 'drills': [Point(h) for h in holes], 'slots': [], 'data': data}
    target = SimpleNamespace(kind='excellon', tools=tools, obj_options={'name': 'holes', 'xmin': 0, 'ymin': 0, 'xmax': 20, 'ymax': 0})
    jobs, seed_calls = [], []

    def new_object(kind, name, initializer, **kwargs):
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            job = CNCJobObject(name, app)
        if initializer(job, app) == 'fail':
            return 'fail'
        jobs.append(job)
        return job

    def seed(*args, **kwargs):
        seed_calls.append(1)
        return ToolMilling.clear_polygon_seed(milling, *args, **kwargs)

    milling = MillingHarness(app=app, target_obj=target, units='MM', decimals=4, circle_steps=16, new_object=new_object,
                             sel_tools=deepcopy_keep_geometry(tools), clear_polygon_seed=seed)
    result = milling.paint_excellon_cncjpb(tools_dict=deepcopy_keep_geometry(tools), from_tcl=True, plot=False,
                                           use_thread=use_thread)
    return app, milling, jobs, result, seed_calls

def messages(app):
    return ' '.join(str(c.args[0]) for c in app.inform.emit.call_args_list if c.args)

def test_excellon_paint_job_clears_the_holes_and_names_the_skipped_tools():
    app, __, jobs, result, __ = excellon_paint()
    assert result is None and len(jobs) == 1
    job = jobs[0]
    # tool 2 has holes of 0.4: smaller than the milling bit of 0.5, nothing to clear
    assert list(job.tools) == [1] and job.used_tools == [1]
    cuts = xy_cuts(job.source_file)
    assert cuts and all(min(abs(x - hx) for hx, __ in HOLES_1) < 1.5 for x, __ in cuts)
    assert '0.4' in messages(app)

@pytest.mark.parametrize('multigeo', [False])
@pytest.mark.parametrize('optimization, last_cut', [('N', (30.0, 10.0)), ('R', (20.0, 0.0))])
def test_the_optimization_of_the_tool_orders_its_paths(multigeo, optimization, last_cut):
    app, job = milling_job(1, multigeo=multigeo, from_tcl=True, data=dict(tools_mill_optimization_type=optimization))
    assert xy_cuts(job.source_file)[-1] == last_cut
    # the application option is not left changed
    assert app.options['tools_mill_optimization_type'] == 'R'

@pytest.mark.parametrize('multigeo', [False, True])
def test_cut_z_zero_fails_cleanly(multigeo):
    # Issue #40: a Cut Z of 0 with a non-laser preprocessor is a clean failure and no job, not an unpack error
    app, job = milling_job(2, multigeo=multigeo, from_tcl=True, data=dict(tools_mill_cutz=0.0))
    assert job == 'fail'
    messages = ' '.join(str(call.args[0]) for call in app.inform.emit.call_args_list)
    assert 'unpack' not in messages


# ==============================================================================================================
# test_milling_lifecycle
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

class MillingHarness_milling_lifecycle(SimpleNamespace):
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
    milling = MillingHarness_milling_lifecycle()
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
    tool = MillingHarness_milling_lifecycle()
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


# ==============================================================================================================
# test_milling_old_tool_data: The CNC job of the Milling plugin (and of the Tcl 'cncjob' command) reads the 'tools_mill_*' options from the tool
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

MILL_OPTIONS = {k: v for k, v in AppDefaults.factory_defaults.items() if k.startswith('tools_mill_')}

# the options added in 2023-12: the tool data of a project saved before does not have them
ADDED_2023_12 = ('tools_mill_laser_on', 'tools_mill_z_p_depth')

# application options that differ from the settings saved in the FlatCAM 8.994 tools below
OTHER_OPTIONS = dict(tools_mill_cutz=-0.5, tools_mill_multidepth=False, tools_mill_depthperpass=0.3,
                     tools_mill_travelz=5.0, tools_mill_feedrate=300.0, tools_mill_feedrate_z=30.0,
                     tools_mill_ppname_g='GRBL_laser', tools_mill_spindlespeed=500, tools_mill_endz=25.0,
                     tools_mill_tooldia=2.4, tools_mill_offset_type=2, tools_mill_job_type=3, tools_mill_tool_shape=5,
                     tools_mill_laser_on='M4', tools_mill_min_power=7.0, tools_mill_toolchange=False)

class MillingHarness_milling_old_tool_data(SimpleNamespace):
    """Run the real controller methods without constructing its application GUI."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))

def evo_2023_tool_data():
    data = {k: deepcopy(v) for k, v in MILL_OPTIONS.items() if k not in ADDED_2023_12}
    data.update(tools_mill_tooldia=0.1, tools_mill_offset_type=0, tools_mill_job_type=0, tools_mill_cutz=-0.05,
                tools_mill_travelz=2.0, tools_mill_multidepth=False, tools_mill_toolchange=False,
                tools_mill_spindlespeed=800, tools_mill_optimization_type='R', tools_mill_ppname_g='GRBL_laser')
    # the probe depth had this name before 2023-12
    data['tools_mill_z_pdepth'] = -0.03
    return data

def flatcam_8_994_cutout_tool(**data_changes):
    """A tool of an Edge_Cuts cutout Geometry in a real FlatCAM 8.994 project: no 'tools_mill_*' key."""
    data = {'area_exclusion': False, 'area_overz': 1.0, 'area_shape': 'polygon', 'area_strategy': 'over',
            'cutz': -2.4, 'depthperpass': 1.2, 'dwell': False, 'dwelltime': 1.0, 'endxy': None, 'endz': 15.0,
            'extracut': False, 'extracut_length': 0.1, 'feedrate': 120.0, 'feedrate_rapid': 500.0, 'feedrate_z': 60.0,
            'multidepth': True, 'name': 'board-Edge_Cuts.gbr_cutout', 'optimization_type': 'R', 'plot': True,
            'ppname_g': 'default', 'spindledir': 'CW', 'spindlespeed': 0, 'startz': None, 'toolchange': False,
            'toolchangexy': [0.0, 0.0], 'toolchangez': 15.0, 'tools_cutout_tooldia': 2.4, 'tools_cutout_z': -1.8,
            'travelz': 2.0, 'vtipangle': 30.0, 'vtipdia': 0.1}
    data.update(data_changes)
    return {'offset': 'Path', 'offset_value': 0.0, 'tool_type': 'C1', 'tooldia': '1.75', 'type': 'Rough', 'data': data}

def milling_harness(tool, multigeo, **options):
    app = make_app(**options)
    # a plain dict, as in the app: a preprocessor name that it does not have raises a KeyError
    app.preprocessors = {'default': default(), 'GRBL_laser': GRBL_laser(), 'GRBL_11': GRBL_11(),
                         'Line_xyz': Line_xyz(), 'GRBL_laser_z': GRBL_laser_z()}
    app.dec_format = lambda value, decimals: round(value, decimals)
    app.exc_areas = SimpleNamespace(travel_coordinates=lambda **kw: [(None, kw['end_point'])])

    geometry = [LineString([(0, 0), (10, 0)]), LineString([(0, 5), (10, 5)])]
    tools = {1: dict(tool, solid_geometry=geometry)}
    obj_options = dict(MILL_OPTIONS, name='old_geo', xmin=0, ymin=0, xmax=10, ymax=5)
    source = SimpleNamespace(kind='geometry', multigeo=multigeo, tools=tools, solid_geometry=geometry,
                             obj_options=obj_options)
    jobs = []

    def new_object(kind, name, initializer, **kwargs):
        job = CNCjob(app, pp_geometry_name='GRBL_laser')
        job.kind, job.obj_options, job.tools, job.gc_start = kind, {'name': name}, {}, ''
        if initializer(job, app) == 'fail':
            return 'fail'
        jobs.append(job)
        return job

    milling = MillingHarness_milling_old_tool_data(app=app, target_obj=source, sel_tools=tools, units='MM', decimals=4, circle_steps=16,
                             new_object=new_object)
    return milling, jobs

def make_cnc_job(tool, multigeo, **options):
    """
    The CNC job from the saved tool data alone: from_tcl=True, without the form of the Milling plugin. No user action
    makes it like this: the Tcl 'cncjob' command first writes its arguments (or the application options) into the main
    keys of each tool (cut Z, feed rates, spindle speed, preprocessor...), and the plugin takes the general fields of a
    multi-geometry job from its form (make_plugin_cnc_job()).
    """
    milling, jobs = milling_harness(tool, multigeo, **options)
    result = milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False, from_tcl=True)
    assert result is None and len(jobs) == 1
    return jobs[0].source_file, milling

class FormField:
    """A field of the Milling plugin form."""
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return deepcopy(self.value)

    def set_value(self, value):
        self.value = deepcopy(value)

# the general fields of the Milling plugin form that its CNC job reads: widget -> the option it shows
PLUGIN_FORM = {'offset_entry': 'tools_mill_offset_value', 'toolchange_cb': 'tools_mill_toolchange',
               'toolchangez_entry': 'tools_mill_toolchangez', 'toolchangexy_entry': 'tools_mill_toolchangexy',
               'endz_entry': 'tools_mill_endz', 'endxy_entry': 'tools_mill_endxy',
               'pdepth_entry': 'tools_mill_z_p_depth', 'feedrate_probe_entry': 'tools_mill_feedrate_probe',
               'exclusion_cb': 'tools_mill_area_exclusion', 'area_shape_radio': 'tools_mill_area_shape',
               'strategy_radio': 'tools_mill_area_strategy', 'over_z_entry': 'tools_mill_area_overz',
               'pp_geo_name_cb': 'tools_mill_ppname_g'}

def make_plugin_cnc_job(tool, multigeo, form=None, **options):
    """
    The CNC job of the Milling plugin (Generate CNCJob): from_tcl=False, with its form. The form shows the application
    values (as when it showed nothing else since the plugin opened), changed by 'form' (option -> value).
    """
    milling, jobs = milling_harness(tool, multigeo, **options)
    values = dict(milling.app.options, **(form or {}))
    milling.ui = SimpleNamespace(**{widget: FormField(values[key]) for widget, key in PLUGIN_FORM.items()})
    result = milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False)
    assert result is None and len(jobs) == 1
    return jobs[0], milling

def load_plugin_target(milling):
    """Load the target through the controller and its real Geometry tools table."""
    from appGUI.GUIElements import FCTable

    milling.app.app_units = 'MM'
    milling.app.ui = SimpleNamespace(notebook=SimpleNamespace(tabText=lambda _index: 'Milling'))
    milling.app.collection = SimpleNamespace(get_by_name=lambda _name: milling.target_obj,
                                             set_all_inactive=lambda: None, set_active=lambda _name: None)
    milling.ui = SimpleNamespace(**{widget: FormField(milling.app.options[key])
                                   for widget, key in PLUGIN_FORM.items()})
    milling.ui.object_combo = QtWidgets.QComboBox()
    milling.ui.object_combo.addItem('old_geo')
    milling.ui.target_radio = FormField('geo')
    milling.ui.tools_table_mill_geo = FCTable()
    milling.ui.tools_table_mill_geo.setColumnCount(5)
    milling.ui.param_frame = QtWidgets.QFrame()
    milling.ui.plot_cb = QtWidgets.QCheckBox()
    milling.ui.generate_cnc_button = QtWidgets.QPushButton()
    milling.ui.tool_data_label = QtWidgets.QLabel()
    milling.form_fields = {key: FormField(milling.app.options[key]) for key in (
        'tools_mill_cutz', 'tools_mill_multidepth', 'tools_mill_depthperpass',
        'tools_mill_travelz', 'tools_mill_feedrate', 'tools_mill_feedrate_z', 'tools_mill_spindlespeed')}
    milling.general_form_fields = {key: getattr(milling.ui, widget) for widget, key in PLUGIN_FORM.items()}
    milling.ui_disconnect = milling.ui_connect = milling.plot_cb_handler = lambda: None
    milling.build_ui = milling.build_ui_mill
    milling.on_object_changed()

@pytest.mark.parametrize('multigeo, edit_depth', [(False, False), (True, True)])
def test_legacy_form_displays_the_generated_parameters(multigeo, edit_depth):
    tool = flatcam_8_994_cutout_tool()
    milling, jobs = milling_harness(tool, multigeo, **OTHER_OPTIONS)

    load_plugin_target(milling)

    expected = {'tools_mill_cutz': -2.4, 'tools_mill_multidepth': True, 'tools_mill_depthperpass': 1.2,
                'tools_mill_travelz': 2.0, 'tools_mill_feedrate': 120.0, 'tools_mill_feedrate_z': 60.0}
    assert {key: milling.form_fields[key].get_value() for key in expected} == expected
    assert milling.ui.pp_geo_name_cb.get_value() == 'default'
    assert milling.ui.endz_entry.get_value() == 15.0

    if edit_depth:
        field = milling.form_fields['tools_mill_cutz']
        field.set_value(-0.8)
        field.objectName = lambda: 'cutz'
        milling.sender = lambda: field
        milling.name2option = {'cutz': 'tools_mill_cutz'}
        milling.form_to_storage()

    milling.generate_cnc_job_handler(tools_dict=deepcopy(milling.target_obj.tools),
                                     toolchange=False, plot=False, use_thread=False)

    gcode = jobs[0].source_file
    assert ('G01 Z-0.8000' if edit_depth else 'G01 Z-2.4000') in gcode
    assert 'G01 F120.00' in gcode and 'G01 F60.00' in gcode
    assert 'G00 Z2.0000' in gcode and 'G00 Z15.00' in gcode
    assert jobs[0].pp_geometry_name == milling.ui.pp_geo_name_cb.get_value()
    assert 'Z-0.5000' not in gcode

@pytest.mark.parametrize('multigeo', [False, True])
def test_milling_plugin_uses_the_saved_depth_passes_and_feed_rates(multigeo):
    # Generate CNCJob in the Milling plugin, with the 'default' preprocessor in the application options and in the form;
    # the application cut Z, passes, travel Z and feed rates differ from the saved ones
    tool = flatcam_8_994_cutout_tool()

    job, _milling = make_plugin_cnc_job(tool, multigeo, **dict(OTHER_OPTIONS, tools_mill_ppname_g='default'))

    gcode = job.source_file
    assert job.pp_geometry_name == 'default'
    assert 'G00 Z2.0000' in gcode and 'G01 Z-1.2000' in gcode and 'G01 Z-2.4000' in gcode
    assert 'G01 F120.00' in gcode and 'G01 F60.00' in gcode
    for value in ('Z-0.5000', 'Z-0.3000', 'Z5.0000', 'F300.00', 'F30.00'):
        assert value not in gcode
    # a multi-geometry job takes the general fields (preprocessor, end Z, toolchange...) from the form, as for every
    # project; a single-geometry job takes the saved ones
    assert ('G00 Z25.00' if multigeo else 'G00 Z15.00') in gcode
    assert tool['data']['tools_mill_endz'] == (OTHER_OPTIONS['tools_mill_endz'] if multigeo else 15.0)

@pytest.mark.parametrize('saved, app_value, expected', [
    ('Seed', 2, 1),
    # not recognized, so the application value: a translated text (here Spanish), an index that does not exist, a bool
    ('Semilla', 2, 2), (7, 1, 1), (True, 2, 2)])
def test_saved_polish_method(saved, app_value, expected):
    # the development builds of 21-28.10.2020 saved the text of the polish method combo box; the FlatCAM 8.994 release
    # and FlatCAM Evo save the index
    tool = flatcam_8_994_cutout_tool(polish_method=saved)

    gcode, _milling = make_cnc_job(tool, True, **dict(OTHER_OPTIONS, tools_mill_polish_method=app_value))

    assert tool['data']['tools_mill_polish_method'] == expected
    assert type(tool['data']['tools_mill_polish_method']) is int
    # the other saved settings are kept
    assert 'G01 Z-2.4000' in gcode and 'G01 F120.00' in gcode


# ==============================================================================================================
# test_milling_target_settings: Issue #41: the Milling plugin took the general job settings of a multi-geometry job (preprocessor, End Z, toolchange,
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

PER_TOOL = ('tools_mill_cutz', 'tools_mill_multidepth', 'tools_mill_depthperpass', 'tools_mill_travelz',
            'tools_mill_feedrate', 'tools_mill_feedrate_z', 'tools_mill_spindlespeed')

LASER = dict(tools_mill_ppname_g='GRBL_laser', tools_mill_endz=20.0, tools_mill_toolchangez=30.0,
             tools_mill_cutz=0.0, tools_mill_feedrate=1000.0)

MILL = dict(tools_mill_ppname_g='default', tools_mill_endz=15.0, tools_mill_toolchangez=12.0,
            tools_mill_cutz=-1.8, tools_mill_feedrate=120.0)

class QtMilling(QtCore.QObject):
    """The real ToolMilling methods on a QObject: the form's signals reach form_to_storage() with their sender()."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))

@pytest.fixture(autouse=True)
def slot_errors(monkeypatch):
    """PyQt6 aborts the process on an exception in a slot: report it as a test failure instead."""
    import sys
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, value, tb: errors.append(value))
    yield
    assert not errors, errors

def saved_tool(**changes):
    geometry = [LineString([(0, 0), (10, 0)]), LineString([(0, 5), (10, 5)])]
    data = dict(deepcopy(MILL_OPTIONS), tools_mill_tooldia=1.0, tools_mill_offset_type=0, tools_mill_job_type=0,
                tools_mill_multidepth=False, tools_mill_optimization_type='R', tools_mill_toolchange=False,
                tools_mill_area_exclusion=False)
    data.update(changes)
    return {'tooldia': 1.0, 'solid_geometry': geometry, 'data': data}

def make_object(name, multigeo=True, tool=None, **changes):
    tool = tool or saved_tool(**changes)
    tool.setdefault('solid_geometry', [LineString([(0, 0), (10, 0)])])
    return SimpleNamespace(kind='geometry', multigeo=multigeo, tools={1: tool}, solid_geometry=tool.get('solid_geometry', []),
                           obj_options=dict(tool['data'], name=name, xmin=0, ymin=0, xmax=10, ymax=5))

def plugin(objects, multigeo=True, **options):
    """
    The Milling plugin with its real form and signals, and several Geometry targets. Selecting a target in the Target
    combo box runs on_object_changed(), as the project tree selection does.
    """
    from unittest.mock import MagicMock
    from PyQt6 import QtGui
    from appPlugins.ToolMilling import MillingUI

    harness, jobs = milling_harness(saved_tool(), multigeo, **options)
    milling = QtMilling()
    vars(milling).update(vars(harness))
    app = milling.app
    app.app_units = 'MM'
    app.resource_location = os.path.abspath('assets/resources')
    app.proj_selection_changed = MagicMock()
    app.exc_areas = MagicMock(exclusion_areas_storage=[], travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    app.ui = SimpleNamespace(notebook=SimpleNamespace(tabText=lambda _index: 'Milling'))
    app.collection = QtGui.QStandardItemModel()
    app.collection.get_by_name = lambda name: objects[name]
    app.collection.set_all_inactive = lambda: None
    app.collection.set_active = lambda _name: None
    milling.ui = MillingUI(layout=QtWidgets.QVBoxLayout(), app=app, name='Milling')
    ui = milling.ui
    ui.object_combo = QtWidgets.QComboBox()      # the app's combo shows the project tree; this one the test targets
    ui.object_combo.addItems(list(objects))
    ui.target_radio.set_value('geo')
    ui.pp_geo_name_cb.clear()
    ui.pp_geo_name_cb.addItems(list(app.preprocessors))
    ui.pp_geo_name_cb.set_value(app.options['tools_mill_ppname_g'])
    milling.form_fields = {
        "tools_mill_offset_type": ui.offset_type_combo, "tools_mill_offset_value": ui.offset_entry,
        "tools_mill_tool_shape": ui.tool_shape_combo, "tools_mill_job_type": ui.job_type_combo,
        "tools_mill_cutz": ui.cutz_entry, "tools_mill_multidepth": ui.mpass_cb,
        "tools_mill_depthperpass": ui.maxdepth_entry, "tools_mill_travelz": ui.travelz_entry,
        "tools_mill_feedrate": ui.xyfeedrate_entry, "tools_mill_feedrate_z": ui.feedrate_z_entry,
        "tools_mill_feedrate_rapid": ui.feedrate_rapid_entry, "tools_mill_spindlespeed": ui.spindlespeed_entry,
    }
    milling.general_form_fields = {
        "tools_mill_toolchange": ui.toolchange_cb, "tools_mill_toolchangez": ui.toolchangez_entry,
        "tools_mill_toolchangexy": ui.toolchangexy_entry, "tools_mill_endz": ui.endz_entry,
        "tools_mill_endxy": ui.endxy_entry, "tools_mill_z_p_depth": ui.pdepth_entry,
        "tools_mill_feedrate_probe": ui.feedrate_probe_entry, "tools_mill_ppname_g": ui.pp_geo_name_cb,
        "tools_mill_area_exclusion": ui.exclusion_cb, "tools_mill_area_shape": ui.area_shape_radio,
        "tools_mill_area_strategy": ui.strategy_radio, "tools_mill_area_overz": ui.over_z_entry,
        "tools_mill_optimization_type": ui.opt_algorithm_radio, "tools_mill_search_time": ui.opt_time_entry,
    }
    milling.name2option = {widget.objectName(): key for key, widget in
                           {**milling.form_fields, **milling.general_form_fields}.items() if widget.objectName()}
    milling.plot_cb_handler = lambda: None
    milling.jobs = jobs
    milling.ui_connect()
    return milling

def select(milling, name):
    milling.ui.object_combo.setCurrentText(name)
    milling.on_object_changed()

def generate(milling):
    """Generate CNCJob for the selected target: all its tools, as on_generate_cnc_from_geo() selects them."""
    milling.sel_tools.clear()
    milling.sel_tools.update(deepcopy(milling.target_obj.tools))
    milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False)
    return milling.jobs[-1]

def form_values(milling, keys):
    fields = {**milling.form_fields, **milling.general_form_fields}
    return {key: fields[key].get_value() for key in keys}

def test_multigeo_job_uses_the_settings_of_the_selected_target():
    objects = {'A': make_object('A', **LASER), 'B': make_object('B', **MILL)}
    milling = plugin(objects)
    select(milling, 'A')
    assert 'laser' in generate(milling).pp_geometry_name
    select(milling, 'B')
    job = generate(milling)
    assert job.pp_geometry_name == 'default'
    assert 'G01 Z-1.8000' in job.source_file

def test_form_edit_goes_to_the_target_only():
    objects = {'A': make_object('A', **LASER), 'B': make_object('B', **MILL)}
    milling = plugin(objects)
    select(milling, 'B')
    milling.general_form_fields['tools_mill_endz'].set_value(33.0)
    field = milling.general_form_fields['tools_mill_endz']
    field.objectName = lambda: 'endz'
    milling.sender = lambda: field
    milling.name2option = {'endz': 'tools_mill_endz'}
    milling.form_to_storage()
    assert objects['B'].tools[1]['data']['tools_mill_endz'] == 33.0
    assert objects['A'].tools[1]['data']['tools_mill_endz'] == 20.0
    select(milling, 'A')
    assert form_values(milling, ['tools_mill_endz']) == {'tools_mill_endz': 20.0}
    assert generate(milling).pp_geometry_name == 'GRBL_laser'

def tools_milling(n_tools=2, diameters=None):
    """The plugin with one Geometry target of n tools; the table shows them."""
    tools = {}
    for uid in range(1, n_tools + 1):
        tool = saved_tool(tools_mill_tooldia=float(uid))
        tool['tooldia'] = float(uid)
        tools[uid] = tool
    obj = make_object('A')
    obj.tools = tools
    obj.build_ui = lambda: None
    obj.ser_attrs = []
    milling = plugin({'A': obj})
    milling.build_ui_sig = SimpleNamespace(emit=milling.build_ui)
    milling.default_data = dict(MILL_OPTIONS, tools_mill_tooldia=1.0)
    milling.app.plot_all = lambda *a, **k: None
    select(milling, 'A')
    return milling, obj

def select_rows(milling, rows):
    table = milling.ui.tools_table_mill_geo
    table.clearSelection()
    for row in rows:
        table.selectionModel().select(
            table.model().index(row, 0),
            QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows)

def test_delete_removes_only_the_selected_rows():
    milling, obj = tools_milling(3)
    select_rows(milling, [0, 2])
    milling._on_tool_delete()
    assert sorted(obj.tools) == [2]

def v_index(milling):
    return milling.ui.tool_shape_combo.findText('V')

def click_row(milling, row):
    table = milling.ui.tools_table_mill_geo
    table.setCurrentCell(row, 0)
    milling.update_ui()

def test_clicking_a_row_keeps_the_cut_z_of_every_tool():
    milling, obj = tools_milling(2)
    obj.tools[1]['data'].update(tools_mill_tool_shape=v_index(milling), tools_mill_cutz=-0.2, tools_mill_vtipdia=0.1,
                                tools_mill_vtipangle=30.0)
    obj.tools[2]['data'].update(tools_mill_tool_shape=0, tools_mill_cutz=-1.0)
    click_row(milling, 0)
    click_row(milling, 1)
    assert obj.tools[2]['data']['tools_mill_cutz'] == -1.0
    assert obj.tools[1]['data']['tools_mill_cutz'] == -0.2
    assert milling.ui.cutz_entry.get_value() == -1.0
    assert milling.ui.tipdia_entry.isHidden()

def test_v_tip_edit_recomputes_the_cut_z_of_all_selected_v_tools():
    milling, obj = tools_milling(3)
    for uid in (1, 2):
        obj.tools[uid]['data'].update(tools_mill_tool_shape=v_index(milling), tools_mill_vtipangle=90.0)
    obj.tools[3]['data'].update(tools_mill_tool_shape=0, tools_mill_cutz=-1.0)
    click_row(milling, 0)
    select_rows(milling, [0, 1, 2])
    milling.ui.tipangle_entry.set_value(90.0)
    milling.ui.tipdia_entry.set_value(0.5)
    assert obj.tools[1]['data']['tools_mill_cutz'] == pytest.approx(-0.25)
    assert obj.tools[2]['data']['tools_mill_cutz'] == pytest.approx(-0.75)
    assert obj.tools[3]['data']['tools_mill_cutz'] == -1.0

def test_dragging_geometry_rows_reorders_the_tools_of_the_object():
    milling, obj = tools_milling(3)
    table = milling.ui.tools_table_mill_geo
    milling.ui_disconnect()
    for row, uid in enumerate((3, 1, 2)):
        table.item(row, 3).setText(str(uid))
    milling.on_geo_rebuild_ui()
    assert [obj.tools[uid]['tooldia'] for uid in (1, 2, 3)] == [3.0, 1.0, 2.0]

def told(milling, level):
    return ' '.join(str(c.args[0]) for c in milling.app.inform.emit.call_args_list
                    if c.args and str(c.args[0]).startswith('[%s' % level))

@pytest.mark.parametrize('changes, text', [
    (dict(tools_mill_tooldia=0.0), 'diameter must be above zero'),
    (dict(tools_mill_multidepth=True, tools_mill_depthperpass=0.0), 'Depth per Pass must be above zero'),
])
@pytest.mark.parametrize('multigeo', [True])
def test_a_tool_that_cannot_make_a_job_blocks_the_job(changes, text, multigeo):
    objects = {'A': make_object('A', multigeo=multigeo, **changes)}
    milling = plugin(objects, multigeo=multigeo)
    select(milling, 'A')
    milling.sel_tools.clear()
    milling.sel_tools.update(deepcopy(milling.target_obj.tools))
    assert milling.generate_cnc_job_handler(toolchange=False, plot=False, use_thread=False) == 'fail'
    assert not milling.jobs
    assert text in told(milling, 'ERROR')


# ==============================================================================================================
# test_milling_apply_param_to_all: Milling Tool: 'Apply parameters to all tools' reads the tools table of the current target.
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

class MillingHarness_milling_apply_param_to_all(SimpleNamespace):
    """Run the real controller methods without constructing its application GUI."""
    def __getattr__(self, name):
        method = getattr(ToolMilling, name)
        return method.__get__(self, type(self))

@pytest.fixture
def qapp_milling_apply_param_to_all():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def tools_table(uids, totals=False):
    """A tools table with the tool UID in column 3; the Excellon table ends with 2 rows of totals (no UID)."""
    table = QtWidgets.QTableWidget(len(uids) + (2 if totals else 0), 5)
    for row, uid in enumerate(uids):
        table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(row + 1)))
        table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(uid)))
    if totals:
        for row in (len(uids), len(uids) + 1):
            table.setItem(row, 1, QtWidgets.QTableWidgetItem('Total'))
    return table

def harness(target, geo_table, exc_table, tool_uids):
    events, calls = [], []
    milling = MillingHarness_milling_apply_param_to_all()
    milling.app = SimpleNamespace(log=SimpleNamespace(debug=lambda *_: None),
                                  inform=SimpleNamespace(emit=events.append))
    milling.ui = SimpleNamespace(target_radio=SimpleNamespace(get_value=lambda: target),
                                 tools_table_mill_geo=geo_table, tools_table_mill_exc=exc_table)
    milling.target_obj = SimpleNamespace(tools={uid: {'data': {'tools_mill_feedrate': 100.0 * uid}}
                                                for uid in tool_uids})
    milling.ui_disconnect = lambda: calls.append('disconnect')
    milling.ui_connect = lambda: calls.append('connect')
    return milling, events, calls

def feedrates(milling):
    return [record['data']['tools_mill_feedrate'] for record in milling.target_obj.tools.values()]

@pytest.mark.parametrize('exc_uids', [[], [1, 2, 3]], ids=['new_tool', 'after_excellon'])
def test_geometry_target_uses_the_geometry_tools_table(qapp_milling_apply_param_to_all, exc_uids):
    # the Excellon table is hidden for a Geometry target: it holds its 2 rows of totals (a tool opened on a
    # Geometry) or the rows of the Excellon object milled before
    geo_table = tools_table([1, 2, 3])
    geo_table.setCurrentCell(1, 0)
    milling, events, calls = harness('geo', geo_table, tools_table(exc_uids, totals=True), [1, 2, 3])
    milling.on_apply_param_to_all_clicked()
    assert feedrates(milling) == [200.0, 200.0, 200.0]
    assert calls == ['disconnect', 'connect']
    assert any('applied to all tools' in str(msg) for msg in events)

def test_excellon_target_uses_the_excellon_tools_table(qapp_milling_apply_param_to_all):
    exc_table = tools_table([1, 2], totals=True)
    exc_table.setCurrentCell(1, 0)
    milling, events, calls = harness('exc', tools_table([]), exc_table, [1, 2])
    milling.on_apply_param_to_all_clicked()
    assert feedrates(milling) == [200.0, 200.0]
    assert calls == ['disconnect', 'connect']
