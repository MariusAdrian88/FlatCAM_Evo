"""Drilling G-code generation: camlib CNCjob.excellon_tool_gcode_gen() (Drilling plugin), tcl_gcode_from_excellon_by_tool()
(Tcl drillcncjob) and appPlugins/ToolDrilling/DrillingGen.py (slots as drills, merged tools).

Run from the repository root:
    .venv\\Scripts\\python.exe -m pytest tests/test_drilling_gen.py -q -p no:cacheprovider
"""
import importlib
import re
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import Point

import camlib
from appObjects.ExcellonObject import ExcellonObject  # noqa: F401
from appParsers.ParseExcellon import Excellon
from appPlugins.ToolDrilling import DrillingGen
from camlib import CNCjob
from defaults import AppDefaults
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from tests.helpers_objects import make_app_0b

OPT_TYPES = ['R', 'T', 'No', 'B', 'M']
RAPID_RE = re.compile(r'^G00 X(-?\d+\.\d+) Y(-?\d+\.\d+)$')


def make_app(**options):
    app = make_app_0b(**options)
    app.preprocessors['default'] = importlib.import_module('preprocessors.default').default()
    app.dec_format = round
    app.pool = None
    app.travel_calls = []

    def travel_coordinates(**kw):
        app.travel_calls.append(kw)
        return app.travel_result(kw) if app.travel_result else [(None, kw['end_point'])]

    app.travel_result = None
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[], travel_coordinates=travel_coordinates)
    return app


def drilled_positions(gcode):
    """[(x, y)] of the holes: a rapid move that is followed by the plunge."""
    lines = gcode.splitlines()
    out = []
    for line, nxt in zip(lines, lines[1:]):
        found = RAPID_RE.match(line)
        if found and nxt.startswith('G01 Z'):
            out.append((float(found.group(1)), float(found.group(2))))
    return out


def tool_dict(dia, drills, slots=(), **data):
    values = dict(AppDefaults.factory_defaults)
    values.update(data)
    return {'tooldia': dia, 'drills': [Point(p) for p in drills], 'slots': [(Point(a), Point(b)) for a, b in slots],
            'solid_geometry': [], 'data': values}


# ---------------------------------------------------------------------------------------------------------------------
# Drilling plugin path: CNCjob.excellon_tool_gcode_gen()
# ---------------------------------------------------------------------------------------------------------------------

def gui_run(holes, opt_type='R', first_pt=(0.0, 0.0), toolchange=False, source_drills=None, travel_result=None,
            options=None, **data):
    app = make_app(excellon_search_time=1, **(options or {}))
    app.travel_result = travel_result
    job = CNCjob(app, pp_excellon_name='default')
    job.obj_options = {'name': 'job', 'type': 'Excellon', 'xmin': 0, 'xmax': 30, 'ymin': 0, 'ymax': 20}
    job.coords_decimals = 4
    job.fr_decimals = 2
    tools = {1: tool_dict(0.8, source_drills if source_drills is not None else holes, **data)}
    job.tools = {1: {'nr_drills': len(holes)}}
    job.tool = 1
    result = job.excellon_tool_gcode_gen(1, [Point(h) for h in holes], tools, first_pt, is_first=True, is_last=True,
                                         opt_type=opt_type, toolchange=toolchange)
    return app, job, result


def skip_without_ortools(opt_type):
    if opt_type in ('M', 'B') and not camlib.HAS_ORTOOLS:
        pytest.skip('no OR-Tools')




