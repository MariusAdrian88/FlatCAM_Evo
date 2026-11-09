"""
Tests for the output of the preprocessors: no swallowed errors and correct G-code.

A preprocessor that raised was not noticed by the user: CNCjob.doformat2() logged
"Exception occurred within a preprocessor" and returned '' for that part of the G-code. The Tcl command and the GUI went
on as if nothing had happened. Now the CNC job fails with the preprocessor method in the message.

  * Line_xyz.start_code() read ``p['end_xy']``; the attribute of the job is ``xy_end`` (every other preprocessor reads
    that one). The whole start block was lost: no units (G21), no absolute mode (G90), no feed-rate mode (G94).
  * ISEL_ICP_CNC.toolchange_code() read ``p['obj_options']['Tools_in_use']`` for a drill job. Nothing sets that key and
    the value computed from it was never used. Every tool change of a drill job with tool change was lost (no GETTOOL).
  * Berta_CNC.start_code() tested the tool change position (``coords_xy``) before it printed the end position
    (``end_coords_xy``), as a typo of the check the other preprocessors make. With a tool change position and no end
    position it raised a TypeError.
  * Roland_MDX_20/540 and hpgl: the plotter units are integers, the feedrate is clamped to the plotter range, and the
    HPGL position is offset, scaled, then clamped.
  * The Roland plunge is made at the Z feedrate, not at the XY feedrate.
  * ISEL_CNC selects G70 for an inch job; ISEL_ICP_CNC writes the inch z moves and feedrates in micrometres.
  * An inch job drives the same machine position as the mm job (Roland, hpgl, ISEL_ICP_CNC).
  * The end move writes the end X-Y and the end Z in order and lifts before it moves at a low end Z (eleks,
    ISEL_ICP_CNC, NCCAD9, Line_xyz, solder paste).
  * A preprocessor error fails the CNC job instead of dropping the line (a solder paste job keeps the error of the
    preprocessor); a method with nothing to emit writes no empty line.
  * The extra cut of a multi-pass ring is made at the depth of the pass, not at the final Cut Z (Line_xyz).
  * The laser and solder paste end X-Y are printed with coords_decimals, not as raw floats.
  * The tool change moves to the tool change X-Y (a zero coordinate included); a drill job gets its tool change; the
    spindle stops before the operator pause; a machine without ATC names the tool in the message.
  * The probe tool change stops the spindle, retracts, changes the tool, then waits.
  * The bed offset and skew shift the position; a skew with a bed size of 0 fails the job.
  * The dwell is in the unit of the machine (Marlin S is seconds, P is milliseconds; GRBL P is seconds).
  * The fan speed is clamped to 0..255; a laser without speed is at zero power.
  * The laser_z start block shows the feedrate and power, also when a tool misses keys.
  * The NCCAD9 Excellon start block sets mm, absolute mode and the drill cycle.

The first two tests call the preprocessors through the real CNCjob.doformat2(), with the attributes of a generated job.
The others call the preprocessor methods with the parameters of a job.
"""

import importlib
import pkgutil
import re
from decimal import Decimal
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString

import appPreProcessor
import preprocessors
from camlib import AttrDict, CNCjob
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b as make_app  # noqa: F401,E402

for _module in pkgutil.iter_modules(preprocessors.__path__):
    importlib.import_module('preprocessors.' + _module.name)

# the registry is module wide and other tests empty or reload it: keep the preprocessors of the collection time
PREPROCESSORS = dict(appPreProcessor.preprocessors)
# the solder paste preprocessors need the solder paste options of a solder paste job
GENERAL = sorted(name for name in PREPROCESSORS if not name.startswith('Paste_'))


def _job(kind, pp_name, toolchange_xy='1.0,2.0', end_xy=None):
    app = make_app()
    app.preprocessors = PREPROCESSORS
    app.log = Mock()
    job = CNCjob(app, units='MM', pp_geometry_name=pp_name, pp_excellon_name=pp_name)
    job.obj_options = {'name': 'job', 'type': kind, 'xmin': 0.0, 'ymin': 0.0, 'xmax': 10.0, 'ymax': 20.0,
                       'tool_dia': 0.2}
    # CNCJobObject sets these 2 on the job
    job.coords_decimals, job.fr_decimals = 4, 2
    job.feedrate_probe = 3.0
    job.xy_toolchange = [float(a) for a in toolchange_xy.split(',')]
    job.xy_end = end_xy
    job.z_cut, job.z_move, job.z_toolchange, job.z_end, job.startz = -1.7, 2.0, 15.0, 15.0, None
    job.multidepth, job.f_plunge, job.f_retract, job.toolchange = False, True, False, True
    job.tool, job.toolC, job.tooldia = 1, 0.8, 0.8
    job.spindlespeed, job.spindledir, job.dwell, job.dwelltime = 10000, 'CW', True, 1000
    job.exc_tools = {1: {'tooldia': 0.8, 'nr_drills': 3, 'data': {}}}
    job.tools = {1: {'tooldia': 0.8, 'nr_drills': 3, 'data': {}}}
    return app, job


def _exceptions(app):
    return [call for call in app.log.error.call_args_list
            if 'Exception occurred within a preprocessor' in str(call)]


METHODS = ('start_code', 'startz_code', 'toolchange_code', 'spindle_code', 'dwell_code', 'rapid_code', 'down_code',
           'linear_code', 'up_to_zero_code', 'lift_code', 'spindle_stop_code', 'end_code')
