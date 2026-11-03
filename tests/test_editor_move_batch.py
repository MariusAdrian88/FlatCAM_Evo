"""Editors: the Move (and the Gerber Copy) translate the selection in 1 batch and plot 1 time."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from shapely.geometry import LineString, Point, box

from appEditors.appGeoEditor import AppGeoEditor, FCMove, DrawToolShape as GeoShape
from appEditors.appGerberEditor import MoveEditorGrb, CopyEditorGrb
from appEditors.grb_plugins.GrbCommon import DrawToolShape as GrbShape


def counting_app():
    return SimpleNamespace(inform=Mock(), jump_signal=Mock(), proc_container=MagicMock())


def grb_tool(cls, storage_dict, selected, apertures, dx=2.0, dy=-1.0):
    tool = cls.__new__(cls)
    tool.origin = (0.0, 0.0)
    tool.destination = (dx, dy)
    tool.selected_apertures = apertures
    tool.draw_app = SimpleNamespace(
        storage_dict=storage_dict, selected=selected, get_selected=lambda: selected,
        touch_geometry=Mock(), delete_utility_geometry=Mock(), tool_shape=Mock(), plot_all=Mock(),
        build_ui=Mock(), app=counting_app())
    return tool


class TestGerberEditorMove(unittest.TestCase):

    def setUp(self):
        self.pad = GrbShape({'solid': box(0, 0, 1, 1), 'follow': Point(0.5, 0.5)})
        self.track = GrbShape({'solid': LineString([(0, 0), (3, 0)]).buffer(0.1),
                               'follow': LineString([(0, 0), (3, 0)]), 'clear': box(1, -0.05, 1.1, 0.05)})
        self.other = GrbShape({'solid': box(5, 5, 6, 6)})
        self.storage = {'10': {'geometry': [self.pad, self.other]}, '11': {'geometry': [self.track]}}

    def test_move_translates_every_key_and_removes_the_source(self):
        selected = [self.pad, self.track]
        tool = grb_tool(MoveEditorGrb, self.storage, selected, ['10', '11'])
        tool.make()

        ap10 = self.storage['10']['geometry']
        self.assertIs(ap10[0], self.other)
        self.assertEqual(ap10[1].geo['solid'].bounds, (2.0, -1.0, 3.0, 0.0))
        self.assertEqual(ap10[1].geo['follow'].coords[0], (2.5, -0.5))
        ap11 = self.storage['11']['geometry']
        self.assertEqual(len(ap11), 1)
        self.assertEqual(set(ap11[0].geo), {'solid', 'follow', 'clear'})
        self.assertEqual(ap11[0].geo['clear'].bounds, (3.0, -1.05, 3.1, -0.95))
        self.assertEqual(selected, [])
        tool.draw_app.plot_all.assert_called_once()
        tool.draw_app.touch_geometry.assert_called_once()


    def test_copy_keeps_the_source(self):
        selected = [self.pad]
        tool = grb_tool(CopyEditorGrb, self.storage, selected, ['10'])
        tool.make()
        ap10 = self.storage['10']['geometry']
        self.assertEqual(len(ap10), 3)
        self.assertIs(ap10[0], self.pad)
        self.assertEqual(ap10[2].geo['solid'].bounds, (2.0, -1.0, 3.0, 0.0))
        tool.draw_app.plot_all.assert_called_once()




class TestGeometryEditorMove(unittest.TestCase):

    def test_move_prepares_without_mutation_then_commits_once(self):
        a, b, c = GeoShape(box(0, 0, 1, 1)), GeoShape(LineString([(0, 0), (1, 1)])), GeoShape(box(5, 5, 6, 6))
        storage = AppGeoEditor.make_storage()
        for shp in (a, b, c):
            storage.insert(shp)
        editor = SimpleNamespace(storage=storage, selected=[a, b], utility=[b], plot_all=Mock(), build_ui=Mock(),
                                 sel_shapes=Mock(), app=counting_app())
        editor.get_selected = lambda: editor.selected
        editor.delete_shape = lambda shapes: AppGeoEditor.delete_shape(editor, shapes)

        tool = FCMove.__new__(FCMove)
        tool.draw_app = editor
        tool.origin, tool.destination = (0.0, 0.0), (3.0, 0.0)
        tool.draw_cursor_data = Mock()
        tool.make()

        self.assertEqual([shp.geo.bounds for shp in tool.geometry], [(3.0, 0.0, 4.0, 1.0), (3.0, 0.0, 4.0, 1.0)])
        self.assertEqual(list(storage.get_objects()), [a, b, c])
        self.assertEqual(editor.selected, [a, b])
        self.assertEqual(editor.utility, [b])
        editor.plot_all.assert_not_called()      # on_shape_complete() plots after the new shapes are added
        self.assertTrue(tool.complete)
        editor.active_tool = tool
        editor.app.options = {'geometry_editor_milling_type': 'cv'}
        editor.app.defaults = SimpleNamespace(get=lambda k, d=None: d, factory_defaults={})
        editor.app.log = Mock()
        editor.build_ui_sig = Mock()
        editor.tool_shape = Mock()
        editor.plot_changes = Mock()
        editor._collect_shape = lambda shp, acc: AppGeoEditor._collect_shape(editor, shp, acc)
        editor.delete_utility_geometry = lambda: AppGeoEditor.delete_utility_geometry(editor)
        AppGeoEditor.on_shape_complete(editor)
        self.assertEqual(set(storage.get_objects()), {c, *tool.geometry})
        self.assertEqual(editor.selected, tool.geometry)
        self.assertEqual(editor.utility, [])
        editor.plot_changes.assert_called_once()
        self.assertTrue(AppGeoEditor.undo(editor))
        self.assertEqual(set(storage.get_objects()), {a, b, c})
        self.assertEqual(editor.selected, [a, b])


class TestClimbMillingDirection(unittest.TestCase):
    """With the 'cl' milling type only a new drawn shape is reversed; a moved shape keeps its direction."""

    def complete(self, tool_cls):
        line = LineString([(0, 0), (1, 0), (2, 1)])
        tool = tool_cls.__new__(tool_cls)
        tool.geometry = [GeoShape(line)]
        app = SimpleNamespace(options={'geometry_editor_milling_type': 'cl'}, log=Mock(),
                              defaults=SimpleNamespace(get=lambda k, d=None: d, factory_defaults={}))
        editor = SimpleNamespace(app=app, active_tool=tool, add_shape=Mock(), delete_utility_geometry=Mock(),
                                 tool_shape=Mock(), plot_changes=Mock(), selected=[])
        AppGeoEditor.on_shape_complete(editor)
        return list(editor.add_shape.call_args[0][0][0].geo.coords)

    def test_drawn_shape_is_reversed(self):
        from appEditors.appGeoEditor import FCPath
        self.assertEqual(self.complete(FCPath), [(2, 1), (1, 0), (0, 0)])

    def test_moved_copied_and_exploded_shapes_keep_the_direction(self):
        from appEditors.appGeoEditor import FCCopy, FCExplode
        for tool_cls in (FCMove, FCCopy, FCExplode):
            self.assertEqual(self.complete(tool_cls), [(0, 0), (1, 0), (2, 1)], tool_cls.__name__)


if __name__ == '__main__':
    unittest.main()
