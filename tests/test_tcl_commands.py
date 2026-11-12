"""Merged from: test_tcl_audit_generation: Headless Tcl command regressions: command-generation and factory failure contracts, cutting and clearing commands,; test_tcl_drillcncjob: Tcl drillcncjob: the values of each tool come from the tool data of the Excellon object (as in the Drilling plugin),; test_tcl_transform_and_export_format: Regression tests for the shared bounds in the Tcl mirror / skew commands and the Gerber export number format.; test_tcl_join_geometry: join_geometry (Tcl) must join like Edit -> Join.
"""

import importlib
import pytest
import re
import tests.helpers_objects as char
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from shapely import LineString, box
from shapely.ops import unary_union
from camlib import Geometry, CNCjob
from defaults import AppDefaults
from tclCommands.TclCommandAlignDrill import TclCommandAlignDrill
from tclCommands.TclCommandCncjob import TclCommandCncjob
from tclCommands.TclCommandCopperClear import TclCommandCopperClear
from tclCommands.TclCommandCutout import TclCommandCutout
from tclCommands.TclCommandMirror import TclCommandMirror
from tclCommands.TclCommandOffset import TclCommandOffset
from tclCommands.TclCommandPanelize import TclCommandPanelize
from tclCommands.TclCommandSplitGeometry import TclCommandSplitGeometry
from test_tcl_audit_regressions import FCShell, object_app
from shapely.geometry import Point, MultiLineString, MultiPolygon
from appParsers.ParseExcellon import Excellon
from preprocessors.GRBL_laser import GRBL_laser
from preprocessors.Toolchange_Probe_MACH3 import Toolchange_Probe_MACH3
from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
from tclCommands.TclCommandMillDrills import TclCommandMillDrills
from tests.helpers_objects import make_app_0b, app, _headless_qt, assert_xy_close, drill_xy, make_app_0b as make_app, make_excellon_0b as make_excellon, make_gerber, parts
from appObjects.GerberObject import GerberObject
from tclCommands.TclCommandSkew import TclCommandSkew
from tclCommands.TclCommandScale import TclCommandScale
from copy import deepcopy
from appObjects.GeometryObject import GeometryObject
from tclCommands.TclCommandJoinGeometry import TclCommandJoinGeometry
from tclCommands.TclCommandJoinGerber import TclCommandJoinGerber


# ==============================================================================================================
# test_tcl_audit_generation: Headless Tcl command regressions: command-generation and factory failure contracts, cutting and clearing commands,
# ==============================================================================================================

def factory_app(obj):
    app = object_app(obj)
    app.app_obj = Mock()
    app.options.update(AppDefaults.factory_defaults)
    app.app_units = 'MM'
    app.plotcanvas = Mock()
    app.proc_container = Mock()
    app.proc_container.new.side_effect = lambda *args: nullcontext()
    app.file_opened = Mock()
    return app

def test_export_gcode_accepts_both_documented_options_and_legacy_positionals():
    from tclCommands.TclCommandExportGcode import TclCommandExportGcode
    app = factory_app(Mock(kind='cncjob'))
    app.collection.has_promises.return_value = False
    cmd = TclCommandExportGcode(app)
    options, extra = cmd.check_args(('job', '-preamble', 'G0', '-postamble', 'M2'))
    positional, _ = cmd.check_args(('job', 'G0', 'M2'))
    assert options == positional
    cmd.execute(options, extra)
    app.collection.get_by_name.return_value.get_gcode.assert_called_once_with(preamble='G0', postamble='M2')

def test_write_gcode_propagates_explicit_failure():
    from tclCommands.TclCommandWriteGCode import TclCommandWriteGCode
    obj = Mock(kind='cncjob')
    obj.export_gcode.return_value = 'fail'
    app = factory_app(obj)
    assert TclCommandWriteGCode(app).execute({'name': 'job', 'filename': 'output'}, []) == 'fail'

def test_export_gcode_returns_the_text_that_write_gcode_writes(tmp_path):
    # export_gcode returned preamble + job.gcode + postamble: no start code, and for a multi-tool job of the Milling
    # tool job.gcode holds only the code of the last tool
    from types import MethodType
    from appObjects.CNCJobObject import CNCJobObject
    from preprocessors.default import default
    from tclCommands.TclCommandExportGcode import TclCommandExportGcode
    from tclCommands.TclCommandWriteGCode import TclCommandWriteGCode
    options = {'cncjob_footer': False, 'cncjob_prepend': '', 'cncjob_append': '', 'cncjob_line_ending': False}
    job = SimpleNamespace(
        kind='cncjob', app=SimpleNamespace(preprocessors={'default': default()}), multitool=True,
        obj_options={'type': 'Geometry', 'name': 'job'}, gc_header='', gc_start='START_CODE\n', gcode='BODY_TOOL_2\n',
        tools={uid: {'data': {'tools_mill_ppname_g': 'default'}, 'gcode': 'BODY_TOOL_%d\n' % uid} for uid in (1, 2)},
        _app_option=lambda key, fallback=None: options.get(key, fallback), gcode_footer=lambda: '')
    job.get_gcode = MethodType(CNCJobObject.get_gcode, job)
    job.export_gcode = MethodType(CNCJobObject.export_gcode, job)
    app = factory_app(job)
    app.collection.has_promises.return_value = False
    snippets = {'preamble': '(PRE)', 'postamble': '(POST)'}
    text = TclCommandExportGcode(app).execute(dict(snippets, name='job'), [])
    TclCommandWriteGCode(app).execute(dict(snippets, name='job', filename=str(tmp_path / 'job.nc')), [])
    assert text == (tmp_path / 'job.nc').read_text()
    assert text.index('START_CODE') < text.index('(PRE)') < text.index('BODY_TOOL_1') < text.index('BODY_TOOL_2')

def test_new_object_commands_propagate_factory_failure():
    from appObjects.AppObject import AppObject
    factory = SimpleNamespace(new_object=Mock(return_value='fail'))
    app = factory_app(None)
    app.app_obj = SimpleNamespace(new_geometry_object=lambda **kw: AppObject.new_geometry_object(factory, **kw))
    cls = getattr(importlib.import_module('tclCommands.TclCommandNewGeometry'), 'TclCommandNewGeometry')
    assert cls(app).execute({'name': 'output'}, []) == 'fail'