DRILL_DATA = {'tools_drill_feedrate_z': 300.0, 'tools_drill_feedrate_rapid': 1500.0, 'tools_drill_cutz': -1.7,
              'tools_drill_offset': 0.0, 'tools_drill_depthperpass': 0.5, 'tools_drill_travelz': 2.0}


def params(kind='Geometry', **kw):
    d = dict(units='MM', x=1.0, y=2.0, oldx=None, oldy=None, z_cut=-1.5, z_move=2.0, z_end=15.0, z_toolchange=15.0,
             xy_end=None, xy_toolchange=None, startz=None, is_first_tool=False, f_plunge=False, tool=1, toolC=0.8,
             tools={1: {'tooldia': 0.8, 'nr_drills': 3, 'data': {}}}, feedrate=100.0, z_feedrate=60.0,
             feedrate_rapid=1500.0, spindlespeed=10000, spindledir='CW', dwelltime=1.0, feedrate_probe=25.0,
             z_p_depth=-10.0, laser_on_code='M3', laser_min_power=0, fr_rapids=1500, decimals=4, coords_decimals=4,
             fr_decimals=2, steps_per_circle=64, toolchange=True, multidepth=False, use_ui=False, multigeo=False,
             pp_geometry_name='x', pp_excellon_name='x', _bed_offset_x=0.0, _bed_offset_y=0.0, _bed_skew_x=0.0,
             _bed_skew_y=0.0, _bed_limit_x=100.0, _bed_limit_y=100.0,
             obj_options=dict(type=kind, tool_dia=0.8, xmin=0.0, xmax=1.0, ymin=0.0, ymax=1.0))
    d.update(kw)
    return AttrDict(d)


def assert_in_order(text, parts):
    positions = [text.find(part) for part in parts]
    assert -1 not in positions and positions == sorted(positions), text


def test_plotter_units_position_and_feedrate():
    # an inch job drives the same plotter position as the mm job; the plotter takes integers (also for a Decimal
    # depth); the feedrate is clamped to the plotter range (inch/min converted first); the HPGL position is
    # offset, then scaled to plotter units (40 per mm), then clamped
    for name in ('Roland_MDX_20', 'Roland_MDX_540', 'hpgl', 'ISEL_ICP_CNC'):
        pp = PREPROCESSORS[name]
        mm = pp.position_code(params(units='MM', x=25.4, y=-12.7, z_move=2.0))
        inch = pp.position_code(params(units='IN', x=1.0, y=-0.5, z_move=2.0 / 25.4))
        assert mm and mm == inch, name
    for name, res, top in (('Roland_MDX_20', 40, 15), ('Roland_MDX_540', 100, 125)):
        pp = PREPROCESSORS[name]
        assert '.' not in pp.linear_code(params(z_cut=-0.062000124000248)).split('\n')[1]
        assert pp.down_code(params(z_cut=Decimal('-0.5'))).endswith(',%d;' % round(-0.5 * res))
        assert pp.up_to_zero_code(params()).endswith(',0;')
        assert pp.feedrate_code(params(feedrate=1.0)) == 'V0.1;'
        assert pp.feedrate_code(params(feedrate=100000.0)) == 'V%.1f;' % top
        assert pp.feedrate_code(params(units='IN', feedrate=10.0)) == 'V4.2;'
        assert pp.z_feedrate_code(params(z_feedrate=1.0)) == 'V0.1;'
        assert pp.feedrate_rapid_code(params(feedrate_rapid=1.0)) == 'V0.1;'
    pp = PREPROCESSORS['hpgl']
    assert pp.position_code(params(x=10.0, y=5.0, _bed_offset_x=5.0)) == 'PA600,200;'
    assert pp.position_code(params(x=1000.0, y=-1000.0)) == 'PA32767,-32768;'
    assert pp.position_code(params(x=800.0, y=5.0, _bed_offset_x=100.0)) == 'PA32767,200;'
    # the Roland plunge runs at the Z feedrate (the down code once set the XY feedrate over it)
    for name, res in (('Roland_MDX_20', 40), ('Roland_MDX_540', 100)):
        pp = PREPROCESSORS[name]
        p = params(feedrate=600.0, z_feedrate=60.0, z_cut=-0.1)
        assert pp.down_code(p) == 'V1.0;\nZ%d,%d,%d;' % (res, 2 * res, round(-0.1 * res))
        # the cut moves keep the XY feedrate
        assert pp.linear_code(p).startswith('V10.0;\n')


def test_isel_units_and_micrometres():
    # G70 selects inches on the ISEL controller: an inch job must select it, a mm job must not
    pp = PREPROCESSORS['ISEL_CNC']
    assert 'G70\n' in pp.start_code(params(units='IN'))
    assert 'G71\n' in pp.start_code(params())
    assert 'G70' not in pp.start_code(params())
    # the ISEL_ICP_CNC controller takes micrometres for z and the feedrate in um/s
    pp = PREPROCESSORS['ISEL_ICP_CNC']
    p = params(units='IN', z_move=0.1, z_cut=-0.05, feedrate=60.0, z_feedrate=30.0)
    assert pp.lift_code(p) == 'FASTABS Z2540'
    assert pp.down_code(p) == 'MOVEABS Z-1270'
    assert pp.feedrate_code(p) == 'VEL 25400'
    assert pp.z_feedrate_code(p) == 'VEL 12700'
    assert pp.feedrate_code(params(feedrate=120.0)) == 'VEL 2000'


