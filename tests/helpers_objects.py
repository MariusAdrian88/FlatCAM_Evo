"""Shared helpers and fixtures for the object tests (Gerber / Excellon / project / copy / merge).

Not collected by pytest (the name does not start with ``test_``). Test modules import what they need:
``from tests.helpers_objects import app, _headless_qt, make_gerber, ...``. The fixtures (``app``, ``gerber``,
``excellon``, ``_headless_qt``, ``no_shape_collection``) must be imported by name into the test module.

Two families of factories exist:
- ``make_app`` / ``make_gerber(app, path)`` / ``make_excellon(app, path)``: real GerberObject / ExcellonObject
  instances parsed from the files in ``tests/fixtures/unify`` with an app namespace that has export options.
- ``make_app_0b`` / ``make_gerber_0b(lines)`` / ``make_excellon_0b(...)``: plain camlib objects (Gerber / Excellon
  parsers) built from literal data, with a smaller app namespace.

Everything runs headless.
"""

import copy
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, \
    Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets  # noqa: E402

from appHandlers.appIO import appIO  # noqa: E402
from appHandlers.appObjectOps import AppObjectOps  # noqa: E402
from appObjects.ExcellonObject import ExcellonObject  # noqa: E402
from appObjects.GerberObject import GerberObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from appParsers.ParseGerber import Gerber  # noqa: E402
from camlib import dict2obj  # noqa: E402
from appCommon.ProjectFile import load_project_file  # noqa: E402
from defaults import AppDefaults  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures' / 'unify'
MIXED_GBR = str(FIXTURES / 'unify_mixed.gbr')
NATIVE_GBR = str(FIXTURES / 'unify_native.gbr')
MIXED_DRL = str(FIXTURES / 'unify_mixed.drl')

TRANSFORM_NAMES = ['offset', 'mirror_x', 'mirror_y', 'rotate', 'skew', 'scale']


# ---------------------------------------------------------------------------------------------------------------
# headless object factory
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module', autouse=True)
def _headless_qt():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield qapp


def make_app():
    defaults = AppDefaults()
    options = dict(defaults.defaults)
    options.update({
        'gerber_exp_units': 'MM', 'gerber_exp_integer': 4, 'gerber_exp_decimals': 6, 'gerber_exp_zeros': 'L',
        'excellon_exp_units': 'METRIC', 'excellon_exp_integer': 3, 'excellon_exp_decimals': 3,
        'excellon_exp_format': 'dec', 'excellon_exp_slot_type': 'routing', 'excellon_exp_zeros': 'LZ',
        'global_save_compressed': False,
    })
    app = SimpleNamespace(
        log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=options, app_units='MM',
        abort_flag=False, app_obj=Mock(), plot_all=Mock(), defaults=defaults, decimals=4,
        version='test', version_date='test', proc_container=MagicMock(), plotcanvas=Mock(), pool=None,
        collection=Mock(), new_project_signal=Mock(), file_opened=Mock(), file_saved=Mock(), call_source='app', ui=MagicMock(),
        should_we_save=False, save_in_progress=False, start_delayed_quit=Mock(), history=Mock(),
    )
    return app


def make_gerber(app, path=MIXED_GBR, name='grb'):
    obj = GerberObject(name, app)
    assert obj.parse_file(path) is None
    return obj


def make_excellon(app, path=MIXED_DRL, name='exc'):
    obj = ExcellonObject(name, app)
    assert obj.parse_file(path) is None
    assert obj.create_geometry() is None
    return obj


def make_same_diameter_excellon(app, name='same_dia'):
    """2 tools with the same diameter and different data (cannot live in a file)."""
    obj = ExcellonObject(name, app)
    obj.units = 'MM'
    obj.tools = {
        1: {'tooldia': 1.0, 'drills': [Point(5, 5), Point(9, 9)], 'slots': [], 'solid_geometry': [],
            'data': {}},
        2: {'tooldia': 1.0, 'drills': [Point(15, 5)], 'slots': [(Point(20, 5), Point(26, 5))],
            'solid_geometry': [], 'data': {}},
    }
    assert obj.create_geometry() is None
    # create_geometry() overwrites 'data', so set the distinct data after it
    obj.tools[1]['data'] = {'cutz': -1.7, 'multidepth': True, 'depthperpass': 0.4, 'tag': 'first'}
    obj.tools[2]['data'] = {'cutz': -2.4, 'multidepth': False, 'depthperpass': 0.8, 'tag': 'second'}
    return obj


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
def gerber(app):
    return make_gerber(app)


