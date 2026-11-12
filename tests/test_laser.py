"""Merged from: test_laser_safety: Laser Min Power warning.; test_laser_drill_travel: Drilling tool G-code with a laser preprocessor: the laser is OFF for every travel.
"""

import importlib
import pytest
import re
from types import SimpleNamespace
from unittest.mock import Mock
from shapely.geometry import LineString, Point
from camlib import AttrDict, CNCjob, Geometry
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b as make_app, make_excellon_0b as make_excellon


# ==============================================================================================================
# test_laser_safety: Laser Min Power warning.
# ==============================================================================================================

LASER_PREPROCESSORS = ['GRBL_laser', 'GRBL_laser_z', 'default_laser', 'Marlin_laser_FAN_pin',
                       'Marlin_laser_Spindle_pin', 'Marlin_laser_z', 'grbl_laser_eleks_drd']

def laser_app(pp_name='GRBL_laser', **option_overrides):
    app = make_app(**option_overrides)
    app.preprocessors[pp_name] = getattr(importlib.import_module('preprocessors.%s' % pp_name), pp_name)()
    app.dec_format = round
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[],
                                    travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    return app

def laser_warnings(app):
    return [c.args[0] for c in app.inform.emit.call_args_list
            if c.args and str(c.args[0]).startswith('[WARNING]') and 'laser OFF' in str(c.args[0])]

def laser_job(app, obj_type='Geometry', **kwargs):
    job = CNCjob(app, **kwargs)
    # the bounds are read by the start code (the CNC job tools set them before the G-code is made)
    job.obj_options = {'name': 'job', 'type': obj_type, 'xmin': 0, 'ymin': 0, 'xmax': 7, 'ymax': 0}
    job.coords_decimals = 4
    job.fr_decimals = 2
    return job

def mill_paths():
    return [LineString([(0, 0), (2, 0)]), LineString([(5, 0), (7, 0)])]

def milling_tool_job(pp_name, min_power):
    app = laser_app(pp_name)
    job = laser_job(app)
    data = dict(AppDefaults.factory_defaults)
    data.update(tools_mill_tooldia=0.1, tools_mill_offset_value=0, tools_mill_optimization_type='R',
                tools_mill_ppname_g=pp_name, tools_mill_min_power=min_power, tools_mill_cutz=0,
                tools_mill_spindlespeed=500, tools_mill_multidepth=False)
    tools = {1: {'data': data, 'tooldia': 0.1, 'solid_geometry': mill_paths()}}
    gcode, __ = job.geometry_tool_gcode_gen(1, tools, (0, 0), (0, 0), 0.001)
    return app, gcode

def test_warning_only_for_gcode_with_laser_off_lines():
    app = laser_app()
    job = CNCjob(app)
    job.laser_min_power = 250

    job.laser_min_power_warning('G0 X0 Y0\nM03 S500\nG1 X2 Y0 F100\nM5\n')
    assert laser_warnings(app) == []

    job.laser_min_power_warning('G0 X0 Y0\nM03 S500\nG1 X2 Y0 F100\nM03 S250 (laser OFF)\n')
    job.laser_min_power_warning('G0 X0 Y0\nM106 S255\nG1 X2 Y0 F100\nM106 S250 ;laser OFF\n')
    msgs = laser_warnings(app)
    assert len(msgs) == 2
    assert all('250' in m for m in msgs)

@pytest.mark.parametrize('pp_name', ['GRBL_laser', 'GRBL_laser_z', 'grbl_laser_eleks_drd'])
def test_grbl_laser_activation_does_not_reuse_previous_power(pp_name):
    pp = getattr(importlib.import_module('preprocessors.%s' % pp_name), pp_name)()
    p = AttrDict(units='MM', coords_decimals=4, fr_decimals=2, feedrate=1000, feedrate_rapid=1500,
                 steps_per_circle=64, obj_options={'type': 'Geometry', 'xmin': 0, 'xmax': 1, 'ymin': 0, 'ymax': 1},
                 pp_geometry_name=pp_name, spindledir='CW', laser_min_power=250)
    methods = (pp.start_code, pp.down_code) if pp_name == 'grbl_laser_eleks_drd' else \
        (pp.spindle_code, pp.down_code)

    for laser_on_code in ('M03', 'M04'):
        p['laser_on_code'] = laser_on_code
        for power in (500, 0, 0.0, None):
            p['spindlespeed'] = power
            for method in methods:
                lines = [line.split(';')[0] for line in method(p).splitlines()
                         if line.startswith(laser_on_code)]
                # A previous S500 (or minimum travel power) must not survive zero-power activation.
                assert lines == ['%s S%d' % (laser_on_code, power or 0)]

        expected_lift = 'M05;lift' if pp_name == 'grbl_laser_eleks_drd' else \
            '%s S250 (laser OFF)\n' % laser_on_code
        assert pp.lift_code(p) == expected_lift
        p['laser_min_power'] = 0
        assert pp.lift_code(p) == ('M05;lift' if pp_name == 'grbl_laser_eleks_drd' else 'M5')
        p['laser_min_power'] = 250