def test_end_moves_to_end_xy_and_z():
    # the end X-Y comes after the lift to the end Z, the end Z is the last move, the end X-Y is formatted
    cases = [
        ('grbl_laser_eleks_drd', dict(xy_end=[1.0, 2.0]), ['X1.000 Y2.000']),
        ('ISEL_ICP_CNC', dict(xy_end=[10.0, 20.0]), ['FASTABS Z15000', 'FASTABS X10000 Y20000', 'PROGEND']),
        # a low end Z: the X-Y move is made at the move height, then the end Z is reached
        ('ISEL_ICP_CNC', dict(xy_end=[10.0, 20.0], z_end=0.5),
         ['FASTABS Z2000', 'FASTABS X10000 Y20000', 'FASTABS Z500']),
        ('NCCAD9', dict(xy_end=[1.0, 2.0]), ['G00 Z15', 'G0 X1.000 Y2.000', 'G77']),
        ('Line_xyz', dict(xy_end=[1 / 3, 2.5], z_end=0.5), ['X0.333 Y2.500 Z2.000', 'X0.333 Y2.500 Z0.500']),
        ('Paste_1', dict(xy_end=[1.23456, 2.0]), ['G01 Z15.000 F1500', 'G01 X1.235 Y2.000 F1500']),
    ]
    for name, kw, parts in cases:
        assert_in_order(PREPROCESSORS[name].end_code(params(coords_decimals=3, **kw)), parts)
    for name in ('default_laser', 'GRBL_laser', 'GRBL_laser_z', 'grbl_laser_eleks_drd', 'Marlin_laser_FAN_pin',
                 'Marlin_laser_Spindle_pin'):
        assert 'X2.123 Y3.000' in PREPROCESSORS[name].end_code(params(xy_end=[2.123456, 3.0], coords_decimals=3)), name


def test_bed_offset_and_skew_move_the_position():
    cases = [
        ('ISEL_ICP_CNC', 'position_code', dict(x=1.0, y=2.0, _bed_offset_x=1.0, _bed_offset_y=2.0), 'X2000 Y4000'),
        ('ISEL_ICP_CNC', 'position_code', dict(x=10.0, y=20.0, _bed_skew_y=5.0), 'X10000 Y20500'),
        ('grbl_laser_eleks_drd', 'rapid_code', dict(kind='Excellon', _bed_offset_x=10.0, _bed_offset_y=20.0,
                                                     coords_decimals=3), 'X10.900 Y22.000'),
        ('Marlin', 'rapid_code', dict(_bed_offset_x=1.0, _bed_skew_x=2.0), 'G0 X2.0400 Y2.0000'),
    ]
    for name, method, kw, expected in cases:
        assert expected in getattr(PREPROCESSORS[name], method)(params(**kw)), name


def test_tool_change_moves_to_the_tool_change_xy_and_a_drill_job_gets_it():
    cases = [
        ('Line_xyz', dict(xy_toolchange=[5.0, 7.0], f_plunge=True), 'G00 X5.0000 Y7.0000 Z15.0000'),
        # no old position: the move goes to 0,0 instead of raising
        ('Line_xyz', dict(kind='Excellon', oldx=None, oldy=None, x=None, y=None), 'G00 X0.0000 Y0.0000 Z15.0000'),
        ('ISEL_CNC', dict(xy_toolchange=[5.0, 6.0]), 'G00 X5.0000 Y6.0000'),
        ('ISEL_ICP_CNC', dict(xy_toolchange=[5.0, 6.0]), 'FASTABS X5000 Y6000'),
        # a zero coordinate is still written
        ('NCCAD9', dict(xy_toolchange=[0.0, 5.0]), 'G00 X0.0000 Y5.0000'),
        ('NCCAD9', dict(xy_toolchange=[5.0, 0.0]), 'G00 X5.0000 Y0.0000'),
        ('Check_points', dict(xy_toolchange=[5.0, 5.0]), 'G00 X5.0000 Y5.0000'),
        ('Toolchange_Manual', dict(xy_toolchange=[5.0, 7.0]), 'G00 X5.0000 Y7.0000'),
        # a drill job with tool change gets its tool change (the tool was lost for ISEL_ICP_CNC)
        ('ISEL_CNC', dict(kind='Excellon', f_plunge=True), 'M00'),
        ('ISEL_ICP_CNC', dict(kind='Excellon', f_plunge=True), 'GETTOOL 1'),
    ]
    for name, kw, expected in cases:
        assert expected in PREPROCESSORS[name].toolchange_code(params(**kw)), (name, expected)


def test_tool_change_stops_the_spindle_and_names_the_tool_without_atc():
    # the spindle is off before the operator pause; the motors are not switched off (M84)
    for name, stop, pause in (('Marlin', 'M5', 'M0 '), ('Repetier', 'M107', '@pause'),
                              ('ISEL_ICP_CNC', 'SPINDLE OFF', 'GETTOOL 1')):
        g = PREPROCESSORS[name].toolchange_code(params(xy_toolchange=[5.0, 6.0]))
        lines = g.splitlines()
        pause_line = next(i for i, ln in enumerate(lines) if ln.startswith(pause))
        assert stop in lines[:pause_line], name
        assert 'M84' not in g
    # a machine without automatic tool change: the message names the tool and no M6 or Tn line is written
    for name in ('Marlin', 'Default_no_M6'):
        g = PREPROCESSORS[name].toolchange_code(params(tool=3))
        msg = next(ln for ln in g.splitlines() if ln.startswith(('M0 ', '(MSG')))
        assert 'T3' in msg and '0.8000' in msg
        assert not re.search(r'^(T\d|M6)', g, re.M)