@pytest.fixture
def excellon(app):
    return make_excellon(app)


# ---------------------------------------------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------------------------------------------

def parts(geo):
    """Flatten lists / Multi* / GeometryCollection into a list of simple shapely geometries (order kept)."""
    out = []
    if geo is None:
        return out
    if isinstance(geo, (list, tuple)):
        for g in geo:
            out.extend(parts(g))
    elif isinstance(geo, (MultiPolygon, MultiLineString, MultiPoint, GeometryCollection)):
        for g in geo.geoms:
            out.extend(parts(g))
    elif isinstance(geo, BaseGeometry):
        out.append(geo)
    return out


def polygon_union(geo):
    polys = [shapely.make_valid(p) for p in parts(geo) if isinstance(p, Polygon) and not p.is_empty]
    return unary_union(polys) if polys else Polygon()


def sym_diff_area(a, b):
    return polygon_union(a).symmetric_difference(polygon_union(b)).area


def assert_poly_equiv(actual, expected, rel=1e-6, abs_tol=1e-6, msg=''):
    exp = polygon_union(expected)
    diff = polygon_union(actual).symmetric_difference(exp).area
    assert diff <= max(abs_tol, rel * exp.area), '%s symmetric difference %.9f (area %.6f)' % (msg, diff, exp.area)


def coords_of(geo):
    """List of coordinate arrays, one per simple geometry (order kept)."""
    return [shapely.get_coordinates(g) for g in parts(geo)]


def assert_coords_close(actual, expected, atol=1e-9, msg=''):
    a = coords_of(actual)
    e = coords_of(expected)
    assert len(a) == len(e), '%s part count %d != %d' % (msg, len(a), len(e))
    for i, (ca, ce) in enumerate(zip(a, e)):
        assert ca.shape == ce.shape, '%s part %d shape %s != %s' % (msg, i, ca.shape, ce.shape)
        assert np.allclose(ca, ce, atol=atol), '%s part %d coords differ' % (msg, i)


def pts(items):
    """Coordinate tuples of a list of Points."""
    return [(p.x, p.y) for p in items]


def slot_coords(slots):
    return [((s[0].x, s[0].y), (s[1].x, s[1].y)) for s in slots]


def slot_flat(slots):
    """Slots as flat (x1, y1, x2, y2) tuples, usable with pytest.approx."""
    return [(s[0].x, s[0].y, s[1].x, s[1].y) for s in slots]


# transform table: name -> (apply to object, shapely function applied to one geometry)
ORIGIN = (6.0, 4.0)
AFFINE_TRANSFORMS = {
    'offset': (lambda o: o.offset((3.5, -2.25)), lambda g: affinity.translate(g, 3.5, -2.25)),
    'mirror_x': (lambda o: o.mirror('X', (12.0, 7.0)), lambda g: affinity.scale(g, 1.0, -1.0, origin=(12.0, 7.0))),
    'mirror_y': (lambda o: o.mirror('Y', (12.0, 7.0)), lambda g: affinity.scale(g, -1.0, 1.0, origin=(12.0, 7.0))),
    'rotate': (lambda o: o.rotate(30.0, (10.0, 8.0)), lambda g: affinity.rotate(g, 30.0, origin=(10.0, 8.0))),
    'skew': (lambda o: o.skew(10.0, 5.0, (4.0, 3.0)), lambda g: affinity.skew(g, 10.0, 5.0, origin=(4.0, 3.0))),
    'scale': (lambda o: o.scale(1.5, 1.5, ORIGIN), lambda g: affinity.scale(g, 1.5, 1.5, origin=ORIGIN)),
}