def test_real_open_handler_reports_missing_tcl_input(tmp_path):
    from appHandlers.appIO import appIO
    app = factory_app(None)
    app.defaults = {}
    app.options['global_tcl_path'] = str(tmp_path)
    handler = SimpleNamespace(app=app, log=app.log, inform=app.inform)
    assert appIO.open_gerber(handler, 'missing-input-file', from_tcl=True) == 'fail'
    app.file_opened.emit.assert_not_called()

def test_object_factory_rejects_initializer_false_without_publishing():
    from appObjects.AppObject import AppObject
    from unittest.mock import patch
    app = factory_app(None)
    app.main_thread = None
    obj = SimpleNamespace(units='MM', obj_options={}, tool_option_prefix='', inherits_mill_options=False,
                          bounds=Mock(return_value=(0, 0, 1, 1)), moveToThread=Mock())
    factory = SimpleNamespace(app=app, _app_option=app.options.get, object_created=Mock(),
                              load_context=SimpleNamespace(batch=None))
    factory._new_object = lambda *a: AppObject._new_object(factory, *a)
    with patch('appObjects.AppObject.GeometryObject', return_value=obj):
        assert AppObject.new_object(factory, 'geometry', 'out', lambda *_: False, plot=False) == 'fail'
    factory.object_created.emit.assert_not_called()

def test_set_active_reports_selection_failure_as_tcl_error():
    from tclCommands.TclCommandSetActive import TclCommandSetActive
    app = factory_app(None)
    app.collection.set_active.side_effect = ValueError('missing object')
    app.shell.tcl.createcommand('set_active', TclCommandSetActive(app).execute_wrapper)
    assert app.shell.tcl.eval('catch {set_active missing} msg') == '1'
    assert 'missing object' in app.shell.tcl.eval('set msg')

def test_alignment_drills_install_tools_before_geometry_creation():
    from appParsers.ParseExcellon import Excellon
    source = SimpleNamespace(kind='geometry', bounds=lambda: (10, 10, 20, 20))
    app = factory_app(source)
    app.decimals = 4
    app.abort_flag = False
    app.dec_format = lambda value, decimals: round(value, decimals)
    output = Excellon(app)
    output.regenerate_source = Mock()

    def create(kind, name, init, **kwargs):
        assert init(output, app) != 'fail'
        return output

    app.app_obj.new_object.side_effect = create
    TclCommandAlignDrill(app).execute({'name': 'g', 'axis': 'X', 'dia': 1,
                                       'holes': '(1,2),(3,4)', 'dist': 0}, [])
    assert len(output.tools[1]['drills']) == 4
    assert len(output.solid_geometry) == 4

@pytest.mark.parametrize('argv', [('-offset_type', 'sideways')])
def test_cncjob_rejects_wrong_milling_options(argv):
    obj = Mock(kind='geometry', multigeo=True, tools={})
    app = factory_app(obj)
    app.milling_tool = Mock()
    cmd = TclCommandCncjob(app)
    args, unnamed = cmd.check_args(('g',) + argv)
    with pytest.raises(Exception):
        cmd.execute(args, unnamed)
    app.milling_tool.generate_cnc_job_handler.assert_not_called()

def test_split_geometry_populates_root_and_only_the_selected_tool():
    obj = SimpleNamespace(kind='geometry', tools={7: {'solid_geometry': [box(0, 0, 1, 1)]}})
    app = factory_app(obj)
    output = SimpleNamespace(tools={1: {'solid_geometry': []}})
    app.app_obj.new_object.side_effect = lambda kind, name, init, **kw: init(output, app)
    TclCommandSplitGeometry(app).execute({'source_name': 'g'}, [])
    assert list(output.tools) == [7]
    assert output.solid_geometry[0].equals(obj.tools[7]['solid_geometry'][0])

def _ncc_run(args, source_kind='gerber', other=None, options=None):
    objects = {'g': SimpleNamespace(kind=source_kind, decimals=4)}
    objects.update(other or {})
    app = factory_app(objects['g'])
    app.options.update(options or {})
    app.collection.get_by_name.side_effect = lambda n: objects.get(n)
    app.ncclear_tool = Mock()
    app.ncclear_tool.gen.clear_copper_tcl.return_value = None
    app.dec_format = lambda value, decimals: round(value, decimals)
    TclCommandCopperClear(app).execute(dict({'name': 'g'}, **args), [])
    return app.ncclear_tool.gen.clear_copper_tcl.call_args.kwargs

def test_ncc_rejects_inconsistent_new_options():
    other = {'lines': SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 1, 1).exterior])}
    with pytest.raises(FCShell.TclErrorException):
        _ncc_run({'all': None, 'area': '0,0,1,1'}, source_kind='gerber', other=other)

def test_ncc_crosshatch_method_and_angle_reach_the_tool_data():
    kwargs = _ncc_run({'all': None, 'method': 'crosshatch', 'angle': 45.0})
    tool_data = kwargs['tools_storage'][1]['data']
    assert tool_data['tools_ncc_method'] == 4
    assert tool_data['tools_ncc_crosshatch_angle'] == 45.0

def test_ncc_crosshatch_rejects_invalid_angles():
    for angle in (float('nan'), float('inf'), 'invalid'):
        with pytest.raises(FCShell.TclErrorException):
            _ncc_run({'all': None, 'method': 'crosshatch', 'angle': angle})

def test_ncc_crosshatch_preference_honors_explicit_angle_and_validates_it():
    options = {'tools_ncc_method': 4, 'tools_ncc_crosshatch_angle': 10.0}
    kwargs = _ncc_run({'all': None, 'angle': -45.0}, options=options)
    assert kwargs['tools_storage'][1]['data']['tools_ncc_crosshatch_angle'] == 135.0
    assert kwargs['angle'] == 135.0
    with pytest.raises(FCShell.TclErrorException):
        _ncc_run({'all': None, 'angle': float('nan')}, options=options)