def test_grblhal_tool_change_leaves_the_pause_to_the_firmware():
    # grblHAL waits in the Tool state at M6 for the sender's acknowledge and a Cycle Start; an M0 after it would
    # stop again when the tool change is already done
    native = PREPROCESSORS['grblHAL'].toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(native, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M6'])
    assert 'M0' not in native.splitlines()
    # no M6: the pause is the operator tool change of the sender
    no_m6 = PREPROCESSORS['grblHAL_no_M6'].toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(no_m6, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M0'])
    assert not re.search(r'^M6$', no_m6, re.M)


def test_fluidnc_tool_change_keeps_the_pause_that_its_m6_does_not_make():
    # FluidNC supports M6, but a standard FluidNC M6 only selects the tool/spindle and returns at once; the
    # operator pause is the M0 after it (unlike grblHAL, whose M6 waits in the Tool state)
    pp = PREPROCESSORS['FluidNC']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M6', 'M0'])
    assert '(MSG ' in g


def test_linuxcnc_start_and_tool_change():
    pp = PREPROCESSORS['LinuxCNC']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        assert start.rstrip().endswith('\n%s\nG90\nG17\nG94\nG40\nG49' % select)
        # Preserve the operator's path-control mode and tolerance; don't pick a coordinate system.
        assert not re.search(r'^G(?:61(?:\.1)?|64)(?:\s|$)', start, re.M)
        assert 'G54' not in start
    t = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(t, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M6'])
    # LinuxCNC M6 pauses for the tool change (hal_manualtoolchange); M0 would pause a second time
    assert not re.search(r'^M0$', t, re.M)
    end = pp.end_code(params(xy_end=[10.0, 20.0]))
    assert_in_order(end, ['G00 Z15.0000', 'G00 X10.0000 Y20.0000'])
    # LinuxCNC needs a program end, but export_gcode writes it after the user postamble
    assert not re.search(r'^M2$', end, re.M)


def test_fanuc_start_block_and_manual_setup_stop():
    pp = PREPROCESSORS['Fanuc']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        assert_in_order(start, [select, 'G17', 'G90', 'G94', 'G40', 'G49', 'G80', 'M05'])
        # the unconditional stop comes before the first generated axis move
        assert start.rstrip().endswith('M00')
        # no inferred compensation, work offset, reference return, path control or coolant
        for pattern in (r'^G43', r'^G5[34]', r'^G28', r'^G6[14]', r'^M0?6\s*$', r'^T\d', r'^M0?[789]\s*$'):
            assert not re.search(pattern, start, re.M), pattern


def test_fanuc_manual_tool_change_reestablishes_the_reference():
    pp = PREPROCESSORS['Fanuc']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M00'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'reestablish' in g
    assert not re.search(r'^(T\d|M0?6)\s*$', g, re.M)
    # no configured tool-change X-Y: the safe Z retract and the pause only
    g = pp.toolchange_code(params())
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'M00'])
    assert 'G00 X' not in g


def test_fanuc_dwell_is_x_seconds():
    pp = PREPROCESSORS['Fanuc']
    assert pp.dwell_code(params(dwelltime=1.234)) == 'G04 X1.234'
    assert pp.dwell_code(params(dwelltime=0)) == ''


def test_haas_start_block_and_manual_setup_stop():
    pp = PREPROCESSORS['Haas']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        assert_in_order(start, [select, 'G17', 'G90', 'G94', 'G40', 'G49', 'G80', 'M05'])
        # the unconditional stop comes before the first generated axis move
        assert start.rstrip().endswith('M00')
        # NGC may apply a tool length offset automatically, so G49 has to cancel it explicitly
        assert re.search(r'^G49$', start, re.M)
        # no inferred compensation, work offset, reference return, path control, coolant or tool call
        for pattern in (r'^G43', r'^G5[34]', r'^G28', r'^G53', r'^G6[14]', r'^G187', r'^M0?6\s*$', r'^T\d',
                        r'^[HD]\d', r'^M0?[789]\s*$'):
            assert not re.search(pattern, start, re.M), pattern


def test_haas_manual_tool_change_has_no_tool_call_or_compensation():
    pp = PREPROCESSORS['Haas']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M00'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'reestablish' in g
    assert not re.search(r'^(T\d|M0?6|[HD]\d|G43)\b', g, re.M)
    # no configured tool-change X-Y: the safe Z retract and the pause only
    g = pp.toolchange_code(params())
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'M00'])
    assert 'G00 X' not in g


def test_haas_dwell_is_p_with_decimal_seconds():
    pp = PREPROCESSORS['Haas']
    # HAAS reads G04 P with a decimal point as seconds; without one, P10 is 10 milliseconds
    assert pp.dwell_code(params(dwelltime=10)) == 'G04 P10.000'
    assert pp.dwell_code(params(dwelltime=1.234)) == 'G04 P1.234'
    assert pp.dwell_code(params(dwelltime=0)) == ''


def test_sinumerik_start_block_selects_the_iso_dialect_first():
    pp = PREPROCESSORS['Sinumerik']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        # G291 is the first block and is alone in its block; the metadata and the setup comments follow it
        assert start.splitlines()[0] == 'G291'
        assert start.count('G291') == 1
        assert not start.startswith('(')
        assert_in_order(start, ['G291', select, 'G17', 'G90', 'G94', 'G40', 'G49', 'G80', 'M05', 'M00'])
        assert start.rstrip().endswith('M00')
        assert start.index('(TOOL DIAMETER:') > start.index(select)
        assert start.index('(Spindle Speed:') > start.index(select)
        # no inferred compensation, work offset, reference return, path control, coolant or tool call
        for pattern in (r'^G43', r'^G5[34]', r'^G28', r'^G53', r'^G6[14]', r'^G187', r'^M0?6\s*$', r'^T\d',
                        r'^[HD]\d', r'^M0?[789]\s*$'):
            assert not re.search(pattern, start, re.M), pattern