def test_no_optimization_drills_the_given_points_not_the_tool_drills():
    # the points are what the job drills (e.g. the drills and the slots converted to drills of the tool)
    __, __, result = gui_run([(5.0, 5.0), (7.0, 1.0)], opt_type='No', source_drills=[(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
    assert drilled_positions(result[0]) == [(5.0, 5.0), (7.0, 1.0)]


@pytest.mark.parametrize('opt_type', OPT_TYPES)
def test_every_optimizer_drills_a_duplicate_hole_once(opt_type):
    skip_without_ortools(opt_type)
    holes = [(1.0, 1.0), (9.0, 1.0), (1.0, 1.0), (5.0, 5.0), (9.0, 1.0)]
    __, __, result = gui_run(holes, opt_type=opt_type)
    assert sorted(drilled_positions(result[0])) == [(1.0, 1.0), (5.0, 5.0), (9.0, 1.0)]


@pytest.mark.parametrize('opt_type', ['R', 'M'])
def test_the_path_starts_at_the_hole_nearest_to_the_start_point(opt_type):
    skip_without_ortools(opt_type)
    holes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (10.0, 10.0)]
    __, __, result = gui_run(holes, opt_type=opt_type, first_pt=(19.0, 1.0))
    assert drilled_positions(result[0])[0] == (20.0, 0.0)


def test_travel_after_a_toolchange_starts_at_the_toolchange_position():
    app, job, result = gui_run([(50.0, 0.0)], toolchange=True, tools_drill_toolchangexy='50, 0', first_pt=(0.0, 0.0),
                               tools_drill_area_exclusion=True)
    assert app.travel_calls[0]['start_point'] == (50.0, 0.0)
    # the hole is where the machine is: nothing to travel
    assert job.measured_distance == pytest.approx(0.0)


def test_positive_cut_z_is_drilled_as_negative_with_a_warning():
    app, __, result = gui_run([(1.0, 1.0)], tools_drill_cutz=1.5)
    assert 'G01 Z-1.5000' in result[0].splitlines()
    assert any(str(c.args[0]).startswith('[WARNING]') for c in app.inform.emit.call_args_list)


def test_travel_over_an_exclusion_area_restores_the_travel_z():
    def cross(kw):
        mid = (kw['end_point'][0] - 1, kw['end_point'][1])
        return [(7.5, mid), (None, kw['end_point'])]

    __, __, result = gui_run([(5.0, 5.0)], travel_result=cross, tools_drill_travelz=2.0, tools_drill_area_exclusion=True)
    lifts = [line for line in result[0].splitlines() if line.startswith('G00 Z')]
    assert 'G00 Z7.5000' in lifts
    # after the crossing the lift is to the travel Z of the tool again
    assert lifts[lifts.index('G00 Z7.5000') + 1:][0] == 'G00 Z2.0000'


# ---------------------------------------------------------------------------------------------------------------------
# Tcl drillcncjob path: CNCjob.tcl_gcode_from_excellon_by_tool()
# ---------------------------------------------------------------------------------------------------------------------

def tcl_run(tools, args=None, options=None, travel_result=None, tool_data=None):
    """Run the Tcl command drillcncjob on an Excellon with these tools ({id: (dia, drills, slots)}); returns
    (app, job, gcode)."""
    app = make_app(**(options or {}))
    app.travel_result = travel_result
    source = Excellon(app)
    source.units = 'MM'
    source.tools = {tid: tool_dict(dia, drills, slots, **(tool_data or {})) for tid, (dia, drills, slots) in tools.items()}
    for tool in source.tools.values():
        tool['data'] = dict(tool_data or {})
    source.create_geometry()
    source.kind = 'excellon'
    source.obj_options = {'name': 'board'}
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), (0, 0, 50, 50)))
    app.collection.get_by_name.return_value = source

    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.excellon_tool_gcode_parse = Mock(return_value=[])
    outcome = {}

    def create(kind, name, init, **kwargs):
        outcome['ret'] = init(job, app)
        return job

    app.app_obj.new_object.side_effect = create
    arguments = {'name': 'board', 'pp': 'default', 'drilled_dias': 'all'}
    arguments.update(args or {})
    TclCommandDrillcncjob(app).execute(arguments, [])
    return app, job, outcome['ret'], getattr(job, 'source_file', '')


def warnings(app):
    return [str(c.args[0]) for c in app.inform.emit.call_args_list if str(c.args[0]).startswith('[WARNING')]


@pytest.mark.parametrize('opt_type', ['T'])
def test_tcl_and_gui_drill_the_same_holes_once(opt_type):
    holes = [(1.0, 1.0), (9.0, 1.0), (1.0, 1.0), (5.0, 5.0)]
    __, __, __, code = tcl_run({1: (0.8, holes, ())}, args={'opt_type': opt_type, 'drillz': -1.5},
                               options=dict(tools_drill_toolchange=False))
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (5.0, 5.0), (9.0, 1.0)]


def test_tcl_slots_off_the_slot_only_tool_is_left_out():
    slot = ((10.0, 20.0), (14.0, 20.0))
    app, __, ret, code = tcl_run({1: (1.0, [], [slot])}, options=dict(tools_drill_toolchange=False))
    assert ret == 'fail'


def test_tcl_empty_tool_is_reported_and_the_end_code_follows_the_last_tool_with_gcode():
    slot = ((10.0, 20.0), (14.0, 20.0))
    app, job, ret, code = tcl_run({1: (0.8, [(1.0, 1.0)], ()), 2: (1.0, [], [slot])},
                                  options=dict(tools_drill_toolchange=True))
    assert ret != 'fail'
    # the tool 2 has no holes to drill: the user is told, it has no G-code, the end code is in the tool 1
    assert any('2' in w and 'No G-code' in w for w in warnings(app))
    assert job.tools[2]['gcode'] == ''
    p = job.pp_excellon
    end_code = job.doformat(p.spindle_stop_code) + job.doformat(p.end_code, x=0, y=0)
    assert job.tools[1]['gcode'].endswith(end_code)


def test_tcl_over_z_travel_restores_the_job_travel_z():
    def cross(kw):
        mid = (kw['end_point'][0] - 1, kw['end_point'][1])
        return [(7.5, mid), (None, kw['end_point'])]

    __, __, __, code = tcl_run({1: (0.8, [(5.0, 5.0)], ())}, args={'travelz': 3.0}, travel_result=cross,
                               options=dict(tools_drill_toolchange=False))
    lifts = [line for line in code.splitlines() if line.startswith('G00 Z')]
    assert 'G00 Z7.5000' in lifts
    assert lifts[lifts.index('G00 Z7.5000') + 1] == 'G00 Z3.0000'


def test_tcl_positive_drillz_is_drilled_as_negative_with_a_warning():
    app, __, __, code = tcl_run({1: (0.8, [(1.0, 1.0)], ())}, args={'drillz': 1.5},
                                options=dict(tools_drill_toolchange=False))
    assert 'G01 Z-1.5000' in code.splitlines()
    assert any(w.startswith('[WARNING]') for w in warnings(app))