def test_cutout_keeps_all_input_components():
    source = SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 2, 2).exterior,
                                                              box(10, 0, 12, 2).exterior],
                             bounds=lambda: (0, 0, 12, 2))
    app = factory_app(source)
    app.cutout_tool = Mock()
    app.cutout_tool.any_cutout_handler.side_effect = lambda geo, *args: ([geo], [])
    output = SimpleNamespace(obj_options={})
    app.app_obj.new_object.side_effect = lambda kind, name, init, **kw: init(output, app)
    TclCommandCutout(app).execute({'name': 'g', 'dia': 1, 'gaps': 'none'}, [])
    assert unary_union(output.solid_geometry).bounds == (0, 0, 12, 2)

def test_panelize_factory_failure_is_not_announced_as_done():
    obj = SimpleNamespace(kind='geometry', bounds=lambda: (0, 0, 1, 1))
    app = factory_app(obj)
    app.abort_flag = False
    app.app_obj.new_object.return_value = 'fail'
    result = TclCommandPanelize(app).execute({'name': 'g', 'rows': 2}, [])
    assert result == 'fail'
    assert not any('[success]' in str(call) for call in app.inform.emit.call_args_list)

def paint_command(units='MM', **options):
    from tclCommands.TclCommandPaint import TclCommandPaint
    obj = SimpleNamespace(kind='geometry', decimals=4)
    app = factory_app(obj)
    app.app_units = units
    app.decimals = 4
    app.dec_format = lambda value, decimals: round(value, decimals)
    app.options.update(options)
    app.paint_tool = SimpleNamespace(gen=Mock())
    return TclCommandPaint(app), app, app.paint_tool.gen

def test_paint_reports_bad_values_and_failures_to_the_script():
    command, app, gen = paint_command()
    for args in ({'method': 'zigzag'}, {'overlap': 100.0}, {'overlap': -1.0}, {'rest_offset': float('nan')},
                 {'offset': float('inf')}, {'simplification': -0.1}):
        with pytest.raises(FCShell.TclErrorException):
            command.execute(dict(args, name='g'), [])
    for method, extra in (('paint_poly_all', {}), ('paint_poly', {'single': '1,1'}),
                          ('paint_poly_ref', {'ref': 'r'}), ('paint_poly_area_worker', {'area': '0,0,1,1'})):
        getattr(gen, method).return_value = 'fail'
        with pytest.raises(FCShell.TclErrorException):
            command.execute(dict(extra, name='g'), [])

def test_paint_crosshatch_method_and_angle_reach_the_tool_data():
    command, app, gen = paint_command()
    command.execute({'name': 'g', 'method': 'crosshatch', 'angle': 45.0}, [])
    tool_data = gen.paint_poly_all.call_args.kwargs['tools_storage'][1]['data']
    assert tool_data['tools_paint_method'] == 5
    assert tool_data['tools_paint_crosshatch_angle'] == 45.0

def test_paint_crosshatch_rejects_invalid_angles():
    command, app, gen = paint_command()
    for angle in (float('nan'), float('inf'), 'invalid'):
        with pytest.raises(FCShell.TclErrorException):
            command.execute({'name': 'g', 'method': 'crosshatch', 'angle': angle}, [])
    gen.paint_poly_all.assert_not_called()

def geometry(multigeo):
    app = Mock(app_units='MM', decimals=4)
    geo = Geometry(app)
    geo.kind = 'geometry'
    geo.obj_options = {}
    geo.multigeo = multigeo
    a, b = LineString([(0, 0), (3, 0)]), LineString([(2, 0), (5, 0)])
    geo.solid_geometry = [a, b]
    geo.tools = {7: {'solid_geometry': [a], 'data': {'depth': -1}},
                 9: {'solid_geometry': [b], 'data': {'depth': -2}}}
    return geo

def test_path_subtraction_updates_cnc_tools_and_bounds():
    geo = geometry(True)
    geo.subtract_polygon(box(-1, -1, 1, 1))
    paths = unary_union([g for tool in geo.tools.values() for g in tool['solid_geometry']])
    assert paths.equals(unary_union(geo.solid_geometry))
    assert paths.intersection(box(-1, -1, 0.9, 1)).is_empty
    assert geo.obj_options['xmin'] == 1
    assert geo.tools[9]['data']['depth'] == -2

def test_union_updates_each_tool_without_mixing_multigeometry_depths():
    geo = geometry(True)
    geo.tools[7]['solid_geometry'].append(LineString([(1, 0), (4, 0)]))
    geo.union()
    assert sum(g.length for g in geo.tools[7]['solid_geometry']) == 4
    assert unary_union(geo.tools[7]['solid_geometry']).bounds == (0, 0, 4, 0)
    assert unary_union(geo.tools[9]['solid_geometry']).bounds == (2, 0, 5, 0)
    assert unary_union(geo.solid_geometry).bounds == (0, 0, 5, 0)

@pytest.mark.parametrize('tool_id', ['7'])
def test_real_drill_generator_keeps_tool_ids_and_stores_text_source(tool_id):
    from camlib import CNCjob
    from tclCommands.TclCommandDrillcncjob import TclCommandDrillcncjob
    from tests.helpers_objects import make_app_0b as make_app, make_excellon_0b as make_excellon  # noqa: F401,E402
    app = make_app(tools_drill_toolchange=False)
    app.exc_areas = SimpleNamespace(exclusion_areas_storage=[],
                                    travel_coordinates=lambda **kw: [(None, kw['end_point'])])
    source = make_excellon(app, drills=((1, 1), (3, 2)), slots=())
    source.tools = {tool_id: source.tools[1]}
    source.obj_options.update(app.options)
    source.obj_options.update(zip(('xmin', 'ymin', 'xmax', 'ymax'), source.bounds()))
    app.collection.get_by_name.return_value = source
    job = CNCjob(app)
    job.kind = 'cncjob'
    job.obj_options = {}
    job.doformat = lambda *args, **kwargs: ''
    job.excellon_tool_gcode_parse = lambda *args, **kwargs: []

    def create(kind, name, init, **kwargs):
        result = init(job, app)
        assert result != 'fail'
        return job

    app.app_obj.new_object.side_effect = create
    TclCommandDrillcncjob(app).execute({'name': 'e', 'dpp': None, 'startz': 0, 'endz': 0}, [])
    assert tool_id in job.tools
    assert isinstance(job.source_file, str)
    assert job.startz == job.z_end == 0

