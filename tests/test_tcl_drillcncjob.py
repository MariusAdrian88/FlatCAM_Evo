"""Tcl drillcncjob: the values of each tool come from the tool data of the Excellon object (as in the Drilling plugin),
a job-wide argument overrides all the tools, and the new arguments of the Drilling plugin are available.

Run from the repository root:
    .venv\\Scripts\\python.exe -m pytest tests/test_tcl_drillcncjob.py -q -p no:cacheprovider
"""
import importlib
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import Point

from appParsers.ParseExcellon import Excellon
from camlib import CNCjob
from preprocessors.GRBL_laser import GRBL_laser
from preprocessors.Toolchange_Probe_MACH3 import Toolchange_Probe_MACH3
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b

RAPID_RE = re.compile(r'^G00 X(-?\d+\.\d+) Y(-?\d+\.\d+)$')


def make_app(**options):
    app = make_app_0b(**options)
    app.preprocessors['default'] = importlib.import_module('preprocessors.default').default()
    app.dec_format = round
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


def tool_dict(dia, drills, slots=()):
    return {'tooldia': dia, 'drills': [Point(p) for p in drills], 'slots': [(Point(a), Point(b)) for a, b in slots],
            'solid_geometry': [], 'data': dict(AppDefaults.factory_defaults)}

TWO = {1: (0.8, [(1.0, 1.0)], (), {}), 2: (1.0, [(5.0, 5.0)], (), {})}


def run(tools, argv=(), options=None, pp='default', travel_result=None, extra=None):
    """The Tcl command drillcncjob on an Excellon with these tools ({id: (dia, drills, slots, tool data)}); the
    arguments are parsed like the shell does. Returns (app, job, G-code of the job)."""
    app = make_app(**{'tools_drill_toolchange': False, **(options or {})})
    app.travel_result = travel_result
    app.preprocessors['GRBL_laser'] = GRBL_laser()
    app.preprocessors['Toolchange_Probe_MACH3'] = Toolchange_Probe_MACH3()
    source = Excellon(app)
    source.units = 'MM'
    source.tools = {tid: tool_dict(dia, drills, slots) for tid, (dia, drills, slots, __) in tools.items()}
    for tid, (__, __, __, data) in tools.items():
        source.tools[tid]['data'] = dict(data)
    source.create_geometry()
    source.kind = 'excellon'
    source.obj_options = {'name': 'board'}
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), (0, 0, 50, 50)))
    objects = {'board': source}
    for extra_name, extra_tools in (extra or {}).items():
        other = Excellon(app)
        other.units = 'MM'
        other.tools = {tid: tool_dict(dia, drills, slots) for tid, (dia, drills, slots, __) in extra_tools.items()}
        for tid, (__, __, __, data) in extra_tools.items():
            other.tools[tid]['data'] = dict(data)
        other.create_geometry()
        other.kind = 'excellon'
        other.obj_options = dict(source.obj_options, name=extra_name)
        other.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), (60, 60, 90, 90)))
        objects[extra_name] = other
    app.collection.get_by_name.side_effect = objects.get

    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.excellon_tool_gcode_parse = Mock(return_value=[])
    outcome = {}

    def create(kind, name, init, **kwargs):
        outcome['ret'] = init(job, app)
        return job

    app.app_obj.new_object.side_effect = create
    command = TclCommandDrillcncjob(app)
    args, unnamed = command.check_args(('board',) + tuple(str(a) for a in argv) + ('-pp', pp))
    command.execute(args, unnamed)
    assert outcome['ret'] != 'fail', [c.args[0] for c in app.inform.emit.call_args_list]
    return app, job, job.source_file


def tool_codes(job):
    return {tid: tool['gcode'] for tid, tool in job.tools.items()}


# ---------------------------------------------------------------------------------------------------------------------
# per-tool values and the job-wide override

