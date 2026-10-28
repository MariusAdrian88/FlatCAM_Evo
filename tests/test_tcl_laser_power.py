"""cncjob and drillcncjob (Tcl) with a laser preprocessor took the laser power only from -las_power and ignored
-spindlespeed, which is how scripts for older versions set it: the G-code had 'M03' without S. The tests make the real
G-code with the GRBL_laser preprocessor."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from shapely import LineString

from appObjects.CNCJobObject import CNCJobObject
from appObjects.GeometryObject import GeometryObject
from camlib import CNCjob
from preprocessors.GRBL_laser import GRBL_laser
from preprocessors.default import default
from tclCommands.TclCommandCncjob import TclCommandCncjob
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob

from tests.helpers_objects import _headless_qt, make_app, make_excellon  # noqa: F401,E402


def laser_app():
    app = make_app()
    app.preprocessors = {'default': default(), 'GRBL_laser': GRBL_laser()}
    app.dec_format = lambda value, decimals: round(value, decimals)
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[],
                                    travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    jobs = []

    def new_object(kind, name, initializer, **kwargs):
        job = CNCjob(app)
        job.kind, job.obj_options, job.tools = kind, {'name': name}, {}
        assert initializer(job, app) != 'fail'
        jobs.append(job)
        return job

    app.app_obj.new_object = Mock(side_effect=new_object)
    return app, jobs


def run(command_class, app, *argv):
    cmd = command_class(app)
    args, unnamed = cmd.check_args(('src',) + argv + ('-pp', 'GRBL_laser'))
    cmd.execute(args, unnamed)


def laser_on_lines(gcode):
    return {line for line in gcode.splitlines() if line.startswith('M03')}


def cncjob_gcode(*argv, power=None):
    app, jobs = laser_app()
    source = GeometryObject('src', app)
    source.solid_geometry = [LineString([(0, 0), (10, 0), (10, 10)])]
    source.multigeo = False
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    if power is not None:
        source.obj_options['tools_mill_spindlespeed'] = power
    app.collection.get_by_name = Mock(return_value=source)
    run(TclCommandCncjob, app, '-dia', '0.1', '-z_cut', '0', '-feedrate', '1000', *argv)
    return jobs[0].source_file


def drillcncjob_gcode(*argv, power=None):
    app, jobs = laser_app()
    source = make_excellon(app, name='src')
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    if power is not None:
        for tool in source.tools.values():
            tool['data']['tools_drill_spindlespeed'] = power
    app.collection.get_by_name = Mock(return_value=source)
    run(TclCommandDrillcncjob, app, '-drillz', '-1', '-travelz', '2', '-feedrate_z', '100', *argv)
    return jobs[0].source_file


@pytest.mark.parametrize('argv, expected', [
    (('-spindlespeed', '500'), {'M03 S500'}),
    (('-las_power', '300', '-spindlespeed', '500'), {'M03 S300'}),
    ((), {'M03 S0'}),
    (('-spindlespeed',), {'M03 S0'}),
], ids=['spindlespeed', 'las_power_first', 'no_power', 'bare_spindlespeed'])
def test_cncjob_laser_power(argv, expected):
    assert laser_on_lines(cncjob_gcode(*argv)) == expected


@pytest.mark.parametrize('argv, expected', [
    (('-spindlespeed', '500'), {'M03 S500.0'}),
    (('-las_power', '300', '-spindlespeed', '500'), {'M03 S300.0'}),
    ((), {'M03 S0'}),
], ids=['spindlespeed', 'las_power_first', 'no_power'])
def test_drillcncjob_laser_power(argv, expected):
    assert laser_on_lines(drillcncjob_gcode(*argv)) == expected


def test_without_las_power_the_laser_power_of_the_object_or_of_each_tool_is_used():
    """With neither -las_power nor -spindlespeed the job was made at power 0 (nothing burns), while the GUI and a
    multi-geometry object use the laser power of the object or of each tool."""
    assert laser_on_lines(cncjob_gcode(power=800)) == {'M03 S800'}
    assert laser_on_lines(drillcncjob_gcode(power=800)) == {'M03 S800'}
    # a power given in the command still wins
    assert laser_on_lines(cncjob_gcode('-las_power', '300', power=800)) == {'M03 S300'}
    assert laser_on_lines(drillcncjob_gcode('-las_power', '300', power=800)) == {'M03 S300.0'}


def test_cncjob_multigeo_laser_power_from_spindlespeed():
    source = Mock(kind='geometry', multigeo=True)
    source.tools = {7: {'data': {}, 'solid_geometry': [LineString([(0, 0), (1, 0)])]}}
    source.obj_options = {}
    app = make_app()
    app.collection.get_by_name = Mock(return_value=source)
    app.milling_tool = Mock()

    run(TclCommandCncjob, app, '-spindlespeed', '500')

    data = app.milling_tool.generate_cnc_job_handler.call_args.kwargs['tools_dict'][7]['data']
    assert data['tools_mill_spindlespeed'] == 500


# ---------------------------------------------------------------------------------------------------------------------
# cncjob with the Milling plugin options (offset, polishing) and from an Excellon, with the real Milling code


def milling_cncjob(make_source, *argv, pp='default'):
    from test_milling_single_geometry_tools import MillingHarness
    app, jobs = laser_app()
    source = make_source(app)
    app.preprocessors['default'] = default()
    app.regFK, app.ui, app.inform_no_echo = Mock(), Mock(), Mock()

    def new_object(kind, name, initializer, **kwargs):
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            job = CNCJobObject(name, app)
        assert initializer(job, app) != 'fail'
        jobs.append(job)
        return job

    app.app_obj.new_object = Mock(side_effect=new_object)
    app.milling_tool = MillingHarness(app=app, target_obj=None, sel_tools={}, units='MM', decimals=4, circle_steps=16,
                                      new_object=new_object)
    app.collection.get_by_name = Mock(return_value=source)
    cmd = TclCommandCncjob(app)
    args, unnamed = cmd.check_args(('src',) + argv + ('-pp', pp))
    result = cmd.execute(args, unnamed)
    assert jobs, (result, app.inform.emit.call_args_list)
    return jobs[0].source_file


def line_source(app):
    source = GeometryObject('src', app)
    source.solid_geometry = [LineString([(0, 0), (10, 0), (10, 10)])]
    source.multigeo = False
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    return source


def xy_values(gcode):
    import re
    return [(float(x), float(y)) for x, y in re.findall(r'G01 X(-?[\d.]+) Y(-?[\d.]+)', gcode)]


def test_cncjob_offset_type_moves_the_path_by_half_the_tool():
    path = xy_values(milling_cncjob(line_source, '-dia', '1', '-z_cut', '-1', '-feedrate', '100'))
    out = xy_values(milling_cncjob(line_source, '-dia', '1', '-z_cut', '-1', '-feedrate', '100',
                                   '-offset_type', 'out'))
    assert path and out and path != out
    assert min(y for _, y in path) == 0 and abs(min(y for _, y in out)) == pytest.approx(0.5, abs=0.01)


def test_cncjob_polishing_paints_the_bounding_box_with_margin():
    paint = xy_values(milling_cncjob(line_source,'-dia', '1', '-z_cut', '-0.1', '-feedrate', '100',
                                     '-job_type', 'polishing', '-polish_margin', '2', '-polish_method', 'standard'))
    assert paint and min(x for x, _ in paint) < 0 and max(x for x, _ in paint) > 10
    assert min(y for _, y in paint) < 0 and max(y for _, y in paint) > 10


def test_cncjob_mills_the_holes_of_an_excellon():
    def excellon_source(app):
        source = make_excellon(app, name='src')
        source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
        return source

    tools = excellon_source(make_app()).tools.values()
    holes = [(p.x, p.y) for tool in tools for p in tool['drills']]
    slot_ends = [(p.x, p.y) for tool in tools for slot in tool['slots'] for p in slot]
    gcode = milling_cncjob(excellon_source,'-mill_dia', '0.2', '-milling_type', 'drills', '-overlap', '10', '-z_cut', '-1',
                           '-feedrate', '100')
    cuts = xy_values(gcode)
    assert cuts
    x0, y0 = holes[0]
    assert all(any(abs(x - hx) < 2 and abs(y - hy) < 2 for hx, hy in holes) for x, y in cuts)
    assert any(abs(x - x0) < 1 and abs(y - y0) < 1 for x, y in cuts)
    # -milling_type drills: the slots are not milled
    assert not any(abs(x - sx) < 0.3 and abs(y - sy) < 0.3 for x, y in cuts for sx, sy in slot_ends)
