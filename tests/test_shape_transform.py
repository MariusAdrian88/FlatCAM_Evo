#!/usr/bin/env python
"""
Tests for ShapeCollectionVisual.transform_keys(): the plot of a transformed object is made by mapping the stored
vertex arrays, with the same result as a new triangulation of the transformed geometry.
"""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, LineString

from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
from appObjects.AppObjectTemplate import plot_transformed_many
from tests.helpers_objects import make_app, make_same_diameter_excellon

OPTS = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}


def new_collection():
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None, fcoptions=OPTS)


def sample_shapes():
    holed = Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(2, 2), (4, 2), (4, 4), (2, 4)]])
    tri = Polygon([(12, 1), (15, 1), (13, 5)])
    line = LineString([(0, 0), (3, 4), (6, 1)])
    return [holed, tri, line]


def to_affine(matrix, geo):
    return affinity.affine_transform(geo, matrix)


def sorted_pts(arr):
    arr = np.round(np.asarray(arr, dtype=np.float64), 3)
    return arr[np.lexsort((arr[:, 1], arr[:, 0]))]


def tri_area_signed(verts, tris):
    t = tris.reshape(-1, 3)
    p0, p1, p2 = verts[t[:, 0]], verts[t[:, 1]], verts[t[:, 2]]
    cross = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p1[:, 1] - p0[:, 1]) * (p2[:, 0] - p0[:, 0])
    return cross / 2.0


class TestTransformKeys(unittest.TestCase):
    def setUp(self):
        # the merge of the buffers into the GL visuals needs a canvas; only the stored arrays are tested here
        p = patch.object(ShapeCollectionVisual, '_ShapeCollectionVisual__update')
        p.start()
        self.addCleanup(p.stop)

    MATRICES = {
        'rotate': (0.0, -1.0, 1.0, 0.0, 0.0, 0.0),
        'rotate30': (np.cos(0.5236), -np.sin(0.5236), np.sin(0.5236), np.cos(0.5236), 3.0, -2.0),
        'mirror_x': (1.0, 0.0, 0.0, -1.0, 0.0, 20.0),
        'mirror_y': (-1.0, 0.0, 0.0, 1.0, 20.0, 0.0),
        'skew': (1.0, 0.5, 0.2, 1.0, 0.0, 0.0),
        'scale': (2.0, 0.0, 0.0, 3.0, 0.0, 0.0),
        'offset': (1.0, 0.0, 0.0, 1.0, 100.0, -50.0),
    }

    def build(self):
        c = new_collection()
        keys = c.add_batch([{'shape': s, 'color': 'red', 'face_color': 'blue'} for s in sample_shapes()])
        c.redraw([])
        return c, keys

    def test_matches_fresh_triangulation(self):
        for name, m in self.MATRICES.items():
            with self.subTest(name):
                c, keys = self.build()
                self.assertTrue(c.transform_keys(keys, m))

                ref = new_collection()
                rkeys = ref.add_batch([{'shape': to_affine(m, s), 'color': 'red', 'face_color': 'blue'}
                                       for s in sample_shapes()])
                ref.redraw([])

                for k, rk, shape in zip(keys, rkeys, sample_shapes()):
                    d, r = c.data[k], ref.data[rk]
                    self.assertEqual(d['line_pts'].dtype, np.float32)
                    self.assertEqual(d['line_pts'].shape, r['line_pts'].shape)
                    np.testing.assert_allclose(sorted_pts(d['line_pts']), sorted_pts(r['line_pts']), atol=2e-3)
                    if len(r['mesh_vertices']):
                        np.testing.assert_allclose(sorted_pts(d['mesh_vertices']), sorted_pts(r['mesh_vertices']),
                                                   atol=2e-3)
                        area = to_affine(m, shape).area
                        areas = tri_area_signed(d['mesh_vertices'].astype(np.float64), d['mesh_tris'])
                        ref_areas = tri_area_signed(r['mesh_vertices'].astype(np.float64), r['mesh_tris'])
                        # the winding is the same as that of a fresh triangulation: all the same sign
                        self.assertEqual(np.sign(areas.sum()), np.sign(ref_areas.sum()))
                        self.assertTrue(np.all(np.sign(areas) == np.sign(areas.sum())))
                        self.assertAlmostEqual(abs(areas.sum()), area, delta=1e-3 * max(1.0, area))
                    # colors are kept
                    np.testing.assert_array_equal(d['mesh_rgba'], r['mesh_rgba'])
                    np.testing.assert_array_equal(d['line_rgba'], r['line_rgba'])


    def test_excellon_scale_replots_round_holes_in_the_cached_shapes(self):
        # the cached plot of a scaled Excellon must be made again from the rebuilt geometry, not mapped:
        # a 1 mm hole at x=10 keeps the 9.5 .. 10.5 bounds instead of becoming 9 .. 11
        app = make_app()
        obj = make_same_diameter_excellon(app)
        collection = new_collection()
        obj.shapes = ShapeGroup(collection)

        obj.scale(2, 1, point=(0, 0))
        self.assertFalse(obj.plot_transformed((2.0, 0.0, 0.0, 1.0, 0.0, 0.0)))

        hole = collection.data[0]['line_pts']
        self.assertAlmostEqual(float(hole[:, 0].min()), 9.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 0].max()), 10.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 1].min()), 4.5, delta=0.02)
        self.assertAlmostEqual(float(hole[:, 1].max()), 5.5, delta=0.02)

        slot = collection.data[3]['line_pts']
        self.assertAlmostEqual(float(slot[:, 0].min()), 39.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 0].max()), 52.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 1].min()), 4.5, delta=0.02)
        self.assertAlmostEqual(float(slot[:, 1].max()), 5.5, delta=0.02)


