"""Headless command-generation regressions, including factory failure contracts."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely import box

from defaults import AppDefaults
from tclCommands.TclCommandAlignDrill import TclCommandAlignDrill
from tclCommands.TclCommandAlignDrillGrid import TclCommandAlignDrillGrid
from tclCommands.TclCommandBbox import TclCommandBbox
from tclCommands.TclCommandNregions import TclCommandNregions
from tclCommands.TclCommandCncjob import TclCommandCncjob
from tclCommands.TclCommandOpenDXF import TclCommandOpenDXF
from tclCommands.TclCommandSplitGeometry import TclCommandSplitGeometry
from test_tcl_audit_regressions import object_app


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


@pytest.mark.parametrize('argv', [('-offset_type', 'sideways'), ('-search_time', '0')])
def test_cncjob_rejects_wrong_milling_options(argv):
    obj = Mock(kind='geometry', multigeo=True, tools={})
    app = factory_app(obj)
    app.milling_tool = Mock()
    cmd = TclCommandCncjob(app)
    args, unnamed = cmd.check_args(('g',) + argv)
    with pytest.raises(Exception):
        cmd.execute(args, unnamed)
    app.milling_tool.generate_cnc_job_handler.assert_not_called()


@pytest.mark.parametrize('cls,method,extra', [(TclCommandOpenDXF, 'import_dxf_as_geo', {})])
def test_import_failure_is_returned_to_factory_and_never_announced(cls, method, extra):
    app = factory_app(None)
    from camlib import Geometry
    geo = Mock(spec=Geometry)
    getattr(geo, method).return_value = 'fail'
    results = []

    def create(kind, name, init, **kwargs):
        results.append(init(geo, app))
        return 'fail'

    app.app_obj.new_object.side_effect = create
    command = cls(app)
    result = command.execute({'filename': 'drawing.svg', 'outname': 'custom', **extra}, [])
    assert result == 'fail'
    assert results and all(r == 'fail' for r in results)
    app.file_opened.emit.assert_not_called()


def test_split_geometry_populates_root_and_only_the_selected_tool():
    obj = SimpleNamespace(kind='geometry', tools={7: {'solid_geometry': [box(0, 0, 1, 1)]}})
    app = factory_app(obj)
    output = SimpleNamespace(tools={1: {'solid_geometry': []}})
    app.app_obj.new_object.side_effect = lambda kind, name, init, **kw: init(output, app)
    TclCommandSplitGeometry(app).execute({'source_name': 'g'}, [])
    assert list(output.tools) == [7]
    assert output.solid_geometry[0].equals(obj.tools[7]['solid_geometry'][0])

