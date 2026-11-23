"""ObjectCollection.update_view refreshes rows with dataChanged and keeps selection and persistent indexes."""

import os
import unittest
import pytest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets
from PyQt6.QtTest import QSignalSpy

from appObjects.ObjectCollection import ObjectCollection, TreeItem
from libs import qdarktheme

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_collection():
    app = mock.MagicMock()
    app.resource_location = "."
    app.options = {
        "global_allow_edit_in_project_tab": False,
        "global_proj_item_color_dark": "#FFFFFFFF",
        "global_proj_item_dis_color_dark": "#808080FF",
        "global_proj_item_color_light": "#000000FF",
        "global_proj_item_dis_color_light": "#808080FF",
    }
    col = ObjectCollection(app)
    return col


def add_item(col, kind, name):
    """Add a stand-in object to a group the way the model does it (rows inserted with begin/end)."""
    group = col.group_items[kind]
    group_index = col.index(col.root_item.child_items.index(group), 0, QtCore.QModelIndex())
    row = group.child_count()
    obj = SimpleNamespace(kind=kind, obj_options={"name": name, "plot": True})
    col.beginInsertRows(group_index, row, row)
    item = TreeItem(None, col.icons[kind], obj=obj, parent_item=group)
    col.endInsertRows()
    return obj, item


def item_index(col, kind, item):
    group = col.group_items[kind]
    parent = col.index(col.root_item.child_items.index(group), 0, QtCore.QModelIndex())
    return col.index(item.row(), 0, parent)


