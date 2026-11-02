"""Standard inset accuracy checks and recursive/unbounded comparison experiments.

Run with --benchmark for generation, segment counts and cutter-sweep differences.
Add --connection to measure the shared connection handler too.
Use --standard-benchmark for the implemented handler, or --accuracy for physical cutter sweeps.
"""
import json
import math
import statistics
import sys
import time
import unittest

import shapely
from shapely import MultiPolygon, Point, Polygon, box, linestrings, buffer, maximum_inscribed_circle
from shapely.ops import unary_union
from shapely.affinity import scale


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    return list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]


def inset_layers(polygon, diameter, overlap, steps, cumulative, bounded=False, tolerance=0.0):
    # Same full-circle to quadrant conversion as circle_quad_segs(). Keep this
    # numerical experiment independent of application/GUI imports.
    q = max(1, math.ceil(steps / 4))
    margins = polygon_parts(polygon.buffer(-diameter / 2, quad_segs=q))
    layers = [margins]
    step = diameter * (1 - overlap)
    chord_error = diameter * math.sin(math.pi / (8 * q)) ** 2
    if tolerance > 0:
        chord_error = min(chord_error, tolerance)
    for margin in margins:
        current = margin
        index = 1
        while True:
            distance = index * step
            inset_q = max(q, math.ceil(3 * math.pi / 8 * math.sqrt(distance / (2 * chord_error)))) if bounded else q
            current = (margin.buffer(-distance, quad_segs=inset_q) if cumulative
                       else current.buffer(-step, quad_segs=q))
            parts = [p for p in polygon_parts(current) if p.area > 0]
            if not parts:
                break
            layers.append(parts)
            if not cumulative:
                current = unary_union(parts) if len(parts) > 1 else parts[0]
            index += 1
    return layers


def layer_paths(layers):
    return [ring for layer in layers for part in layer for ring in [part.exterior, *part.interiors]]


def board():
    holes = [Point(x, y).buffer(0.7, quad_segs=16).exterior.coords
             for x in range(3, 29, 3) for y in range(3, 20, 3)]
    return Polygon(box(0, 0, 30, 21).exterior.coords, holes)


def cutter_sweep(paths, radius):
    # Buffer two-point segments, not whole paths: GEOS otherwise simplifies the
    # input line before buffering it. Capsule unions model a physical round tool.
    segments = linestrings([[a, b] for path in paths for a, b in zip(path.coords, list(path.coords)[1:])])
    return unary_union(buffer(segments, radius, quad_segs=128))


def physical_accuracy_report():
    import test_paint_geometry as paint_cases
    case = paint_cases.TestPaintGeometry()
    case.setUp()
    case.geometry.app.options = {'global_tolerance': 0.005}
    for units, factor in (('MM', 1), ('IN', 1 / 25.4)):
        case.geometry.app.units = case.geometry.app.app_units = units
        for kind in ('square', 'circle'):
            hole = box(2, 2, 4, 4) if kind == 'square' else Point(3, 3).buffer(0.7, quad_segs=16)
            polygon_mm = Polygon(box(0, 0, 6, 6).exterior.coords, [hole.exterior.coords])
            polygon = scale(polygon_mm, xfact=factor, yfact=factor, origin=(0, 0))
            for steps in (4, 16, 64):
                legacy = layer_paths(inset_layers(polygon, 0.4 * factor, 0.15, steps, False))
                old = scale(cutter_sweep(legacy, 0.2 * factor), xfact=1 / factor,
                            yfact=1 / factor, origin=(0, 0))
                for variant in ('unbounded', 'implemented'):
                    paths = (layer_paths(inset_layers(polygon, 0.4 * factor, 0.15, steps, True))
                             if variant == 'unbounded' else list(case.geometry.clear_polygon_shrink(
                                 polygon, 0.4 * factor, steps, overlap=0.15, connect=False).get_objects()))
                    sweep = scale(cutter_sweep(paths, 0.2 * factor), xfact=1 / factor,
                                  yfact=1 / factor, origin=(0, 0))
                    lost, added = old.difference(sweep), sweep.difference(old)

                    def diameter(geometry):
                        return 0 if geometry.is_empty else 2 * maximum_inscribed_circle(
                            geometry, tolerance=1e-7).length

                    print(json.dumps({
                        'units': units, 'hole': kind, 'steps': steps, 'variant': variant,
                        'lost_stock_patch_diameter_mm': diameter(lost),
                        'added_stock_patch_diameter_mm': diameter(added),
                        'extra_outside_patch_diameter_mm': diameter(added.difference(polygon_mm)),
                        'cutter_envelope_radial_error_mm': 0.2 * (1 - math.cos(math.pi / 512)),
                    }), flush=True)