@pytest.mark.parametrize('pp_name', ['Marlin_laser_FAN_pin', 'Marlin_laser_Spindle_pin', 'Marlin_laser_z'])
def test_marlin_laser_activation_sends_zero_power(pp_name):
    # without S Marlin runs M106 at full output (255) and M3 / M4 at its startup power
    pp = getattr(importlib.import_module('preprocessors.%s' % pp_name), pp_name)()
    fan = pp_name == 'Marlin_laser_FAN_pin'
    for laser_on_code in ('M03', 'M04'):
        on = 'M106' if fan else laser_on_code.replace('0', '')
        for power, s_word in ((500, 'S255' if fan else 'S500'), (0, 'S0'), (0.0, 'S0'), (None, 'S0')):
            p = AttrDict(laser_on_code=laser_on_code, spindlespeed=power)
            assert pp.spindle_code(p) == pp.down_code(p) == '%s %s' % (on, s_word)

@pytest.mark.parametrize('pp_name', ['GRBL_laser', 'Marlin_laser_FAN_pin', 'grbl_laser_eleks_drd'])
def test_the_warning_follows_the_laser_off_lines_of_each_laser_preprocessor(pp_name):
    app, gcode = milling_tool_job(pp_name, 250)
    # grbl_laser_eleks_drd does not use Min Power: it always turns the laser off with M05
    assert ('laser OFF' in gcode) is (pp_name != 'grbl_laser_eleks_drd')
    assert len(laser_warnings(app)) == (1 if 'laser OFF' in gcode else 0)

@pytest.mark.parametrize('min_power', [250])
def test_single_geometry_job_warns_when_the_laser_off_lines_keep_the_laser_on(min_power):
    app = laser_app()
    geo = Geometry(app)
    geo.solid_geometry = mill_paths()
    job = laser_job(app)
    gcode, __ = job.generate_from_geometry_2(geo, tooldia=0.1, tolerance=0.001, z_cut=0, z_move=2, feedrate=1000,
                                             feedrate_z=100, feedrate_rapid=1000, spindlespeed=500,
                                             laser_min_power=min_power, pp_geometry_name='GRBL_laser',
                                             is_first=True)
    assert ('(laser OFF)' in gcode) is bool(min_power)
    assert len(laser_warnings(app)) == (1 if min_power else 0)

@pytest.mark.parametrize('min_power', [250])
def test_drillcncjob_tcl_command_warns_when_the_laser_off_lines_keep_the_laser_on(min_power):
    from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob

    app = laser_app(tools_drill_toolchange=False)
    source = make_excellon(app, slots=())
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    app.collection.get_by_name.return_value = source
    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.excellon_tool_gcode_parse = Mock(return_value=[])

    def create(kind, name, init, **kwargs):
        assert init(job, app) != 'fail'
        return job

    app.app_obj.new_object.side_effect = create
    TclCommandDrillcncjob(app).execute({'name': 'e', 'pp': 'GRBL_laser', 'las_power': 500,
                                        'las_min_pwr': min_power}, [])
    assert ('(laser OFF)' in job.source_file) is bool(min_power)
    assert len(laser_warnings(app)) == (1 if min_power else 0)


# ==============================================================================================================
# test_laser_drill_travel: Drilling tool G-code with a laser preprocessor: the laser is OFF for every travel.
# ==============================================================================================================

# 2 tools with 2 holes each: travels from the start point, between the holes and after the tool change
DRILLS = {1: (0.8, [(5.0, 5.0), (8.0, 2.0)]), 2: (1.2, [(20.0, 10.0), (25.0, 15.0)])}

LASER_OFF_RE = re.compile(r'^(M0?5|M107|M106 S0)\b')

LASER_ON_RE = re.compile(r'^(M0?[34]|M106)\b')

