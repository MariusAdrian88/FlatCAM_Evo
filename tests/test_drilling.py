"""Merged from: test_drilling_gen: Drilling G-code generation: camlib CNCjob.excellon_tool_gcode_gen() (Drilling plugin), tcl_gcode_from_excellon_by_tool(); test_drilling_ui: Drilling plugin UI / controller behavior: tool order, levels, selection, exclusion areas, form <-> storage.
"""

import importlib
import re
import sys
import pytest
import camlib
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from shapely.geometry import Point, Polygon
from appObjects.ExcellonObject import ExcellonObject
from appParsers.ParseExcellon import Excellon
from appPlugins.ToolDrilling import DrillingGen
from camlib import CNCjob
from defaults import AppDefaults
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from tests.helpers_objects import make_app_0b
from copy import deepcopy
from PyQt6 import QtWidgets
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj


# ==============================================================================================================
# test_drilling_gen: Drilling G-code generation: camlib CNCjob.excellon_tool_gcode_gen() (Drilling plugin), tcl_gcode_from_excellon_by_tool()
# ==============================================================================================================

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

@pytest.mark.parametrize('opt_type', ['R', 'T'])
def test_every_optimizer_drills_a_duplicate_hole_once(opt_type):
    skip_without_ortools(opt_type)
    holes = [(1.0, 1.0), (9.0, 1.0), (1.0, 1.0), (5.0, 5.0), (9.0, 1.0)]
    __, __, result = gui_run(holes, opt_type=opt_type)
    assert sorted(drilled_positions(result[0])) == [(1.0, 1.0), (5.0, 5.0), (9.0, 1.0)]

@pytest.mark.parametrize('opt_type', ['R'])
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

def permanent_drilling_gcode(areas, exclusion_owner=None):
    """The Drilling G-code of one hole at (10, 0) from (0, 0) with the permanent areas of a machine profile, the job
    option Area Exclusion off and the real router."""
    from appCommon.Common import ExclusionAreas
    app = make_app(excellon_search_time=1)
    app.exc_areas = ExclusionAreas(SimpleNamespace(app_units='MM', log=Mock()))
    job = CNCjob(app, pp_excellon_name='default')
    job.obj_options = {'name': 'job', 'type': 'Excellon', 'xmin': 0, 'xmax': 30, 'ymin': 0, 'ymax': 20}
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.permanent_areas = areas
    tools = {1: tool_dict(0.8, [(10.0, 0.0)], tools_drill_area_exclusion=False)}
    job.tools = {1: {'nr_drills': 1}}
    job.tool = 1
    return job.excellon_tool_gcode_gen(1, [Point(10.0, 0.0)], tools, (0.0, 0.0), is_first=True, is_last=True,
                                       opt_type='R', toolchange=False, exclusion_owner=exclusion_owner)[0]

def rapid_ys(gcode):
    return [float(RAPID_RE.match(line).group(2)) for line in gcode.splitlines() if RAPID_RE.match(line)]

def test_a_permanent_forbid_area_steers_the_drilling_travel_with_the_job_flag_off():
    from shapely.geometry import box
    plain = permanent_drilling_gcode([])
    assert set(rapid_ys(plain)) == {0.0}
    area = dict(obj_type='profile', shape=box(3, -1, 6, 1), strategy='forbid', overz=None, name='clamp')
    gcode = permanent_drilling_gcode([area], exclusion_owner='drilling')
    assert drilled_positions(gcode) == [(10.0, 0.0)]
    # the travel goes around the area grown by the tool radius (0.4) and 0.1 mm: it passes at |y| >= 1.5, never over Z
    assert max(abs(y) for y in rapid_ys(gcode)) >= 1.5
    assert 'G00 Z5.0000' not in gcode.splitlines()
    over = dict(area, strategy='over', overz=7.5)
    assert 'G00 Z7.5000' in permanent_drilling_gcode([over]).splitlines()

