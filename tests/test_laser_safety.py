"""Laser Min Power warning.

Above 0 the 'laser' preprocessors write the laser minimum power in place of the laser off code
(e.g. 'M03 S250 (laser OFF)' instead of 'M5'), so the laser is not turned off between the cuts.
Each G-code generator warns when its G-code has these 'laser OFF' lines.
"""
import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString

from camlib import AttrDict, CNCjob, Geometry
from defaults import AppDefaults
from tests.helpers_objects import make_app_0b as make_app, make_excellon_0b as make_excellon  # noqa: F401,E402

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


@pytest.mark.parametrize('pp_name', LASER_PREPROCESSORS)
def test_the_warning_follows_the_laser_off_lines_of_each_laser_preprocessor(pp_name):
    app, gcode = milling_tool_job(pp_name, 250)
    # grbl_laser_eleks_drd does not use Min Power: it always turns the laser off with M05
    assert ('laser OFF' in gcode) is (pp_name != 'grbl_laser_eleks_drd')
    assert len(laser_warnings(app)) == (1 if 'laser OFF' in gcode else 0)


@pytest.mark.parametrize('min_power', [250, 0])
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


@pytest.mark.parametrize('min_power', [250, 0])
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