def test_sinumerik_manual_tool_change_and_dwell():
    pp = PREPROCESSORS['Sinumerik']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M00'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'reestablish' in g
    assert not re.search(r'^(T\d|M0?6|[HD]\d|G43)\b', g, re.M)
    # no configured tool-change X-Y: the safe Z retract and the pause only
    g = pp.toolchange_code(params())
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'M00'])
    assert 'G00 X' not in g
    # G04 X is a decimal number of seconds with G94 active; P would be internal units
    assert pp.dwell_code(params(dwelltime=1.234)) == 'G04 X1.234'
    assert pp.dwell_code(params(dwelltime=0)) == ''


def test_sinumerik_has_no_fanuc_program_envelope():
    pp = PREPROCESSORS['Sinumerik']
    assert pp.include_header is False and pp.include_footer is True
    gcode = 'G291\nG21\nG00 X1.0000 Y2.0000\nM30\n'
    assert pp.finalize_gcode(gcode) == gcode


def test_sinumerik_iso_program_ends_with_m30_without_returning_to_siemens_mode():
    pp = PREPROCESSORS['Sinumerik']
    # the ISO dialect manual ends every G291 program with M30 and never switches back; Reset restores the default mode
    for tail in ('M02', 'M2'):
        out = pp.finalize_gcode('G291\nG21\nG00 X1.0000 Y2.0000\n%s\n' % tail)
        assert out == 'G291\nG21\nG00 X1.0000 Y2.0000\nM30\n'
    assert pp.finalize_gcode('G291\nG00 X1.0000') == 'G291\nG00 X1.0000\nM30\n'
    assert 'G290' not in out


def test_heidenhain_iso_start_block_uses_din_iso_and_no_fanuc_resets():
    pp = PREPROCESSORS['Heidenhain_ISO']
    for units, word in (('MM', 'G71'), ('IN', 'G70')):
        start = pp.start_code(params(units=units))
        lines = start.splitlines()
        assert lines[0] == '%%FLATCAM %s *' % word
        assert_in_order(start, ['G17', 'G90', 'G40', 'M00'])
        assert start.rstrip().endswith('M00')
        assert '(SETUP:' not in start
        assert all(line.startswith((';', '%')) or re.match(r'^(G|M)\d', line) for line in lines)
        assert sum(line.count('*') for line in lines) == 1
        # TNC G20 is a working plane and G80 tilts it; G49/G94 and the FANUC compensation and offsets are not used
        for pattern in (r'^G2[01]\b', r'^G49\b', r'^G94\b', r'^G80\b', r'^G43', r'^G44', r'^G28\b', r'^G53\b',
                        r'^G54\b', r'^G99\b', r'^T\d', r'^M0?6\b'):
            assert not re.search(pattern, start, re.M), pattern


def test_heidenhain_iso_tool_change_and_spindle_blocks():
    pp = PREPROCESSORS['Heidenhain_ISO']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M05', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M00'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'reestablish' in g
    assert not re.search(r'^(T\d|M0?6|[HD]\d|G43|G44)\b', g, re.M)
    # the CNCjob writes the spindle start after the tool change; the tool change itself must not hide one
    assert 'M03' not in g and 'M04' not in g
    # the speed is set by the tool call of the tool that the tool change names
    assert pp.spindle_code(params(spindlespeed=10000, spindledir='CW')) == 'T1 G17 S10000\nM03'
    assert pp.spindle_code(params(spindlespeed=12000, spindledir='CCW', tool=3)) == 'T3 G17 S12000\nM04'
    assert 'Change to FlatCAM tool 3' in pp.toolchange_code(params(tool=3))
    assert pp.spindle_code(params(spindlespeed=None)) == 'M03'
    assert pp.spindle_stop_code(params()) == 'M05'


def test_heidenhain_iso_feeds_dwell_and_comments():
    pp = PREPROCESSORS['Heidenhain_ISO']
    mm = params(units='MM', feedrate=250.0, z_feedrate=60.0, dwelltime=1.234567)
    inch = params(units='IN', feedrate=250.0, z_feedrate=60.0, dwelltime=1.234567)
    assert pp.linear_code(mm).endswith(' F250.00')
    assert pp.linear_code(inch).endswith(' F2500.00')
    assert pp.down_code(mm) == 'G01 Z-1.5000 F60.00'
    assert pp.down_code(inch) == 'G01 Z-1.5000 F600.00'
    assert pp.up_to_zero_code(mm) == 'G01 Z0 F60.00'
    assert pp.up_to_zero_code(inch) == 'G01 Z0 F600.00'
    # the G01 moves carry their feed; no coordinate-free feed block
    assert pp.feedrate_code(mm) == '' and pp.z_feedrate_code(mm) == ''
    # G04 F is in seconds in both unit modes
    assert pp.dwell_code(mm) == 'G04 F1.234567'
    assert pp.dwell_code(inch) == 'G04 F1.234567'
    assert pp.dwell_code(params(dwelltime=0.00001)) == 'G04 F0.00001'
    assert pp.dwell_code(params(dwelltime=0)) == ''
    # generated comments are conservative blocks: no newline, no '*' and no trailing '~'
    assert pp._comment('a*b\nc~') == '; a b c'
    # the header shows the values of the job in the units of the job, not the 1/10 inch/min of the G70 blocks
    start = pp.start_code(params(units='IN', feedrate=100.0, z_feedrate=60.0, feedrate_rapid=1500.0))
    assert '; Feedrate_XY: 100.0 in/min' in start and '; Feedrate_Z: 60.0 in/min' in start
    assert '; Feedrate rapids 1500.0 in/min' in start


