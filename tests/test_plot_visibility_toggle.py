"""
Tests for the fast plot toggle: show or hide the shapes of the last full plot without a new plot.

- ShapeGroup.visible and ShapeGroup.update_visibility() must update the canvas (the collection skips the
  update if it is not marked dirty).
- FlatCAMObj.show_plotted_shapes() must work only while the shapes of the last full plot are on the canvas.
"""

import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_fake_canvas = None


def setUpModule():
    # the collection update sets a GL state; gloo needs a current canvas for this, a fake one is enough
    global _fake_canvas
    from vispy.gloo.context import FakeCanvas
    _fake_canvas = FakeCanvas()


def _square(x):
    return Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)])


def _new_collection():
    from appGUI.VisPyVisuals import ShapeCollectionVisual
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None,
                                 fcoptions={"global_graphic_engine_3d_no_mp": True})


def _add_squares(group, n, start=0):
    return group.add_batch([{'shape': _square(start + i), 'color': '#FF0000FF', 'face_color': '#00FF00FF'}
                            for i in range(n)])


class TestShapeGroupVisibility(unittest.TestCase):

    def test_visible_setter_and_update_visibility_update_canvas(self):
        from appGUI.VisPyVisuals import ShapeGroup
        collection = _new_collection()
        group = ShapeGroup(collection)
        _add_squares(group, 3)
        group.redraw()
        line = collection._lines[1]   # layer 1 is the default layer of the shapes
        self.assertTrue(line.visible)

        group.visible = False
        self.assertFalse(line.visible)
        self.assertTrue(all(not d['visible'] for d in collection.data.values()))
        group.visible = True
        self.assertTrue(line.visible)

        keys = list(group._indexes)
        group.update_visibility(False, indexes=keys[:2])
        self.assertEqual([collection.data[k]['visible'] for k in keys], [False, False, True])
        self.assertTrue(line.visible)
        group.update_visibility(False, indexes=keys[2:])
        self.assertFalse(line.visible)


class TestMoveToTop(unittest.TestCase):

    def test_moves_group_to_end_and_keeps_buffers(self):
        from appGUI.VisPyVisuals import ShapeGroup
        collection = _new_collection()
        big, small = ShapeGroup(collection), ShapeGroup(collection)
        _add_squares(big, 3)
        _add_squares(small, 2, start=10)
        collection.redraw()
        buffers = {k: collection.data[k]['mesh_vertices'] for k in big._indexes}
        big.move_to_top()
        self.assertEqual(list(collection.data), small._indexes + big._indexes)
        for k, v in buffers.items():
            self.assertIs(collection.data[k]['mesh_vertices'], v)


class TestShowPlottedShapes(unittest.TestCase):

    def test_after_full_plot(self):
        from appGUI.VisPyVisuals import ShapeGroup
        from appObjects.AppObjectTemplate import FlatCAMObj

        collection = _new_collection()
        obj = SimpleNamespace(app=SimpleNamespace(), shapes=ShapeGroup(collection), _full_plot_sig=None)
        for name in ('_shapes_signature', '_has_full_plot', 'mark_full_plot', 'show_plotted_shapes', 'bring_to_top'):
            setattr(obj, name, getattr(FlatCAMObj, name).__get__(obj))
        _add_squares(obj.shapes, 4)
        obj.shapes.redraw()

        def flags():
            return [collection.data[k]['visible'] for k in obj.shapes._indexes]

        buffers = {k: collection.data[k]['mesh_vertices'] for k in obj.shapes._indexes}
        obj.mark_full_plot()
        self.assertTrue(obj.show_plotted_shapes(False))
        self.assertEqual(flags(), [False] * 4)
        self.assertTrue(obj.show_plotted_shapes(True))
        self.assertEqual(flags(), [True] * 4)
        for key, vertices in buffers.items():
            self.assertIs(collection.data[key]['mesh_vertices'], vertices)


class TestHiddenObjectAnnotation(unittest.TestCase):

    def test_hiding_an_object_hides_the_first_text_group_of_its_collection(self):
        # TextGroup.visible tested 'if self._index:', so the text group with key 0 (each CNCJob has its own text
        # collection) kept its texts: the path numbers of a CNCJob that a plugin hides (Levelling hides its source)
        from appGUI.VisPyVisuals import ShapeGroup, TextCollectionVisual, TextGroup
        from appObjects.AppObjectTemplate import FlatCAMObj
        texts = TextCollectionVisual()
        annotation = TextGroup(texts)
        annotation.set(text=['1', '2'], pos=[(0, 0), (1, 1)])
        app = SimpleNamespace(log=Mock(), worker_task=SimpleNamespace(emit=lambda job: job['fcn'](*job['params'])))
        obj = SimpleNamespace(app=app, shapes=ShapeGroup(_new_collection()), annotation=annotation)

        FlatCAMObj.visible.fset(obj, False)
        self.assertFalse(texts.data[0]['visible'])
        self.assertFalse(texts.text)
        FlatCAMObj.visible.fset(obj, True)
        self.assertTrue(texts.data[0]['visible'])
        self.assertEqual(list(texts.text), ['1', '2'])


if __name__ == '__main__':
    unittest.main()
