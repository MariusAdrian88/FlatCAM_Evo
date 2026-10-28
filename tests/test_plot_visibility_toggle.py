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

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeGroup
        self.collection = _new_collection()
        self.group = ShapeGroup(self.collection)
        _add_squares(self.group, 3)
        self.group.redraw()
        # layer 1 is the default layer of the shapes
        self.line = self.collection._lines[1]
        self.assertTrue(self.line.visible)

    def test_visible_setter_updates_canvas(self):
        self.group.visible = False
        self.assertFalse(self.line.visible)
        self.assertTrue(all(not d['visible'] for d in self.collection.data.values()))

        self.group.visible = True
        self.assertTrue(self.line.visible)

    def test_update_visibility_with_indexes(self):
        keys = list(self.group._indexes)
        self.group.update_visibility(False, indexes=keys[:2])
        self.assertEqual([self.collection.data[k]['visible'] for k in keys], [False, False, True])
        self.assertTrue(self.line.visible)

        self.group.update_visibility(False, indexes=keys[2:])
        self.assertFalse(self.line.visible)


class _CountedKeys(list):
    """The key list of a group that counts the 'key in list' tests."""
    lookups = 0

    def __contains__(self, key):
        self.lookups += 1
        return list.__contains__(self, key)


class TestSharedCollectionKeys(unittest.TestCase):

    def test_recolor_and_visibility_look_up_the_group_keys_in_a_set(self):
        # update_color() and update_visibility() of the collection tested 'key in list' with the key list of the
        # group, for each shape of the collection that all the objects share: a recolor (Project menu > Color) of
        # a 20000-shape Gerber in a 60000-shape project took 10 s
        from appGUI.VisPyVisuals import ShapeGroup
        collection = _new_collection()
        other, group = ShapeGroup(collection), ShapeGroup(collection)
        _add_squares(other, 3)
        _add_squares(group, 2, start=10)
        collection.redraw()
        group._indexes = _CountedKeys(group._indexes)

        group.redraw(update_colors=('#0000FFFF', '#000088FF'))
        self.assertEqual([collection.data[k]['face_color'] for k in group._indexes], ['#0000FFFF'] * 2)
        self.assertEqual([collection.data[k]['face_color'] for k in other._indexes], ['#00FF00FF'] * 3)
        collection.update_visibility(False, group._indexes)
        self.assertEqual([collection.data[k]['visible'] for k in other._indexes + group._indexes],
                         [True] * 3 + [False] * 2)
        self.assertEqual(group._indexes.lookups, 0)


class TestMoveToTop(unittest.TestCase):

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeGroup
        self.collection = _new_collection()
        self.big = ShapeGroup(self.collection)
        self.small = ShapeGroup(self.collection)
        _add_squares(self.big, 3)
        _add_squares(self.small, 2, start=10)
        self.collection.redraw()

    def test_moves_group_to_end_and_keeps_buffers(self):
        buffers = {k: self.collection.data[k]['mesh_vertices'] for k in self.big._indexes}
        self.big.move_to_top()
        self.assertEqual(list(self.collection.data), self.small._indexes + self.big._indexes)
        for k, v in buffers.items():
            self.assertIs(self.collection.data[k]['mesh_vertices'], v)

    def test_foreground_overrides_layers_and_restores_previous_group(self):
        from appGUI.VisPyVisuals import ShapeGroup
        paint = ShapeGroup(self.collection)
        paint.add(shape=_square(0), color='blue', face_color='blue', layer=2)
        self.big.move_to_top()
        self.assertIs(self.collection._subvisuals[-2], self.collection._top_mesh)
        self.assertIs(self.collection._subvisuals[-1], self.collection._top_line)
        self.assertEqual(len(self.collection._top_mesh.mesh_data.get_vertices()), 12)
        paint.move_to_top()
        self.assertEqual(len(self.collection._top_mesh.mesh_data.get_vertices()), 4)
        self.assertEqual(len(self.collection._meshes[1].mesh_data.get_vertices()), 20)
        self.assertTrue(all(self.collection.data[k]['layer'] == 1 for k in self.big._indexes))
        paint.visible = False
        self.assertFalse(self.collection._top_line.visible)
        self.assertFalse(self.collection._top_mesh.visible)

    def test_clear_does_not_promote_reused_keys(self):
        self.big.move_to_top()
        self.collection.clear()
        _add_squares(self.small, 1)
        self.collection.redraw()
        self.assertFalse(self.collection._top_mesh.visible)
        self.assertTrue(self.collection._meshes[1].visible)