def test_heidenhain_iso_envelope_uses_the_saved_units_and_numbers_blocks():
    pp = PREPROCESSORS['Heidenhain_ISO']
    text = pp.finalize_gcode('%FLATCAM G70 *\nG17\nG90\nM30\n')
    lines = text.splitlines()
    assert lines[0] == '%FLATCAM G70 *'
    assert lines[-1] == 'N99999999 %FLATCAM G70 *'
    assert [re.match(r'^N(\d+) ', line).group(1) for line in lines[1:-1]] == ['10', '20', '30']
    # the user's M30 is the program end; no second M02 is added
    assert not re.search(r'^N\d+ M02 \*$', text, re.M)
    assert all(line.endswith('*') for line in lines[1:])
    # a standalone comment is a block of its own and is sanitized
    text = pp.finalize_gcode('%FLATCAM G71 *\n; a*b ~\nG17\n')
    assert text.splitlines()[1] == 'N10 ; a b *'
    assert text.splitlines()[-1] == 'N99999999 %FLATCAM G71 *'


def test_tinyg_start_block_selects_units_and_leaves_offsets_to_the_operator():
    pp = PREPROCESSORS['TinyG']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        assert_in_order(start, [select, 'G90', 'G17', 'G94'])
        # the work zero, path control and tool offset are the operator's: no compensation, work offset, reference
        # return, blending or tool call
        for pattern in (r'^G43', r'^G49', r'^G5[34]', r'^G28', r'^G53', r'^G6[14]', r'^M0?6\s*$', r'^T\d'):
            assert not re.search(pattern, start, re.M), pattern


def test_tinyg_manual_tool_change_stops_the_spindle_and_pauses_before_the_tool_operation():
    pp = PREPROCESSORS['TinyG']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M0'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'work Z' in g
    assert not re.search(r'^(T\d|M0?6)\s*$', g, re.M)
    # no configured tool-change X-Y: the safe Z retract and the pause only
    g = pp.toolchange_code(params())
    assert_in_order(g, ['M5', 'G00 Z15.0000', 'M0'])
    assert 'G00 X' not in g
    # M3/M4 drive the spindle at the set speed, M5 stops it
    assert pp.spindle_code(params(spindledir='CW')) == 'M3 S10000'
    assert pp.spindle_code(params(spindledir='CCW', spindlespeed=8000)) == 'M4 S8000'
    assert pp.spindle_stop_code(params()) == 'M5'


def test_tinyg_dwell_is_decimal_seconds():
    pp = PREPROCESSORS['TinyG']
    for time in (1.234, 10, 0.0001):
        code = pp.dwell_code(params(dwelltime=time))
        assert code.startswith('G4 P')
        assert float(code[4:]) == time
    assert pp.dwell_code(params(dwelltime=0)) == ''


def test_buildbotics_start_block_leaves_the_setup_to_the_controller():
    pp = PREPROCESSORS['Buildbotics']
    for units, select in (('MM', 'G21'), ('IN', 'G20')):
        start = pp.start_code(params(units=units))
        assert_in_order(start, [select, 'G90', 'G17', 'G94'])
        # no inferred compensation, work offset, reference return, blending, coolant or tool call
        for pattern in (r'^G43', r'^G49', r'^G5[34]', r'^G28', r'^G53', r'^G6[14]', r'^M0?6\s*$', r'^T\d',
                        r'^M0?[789]\s*$'):
            assert not re.search(pattern, start, re.M), pattern


def test_buildbotics_manual_tool_change_has_no_tool_call_or_m6():
    pp = PREPROCESSORS['Buildbotics']
    g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
    assert_in_order(g, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'M0'])
    assert 'FlatCAM tool 1' in g and 'Dia = 0.8000 mm' in g and 'work Z' in g
    assert not re.search(r'^(T\d|M0?6)\s*$', g, re.M)
    # no configured tool-change X-Y: the safe Z retract and the pause only
    g = pp.toolchange_code(params())
    assert_in_order(g, ['M5', 'G00 Z15.0000', 'M0'])
    assert 'G00 X' not in g


def test_buildbotics_dwell_and_spindle_match_the_camotics_interpreter():
    pp = PREPROCESSORS['Buildbotics']
    for time in (1.234, 10, 0.0001):
        code = pp.dwell_code(params(dwelltime=time))
        assert code.startswith('G4 P')
        assert float(code[4:]) == time
    assert pp.dwell_code(params(dwelltime=0)) == ''
    assert pp.spindle_code(params(spindledir='CW')) == 'M3 S10000'
    assert pp.spindle_code(params(spindledir='CCW', spindlespeed=8000)) == 'M4 S8000'
    assert pp.spindle_stop_code(params()) == 'M5'


