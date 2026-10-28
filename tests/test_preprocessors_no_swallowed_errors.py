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


def test_roland_plunges_at_the_z_feedrate():
    # the down code set the XY feedrate over the Z feedrate set just before it: the plunge ran at the XY feed
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


@pytest.mark.parametrize('xy', [None, [5.0, 7.0]])
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


def test_start_blocks_of_laser_z_and_nccad_excellon():
    # a tool that misses keys does not lose the start block; the header shows the values, '-' when unset
    g = PREPROCESSORS['Marlin_laser_z'].start_code(params(
        use_ui=True, multigeo=True, tools={1: {'tooldia': 1, 'data': {'tools_drill_feedrate_z': 50}}}))
    for part in ['Feedrate: 50', 'Z: -', 'G90']:
        assert part in g
    g = PREPROCESSORS['GRBL_laser_z'].start_code(params(
        kind='Excellon', use_ui=True, multigeo=True,
        tools={1: {'tooldia': 0.8, 'data': {'tools_drill_feedrate_z': 50, 'tools_drill_travelz': 2,
                                            'tools_drill_spindlespeed': 300}},
               2: {'tooldia': 1.0, 'data': {}}}))
    for part in ['G21', 'G90', 'Feedrate: 50', 'Z: 2)', 'Power: 300', 'Power: -)']:
        assert part in g
    tools = {1: {'tooldia': 0.8, 'nr_drills': 3, 'data': dict(DRILL_DATA)}}
    assert 'G21\nG90\nG76' in PREPROCESSORS['NCCAD9'].start_code(params('Excellon', tools=tools))


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


@pytest.mark.parametrize('end_xy', [None, [3.0, 4.0]])
def test_line_xyz_writes_its_start_block(end_xy):
    app, job = _job('Geometry', 'Line_xyz', end_xy=end_xy)

    code = job.doformat2(appPreProcessor.preprocessors['Line_xyz'].start_code)

    assert _exceptions(app) == []
    assert 'G21' in code and 'G90' in code and 'G94' in code
    if end_xy is None:
        assert '(X,Y End: None mm)' in code
    else:
        assert '(X,Y End: 3.0000, 4.0000 mm)' in code
