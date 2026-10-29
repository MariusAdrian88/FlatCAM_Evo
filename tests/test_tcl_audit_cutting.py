"""Selection, factory and multipart output contracts for clearing and cutout commands."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely import box
from shapely.ops import unary_union

from tclCommands.TclCommandCopperClear import TclCommandCopperClear
from tclCommands.TclCommandCutout import TclCommandCutout
from tclCommands.TclCommandPanelize import TclCommandPanelize
from test_tcl_audit_generation import factory_app
from test_tcl_audit_regressions import FCShell, qt_app


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


@pytest.mark.parametrize('args,kind', [
    ({'all': None, 'area': '0,0,1,1'}, 'gerber'),
    ({'all': None, 'iso_tooldia': '0.2'}, 'geometry'),
])
def test_ncc_rejects_inconsistent_new_options(args, kind):
    other = {'lines': SimpleNamespace(kind='geometry', solid_geometry=[box(0, 0, 1, 1).exterior])}
    with pytest.raises(FCShell.TclErrorException):
        _ncc_run(args, source_kind=kind, other=other)


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