def apply_to(fn, geo):
    """Apply a shapely function to every simple part, returning a flat list."""
    return [fn(g) for g in parts(geo)]


def snapshot_gerber(obj):
    return {
        'solid': copy.deepcopy(obj.solid_geometry),
        'follow': copy.deepcopy(obj.follow_geometry),
        'tools': copy.deepcopy(obj.tools),
    }


def snapshot_excellon(obj):
    return copy.deepcopy(obj.tools)


def save_and_load(app, objects, tmp_path):
    """appIO.save_project() into a JSON file, then the parse step of open_project() and
    appIO.restore_project_objects(). Not covered: restore_project_handler (message boxes), the LZMA branch,
    and AppObject.new_object (replaced by a factory that calls initialize())."""
    io = appIO(app)
    app.collection.get_list.return_value = objects
    app.collection.get_active.return_value = None
    path = str(tmp_path / 'project.FlatPrj')
    io.save_project(path, silent=True)
    assert os.path.getsize(path) > 0

    proj = load_project_file(path, object_hook=dict2obj)

    restored = []

    def new_object(kind, name, initialize, *args, **kwargs):
        new_obj = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind](name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        restored.append(new_obj)
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    io.restore_project_objects(proj, path, True, False)
    return proj, restored


def assert_same_tools(old, new, kind):
    assert sorted(old) == sorted(new)
    for key, tool in old.items():
        other = new[key]
        assert set(tool) == set(other), (key, set(tool) ^ set(other))
        for field, value in tool.items():
            if field == 'macro':
                assert other[field].name == value.name and other[field].raw == value.raw
            elif field == 'geometry':
                assert len(value) == len(other[field]), key
                for rec, rec2 in zip(value, other[field]):
                    assert set(rec) == set(rec2)
                    for part in rec:
                        assert_coords_close(rec2[part], rec[part], atol=2e-6, msg='%s %s' % (key, part))
            elif field == 'solid_geometry':
                assert_coords_close(other[field], value, atol=2e-6, msg='%s solid' % key)
            elif field == 'drills':
                assert pts(other[field]) == pytest.approx(np.array(pts(value)), abs=2e-6)
            elif field == 'slots':
                assert slot_flat(other[field]) == pytest.approx(np.array(slot_flat(value)), abs=2e-6)
            elif isinstance(value, float):
                assert other[field] == pytest.approx(value), (key, field)
            else:
                assert other[field] == value, (key, field)


def export_gerber_text(app, obj):
    out = appIO(app).export_gerber(obj.obj_options['name'], None, local_use=obj, use_thread=False)
    assert isinstance(out, str) and out != 'fail'
    return out


def export_excellon_text(app, obj):
    out = appIO(app).export_excellon(obj.obj_options['name'], None, local_use=obj, use_thread=False)
    assert isinstance(out, str) and out != 'fail'
    return out


def reparse_gerber(app, text, tmp_path, name='again'):
    path = tmp_path / (name + '.gbr')
    path.write_text(text)
    obj = GerberObject(name, app)
    assert obj.parse_file(str(path)) is None
    return obj


def reparse_excellon(app, text, tmp_path, name='again'):
    path = tmp_path / (name + '.drl')
    path.write_text(text)
    obj = ExcellonObject(name, app)
    assert obj.parse_file(str(path)) is None
    assert obj.create_geometry() is None
    return obj


# ---------------------------------------------------------------------------------------------------------------
# plain camlib objects from literal data (headless)
# ---------------------------------------------------------------------------------------------------------------

def make_app_0b(**option_overrides):
    """A minimal headless app: enough for the camlib classes, the FlatCAMObj classes and the Tcl commands."""
    defaults = AppDefaults()
    options = dict(defaults.defaults)
    options.update(option_overrides)

    plotcanvas = Mock()
    # count the temp_shapes that Geometry.__init__ creates
    plotcanvas.new_shape_collection.side_effect = lambda **kwargs: Mock()
    return SimpleNamespace(
        log=Mock(), inform=Mock(), options=options, defaults=defaults, app_units='MM', decimals=4,
        plotcanvas=plotcanvas, pool=None, abort_flag=False,
        proc_container=MagicMock(), shell=Mock(), collection=Mock(), call_source='app',
        preprocessors=defaultdict(Mock), splash=None, worker_task=Mock(), app_obj=Mock(),
    )


@pytest.fixture
def no_shape_collection():
    """FlatCAMObj.__init__ builds a VisPy ShapeCollection; replace it."""
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        yield


def make_excellon_0b(app=None, tooldia=1.0, drills=((0.0, 0.0), (4.0, 0.0)), slots=(((0.0, 2.0), (4.0, 2.0)),),
                  default_data=None, units='MM'):
    """A plain camlib Excellon with 1 tool. ``default_data`` mimics ExcellonObject (which always has it)."""
    app = app or make_app_0b()
    ex = Excellon(app)
    ex.units = units
    if default_data is not None:
        ex.default_data = default_data
    ex.tools = {1: {
        'tooldia': tooldia,
        'drills': [Point(p) for p in drills],
        'slots': [(Point(a), Point(b)) for a, b in slots],
        'solid_geometry': [],
        'data': {},
    }}
    ex.create_geometry()
    ex.obj_options = {'name': 'ex'}
    ex.kind = 'excellon'
    return ex


def drill_xy(ex, tool=1):
    return [(p.x, p.y) for p in ex.tools[tool]['drills']]


def slot_xy(ex, tool=1):
    return [((a.x, a.y), (b.x, b.y)) for a, b in ex.tools[tool]['slots']]


def assert_xy_close(actual, expected, tol=1e-9):
    assert len(actual) == len(expected), "%r != %r" % (actual, expected)
    for (ax, ay), (ex_, ey) in zip(actual, expected):
        assert math.isclose(ax, ex_, abs_tol=tol) and math.isclose(ay, ey, abs_tol=tol), \
            "%r != %r" % (actual, expected)


GERBER_C_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,1.0*%", "D10*", "X10000Y10000D03*", "M02*"]
GERBER_P_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD11P,1.0X6*%", "D11*", "X10000Y10000D03*", "M02*"]


def make_gerber_0b(lines, app=None):
    g = Gerber(app or make_app_0b())
    g.parse_lines(lines)
    return g


# copy: run AppObjectOps.on_copy_command() headless

def run_copy(app, source, kind):
    """Run AppObjectOps.on_copy_command() headless; returns the new object."""
    created = {}

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        assert obj_kind == kind
        cls = GerberObject if kind == 'gerber' else ExcellonObject
        new_obj = cls(name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        created[name] = new_obj
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.collection.get_selected.return_value = [source]
    AppObjectOps(app).on_copy_command()
    assert list(created) == [source.obj_options['name'] + '_copy']
    return created[source.obj_options['name'] + '_copy']

# The mixed fixture without the polarity sequence: C, R, O, P, macro flashes, tracks and the region.
MIXED_GBR_NO_POLARITY = '''G04 P and macro flashes without the polarity sequence*
%FSLAX46Y46*%
%MOMM*%
%AMOUTL*
4,1,4,0,0,2,0,2,1,0,1,0,0,0*%
%AMRING*
1,1,$1,0,0*
1,0,$2,0,0*%
%ADD10C,1.0*%
%ADD11R,2.0X1.0*%
%ADD12O,2.0X1.0*%
%ADD13P,2.0X5X30*%
%ADD14OUTL*%
%ADD15RING,2.0X1.0*%
G01*
%LPD*%
D10*
X5000000Y5000000D03*
D11*
X10000000Y5000000D03*
D12*
X15000000Y5000000D03*
D13*
X20000000Y5000000D03*
D14*
X25000000Y5000000D03*
D15*
X30000000Y5000000D03*
D10*
X0Y10000000D02*
X30000000Y10000000D01*
X30000000Y15000000D01*
G36*
X0Y20000000D02*
X10000000Y20000000D01*
X10000000Y28000000D01*
X0Y28000000D01*
X0Y20000000D01*
G37*
M02*
'''
