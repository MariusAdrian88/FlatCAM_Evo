"""CPU benchmark of the real Geometry/Gerber plot methods and VisPy submission.

Run from the repository root, optionally with --baseline-ref <git revision>.
Input generation and GPU execution aren't timed. One warm-up precedes each case.
"""
import argparse
import ast
import cProfile
import hashlib
import json
import logging
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace, MethodType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import LineString, Point
from vispy.gloo.context import FakeCanvas

import appGUI.VisPyTesselators as tessellators
import appGUI.VisPyVisuals as visuals
import appObjects.GeometryObject as geometry_module
import appObjects.GerberObject as gerber_module


def historical(path, names, namespace, revision):
    source = subprocess.check_output(['git', 'show', f'{revision}:{path}'],
                                     cwd=Path(__file__).resolve().parents[1], encoding='utf-8')
    nodes = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


def make_object(kind, geometry, solid=True, follow=False):
    obj = SimpleNamespace(
        deleted=False, obj_options={'plot': True, 'solid': solid, 'follow': follow, 'multicolored': False},
        outline_color='#204080FF', fill_color='#4080FFFF', solid_geometry=geometry,
        follow_geometry=geometry, tools={1: {'solid_geometry': geometry, 'data': {}}}, batch=[], times={},
        app=SimpleNamespace(log=logging.getLogger('benchmark')))
    obj.random_color = lambda: '#4080FFFF'
    obj._app_option = lambda key: True if key == 'gerber_plot_line_enable' else '#204080FF'
    obj.mark_full_plot = lambda: None
    obj._collect_elements = MethodType(geometry_module.GeometryObject._collect_elements, obj)
    obj.clear = lambda: None
    return obj


def benchmark(args):
    geometry_plot = geometry_module.GeometryObject.plot
    gerber_plot = gerber_module.GerberObject.plot
    collection_class = visuals.ShapeCollectionVisual
    if args.baseline_ref:
        old_tess = historical('appGUI/VisPyTesselators.py', {'GLUTess'}, dict(vars(tessellators)), args.baseline_ref)
        old_visuals = historical('appGUI/VisPyVisuals.py',
                                 {'_update_shape_buffers', '_linearring_to_segments', '_linestring_to_segments',
                                  'ShapeCollectionVisual'}, dict(vars(visuals), GLUTess=old_tess['GLUTess']),
                                 args.baseline_ref)
        collection_class = old_visuals['ShapeCollectionVisual']
        geometry_plot = historical('appObjects/GeometryObject.py', {'plot', '_collect_elements'},
                                   dict(vars(geometry_module)), args.baseline_ref)['plot']
        gerber_plot = historical('appObjects/GerberObject.py', {'plot'},
                                 dict(vars(gerber_module)), args.baseline_ref)['plot']

    lines = [LineString([(i % 200, i // 200), (i % 200 + 0.2, i // 200 + 0.3),
                         (i % 200 + 0.4, i // 200)]) for i in range(args.count)]
    polygons = [Point(i % 100, i // 100).buffer(0.4, quad_segs=16).difference(
        Point(i % 100, i // 100).buffer(0.1, quad_segs=8)) for i in range(args.count)]
    cases = [('geometry-lines', 'geometry', lines, True, False),
             ('geometry-contours', 'geometry', polygons, True, False),
             ('gerber-surfaces', 'gerber', polygons, True, False),
             ('gerber-contours', 'gerber', polygons, False, False),
             ('gerber-follow', 'gerber', lines, True, True)]
    rows = []
    for name, kind, geometry, solid, follow in cases:
        timings = []
        for repeat in range(args.repeat + 1):
            canvas = FakeCanvas()
            collection = collection_class(fcoptions={'global_graphic_engine_3d_no_mp': True})
            obj = make_object(kind, geometry, solid, follow)

            def add_batch(batch, **kwargs):
                obj.times['prepared'] = perf_counter()
                obj.batch.extend(batch)
                keys = collection.add_batch(batch, tolerance=0.001, **kwargs)
                obj.times['buffered'] = perf_counter()
                return keys

            obj.add_shapes_batch = add_batch
            obj.add_shape = lambda **kwargs: add_batch(
                [{key: value for key, value in kwargs.items() if key != 'visible'}],
                visible=kwargs.get('visible', True))
            obj.shapes = collection
            start = perf_counter()
            (geometry_plot if kind == 'geometry' else gerber_plot)(obj)
            end = perf_counter()
            assert len(obj.batch) == args.count, 'Plot must retain every input shape'
            if repeat:
                timings.append((obj.times['prepared'] - start,
                                obj.times['buffered'] - obj.times['prepared'],
                                end - obj.times['buffered'], end - start))
        digest = hashlib.sha256()
        for item in obj.batch:
            digest.update(item['shape'].wkb)
            digest.update(repr({k: v for k, v in item.items() if k != 'shape'}).encode())
        row = dict(case=name, shapes=len(obj.batch), fingerprint=digest.hexdigest())
        row.update(zip(('prepare_s', 'buffers_s', 'submit_s', 'total_s'),
                       (statistics.median(t[i] for t in timings) for i in range(4))))
        rows.append(row)
        print(json.dumps(row), flush=True)
        if args.profile:
            profiler = cProfile.Profile()
            profiler.runcall(collection.add_batch, obj.batch, tolerance=0.001)
            import pstats
            pstats.Stats(profiler).sort_stats('cumulative').print_stats(15)
    if args.output:
        Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=2000)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output')
    benchmark(parser.parse_args())