def test_no_detour_around_a_permanent_forbid_area_stops_the_drilling_job_with_the_area_name():
    from shapely.geometry import box
    wall = lambda shape, name: dict(obj_type='profile', shape=shape, strategy='forbid', overz=None, name=name)
    areas = [wall(box(-3, -3, 3, -2), 'north'), wall(box(-3, 2, 3, 3), 'south'),
             wall(box(-3, -2, -2, 2), 'west'), wall(box(2, -2, 3, 2), 'east')]
    with pytest.raises(ValueError, match='east'):
        permanent_drilling_gcode(areas)

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

def forbid_profile_options(folder):
    """The app options that activate a profile with a permanent forbid area at x 3..6, y -1..1."""
    from appCommon.machine_profile.profile import MachineProfile, save_profile
    save_profile(MachineProfile(name='bench', bed_x=200.0, bed_y=200.0, exclusions=[
        {'name': 'clamp', 'polygon': [(3, -1), (6, -1), (6, 1), (3, 1)], 'strategy': 'forbid', 'over_z': None}]),
        str(folder))
    return {'global_machine_profile': 'bench', 'global_machine_profiles_folder': str(folder)}

def test_tcl_drillcncjob_routes_around_the_permanent_areas_of_the_active_profile(tmp_path):
    from appCommon.Common import ExclusionAreas
    router = ExclusionAreas(SimpleNamespace(app_units='MM', log=Mock()))
    tools = {1: (0.8, [(10.0, 0.0)], ())}
    options = dict(tools_drill_toolchange=False)
    run = lambda **extra: tcl_run(tools, options=dict(options, **extra), travel_result=lambda kw: router.travel_coordinates(**kw))
    plain = run()[3]
    assert set(rapid_ys(plain)) == {0.0}
    around = run(**forbid_profile_options(tmp_path))[3]
    assert drilled_positions(around) == [(10.0, 0.0)]
    assert max(abs(y) for y in rapid_ys(around)) >= 1.5

def warnings(app):
    return [str(c.args[0]) for c in app.inform.emit.call_args_list if str(c.args[0]).startswith('[WARNING')]

def test_tcl_zero_feedrate_does_not_divide_by_zero():
    app, job, ret, __ = tcl_run({1: (0.8, [(1.0, 1.0)], ())},
                               options=dict(tools_drill_toolchange=False, tools_drill_feedrate_rapid=0))
    assert ret != 'fail'
    assert job.routing_time > 0
    assert any('rate' in m.lower() for m in warnings(app))

@pytest.mark.parametrize('generator', ['tcl'])
def test_estimates_count_complete_drilling_motion(generator):
    data = dict(tools_drill_toolchange=False, tools_drill_travelz=2.0, tools_drill_cutz=-1.0,
                tools_drill_endz=15.0, tools_drill_endxy=(0, 0), tools_drill_feedrate_z=60.0,
                tools_drill_feedrate_rapid=600.0, tools_drill_dwell=True, tools_drill_dwelltime=30)
    if generator == 'gui':
        __, job, result = gui_run([(5, 0)], **data)
        assert result[0] != 'fail'
    else:
        __, job, ret, code = tcl_run({1: (0.8, [(5, 0)], ())}, options=data)
        assert ret != 'fail'
    # From assumed job origin: lift 2, XY 5, plunge 3, feed retract 1, lift 2, park Z 13, XY 5.
    assert job.travel_distance == pytest.approx(31)
    assert job.routing_time == pytest.approx(27 / 600 + 4 / 60 + 30 / 60)

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

def unmarked(code):
    """The G-code without the Machine Profile marker lines of the fixed sequences."""
    return ''.join(line for line in code.splitlines(True) if 'MP:FIXED' not in line)

