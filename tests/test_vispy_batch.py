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


class TestShapeCollectionVisualBatch(unittest.TestCase):
    """Tests for ShapeCollectionVisual batch processing."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Import after path setup
        from appGUI.VisPyVisuals import ShapeCollectionVisual, _update_shape_buffers
        self.ShapeCollectionVisual = ShapeCollectionVisual
        self._update_shape_buffers = _update_shape_buffers
        
        # Mock fc_options with all required keys
        self.default_fc_options = {
            "global_graphic_engine_3d_no_mp": False,
            "global_backface_culling": True,
        }
    
class TestCollectElements(unittest.TestCase):
    """Tests for GeometryObject._collect_elements() method."""
    
    def setUp(self):
        """Set up test fixtures."""
        from appObjects.GeometryObject import GeometryObject
        # Can't fully instantiate without app, so we'll test the method directly
        self.GeometryObject = GeometryObject
    
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
        mesh_colors = collection._meshes[0].mesh_data.get_face_colors()
        self.assertEqual(len(mesh_colors), 2)
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

class TestDeferredRedraw(unittest.TestCase):
    """A deferred collection joins a burst of redraws into few rebuilds."""

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeCollectionVisual
        options = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}
        self.collection = ShapeCollectionVisual(linewidth=1, layers=1, pool=None, fcoptions=options, deferred=True)
        self.rebuilds = 0
        patcher = patch('appGUI.VisPyVisuals.set_state', side_effect=self._count)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _count(self, **kwargs):
        self.rebuilds += 1

    def _burst(self, count):
        from appGUI.VisPyVisuals import ShapeGroup
        for i in range(count):
            group = ShapeGroup(self.collection)
            group.add(shape=Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]), color='#112233FF',
                      face_color='#11223350', layer=0)
            group.redraw()

if __name__ == '__main__':
    unittest.main()
