"""Geometry Editor: shape deletion and full replot."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtCore, QtWidgets
from shapely.geometry import LineString, Polygon, box

from appEditors.appGeoEditor import AppGeoEditor, DrawToolShape


class FakeCollection:
    """Records clears, plotted shapes and redraws like a ShapeCollection."""

    def __init__(self):
        self.calls = []
        self.data = {}
        self.hidden = set()
        self.last_key = -1

    @property
    def items(self):
        """The shown shapes."""
        return [shape for key, shape in self.data.items() if key not in self.hidden]

    def clear(self, update=False):
        self.calls.append(('clear', update))
        self.data = {}
        self.hidden = set()
        self.last_key = -1

    def _new_key(self, shape):
        self.last_key += 1
        self.data[self.last_key] = shape
        return self.last_key

    def add(self, shape=None, color=None, **kwargs):
        self.calls.append(('add', color))
        return self._new_key(shape)

    def add_copy(self, source, source_key, color=None, face_color=None, linewidth=None):
        if source_key not in source.data:
            return None
        self.calls.append(('copy', color))
        return self._new_key(source.data[source_key])

    def remove(self, key, update=False):
        self.calls.append(('remove', key))
        self.data.pop(key, None)
        self.hidden.discard(key)

    def update_visibility(self, state, indexes=None):
        self.calls.append(('visible', state, len(indexes)))
        if state:
            self.hidden -= set(indexes)
        else:
            self.hidden |= set(indexes)

    def redraw(self, *args, **kwargs):
        self.calls.append(('redraw',))


def make_editor(shapes, selected=(), utility=()):
    storage = AppGeoEditor.make_storage()
    for shp in shapes:
        storage.insert(shp)
    app = SimpleNamespace(
        options={'global_draw_color': '#112233FF', 'global_sel_draw_color': '#AABBCCFF', 'global_theme': 'default'},
        app_units='MM', defaults=SimpleNamespace(get=lambda k, d=None: d, factory_defaults={}), log=Mock())
    editor = SimpleNamespace(
        app=app, storage=storage, selected=list(selected), utility=list(utility),
        shapes=FakeCollection(), sel_shapes=FakeCollection(),
        fcgeometry=SimpleNamespace(drawing_tolerance=0.01), build_ui=Mock(), plot_all=Mock(), plot_changes=Mock())
    editor.get_selected = lambda: editor.selected
    editor.get_draw_color = lambda: AppGeoEditor.get_draw_color(editor)
    editor.color_with_alpha = AppGeoEditor.color_with_alpha
    editor.get_sel_color = lambda: AppGeoEditor.get_sel_color(editor)
    editor.plot_shape = lambda *a, **k: AppGeoEditor.plot_shape(editor, *a, **k)
    editor.delete_shape = lambda s, inserted=(): AppGeoEditor.delete_shape(editor, s, inserted)
    editor.get_render_state = lambda: AppGeoEditor.get_render_state(editor)
    editor.copy_to_selection = lambda entry, color: AppGeoEditor.copy_to_selection(editor, entry, color)
    editor.unplot_entry = lambda entry: AppGeoEditor.unplot_entry(editor, entry)
    editor.submit_worker = lambda fcn, params: AppGeoEditor.submit_worker(editor, fcn, params)
    return editor


def line(x):
    return DrawToolShape(LineString([(x, 0), (x, 1)]))


class TestGeoEditorDelete(unittest.TestCase):
    def test_delete_selected_removes_every_selected_shape(self):
        a, b, c, d = line(0), line(1), line(2), line(3)
        editor = make_editor([a, b, c, d], selected=[a, b, c], utility=[b])
        sel_list = editor.selected
        editor.sel_shapes.clear = Mock()
        editor.sel_shapes.redraw = Mock()
        AppGeoEditor.delete_selected(editor)
        self.assertEqual(list(editor.storage.get_objects()), [d])
        self.assertEqual(editor.selected, [])
        self.assertEqual(editor.utility, [])
        self.assertIs(editor.selected, sel_list)


def selection_editor(shapes, selected=()):
    editor = make_editor(shapes, selected=selected)
    editor.plot_all = lambda: AppGeoEditor.plot_all(editor)
    editor.plot_changes = lambda: AppGeoEditor.plot_changes(editor)
    editor.plot_all()
    editor.shapes.calls.clear()
    editor.sel_shapes.calls.clear()
    return editor


class TestGeoEditorPlotSelection(unittest.TestCase):
    """plot_changes() changes only the shapes whose selection changed, without a new triangulation."""


def tree_editor(shapes):
    """An editor with a real tree, rows like build_ui() makes them: column 0 is str(id(shape))."""
    editor = selection_editor(shapes)
    editor.ui = Mock()
    tw = QtWidgets.QTreeWidget()
    tw.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
    parent = QtWidgets.QTreeWidgetItem(tw, ['Geometry'])
    for shp in shapes:
        QtWidgets.QTreeWidgetItem(parent, [str(id(shp))])
    editor.ui.tw = tw
    editor.tree_handler_runs = 0

    def on_tree_selection():
        editor.tree_handler_runs += 1
        AppGeoEditor.on_tree_selection(editor)

    editor.on_tree_selection = on_tree_selection
    editor.active_tool = None
    tw.itemSelectionChanged.connect(editor.on_tree_selection)
    editor.shapes_by_tree_id = lambda: AppGeoEditor.shapes_by_tree_id(editor)
    editor.sync_tree_selection = lambda: AppGeoEditor.sync_tree_selection(editor)
    editor.update_all_vertex_count = lambda: AppGeoEditor.update_all_vertex_count(editor)
    editor.app.delete_selection_shape = Mock()
    editor.app.options['global_mselect_key'] = 'Control'
    return editor


def tree_selected(editor):
    return sorted(item.text(0) for item in editor.ui.tw.selectedItems())


class TestGeoEditorSelectionTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


    def test_area_selection_with_the_key_toggles(self):
        shapes = [line(x) for x in range(6)]
        editor = tree_editor(shapes)
        editor.selected = [shapes[0], shapes[1]]
        with unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                                        return_value=QtCore.Qt.KeyboardModifier.ControlModifier):
            AppGeoEditor.draw_selection_area_handler(editor, (0.5, -0.5), (2.5, 2), True)
        self.assertEqual(editor.selected, [shapes[0], shapes[2]])


def pick_editor(shapes):
    """A tree editor with the click selection: 1 screen pixel is 0.01 plot units, so the pick tolerance is 0.05."""
    editor = tree_editor(shapes)
    editor.canvas = SimpleNamespace(pixel_size=lambda: 0.01)
    editor.interdict_selection = False
    editor.update_ui = Mock()
    editor.app.inform = Mock()
    editor.get_pick_index = lambda: AppGeoEditor.get_pick_index(editor)
    editor.pick_at = lambda point: AppGeoEditor.pick_at(editor, point)
    editor.click_select = lambda point: AppGeoEditor.click_select(editor, point)
    return editor


CONTROL = QtCore.Qt.KeyboardModifier.ControlModifier


class TestGeoEditorClickSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        # click_select() reads the keyboard state of the application; a Shift or Ctrl state left by an earlier test
        # would extend the selection. A test that needs a key patches it again inside.
        patcher = unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers',
                                             return_value=QtCore.Qt.KeyboardModifier.NoModifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_click_on_a_line_selects_it_and_the_row(self):
        shapes = [line(x) for x in range(4)]
        editor = pick_editor(shapes)
        # the middle of the line: far from its vertices, on the line
        self.assertTrue(editor.click_select((1.02, 0.5)))
        self.assertEqual(editor.selected, [shapes[1]])
        self.assertEqual(tree_selected(editor), [str(id(shapes[1]))])


    def test_click_with_the_key_adds_and_removes(self):
        shapes = [line(x) for x in range(4)]
        editor = pick_editor(shapes)
        with unittest.mock.patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=CONTROL):
            editor.click_select((1.0, 0.5))
            editor.click_select((3.0, 0.5))
            self.assertEqual(editor.selected, [shapes[1], shapes[3]])
            editor.click_select((1.0, 0.5))
            self.assertEqual(editor.selected, [shapes[3]])

    def test_click_inside_of_nested_polygons_picks_the_smaller(self):
        big = DrawToolShape(box(0, 0, 10, 10))
        small = DrawToolShape(box(4, 4, 6, 6))
        editor = pick_editor([big, small])
        editor.click_select((5, 5))
        self.assertEqual(editor.selected, [small])
        editor.click_select((1, 1))
        self.assertEqual(editor.selected, [big])


class TestGeoEditorSimplify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def simplify_tool(self, editor):
        from appEditors.appGeoEditor import FCSimplification
        tool = FCSimplification.__new__(FCSimplification)
        tool.draw_app = editor
        tool.simp_tool = Mock()
        return tool


    def test_simplify_replaces_only_the_selected_shapes(self):
        from appEditors.geo_plugins.GeoSimplificationPlugin import SimplificationTool
        from shapely.geometry import Point
        circle = DrawToolShape(Point(5, 5).buffer(2, quad_segs=32))
        others = [line(x) for x in range(3)]
        editor = selection_editor(others + [circle], selected=[circle])
        editor.interdict_selection = False
        editor._collect_shape = lambda shape, to_insert: AppGeoEditor._collect_shape(editor, shape, to_insert)
        editor.build_ui_sig = Mock()
        jobs = []
        editor.app.worker_task = SimpleNamespace(emit=jobs.append)
        editor.app.proc_container = unittest.mock.MagicMock()
        editor.app.inform = Mock()
        tool = SimpleNamespace(app=editor.app, geo_editor=editor, update_ui=Mock(),
                               ui=SimpleNamespace(geo_tol_entry=SimpleNamespace(get_value=lambda: 0.1)))
        tool.calculate_coordinates_vertex = lambda geos: SimplificationTool.calculate_coordinates_vertex(tool, geos)

        SimplificationTool.on_simplification_click(tool)
        jobs[0]['fcn'](*jobs[0]['params'])

        objects = list(editor.storage.get_objects())
        self.assertEqual(len(objects), 4)
        self.assertEqual(objects[:3], others)
        new = objects[3]
        self.assertIsNot(new, circle)
        self.assertLess(len(new.geo.exterior.coords), len(circle.geo.exterior.coords))
        self.assertEqual(editor.selected, [new])
        self.assertEqual(editor.sel_shapes.items, [new.geo])
        self.assertFalse(editor.interdict_selection)


if __name__ == '__main__':
    unittest.main()