def test_machine_profile_toolchange_and_end_reach_the_tcl_drill_gcode(tmp_path):
    from appCommon.machine_profile.profile import MachineProfile, save_profile
    save_profile(MachineProfile(name='bench', toolchange=(12.5, 7.0, 30.0), end=(3.0, 4.0, 40.0)), str(tmp_path))
    tools = {1: (0.8, [(1.0, 1.0)], ()), 2: (1.0, [(5.0, 5.0)], ())}
    __, job, ret, code = tcl_run(tools, options=dict(tools_drill_toolchange=True, global_machine_profile='bench',
                                                     global_machine_profiles_folder=str(tmp_path)))
    __, plain, __, plain_code = tcl_run(tools, options=dict(tools_drill_toolchange=True))
    __, by_hand, __, by_hand_code = tcl_run(tools, options=dict(
        tools_drill_toolchange=True, tools_drill_toolchangexy='12.5, 7.0', tools_drill_toolchangez=30.0,
        tools_drill_endxy='3.0, 4.0', tools_drill_endz=40.0))
    assert ret != 'fail'
    assert 'X12.5000 Y7.0000' in code and 'X3.0000 Y4.0000' in code and 'Z40.0000' in code
    assert '(MP:FIXED BEGIN toolchange)' in code and '(MP:FIXED BEGIN end)' in code and 'MP:FIXED' not in by_hand_code
    assert unmarked(code) == by_hand_code != plain_code
    assert job.obj_options['machine_profile']['name'] == 'bench'
    assert 'machine_profile' not in plain.obj_options

def test_machine_profile_preprocessor_reaches_the_plugin_drill_gcode(fc, plugin, monkeypatch, tmp_path):
    from appCommon.machine_profile.profile import MachineProfile, save_profile
    save_profile(MachineProfile(name='bench', preprocessor_drill='GRBL_11'), str(tmp_path))
    open_plugin(fc, plugin, 'gen_pp_a', selected=[1], tools_drill_toolchange=False)
    plugin.ui.pp_excellon_name_cb.set_value('GRBL_11')
    by_hand, __ = generate(fc, plugin, monkeypatch)
    open_plugin(fc, plugin, 'gen_pp_b', selected=[1], tools_drill_toolchange=False)
    plugin.ui.pp_excellon_name_cb.set_value('default')
    monkeypatch.setitem(fc.options, 'global_machine_profile', 'bench')
    monkeypatch.setitem(fc.options, 'global_machine_profiles_folder', str(tmp_path))
    job, __ = generate(fc, plugin, monkeypatch)
    assert job is not by_hand and unmarked(job.gcode) == by_hand.gcode != ''
    assert 'MP:FIXED' in job.gcode and 'MP:FIXED' not in by_hand.gcode
    assert 'machine_profile' not in by_hand.obj_options
    assert job.obj_options['machine_profile']['name'] == 'bench'

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

SPOT = dict(tools_drill_spot=True, tools_drill_spot_dia=0.5, tools_drill_spot_cutz=-0.3,
            tools_drill_spot_feedrate_z=50.0)

def tool_blocks(gcode):
    """{tool number: G-code that follows its toolchange}, in the order of the job."""
    parts = re.split(r'(?m)^T(\d+)$', gcode)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]

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
    from appParsers.ParseGCode import parse_gcode
    expected = parse_gcode('(Preprocessor Excellon: default)\n' +
                           ('G20\n' if job.units.upper() == 'IN' else 'G21\n') +
                           'G0 X0 Y0 Z0\n' + job.gcode, feedrate_rapid=job.feedrate_rapid, target_units=job.units)
    assert job.travel_distance == pytest.approx(expected['travel_distance'])
    assert job.routing_time == pytest.approx(expected['routing_time'])

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


# ==============================================================================================================
# test_drilling_ui: Drilling plugin UI / controller behavior: tool order, levels, selection, exclusion areas, form <-> storage.
# ==============================================================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow

@pytest.fixture(scope='module')
def fc_drilling_ui():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return App(qapp=qapp, headless=True)

@pytest.fixture(autouse=True)
def no_plot(monkeypatch, fc_drilling_ui):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    fc_drilling_ui.exc_areas.exclusion_areas_storage.clear()
    yield
    fc_drilling_ui.exc_areas.exclusion_areas_storage.clear()

def _load(fc_drilling_ui, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, 'unify_mixed.drl'))
        obj.create_geometry()
    return fc_drilling_ui.app_obj.new_object('excellon', name, init, plot=False)