def test_unsupported_later_target_does_not_partially_transform():
    first = Mock(kind='geometry', obj_options={})
    first.bounds.return_value = (0, 0, 1, 1)
    second = Mock(kind='cncjob')
    second.bounds.return_value = (0, 0, 1, 1)
    app = object_app(first)
    app.collection.get_by_name.side_effect = lambda n: {'a': first, 'b': second}.get(n)
    try:
        TclCommandOffset(app).execute({'x': 1}, ['a', 'b'])
    except FCShell.TclErrorException:
        pass
    first.offset.assert_not_called()

def test_invalid_mirror_axis_is_rejected_before_mutation():
    obj = Mock(kind='geometry', obj_options={})
    obj.bounds.return_value = (0, 0, 1, 1)
    with pytest.raises(FCShell.TclErrorException):
        TclCommandMirror(object_app(obj)).execute({'axis': 'invalid'}, ['a'])
    obj.mirror.assert_not_called()

def test_single_object_origin_refreshes_bounds_source_and_dirty_state():
    from tclCommands.TclCommandSetOrigin import TclCommandSetOrigin
    obj = Mock(kind='geometry', obj_options={})
    obj.bounds.return_value = (1, 2, 3, 4)
    app = object_app(obj)
    app.app_obj = Mock()
    TclCommandSetOrigin(app).execute({'loc': '4,8', 'obj_name': 'g'}, [])
    obj.offset.assert_called_once_with((3, 6))
    obj.regenerate_source.assert_called_once_with()
    app.app_obj.object_changed.emit.assert_called_once_with(obj)
    assert app.should_we_save is True
    assert obj.obj_options['xmin'] == 1
    assert '3.0000, 6.0000' in app.inform_shell.emit.call_args.args[0]


# ==============================================================================================================
# test_tcl_drillcncjob: Tcl drillcncjob: the values of each tool come from the tool data of the Excellon object (as in the Drilling plugin),
# ==============================================================================================================

RAPID_RE = re.compile(r'^G00 X(-?\d+\.\d+) Y(-?\d+\.\d+)$')

def make_app_tcl_drillcncjob(**options):
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
    app = make_app_tcl_drillcncjob(**{'tools_drill_toolchange': False, **(options or {})})
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

def test_the_optimization_type_defaults_to_the_application_option():
    tools = {1: (0.8, [(8.0, 8.0), (1.0, 1.0)], (), {})}
    assert drilled_positions(run(tools, options=dict(excellon_optimization_type='No'))[2]) == [(8.0, 8.0), (1.0, 1.0)]
    assert drilled_positions(run(tools, options=dict(excellon_optimization_type='R'))[2]) == [(1.0, 1.0), (8.0, 8.0)]
    assert drilled_positions(run(tools, ['-opt_type', 'No'], options=dict(excellon_optimization_type='R'))[2]) == \
        [(8.0, 8.0), (1.0, 1.0)]

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

def test_the_holes_of_aligndrillgrid_are_drilled_with_the_tool_change():
    """aligndrillgrid gave its tool the id '1' (text): the toolchange code of the preprocessors reads
    p['tools'][int(p['tool'])], so the tool change block was left out with 'Preprocessor error, G-code is missing'."""
    from tclCommands.TclCommandAlignDrillGrid import TclCommandAlignDrillGrid
    app = make_app_tcl_drillcncjob()
    made = []

    def create(kind, name, init, **kwargs):
        grid = Excellon(app)
        grid.units = 'MM'
        grid.obj_options = {'name': name}
        grid.regenerate_source = Mock()
        init(grid, app)
        made.append(grid)

    app.app_obj.new_object.side_effect = create
    command = TclCommandAlignDrillGrid(app)
    command.execute(*command.check_args(('-dia', '3', '-gridx', '10', '-gridy', '10', '-columns', '2', '-rows', '1')))
    grid, = made
    tools = {tid: (tool['tooldia'], [(p.x, p.y) for p in tool['drills']], (), {}) for tid, tool in grid.tools.items()}

    app, __, code = run(tools, ['-toolchange', 1, '-toolchangez', 30])
    assert [line for line in code.splitlines() if line in ('T1', 'M6')] == ['T1', 'M6']
    assert sorted(drilled_positions(code)) == [(0.0, 0.0), (10.0, 0.0)]
    assert not any('G-code is missing' in str(c.args[0]) for c in app.inform.emit.call_args_list)

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

def test_peck_and_dwell_at_the_bottom_arguments():
    tools = {1: (0.8, [(1.0, 1.0)], (), {})}
    code = run(tools, ['-drillz', -3, '-f_retract', 1, '-peck', 1, '-peck_depth', 1, '-peck_retract', 0.3,
                       '-dwell_bottom', 1, '-dwelltime', 0.5, '-dwell', 0])[2].splitlines()
    z = [line for line in code if line.startswith(('G00 Z', 'G01 Z', 'G4'))]
    first = z.index('G01 Z-1.0000')
    assert z[first:first + 9] == [
        'G01 Z-1.0000', 'G00 Z2.0000', 'G00 Z-0.7000', 'G01 Z-2.0000', 'G00 Z2.0000', 'G00 Z-1.7000',
        'G01 Z-3.0000', 'G4 P0.5', 'G00 Z2.0000']

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

XF_TOOLS = {1: (0.8, [(0.0, 0.0), (4.0, 2.0)], (), {}), 2: (1.0, [(2.0, 4.0)], (), {})}