PER_TOOL = {
    1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_cutz=-1.0, tools_drill_travelz=2.0, tools_drill_feedrate_z=100,
                                    tools_drill_spindlespeed=5000)),
    2: (1.0, [(5.0, 5.0)], (), dict(tools_drill_cutz=-2.5, tools_drill_travelz=4.0, tools_drill_feedrate_z=200,
                                    tools_drill_spindlespeed=9000)),
}


def test_each_tool_is_drilled_with_its_own_values():
    __, job, __ = run(PER_TOOL, ['-toolchange', 1])
    one, two = tool_codes(job)[1], tool_codes(job)[2]
    assert 'G01 Z-1.0000' in one and 'G00 Z2.0000' in one and 'G01 F100.00' in one and 'S5000' in one
    assert 'G01 Z-2.5000' in two and 'G00 Z4.0000' in two and 'G01 F200.00' in two and 'S9000' in two
    assert 'Z-2.5000' not in one and 'Z-1.0000' not in two


def test_job_wide_arguments_override_every_tool():
    __, job, __ = run(PER_TOOL, ['-toolchange', 1, '-drillz', -3, '-travelz', 5, '-feedrate_z', 50,
                                 '-spindlespeed', 7000])
    for code in tool_codes(job).values():
        assert 'G01 Z-3.0000' in code and 'G00 Z5.0000' in code and 'G01 F50.00' in code and 'S7000' in code


def test_the_depth_per_pass_of_each_tool_makes_the_passes():
    tools = {1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_cutz=-2.0, tools_drill_multidepth=True,
                                             tools_drill_depthperpass=1.0)),
             2: (1.0, [(5.0, 5.0)], (), dict(tools_drill_cutz=-2.0, tools_drill_multidepth=False,
                                             tools_drill_depthperpass=1.0))}
    __, job, __ = run(tools, ['-toolchange', 1])
    assert tool_codes(job)[1].count('G01 Z-') == 2
    assert tool_codes(job)[2].count('G01 Z-') == 1
    __, job, __ = run(tools, ['-toolchange', 1, '-dpp', 0.5])
    assert all(code.count('G01 Z-') == 4 for code in tool_codes(job).values())


def test_the_offset_of_a_tool_and_the_offset_argument_deepen_the_cut():
    tools = {1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_cutz=-1.0, tools_drill_offset=0.25))}
    assert 'G01 Z-1.2500' in run(tools, ['-toolchange', 1])[2]
    assert 'G01 Z-1.5000' in run(tools, ['-toolchange', 1, '-offset', 0.5])[2]


# ---------------------------------------------------------------------------------------------------------------------
# defaults like the Drilling plugin, tool order, toolchange

def test_the_optimization_type_defaults_to_the_application_option():
    tools = {1: (0.8, [(8.0, 8.0), (1.0, 1.0)], (), {})}
    assert drilled_positions(run(tools, options=dict(excellon_optimization_type='No'))[2]) == [(8.0, 8.0), (1.0, 1.0)]
    assert drilled_positions(run(tools, options=dict(excellon_optimization_type='R'))[2]) == [(1.0, 1.0), (8.0, 8.0)]
    assert drilled_positions(run(tools, ['-opt_type', 'No'], options=dict(excellon_optimization_type='R'))[2]) == \
        [(8.0, 8.0), (1.0, 1.0)]


@pytest.mark.parametrize('order, expected', [('rev', ['2', '1']), ('no', ['2', '1'])])
def test_the_tools_are_drilled_in_the_order_asked(order, expected):
    tools = {2: (1.0, [(5.0, 5.0)], (), {}), 1: (0.8, [(1.0, 1.0)], (), {})}
    code = run(tools, ['-toolchange', 1, '-order', order])[2]
    assert re.findall(r'^T(\d+)$', code, re.M) == expected


