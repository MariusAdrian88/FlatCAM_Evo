# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

# ##########################################################
# File modified by: Dennis Hayrullin                       #
# File modified by: Marius Stanciu                         #
# ##########################################################

from appHandlers.appHistory import history_of
from PyQt6 import QtGui, QtCore, QtWidgets
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QColor
# from PyQt6.QtCore import QModelIndex

from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ObjectDispatch import dispatch_attr
from appObjects.ProjectContext import unique_name
from appObjects.ScriptObject import ScriptObject

import sys
import json
import uuid

import re
import logging
from copy import deepcopy
try:
    from numpy import Inf
except ImportError:
    from numpy import inf as Inf

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class EventSensitiveListView(QtWidgets.QTreeView):
    """
    QtGui.QListView extended to emit a signal on key press.
    """

    def __init__(self, app, parent=None):
        super(EventSensitiveListView, self).__init__(parent)
        self.setHeaderHidden(True)

        # no expand arrow before the category rows: a double click expands or collapses a category
        self.setRootIsDecorated(False)
        self.setEditTriggers(QtWidgets.QTreeView.EditTrigger.NoEditTriggers)    # No edit in the Project Tab Tree

        # Enable dragging and dropping onto the appGUI
        self.setAcceptDrops(True)
        self.filename = ""
        self.app = app

        # Enabling Drag and Drop for the items in the Project Tab
        # Example: https://github.com/d1vanov/PyQt6-reorderable-list-model/blob/master/reorderable_list_model.py
        # https://github.com/jimmykuu/PyQt-PySide-Cookbook/blob/master/tree/drop_indicator.md
        # self.setDragEnabled(True)
        # self.viewport().setAcceptDrops(True)
        # self.setDropIndicatorShown(True)
        # self.DragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        # self.DragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragDrop)

        # self.current_idx = None
        # self.current_group = None
        # self.dropped_obj = None

    keyPressed = QtCore.pyqtSignal(int)
    mouseReleased = QtCore.pyqtSignal(object)

    # def mouseMoveEvent(self, event):  # is called whenever the mouse moves while a mouse button is held down
    #     super().mouseMoveEvent(event)  # propagate
    #
    #     realfile = 'a.txt'
    #     fileurl = "file:///%s" % realfile  # build url
    #     url = QtCore.QUrl(fileurl)
    #     urllist = []
    #     urllist.append(url)  # only 1 file
    #
    #     drag = QtGui.QDrag(self)
    #
    #     md = QtCore.QMimeData()
    #     md.setUrls(urllist)
    #     drag.setMimeData(md)
    #     result = drag.exec(Qt.DropAction.CopyAction)
    #
    #     source = drag.source()
    #     print("source = %s" % source)
    #     target = drag.target()  # returns widget if inside the application, returns None if windows desktop ... etc.
    #     print("target = %s" % target)

    # Files dragged from the OS are opened by the main window; only the drag of the rows is handled here.
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.app.ui.dragEnterEvent(event)
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            self.app.ui.dragMoveEvent(event)
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.app.ui.dropEvent(event)
            return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            return
        self.keyPressed.emit(event.key())

    def swatch_rect(self, index, row_rect):
        """
        The rectangle of the color swatch of a row, at the right side of the visible part of the row.

        :param index:       the model index of the row
        :param row_rect:    the rectangle of the row, in viewport coordinates
        :return:            a QRect or None if the row has no swatch (groups, objects without plot colors)
        """
        if not index.isValid():
            return None
        obj = index.internalPointer().obj
        if obj is None or not dispatch_attr(obj, 'has_plot_colors') or not getattr(obj, 'fill_color', None):
            return None

        # a small font makes low rows; keep a gap between the swatches of adjacent rows
        size = min(ProjectItemDelegate.SWATCH_SIZE, row_rect.height() - 2)
        right = min(row_rect.right(), self.viewport().width() - 1) - ProjectItemDelegate.SWATCH_MARGIN
        top = row_rect.top() + (row_rect.height() - size) // 2
        return QtCore.QRect(right - size + 1, top, size, size)

    def _swatch_index_at(self, pos):
        index = self.indexAt(pos)
        rect = self.swatch_rect(index, self.visualRect(index))
        if rect is not None and rect.contains(pos):
            return index
        return None

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # a click on the color swatch of a selected row keeps the selection: the color goes to the whole selection
        if self._swatch_index_at(event.position().toPoint()) is not None:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._swatch_index_at(event.position().toPoint()) is not None:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        index = self._swatch_index_at(event.position().toPoint())
        if index is not None:
            event.accept()
            if event.button() == Qt.MouseButton.LeftButton:
                # the swatch of a row that is not selected makes its row the only selected row
                if not self.selectionModel().isSelected(index):
                    self.setCurrentIndex(index)
                self.on_swatch_clicked(index.internalPointer().obj)
            return
        self.mouseReleased.emit(event.button())
        super().mouseReleaseEvent(event)

    def on_swatch_clicked(self, obj):
        current_color = QtGui.QColor(obj.fill_color[:7])
        try:
            current_color.setAlpha(int(obj.fill_color[7:9], 16))
        except ValueError:
            current_color.setAlpha(255)

        color_dialog = QtWidgets.QColorDialog(self)
        color_dialog.setOption(QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        color_dialog.setCurrentColor(current_color)
        if color_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        selected_color = color_dialog.selectedColor()
        if not selected_color.isValid():
            return

        argb = selected_color.name(QtGui.QColor.NameFormat.HexArgb)
        new_color = ('#%s%s' % (argb[3:], argb[1:3])).upper()
        self.app.plot_manager.set_color_from_swatch(obj, new_color)


class ProjectItemDelegate(QtWidgets.QStyledItemDelegate):
    """
    Paints a color swatch at the right side of the rows of the objects that have plot colors.
    The swatch has the fill color of the object; a click on it is handled by the view.
    """

    SWATCH_SIZE = 12
    SWATCH_MARGIN = 6

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def paint(self, painter, option, index):
        rect = self.view.swatch_rect(index, option.rect)
        if rect is None:
            super().paint(painter, option, index)
            return

        # the background (selection, hover) goes on the whole row, the text stops before the swatch
        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
        style.drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        text_option = QtWidgets.QStyleOptionViewItem(option)
        text_option.rect = QtCore.QRect(option.rect)
        text_option.rect.setRight(rect.left() - self.SWATCH_MARGIN)
        super().paint(painter, text_option, index)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtGui.QPen(QColor('dimgray'), 1))
        painter.setBrush(QColor(index.internalPointer().obj.fill_color[:7]))
        painter.drawRoundedRect(QtCore.QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.restore()

    def dragEnterEvent(self, event):
        # if event.source():
        #     self.current_idx = self.currentIndex()
        #     self.current_group = self.model().group_items[self.current_idx.internalPointer().obj.kind]
        #     self.dropped_obj = self.current_idx.internalPointer().obj

        if event.mimeData().hasUrls:
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.setDropIndicatorShown(True)

        if event.mimeData().hasUrls:
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        drop_indicator = self.dropIndicatorPosition()

        # if event.source():
        #     new_index = self.indexAt(event.pos())
        #     new_group = self.model().group_items[new_index.internalPointer().obj.kind]
        #     if self.current_group == new_group:
        #
        #         # delete it from the model
        #         deleted_obj_name = self.dropped_obj.obj_options['name']
        #         self.model().delete_by_name(deleted_obj_name)
        #
        #         # add the object to the new index
        #         self.model().append(self.dropped_obj, to_index=new_index)
        #
        #         return

        m = event.mimeData()
        if m.hasUrls:
            event.accept()

            for url in m.urls():
                self.filename = str(url.toLocalFile())

                # file drop from outside application
                if drop_indicator == QtWidgets.QAbstractItemView.DropIndicatorPosition.OnItem:
                    if self.filename == "":
                        self.app.inform.emit(_("Cancelled."))
                    else:
                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.grb_list:
                            self.app.worker_task.emit({'fcn': self.app.f_handlers.open_gerber,
                                                       'params': [self.filename]})
                        else:
                            event.ignore()

                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.exc_list:
                            self.app.worker_task.emit({'fcn': self.app.f_handlers.open_excellon,
                                                       'params': [self.filename]})
                        else:
                            event.ignore()

                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.gcode_list:
                            self.app.worker_task.emit({'fcn': self.app.f_handlers.open_gcode,
                                                       'params': [self.filename]})
                        else:
                            event.ignore()

                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.svg_list:
                            object_type = 'geometry'
                            self.app.worker_task.emit({'fcn': self.app.f_handlers.import_svg,
                                                       'params': [self.filename, object_type, None]})

                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.dxf_list:
                            object_type = 'geometry'
                            self.app.worker_task.emit({'fcn': self.app.f_handlers.import_dxf,
                                                       'params': [self.filename, object_type, None]})

                        if self.filename.lower().rpartition('.')[-1] in self.app.regFK.prj_list:
                            # self.app.open_project() is not Thread Safe
                            self.app.f_handlers.open_project(self.filename)
                        else:
                            event.ignore()
                else:
                    pass
        else:
            event.ignore()


class TreeItem(EventSensitiveListView):
    """
    Item of a tree model
    """

    def __init__(self, data, icon=None, obj=None, parent_item=None):
        super(TreeItem, self).__init__(parent_item)
        self.parent_item = parent_item
        self.item_data = data  # Columns string data
        self.icon = icon  # Decoration
        self.obj = obj  # FlatCAMObj

        self.child_items = []

        if parent_item:
            parent_item.append_child(self)

    def append_child(self, item):
        self.child_items.append(item)
        item.set_parent_item(self)

    def remove_child(self, item):
        child = self.child_items.pop(self.child_items.index(item))
        child.obj.clear(True)
        child.obj.delete()
        del child.obj
        del child

    def remove_children(self):
        for child in self.child_items:
            child.obj.clear()
            child.obj.delete()
            del child.obj
            del child

        self.child_items = []

    def child(self, row):
        return self.child_items[row]

    def child_count(self):
        return len(self.child_items)

    def column_count(self):
        return len(self.item_data)

    def data(self, column):
        return self.item_data[column]

    def row(self):
        return self.parent_item.child_items.index(self)

    def set_parent_item(self, parent_item):
        self.parent_item = parent_item

    def __del__(self):
        try:
            del self.icon
        except AttributeError:
            pass


class ObjectCollection(QtCore.QAbstractItemModel):
    """
    Object storage and management.
    """

    groups = [
        ("gerber", _("Gerber")),
        ("excellon", _("Excellon")),
        ("geometry", _("Geometry")),
        ("cncjob", "CNC Job"),
        ("script", _("Script")),
        ("document", _("Document")),
    ]

    classdict = {
        "gerber": GerberObject,
        "excellon": ExcellonObject,
        "cncjob": CNCJobObject,
        "geometry": GeometryObject,
        "script": ScriptObject,
        "document": DocumentObject
    }

    icon_files = {
        "gerber": "assets/resources/gerber16_project.png",
        "excellon": "assets/resources/excellon16_project.png",
        "cncjob": "assets/resources/cnc16_project.png",
        "geometry": "assets/resources/geometry16_project.png",
        "script": "assets/resources/script16_project.png",
        "document": "assets/resources/notes16_project.png"
    }

    # mime type of the objects dragged inside the Project tab
    PROJECT_MIME = "application/x-flatcam-project-items"

    # will emit the name of the object that was just selected
    item_selected = QtCore.pyqtSignal(str)
    update_list_signal = QtCore.pyqtSignal()

    root_item = None
    # app = None

    def __init__(self, app, parent=None):

        QtCore.QAbstractItemModel.__init__(self)

        self.app = app

        # ## Icons for the list view
        self.icons = {}
        for kind in ObjectCollection.icon_files:
            self.icons[kind] = QtGui.QPixmap(
                ObjectCollection.icon_files[kind].replace('assets/resources', self.app.resource_location))

        # Create root tree view item
        self.root_item = TreeItem(["root"])

        # Create group items
        self.group_items = {}
        for kind, title in ObjectCollection.groups:
            item = TreeItem([title], self.icons[kind])
            self.group_items[kind] = item
            self.root_item.append_child(item)

        # Create test sub-items
        # for i in self.root_item.child_items:
        #     print(i.data(1))
        #     i.append_child(TreeItem(["empty"]))

        # ## Data # ##
        self.checked_indexes = []

        # Names of objects that are expected to become available.
        # For example, when the creation of a new object will run
        # in the background and will complete some time in the
        # future. This is a way to reserve the name and to let other
        # tasks know that they have to wait until available.
        self.promises = set()

        # same as above only for objects that are plotted
        self.plot_promises = set()

        # ## View
        self.view = EventSensitiveListView(self.app)
        self.view.setModel(self)
        self.view.setItemDelegate(ProjectItemDelegate(self.view))

        self.view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

        if self.app.options.get("global_allow_edit_in_project_tab",
                                self.app.defaults.get("global_allow_edit_in_project_tab",
                                                      self.app.defaults.factory_defaults.get("global_allow_edit_in_project_tab"))) is True:
            self.view.setEditTriggers(QtWidgets.QTreeView.EditTrigger.SelectedClicked)  # allow Edit on Tree
        else:
            self.view.setEditTriggers(QtWidgets.QTreeView.EditTrigger.NoEditTriggers)

        # objects are reordered by drag and drop inside their category (dropMimeData)
        self.view.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.view.setDragEnabled(True)
        self.view.setDropIndicatorShown(True)
        self.view.setDefaultDropAction(Qt.DropAction.MoveAction)

        settings = QSettings("Open Source", "FlatCAM_EVO")
        if settings.contains("notebook_font_size"):
            fsize = settings.value('notebook_font_size', type=int)
        else:
            fsize = 12

        font = QtGui.QFont()
        font.setPixelSize(fsize)
        font.setFamily("Seagoe UI")
        self.view.setFont(font)

        # ## GUI Events
        self.view.selectionModel().selectionChanged.connect(self.on_list_selection_change)  # noqa
        # self.view.activated.connect(self.on_item_activated)
        self.view.keyPressed.connect(self.app.ui.keyPressEvent)
        self.view.mouseReleased.connect(self.on_list_click_release)
        # self.view.clicked.connect(self.on_mouse_down)
        self.view.customContextMenuRequested.connect(self.on_menu_request)

        self.click_modifier = None

        self.update_list_signal.connect(self.on_update_list_signal)
        self.view.activated.connect(self.on_row_activated)

    def promise(self, obj_name):
        self.app.log.debug("Object %s has been promised." % obj_name)
        self.promises.add(obj_name)

    def remove_promise(self, obj_name):
        """The promised object will not be created (it failed or the user aborted): do not stay busy for it."""
        self.promises.discard(obj_name)

    def has_promises(self):
        return len(self.promises) > 0

    def plot_promise(self, plot_obj_name):
        self.plot_promises.add(plot_obj_name)

    def plot_remove_promise(self, plot_obj_name):
        if plot_obj_name in self.plot_promises:
            self.plot_promises.remove(plot_obj_name)

    def has_plot_promises(self):
        return len(self.plot_promises) > 0

    def on_mouse_down(self, event):
        # self.app.log.debug("Mouse button pressed on list")
        pass

    @staticmethod
    def has_source(obj):
        """
        Used by the project context menu and by App.on_view_source().

        :param obj: an App object
        :return:    True if View Source has a text to show for the object: the G-code of a CNCJob object, the source
                    text of the other objects (a Geometry made by a tool has none)
        """
        if obj.kind == 'cncjob':
            return True
        source = getattr(obj, 'source_file', None)
        return isinstance(source, str) and source != ''

    def category_open_actions(self):
        """
        :return: {kind: callable} with the file open action of each category of the project tree
        """
        f_handlers = self.app.f_handlers
        return {
            'gerber': f_handlers.on_file_open_gerber,
            'excellon': f_handlers.on_file_open_excellon,
            'cncjob': f_handlers.on_file_open_gcode,
            'geometry': f_handlers.on_file_open_geometry,
            'script': f_handlers.on_file_open_script,
            'document': f_handlers.on_file_open_document,
        }

    def on_category_menu_request(self, kind, pos):
        """
        Popup of a category row (Gerber, Excellon, ...): Show All, Hide All of the category and its Open action.
        """
        ui = self.app.ui
        objects = [obj for obj in self.get_list() if obj.kind == kind]
        plotted = kind in ['geometry', 'gerber', 'excellon', 'cncjob']

        menu = QtWidgets.QMenu(self.view)
        show_all = menu.addAction(ui.menuprojectenable.icon(), _('Show All'))
        hide_all = menu.addAction(ui.menuprojectdisable.icon(), _('Hide All'))
        show_all.setEnabled(plotted and len(objects) > 0)
        hide_all.setEnabled(plotted and len(objects) > 0)
        show_all.triggered.connect(lambda: self.app.enable_plots(objects))
        hide_all.triggered.connect(lambda: self.app.disable_plots(objects))

        open_action = self.category_open_actions().get(kind)
        if open_action is not None:
            menu.addSeparator()
            menu.addAction(QtGui.QIcon(self.icons[kind]), _('Open')).triggered.connect(lambda: open_action())

        menu.popup(self.view.mapToGlobal(pos))

    def on_menu_request(self, pos):
        ui = self.app.ui

        index = self.view.indexAt(pos)
        if index.isValid():
            item = index.internalPointer()
            if item is not None and item.obj is None:
                for kind, group_item in self.group_items.items():
                    if group_item is item:
                        self.on_category_menu_request(kind, pos)
                        return

        obj_selection = self.get_selected()
        sel = len(obj_selection) > 0

        entries = [ui.menuprojectenable, ui.menuprojectdisable, ui.menuprojectcolor.menuAction(),
                   ui.menuprojectviewsource, ui.menuprojectedit, ui.menuprojectcopy, ui.menuprojectdelete,
                   ui.menuprojectsave, ui.menuprojectproperties]
        for entry in entries:
            entry.setEnabled(sel)
            # set again on each request: the entries hidden for a Script or a Document stayed hidden for the next
            # objects
            entry.setVisible(True)

        ui.menuprojectsave.setText(_('Save'))

        if sel:
            # only the entries that apply to the selected objects
            plotted = all(obj.kind in ['geometry', 'gerber', 'excellon', 'cncjob'] for obj in obj_selection)
            all_cncjob = all(obj.kind == 'cncjob' for obj in obj_selection)
            # View Source, Edit and Save work on the active object (Save on several CNCJob objects is a Batch Save)
            single = obj_selection[0] if len(obj_selection) == 1 else None

            ui.menuprojectenable.setVisible(
                plotted and any(not obj.obj_options.get('plot', True) for obj in obj_selection))
            ui.menuprojectdisable.setVisible(
                plotted and any(obj.obj_options.get('plot', True) for obj in obj_selection))
            ui.menuprojectcolor.menuAction().setVisible(
                all(dispatch_attr(obj, 'has_plot_colors') for obj in obj_selection))
            ui.menuprojectviewsource.setVisible(plotted and single is not None and self.has_source(single))
            ui.menuprojectedit.setVisible(plotted and single is not None)
            ui.menuprojectsave.setVisible(single is not None or all_cncjob)
            ui.menuprojectproperties.setVisible(plotted)

            if single is None and all_cncjob:
                ui.menuprojectsave.setText(_('Batch Save'))

        ui.menuproject.popup(self.view.mapToGlobal(pos))

    def index(self, row, column=0, parent=None, *args, **kwargs):
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()

        # if not parent.isValid():
        #     parent_item = self.root_item
        # else:
        #     parent_item = parent.internalPointer()
        parent_item = parent.internalPointer() if parent.isValid() else self.root_item

        child_item = parent_item.child(row)
        if child_item:
            return self.createIndex(row, column, child_item)
        else:
            return QtCore.QModelIndex()

    def parent(self, index=None):
        if not index.isValid():
            return QtCore.QModelIndex()

        parent_item = index.internalPointer().parent_item

        if parent_item == self.root_item:
            return QtCore.QModelIndex()

        return self.createIndex(parent_item.row(), 0, parent_item)

    def rowCount(self, index=None, *args, **kwargs):
        if index.column() > 0:
            return 0

        if not index.isValid():
            parent_item = self.root_item
        else:
            parent_item = index.internalPointer()

        return parent_item.child_count()

    def columnCount(self, index=None, *args, **kwargs):
        if index.isValid():
            return index.internalPointer().column_count()
        else:
            return self.root_item.column_count()

    def data(self, index, role=None):
        if not index.isValid():
            return None

        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            obj = index.internalPointer().obj
            if obj:
                return obj.obj_options["name"]
            else:
                return index.internalPointer().data(index.column())

        if role == Qt.ItemDataRole.ForegroundRole:
            theme_settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
            theme = theme_settings.value('theme', type=str)

            if theme == 'dark':
                color = QColor(self.app.options.get(
                    'global_proj_item_color_dark',
                    self.app.defaults.get('global_proj_item_color_dark',
                                          self.app.defaults.factory_defaults.get('global_proj_item_color_dark'))
                )[:-2])
                color_disabled = QColor(self.app.options.get(
                    'global_proj_item_dis_color_dark',
                    self.app.defaults.get('global_proj_item_dis_color_dark',
                                          self.app.defaults.factory_defaults.get('global_proj_item_dis_color_dark'))
                )[:-2])
            else:
                color = QColor(self.app.options.get(
                    'global_proj_item_color_light',
                    self.app.defaults.get('global_proj_item_color_light',
                                          self.app.defaults.factory_defaults.get('global_proj_item_color_light'))
                )[:-2])
                color_disabled = QColor(self.app.options.get(
                    'global_proj_item_dis_color_light',
                    self.app.defaults.get('global_proj_item_dis_color_light',
                                          self.app.defaults.factory_defaults.get('global_proj_item_dis_color_light'))
                )[:-2])

            obj = index.internalPointer().obj
            if obj:
                return QtGui.QBrush(color) if obj.obj_options["plot"] else QtGui.QBrush(color_disabled)
            else:
                return index.internalPointer().data(index.column())

        elif role == Qt.ItemDataRole.DecorationRole:
            icon = index.internalPointer().icon
            if icon:
                return icon
            else:
                return QtGui.QPixmap()
        elif role == Qt.ItemDataRole.ToolTipRole:
            try:
                obj = index.internalPointer().obj
            except AttributeError:
                return None

            if obj:
                text = obj.obj_options['name']
                return text
            else:
                QtWidgets.QToolTip.hideText()
                return None
        else:
            return None

    def setData(self, index, data, role=None):
        if index.isValid():
            obj = index.internalPointer().obj

            if obj:
                old_name = deepcopy(obj.obj_options['name'])
                new_name = str(data)
                if old_name != new_name and new_name != '':
                    # the name of another object gets a number, as in append(): a lookup by name finds only the
                    # first object with that name
                    new_name = unique_name(new_name, {other.obj_options['name'] for other in self.get_list()
                                                      if other is not obj})
                if old_name != new_name and new_name != '':
                    # rename the object
                    history_of(self.app).record_rename(obj, old_name, new_name)
                    obj.obj_options["name"] = new_name

                    self.app.object_status_changed.emit(obj, 'rename', old_name)

                    # update the SHELL auto-completer model data
                    try:
                        self.app.regFK.remove_keyword(old_name, update=False)
                        self.app.regFK.prepend_keyword(new_name)
                        self.app.shell._edit.set_model_data(self.app.regFK.myKeywords)
                    except Exception as e:
                        self.app.log.error(
                            "setData() --> Could not remove the old object name from auto-completer model list. %s" %
                            str(e))
                    # obj.build_ui()
                    msg = "%s: <b>%s</b> %s: <b>%s</b>" % (_("Object renamed from"), old_name, _("to"), new_name)
                    self.app.inform.emit(msg)

            self.dataChanged.emit(index, index) # noqa
            return True
        else:
            return False

    def rename_object(self, obj, new_name):
        """
        Rename as the Project tree does (the keyword list, the status signal, a number for the name of another
        object), without a history step.

        :return:    the name of the object after the call
        """
        old_name = obj.obj_options['name']
        if old_name != new_name and new_name != '':
            new_name = unique_name(new_name, {other.obj_options['name'] for other in self.get_list()
                                              if other is not obj})
        if old_name == new_name or new_name == '':
            return old_name
        obj.obj_options['name'] = new_name
        self.app.object_status_changed.emit(obj, 'rename', old_name)
        try:
            self.app.regFK.remove_keyword(old_name, update=False)
            self.app.regFK.prepend_keyword(new_name)
        except Exception as e:
            self.app.log.error("rename_object() --> could not update the auto-completer: %s" % str(e))
        self.update_view()
        return new_name

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        return [ObjectCollection.PROJECT_MIME]

    def mimeData(self, indexes):
        """
        The rows of the dragged objects. Only the objects of one category can be dragged together.

        :return:    a QMimeData or None (no drag) if an index is not an object or the categories differ
        """
        groups = set()
        rows = set()
        for index in indexes:
            item = index.internalPointer() if index.isValid() else None
            if item is None or item.obj is None:
                return None
            groups.add(item.parent_item)
            rows.add(item.row())
        if len(groups) != 1:
            return None

        kind = next(k for k, group in self.group_items.items() if group is groups.pop())
        mime = QtCore.QMimeData()
        mime.setData(ObjectCollection.PROJECT_MIME,
                     QtCore.QByteArray(json.dumps({"kind": kind, "rows": sorted(rows)}).encode()))
        return mime

    def _drop_target(self, data, parent, row):
        """
        :return:    (group item, items to move, row where they go in that group) or None if the drop is refused:
                    foreign data, another category or the drop is not inside a category row.
        """
        if not data.hasFormat(ObjectCollection.PROJECT_MIME) or not parent.isValid():
            return None
        try:
            payload = json.loads(bytes(data.data(ObjectCollection.PROJECT_MIME)).decode())
            kind, rows = payload["kind"], payload["rows"]
        except (ValueError, KeyError, TypeError):
            return None

        target = parent.internalPointer()
        if target.obj is not None:
            # dropped on an object row: before that object
            row, target = parent.row(), target.parent_item
        if target is not self.group_items.get(kind) or not rows or max(rows) >= target.child_count():
            return None
        if row < 0 or row > target.child_count():
            row = target.child_count()
        return target, [target.child_items[r] for r in rows], row

    def canDropMimeData(self, data, action, row, column, parent):
        return action == Qt.DropAction.MoveAction and self._drop_target(data, parent, row) is not None

    def dropMimeData(self, data, action, row, column, parent):
        """
        Move the dragged objects inside their category to the drop row. The drop returns True when the move is done;
        the view then asks for removeRows(), which the model does not implement, so nothing else is removed.
        """
        if action != Qt.DropAction.MoveAction:
            return False
        target = self._drop_target(data, parent, row)
        if target is None:
            return False
        group, moved, row = target

        old_rows = {id(item.obj): item.row() for item in group.child_items}
        before = sum(1 for item in moved if item.row() < row)
        rest = [item for item in group.child_items if item not in moved]
        new_order = rest[:row - before] + moved + rest[row - before:]

        group_index = self.createIndex(group.row(), 0, group)
        for new_row, item in enumerate(new_order):
            current = group.child_items.index(item)
            if current == new_row:
                continue
            self.beginMoveRows(group_index, current, current, group_index, new_row)
            group.child_items.insert(new_row, group.child_items.pop(current))
            self.endMoveRows()

        if group is self.group_items["gerber"]:
            self.apply_gerber_row_colors(old_rows)
        self.app.should_we_save = True
        return True

    def apply_gerber_row_colors(self, old_rows):
        """
        The Gerber colors are positional: row N of the Gerber category has the entry N of "gerber_color_list".
        After a move, the Gerber objects whose row changed take the colors of the entry of their new row; the list
        itself is not changed. An object without an entry for its row keeps its colors.

        :param old_rows:    {id(Gerber object): row before the move}
        """
        options = self.app.options
        defaults = getattr(self.app, "defaults", None)
        store = options.get("gerber_store_color_list",
                            defaults.get("gerber_store_color_list") if defaults is not None else None)
        if store is not True:
            return
        color_list = options.get("gerber_color_list", [])

        for row, item in enumerate(self.group_items["gerber"].child_items):
            obj = item.obj
            if old_rows.get(id(obj)) == row or row >= len(color_list):
                continue
            outline, fill = color_list[row][0], color_list[row][1]
            obj.outline_color = outline
            obj.fill_color = fill
            obj.alpha_level = str(hex(int(fill[7:9], 16))[2:])
            obj.shapes.redraw(update_colors=(fill, outline))
            self.update_view(obj)

    def flags(self, index):
        default_flags = QtCore.QAbstractItemModel.flags(self, index)

        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled | default_flags

        # Prevent groups from selection; they accept the drop of the objects of their category
        try:
            if not index.internalPointer().obj:
                return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
            else:
                return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable | \
                       Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        except AttributeError:
            return Qt.ItemFlag.ItemIsEnabled
        # return QtWidgets.QAbstractItemModel.flags(self, index)

    def append(self, obj, active=False, to_index=None):
        self.app.log.debug(sys._getframe(1).f_code.co_name + " --> OC.append()")

        name = obj.obj_options["name"]

        # Check promises and clear if exists
        if name in self.promises:
            self.promises.remove(name)
            # log.debug("Promised object %s became available." % name)
            # log.debug("%d promised objects remaining." % len(self.promises))

        # one pass over the collection for the names and the history ids
        others = [o for o in self.get_list() if o is not obj]
        names = {o.obj_options['name'] for o in others}

        # an object restored from a file can bring the history id of another object
        if getattr(obj, 'history_uid', None) in {getattr(o, 'history_uid', None) for o in others} - {None}:
            obj.history_uid = uuid.uuid4().hex

        # Prevent same name
        while name in names:
            # ## Create a new name
            # Ends with number?
            self.app.log.debug("app_obj.new_object(): Object name (%s) exists, changing." % name)
            match = re.search(r'(.*[^\d])?(\d+)$', name)
            if match:  # Yes: Increment the number!
                base = match.group(1) or ''
                num = int(match.group(2))
                name = base + str(num + 1)
            else:  # No: add a number!
                name += "_1"
        obj.obj_options["name"] = name

        # ############################################################################################################
        # update the KeyWords list with the name of the file
        # ############################################################################################################
        self.app.regFK.prepend_keyword(name, update=not bool(getattr(self.app.app_obj, 'load_batches', None)))

        # ############################################################################################################
        # ############################# Set the Object UI (Properties Tab) ###########################################
        # ############################################################################################################
        if obj.kind == 'script':
            obj._script_collection = self
        if obj.kind != 'script' or getattr(obj, '_load_batch', None) is None:
            obj.set_ui(obj.ui_type(app=self.app))
            obj.build_ui()

        # a way to signal that the object was fully loaded
        obj.load_complete = True

        # Required before appending (Qt MVC)
        group = self.group_items[obj.kind]
        group_index = self.index(group.row(), 0, QtCore.QModelIndex())

        if to_index is None:
            self.beginInsertRows(group_index, group.child_count(), group.child_count())
            # Append new item
            obj.item = TreeItem(None, self.icons[obj.kind], obj, group)
            # Required after appending (Qt MVC)
            self.endInsertRows()
        else:
            self.beginInsertRows(group_index, to_index.row()-1, to_index.row()-1)
            # Append new item
            obj.item = TreeItem(None, self.icons[obj.kind], obj, group)
            # Required after appending (Qt MVC)
            self.endInsertRows()

        # Expand group
        if group.child_count() == 1:
            self.view.setExpanded(group_index, True)

        self.app.should_we_save = True

        self.app.object_status_changed.emit(obj, 'append', name)

        # decide if to show or hide the Notebook side of the screen
        if self.app.options.get("global_project_autohide",
                                self.app.defaults.get("global_project_autohide",
                                                      self.app.defaults.factory_defaults.get("global_project_autohide"))) is True:
            # always open the notebook on object added to collection
            self.app.ui.splitter.setSizes([1, 1])

    def get_names(self):
        """
        Gets a list of the names of all objects in the collection.

        :return:            List of names.
        :rtype:             list
        """

        # log.debug(sys._getframe(1).f_code.co_name + " --> OC.get_names()")
        return [x.obj_options['name'] for x in self.get_list()]

    def get_bounds(self):
        """
        Finds coordinates bounding all objects in the collection.

        :return: [xmin, ymin, xmax, ymax]
        :rtype: list
        """
        self.app.log.debug(sys._getframe(1).f_code.co_name + "--> OC.get_bounds()")

        # TODO: Move the operation out of here.

        xmin = Inf
        ymin = Inf
        xmax = -Inf
        ymax = -Inf

        # for obj in self.object_list:
        for obj in self.get_list():
            try:
                gxmin, gymin, gxmax, gymax = obj.bounds()
                xmin = min([xmin, gxmin])
                ymin = min([ymin, gymin])
                xmax = max([xmax, gxmax])
                ymax = max([ymax, gymax])
            except Exception as e:
                self.app.log.error("Tried to get bounds of empty geometry. %s" % str(e))

        return [xmin, ymin, xmax, ymax]

    def get_by_name(self, name, isCaseSensitive=None):
        """
        Fetches the FlatCAMObj with the given `name`.

        :param name: The name of the object.
        :type name: str
        :param isCaseSensitive: whether searching of the object is done by name where the name is case sensitive
        :return: The requested object or None if no such object.
        :rtype: FlatCAMObj or None
        """
        # log.debug(sys._getframe(1).f_code.co_name + "--> OC.get_by_name()")

        if isCaseSensitive is None or isCaseSensitive is True:
            for obj in self.get_list():
                if obj.obj_options['name'] == name:
                    return obj
        else:
            for obj in self.get_list():
                if obj.obj_options['name'].lower() == name.lower():
                    return obj
        return None

    def close_object_editor(self, obj):
        tabs = self.app.ui.plot_tab_area
        if obj.kind == 'script':
            editor = getattr(obj, 'script_editor_tab', None)
            if editor is not None:
                for idx in reversed(range(tabs.count())):
                    if tabs.widget(idx) is editor:
                        tabs.removeTab(idx)
                editor.script_owner = None
                editor.deleteLater()
                obj.script_editor_tab = None
            return
        name = obj.obj_options['name']
        for idx in reversed(range(tabs.count())):
            if tabs.widget(idx).objectName() in (name, name + '_editor_tab'):
                tabs.removeTab(idx)

    def delete_active(self, select_project=True):
        if getattr(self.app, '_script_event_pump', False) is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before deleting objects."))
            return 'fail'
        selections = self.view.selectedIndexes()
        if len(selections) == 0:
            return

        active = selections[0].internalPointer()
        group = active.parent_item

        # send signal with the object that is deleted
        # self.app.object_status_changed.emit(active.obj, 'delete')

        self.close_object_editor(active.obj)

        # update the SHELL auto-completer model data
        name = active.obj.obj_options['name']
        try:
            self.app.regFK.remove_keyword(name)
            # this is not needed any more because now the code editor is created on demand
            # self.app.ui.code_editor.set_model_data(self.app.myKeywords)
        except Exception as e:
            self.app.log.error(
                "delete_active() --> Could not remove the old object name from auto-completer model list. %s" % str(e))

        self.app.object_status_changed.emit(active.obj, 'delete', name)

        # ############ OBJECT DELETION FROM MODEL STARTS HERE ####################
        self.beginRemoveRows(self.index(group.row(), 0, QtCore.QModelIndex()), active.row(), active.row())
        group.remove_child(active)
        # after deletion of object store the current list of objects into the self.app.all_objects_list
        self.app.all_objects_list = self.get_list()
        self.endRemoveRows()
        # ############ OBJECT DELETION FROM MODEL STOPS HERE ####################

        self.app.plotcanvas.redraw()

        if select_project:
            # always go to the Project Tab after object deletion as it may be done with a shortcut key
            self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)

        self.app.should_we_save = True

        # decide if to show or hide the Notebook side of the screen
        if self.app.options.get("global_project_autohide",
                                self.app.defaults.get("global_project_autohide",
                                                      self.app.defaults.factory_defaults.get("global_project_autohide"))) is True:
            # hide the notebook if there are no objects in the collection
            if not self.get_list():
                self.app.ui.splitter.setSizes([0, 1])

    def delete_objects(self, objects):
        """Detach a batch from the tree before cleanup, then refresh shared visuals once."""
        if getattr(self.app, '_script_event_pump', False) is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before deleting objects."))
            return 'fail'
        object_ids = {id(obj) for obj in objects}
        items = []
        for group in self.root_item.child_items:
            rows = [row for row, item in enumerate(group.child_items) if id(item.obj) in object_ids]
            if not rows:
                continue
            items.extend(group.child_items[row] for row in rows)
            # Remove contiguous ranges backwards so surviving persistent indexes stay valid.
            last = rows[-1]
            first = last
            ranges = []
            for row in reversed(rows[:-1]):
                if row == first - 1:
                    first = row
                else:
                    ranges.append((first, last))
                    first = last = row
            ranges.append((first, last))
            parent = self.index(group.row(), 0, QtCore.QModelIndex())
            for first, last in ranges:
                self.beginRemoveRows(parent, first, last)
                del group.child_items[first:last + 1]
                self.endRemoveRows()

        if not items:
            return
        self.app.all_objects_list = self.get_list()
        self.app.should_we_save = True
        # Paint the completed removal without processing unrelated Qt events during cleanup.
        self.view.viewport().repaint()

        for item in items:
            obj = item.obj
            name = obj.obj_options['name']
            self.close_object_editor(obj)
            try:
                self.app.regFK.remove_keyword(name, update=False)
            except Exception as e:
                self.app.log.error("delete_objects() --> Could not remove object keyword. %s" % str(e))
            self.app.object_status_changed.emit(obj, 'delete', name)
            obj.clear()
            obj.delete()
            del item.obj

        try:
            self.app.regFK.register_keywords(self.app.regFK.myKeywords)
        except Exception as e:
            self.app.log.error("delete_objects() --> Could not update auto-completer model. %s" % str(e))
        self.app.plotcanvas.redraw()
        self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)
        if self.app.options.get("global_project_autohide",
                                self.app.defaults.get("global_project_autohide",
                                                      self.app.defaults.factory_defaults.get("global_project_autohide"))) is True:
            if not self.app.all_objects_list:
                self.app.ui.splitter.setSizes([0, 1])

    def delete_by_name(self, name, select_project=True):
        if getattr(self.app, '_script_event_pump', False) is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before deleting objects."))
            return 'fail'
        obj = self.get_by_name(name=name)
        item = obj.item
        group = self.group_items[obj.kind]

        group_index = self.index(group.row(), 0, QtCore.QModelIndex())
        item_index = self.index(item.row(), 0, group_index)

        deleted = item_index.internalPointer()
        group = deleted.parent_item

        self.close_object_editor(deleted.obj)

        # update the SHELL auto-completer model data
        name = deleted.obj.obj_options['name']
        try:
            self.app.regFK.remove_keyword(name)
            # this is not needed any more because now the code editor is created on demand
            # self.app.ui.code_editor.set_model_data(self.app.myKeywords)
        except Exception as e:
            self.app.log.error(
                "delete_by_name() --> Could not remove the old object name from auto-completer model list. %s" % str(e))

        self.app.object_status_changed.emit(deleted.obj, 'delete', name)

        # ############ OBJECT DELETION FROM MODEL STARTS HERE ####################
        self.beginRemoveRows(self.index(group.row(), 0, QtCore.QModelIndex()), deleted.row(), deleted.row())
        group.remove_child(deleted)
        # after deletion of object store the current list of objects into the self.app.all_objects_list
        self.update_list_signal.emit()
        self.endRemoveRows()
        # ############ OBJECT DELETION FROM MODEL STOPS HERE ####################

        self.app.plotcanvas.redraw()

        if select_project:
            # always go to the Project Tab after object deletion as it may be done with a shortcut key
            self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)

        self.app.should_we_save = True

        # decide if to show or hide the Notebook side of the screen
        if self.app.options.get("global_project_autohide",
                                self.app.defaults.get("global_project_autohide",
                                                      self.app.defaults.factory_defaults.get("global_project_autohide"))) is True:
            # hide the notebook if there are no objects in the collection
            if not self.get_list():
                self.app.ui.splitter.setSizes([0, 1])

    def on_update_list_signal(self):
        self.app.all_objects_list = self.get_list()

    def delete_all(self):
        if getattr(self.app, '_script_event_pump', False) is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Stop the running script before deleting objects."))
            return 'fail'
        self.app.log.debug(sys._getframe(1).f_code.co_name + "--> OC.delete_all()")

        for obj in self.get_list():
            if obj.kind == 'script':
                self.close_object_editor(obj)

        self.app.object_status_changed.emit(None, 'delete_all', '')

        try:
            self.app.all_objects_list.clear()

            self.app.geo_editor.clear()
            self.app.exc_editor.clear()

            self.app.dblsidedtool.reset_fields()
            self.app.panelize_tool.reset_fields()
            self.app.cutout_tool.reset_fields()
            self.app.film_tool.reset_fields()
        except Exception as e:
            self.app.log.error("ObjectCollection.delete_all() --> %s" % str(e))

        self.beginResetModel()
        self.checked_indexes = []

        for group in self.root_item.child_items:
            try:
                group.remove_children()
            except Exception as e:
                self.app.log.error("ObjectCollection.delete_all() group %s--> %s" % (str(group), str(e)))

        self.endResetModel()

        self.app.plotcanvas.redraw()

    def get_active(self):
        """
        Returns the active object or None

        :return: FlatCAMObj or None
        """
        selections = self.view.selectedIndexes()
        if len(selections) == 0:
            return None

        return selections[0].internalPointer().obj

    def get_selected(self):
        """
        Returns list of objects selected in the view.

        :return: List of objects
        """
        return [sel.internalPointer().obj for sel in self.view.selectedIndexes()]

    def get_non_selected(self):
        """
        Returns list of objects non-selected in the view.

        :return: List of objects
        """

        obj_list = self.get_list()

        for sel in self.get_selected():
            obj_list.remove(sel)

        return obj_list

    def set_active(self, name):
        """
        Selects object by name from the project list. This triggers the
        list_selection_changed event and call on_list_selection_changed.

        :param name: Name of the FlatCAM Object
        :return: None
        """
        try:
            obj = self.get_by_name(name)
            item = obj.item
            group = self.group_items[obj.kind]

            group_index = self.index(group.row(), 0, QtCore.QModelIndex())
            item_index = self.index(item.row(), 0, group_index)

            self.view.selectionModel().select(item_index, QtCore.QItemSelectionModel.SelectionFlag.Select)
        except Exception as e:
            self.app.log.error("[ERROR] Cause: %s" % str(e))
            raise

    def set_all_active(self):
        """
        Select all objects from the project list. This triggers the
        list_selection_changed event and call on_list_selection_changed.

        :return: None
        """
        for name in self.get_names():
            self.set_active(name)

    def set_exclusive_active(self, name):
        """
        Make the object with the name in parameters the only selected object

        :param name: name of object to be selected and made the only active object
        :return: None
        """
        self.set_all_inactive()
        self.set_active(name)

    def set_inactive(self, name):
        """
        Unselect object by name from the project list. This triggers the
        list_selection_changed event and call on_list_selection_changed.

        :param name: Name of the FlatCAM Object
        :return: None
        """
        # log.debug("ObjectCollection.set_inactive()")

        obj = self.get_by_name(name)
        item = obj.item
        group = self.group_items[obj.kind]

        group_index = self.index(group.row(), 0, QtCore.QModelIndex())
        item_index = self.index(item.row(), 0, group_index)

        self.view.selectionModel().select(item_index, QtCore.QItemSelectionModel.SelectionFlag.Deselect)

    def set_all_inactive(self):
        """
        Unselect all objects from the project list. This triggers the
        list_selection_changed event and call on_list_selection_changed.

        :return: None
        """
        for name in self.get_names():
            self.set_inactive(name)

    def on_list_selection_change(self, current, previous):
        """

        :param current:     Current selected item
        :param previous:    Previously selected item
        :return:
        """

        # log.debug("on_list_selection_change()")
        # log.debug("Current: %s, Previous %s" % (str(current), str(previous)))

        try:
            indexes = current.indexes()
            if not indexes:
                return
            obj = indexes[0].internalPointer().obj
            self.item_selected.emit(obj.obj_options['name'])

            status_color = dispatch_attr(obj, 'status_color')
            if status_color is not None:
                self.app.inform.emit('[selected]<span style="color:{color};">{name}</span> {tx}'.format(
                    color=status_color,
                    name=str(obj.obj_options['name']),
                    tx=_("selected"))
                )
        except IndexError:
            self.item_selected.emit('none')
            # log.debug("on_list_selection_change(): Index Error (Nothing selected?)")
            self.app.inform.emit('')
            try:
                self.app.ui.properties_scroll_area.takeWidget()
            except Exception as e:
                self.app.log.error("ObjectCollection.on_list_selection_changed() -> Nothing to remove. %s" % str(e))

            self.app.setup_default_properties_tab()
            return

        # make sure that if the Properties Tab is selected then the Object UI is updated (built)
        self.app.on_notebook_tab_changed()

        # don't emit the signal if there is more than one objects selected
        # this signal is intended to be emitted for a single selection in the collection view
        if len(self.get_selected()) == 1:
            self.app.proj_selection_changed.emit(current, previous)

    def on_list_click_release(self, button):
        # works only for mouse button 1 (left click)
        if button == Qt.MouseButton.LeftButton:
            # on Gerber object selection it will redrawn on top of the other Gerber objects
            if self.app.options.get("gerber_plot_on_select",
                                    self.app.defaults.get("gerber_plot_on_select",
                                                          self.app.defaults.factory_defaults.get("gerber_plot_on_select"))) is True:
                self.app.gerber_redraw()

    def on_item_activated(self, index):
        """
        Double-click or Enter on item.

        :param index: Index of the item in the list.
        :return: None
        """
        a_idx = index.internalPointer().obj
        if a_idx is None:
            return

        try:
            a_idx.build_ui()
        except RuntimeError:
            a_idx.set_ui(a_idx.ui_type(app=self.app))
            a_idx.build_ui()
        except Exception as e:
            self.app.inform.emit('[ERROR] %s: %s' % (_("Cause of error"), str(e)))

    def get_list(self):
        """
        Will return a list of all objects currently opened. Except FlatCAMScript and FlatCAMDocuments

        :return:
        """
        obj_list = []
        for group in self.root_item.child_items:
            for item in group.child_items:
                obj_list.append(item.obj)

        return obj_list

    def update_view(self, obj=None):
        """
        Refresh the project tree rows without resetting the layout, so the selection and the persistent
        indexes are kept.

        :param obj:     If set, only the row of this object is refreshed; if the object is not in the collection
                        nothing happens. If None, all the rows of all the non-empty groups are refreshed.
        :return:        None
        """
        if obj is not None:
            for group in self.root_item.child_items:
                for row, item in enumerate(group.child_items):
                    if item.obj is obj:
                        # both indexes have the same parent (the group)
                        item_index = self.createIndex(row, 0, item)
                        self.dataChanged.emit(item_index, item_index)
                        return
            return

        for group_row, group in enumerate(self.root_item.child_items):
            if not group.child_items:
                continue
            parent = self.createIndex(group_row, 0, group)
            last = len(group.child_items) - 1
            self.dataChanged.emit(self.index(0, 0, parent), self.index(last, 0, parent))

    def on_row_activated(self, index):
        if index.isValid():
            if index.internalPointer().parent_item != self.root_item:
                self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
        self.on_item_activated(index)

    def on_objects_selection(self, on_off):
        obj_list = self.get_names()

        if on_off is True:
            self.set_all_active()
            if obj_list:
                self.app.inform[str, bool].emit('[selected] %s' % _("All objects are selected."), False)
        else:
            self.set_all_inactive()

            if obj_list:
                self.app.inform[str, bool].emit('%s' % _("Objects selection is cleared."), False)
            else:
                self.app.inform[str, bool].emit('', False)
