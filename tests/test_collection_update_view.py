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
    item = TreeItem([name], col.icons[kind], obj=obj, parent_item=group)
    col.endInsertRows()
    return obj, item


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
        view.selectionModel().select(idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
        persistent = QtCore.QPersistentModelIndex(idx)

        self.col.update_view(self.g2)
        self.col.update_view()

        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.row(), 1)
        self.assertIs(QtCore.QModelIndex(persistent).internalPointer(), self.g2_item)
        selected = view.selectionModel().selectedIndexes()
        self.assertEqual([s.internalPointer() for s in selected], [self.g2_item])



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

if __name__ == "__main__":
    unittest.main()