def test_the_tools_of_the_job_are_in_the_order_they_are_drilled():
    # T1 is bigger than T2: T2 is drilled first, after the spot pass (T3). The export (CNCJobObject.export_gcode())
    # joins the G-code of the tools in the order of job.tools: the end code was in the middle of the file, the file
    # ended with the spindle on and the spot pass was the last one
    tools = {1: (1.0, [(5.0, 5.0)], (), {}), 2: (0.4, [(1.0, 1.0)], (), {})}
    __, job, code = run(tools, ['-toolchange', 1, '-spot', 1])
    exported = ''.join(tool['gcode'] for tool in job.tools.values())
    assert [t for t, __ in blocks(exported)] == [3, 2, 1]
    assert job.gc_start + exported == code


def test_toolchange_argument_turns_the_toolchange_on_and_off():
    assert 'M6' in run(TWO, ['-toolchange', 1])[2]
    assert 'M6' not in run(TWO, ['-toolchange', 0], options=dict(tools_drill_toolchange=True))[2]
    assert 'M6' in run(TWO, options=dict(tools_drill_toolchange=True))[2]


def test_plunge_to_travel_z_after_the_toolchange():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    argv = ['-toolchange', 1, '-travelz', 2, '-toolchangez', 30]
    without = run(tools, argv)[2]
    with_plunge = run(tools, argv + ['-f_plunge', 1])[2]
    assert with_plunge.count('G00 Z2.0000') == without.count('G00 Z2.0000') + 1


TOOLCHANGE_Z_RE = re.compile(r'^M5\nG00 Z(\S+)\nT\d+$', re.M)


def start_z_moves(code):
    """The Z moves of the G-code before the first travel."""
    return re.findall(r'^G00 Z(\S+)$', code.split('\nG00 X', 1)[0], re.M)


def test_without_startz_the_first_toolchange_and_travel_are_at_the_heights_of_the_job():
    # no -startz and no Start Z in the Preferences: no start Z move, as in the Drilling plugin. The Travel Z of the
    # Preferences (2.0) was the Start Z: the T1 toolchange and the first travel were 2 mm above the material
    code = run(TWO, ['-toolchange', 1, '-toolchangez', 30, '-travelz', 10])[2]
    assert TOOLCHANGE_Z_RE.findall(code) == ['30.0000', '30.0000']
    assert start_z_moves(run(TWO, ['-toolchange', 0, '-travelz', 10])[2]) == ['10.0000']


def test_the_startz_argument_or_preference_is_still_the_start_height():
    code = run(TWO, ['-toolchange', 1, '-toolchangez', 30, '-startz', 5])[2]
    assert TOOLCHANGE_Z_RE.findall(code) == ['5.0000', '30.0000']
    code = run(TWO, ['-toolchange', 0, '-travelz', 10], options=dict(tools_drill_startz=3.0))[2]
    assert start_z_moves(code) == ['10.0000', '3.0000']


def test_retract_option_decides_the_up_to_zero_move():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    assert 'G01 Z0' in run(tools, ['-f_retract', 0])[2].splitlines()
    assert 'G01 Z0' not in run(tools, ['-f_retract', 1])[2].splitlines()


def test_spindle_direction_dwell_and_dwell_time():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    assert 'M04' in run(tools, ['-spindledir', 'CCW'])[2]
    assert 'M03' in run(tools, ['-spindledir', 'CW'])[2]
    assert 'G4 P2.5' in run(tools, ['-dwell', 1, '-dwelltime', 2.5])[2]
    assert 'G4' not in run(tools, ['-dwell', 0, '-dwelltime', 2.5])[2]
    assert 'G4 P2.5' in run(tools, ['-dwelltime', 2.5])[2]


def test_the_probe_depth_and_feed_are_used_by_the_probe_preprocessor():
    code = run({1: (0.8, [(1.0, 1.0)], (), {})}, ['-toolchange', 1, '-pdepth', -0.5, '-pfeed', 60],
               pp='Toolchange_Probe_MACH3')[2]
    assert 'G31 Z-0.5000' in code and 'F60.00' in code
    tools = {1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_z_p_depth=-0.3, tools_drill_feedrate_probe=40))}
    code = run(tools, ['-toolchange', 1], pp='Toolchange_Probe_MACH3')[2]
    assert 'G31 Z-0.3000' in code and 'F40.00' in code


