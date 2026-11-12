"""Run directly so no conftest imports heavy modules before the import blocker."""

import json
from collections import defaultdict
from unittest.mock import Mock
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def probe(blocked):
    import importlib.abc
    import math

    class BlockMatplotlib(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in ('matplotlib', 'descartes'):
                raise ModuleNotFoundError(f'Blocked optional dependency: {fullname}')

    if blocked:
        sys.meta_path.insert(0, BlockMatplotlib())
    sys.path.insert(0, str(ROOT))
    import ezdxf
    import fontTools
    from ezdxf.fonts import fonts
    from appParsers.DXF import text as dxf_text

    assert dxf_text.TEXT_RENDERING_AVAILABLE
    assert dxf_text.TEXT2PATH_IMPORTED
    assert dxf_text.TEXT2PATH_VERSION_OK, ezdxf.__version__
    doc = ezdxf.new()
    doc.styles.get('Standard').dxf.font = 'Arial.ttf'
    model = doc.modelspace()
    entities = [model.add_text('ABC', dxfattribs={'height': 2.5}),
                model.new_entity('ATTRIB', dxfattribs={
                    'tag': 'LABEL', 'text': 'ABC', 'insert': (0, 0), 'height': 2.5})]
    results = {}
    for entity in entities:
        for mode in ('stroke', 'outline'):
            geometry = dxf_text.text_entity_to_shapes(entity, text_mode=mode)
            assert geometry, (entity.dxftype(), mode)
            assert all(g.is_valid and not g.is_empty and g.length > 0 for g in geometry)
            assert all(math.isfinite(v) for g in geometry for v in g.bounds)
            expected_type = 'LineString' if mode == 'stroke' else 'Polygon'
            assert all(g.geom_type == expected_type for g in geometry)
            results[f'{entity.dxftype()}:{mode}'] = {
                'count': len(geometry), 'bounds': [g.bounds for g in geometry],
                'wkb': [g.wkb_hex for g in geometry]}

    unsupported = {}
    for entity in (model.add_mtext('ABC'), model.add_attdef('LABEL', text='ABC')):
        for mode in ('stroke', 'outline'):
            unsupported[f'{entity.dxftype()}:{mode}'] = len(
                dxf_text.text_entity_to_shapes(entity, text_mode=mode))

    # Exercise the defensive branches without changing installed packages.
    import builtins
    import importlib
    from unittest.mock import patch

    with patch.object(ezdxf, '__version__', '1.4.4'):
        importlib.reload(dxf_text)
        assert not dxf_text.TEXT_RENDERING_AVAILABLE
        assert not dxf_text.TEXT2PATH_VERSION_OK
        assert dxf_text.text_entity_to_shapes(entities[0]) == []
    with patch.object(ezdxf, '__version__', 'invalid'):
        importlib.reload(dxf_text)
        assert not dxf_text.TEXT_RENDERING_AVAILABLE
        assert dxf_text.text_entity_to_shapes(entities[0]) == []
    original_import = builtins.__import__

    def without_addon(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'ezdxf.addons' and fromlist and 'text2path' in fromlist:
            raise ImportError('text2path unavailable')
        return original_import(name, globals, locals, fromlist, level)

    with patch('builtins.__import__', side_effect=without_addon):
        importlib.reload(dxf_text)
        assert not dxf_text.TEXT2PATH_IMPORTED
        assert not dxf_text.TEXT_RENDERING_AVAILABLE
        assert dxf_text.text_entity_to_shapes(entities[0]) == []
    importlib.reload(dxf_text)
    assert dxf_text.TEXT_RENDERING_AVAILABLE
    if blocked:
        assert not any(name.split('.')[0] in ('matplotlib', 'descartes') for name in sys.modules)
    return {'ezdxf': ezdxf.__version__, 'fontTools': fontTools.__version__,
            'font': str(fonts.font_manager.get_font_face('Arial.ttf')),
            'geometry': results, 'unsupported_entity_counts': unsupported}


def test_dxf_without_matplotlib():
    results = []
    for mode in ('baseline', 'blocked'):
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--probe', mode],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout.split('DXF_RESULT=', 1)[1])
        results.append(result)
    assert results[0] == results[1]
    evidence = dict(results[1])
    evidence['geometry'] = {key: {'count': value['count'], 'bounds': value['bounds']}
                            for key, value in evidence['geometry'].items()}
    print(json.dumps(evidence))



def _writer():
    sys.path.insert(0, str(ROOT))
    from appParsers.DXF.writer import build_dxf
    return build_dxf


def _xy(polyline):
    pts = polyline.points() if polyline.dxftype() == 'POLYLINE' else polyline.get_points(format='xy')
    return [(round(p[0], 6), round(p[1], 6)) for p in pts]


def _box_with_hole():
    from shapely.geometry import Polygon
    return Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(3, 2), (6, 2), (6, 5), (3, 5)]])


