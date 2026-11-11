#!/usr/bin/env python
"""
Automated tests for VisPy batch processing optimizations (Phase 2).

Tests cover:
1. add_batch() functionality
2. Synchronous add() behavior
3. Backface culling configuration
4. redraw() batch result collection
5. _collect_elements() method
6. Edge cases (empty batches, missing fc_options, etc.)

Run: python tests/test_vispy_batch.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import numpy as np
from unittest.mock import patch
from shapely.geometry import Polygon, LineString


class TestAddCopy(unittest.TestCase):
    """add_copy() reuses the position buffers of a shape and makes new color buffers."""

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        options = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
        self.source = ShapeCollectionVisual(linewidth=1, layers=1, pool=None, fcoptions=options)
        self.target = ShapeCollectionVisual(linewidth=1, layers=1, pool=None, fcoptions=options)
        # no GL canvas in the tests
        patcher = patch('appGUI.VisPyVisuals.set_state')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_two_shapes_get_their_own_color_per_vertex(self):
        collection = self.source
        line = LineString([(0, 0), (1, 1), (2, 0)])
        square = Polygon([(5, 0), (6, 0), (6, 1), (5, 1)])
        collection.add(shape=line, color='#FF0000FF', layer=0)
        collection.add(shape=square, color='#0000FFFF', face_color='#00FF00FF', layer=0)
        collection.redraw([])
        colors = collection._lines[0]._color
        n_line = 4
        self.assertEqual(colors.dtype.name, 'float32')
        self.assertEqual(colors.shape, (n_line + 8, 4))
        self.assertTrue(np.all(colors[:n_line] == (1.0, 0.0, 0.0, 1.0)))
        self.assertTrue(np.all(colors[n_line:] == (0.0, 0.0, 1.0, 1.0)))
        mesh = collection._meshes[0]
        mesh_colors = mesh.vertex_colors
        self.assertEqual(len(mesh_colors), len(mesh.vertices))
        self.assertEqual(mesh_colors.dtype.name, 'float32')
        self.assertEqual(mesh.faces.shape, (2, 3))
        self.assertLess(mesh.faces.max(), len(mesh.vertices))
        self.assertTrue(np.all(mesh_colors == (0.0, 1.0, 0.0, 1.0)))

    def test_update_color_changes_the_painted_shapes(self):
        collection = self.source
        k0 = collection.add(shape=LineString([(0, 0), (1, 1)]), color='#FF0000FF', layer=0)
        collection.add(shape=LineString([(2, 0), (3, 1)]), color='#FF0000FF', layer=0)
        collection.redraw([])
        collection.update_color(new_line_color='#0000FFFF', indexes=[k0])
        collection.redraw([])
        colors = collection._lines[0]._color
        self.assertTrue(np.all(colors[:2] == (0.0, 0.0, 1.0, 1.0)))
        self.assertTrue(np.all(colors[2:] == (1.0, 0.0, 0.0, 1.0)))


def _box(x, y, size=1.0):
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def _reference(c):
    """From-scratch join of the visible shapes in the order of c.data, per layer (the old rebuild)."""
    layers = len(c._meshes) + 1
    out = []
    for layer in range(layers):
        pts, cols, verts, tris, mcols = [], [], [], [], []
        count = 0
        for key, d in c.data.items():
            if not d['visible'] or 'line_pts' not in d:
                continue
            if (layers - 1 if key in c._top_keys else d['layer']) != layer:
                continue
            pts.append(d['line_pts'])
            cols.append(np.broadcast_to(d['line_rgba'], (len(d['line_pts']), 4)))
            if len(d['mesh_tris']) and len(d['mesh_vertices']):
                tris.append(d['mesh_tris'] + count)
                verts.append(d['mesh_vertices'])
                mcols.append(np.broadcast_to(d['mesh_rgba'], (len(d['mesh_vertices']), 4)))
                count += len(d['mesh_vertices'])
        line = (np.concatenate(pts), np.concatenate(cols)) if pts and len(np.concatenate(pts)) else None
        mesh = (np.concatenate(verts), np.concatenate(tris).reshape(-1, 3), np.concatenate(mcols)) if verts else None
        out.append((line, mesh))
    return out


def _submitted(c):
    """What the visuals got from the last rebuild, per layer."""
    out = []
    for line, mesh in zip(c._lines + [c._top_line], c._meshes + [c._top_mesh]):
        sub_line = (np.asarray(line._pos), np.asarray(line._color)) if line.visible else None
        sub_mesh = None
        if mesh.visible:
            sub_mesh = (mesh.vertices, mesh.faces, mesh.vertex_colors)
        out.append((sub_line, sub_mesh))
    return out


class TestBlockCache(unittest.TestCase):
    """The rebuild joins only the changed groups; the result is the same as a join of all the shapes."""

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup
        self.options = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
        self.c = ShapeCollectionVisual(linewidth=1, layers=2, pool=None, fcoptions=self.options)
        self.groups = [ShapeGroup(self.c) for _ in range(3)]
        patcher = patch('appGUI.VisPyVisuals.set_state')
        patcher.start()
        self.addCleanup(patcher.stop)

    def fill(self, group, x0, layers=(0, 1)):
        for i, layer in enumerate(layers):
            group.add(shape=_box(x0 + 2 * i, 0), color='#FF0000FF', face_color='#00FF00FF', layer=layer)
        group.add(shape=LineString([(x0, 5), (x0 + 1, 6), (x0 + 2, 5)]), color='#0000FFFF', layer=layers[0])

    def check(self):
        self.c.redraw([])
        for layer, (got, want) in enumerate(zip(_submitted(self.c), _reference(self.c))):
            for g, w in zip(got, want):
                self.assertEqual(g is None, w is None, "layer %d" % layer)
                if w is not None:
                    for a, b in zip(g, w):
                        np.testing.assert_array_equal(a, b, err_msg="layer %d" % layer)

    def test_mixed_operations_equal_a_full_join(self):
        a, b, c3 = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.fill(c3, 20)
        self.check()
        b.visible = False
        self.check()
        b.visible = True
        a.move_to_top()
        self.check()
        c3.transform((1, 0, 0, -1, 3, 4))      # a mirror: the triangles are flipped
        self.check()
        b.redraw(update_colors=('#112233FF', '#445566FF'))
        self.check()
        c3.move_to_top()                        # the selection changes: a is normal again
        self.check()
        b.remove(b._indexes[1])
        self.check()
        a.clear()
        self.check()
        self.fill(a, 30)                        # a again, after the others
        self.check()
        a.update_visibility(False, indexes=a._indexes[:2])
        self.check()

    def test_hidden_then_recolored_then_shown(self):
        a, b, _ = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.check()
        a.visible = False
        self.check()
        a.redraw(update_colors=('#AA0000FF', '#00AA00FF'))
        a.visible = True
        self.check()
        mesh = self.c._meshes[0].vertex_colors
        np.testing.assert_allclose(mesh[0], (2 / 3, 0.0, 0.0, 1.0), atol=1e-6)

    def test_update_color_without_rebuild_changes_only_the_color_buffer(self):
        a, b, _ = self.groups
        self.fill(a, 0)
        self.fill(b, 10)
        self.check()
        mesh = self.c._meshes[0]
        positions, faces = mesh.vertices, mesh.faces
        self.c.update_color(new_mesh_color='#0000FFFF', indexes=b._indexes)
        colors = mesh.vertex_colors
        self.assertIs(mesh.vertices, positions)
        self.assertIs(mesh.faces, faces)
        self.assertEqual(len(colors), len(positions))
        in_b = positions[:, 0] >= 10
        self.assertTrue(np.all(colors[in_b] == (0.0, 0.0, 1.0, 1.0)))
        self.assertTrue(np.all(colors[~in_b] == (0.0, 1.0, 0.0, 1.0)))
        # the outlines too: new colors for the shapes of b, the positions are the same arrays
        line = self.c._lines[0]
        pos = line._pos
        self.c.update_color(new_line_color='#FF00FFFF', indexes=b._indexes)
        self.assertIs(line._pos, pos)
        in_b = pos[:, 0] >= 10
        self.assertTrue(np.all(line._color[in_b] == (1.0, 0.0, 1.0, 1.0)))
        self.assertFalse(np.any(np.all(line._color[~in_b] == (1.0, 0.0, 1.0, 1.0), axis=1)))
        self.check()

    def test_segment_line_visual_ignores_a_wrong_color_count_and_draws_no_old_data(self):
        from appGUI.VisPyVisuals import _new_line_visual
        line = _new_line_visual()
        pos = np.zeros((4, 2), dtype=np.float32)
        line.set_data(pos=pos, color=np.ones((4, 4), dtype=np.float32), width=1)
        line.set_colors(np.zeros((6, 4), dtype=np.float32))      # the count does not fit: ignored
        self.assertTrue(np.all(np.asarray(line._color) == 1.0))
        line.set_colors(np.zeros((4, 4), dtype=np.float32))
        self.assertTrue(np.all(np.asarray(line._color) == 0.0))

    def test_pending_pool_results_are_collected_later(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual, ShapeGroup, _update_shape_buffers

        class Result:
            def __init__(self, items):
                self.items = items

            def ready(self):
                return True

            def wait(self):
                pass

            def get(self):
                return [_update_shape_buffers(dict(d)) for d in self.items]

        class Pool:
            def map_async(self, func, items):
                return Result(list(items))

        c = ShapeCollectionVisual(linewidth=1, layers=2, pool=Pool(), fcoptions={"global_backface_culling": True})
        self.c = c
        g1, g2 = ShapeGroup(c), ShapeGroup(c)
        g1.add_batch([{'shape': _box(0, 0), 'color': '#FF0000FF', 'face_color': '#00FF00FF', 'layer': 0}])
        g2.add_batch([{'shape': _box(5, 0), 'color': '#FF0000FF', 'face_color': '#0000FFFF', 'layer': 1}])
        # the shapes are placeholders until the results are collected
        c._update_deferred()
        self.assertTrue(all(sub[1] is None for sub in _submitted(c)))
        self.check()
        self.assertIsNotNone(_submitted(c)[1][1])

    def test_failed_rebuild_is_repaired_by_the_next_one(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        a, b, c3 = self.groups
        for g, x in zip(self.groups, (0, 10, 20)):
            self.fill(g, x)
        self.check()
        # no rebuild yet: two blocks are dirty
        self.c.update_visibility(False, indexes=a._indexes + b._indexes)
        real = ShapeCollectionVisual._join_block
        calls = []

        def flaky(self_, block):
            calls.append(block)
            if len(calls) == 2:
                raise RuntimeError("join failed")
            return real(self_, block)

        with patch.object(ShapeCollectionVisual, '_join_block', flaky):
            with self.assertRaises(RuntimeError):
                self.c.redraw([])
        self.check()


if __name__ == '__main__':
    unittest.main()