def test_the_probe_plate_thickness_sets_the_zero_to_the_top_of_the_material():
    code = run({1: (0.8, [(1.0, 1.0)], (), {})}, ['-toolchange', 1, '-pplate', 1.5],
               pp='Toolchange_Probe_MACH3')[2]
    assert 'G92 Z1.5000' in code and 'G92 Z0' not in code
    tools = {1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_probe_plate=0.8))}
    code = run(tools, ['-toolchange', 1], pp='Toolchange_Probe_MACH3')[2]
    assert 'G92 Z0.8000' in code
    assert 'G92 Z0\n' in run({1: (0.8, [(1.0, 1.0)], (), {})}, ['-toolchange', 1], pp='Toolchange_Probe_MACH3')[2]


def test_laser_on_code_and_min_power():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    code = run(tools, ['-las_power', 300, '-laser_on', 'M4'], pp='GRBL_laser')[2]
    assert 'M4 S300.0' in code.splitlines()
    code = run(tools, ['-las_power', 300, '-min_power', 20], pp='GRBL_laser')[2]
    assert any('S20' in line and 'laser OFF' in line for line in code.splitlines())


# ---------------------------------------------------------------------------------------------------------------------
# slots

SLOT = ((10.0, 20.0), (14.0, 20.0))


def test_slots_follow_the_drill_slots_arguments():
    tools = {1: (1.0, [], [SLOT], dict(tools_drill_drill_slots=True, tools_drill_drill_overlap=0.0,
                                       tools_drill_last_drill=True))}
    # the values of the tool
    assert len(drilled_positions(run(tools, ['-opt_type', 'No'])[2])) == 5
    # the arguments override the tool
    assert len(drilled_positions(run(tools, ['-opt_type', 'No', '-last_drill', 0])[2])) == 4
    assert len(drilled_positions(run(tools, ['-opt_type', 'No', '-overlap', 50])[2])) == 9
    off = {1: (1.0, [(1.0, 1.0)], [SLOT], dict(tools_drill_drill_slots=False))}
    assert drilled_positions(run(off, ['-opt_type', 'No'])[2]) == [(1.0, 1.0)]
    assert len(drilled_positions(run(off, ['-opt_type', 'No', '-drill_slots', 1, '-overlap', 0])[2])) == 6


# ---------------------------------------------------------------------------------------------------------------------

def test_peck_and_dwell_at_the_bottom_arguments():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    code = run(tools, ['-drillz', -3, '-f_retract', 1, '-peck', 1, '-peck_depth', 1, '-peck_retract', 0.3,
                       '-dwell_bottom', 1, '-dwelltime', 0.5, '-dwell', 0])[2].splitlines()
    z = [line for line in code if line.startswith(('G00 Z', 'G01 Z', 'G4'))]
    first = z.index('G01 Z-1.0000')
    assert z[first:first + 9] == [
        'G01 Z-1.0000', 'G00 Z2.0000', 'G00 Z-0.7000', 'G01 Z-2.0000', 'G00 Z2.0000', 'G00 Z-1.7000',
        'G01 Z-3.0000', 'G4 P0.5', 'G00 Z2.0000']


def test_the_peck_of_each_tool_is_the_default_and_the_argument_overrides_it():
    data = dict(tools_drill_cutz=-3.0, tools_drill_f_retract=True, tools_drill_peck=True, tools_drill_peck_depth=1.5,
                tools_drill_peck_retract=0.2)
    tools = {1: (0.8, [(1.0, 1.0)], (), data), 2: (1.0, [(5.0, 5.0)], (), dict(data, tools_drill_peck=False))}
    __, job, __ = run(tools, ['-toolchange', 1])
    assert 'G01 Z-1.5000' in job.tools[1]['gcode'] and 'G01 Z-3.0000' in job.tools[1]['gcode']
    assert 'Z-1.5000' not in job.tools[2]['gcode']
    __, job, __ = run(tools, ['-toolchange', 1, '-peck', 0])
    assert 'Z-1.5000' not in job.tools[1]['gcode']