def drilling_gcode(pp_name, toolchange, min_power=0):
    """
    The G-code of the tool blocks, made as the Drilling tool does: without toolchange 1 call with all the holes, with
    toolchange 1 call per tool (the last point of a tool is the first point of the next one).
    """
    app = laser_app(pp_name)
    job = CNCjob(app, pp_excellon_name=pp_name)
    job.obj_options = {'name': 'job', 'type': 'Excellon', 'xmin': 0, 'xmax': 30, 'ymin': 0, 'ymax': 20}
    job.coords_decimals = 4
    job.fr_decimals = 2

    tools = {}
    for tid, (dia, pts) in DRILLS.items():
        data = dict(AppDefaults.factory_defaults)
        data.update(tools_drill_ppname_e=pp_name, tools_drill_min_power=min_power, tools_drill_spindlespeed=800,
                    tools_drill_dwell=True, tools_drill_dwelltime=1.5)
        tools[tid] = {'tooldia': dia, 'drills': [Point(p) for p in pts], 'data': data}
    # read by the toolchange code of the preprocessors
    job.tools = {tid: {'nr_drills': len(t['drills'])} for tid, t in tools.items()}

    blocks = []
    if toolchange:
        first_pt = (0.0, 0.0)
        for tid in tools:
            job.tool = tid
            gcode, first_pt, __ = job.excellon_tool_gcode_gen(tid, tools[tid]['drills'], tools, first_pt,
                                                              is_first=tid == 1, is_last=tid == 2, opt_type='R',
                                                              toolchange=True)
            blocks.append(gcode)
    else:
        job.tool = 1
        holes = [pt for t in tools.values() for pt in t['drills']]
        gcode, __, __ = job.excellon_tool_gcode_gen(1, holes, tools, (0.0, 0.0), is_first=True, is_last=True,
                                                    opt_type='R', toolchange=False)
        blocks.append(gcode)

    # the travel line to each hole, as the preprocessor writes it
    travels = {job.doformat2(job.pp_excellon.rapid_code, x=x, y=y)
               for __, pts in DRILLS.values() for x, y in pts}
    return blocks, travels

def laser_state(line):
    """True for a laser ON line, False for a laser OFF line (the 'laser OFF' lines of Min Power too), else None."""
    if 'laser OFF' in line or LASER_OFF_RE.match(line):
        return False
    if LASER_ON_RE.match(line):
        return True
    return None

@pytest.mark.parametrize('pp_name,toolchange', [(pp, i % 2 == 1) for i, pp in enumerate(LASER_PREPROCESSORS)])
def test_the_laser_is_off_for_every_travel_and_on_at_each_hole(pp_name, toolchange):
    blocks, travels = drilling_gcode(pp_name, toolchange, 250)

    holes = 0
    for gcode in blocks:
        laser_on = False
        after_travel = False
        for line in gcode.splitlines():
            line = line.strip()
            state = laser_state(line)
            if state is not None:
                if after_travel:
                    # the hole: the down code turns the laser ON
                    assert state is True, gcode
                    holes += 1
                    after_travel = False
                laser_on = state
            elif line in travels:
                assert not laser_on, 'the laser is ON for the travel %r:\n%s' % (line, gcode)
                after_travel = True
    assert holes == sum(len(pts) for __, pts in DRILLS.values())

@pytest.mark.parametrize('toolchange', [False, True])
def test_grbl_laser_does_not_turn_the_laser_on_before_the_first_travel_of_a_tool(toolchange):
    blocks, travels = drilling_gcode('GRBL_laser', toolchange)

    assert len(blocks) == (2 if toolchange else 1)
    for gcode in blocks:
        lines = [line for line in gcode.splitlines() if line]
        first_travel = next(i for i, line in enumerate(lines) if line in travels)
        # before the fix: [..., 'M3 S800'] (laser at full power for the travel)
        assert 'M3 S800' not in lines[:first_travel]
        assert lines[first_travel - 1] == 'M5'
        assert lines[first_travel + 1] == 'M3 S800'

@pytest.mark.parametrize('pp_name,toolchange', [('default', False), ('GRBL_11', True)])
def test_other_preprocessors_still_start_the_spindle_before_the_first_travel(pp_name, toolchange):
    blocks, travels = drilling_gcode(pp_name, toolchange)

    assert len(blocks) == (2 if toolchange else 1)
    for gcode in blocks:
        lines = [line for line in gcode.splitlines() if line]
        first_travel = next(i for i, line in enumerate(lines) if line in travels)
        assert lines[first_travel - 2:first_travel] == ['M03 S800', 'G4 P1.5']