def _open(fc_drilling_ui, name):
    tool = fc_drilling_ui.drilling_tool
    tool.run(toggle=False)
    tool.ui.object_combo.setCurrentText(name)
    tool.on_object_changed()
    tool.ui.tools_table.selectAll()
    return tool

def _edit(widget, value):
    widget.set_value(value)
    widget.editingFinished.emit()

def _area(obj_type='excellon', x=0.0, strategy='around', overz=1.0, idx=1):
    area = {'obj_type': obj_type, 'shape': Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)]),
            'strategy': strategy, 'overz': overz, 'idx': idx}
    if obj_type == 'excellon':
        area['owner'] = 'drilling'
    return area

def test_beginner_level_keeps_the_stored_tool_values(fc_drilling_ui):
    exc = _load(fc_drilling_ui, 'e_ui_level')
    for uid, tl in exc.tools.items():
        tl['data'].update({'tools_drill_multidepth': True, 'tools_drill_dwell': True,
                           'tools_drill_drill_slots': True, 'tools_drill_area_exclusion': True,
                           'tools_drill_dwelltime': 3.0})
    stored = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    tool = _open(fc_drilling_ui, 'e_ui_level')
    for level in ('b', 'a', 'b'):
        tool.change_level(level)
    tool.set_tool_ui()
    tool.on_object_changed()
    assert {k: v['data'] for k, v in exc.tools.items()} == stored

    # the generation takes the safe values in Beginner and the stored ones in Advanced
    tool.change_level('b')
    safe = tool._effective_tool_data(1)
    assert not safe['tools_drill_multidepth'] and not safe['tools_drill_dwell']
    assert not safe['tools_drill_drill_slots'] and not safe['tools_drill_area_exclusion']
    assert exc.tools[1]['data']['tools_drill_multidepth'] is True
    tool.change_level('a')
    assert tool._effective_tool_data(1)['tools_drill_multidepth'] is True

@pytest.mark.parametrize('object_pp, tool_pp, expected', [
    ('default', None, 'default'),
    ('GRBL_11_no_M6', 'GRBL_11_no_M6', 'GRBL_11_no_M6'),
    ('default', 'GRBL_11_no_M6', 'GRBL_11_no_M6'),
    (None, None, 'GRBL_11'),
    ('Check_points', None, 'Check_points'),
])
def test_plugin_preprocessor_uses_saved_values_before_preferences(
        fc_drilling_ui, monkeypatch, object_pp, tool_pp, expected):
    key = 'tools_drill_ppname_e'
    exc = _load(fc_drilling_ui, 'e_ui_pp_pref')
    for storage, value in [(exc.obj_options, object_pp),
                           *[(tool['data'], tool_pp) for tool in exc.tools.values()]]:
        if value is None:
            storage.pop(key, None)
        else:
            storage[key] = value

    monkeypatch.setitem(fc_drilling_ui.options, key, 'GRBL_11')
    tool = _open(fc_drilling_ui, exc.obj_options['name'])
    assert tool.ui.pp_excellon_name_cb.get_value() == expected
    assert exc.obj_options.get(key) == object_pp
    assert all(tool['data'].get(key) == tool_pp for tool in exc.tools.values())

def test_new_excellon_drilling_uses_preferred_preprocessor(fc_drilling_ui, monkeypatch):
    monkeypatch.setitem(fc_drilling_ui.options, 'tools_drill_ppname_e', 'GRBL_11_no_M6')
    _load(fc_drilling_ui, 'e_ui_pp_new')
    tool = _open(fc_drilling_ui, 'e_ui_pp_new')
    assert tool.ui.pp_excellon_name_cb.get_value() == 'GRBL_11_no_M6'