def test_job_offset_mirror_and_array_args_transform_the_holes():
    base = sorted(drilled_positions(run(XF_TOOLS, ['-opt_type', 'No'])[2]))
    assert base == [(0.0, 0.0), (2.0, 4.0), (4.0, 2.0)]
    moved = sorted(drilled_positions(run(XF_TOOLS, ['-job_offset', '10,-5'])[2]))
    assert moved == [(10.0, -5.0), (12.0, -1.0), (14.0, -3.0)]
    mirrored = sorted(drilled_positions(run(XF_TOOLS, ['-mirror', 'y'])[2]))
    assert mirrored == [(0.0, 2.0), (2.0, 4.0), (4.0, 0.0)]
    mirrored = sorted(drilled_positions(run(XF_TOOLS, ['-mirror', 'xy', '-mirror_point', '0,0'])[2]))
    assert mirrored == [(-4.0, -2.0), (-2.0, -4.0), (0.0, 0.0)]
    grid = drilled_positions(run(XF_TOOLS, ['-array', '2,2', '-array_pitch', '100,50'])[2])
    assert len(grid) == 12 and (104.0, 52.0) in grid and (2.0, 54.0) in grid
    __, job, __ = run(XF_TOOLS, ['-array', '2,1', '-array_pitch', '20,0', '-toolchange', 1])
    assert job.tools[1]['nr_drills'] == 4 and job.tools[2]['nr_drills'] == 2

TWO_OBJECTS = {'second': {1: (0.8, [(70.0, 70.0)], (), {}), 2: (2.0, [(80.0, 80.0)], (), {})}}

def test_objects_argument_drills_the_holes_of_the_extra_objects_merged_by_diameter():
    __, job, code = run(TWO, ['-toolchange', 1, '-objects', 'second'], extra=TWO_OBJECTS)
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (5.0, 5.0), (70.0, 70.0), (80.0, 80.0)]
    assert sorted(job.tools) == [1, 2, 3]
    assert job.tools[1]['nr_drills'] == 2 and job.tools[3]['tooldia'] == 2.0
    assert job.obj_options['xmax'] == 90 and job.obj_options['xmin'] == 0

def test_area_argument_drills_only_the_holes_inside_the_rectangle():
    tools = {1: (0.8, [(1.0, 1.0), (9.0, 9.0)], (), {}), 2: (1.0, [(30.0, 30.0)], (), {})}
    __, job, code = run(tools, ['-area', '0,0,10,10', '-toolchange', 1])
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (9.0, 9.0)]
    assert sorted(job.tools) == [1, 2] and 2 not in [t for t, __ in blocks(code)]
    __, __, code = run(tools, ['-area', '10,10,0,0'])
    assert sorted(drilled_positions(code)) == [(1.0, 1.0), (9.0, 9.0)]

def milled_tools(dias):
    obj = Mock(kind='excellon', decimals=4)
    obj.tools = {tid: {'tooldia': dia} for tid, dia in {1: 0.8, 2: 1.0}.items()}
    obj.generate_milling_drills = Mock(
        side_effect=lambda **kw: (True, '') if kw['tools'] else (False, 'Error: No tools.'))
    app = make_app_tcl_drillcncjob()
    app.collection.get_by_name.side_effect = lambda n: obj
    app.collection.has_promises.return_value = False
    command = TclCommandMillDrills(app)
    args, unnamed = command.check_args(('board', '-milled_dias', dias, '-tooldia', '0.1'))
    command.execute(args, unnamed)
    return obj.generate_milling_drills.call_args.kwargs['tools']

def test_drilled_dias_process_only_the_matching_tools_and_no_match_fails():
    __, job, __ = run(TWO, ['-toolchange', 1, '-drilled_dias', '0.8,3.0'])
    assert set(tool_codes(job)) == {1}
    assert milled_tools('0.8,3.0') == {1}
    with pytest.raises(Exception):
        run(TWO, ['-drilled_dias', '3.0'])
    with pytest.raises(Exception):
        milled_tools('3.0')


# ==============================================================================================================
# test_tcl_transform_and_export_format: Regression tests for the shared bounds in the Tcl mirror / skew commands and the Gerber export number format.
# ==============================================================================================================

def _run(cmd_cls, objs, *argv):
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)

def _two_excellons():
    a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
    b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 2.0),), slots=())
    return a, b

def _expected_mirror(drills, internal_axis, point):
    ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
    ref.mirror(internal_axis, point)
    return drill_xy(ref)

class TestTclMirror:
    def test_two_objects_share_one_center(self):
        a, b = _two_excellons()
        _run(TclCommandMirror, {'a': a, 'b': b}, '-axis', 'X', '-origin', 'center')
        # the Tcl command swaps the axis name to work like the GUI: -axis X is mirror('Y', ...)
        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [2.0, 1.0]))
        assert_xy_close(drill_xy(b), _expected_mirror(((4.0, 2.0),), 'Y', [2.0, 1.0]))

    def test_box_path_refreshes_the_bounds_options(self):
        a, _ = _two_excellons()
        box = make_excellon(default_data={}, tooldia=0.2, drills=((10.0, 10.0),), slots=())
        app_objs = {'a': a}

        app = make_app()
        app.collection.get_by_name.side_effect = lambda n: {'a': a, 'box': box}.get(n)
        cmd = TclCommandMirror(app)
        args, unnamed = cmd.check_args(tuple(app_objs) + ('-axis', 'X', '-box', 'box'))
        cmd.execute(args, unnamed)

        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [10.0, 10.0]))
        xmin, ymin, xmax, ymax = a.bounds()
        assert a.obj_options['xmin'] == xmin and a.obj_options['ymin'] == ymin
        assert a.obj_options['xmax'] == xmax and a.obj_options['ymax'] == ymax

class TestTclSkew:
    def test_two_objects_share_one_reference_point(self):
        a, b = _two_excellons()
        _run(TclCommandSkew, {'a': a, 'b': b}, '-x', '10', '-y', '0', '-origin', 'center')

        def expected(drills):
            ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
            ref.skew(10.0, 0.0, point=(2.0, 1.0))
            return drill_xy(ref)

        assert_xy_close(drill_xy(a), expected(((0.0, 0.0),)), tol=1e-6)
        assert_xy_close(drill_xy(b), expected(((4.0, 2.0),)), tol=1e-6)

GERBER_NEGATIVE_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,1.0*%", "D10*", "X15000Y-22500D03*", "M02*"]

def _exported_body(g_zeros):
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        obj = GerberObject('g', make_app())
    obj.parse_lines(GERBER_NEGATIVE_FLASH)
    return obj.export_gerber(whole=2, fract=4, g_zeros=g_zeros)