def standard_benchmark():
    import test_paint_geometry as paint_cases
    from test_paint_geometry import camlib, storage_of
    case = paint_cases.TestPaintGeometry()
    case.setUp()
    case.geometry.app.options = {'global_tolerance': 0.005}
    case.geometry.app.units = 'MM'
    polygon = board()
    for diameter, overlap in ((0.4, 0.15), (0.2, 0.6)):
        for variant in ('recursive_reference', 'implemented'):
            generation, connection = [], []
            for _ in range(3):
                start = time.perf_counter()
                storage = (storage_of(layer_paths(inset_layers(polygon, diameter, overlap, 64, False)))
                           if variant == 'recursive_reference' else case.geometry.clear_polygon_shrink(
                               polygon, diameter, 64, overlap=overlap, connect=False))
                paths = list(storage.get_objects())
                generation.append(time.perf_counter() - start)
                start = time.perf_counter()
                connected = list(camlib.Geometry.paint_connect(storage, polygon, diameter, 64).get_objects())
                connection.append(time.perf_counter() - start)
            painted = [p.simplify(0.01) for p in connected]
            print(json.dumps({
                'variant': variant, 'diameter': diameter, 'overlap': overlap,
                'generation_median_seconds': statistics.median(generation),
                'connection_median_seconds': statistics.median(connection),
                'paths': len(paths), 'segments': sum(len(p.coords) - 1 for p in paths),
                'connected_paths': len(connected),
                'connected_segments': sum(len(p.coords) - 1 for p in connected),
                'segments_after_existing_paint_simplification': sum(len(p.coords) - 1 for p in painted),
            }), flush=True)


def report_case(name, polygon, diameter, overlap, steps=64, coverage=True, hausdorff=True,
                connect=False):
    results = []
    for cumulative in (False, True):
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            layers = inset_layers(polygon, diameter, overlap, steps, cumulative)
            paths = layer_paths(layers)
            samples.append(time.perf_counter() - start)
        result = {'case': name, 'cumulative': cumulative, 'seconds': statistics.median(samples),
                  'layers': len(layers), 'paths': len(paths),
                  'segments': sum(len(p.coords) - 1 for p in paths)}
        if connect:
            from test_paint_geometry import camlib, storage_of
            start = time.perf_counter()
            connected = camlib.Geometry.paint_connect(storage_of(paths), polygon, diameter, steps)
            connected = list(connected.get_objects()) if connected else []
            result.update(connection_seconds=time.perf_counter() - start,
                          connected_paths=len(connected),
                          connected_segments=sum(len(p.coords) - 1 for p in connected))
        print(json.dumps(result), flush=True)
        results.append((layers, paths))
    old_layers, old_paths = results[0]
    new_layers, new_paths = results[1]
    differences = []
    for old, new in zip(old_layers, new_layers):
        old = MultiPolygon(old)
        new = MultiPolygon(new)
        differences.append({'symmetric_area': old.symmetric_difference(new).area,
                            'old_parts': len(old.geoms), 'new_parts': len(new.geoms)})
        if hausdorff:
            differences[-1]['hausdorff'] = old.boundary.hausdorff_distance(new.boundary)
    comparison = {
        'case': name,
        'max_layer_symmetric_area': max(d['symmetric_area'] for d in differences),
        'topology_differences': [d for d in differences if d['old_parts'] != d['new_parts']],
        'circle_sagitta_at_step': diameter * (1 - overlap) * (
            1 - math.cos(math.pi / (4 * max(1, math.ceil(steps / 4)))))
    }
    if hausdorff:
        comparison['max_layer_hausdorff'] = max(d['hausdorff'] for d in differences)
    print(json.dumps(comparison), flush=True)
    if coverage:
        # Individual path buffers preserve the same cutter envelope on both sides.
        old_swept = unary_union([p.buffer(diameter / 2, quad_segs=16) for p in old_paths])
        new_swept = unary_union([p.buffer(diameter / 2, quad_segs=16) for p in new_paths])
        print(json.dumps({'case': name, 'old_swept_area': old_swept.area,
                          'new_swept_area': new_swept.area,
                          'lost_coverage_area': old_swept.difference(new_swept).area,
                          'added_coverage_area': new_swept.difference(old_swept).area,
                          'old_outside_area': old_swept.difference(polygon).area,
                          'new_outside_area': new_swept.difference(polygon).area}), flush=True)