def test_a_laser_preprocessor_ignores_the_peck_and_the_spot_pass():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    plain = run(tools, ['-drillz', -3], pp='GRBL_laser')[2]
    assert run(tools, ['-drillz', -3, '-peck', 1, '-peck_depth', 1, '-dwell_bottom', 1], pp='GRBL_laser')[2] == plain
    plain = run(tools, ['-drillz', -3, '-toolchange', 1], pp='GRBL_laser')[2]
    assert run(tools, ['-drillz', -3, '-toolchange', 1] + SPOT_ARGS[2:], pp='GRBL_laser')[2] == plain


SPOT_ARGS = ['-toolchange', 1, '-spot', 1, '-spot_dia', 0.5, '-spot_cutz', -0.3, '-spot_feedrate', 50]


def blocks(code):
    parts = re.split(r'(?m)^T(\d+)$', code)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def test_the_spot_pass_comes_first_over_all_the_holes_with_the_next_free_tool_number():
    __, job, code = run(PER_TOOL, SPOT_ARGS)
    spot, one, two = blocks(code)
    assert [spot[0], one[0], two[0]] == [3, 1, 2]
    assert len(drilled_positions(spot[1])) == 2
    assert 'G01 Z-0.3000' in spot[1] and 'G01 F50.00' in spot[1] and 'Z-1.0000' not in spot[1]
    assert 'Z-0.3000' not in one[1] + two[1] and 'F50.00' not in one[1] + two[1]
    assert 'Dia = 0.5' in code and 'Total drills for tool T3 = 2' in code
    assert sorted(job.tools) == [1, 2, 3] and job.tools[3]['tooldia'] == 0.5 and job.tools[3]['nr_drills'] == 2
    assert job.tools[3]['gcode_parsed'] == [] and job.tools[3]['gcode']


def test_the_spot_values_default_to_the_application_options():
    options = dict(tools_drill_spot=True, tools_drill_spot_dia=0.4, tools_drill_spot_cutz=-0.2,
                   tools_drill_spot_feedrate_z=33.0)
    __, __, code = run(PER_TOOL, ['-toolchange', 1], options=options)
    assert blocks(code)[0][0] == 3 and 'G01 Z-0.2000' in blocks(code)[0][1] and 'G01 F33.00' in blocks(code)[0][1]
    __, __, code = run(PER_TOOL, ['-toolchange', 1, '-spot', 0], options=options)
    assert [t for t, __ in blocks(code)] == [1, 2]


def test_the_spot_pass_without_a_toolchange_is_skipped_with_a_warning():
    app, job, code = run(PER_TOOL, ['-toolchange', 0, '-spot', 1])
    assert 'Z-0.3000' not in code and sorted(job.tools) == [1, 2]
    assert any('spot' in str(c.args[0]).lower() and 'toolchange' in str(c.args[0]).lower()
               for c in app.inform.emit.call_args_list)


# ---------------------------------------------------------------------------------------------------------------------
# job XY transforms: mirror -> offset -> array

XF_TOOLS = {1: (0.8, [(0.0, 0.0), (4.0, 2.0)], (), {}), 2: (1.0, [(2.0, 4.0)], (), {})}


def test_job_offset_mirror_and_array_args_transform_the_holes():
    base = sorted(drilled_positions(run(XF_TOOLS, ['-opt_type', 'No'])[2]))
    assert base == [(0.0, 0.0), (2.0, 4.0), (4.0, 2.0)]
    moved = sorted(drilled_positions(run(XF_TOOLS, ['-job_offset', '10,-5'])[2]))
    assert moved == [(10.0, -5.0), (12.0, -1.0), (14.0, -3.0)]
    mirrored = sorted(drilled_positions(run(XF_TOOLS, ['-mirror', 'y'])[2]))
    assert mirrored == [(0.0, 2.0), (2.0, 4.0), (4.0, 0.0)]