def test_tcl_zero_feedrate_does_not_divide_by_zero():
    __, job, ret, __ = tcl_run({1: (0.8, [(1.0, 1.0)], ())},
                               options=dict(tools_drill_toolchange=False, tools_drill_feedrate_rapid=0))
    assert ret != 'fail'
    assert job.routing_time >= 0


# ---------------------------------------------------------------------------------------------------------------------
# optimizer helpers
# ---------------------------------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------------------------------
# DrillingGen
# ---------------------------------------------------------------------------------------------------------------------

def slot_points(a, b):
    return Point(a), Point(b)


def test_slot_drills_run_from_the_start_to_the_end():
    pts = DrillingGen.process_slot_as_drills(slot_points((0, 0), (4, 0)), overlap=1.0, add_last_pt=True)
    assert [(p.x, p.y) for p in pts] == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


def test_slot_drills_without_the_last_point_stop_before_the_end():
    pts = DrillingGen.process_slot_as_drills(slot_points((0, 0), (4.5, 0)), overlap=1.0, add_last_pt=False)
    assert [(p.x, p.y) for p in pts] == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


@pytest.mark.parametrize('spacing', [-0.5])
def test_slot_drills_with_no_spacing_are_the_two_ends_only(spacing):
    pts = DrillingGen.process_slot_as_drills(slot_points((0, 0), (4, 0)), overlap=spacing, add_last_pt=True)
    assert [(p.x, p.y) for p in pts] == [(0, 0), (4, 0)]


def test_create_drill_points_reads_slot_options_for_each_tool():
    tools = {1: tool_dict(1.0, [(0, 0)], [((10, 0), (12, 0))], tools_drill_drill_slots=True,
                          tools_drill_drill_overlap=0.0, tools_drill_last_drill=True),
             2: tool_dict(2.0, [], [((10, 5), (14, 5))], tools_drill_drill_slots=False),
             3: tool_dict(1.0, [], [((0, 9), (4, 9))], tools_drill_drill_slots=True,
                          tools_drill_drill_overlap=50.0, tools_drill_last_drill=False)}
    sorted_tools = [(k, v['tooldia']) for k, v in tools.items()]
    points = DrillingGen.create_drill_points([1, 2, 3], sorted_tools, tools)

    assert [(p.x, p.y) for p in points[1]] == [(0, 0), (10, 0), (11, 0), (12, 0)]
    assert 2 not in points
    # overlap 50 % of 1.0 = 0.5 mm between the drills, no end point: the end is on the boundary
    assert [(p.x, p.y) for p in points[3]] == [(0, 9), (0.5, 9), (1, 9), (1.5, 9), (2, 9), (2.5, 9), (3, 9), (3.5, 9)]


def test_merged_tools_keep_their_ids_and_all_their_holes():
    tools = {3: tool_dict(0.8, [(0, 0)]), 5: tool_dict(1.0, [(1, 1)]), 7: tool_dict(0.8, [(2, 2)])}
    merged = DrillingGen.merge_same_diameter_tools(tools)

    assert list(merged) == [3, 5]
    assert [(p.x, p.y) for p in merged[3]['drills']] == [(0, 0), (2, 2)]
    assert len(tools[3]['drills']) == 1


# ---------------------------------------------------------------------------------------------------------------------
# Drilling plugin: on_generate_cnc_job() on a loaded Excellon object
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def fc():
    import os
    import sys
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6 import QtWidgets
    from appMain import App
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return App(qapp=qapp, headless=True)


@pytest.fixture
def plugin(fc, monkeypatch):
    from appObjects.AppObjectTemplate import FlatCAMObj
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    fc.exc_areas.exclusion_areas_storage.clear()
    yield fc.drilling_tool
    # open_plugin(selected=...) sets an instance attribute on the shared plugin; drop it so the class method returns
    fc.drilling_tool.__dict__.pop('get_selected_tools_uid', None)
    fc.exc_areas.exclusion_areas_storage.clear()


def open_plugin(fc, tool, name, selected=None, **data):
    """Load the mixed fixture (3 tools, slots on tools 2 and 3), open the plugin on it and edit the tool data."""
    import os

    def init(obj, _app):
        obj.parse_file(os.path.join(os.path.dirname(__file__), 'fixtures', 'unify', 'unify_mixed.drl'))
        obj.create_geometry()
    fc.app_obj.new_object('excellon', name, init, plot=False)
    tool.run(toggle=False)
    tool.ui.level.setChecked(True)
    tool.ui.object_combo.setCurrentText(name)
    tool.on_object_changed()
    tool.ui.tools_table.selectAll()
    for tool_data in tool.excellon_tools.values():
        tool_data['data'].update(data)
    if selected is not None:
        tool.get_selected_tools_uid = lambda: list(selected)
    return tool


def generate(fc, tool, monkeypatch):
    """Press Generate, run the job the worker got; return (job object or None, the messages shown)."""
    msgs, jobs = [], []
    fc.inform.connect(msgs.append)
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=jobs.append))
    try:
        tool.on_generate_cnc_job()
        job = None
        if jobs:
            jobs[0]['fcn'](fc)
            job = fc.collection.get_by_name(tool.ui.object_combo.currentText() + '_cnc')
    finally:
        fc.inform.disconnect(msgs.append)
    return job, msgs