class TestGerberExportNumberFormat:
    def test_trailing_zeros_keep_the_sign_in_front_of_the_padding(self):
        assert 'X015000Y-022500D03' in _exported_body('T')

    @pytest.mark.parametrize('g_zeros,whole,fract', [('L', 3, 6), ('T', 2, 4)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, g_zeros, whole, fract, tmp_path):
        """appIO.export_gerber -> parser: the Gerber spec 4.2.2 (L) and 8.2.1.1 (T, deprecated) formats."""
        app = char.make_app()
        app.options.update({'gerber_exp_zeros': g_zeros, 'gerber_exp_integer': whole, 'gerber_exp_decimals': fract})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            grb = char.make_gerber(app, char.NATIVE_GBR)
            # negative coordinates on both axes and a fraction with leading decimal zeros
            grb.offset((-20.05, -20.003))
            text = char.export_gerber_text(app, grb)
            assert '%%FS%sAX%d%dY%d%d*%%' % (g_zeros, whole, fract, whole, fract) in text
            again = char.reparse_gerber(app, text, tmp_path)
        assert again.bounds()[0] < 0 and again.bounds()[1] < 0
        char.assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=10 ** -fract * 50,
                               msg='%s %d.%d' % (g_zeros, whole, fract))

def _flat(items):
    return [c for item in items for c in item]

class TestExcellonExportNumberFormat:
    """appIO.export_excellon -> parser, without the decimal point (Excellon CNC-7 rules):
    LZ: the leading zeros are written, the reader adds the trailing zeros, so the whole part has 'whole' digits;
    TZ: the trailing zeros are written, the reader counts the decimals from the right, so no padding."""

    @pytest.mark.parametrize('slot_type,e_zeros,whole,fract', [('routing', 'LZ', 3, 3), ('drilling', 'TZ', 2, 4)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, e_zeros, whole, fract, slot_type,
                                                                     tmp_path):
        app = char.make_app()
        app.options.update({'excellon_exp_format': 'ndec', 'excellon_exp_zeros': e_zeros,
                            'excellon_exp_integer': whole, 'excellon_exp_decimals': fract,
                            'excellon_exp_units': 'METRIC', 'excellon_exp_slot_type': slot_type})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            exc = char.make_excellon(app)
            # negative coordinates on both axes, values below 1 and a fraction with leading decimal zeros
            exc.offset((-9.95, -10.05))
            text = char.export_excellon_text(app, exc)
            assert 'METRIC,%s' % e_zeros in text
            again = char.reparse_excellon(app, text, tmp_path)

        tol = 10 ** -fract
        assert sorted(again.tools) == sorted(exc.tools)
        for t, tool in exc.tools.items():
            msg = '%s %d.%d %s T%s' % (e_zeros, whole, fract, slot_type, t)
            # compare the numbers directly (a drill has zero area); the counts must match first
            assert len(again.tools[t]['drills']) == len(tool['drills']), msg
            assert _flat(char.pts(again.tools[t]['drills'])) == pytest.approx(_flat(char.pts(tool['drills'])),
                                                                              abs=tol), msg
            assert len(again.tools[t].get('slots', [])) == len(tool['slots']), msg
            assert _flat(char.slot_flat(again.tools[t].get('slots', []))) == \
                pytest.approx(_flat(char.slot_flat(tool['slots'])), abs=tol), msg

class TestTclBufferJoin:
    """-join 2 / square is the square corner join: the Shapely 'mitre' join ('square' is a cap style)."""

    @pytest.mark.parametrize('join', ['square'])
    def test_square_join_buffers_the_gerber(self, join):
        from appParsers.ParseGerber import Gerber
        from tclCommands.TclCommandBuffer import TclCommandBuffer
        grb = Gerber(make_app())
        grb.parse_lines(["%FSLAX24Y24*%", "%MOMM*%", "%ADD10R,2.0X1.0*%", "D10*", "X100000Y50000D03*", "M02*"])
        grb.obj_options = {}
        grb.kind = 'gerber'
        xmin, ymin, xmax, ymax = grb.bounds()

        app = make_app()
        app.collection.get_by_name.return_value = grb
        cmd = TclCommandBuffer(app)
        args, unnamed = cmd.check_args(('grb', '-dist', '0.1', '-join', join))
        assert cmd.execute(args, unnamed) != 'fail'

        assert grb.bounds() == pytest.approx((xmin - 0.1, ymin - 0.1, xmax + 0.1, ymax + 0.1), abs=1e-6)

def _tcl_scale(objs, *argv):
    """objs: dict name -> object. Runs ``scale <names> <argv>``."""
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = TclCommandScale(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)

def _scale_target():
    return make_excellon(default_data={}, tooldia=0.2, drills=((1.0, 1.0), (3.0, 2.0)), slots=())

class TestTclScale_0B5:
    def test_x_alone_scales_x_only(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 1.0), (6.0, 2.0)])


    def test_two_objects_axis_path_share_one_center(self):
        # default origin = center of the bounds of ALL named objects, computed once
        a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
        b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 0.0),), slots=())
        _tcl_scale({'a': a, 'b': b}, '-x', '2', '-y', '2')
        assert_xy_close(drill_xy(a), [(-2.0, 0.0)])
        assert_xy_close(drill_xy(b), [(6.0, 0.0)])

@pytest.mark.parametrize('cmd_cls, argv, moved', [
    (TclCommandOffset, ('-x', '10', '-y', '5'), r'G0 X11(\.0*)? Y6'),
    (TclCommandMirror, ('-axis', 'X', '-origin', 'origin'), r'G0 X-1(\.0*)? Y1'),
    (TclCommandSkew, ('-x', '10', '-y', '0', '-origin', 'origin'), r'G0 X1\.17\d* Y1'),
])
def test_tcl_transform_of_a_gcode_job_moves_its_exported_text(cmd_cls, argv, moved, tmp_path):
    """A Tcl transform of an opened G-code job: source_file (the exported text) holds the moved coordinates."""
    import re
    from tests.test_cncjob_export_gcode import MILL_FILE, _open_gcode

    app, job = _open_gcode(tmp_path, MILL_FILE)
    assert 'G0 X1 Y1' in job.source_file
    app.collection.get_by_name.side_effect = lambda n: {'job': job}.get(n)
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(('job',) + argv)
    cmd.execute(args, unnamed)

    assert re.search(moved, job.source_file), job.source_file
    assert 'G0 X1 Y1' not in job.source_file