class TestBoundedStandardOffsets(unittest.TestCase):
    def setUp(self):
        import test_paint_geometry as paint_cases
        case = paint_cases.TestPaintGeometry()
        case.setUp()
        self.geometry = case.geometry
        self.geometry.app.options = {'global_tolerance': 0.005}
        self.geometry.app.units = 'MM'

    def paths(self, polygon, diameter, overlap=0.15, steps=64, **kwargs):
        result = self.geometry.clear_polygon_shrink(
            polygon, diameter, steps, overlap=overlap, connect=False, **kwargs)
        return list(result.get_objects()) if result else []

    def test_actual_handler_bounds_exploding_segment_count(self):
        paths = self.paths(board(), 0.2, overlap=0.6)
        self.assertEqual(len(paths), 919)
        # Require an order-of-magnitude reduction while allowing the extra arc
        # segments needed by the physical-accuracy bound (not the unbounded candidate).
        self.assertLess(sum(len(p.coords) - 1 for p in paths), 3193340 // 10)

    def test_physical_sweeps_match_high_resolution_offsets_without_new_overcut(self):
        square_hole = Polygon(box(0, 0, 6, 6).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
        circle_hole = Polygon(box(0, 0, 6, 6).exterior.coords,
                              [Point(3, 3).buffer(0.7, quad_segs=16).exterior.coords])
        for units, factor in (('MM', 1), ('IN', 1 / 25.4)):
            self.geometry.app.units = self.geometry.app.app_units = units
            tolerance = 0.005 if units == 'MM' else 0.005 / 20
            for polygon_mm in (square_hole, circle_hole):
                polygon = scale(polygon_mm, xfact=factor, yfact=factor, origin=(0, 0))
                for steps in (4, 64):
                    with self.subTest(units=units, steps=steps, holes=len(polygon.interiors)):
                        diameter = 0.4 * factor
                        q = max(1, math.ceil(steps / 4))
                        error = min(tolerance, diameter * math.sin(math.pi / (8 * q)) ** 2)
                        margin = polygon.buffer(-diameter / 2, quad_segs=q)
                        reference = []
                        index = 0
                        while True:
                            offset = margin.buffer(-index * diameter * 0.85, quad_segs=1024)
                            if offset.is_empty:
                                break
                            reference.extend(layer_paths([polygon_parts(offset)]))
                            index += 1
                        actual = self.paths(polygon, diameter, steps=steps)
                        first = layer_paths([polygon_parts(margin)])
                        self.assertEqual([p.wkb for p in actual[:len(first)]], [p.wkb for p in first])
                        self.assertTrue(all(margin.buffer(1e-11 * factor).covers(p) for p in actual))
                        # Minkowski addition of the same physical cutter cannot
                        # increase Hausdorff error between centre-line sets. Test
                        # those sets directly: buffering a many-vertex line with
                        # GEOS performs additional input simplification, even at
                        # high quad_segs, so it isn't an exact cutter-sweep oracle.
                        # Each high-resolution inset has an actual path within the allowance; the extra rings that
                        # cover the gaps between the insets are inside the margin (above). The contour keeps the edge
                        # error (the first paths, above); an inner ring may be off by a quarter of the overlap width,
                        # from its arcs and from its simplification.
                        inner = max(error, diameter * 0.15 / 4)
                        allowance = 2 * inner + diameter * (1 - math.cos(math.pi / 4096)) + 1e-10 * factor
                        points = shapely.points(shapely.get_coordinates(unary_union(reference)))
                        self.assertLessEqual(float(shapely.distance(points, unary_union(actual)).max()), allowance)
                        # All centres lie in the original margin. Any radius-r
                        # disk reaching outside it crosses its unchanged boundary,
                        # so it cannot add overcut beyond that first contour sweep.

    def test_round_cutter_clears_legacy_corner_slivers_without_extra_outside_cut(self):
        polygon = Polygon(box(0, 0, 6, 6).exterior.coords, [box(2, 2, 4, 4).exterior.coords])
        for steps in (16, 64):
            with self.subTest(steps=steps):
                old = cutter_sweep(layer_paths(inset_layers(polygon, 0.4, 0.15, steps, False)), 0.2)
                new = cutter_sweep(self.paths(polygon, 0.4, steps=steps), 0.2)
                kernel_error = 0.2 * (1 - math.cos(math.pi / 512))
                self.assertTrue(old.difference(new).buffer(-2 * kernel_error).is_empty)
                self.assertLess(new.difference(old).difference(polygon).area, 1e-12)
                self.assertGreater(new.difference(old).area, 0)

if __name__ == '__main__':
    if '--standard-benchmark' in sys.argv:
        standard_benchmark()
    elif '--accuracy' in sys.argv:
        physical_accuracy_report()
    elif '--benchmark' in sys.argv:
        report_case('board_low_overlap', board(), 0.4, 0.15, connect='--connection' in sys.argv)
        report_case('board_high_overlap', board(), 0.2, 0.6, hausdorff=False,
                    connect='--connection' in sys.argv)
    else:
        unittest.main()
