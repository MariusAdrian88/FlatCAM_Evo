"""A single-geometry Milling job with 2 or more tools had the paths of the earlier tools in its G-code again.

generate_from_geometry_2() adds its G-code to the job (append=True) and returns all the G-code of the job. The
Milling tool called it once per tool on the same job, so the 'gcode' of tool 2 was tool 1 + tool 2 and the exported
file was tool 1 + (tool 1 + tool 2): the paths of tool 1 were made twice (a laser burns them twice, a mill cuts them
twice). Now each tool's 'gcode' holds only that tool and the file has every path once, in the same order. The job
itself keeps the G-code of all the tools, as before (Tcl export_gcode, the project, the levelling probe points).

The tests make the real G-code (default and GRBL_laser preprocessors) with the real Milling tool code and export it
with the real CNCJobObject.
"""

import importlib
import re
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from shapely.geometry import LineString, Polygon

from appObjects.CNCJobObject import CNCJobObject
from camlib import deepcopy_keep_geometry
from appPlugins.ToolMilling import ToolMilling
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

# 3 paths with 9 X-Y cut moves in all (4 + 4 + 1)
PATHS = [Polygon([(0, 0), (10, 0), (10, 5), (0, 5)]).exterior,
         Polygon([(20, 0), (30, 0), (30, 5), (20, 5)]).exterior,
         LineString([(0, 10), (30, 10)])]
N_PATHS = 3
CUT_MOVES = 9


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


def cut_items(parsed):
    return sum(1 for geo in parsed if geo['kind'][0] == 'C')


@pytest.mark.parametrize('pp', ['default'])
@pytest.mark.parametrize('n_tools', [2])
def test_each_tool_gcode_holds_only_that_tool(n_tools, pp):
    __, job = milling_job(n_tools, pp)
    laser = 'laser' in pp
    assert list(job.tools) == list(range(1, n_tools + 1))
    for k, tool in job.tools.items():
        assert tool_marks(tool['gcode'], laser) == expected_marks([k], laser), k
        assert cut_moves(tool['gcode']) == CUT_MOVES, k


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


def test_the_laser_min_power_warning_looks_at_each_tool_only():
    # tool 1 keeps the laser on between the cuts (Min Power 250), tool 2 turns it off (Min Power 0): 1 warning, for
    # tool 1. With the G-code of tool 1 in the G-code of tool 2 there was a second warning that said 'Min Power is 0'.
    app, job = milling_job(2, 'GRBL_laser', min_power=(250, 0))
    warnings = [c.args[0] for c in app.inform.emit.call_args_list
                if c.args and str(c.args[0]).startswith('[WARNING]') and 'laser OFF' in str(c.args[0])]
    assert len(warnings) == 1
    assert '250' in warnings[0]
    assert '(laser OFF)' in job.tools[1]['gcode']
    assert '(laser OFF)' not in job.tools[2]['gcode']


# ---------------------------------------------------------------------------------------------------------------------
# the job path of the plugin: failures, per-tool settings, travels, polishing


def xy_cuts(gcode):
    return [(float(x), float(y)) for x, y in re.findall(r'^G0?1 X(-?[\d.]+) Y(-?[\d.]+)', gcode, re.MULTILINE)]


@pytest.mark.parametrize('multigeo', [True])
@pytest.mark.parametrize('offset', ['abc'])
def test_custom_offset_without_a_usable_value_is_a_failed_job(multigeo, offset):
    app, job = milling_job(2, multigeo=multigeo, from_tcl=True,
                           data=dict(tools_mill_offset_type=3, tools_mill_offset_value=offset))
    assert job == 'fail'
    assert not any('CNCjob created' in str(c.args[0]) for c in app.inform.emit.call_args_list)


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


@pytest.mark.parametrize('multigeo', [True])
def test_tool_without_area_exclusion_does_not_route_around_the_areas(multigeo):
    travels = []
    milling_job(1, multigeo=multigeo, from_tcl=True, travels=travels, data=dict(tools_mill_area_exclusion=False))
    assert not travels
    milling_job(1, multigeo=multigeo, from_tcl=True, travels=travels, data=dict(tools_mill_area_exclusion=True))
    assert travels


# ---------------------------------------------------------------------------------------------------------------------
# Excellon: the holes are cleared with the milling bit, the paths are made by the job (not by the GUI thread)

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


def test_excellon_paint_without_a_usable_tool_is_an_error_and_no_job():
    app, __, jobs, result, __ = excellon_paint(sizes=(0.4, 0.3))
    assert result == 'fail' and not jobs
    assert '[ERROR' in messages(app)
    assert 'CNCjob created' not in messages(app)


@pytest.mark.parametrize('multigeo', [False])
@pytest.mark.parametrize('optimization, last_cut', [('N', (30.0, 10.0)), ('R', (20.0, 0.0))])
def test_the_optimization_of_the_tool_orders_its_paths(multigeo, optimization, last_cut):
    app, job = milling_job(1, multigeo=multigeo, from_tcl=True, data=dict(tools_mill_optimization_type=optimization))
    assert xy_cuts(job.source_file)[-1] == last_cut
    # the application option is not left changed
    assert app.options['tools_mill_optimization_type'] == 'R'