class TestUpdateView(unittest.TestCase):
    def setUp(self):
        self.col = make_collection()
        self.g1, _ = add_item(self.col, "gerber", "g1")
        self.g2, self.g2_item = add_item(self.col, "gerber", "g2")
        self.g3, _ = add_item(self.col, "gerber", "g3")
        self.e1, _ = add_item(self.col, "excellon", "e1")
        self.spy = QSignalSpy(self.col.dataChanged)
        self.layout_spy = QSignalSpy(self.col.layoutChanged)

    def _ranges(self):
        res = []
        for args in self.spy:
            top, bottom = args[0], args[1]
            self.assertEqual(top.parent(), bottom.parent())
            res.append((top.parent().row(), top.row(), bottom.row(), top.internalPointer()))
        return res

    def test_selection_and_persistent_index_survive(self):
        view = self.col.view
        gerber_group = self.col.group_items["gerber"]
        parent = self.col.index(self.col.root_item.child_items.index(gerber_group), 0, QtCore.QModelIndex())
        idx = self.col.index(1, 0, parent)
        self.assertEqual(self.col.columnCount(idx), 1)
        view.selectionModel().select(idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
        persistent = QtCore.QPersistentModelIndex(idx)

        self.col.update_view(self.g2)
        self.col.update_view()

        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.row(), 1)
        self.assertIs(QtCore.QModelIndex(persistent).internalPointer(), self.g2_item)
        selected = view.selectionModel().selectedIndexes()
        self.assertEqual([s.internalPointer() for s in selected], [self.g2_item])

    def test_deselect_and_clear_refresh_the_properties_tab(self):
        parent = self.col.index(self.col.root_item.child_items.index(self.col.group_items["gerber"]),
                                0, QtCore.QModelIndex())
        first = self.col.index(0, 0, parent)
        second = self.col.index(1, 0, parent)
        model = self.col.view.selectionModel()
        model.select(first, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
        model.select(second, QtCore.QItemSelectionModel.SelectionFlag.Select)
        self.col.app.on_notebook_tab_changed.reset_mock()
        self.col.app.setup_default_properties_tab.reset_mock()
        self.col.app.ui.properties_scroll_area.takeWidget.reset_mock()

        # a row leaves a multiple selection: the remaining active object gets the Properties tab
        model.select(second, QtCore.QItemSelectionModel.SelectionFlag.Deselect)
        self.assertEqual([i.internalPointer().obj for i in model.selectedIndexes()], [self.g1])
        self.col.app.on_notebook_tab_changed.assert_called_once()

        # no row is selected: the default Properties tab comes back
        model.clearSelection()
        self.col.app.setup_default_properties_tab.assert_called_once()
        self.col.app.ui.properties_scroll_area.takeWidget.assert_called_once()


class TestRenameMarksProjectChanged(unittest.TestCase):
    def setUp(self):
        from appHandlers.appHistory import HistoryManager
        self.col = make_collection()
        self.obj, self.item = add_item(self.col, "gerber", "old")
        self.obj.history_uid = "g1"
        self.index = item_index(self.col, "gerber", self.item)
        self.col.app.headless = False
        self.col.app.should_we_save = False
        self.history = HistoryManager(self.col.app, self.col)
        self.col.app.history = self.history

    def test_set_data_rename_marks_changed_and_records_the_step(self):
        self.assertTrue(self.col.setData(self.index, "new", QtCore.Qt.ItemDataRole.EditRole))
        self.assertEqual(self.obj.obj_options["name"], "new")
        self.assertEqual(len(self.history.undo_stack), 1)
        self.assertTrue(self.col.app.should_we_save)

    def test_rejected_renames_keep_the_project_clean(self):
        self.assertTrue(self.col.setData(self.index, "old", QtCore.Qt.ItemDataRole.EditRole))
        self.assertTrue(self.col.setData(self.index, "", QtCore.Qt.ItemDataRole.EditRole))
        self.assertEqual(self.obj.obj_options["name"], "old")
        self.assertEqual(len(self.history.undo_stack), 0)
        self.assertFalse(self.col.app.should_we_save)

    def test_rename_object_marks_changed_without_a_history_step(self):
        self.assertEqual(self.col.rename_object(self.obj, "new"), "new")
        self.assertEqual(len(self.history.undo_stack), 0)
        self.assertTrue(self.col.app.should_we_save)

        self.col.app.should_we_save = False
        self.assertEqual(self.col.rename_object(self.obj, ""), "new")
        self.assertFalse(self.col.app.should_we_save)


@pytest.mark.parametrize("theme,active,plot,swatch", [
    ("light", True, True, True), ("light", False, False, False),
    ("dark", True, False, True), ("dark", False, True, False)])
def test_selected_project_text_uses_preferences(theme, active, plot, swatch):
    col = make_collection()
    view = col.view
    view.setStyleSheet(qdarktheme.load_stylesheet(theme))
    obj, _ = add_item(col, "gerber", "MMMMMMMM")
    obj.obj_options["plot"] = plot
    if swatch:
        obj.fill_color = "#FFFF00FF"
    col.app.options["global_proj_item_color_" + theme] = "#E020A0FF"
    col.app.options["global_proj_item_dis_color_" + theme] = "#20C060FF"
    group = col.index(col.root_item.child_items.index(col.group_items["gerber"]), 0, QtCore.QModelIndex())
    index = col.index(0, 0, group)
    settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
    previous = settings.value("theme")
    settings.setValue("theme", theme)
    try:
        option = QtWidgets.QStyleOptionViewItem()
        option.initFrom(view)
        option.widget = view
        option.rect = QtCore.QRect(0, 0, 240, 32)
        option.state |= QtWidgets.QStyle.StateFlag.State_Selected | QtWidgets.QStyle.StateFlag.State_Enabled
        if active:
            option.state |= QtWidgets.QStyle.StateFlag.State_Active
        else:
            option.state &= ~QtWidgets.QStyle.StateFlag.State_Active
        image = QtGui.QImage(240, 32, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(image)
        view.itemDelegate().paint(painter, option, index)
        painter.end()
        expected = QtGui.QColor("#E020A0" if plot else "#20C060")
        assert any(image.pixelColor(x, y) == expected for x in range(20, 180) for y in range(32))
        background = ("#4CA6E5" if active else "#E4E6F2") if theme == "light" else (
            "#004875" if active else "#393D41")
        assert image.pixelColor(190, 16) == QtGui.QColor(background)
        if swatch:
            rect = view.swatch_rect(index, option.rect)
            assert image.pixelColor(rect.center()) == QtGui.QColor("yellow")
        assert col.data(group, QtCore.Qt.ItemDataRole.ForegroundRole) is None
    finally:
        if previous is None:
            settings.remove("theme")
        else:
            settings.setValue("theme", previous)
        view.deleteLater()


class TestDragReorder(unittest.TestCase):
    COLORS = [("#o0", "#f0000000", "L0"), ("#o1", "#f1000011", "L1"), ("#o2", "#f2000022", "L2")]

    def setUp(self):
        self.col = make_collection()
        self.col.app.options["gerber_store_color_list"] = True
        self.col.app.options["gerber_color_list"] = list(self.COLORS)
        self.g = []
        for i, color in enumerate(self.COLORS):
            obj, _item = add_item(self.col, "gerber", "g%d" % i)
            obj.outline_color, obj.fill_color = color[0], color[1]
            obj.alpha_level = "00"
            obj.shapes = mock.MagicMock()
            self.g.append(obj)
        self.e1, _item = add_item(self.col, "excellon", "e1")

    def _group_index(self, kind):
        group = self.col.group_items[kind]
        return self.col.index(self.col.root_item.child_items.index(group), 0, QtCore.QModelIndex())

    def _mime(self, *rows):
        group_index = self._group_index("gerber")
        return self.col.mimeData([self.col.index(r, 0, group_index) for r in rows])

    def _gerber_order(self):
        return [o for o in self.col.get_list() if o.kind == "gerber"]

    def test_moved_gerbers_take_the_colors_of_their_new_rows(self):
        data = self._mime(2)
        self.col.dropMimeData(data, QtCore.Qt.DropAction.MoveAction, 0, -1, self._group_index("gerber"))

        self.assertEqual(self._gerber_order(), [self.g[2], self.g[0], self.g[1]])
        for row, obj in enumerate(self._gerber_order()):
            self.assertEqual((obj.outline_color, obj.fill_color), self.COLORS[row][:2])
        self.assertEqual(self.col.app.options["gerber_color_list"], self.COLORS)
        self.assertEqual(self.g[1].alpha_level, "22")
        self.g[2].shapes.redraw.assert_called_once_with(update_colors=("#f0000000", "#o0"))

    def test_drop_in_another_category_is_refused(self):
        data = self._mime(0)
        excellon = self._group_index("excellon")
        self.assertFalse(self.col.canDropMimeData(data, QtCore.Qt.DropAction.MoveAction, -1, -1, excellon))
        self.assertFalse(self.col.dropMimeData(data, QtCore.Qt.DropAction.MoveAction, -1, -1, excellon))
        self.assertEqual(self._gerber_order(), self.g)
        self.assertEqual(self.col.group_items["excellon"].child_count(), 1)

        # a selection that spans two categories cannot be dragged
        excellon_row = self.col.index(0, 0, excellon)
        self.assertIsNone(self.col.mimeData([self.col.index(0, 0, self._group_index("gerber")), excellon_row]))


def _make_stub(plugin_name, active_obj):
    """Minimal stub mimicking plugin structure for handler testing."""
    from unittest.mock import MagicMock

    stub = SimpleNamespace()
    stub.app = MagicMock()
    stub.app.collection.get_active.return_value = active_obj
    stub.app.ui.notebook.count.return_value = 1
    stub.app.ui.notebook.tabText.return_value = plugin_name

    stub.ui = MagicMock()
    stub.ui.pluginName = plugin_name

    # Mock all possible picker widgets
    for attr in ['object_combo', 'obj_combo', 'tf_object_combo', 'tf_box_combo',
                 'type_obj_radio', 'type_obj_combo', 'target_radio',
                 'tf_type_obj_combo', 'tf_type_box_combo',
                 'object_type_combo', 'box_type_radio', 'box_combo',
                 'insert_type_radio', 'obj_insert_combo']:
        setattr(stub.ui, attr, MagicMock())

    return stub


def _empty_qt_selection():
    """Simulate Qt's empty 'current' for deselection-only events."""
    from unittest.mock import MagicMock
    empty = MagicMock()
    empty.indexes.return_value = []  # This is what causes IndexError in old code
    return empty


@pytest.mark.parametrize("handler_module,handler_class,plugin_name,kind,expected_pickers", [
    ("appPlugins.ToolDrilling.Drilling", "ToolDrilling", "Drill", "excellon",
     [("object_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolMilling.Milling", "ToolMilling", "Mill", "geometry",
     [("target_radio", "set_value", "geo"), ("object_combo", "set_value", "test_obj")]),
    ("appPlugins.Common.clearing.controller_base", "ClearingToolBase", "Clearing", "gerber",
     [("type_obj_radio", "set_value", "gerber"), ("obj_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolCutOut.CutOut", "CutOut", "CutOut", "geometry",
     [("type_obj_radio", "set_value", "geo"), ("obj_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolFilm.Film", "Film", "Film", "gerber",
     [("tf_type_obj_combo", "set_value", "grb"), ("tf_type_box_combo", "set_value", "grb"),
      ("tf_object_combo", "set_value", "test_obj"), ("tf_box_combo", "set_value", "test_obj")]),
    ("appPlugins.Common.levelling_core", "LevellingCoreMixin", "Levelling", "cncjob",
     [("object_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolPanelize.Panelize", "Panelize", "Panelize", "excellon",
     [("type_obj_combo", "set_value", "Excellon"), ("type_box_combo", "set_value", "Excellon"),
      ("object_combo", "set_value", "test_obj"), ("box_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolDblSided.DblSided", "DblSidedTool", "DblSided", "gerber",
     [("object_type_combo", "set_value", 0), ("box_type_radio", "set_value", "grb"),
      ("object_combo", "set_value", "test_obj")]),
    ("appPlugins.ToolMarkers.Markers", "ToolMarkers", "Markers", "geometry",
     [("insert_type_radio", "set_value", "geo"), ("obj_insert_combo", "set_value", "test_obj")]),
])
def test_plugin_handler_uses_get_active_not_current(handler_module, handler_class,
                                                     plugin_name, kind, expected_pickers):
    """Plugin handlers must use collection.get_active() not current.indexes()[0].

    Scenario: select A, add B, deselect A. Qt delivers empty 'current' for deselection-only.
    Handlers must read the actual selection via get_active() and update pickers accordingly.
    """
    from unittest.mock import MagicMock
    import importlib
    import inspect

    mod = importlib.import_module(handler_module)
    HandlerClass = getattr(mod, handler_class)

    # Build test object
    test_obj = SimpleNamespace(
        kind=kind,
        obj_options={'name': 'test_obj'}
    )

    # Create stub and call handler
    stub = _make_stub(plugin_name, test_obj)
    empty_current = _empty_qt_selection()
    previous = MagicMock()

    # Call the unbound handler method on the stub
    handler = getattr(HandlerClass, 'on_object_selection_changed')

    # Check signature: ClearingToolBase only takes (self, current), others take (self, current, previous)
    sig = inspect.signature(handler)
    if len(sig.parameters) == 2:  # self, current
        handler(stub, empty_current)
    else:  # self, current, previous
        handler(stub, empty_current, previous)

    # Verify pickers were set correctly
    for picker_attr, method, expected_value in expected_pickers:
        picker = getattr(stub.ui, picker_attr)
        getattr(picker, method).assert_called_with(expected_value)


if __name__ == "__main__":
    unittest.main()