@pytest.mark.parametrize('edit_before_save', [False, True])
def test_database_drilling_settings_survive_project_reopen(
        fc_drilling_ui, monkeypatch, tmp_path, edit_before_save):
    import json
    from appHandlers.appIO import appIO
    from appCommon.ProjectFile import load_project_file
    from appHandlers.appHistory import history_of
    from camlib import dict2obj

    fc = fc_drilling_ui
    monkeypatch.setitem(fc.options, 'excellon_autoload_db', True)
    monkeypatch.setitem(fc.options, 'tools_drill_ppname_e', 'GRBL_11_no_M6')
    exc = _load(fc, 'e_ui_db_save')
    database = tmp_path / 'tools.FlatDB'
    records = {str(uid): {'name': 'drill', 'tooldia': entry['tooldia'], 'data': {
        'tool_target': 2, 'tol_min': 0.0, 'tol_max': 0.0,
        'tools_drill_ppname_e': 'GRBL_11_no_M6', 'tools_drill_feedrate_z': 123.0,
        'tools_drill_toolchange': False}} for uid, entry in exc.tools.items()}
    records['1']['tooldia'] = 0.7
    records['1']['data'].update(tol_min=0.79, tol_max=0.81)
    database.write_text(json.dumps(records), encoding='utf-8')
    monkeypatch.setattr(fc, 'tools_database_path', lambda: str(database))

    if edit_before_save:
        tool = _open(fc, exc.obj_options['name'])
        tool.change_level('a')
        assert tool.ui.feedrate_z_entry.get_value() == 123.0
        assert exc.tools[1]['tooldia'] == 0.7
        xmin, _, xmax, _ = exc.tools[1]['solid_geometry'][0].bounds
        assert xmax - xmin == pytest.approx(0.7)
        tool.ui.pp_excellon_name_cb.set_value('Fanuc')
        _edit(tool.ui.feedrate_z_entry, 234.0)
        first_job = _generate(fc, monkeypatch, tool)
        assert first_job is not None
        assert first_job.pp_excellon_name == 'Fanuc'
        assert 'F234.00' in first_job.gcode
    else:
        # An older saved project has no DB initialization marker; its settings still take precedence.
        for entry in exc.tools.values():
            entry['data'].update(tools_drill_ppname_e='Fanuc', tools_drill_feedrate_z=234.0,
                                 tools_drill_toolchange=False)

    path = str(tmp_path / 'drilling.FlatPrj')
    io = appIO(fc)
    with monkeypatch.context() as saving:
        saving.setattr(fc.collection, 'get_list', lambda: [exc])
        assert io.save_project(path, silent=True)
    fc.check_project_file_size(filename=path, should_quit=False)
    project = load_project_file(path, object_hook=dict2obj)
    saved = project['objs'][0]
    assert all(entry['data']['tools_drill_ppname_e'] == 'Fanuc' for entry in saved['tools'].values())
    assert all(entry['data']['tools_drill_feedrate_z'] == 234.0 for entry in saved['tools'].values())

    for record in records.values():
        record['data'].update(tools_drill_ppname_e='default', tools_drill_feedrate_z=345.0)
    database.write_text(json.dumps(records), encoding='utf-8')
    if edit_before_save:
        tool = _open(fc, exc.obj_options['name'])
        assert tool.ui.pp_excellon_name_cb.get_value() == 'Fanuc'
        assert tool.ui.feedrate_z_entry.get_value() == 234.0

    restored_name = 'e_ui_db_restored_' + str(edit_before_save)
    saved['obj_options']['name'] = restored_name
    monkeypatch.setattr(fc, 'worker_task', SimpleNamespace(emit=lambda task: task['fcn'](*task['params'])))
    io.restore_project_objects(project, path, True, False)
    QtWidgets.QApplication.processEvents()
    tool = _open(fc, restored_name)
    assert tool.ui.pp_excellon_name_cb.get_value() == 'Fanuc'
    assert tool.ui.feedrate_z_entry.get_value() == 234.0
    next_job = _generate(fc, monkeypatch, tool)
    assert next_job is not None
    assert next_job.pp_excellon_name == 'Fanuc'
    assert 'F234.00' in next_job.gcode

    # Explicit reload remains available and undoable in the interactive application.
    with monkeypatch.context() as interactive:
        interactive.setattr(fc, 'headless', False)
        fc.should_we_save = False
        assert tool.on_tool_db_load()
        tool.update_ui()
        assert tool.ui.pp_excellon_name_cb.get_value() == 'default'
        assert tool.ui.feedrate_z_entry.get_value() == 345.0
        assert fc.should_we_save
        obj = tool.excellon_obj
        assert tuple(obj.obj_options[key] for key in ('xmin', 'ymin', 'xmax', 'ymax')) == pytest.approx(obj.bounds())
        assert history_of(fc).undo()
        assert tool.ui.pp_excellon_name_cb.get_value() == 'Fanuc'
        assert tool.ui.feedrate_z_entry.get_value() == 234.0