@pytest.mark.parametrize('fmt, kind', [('R2010', 'LWPOLYLINE')])
@pytest.mark.parametrize('units, insunits', [('MM', 4)])
def test_polygon_rings_are_closed_2d_polylines(fmt, kind, units, insunits):
    from shapely.geometry import Polygon
    polygon = _box_with_hole()
    # 3D input must not leak the Z value into the file (read back as a start width)
    poly3d = Polygon([(0, 0, 5), (10, 0, 5), (10, 8, 5), (0, 8, 5)])
    doc = _writer()([{'name': '0', 'geometry': [polygon, poly3d]}], units, fmt)
    assert doc is not None
    if fmt != 'R12':
        assert doc.header['$INSUNITS'] == insunits
    assert doc.header['$MEASUREMENT'] == (1 if units == 'MM' else 0)
    polylines = list(doc.modelspace().query(kind))
    assert len(polylines) == 3
    assert all(p.is_closed for p in polylines)
    assert _xy(polylines[0]) == [(0, 0), (10, 0), (10, 8), (0, 8), (0, 0)]
    assert _xy(polylines[1]) == [(3, 2), (6, 2), (6, 5), (3, 5), (3, 2)]
    if kind == 'LWPOLYLINE':
        assert all(len(pt) == 5 for pt in polylines[2].get_points())
        assert all(pt[2] == 0 and pt[3] == 0 for pt in polylines[2].get_points())


@pytest.mark.parametrize('fmt', ['R2010'])
def test_buffered_drill_is_one_circle(fmt):
    from shapely.geometry import Point
    center, radius = (3.2, -1.5), 0.4
    doc = _writer()([{'name': 'drills', 'geometry': [Point(center).buffer(radius, quad_segs=16)]}],
                    'MM', fmt)
    circles = list(doc.modelspace().query('CIRCLE'))
    assert len(circles) == 1
    assert tuple(circles[0].dxf.center)[:2] == pytest.approx(center, abs=1e-6)
    assert circles[0].dxf.radius == pytest.approx(radius, abs=1e-6)
    assert not list(doc.modelspace().query('LWPOLYLINE POLYLINE'))


@pytest.mark.parametrize('fmt', ['R2010', 'R12'])
def test_repeated_rounded_shapes_keep_arcs_and_exact_vertices(fmt):
    from shapely import affinity
    from shapely.geometry import LineString, Point
    rounded = LineString([(0, 0), (4, 0), (5, 2)]).buffer(0.3, quad_segs=8)
    drill = Point(1, 1).buffer(0.5, quad_segs=8)
    offsets = [(0.0, 0.0), (37.1, 12.7), (-80.3, 1.1)]
    shapes = [affinity.translate(g, dx, dy) for dx, dy in offsets for g in (rounded, drill)]
    doc = _writer()([{'name': 'a', 'geometry': shapes, 'width': 0.1}], 'MM', fmt)
    assert doc is not None
    msp = doc.modelspace()
    polylines = list(msp.query('LWPOLYLINE POLYLINE'))
    circles = list(msp.query('CIRCLE'))
    assert len(polylines) == 3 and len(circles) == 3

    def bulges(p):
        if p.dxftype() == 'POLYLINE':
            return [v.dxf.bulge for v in p.vertices]
        return [pt[4] for pt in p.get_points()]

    def verts(p):
        if p.dxftype() == 'POLYLINE':
            return [tuple(v.dxf.location)[:2] for v in p.vertices]
        return [tuple(pt[:2]) for pt in p.get_points()]

    assert any(bulges(polylines[0]))
    for k, (dx, dy) in enumerate(offsets):
        assert bulges(polylines[k]) == pytest.approx(bulges(polylines[0]), abs=1e-8)
        assert circles[k].dxf.radius == pytest.approx(circles[0].dxf.radius, abs=1e-8)
        assert tuple(circles[k].dxf.center)[:2] == pytest.approx((1 + dx, 1 + dy), abs=1e-6)
        expected = {(round(x, 9), round(y, 9)) for x, y in shapes[2 * k].exterior.coords}
        assert {(round(x, 9), round(y, 9)) for x, y in verts(polylines[k])} <= expected


def test_a_shape_almost_equal_to_an_exported_circle_is_fitted_with_its_own_tolerance():
    # with a tight tolerance the 2nd ring (1 vertex moved by 1e-8) is no circle; the circle before it must not
    # decide its fit
    from shapely import affinity
    from shapely.geometry import Point, Polygon
    circle = Point(0, 0).buffer(5.0, quad_segs=8)
    coords = list(affinity.translate(circle, 20.0, 0.0).exterior.coords)[:-1]
    coords[3] = (coords[3][0] + 1e-8, coords[3][1])
    build = _writer()

    def kinds(shapes):
        doc = build([{'name': 'a', 'geometry': shapes}], 'MM', 'R2010', tolerance=1e-10)
        return [e.dxftype() for e in doc.modelspace()]

    alone = kinds([Polygon(coords)])
    assert alone == ['LWPOLYLINE']
    assert kinds([circle, Polygon(coords)]) == ['CIRCLE'] + alone