def test_job_mirror_point_and_array_args():
    mirrored = sorted(drilled_positions(run(XF_TOOLS, ['-mirror', 'xy', '-mirror_point', '0,0'])[2]))
    assert mirrored == [(-4.0, -2.0), (-2.0, -4.0), (0.0, 0.0)]
    grid = drilled_positions(run(XF_TOOLS, ['-array', '2,2', '-array_pitch', '100,50'])[2])
    assert len(grid) == 12 and (104.0, 52.0) in grid and (2.0, 54.0) in grid
    __, job, __ = run(XF_TOOLS, ['-array', '2,1', '-array_pitch', '20,0', '-toolchange', 1])
    assert job.tools[1]['nr_drills'] == 4 and job.tools[2]['nr_drills'] == 2


def test_job_transform_args_move_the_spot_pass_too_and_default_to_the_options():
    __, __, code = run(XF_TOOLS, SPOT_ARGS + ['-job_offset', '1,1'])
    spot, one, two = blocks(code)
    assert sorted(drilled_positions(spot[1])) == [(1.0, 1.0), (3.0, 5.0), (5.0, 3.0)]
    options = dict(tools_drill_job_offset_xy='5, 0', tools_drill_mirror='no')
    assert sorted(drilled_positions(run(XF_TOOLS, [], options=options)[2])) == [(5.0, 0.0), (7.0, 4.0), (9.0, 2.0)]


@pytest.mark.parametrize('argv', [['-job_offset', 'abc'], ['-mirror', 'z'], ['-array', '0,1']])
def test_invalid_job_transform_args_are_errors(argv):
    with pytest.raises(Exception):
        run(XF_TOOLS, argv)


# ---------------------------------------------------------------------------------------------------------------------
# more Excellon objects in one job, holes inside an area

TWO_OBJECTS = {'second': {1: (0.8, [(70.0, 70.0)], (), {}), 2: (2.0, [(80.0, 80.0)], (), {})}}


def test_objects_argument_drills_the_holes_of_the_extra_objects_merged_by_diameter():
    __, job, code = run(TWO, ['-toolchange', 1, '-objects', 'second'], extra=TWO_OBJECTS)
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (5.0, 5.0), (70.0, 70.0), (80.0, 80.0)]
    assert sorted(job.tools) == [1, 2, 3]
    assert job.tools[1]['nr_drills'] == 2 and job.tools[3]['tooldia'] == 2.0
    assert job.obj_options['xmax'] == 90 and job.obj_options['xmin'] == 0


def test_objects_argument_warns_about_tools_of_one_diameter_with_different_data():
    tools = {1: (0.8, [(1.0, 1.0)], (), dict(tools_drill_cutz=-1.0))}
    extra = {'second': {1: (0.8, [(70.0, 70.0)], (), dict(tools_drill_cutz=-2.0))}}
    app, __, code = run(tools, ['-objects', 'second', '-toolchange', 1], extra=extra)
    assert any('same diameter' in str(c.args[0]) for c in app.inform.emit.call_args_list)
    assert 'Z-1.0000' in code and 'Z-2.0000' not in code




def test_area_argument_drills_only_the_holes_inside_the_rectangle():
    tools = {1: (0.8, [(1.0, 1.0), (9.0, 9.0)], (), {}), 2: (1.0, [(30.0, 30.0)], (), {})}
    __, job, code = run(tools, ['-area', '0,0,10,10', '-toolchange', 1])
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (9.0, 9.0)]
    assert sorted(job.tools) == [1, 2] and 2 not in [t for t, __ in blocks(code)]
    __, __, code = run(tools, ['-area', '10,10,0,0'])
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (9.0, 9.0)]


@pytest.mark.parametrize('argv', [['-area', '1,2,3']])
def test_invalid_area_argument_is_an_error(argv):
    with pytest.raises(Exception):
        run(TWO, argv)
