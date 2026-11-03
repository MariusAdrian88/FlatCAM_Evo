"""Plot preparation regressions and a repeatable CPU plotting benchmark.

Run from the repository root:
    .venv/Scripts/python tests/test_cncjob_plot_preparation.py
    .venv/Scripts/python tests/test_cncjob_plot_preparation.py --benchmark --buffers

The benchmark includes real tessellation and VisPy buffer submission with --buffers.
It doesn't measure GPU drawing or application startup. Input construction isn't timed.
"""
import argparse
import ast
import cProfile
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely import LineString, Polygon

from camlib import CNCjob


COLORS = {'T': ['#F0C0404C', '#B080304C'], 'C': ['#4080FFFF', '#204080FF']}


def make_job(paths, source='geometry'):
    job = SimpleNamespace(
        gcode_parsed=paths, tooldia=0.8, steps_per_circle=16,
        obj_options={'type': source}, annotations_dict={}, batch=[])
    job._app_option = lambda key: 'G90'
    job.add_shapes_batch = lambda batch, **kwargs: job.batch.extend(batch)
    return job


def make_paths(count):
    paths = []
    for i in range(count):
        x, y = (i % 200) * 2.0, (i // 200) * 2.0
        coords = [(x, y), (x + 0.3, y + 0.2), (x + 0.8, y + 0.1), (x + 1, y + 0.5)]
        paths.append({'geom': LineString(coords), 'kind': ['T' if i % 2 == 0 else 'C', 'F']})
    return paths


def plot(job, dia=0.8, kind='all'):
    return CNCjob.plot2(job, tooldia=dia, obj=job, visible=True, kind=kind, color=COLORS)


def fingerprint(job):
    digest = hashlib.sha256()
    for item in job.batch:
        digest.update(item['shape'].wkb)
        digest.update(repr({k: v for k, v in item.items() if k != 'shape'}).encode())
    digest.update(json.dumps(job.annotations_dict, sort_keys=True).encode())
    return digest.hexdigest()


class TestCNCJobPlotPreparation(unittest.TestCase):

    def test_native_tessellation_preserves_holes_and_counterclockwise_faces(self):
        import numpy as np
        import shapely
        from appGUI.VisPyTesselators import GLUTess

        if not hasattr(shapely, 'constrained_delaunay_triangles'):
            self.skipTest('Native constrained triangulation requires Shapely 2.1+')
        polygon = Polygon([(0, 0), (0, 6), (6, 6), (6, 0), (3, 2)],
                          holes=[[(1, 2), (1, 3), (2, 3), (2, 2)]])
        with patch('appGUI.VisPyTesselators.GLU.gluNewTess',
                   side_effect=AssertionError('Valid polygons should avoid Python GLU callbacks')):
            indices, vertices = GLUTess().triangulate(polygon)
        triangles = np.asarray(vertices)[np.asarray(indices).reshape(-1, 3)]
        a, b = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        self.assertTrue((cross > 0).all())
        self.assertAlmostEqual(cross.sum() / 2, polygon.area)
        for triangle in triangles:
            self.assertTrue(polygon.covers(Polygon(triangle)))

    def test_geometry_colors_layers_and_annotations(self):
        paths = make_paths(12)
        for source in ('geometry', 'excellon'):
            if source == 'excellon':
                paths = [dict(p, geom=LineString(list(p['geom'].coords) + [p['geom'].coords[0]]))
                         if p['kind'][0] == 'C' else p for p in paths]
            for kind in ('all', 'travel', 'cut'):
                for dia in (0, 0.8):
                    with self.subTest(source=source, kind=kind, dia=dia):
                        job = make_job(paths, source)
                        plot(job, dia, kind)
                        selected = [p for p in paths if kind == 'all' or p['kind'][0] == kind[0].upper()]
                        self.assertEqual(len(job.batch), len(selected))
                        for item, path in zip(job.batch, selected):
                            code = path['kind'][0]
                            expected = path['geom']
                            if dia:
                                expected = (Polygon(expected) if source == 'excellon' and code == 'C'
                                            else expected.buffer(dia / 1.99999999, 16 // 4))
                                expected = expected.simplify(0.0005)
                                self.assertEqual(item['face_color'], COLORS[code][0])
                                self.assertEqual(item['layer'], 1 if code == 'C' else 2)
                            self.assertTrue(item['shape'].equals_exact(expected, 0))
                            self.assertEqual(item['color'], COLORS[code][1])
                        positions = [pos for p in paths if p['kind'][0] == 'T'
                                     for pos in (p['geom'].coords[0], p['geom'].coords[-1])]
                        if dia:
                            self.assertEqual(job.annotations_dict[dia]['pos'], positions)
                            self.assertEqual(job.annotations_dict[dia]['text'],
                                             [str(i + 1) for i in range(len(positions))])
                        else:
                            self.assertEqual(job.annotations_dict, {})

def benchmark(args):
    plot_method = CNCjob.plot2
    if args.buffers:
        import appGUI.VisPyVisuals as visuals
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        from vispy.gloo.context import FakeCanvas
    if args.baseline_ref:
        import camlib
        import appGUI.VisPyTesselators as tessellators

        def historical_symbols(path, names, namespace):
            source = subprocess.check_output(['git', 'show', f'{args.baseline_ref}:{path}'],
                                             cwd=Path(__file__).resolve().parents[1], encoding='utf-8')
            tree = ast.parse(source)
            nodes = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
            return namespace

        namespace = historical_symbols('camlib.py', {'plot2'}, dict(vars(camlib)))
        plot_method = namespace['plot2']
        if args.buffers:
            old_tess = historical_symbols('appGUI/VisPyTesselators.py', {'GLUTess'}, dict(vars(tessellators)))
            namespace = dict(vars(visuals), GLUTess=old_tess['GLUTess'])
            historical_symbols('appGUI/VisPyVisuals.py',
                               {'_update_shape_buffers', '_linearring_to_segments', '_linestring_to_segments'}, namespace)
            visuals._update_shape_buffers = namespace['_update_shape_buffers']
    rows = []
    for count in args.sizes:
        paths = make_paths(count)
        for dia, kind in ((0.8, 'all'), (0.8, 'travel'), (0.8, 'cut'), (0, 'all')):
            times = []
            for repeat in range(args.repeat + 1):
                job = make_job(paths)
                if args.buffers:
                    # Don't accumulate submission commands between independent repetitions.
                    canvas = FakeCanvas()
                start = perf_counter()
                plot_method(job, tooldia=dia, obj=job, visible=True, kind=kind, color=COLORS)
                prepared = perf_counter()
                if args.buffers:
                    collection = ShapeCollectionVisual(fcoptions={'global_graphic_engine_3d_no_mp': True})
                    collection.add_batch(job.batch, tolerance=0.001, visible=True)
                buffered = perf_counter()
                if args.buffers:
                    collection.redraw()
                end = perf_counter()
                if repeat:
                    times.append((prepared - start, buffered - prepared, end - buffered, end - start))
            row = dict(paths=count, diameter=dia, kind=kind, shapes=len(job.batch), fingerprint=fingerprint(job))
            row.update(zip(('prepare_s', 'buffers_s', 'submit_s', 'total_s'),
                           (statistics.median(t[i] for t in times) for i in range(4))))
            rows.append(row)
            print(json.dumps(row), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')
    if args.profile:
        job = make_job(make_paths(max(args.sizes)))
        profiler = cProfile.Profile()
        profiler.runcall(plot_method, job, tooldia=0.8, obj=job, visible=True, color=COLORS)
        profiler.print_stats(sort='cumulative')


if __name__ == '__main__':
    if '--benchmark' in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument('--benchmark', action='store_true')
        parser.add_argument('--buffers', action='store_true')
        parser.add_argument('--sizes', nargs='+', type=int, default=[2000, 10000])
        parser.add_argument('--repeat', type=int, default=3)
        parser.add_argument('--output')
        parser.add_argument('--profile', action='store_true')
        parser.add_argument('--baseline-ref', help='Benchmark plotting functions from this Git revision')
        benchmark(parser.parse_args())
    else:
        unittest.main()