def run_export(command_class, app, source, *argv):
    from appHandlers.appIO import appIO
    app.f_handlers = appIO(app)
    app.collection.get_by_name = Mock(side_effect=lambda n: source if n == source.obj_options['name'] else None)
    cmd = command_class(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed)

def test_tcl_export_gerber_and_excellon_write_files_that_parse_back(app, tmp_path):
    from appObjects.ExcellonObject import ExcellonObject
    from tclCommands.TclCommandExportExcellon import TclCommandExportExcellon
    from tclCommands.TclCommandExportGerber import TclCommandExportGerber

    source = char.make_gerber(app, name='g')
    target = tmp_path / 'out.gbr'
    run_export(TclCommandExportGerber, app, source, 'g', str(target))
    assert target.exists() and target.stat().st_size > 0
    again = GerberObject('again', app)
    assert again.parse_file(str(target)) is None
    assert again.solid_geometry
    for got, want in zip(again.bounds(), source.bounds()):
        assert got == pytest.approx(want, abs=1e-3)

    source = char.make_excellon(app, name='e')
    target = tmp_path / 'out.drl'
    run_export(TclCommandExportExcellon, app, source, 'e', str(target))
    assert target.exists() and target.stat().st_size > 0
    again = ExcellonObject('again', app)
    assert again.parse_file(str(target)) is None
    assert again.create_geometry() is None
    assert sorted(t['tooldia'] for t in again.tools.values()) == sorted(t['tooldia'] for t in source.tools.values())


# ==============================================================================================================
# test_tcl_join_geometry: join_geometry (Tcl) must join like Edit -> Join.
# ==============================================================================================================

def capture_new_objects(app):
    """app_obj.new_object() runs the init callback on a real Geometry object; returns {name: object}."""
    created = {}

    def new_object(obj_kind, obj_name, initialize, plot=True, **kw):
        assert obj_kind == 'geometry'
        obj = GeometryObject(obj_name, app)
        if initialize(obj, app) == 'fail':
            return 'fail'
        created[obj_name] = obj
        return 'ok'

    app.app_obj.new_object = Mock(side_effect=new_object)
    return created

def isolation(app, name):
    created = capture_new_objects(app)
    make_gerber(app, name='g_' + name).isolate(dia=0.2, overlap=10, iso_type=2, plot=False, passes=2, combine=True,
                                              outname=name)
    return created[name]

def single_geo(app, name, geometry):
    """A SingleGeo object with the tool that GeometryObject.set_ui() gives it: the tool holds the solid geometry."""
    obj = GeometryObject(name, app)
    obj.solid_geometry = geometry
    data = {key: value for key, value in app.options.items() if key.startswith('tools_')}
    obj.tools = {1: {'tooldia': 1.0, 'data': data, 'solid_geometry': obj.solid_geometry}}
    return obj

def run_join(app, objs, *argv):
    app.collection.get_by_name = Mock(side_effect=lambda n: objs.get(n))
    created = capture_new_objects(app)
    cmd = TclCommandJoinGeometry(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed), created

def tool_geometry(obj):
    return {uid: sorted(g.wkb_hex for g in parts(tool['solid_geometry'])) for uid, tool in obj.tools.items()}

def test_multigeo_objects_are_joined_as_in_the_gui(app):
    a, b = isolation(app, 'a'), isolation(app, 'b')
    assert a.multigeo and b.multigeo
    a.tools[1]['data'].update({'name': 'a', 'xmin': 0.0})
    b.tools[1]['data'].update({'name': 'b', 'xmin': 100.0})

    ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
    assert ret != 'fail'
    joined = created['joined']

    gui = GeometryObject('gui', app)
    GeometryObject.merge(geo_list=[a, b], geo_final=gui, multi_geo=True,
                         fuse_tools=app.options['geometry_merge_fuse_tools'], log=app.log)

    assert joined.multigeo is True
    assert tool_geometry(joined) == tool_geometry(gui)
    # with the default preference the tools of the same diameter and type are fused into 1 tool
    assert app.options['geometry_merge_fuse_tools'] is True
    assert list(joined.tools) == [1]
    assert len(parts(joined.tools[1]['solid_geometry'])) == len(parts(a.solid_geometry)) + len(parts(b.solid_geometry))

def test_joined_object_does_not_share_containers_with_the_sources(app):
    a, b = isolation(app, 'a'), isolation(app, 'b')
    before = (len(a.solid_geometry), tool_geometry(a), tool_geometry(b))

    ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
    assert ret != 'fail'
    joined = created['joined']
    joined.solid_geometry.clear()
    for tool in joined.tools.values():
        parts_list = tool['solid_geometry']
        if isinstance(parts_list, list):
            parts_list.clear()
        tool['data']['name'] = 'changed'

    assert (len(a.solid_geometry), tool_geometry(a), tool_geometry(b)) == before
    assert a.tools[1]['data'].get('name') != 'changed'

def test_list_solid_geometry_is_flattened_without_errors(app):
    a, b = isolation(app, 'a'), isolation(app, 'b')
    assert isinstance(a.solid_geometry, list)
    app.log = Mock()

    ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
    assert ret != 'fail'
    joined = created['joined']

    assert not any(isinstance(g, list) for g in joined.solid_geometry)
    assert len(joined.solid_geometry) == len(parts(a.solid_geometry)) + len(parts(b.solid_geometry))
    app.log.error.assert_not_called()

def test_singlegeo_objects_stay_singlegeo(app):
    a = single_geo(app, 'a', box(0, 0, 1, 1))
    b = single_geo(app, 'b', MultiPolygon([box(2, 0, 3, 1), box(4, 0, 5, 1)]))
    app.log = Mock()

    ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
    assert ret != 'fail'
    joined = created['joined']

    expected = [(0.0, 0.0, 1.0, 1.0), (2.0, 0.0, 3.0, 1.0), (4.0, 0.0, 5.0, 1.0)]
    assert joined.multigeo is False
    assert sorted(g.bounds for g in joined.solid_geometry) == expected
    # the 2 tools of the same diameter and type are fused; their geometry is a Polygon and a MultiPolygon
    assert list(joined.tools) == [1]
    assert sorted(g.bounds for g in joined.tools[1]['solid_geometry']) == expected
    app.log.error.assert_not_called()