def test_plugin_job_gcode_does_not_pile_up_between_jobs(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_a', selected=[1], tools_drill_toolchange=False)
    first, _ = generate(fc, plugin, monkeypatch)
    second, _ = generate(fc, plugin, monkeypatch)
    assert len(drilled_positions(first.gcode)) == 3
    assert second.gcode == first.gcode


def test_plugin_toolchange_job_has_only_the_selected_tools_with_their_ids(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_b', selected=[2, 3], tools_drill_toolchange=True, tools_drill_offset=0.5)
    job, _ = generate(fc, plugin, monkeypatch)
    assert sorted(job.tools) == [2, 3]
    assert job.tools[2]['nr_drills'] == 2 and job.tools[3]['nr_drills'] == 1
    assert job.tools[2]['offset'] == -0.5
    assert job.used_tools == [2, 3]
    assert len(drilled_positions(job.gcode)) == 3


def test_plugin_tool_without_points_is_skipped_and_the_last_tool_ends_the_job(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_c', selected=[1, 2, 3], tools_drill_toolchange=True)
    plugin.excellon_tools[1]['drills'] = []
    job, _ = generate(fc, plugin, monkeypatch)
    assert sorted(job.tools) == [2, 3]
    assert any(abs(x - 25.0) < 1e-3 and abs(y - 22.0) < 1e-3 for x, y in drilled_positions(job.gcode))
    assert job.tools[2]['gcode'] and job.tools[3]['gcode']


def test_plugin_selected_tools_without_drills_give_a_message_and_no_job(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_d', selected=[3], tools_drill_toolchange=False, tools_drill_drill_slots=False)
    plugin.excellon_tools[3]['drills'] = []
    job, msgs = generate(fc, plugin, monkeypatch)
    assert job is None
    assert any('ERROR_NOTCL' in m and 'no drill points' in m for m in msgs)
    assert any('slots of tool 3 are not drilled' in m for m in msgs)

    # a stale tool row (no such tool): a message, not an exception
    open_plugin(fc, plugin, 'gen_e', selected=[99])
    job, msgs = generate(fc, plugin, monkeypatch)
    assert job is None
    assert any('ERROR_NOTCL' in m for m in msgs)




def test_plugin_refuses_drill_points_inside_a_drilling_exclusion_area(fc, plugin, monkeypatch):
    from shapely.geometry import box
    open_plugin(fc, plugin, 'gen_h', selected=[1], tools_drill_toolchange=False, tools_drill_area_exclusion=True)
    fc.exc_areas.exclusion_areas_storage.append(
        {'obj_type': 'excellon', 'owner': 'drilling', 'shape': box(4, 4, 6, 6), 'strategy': 'around', 'overz': 1.0,
         'idx': 1})
    job, msgs = generate(fc, plugin, monkeypatch)
    assert job is None
    assert any('Drill points inside the exclusion zones' in m for m in msgs)


GOOD_Z = dict(tools_drill_travelz=2.0, tools_drill_feedrate_z=300.0)


def test_plugin_refuses_a_travel_z_or_a_feedrate_z_that_is_not_above_zero(fc, plugin, monkeypatch):
    # a Travel Z of zero or below makes the moves between the holes with the bit in the board; F0 is no feed
    for name, data, value in (('gen_k1', dict(tools_drill_travelz=-2.0), 'Travel Z'),
                              ('gen_k2', dict(tools_drill_travelz=0.0), 'Travel Z'),
                              ('gen_k3', dict(tools_drill_feedrate_z=0.0), 'Feedrate Z')):
        open_plugin(fc, plugin, name, selected=[1], tools_drill_toolchange=False, **dict(GOOD_Z, **data))
        job, msgs = generate(fc, plugin, monkeypatch)
        assert job is None
        assert any(m.startswith('[ERROR_NOTCL]') and value in m and m.endswith('(tool 1)') for m in msgs)
    # with a toolchange every tool uses its own data: the error names the tool
    open_plugin(fc, plugin, 'gen_k4', selected=[1, 2, 3], tools_drill_toolchange=True, **GOOD_Z)
    plugin.excellon_tools[3]['data']['tools_drill_travelz'] = -1.0
    job, msgs = generate(fc, plugin, monkeypatch)
    assert job is None
    assert [m.endswith('(tool 3)') for m in msgs if m.startswith('[ERROR_NOTCL]')] == [True]


def test_plugin_job_check_leaves_out_the_data_the_job_does_not_use(fc, plugin, monkeypatch):
    # without a toolchange the data of the first tool drill all the holes: the Travel Z of tool 3 is not used
    open_plugin(fc, plugin, 'gen_k5', selected=[1, 3], tools_drill_toolchange=False, **GOOD_Z)
    plugin.excellon_tools[3]['data']['tools_drill_travelz'] = -1.0
    job, __ = generate(fc, plugin, monkeypatch)
    assert job is not None and 'G00 Z2.0000' in job.gcode and 'G00 Z-1.0000' not in job.gcode
    # for a laser the Travel Z is the focus height and there is no Z feed
    open_plugin(fc, plugin, 'gen_k6', selected=[1], tools_drill_toolchange=False, tools_drill_travelz=0.0,
                tools_drill_feedrate_z=0.0)
    plugin.ui.pp_excellon_name_cb.set_value('GRBL_laser')
    try:
        job, __ = generate(fc, plugin, monkeypatch)
    finally:
        plugin.ui.pp_excellon_name_cb.set_value('default')
    assert job is not None


# ---------------------------------------------------------------------------------------------------------------------
# peck drilling, dwell at the bottom, spindle direction
# ---------------------------------------------------------------------------------------------------------------------

PECK = dict(tools_drill_cutz=-3.0, tools_drill_travelz=2.0, tools_drill_f_retract=True, tools_drill_peck=True,
            tools_drill_peck_depth=1.0, tools_drill_peck_retract=0.3)


def z_moves(gcode):
    """The Z lines of the first hole: from its rapid XY move to the next hole (or the end)."""
    lines = gcode.splitlines()
    start = next(i for i, line in enumerate(lines) if RAPID_RE.match(line))
    return [line for line in lines[start + 1:] if line.startswith(('G00 Z', 'G01 Z', 'G4'))]


def test_peck_drilling_goes_up_to_travel_z_and_down_to_the_clearance_after_each_peck():
    __, __, (gcode, __, __) = gui_run([(5.0, 5.0)], **PECK)
    assert z_moves(gcode)[:8] == ['G01 Z-1.0000', 'G00 Z2.0000', 'G00 Z-0.7000', 'G01 Z-2.0000', 'G00 Z2.0000',
                                  'G00 Z-1.7000', 'G01 Z-3.0000', 'G00 Z2.0000']


def test_peck_drilling_last_peck_is_the_cut_z_when_it_is_not_a_multiple_of_the_step():
    __, __, (gcode, __, __) = gui_run([(5.0, 5.0)], **dict(PECK, tools_drill_cutz=-2.5))
    downs = [line for line in z_moves(gcode) if line.startswith('G01')]
    assert downs == ['G01 Z-1.0000', 'G01 Z-2.0000', 'G01 Z-2.5000']


def test_peck_drilling_replaces_the_multidepth_with_a_warning():
    app, __, (gcode, __, __) = gui_run([(5.0, 5.0)], **dict(PECK, tools_drill_multidepth=True,
                                                            tools_drill_depthperpass=1.5))
    downs = [line for line in z_moves(gcode) if line.startswith('G01')]
    assert downs == ['G01 Z-1.0000', 'G01 Z-2.0000', 'G01 Z-3.0000']
    assert any('WARNING' in str(c.args[0]) and 'Peck' in str(c.args[0]) for c in app.inform.emit.call_args_list)


def test_a_peck_deeper_than_the_cut_drills_the_hole_in_one_move():
    __, __, (gcode, __, __) = gui_run([(5.0, 5.0)], **dict(PECK, tools_drill_peck_depth=5.0))
    assert z_moves(gcode)[:2] == ['G01 Z-3.0000', 'G00 Z2.0000']
    assert 'G00 Z-0.7000' not in gcode


@contextmanager
def endless_loop_guard(limit=20000):
    """An error after `limit` lines in calculate_depths(): a loop that never ends fails quickly, without the memory."""
    count = [0]

    def lines(frame, event, arg):
        count[0] += event == 'line'
        if count[0] > limit:
            raise RuntimeError('calculate_depths() does not end')
        return lines

    old = sys.gettrace()
    sys.settrace(lambda frame, event, arg: lines if frame.f_code.co_name == 'calculate_depths' else None)
    try:
        yield
    finally:
        sys.settrace(old)


def plunges(gcode):
    return [line for line in gcode.splitlines() if line.startswith('G01 Z-')]


def test_multidepth_with_a_zero_depth_per_pass_drills_in_one_pass():
    # before, the depths of a hole were never done and filled the memory (also Tcl drillcncjob -dpp 0)
    holes = [(1.0, 1.0), (5.0, 1.0), (5.0, 5.0)]
    with endless_loop_guard():
        __, __, (gcode, __, __) = gui_run(holes, tools_drill_cutz=-1.5, tools_drill_multidepth=True,
                                          tools_drill_depthperpass=0.0)
        __, __, __, tcl_code = tcl_run({1: (0.8, holes, ())}, args={'drillz': -1.5, 'dpp': 0.0},
                                       options=dict(tools_drill_toolchange=False))
    assert plunges(gcode) == ['G01 Z-1.5000'] * 3
    assert plunges(tcl_code) == ['G01 Z-1.5000'] * 3


def test_multidepth_with_a_negative_depth_per_pass_uses_it_as_positive():
    data = dict(tools_drill_cutz=-1.5, tools_drill_multidepth=True)
    with endless_loop_guard():
        __, __, (gcode, __, __) = gui_run([(1.0, 1.0)], tools_drill_depthperpass=-0.5, **data)
    assert plunges(gcode) == ['G01 Z-0.5000', 'G01 Z-1.0000', 'G01 Z-1.5000']
    assert gcode == gui_run([(1.0, 1.0)], tools_drill_depthperpass=0.5, **data)[2][0]


def test_dwell_at_the_bottom_is_done_once_at_the_final_depth_of_every_hole():
    __, __, (gcode, __, __) = gui_run([(5.0, 5.0), (9.0, 5.0)], tools_drill_cutz=-3.0, tools_drill_f_retract=True,
                                      tools_drill_dwell=False, tools_drill_dwelltime=0.5,
                                      tools_drill_dwell_bottom=True, **{'tools_drill_peck': True,
                                                                          'tools_drill_peck_depth': 1.0})
    lines = gcode.splitlines()
    assert lines.count('G4 P0.5') == 2
    for i, line in enumerate(lines):
        if line == 'G4 P0.5':
            assert lines[i - 1] == 'G01 Z-3.0000'


def test_plugin_peck_dwell_and_spindle_direction_of_a_tool_are_in_the_job(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_peck', selected=[1], tools_drill_toolchange=False, tools_drill_cutz=-3.0,
                tools_drill_travelz=2.0, tools_drill_f_retract=True, tools_drill_peck=True,
                tools_drill_peck_depth=1.0, tools_drill_peck_retract=0.3, tools_drill_dwell_bottom=True,
                tools_drill_dwelltime=0.5, tools_drill_spindledir='CCW', tools_drill_multidepth=False)
    job, __ = generate(fc, plugin, monkeypatch)
    assert 'G00 Z-0.7000' in job.gcode and 'G00 Z-1.7000' in job.gcode
    assert 'G4 P0.5' in job.gcode and 'M04' in job.gcode




# ---------------------------------------------------------------------------------------------------------------------
# Spot drilling pass
# ---------------------------------------------------------------------------------------------------------------------

SPOT = dict(tools_drill_spot=True, tools_drill_spot_dia=0.5, tools_drill_spot_cutz=-0.3,
            tools_drill_spot_feedrate_z=50.0)


def tool_blocks(gcode):
    """{tool number: G-code that follows its toolchange}, in the order of the job."""
    parts = re.split(r'(?m)^T(\d+)$', gcode)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def test_spot_tool_id_is_the_next_free_one_and_its_data_is_the_spot_parameters():
    tools = {1: tool_dict(0.8, [(1.0, 1.0)], tools_drill_cutz=-1.7, tools_drill_peck=True, tools_drill_multidepth=True),
             5: tool_dict(1.0, [(5.0, 5.0)])}
    assert DrillingGen.next_tool_id(tools) == 6
    spot = DrillingGen.spot_tool(tools[1]['data'])
    assert spot['tooldia'] == AppDefaults.factory_defaults['tools_drill_spot_dia']
    spot = DrillingGen.spot_tool(dict(tools[1]['data'], **SPOT))
    assert spot['tooldia'] == 0.5 and spot['data']['tools_drill_cutz'] == -0.3
    assert spot['data']['tools_drill_feedrate_z'] == 50.0
    assert not spot['data']['tools_drill_peck'] and not spot['data']['tools_drill_multidepth']
    assert not spot['data']['tools_drill_dwell_bottom'] and not spot['data']['tools_drill_offset']


def test_plugin_spot_pass_comes_first_over_all_the_holes_with_its_own_tool_number(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_spot', selected=[1, 2, 3], tools_drill_toolchange=True, tools_drill_cutz=-1.7, **SPOT)
    job, __ = generate(fc, plugin, monkeypatch)
    blocks = tool_blocks(job.gcode)
    assert [t for t, __ in blocks] == [4, 1, 2, 3]
    spot_code = blocks[0][1]
    assert len(drilled_positions(spot_code)) == len(drilled_positions(job.gcode)) - len(
        drilled_positions(''.join(code for __, code in blocks[1:])))
    assert len(drilled_positions(spot_code)) == 6
    assert 'G01 Z-0.3000' in spot_code and 'G01 F50.00' in spot_code and 'Z-1.7000' not in spot_code
    for __, code in blocks[1:]:
        assert 'Z-0.3000' not in code and 'F50.00' not in code
    assert 'Dia = 0.5' in job.gcode and 'Total drills for tool T4 = 6' in job.gcode
    assert job.used_tools[0] == 4 and job.tools[4]['nr_drills'] == 6 and job.tools[4]['tooldia'] == 0.5
    assert sorted(job.tools) == [1, 2, 3, 4]


def test_plugin_spot_pass_does_not_peck_and_is_off_by_default(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_spot_peck', selected=[1], tools_drill_toolchange=True, tools_drill_peck=True,
                tools_drill_peck_depth=0.2, tools_drill_f_retract=True, tools_drill_cutz=-1.0, **SPOT)
    job, __ = generate(fc, plugin, monkeypatch)
    blocks = dict(tool_blocks(job.gcode))
    assert [line for line in blocks[4].splitlines() if line.startswith('G01 Z')] == ['G01 Z-0.3000'] * 3
    assert 'Z-0.2000' in blocks[1]
    open_plugin(fc, plugin, 'gen_spot_off', selected=[1], tools_drill_toolchange=True, tools_drill_spot=False)
    job, __ = generate(fc, plugin, monkeypatch)
    assert sorted(job.tools) == [1]


def test_plugin_spot_pass_without_toolchange_is_skipped_with_a_warning(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_spot_nt', selected=[1], tools_drill_toolchange=False, **SPOT)
    job, msgs = generate(fc, plugin, monkeypatch)
    assert any('WARNING' in m and 'spot' in m.lower() and 'toolchange' in m.lower() for m in msgs)
    assert 'Z-0.3000' not in job.gcode and sorted(job.tools) == [1]




# ---------------------------------------------------------------------------------------------------------------------
# Job XY transforms: mirror -> offset -> array
# ---------------------------------------------------------------------------------------------------------------------

def xy(points):
    return sorted((round(p.x, 4), round(p.y, 4)) for p in points)


def test_transform_points_mirror_then_offset_then_array():
    src = {1: [Point(0, 0), Point(4, 2)], 2: [Point(2, 4)]}
    out = DrillingGen.transform_points(src, offset=(10, 0), mirror='x', columns=2, rows=1, pitch=(100, 0))
    # mirror X flips Y about the bbox center (y = 2); then the offset; then the array copies at +100 in X
    assert xy(out[1]) == [(10, 4), (14, 2), (110, 4), (114, 2)]
    assert xy(out[2]) == [(12, 0), (112, 0)]
    assert all(isinstance(p, Point) for pts in out.values() for p in pts)
    assert xy(src[1]) == [(0, 0), (4, 2)]




@pytest.mark.parametrize('data', [
    {'tools_drill_job_offset_xy': 'abc'}, {'tools_drill_mirror': 'z'}, {'tools_drill_job_offset_xy': (True, False)},
    {'tools_drill_mirror_point': '1,2,3'}, {'tools_drill_array_columns': 0},
    {'tools_drill_array_columns': 2, 'tools_drill_array_pitch': None}])
def test_transform_from_data_rejects_invalid_values(data):
    with pytest.raises(ValueError):
        DrillingGen.transform_job_points({1: [Point(0, 0)]}, data)


def test_transform_from_data_accepts_text_and_tuples_and_blank():
    src = {1: [Point(0, 0)]}
    out = DrillingGen.transform_job_points(src, {'tools_drill_job_offset_xy': '(1, 2)', 'tools_drill_mirror': 'no',
                                                 'tools_drill_mirror_point': '', 'tools_drill_array_columns': 2,
                                                 'tools_drill_array_rows': 1, 'tools_drill_array_pitch': (3.0, 0.0)})
    assert xy(out[1]) == [(1, 2), (4, 2)]
    assert DrillingGen.transform_job_points(src, {}) == src


def plugin_job_positions(fc, plugin, monkeypatch, **data):
    for tool_data in plugin.excellon_tools.values():
        tool_data['data'].update(data)
    name = plugin.ui.object_combo.currentText() + '_cnc'
    if fc.collection.get_by_name(name):
        fc.collection.delete_by_name(name)      # the job of the last call: the name is free again
    job, msgs = generate(fc, plugin, monkeypatch)
    return job, msgs, sorted(drilled_positions(job.gcode)) if job else None


def test_plugin_job_offset_mirror_and_array_move_the_holes_not_the_object(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_xf', selected=[1, 2, 3], tools_drill_toolchange=False, tools_drill_cutz=-1.0)
    original = {k: xy(v['drills']) for k, v in plugin.excellon_tools.items()}
    __, __, base = plugin_job_positions(fc, plugin, monkeypatch)
    cx = (min(p[0] for p in base) + max(p[0] for p in base)) / 2
    cy = (min(p[1] for p in base) + max(p[1] for p in base)) / 2

    __, __, moved = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy=(10.0, -5.0))
    assert moved == sorted((round(x + 10, 4), round(y - 5, 4)) for x, y in base)

    plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy=None)
    __, __, mirrored = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_mirror='y')
    assert mirrored == sorted((round(2 * cx - x, 4), round(y, 4)) for x, y in base)

    __, __, about = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_mirror='x',
                                         tools_drill_mirror_point=(0.0, 0.0))
    assert about == sorted((round(x, 4), round(-y, 4)) for x, y in base)

    plugin_job_positions(fc, plugin, monkeypatch, tools_drill_mirror='no', tools_drill_mirror_point=None)
    job, __, grid = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_array_columns=2,
                                         tools_drill_array_rows=3, tools_drill_array_pitch=(100.0, 50.0))
    assert sorted(set(grid)) == sorted(set((round(x + c * 100, 4), round(y + r * 50, 4))
                                            for x, y in base for c in range(2) for r in range(3)))
    assert sum(t['nr_drills'] for t in job.tools.values()) == 6 * len(base)
    assert {k: xy(v['drills']) for k, v in plugin.excellon_tools.items()} == original
    assert plugin.excellon_obj.obj_options.get('name') == 'gen_xf'


def test_plugin_job_transform_also_moves_the_spot_pass_points(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_xf_spot', selected=[1, 2, 3], tools_drill_toolchange=True, **SPOT)
    __, __, base = plugin_job_positions(fc, plugin, monkeypatch)
    job, __, moved = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy=(7.0, 3.0),
                                          tools_drill_array_columns=2, tools_drill_array_pitch=(40.0, 0.0))
    spot = dict(tool_blocks(job.gcode))[4]
    holes = sorted(drilled_positions(''.join(code for t, code in tool_blocks(job.gcode) if t != 4)))
    assert sorted(drilled_positions(spot)) == holes
    assert len(holes) == len(base)
    assert (round(base[0][0] + 7, 4), round(base[0][1] + 3, 4)) in holes


def test_plugin_job_transform_text_that_is_not_valid_warns_and_makes_no_job(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_xf_bad', selected=[1], tools_drill_toolchange=False)
    job, msgs, __ = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy='abc')
    assert job is None and any('ERROR' in m and 'offset' in m.lower() for m in msgs)
    plugin.excellon_tools[1]['data']['tools_drill_job_offset_xy'] = None


def test_plugin_beginner_level_ignores_the_job_transform(fc, plugin, monkeypatch):
    open_plugin(fc, plugin, 'gen_xf_b', selected=[1], tools_drill_toolchange=False)
    __, __, base = plugin_job_positions(fc, plugin, monkeypatch)
    plugin.ui.level.setChecked(False)
    __, __, same = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy=(10.0, 0.0),
                                        tools_drill_mirror='x', tools_drill_array_columns=2,
                                        tools_drill_array_pitch=(5.0, 0.0))
    assert same == base


# ---------------------------------------------------------------------------------------------------------------------
# more than one Excellon object in a job, holes inside drawn areas
# ---------------------------------------------------------------------------------------------------------------------

def test_object_tools_merge_by_diameter_keep_the_main_tools_and_report_conflicts():
    main = {1: tool_dict(0.8, [(0, 0)], tools_drill_cutz=-1.7), 2: tool_dict(1.0, [(1, 1)])}
    extra = {1: tool_dict(0.8, [(5, 5)], tools_drill_cutz=-2.5), 2: tool_dict(2.0, [(6, 6)], tools_drill_cutz=-3.0)}
    merged, conflicts = DrillingGen.merge_object_tools(main, [extra])

    assert sorted(merged) == [1, 2, 3]
    assert [(p.x, p.y) for p in merged[1]['drills']] == [(0, 0), (5, 5)]
    assert merged[1]['data']['tools_drill_cutz'] == -1.7
    assert merged[3]['tooldia'] == 2.0 and merged[3]['data']['tools_drill_cutz'] == -3.0
    assert conflicts == [(0.8, [1, 3], ['tools_drill_cutz'])]
    assert len(main[1]['drills']) == 1 and len(extra[1]['drills']) == 1


def second_excellon(fc, name):
    def init(obj, _app):
        obj.tools = {1: tool_dict(0.8, [(40, 40)]), 2: tool_dict(2.5, [(45, 45)])}
        obj.create_geometry()
    made = fc.app_obj.new_object('excellon', name, init, plot=False)
    made.tools[1]['data']['tools_drill_cutz'] = -2.5
    made.tools[2]['data']['tools_drill_cutz'] = -3.3


def pick_extras(tool, *names):
    for row in range(tool.ui.extra_objects_list.count()):
        item = tool.ui.extra_objects_list.item(row)
        item.setSelected(item.text() in names)


def test_plugin_job_over_two_objects_merges_tools_and_leaves_the_sources_alone(fc, plugin, monkeypatch):
    second_excellon(fc, 'f4_b')
    open_plugin(fc, plugin, 'f4_a', selected=[1, 2, 3, 4], tools_drill_toolchange=True, tools_drill_cutz=-1.0,
                tools_drill_drill_slots=False)
    msgs = []
    fc.inform.connect(msgs.append)
    try:
        pick_extras(plugin, 'f4_b')
    finally:
        fc.inform.disconnect(msgs.append)
    assert any('same diameter' in m and 'tools_drill_cutz' in m for m in msgs)
    assert sorted(plugin.excellon_tools) == [1, 2, 3, 4]
    assert plugin.excellon_tools[4]['data']['tools_drill_cutz'] == -3.3

    job, _ = generate(fc, plugin, monkeypatch)
    positions = drilled_positions(job.gcode)
    assert len(positions) == 8
    assert any(abs(x - 40) < 1e-3 and abs(y - 40) < 1e-3 for x, y in positions)
    assert any(abs(x - 45) < 1e-3 and abs(y - 45) < 1e-3 for x, y in positions)
    assert job.tools[1]['nr_drills'] == 4 and job.tools[4]['nr_drills'] == 1
    assert job.obj_options['xmax'] >= 45 and job.obj_options['ymax'] >= 45
    assert plugin.ui.object_combo.currentText() == 'f4_a'
    source = fc.collection.get_by_name('f4_a')
    assert sorted(source.tools) == [1, 2, 3] and len(source.tools[1]['drills']) == 3

    pick_extras(plugin)
    assert sorted(plugin.excellon_tools) == [1, 2, 3]


def test_plugin_drills_only_the_holes_inside_the_drawn_areas_before_the_job_transform(fc, plugin, monkeypatch):
    from shapely.geometry import box
    open_plugin(fc, plugin, 'f4_c', selected=[1], tools_drill_toolchange=False, tools_drill_drill_slots=False)
    plugin.ui.holes_radio.set_value('area')

    job, msgs = generate(fc, plugin, monkeypatch)
    assert job is None and any('ERROR_NOTCL' in m for m in msgs)

    plugin.hole_areas.exclusion_areas_storage.append({'shape': box(4, 4, 11, 6)})
    __, __, inside = plugin_job_positions(fc, plugin, monkeypatch)
    assert inside == [(5.0, 5.0), (10.0, 5.0)]
    assert fc.exc_areas.exclusion_areas_storage == []

    __, __, moved = plugin_job_positions(fc, plugin, monkeypatch, tools_drill_job_offset_xy=(100.0, 0.0))
    assert moved == [(105.0, 5.0), (110.0, 5.0)]
