"""Headless Tcl command regressions: command-generation and factory failure contracts, cutting and clearing commands,
geometry / CNC consistency, multi-object transforms.

factory_app() is also used by other test files.
"""
import importlib
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely import LineString, box
from shapely.ops import unary_union

from camlib import Geometry
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


# command API contracts: explicit failure stays failure, None success stays success

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


# generation commands

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


# selection, factory and multipart output contracts for clearing and cutout commands

def _ncc_run(args, source_kind='gerber', other=None):
    objects = {'g': SimpleNamespace(kind=source_kind, decimals=4)}
    objects.update(other or {})
    app = factory_app(objects['g'])
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


# geometry / CNC consistency for Tcl editing and tool selection

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


# multi-object commands validate their full input before changing any object

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