def test_multigeo_tool_geometry_of_a_cutout_is_joined(app):
    a = isolation(app, 'a')
    # as the cutout makes it: MultiGeo, the tool geometry is the MultiLineString of the solid geometry
    cut = GeometryObject('cut', app)
    cut.multigeo = True
    cut.solid_geometry = MultiLineString([[(0, -2), (5, -2)], [(5, -3), (0, -3)]])
    cut.tools = {1: {'tooldia': '1.0', 'data': deepcopy(a.tools[1]['data']), 'solid_geometry': cut.solid_geometry}}

    ret, created = run_join(app, {'a': a, 'cut': cut}, '-outname', 'joined')
    assert ret != 'fail'
    joined = created['joined']

    assert joined.multigeo is True
    lines = {float(tool['tooldia']): len(parts(tool['solid_geometry'])) for tool in joined.tools.values()}
    assert lines == {float(a.tools[1]['tooldia']): len(parts(a.solid_geometry)), 1.0: 2}

def test_multigeo_and_singlegeo_objects_are_not_joined(app):
    a = isolation(app, 'a')
    single = GeometryObject('single', app)
    single.solid_geometry = list(parts(a.solid_geometry))
    assert a.multigeo and not single.multigeo

    ret, created = run_join(app, {'a': a, 'single': single}, '-outname', 'joined')
    assert ret == 'fail'
    assert created == {}
    app.app_obj.new_object.assert_not_called()

@pytest.mark.parametrize('use_tcl,parameter,values', [
    (True, 'tools_mill_cutz', (-0.05, -1.6)),
    (False, 'tools_mill_ppname_g', ('default', 'GRBL_11')),
], ids=['tcl-depth', 'gui-preprocessor'])
def test_fusion_preserves_different_machining_parameters(app, use_tcl, parameter, values):
    a = single_geo(app, 'a', [box(0, 0, 1, 1)])
    b = single_geo(app, 'b', [box(2, 0, 3, 1)])
    a.multigeo = b.multigeo = True
    a.tools[1]['data'][parameter], b.tools[1]['data'][parameter] = values

    if use_tcl:
        ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
        assert ret != 'fail'
        joined = created['joined']
    else:
        joined = GeometryObject('joined', app)
        GeometryObject.merge([a, b], joined, multi_geo=True, fuse_tools=True, log=app.log)

    assert joined.multigeo is True
    assert len(joined.tools) == 2
    assert [tool['data'][parameter] for tool in joined.tools.values()] == list(values)
    assert tool_geometry(joined) == {1: tool_geometry(a)[1], 2: tool_geometry(b)[1]}

@pytest.mark.parametrize('use_tcl,different_depths', [(True, True), (False, False)],
                         ids=['tcl-different', 'gui-equal'])
def test_fusion_preserves_singlegeo_tools_with_empty_geometry(app, use_tcl, different_depths):
    a = single_geo(app, 'a', [box(0, 0, 1, 1)])
    b = single_geo(app, 'b', [box(2, 0, 3, 1)])
    # MultiGeo -> SingleGeo conversion keeps the geometry on the object and clears each tool's geometry.
    a.tools[1]['solid_geometry'] = []
    b.tools[1]['solid_geometry'] = []
    a.tools[1]['data']['tools_mill_cutz'] = -0.05
    b.tools[1]['data']['tools_mill_cutz'] = -1.6 if different_depths else -0.05

    if use_tcl:
        ret, created = run_join(app, {'a': a, 'b': b}, '-outname', 'joined')
        assert ret != 'fail'
        joined = created['joined']
    else:
        joined = GeometryObject('joined', app)
        GeometryObject.merge([a, b], joined, multi_geo=False, fuse_tools=True, log=app.log)

    assert joined.multigeo is False
    assert sorted(g.bounds for g in joined.solid_geometry) == [(0.0, 0.0, 1.0, 1.0), (2.0, 0.0, 3.0, 1.0)]
    assert [tool['data']['tools_mill_cutz'] for tool in joined.tools.values()] == \
        ([-0.05, -1.6] if different_depths else [-0.05])
    assert all(tool['solid_geometry'] == [] for tool in joined.tools.values())

def run_join_gerber(app, objs_by_name, *argv):
    """Runs the command. Returns (result, created) where created = {name: GerberObject built by initialize}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kwargs):
        assert kind == 'gerber'
        obj = GerberObject(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    app.collection.get_by_name = Mock(side_effect=lambda n: objs_by_name.get(n))
    app.app_obj.new_object = Mock(side_effect=new_object)
    cmd = TclCommandJoinGerber(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed), created

def test_join_two_gerbers_with_outname(app):
    a = make_gerber(app, name='a')
    b = make_gerber(app, name='b')
    b.offset((100.0, 0.0))
    bounds_a, bounds_b = a.bounds(), b.bounds()
    res, created = run_join_gerber(app, {'a': a, 'b': b}, 'a', 'b', '-outname', 'both')
    assert res is None
    assert list(created) == ['both']
    final = created['both']
    bounds_f = final.bounds()
    assert bounds_f[0] == pytest.approx(bounds_a[0]) and bounds_f[2] == pytest.approx(bounds_b[2])
    assert bounds_f[2] - bounds_f[0] > bounds_b[2] - bounds_b[0] + 99.0
    app.app_obj.new_object.assert_called_once()
    # the sources are untouched
    assert a.bounds() == bounds_a and b.bounds() == bounds_b

@pytest.mark.parametrize('argv', [('a',)])
def test_fails_with_fewer_than_two_objects(app, argv):
    a = make_gerber(app, name='a')
    res, created = run_join_gerber(app, {'a': a}, *argv)
    assert res == 'fail' and not created
    app.app_obj.new_object.assert_not_called()
