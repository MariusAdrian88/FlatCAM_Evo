"""Standard inset accuracy checks against high resolution offsets and the physical cutter sweep.

inset_layers() and layer_paths() are also used by test_paint_geometry.py.
"""
import math
import unittest

import shapely
from shapely import MultiPolygon, Point, Polygon, box, linestrings, buffer
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


def cutter_sweep(paths, radius):
    # Buffer two-point segments, not whole paths: GEOS otherwise simplifies the
    # input line before buffering it. Capsule unions model a physical round tool.
    segments = linestrings([[a, b] for path in paths for a, b in zip(path.coords, list(path.coords)[1:])])
    return unary_union(buffer(segments, radius, quad_segs=128))


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
                        # Each high-resolution inset has an actual path within the allowance; the extra rings that
                        # cover the gaps between the insets are inside the margin (above). The contour keeps the edge
                        # error (the first paths, above); an inner ring may be off by a quarter of the overlap width,
                        # from its arcs and from its simplification.
                        inner = max(error, diameter * 0.15 / 4)
                        allowance = 2 * inner + diameter * (1 - math.cos(math.pi / 4096)) + 1e-10 * factor
                        points = shapely.points(shapely.get_coordinates(unary_union(reference)))
                        self.assertLessEqual(float(shapely.distance(points, unary_union(actual)).max()), allowance)

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
    unittest.main()