class TestSelectionRedraw(unittest.TestCase):

    def test_visible_object_types_are_brought_to_top(self):
        from appHandlers.appPlotManager import AppPlotManager
        for kind in ('gerber', 'geometry', 'cncjob', 'excellon'):
            with self.subTest(kind=kind):
                obj = SimpleNamespace(kind=kind, obj_options={'plot': True},
                                      bring_to_top=Mock(return_value=True), plot=Mock())
                manager = SimpleNamespace(collection=SimpleNamespace(
                    get_selected=lambda: [obj], get_active=lambda: obj), worker_task=Mock())
                AppPlotManager.gerber_redraw(manager)
                manager.worker_task.emit.assert_called_once()
                task = manager.worker_task.emit.call_args[0][0]
                task['fcn'](*task['params'])
                obj.bring_to_top.assert_called_once()
                obj.plot.assert_not_called()

    def test_cncjob_selection_rebuild_preserves_the_plot_filter(self):
        from appHandlers.appPlotManager import AppPlotManager
        for kind in ('all', 'cut', 'travel'):
            with self.subTest(kind=kind):
                obj = SimpleNamespace(kind='cncjob', obj_options={'plot': True},
                                      ui=SimpleNamespace(cncplot_method_combo=Mock(
                                          get_value=Mock(return_value=kind))),
                                      bring_to_top=Mock(side_effect=[False, True]), plot=Mock())
                manager = SimpleNamespace(collection=SimpleNamespace(
                    get_selected=lambda: [obj], get_active=lambda: obj), worker_task=Mock())
                AppPlotManager.gerber_redraw(manager)
                task = manager.worker_task.emit.call_args[0][0]
                task['fcn'](*task['params'])
                obj.plot.assert_called_once_with(visible=True, kind=kind)
                self.assertEqual(obj.bring_to_top.call_count, 2)


class TestShowPlottedShapes(unittest.TestCase):

    def setUp(self):
        from appGUI.VisPyVisuals import ShapeGroup
        from appObjects.AppObjectTemplate import FlatCAMObj

        self.collection = _new_collection()
        self.obj = SimpleNamespace(app=SimpleNamespace(),
                                   shapes=ShapeGroup(self.collection),
                                   _full_plot_sig=None)
        for name in ('_shapes_signature', '_has_full_plot', 'mark_full_plot', 'show_plotted_shapes', 'bring_to_top'):
            setattr(self.obj, name, getattr(FlatCAMObj, name).__get__(self.obj))

        _add_squares(self.obj.shapes, 4)
        self.obj.shapes.redraw()

    def _visible_flags(self):
        return [self.collection.data[k]['visible'] for k in self.obj.shapes._indexes]

    def test_after_full_plot(self):
        buffers = {k: self.collection.data[k]['mesh_vertices'] for k in self.obj.shapes._indexes}
        self.obj.mark_full_plot()
        self.assertTrue(self.obj.show_plotted_shapes(False))
        self.assertEqual(self._visible_flags(), [False] * 4)
        self.assertTrue(self.obj.show_plotted_shapes(True))
        self.assertEqual(self._visible_flags(), [True] * 4)
        for key, vertices in buffers.items():
            self.assertIs(self.collection.data[key]['mesh_vertices'], vertices)

    def test_partial_plot_after_full_plot(self):
        # as Geometry and CNCJob do when a row of the tools table is changed
        self.obj.mark_full_plot()
        self.obj.shapes.clear()
        _add_squares(self.obj.shapes, 2)
        self.assertFalse(self.obj.show_plotted_shapes(False))


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
