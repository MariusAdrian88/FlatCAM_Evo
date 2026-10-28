"""Stage 4: FlatCAMObj.regenerate_source() replaces the repeated
``obj.source_file = f_handlers.export_xxx(..., local_use=obj, use_thread=False)`` call. Headless."""

from unittest.mock import patch

import numpy as np
import pytest
import ezdxf
from PyQt6 import QtWidgets
from shapely.geometry import LineString, Polygon

from appHandlers.appIO import appIO
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject
from tests.helpers_objects import (  # noqa: F401,E402
    assert_poly_equiv, make_app, make_excellon, make_gerber, pts, reparse_excellon, reparse_gerber,
)


@pytest.fixture(scope='module', autouse=True)
def _headless_qt():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield qapp


@pytest.fixture
def app():
    app = make_app()
    app.f_handlers = appIO(app)
    return app


def test_gerber_regenerated_text_parses_back_to_the_same_geometry(app, tmp_path):
    grb = make_gerber(app)
    grb.offset((2.0, 1.0))
    grb.source_file = 'stale'
    assert grb.regenerate_source() is True
    assert grb.source_file != 'stale' and grb.source_file.startswith('G04*')
    again = reparse_gerber(app, grb.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='regenerated gerber')


def test_excellon_regenerated_text_parses_back_to_the_same_geometry(app, tmp_path):
    exc = make_excellon(app)
    exc.offset((3.5, 2.25))
    exc.source_file = 'stale'
    assert exc.regenerate_source() is True
    assert exc.source_file.startswith('M48')
    again = reparse_excellon(app, exc.source_file, tmp_path)
    for t, tool in exc.tools.items():
        assert pts(again.tools[t]['drills']) == pytest.approx(np.array(pts(tool['drills'])), abs=1e-3)


def test_local_use_export_runs_without_a_thread_and_writes_no_file(app):
    grb = make_gerber(app)
    app.f_handlers.export_gerber = lambda **kw: calls.append(kw) or 'G04*'
    calls = []
    assert grb.regenerate_source(name='other') is True
    assert calls == [dict(obj_name='other', filename=None, local_use=grb, use_thread=False)]
    assert grb.source_file == 'G04*'
    app.file_saved.emit.assert_not_called()


def test_name_defaults_to_the_object_name(app):
    exc = make_excellon(app, name='my_exc')
    seen = []
    app.f_handlers.export_excellon = lambda **kw: seen.append(kw['obj_name']) or 'M48'
    exc.regenerate_source()
    assert seen == ['my_exc']


@pytest.mark.parametrize('bad', ['fail', None])
def test_failed_export_keeps_the_old_source(app, bad):
    grb = make_gerber(app)
    grb.source_file = 'old text'
    app.f_handlers.export_gerber = lambda **kw: bad
    assert grb.regenerate_source() is False
    assert grb.source_file == 'old text'


@pytest.mark.parametrize('units,unit_code', [('MM', 4), ('IN', 1)])
@pytest.mark.parametrize('multigeo', [False, True])
def test_geometry_real_dxf_snapshot_and_project_boundary(app, tmp_path, units, unit_code, multigeo):
    """GEO-09: the usable DXF snapshot is text in the existing project source field."""
    app.app_units = units
    app.options['geometry_dxf_format'] = 'R2010'
    geo = GeometryObject('geo', app)
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]])
    line = LineString([(5, 0), (6, 1)])
    geo.multigeo = multigeo
    geo.solid_geometry = [polygon, line]
    if multigeo:
        geo.solid_geometry = []
        geo.tools = {1: {'solid_geometry': [polygon]}, 2: {'solid_geometry': [line]}}

    assert geo.regenerate_source() is True
    filename = tmp_path / 'snapshot.dxf'
    filename.write_text(geo.source_file, encoding='utf-8')
    again = ezdxf.readfile(filename)
    assert again.units == unit_code
    assert again.header['$MEASUREMENT'] == (1 if units == 'MM' else 0)
    paths = list(again.modelspace())
    assert [entity.dxftype() for entity in paths] == ['LWPOLYLINE'] * 3
    assert [entity.closed for entity in paths] == [True, True, False]
    assert [list(entity.get_points('xy')) for entity in paths] == [
        list(polygon.exterior.coords), list(polygon.interiors[0].coords), list(line.coords)]
    app.file_saved.emit.assert_not_called()
    assert geo.to_dict()['source_file'] == geo.source_file