if __name__ != '__main__':   # the probe run must not import heavy modules before the import blocker
    from tests.helpers_objects import app, _headless_qt  # noqa: F401


@pytest.fixture
def object_app(app):  # noqa: F811
    app.options.update({'geometry_dxf_format': 'R2010', 'geometry_dxf_layers': 'tool',
                        'geometry_dxf_gerber_traces': 'outline', 'geometry_dxf_travel': True,
                        'geometry_dxf_arcs': True})
    return app


def _named(obj, name):
    obj.obj_options['name'] = name
    return obj


def _layers(doc):
    return {layer.dxf.name for layer in doc.layers if layer.dxf.name not in ('0', 'Defpoints')}


def _on(doc, layer, kind):
    return [e for e in doc.modelspace().query(kind) if e.dxf.layer == layer]


def test_multigeo_geometry_layers_per_tool_and_single_polygon(object_app):
    from shapely.geometry import LineString, box
    from appObjects.GeometryObject import GeometryObject
    obj = _named(GeometryObject('geo', object_app), 'geo')
    obj.multigeo = True
    obj.tools = {1: {'tooldia': 0.4, 'solid_geometry': box(0, 0, 4, 4)},          # a single Polygon (was a TypeError)
                 2: {'tooldia': 1.0, 'solid_geometry': [LineString([(0, 0), (5, 5)])]}}
    doc = obj.export_dxf()
    assert _layers(doc) == {'geo_T1', 'geo_T2'}
    assert doc.layers.get('geo_T1').dxf.lineweight == 40
    assert doc.layers.get('geo_T2').dxf.lineweight == 100
    assert len(_on(doc, 'geo_T1', 'LWPOLYLINE')) == 1
    object_app.options['geometry_dxf_layers'] = 'object'
    assert _layers(obj.export_dxf()) == {'geo'}


def test_gerber_layers_per_aperture_and_centerline(object_app):
    from shapely.geometry import LineString, Point, box
    from appObjects.GerberObject import GerberObject
    obj = _named(GerberObject('grb', object_app), 'grb')
    track = LineString([(0, 0), (10, 0)])
    obj.tools = {
        10: {'type': 'C', 'size': 0.25, 'geometry': [{'solid': track.buffer(0.125), 'follow': track},
                                                     {'solid': Point(5, 5).buffer(0.125), 'follow': Point(5, 5)}]},
        0: {'type': 'REG', 'size': 0.0, 'geometry': [{'solid': box(20, 0, 22, 2), 'follow': LineString([(20, 0), (22, 0)])}]},
    }
    obj.solid_geometry = [box(0, 0, 1, 1)]
    doc = obj.export_dxf()
    assert _layers(doc) == {'grb_D10', 'grb_D0'}
    assert not _on(doc, 'grb_D10', 'LWPOLYLINE')[0].dxf.hasattr('const_width')

    object_app.options['geometry_dxf_gerber_traces'] = 'centerline'
    doc = obj.export_dxf()
    assert _layers(doc) == {'grb_D10', 'grb_D10_flash', 'grb_D0'}
    line = _on(doc, 'grb_D10', 'LWPOLYLINE')[0]
    assert line.dxf.const_width == pytest.approx(0.25)
    assert [tuple(p) for p in line.get_points(format='xy')] == [(0, 0), (10, 0)]

    object_app.options['geometry_dxf_layers'] = 'object'
    assert _layers(obj.export_dxf()) == {'grb', 'grb_W0.25'}


def test_cncjob_cut_and_travel_layers(object_app):
    from shapely.geometry import LineString
    from appObjects.CNCJobObject import CNCJobObject
    object_app.preprocessors = defaultdict(Mock)
    obj = _named(CNCJobObject('job', object_app), 'job')
    obj.tooldia = 0.5
    obj.gcode_parsed = [{'geom': LineString([(0, 0), (5, 0)]), 'kind': 'CS'},
                        {'geom': LineString([(5, 0), (9, 3)]), 'kind': 'TF'}]
    doc = obj.export_dxf()
    assert _layers(doc) == {'job_cut', 'job_travel'}
    assert doc.layers.get('job_cut').dxf.lineweight == 50
    assert len(_on(doc, 'job_cut', 'LWPOLYLINE')) == 1 and len(_on(doc, 'job_travel', 'LWPOLYLINE')) == 1
    object_app.options['geometry_dxf_travel'] = False
    assert _layers(obj.export_dxf()) == {'job_cut'}
    obj.tools = {3: {'tooldia': 0.2, 'gcode_parsed': obj.gcode_parsed}}
    assert _layers(obj.export_dxf()) == {'job_T3_cut'}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        print('DXF_RESULT=' + json.dumps(probe(sys.argv[2] == 'blocked')))
    else:
        test_dxf_without_matplotlib()