def test_mach3_and_mach4_manual_tool_changes():
    for name, pause in (('Mach3', 'M0'), ('Mach4', 'M00 (')):
        pp = PREPROCESSORS[name]
        g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
        assert_in_order(g, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M6', pause])
    # Mach4 shows the tool message in the M00 block and reads a decimal P as seconds
    mach4 = PREPROCESSORS['Mach4']
    assert 'M00 (Change to Tool Dia = 0.8000)' in mach4.toolchange_code(params())
    assert mach4.dwell_code(params()) == 'G4 P1.000'


def test_planetcnc_and_uccnc_keep_an_explicit_manual_pause():
    # Neither controller is guaranteed to pause at M6: PlanetCNC needs Tool Change > Action > Message/Pause, and
    # UCCNC can ignore M6 or run a macro that returns. M0 and the tool message are written explicitly.
    for name in ('PlanetCNC', 'UCCNC'):
        pp = PREPROCESSORS[name]
        g = pp.toolchange_code(params(xy_toolchange=[5.0, 7.0]))
        assert_in_order(g, ['M5', 'G00 Z15.0000', 'G00 X5.0000 Y7.0000', 'T1', 'M6', 'M0'])
        assert '(MSG, Change to Tool Dia = 0.8000)' in g
    # PlanetCNC G4 P is seconds; UCCNC default G4 P is milliseconds
    assert PREPROCESSORS['PlanetCNC'].dwell_code(params()) == 'G4 P1.0'
    assert PREPROCESSORS['UCCNC'].dwell_code(params()) == 'G4 P1000'



def test_a_tiny_dwell_is_never_zero_or_scientific():
    for name in ('default', 'GRBL_11', 'LinuxCNC', 'Mach3', 'Mach4', 'UCCNC', 'PlanetCNC', 'FluidNC', 'TinyG',
                 'Buildbotics', 'Fanuc', 'Haas', 'Sinumerik', 'Heidenhain_ISO'):
        code = PREPROCESSORS[name].dwell_code(params(dwelltime=0.00001))
        match = re.fullmatch(r'G0?4 [XPF](\d+(\.\d+)?)', code)
        assert match and float(match.group(1)) > 0, (name, code)
    # normal values are unchanged
    assert PREPROCESSORS['default'].dwell_code(params(dwelltime=1.5)) == 'G4 P1.5'
    assert PREPROCESSORS['UCCNC'].dwell_code(params(dwelltime=0.0001)) == 'G4 P1'


def test_fanuc_family_tool_change_goes_to_the_start_z_for_the_first_tool():
    for name in ('Fanuc', 'Haas', 'Sinumerik'):
        pp = PREPROCESSORS[name]
        assert 'G00 Z20.0000' in pp.toolchange_code(params(startz=20.0, is_first_tool=True)), name
        assert 'G00 Z15.0000' in pp.toolchange_code(params(startz=20.0, is_first_tool=False)), name


def test_a_changed_parent_start_block_fails_loudly():
    from preprocessors.default import default
    from preprocessors.GRBL_11 import GRBL_11

    def parent(self, p):
        return 'G21\nG90\nG17\nG94\n'

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(GRBL_11, 'start_code', parent)
        patch.setattr(default, 'start_code', parent)
        for name in ('Fanuc', 'Haas', 'Sinumerik', 'LinuxCNC', 'FluidNC', 'TinyG', 'Buildbotics', 'Mach3', 'Mach4',
                     'UCCNC', 'PlanetCNC'):
            with pytest.raises(ValueError):
                PREPROCESSORS[name].start_code(params())


@pytest.mark.parametrize('xy', [[5.0, 7.0]])
def test_probe_tool_change(xy):
    pp = PREPROCESSORS['Toolchange_Probe_MACH3']
    # the spindle is off and the tool is retracted before the tool change, and the wait comes last
    g = pp.toolchange_code(params(xy_toolchange=xy))
    parts = ['M5', 'G00 Z15.0000'] + (['G00 X5.0000 Y7.0000'] if xy else []) + ['T1', 'M6']
    assert_in_order(g, parts)
    assert g.splitlines().index('M0') > g.splitlines().index('M6')
    # the probe touches a plate on top of the material: Z0 is the material top, and the retract between the
    # 2 probes never goes down into the plate
    for kind in ('Geometry', 'Excellon'):
        g = pp.toolchange_code(params(kind=kind, xy_toolchange=xy, probe_plate=1.5, z_move=2.0)).splitlines()
        assert g.count('G92 Z1.5000') == 2 and 'G92 Z0' not in g
        assert g.index('G92 Z1.5000') + 1 == g.index('G00 Z2.5000')
        assert 'Plate = 1.5000' in next(ln for ln in g if ln.startswith('(MSG, Change'))
        for plate in (0.0, None):
            g = pp.toolchange_code(params(kind=kind, xy_toolchange=xy, probe_plate=plate)).splitlines()
            assert g.count('G92 Z0') == 2 and 'G00 Z1.0000' in g
            assert 'Plate' not in ' '.join(g)
        g = pp.toolchange_code(params(kind=kind, xy_toolchange=xy)).splitlines()   # a job without the attribute
        assert g.count('G92 Z0') == 2 and 'G00 Z1.0000' in g


def test_dwell_fan_and_laser_power_values():
    # dwell: Marlin S is seconds, P is milliseconds; GRBL P is seconds; no time, no line
    for name, time, expected in (('Marlin', 1.0, 'G4 S1'), ('Marlin', 0.5, 'G4 P500'), ('Repetier', 0.5, 'G4 P500'),
                                 ('GRBL_11', 2, 'G4 P2')):
        pp = PREPROCESSORS[name]
        assert pp.dwell_code(params(dwelltime=time)) == expected, name
        assert pp.dwell_code(params(dwelltime=0)) == ''
    # the fan speed is clamped to 0..255
    cases = [
        ('Repetier', 'spindle_code', dict(spindlespeed=12000), 'M106 S255'),
        ('Repetier', 'spindle_code', dict(spindlespeed=127.6), 'M106 S128'),
        ('Marlin_laser_FAN_pin', 'down_code', dict(spindlespeed=12000), 'M106 S255'),
        ('Marlin_laser_FAN_pin', 'lift_code', dict(laser_min_power=10), 'M106 S10 ;laser OFF'),
        ('Marlin_laser_FAN_pin', 'lift_code', dict(laser_min_power=300), 'M106 S255 ;laser OFF'),
    ]
    for name, method, kw, expected in cases:
        assert expected in getattr(PREPROCESSORS[name], method)(params(**kw)), (name, expected)
    # no speed must not fire the laser at full power
    for spindlespeed in (None, 0):
        assert PREPROCESSORS['default_laser'].spindle_code(params(spindlespeed=spindlespeed)) == 'M3 S0'
    assert PREPROCESSORS['default_laser'].down_code(params(spindlespeed=500)) == 'M3 S500'


def test_no_preprocessor_swallows_an_exception():
    # every preprocessor and every method the CNCjob calls: a variant per value that takes another branch
    swallowed = []
    variants = (
        # the factory default of the end position (Milling and Drilling preferences) is None
        dict(end_xy=None),
        dict(end_xy=[0.0, 0.0], startz=3.0, use_ui=True, multidepth=True, z_depthpercut=0.5),
        dict(end_xy=[0.0, 0.0], xy_toolchange=None, spindlespeed=None, oldx=None, oldy=None, units='IN'),
    )
    for pp_name in GENERAL:
        pp = PREPROCESSORS[pp_name]
        for kind in ('Geometry', 'Excellon'):
            for variant in variants:
                variant = dict(variant)
                app, job = _job(kind, pp_name, end_xy=variant.pop('end_xy'))
                job.tools[1]['data'] = dict(DRILL_DATA)
                job.x, job.y, job.oldx, job.oldy, job.use_ui = 5.0, 6.0, 5.0, 6.0, False
                job.laser_on_code, job.laser_min_power, job.multigeo = 'M03', 0.0, True
                for key, value in variant.items():
                    setattr(job, key, value)
                for method in METHODS:
                    job.doformat2(getattr(pp, method))
                    if _exceptions(app):
                        swallowed.append((pp_name, kind, variant, method, str(_exceptions(app)[0])[-300:]))
                        app.log.error.reset_mock()

    assert swallowed == [], '\n'.join('%s %s %s %s: %s' % (s[0], s[1], s[2], s[3], s[4][-160:]) for s in swallowed)


def test_preprocessor_error_is_told_to_the_user_once_and_empty_methods_write_no_line():
    app, job = _job('Geometry', 'default')

    def broken(p):
        raise KeyError('missing')

    # the line is not dropped from the G-code: the job fails with the preprocessor method in the message
    with pytest.raises(RuntimeError, match=r"G-code is missing: .*broken - 'missing'"):
        job.doformat(broken)
    # a solder paste job keeps the error of the preprocessor
    job.pp_solderpaste_name = 'Paste_1'
    with pytest.raises(KeyError):
        job.doformat(broken)
    # the tool change of a laser preprocessor returns '': no empty line in the G-code
    assert job.doformat(lambda p: '') == ''


def test_bed_skew_with_a_zero_bed_size_fails_the_job():
    # the skew divided by the bed size 0 in every position: these lines were dropped and the job was made without its
    # X-Y moves (the laser went on and off at one spot)
    app, job = _job('Geometry', 'GRBL_laser')
    job._bed_skew_x, job._bed_limit_y = 0.5, 0
    data = dict(AppDefaults.factory_defaults)
    data.update(tools_mill_ppname_g='GRBL_laser', tools_mill_tooldia=0.1, tools_mill_spindlespeed=500,
                tools_mill_optimization_type='R')
    tools = {1: {'data': data, 'tooldia': 0.1, 'solid_geometry': [LineString([(0, 0), (2, 1)])]}}

    with pytest.raises(RuntimeError, match=r'GRBL_laser\.rapid_code - Bed-skew correction needs a non-zero bed size'):
        job.geometry_tool_gcode_gen(1, tools, (0, 0), (0, 0), 0.001)


def test_extra_cut_of_a_multi_pass_is_made_at_the_pass_depth():
    from shapely.geometry import LinearRing

    app, job = _job('Geometry', 'Line_xyz')
    job.x, job.y, job.oldx, job.oldy, job.area_exclusion = 0.0, 0.0, 0.0, 0.0, False
    job.z_feedrate, job.feedrate, job.feedrate_rapid = 60.0, 100.0, 1500.0
    job._bed_offset_x = job._bed_offset_y = job._bed_skew_x = job._bed_skew_y = 0.0
    job._bed_limit_x = job._bed_limit_y = 100.0
    ring = LinearRing([(0, 0), (10, 0), (10, 10), (0, 10)])

    gcode = job.linear2gcode_extra(ring, 0.8, 0.0, z_cut=-0.5, up=False, old_point=(0, 0))

    cut_depths = set(re.findall(r'^G01 X\S+ Y\S+ Z(\S+)$', gcode, re.M))
    assert cut_depths == {'-0.5000'}, gcode


@pytest.mark.parametrize('end_xy', [None])
def test_line_xyz_writes_its_start_block(end_xy):
    app, job = _job('Geometry', 'Line_xyz', end_xy=end_xy)

    code = job.doformat2(appPreProcessor.preprocessors['Line_xyz'].start_code)

    assert _exceptions(app) == []
    assert 'G21' in code and 'G90' in code and 'G94' in code
    if end_xy is None:
        assert '(X,Y End: None mm)' in code
    else:
        assert '(X,Y End: 3.0000, 4.0000 mm)' in code