class TestPlotTransformedMany(unittest.TestCase):
    MATRIX = (1.0, 0.0, 0.0, 1.0, 3.0, -2.0)

    @classmethod
    def setUpClass(cls):
        # the merge of the buffers into the GL visuals needs a canvas; a fake one is enough
        from vispy.gloo.context import FakeCanvas
        cls._canvas = FakeCanvas()

    def test_batch_maps_all_objects_with_one_redraw(self):
        app = make_app()
        shared = new_collection()
        objs = [make_same_diameter_excellon(app, name=n) for n in ('a', 'b')]
        for obj in objs:
            obj.shapes = ShapeGroup(shared)
            obj.plot()
        objs[1].offset((20, 0))
        objs[1].plot()
        for obj in objs:
            obj.offset((3, -2))

        with patch.object(ShapeCollectionVisual, '_ShapeCollectionVisual__update') as update:
            self.assertEqual(plot_transformed_many(objs, self.MATRIX), [True, True])
        self.assertEqual(update.call_count, 1)

        ref = new_collection()
        for obj in objs:
            obj.shapes = ShapeGroup(ref)
            obj.plot()
        got = sorted_pts(np.concatenate([shared.data[k]['line_pts'] for k in sorted(shared.data)]))
        want = sorted_pts(np.concatenate([ref.data[k]['line_pts'] for k in sorted(ref.data)]))
        np.testing.assert_allclose(got, want, atol=2e-3)

    def test_cncjob_with_annotations_is_mapped_without_a_new_plot(self):
        from collections import defaultdict
        from unittest.mock import Mock
        from appGUI.VisPyVisuals import TextCollectionVisual, TextGroup
        from appObjects.CNCJobObject import CNCJobObject

        app = make_app()
        app.preprocessors = defaultdict(Mock)
        job = CNCJobObject('job', app)
        paths = [Polygon([(0, 0), (4, 0), (4, 3), (3.8, 3), (3.8, 0.2), (0, 0.2)]), Polygon([(8, 1), (8.4, 1), (9, 5)])]
        collection = new_collection()
        job.shapes = ShapeGroup(collection)
        job.shapes.add_batch([{'shape': p, 'color': '#FF0000FF', 'face_color': '#00FF00FF'} for p in paths])
        job.mark_full_plot()
        positions = [(0.0, 0.0), (4.0, 3.0), (8.0, 1.0)]
        job.annotation = TextGroup(TextCollectionVisual())
        job.annotation.set(text=['1', '2', '3'], pos=list(positions))
        job.annotations_dict = {0.2: {'pos': list(positions), 'text': ['1', '2', '3']}}
        job.plot = Mock()

        self.assertTrue(job.plot_transformed(self.MATRIX))
        job.plot.assert_not_called()

        ref = new_collection()
        ref.add_batch([{'shape': to_affine(self.MATRIX, p), 'color': '#FF0000FF', 'face_color': '#00FF00FF'}
                       for p in paths])
        for k, rk in zip(sorted(collection.data), sorted(ref.data)):
            np.testing.assert_allclose(sorted_pts(collection.data[k]['mesh_vertices']),
                                       sorted_pts(ref.data[rk]['mesh_vertices']), atol=2e-3)
        want = [(x + 3.0, y - 2.0) for x, y in positions]
        np.testing.assert_allclose(job.annotation._collection.data[0]['pos'], want)
        np.testing.assert_allclose(job.annotations_dict[0.2]['pos'], want)


if __name__ == '__main__':
    unittest.main()