def test_apply_to_all_with_a_totals_row_current_never_blanks_the_tools(fc_drilling_ui):
    exc = _load(fc_drilling_ui, 'e_ui_all')
    tool = _open(fc_drilling_ui, 'e_ui_all')
    before = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    tool.ui.tools_table.setCurrentCell(tool.ui.tools_table.rowCount() - 1, 1)     # the 'Total Slots' row
    tool.on_apply_param_to_all_clicked()
    after = {k: v['data'] for k, v in exc.tools.items()}
    assert all(d for d in after.values())
    assert set(after[1]) == set(before[1])

def _add_areas(fc_drilling_ui, tool, areas):
    fc_drilling_ui.exc_areas.exclusion_areas_storage.extend(areas)
    tool.build_tool_ui()

def test_milling_area_on_an_excellon_is_not_a_drilling_area(fc_drilling_ui):
    _load(fc_drilling_ui, 'e_ui_ex5')
    tool = _open(fc_drilling_ui, 'e_ui_ex5')
    milling = _area('excellon', x=10)
    del milling['owner']                                    # Milling stores obj_type 'excellon' too, without owner
    mine = _area('excellon', x=20)
    _add_areas(fc_drilling_ui, tool, [milling, mine])
    assert tool.ui.exclusion_table.rowCount() == 1
    assert tool._drill_exclusion_areas() == [mine]
    tool.on_clear_area_click()
    assert fc_drilling_ui.exc_areas.exclusion_areas_storage == [milling]

def _generate(fc_drilling_ui, monkeypatch, tool):
    """Generate the CNC job of the open object. Returns the CNCJob, or None when no job was made."""
    monkeypatch.setattr(fc_drilling_ui, 'worker_task', SimpleNamespace(emit=lambda task: task['fcn'](*task['params'])))
    tool.on_generate_cnc_job()
    try:
        job = fc_drilling_ui.collection.get_by_name(tool.ui.object_combo.currentText() + '_cnc')
    except Exception:
        return None
    return job if getattr(job, 'kind', None) == 'cncjob' else None

def test_exclusion_areas_steer_the_travels_only_when_the_tool_uses_them(fc_drilling_ui, monkeypatch):
    def two_holes(name, use_areas):
        # one tool, two holes 20 apart: the travel between them crosses the area below
        exc = _load(fc_drilling_ui, name)
        for uid in [u for u in exc.tools if u != 1]:
            del exc.tools[uid]
        exc.tools[1]['drills'] = [Point(-10, 0), Point(10, 0)]
        exc.tools[1]['data'].update(tools_drill_area_exclusion=use_areas, tools_drill_toolchange=False)
        return exc

    area = dict(_area('excellon', x=0), shape=Polygon([(-6, -1), (-4, -1), (-4, 1), (-6, 1)]))
    # the reference: no exclusion area at all
    two_holes('e_job_plain', False)
    tool = _open(fc_drilling_ui, 'e_job_plain')
    tool.change_level('b')
    plain = _generate(fc_drilling_ui, monkeypatch, tool).gcode

    fc_drilling_ui.exc_areas.exclusion_areas_storage.append(area)
    # Beginner: the Exclusion areas option is hidden and off, even if the stored value is on
    two_holes('e_job_off', True)
    tool = _open(fc_drilling_ui, 'e_job_off')
    tool.change_level('b')
    assert _generate(fc_drilling_ui, monkeypatch, tool).gcode == plain

    # Advanced with the option on: the travel goes around the area
    two_holes('e_job_on', True)
    tool = _open(fc_drilling_ui, 'e_job_on')
    tool.change_level('a')
    assert _generate(fc_drilling_ui, monkeypatch, tool).gcode != plain
