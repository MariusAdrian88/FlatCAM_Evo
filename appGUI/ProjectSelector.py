from PyQt6 import QtCore, QtGui, QtWidgets

# App Translation
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ProjectCombo(QtWidgets.QComboBox):
    """
    A combobox that does not open while a background task runs.
    """

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager

    def showPopup(self):
        if self.manager.is_busy():
            self.manager.inform_busy()
            return
        super().showPopup()


class ProjectSelector(QtWidgets.QWidget):
    """
    The project combobox above the Project tab tree. A right-click on the combobox opens the project commands.
    """

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        # the Import dialog opens on the import that the user ran last
        self.last_import_key = None

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.combo = ProjectCombo(manager)
        self.combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.combo.setToolTip(_("The active project. Right-click for the project commands."))
        self.combo.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)

        layout.addWidget(self.combo, stretch=1)

        self.combo.activated.connect(self.on_activated)
        self.combo.customContextMenuRequested.connect(self.on_menu_request)
        self.manager.projects_changed.connect(self.refresh)

        self.refresh()

    def refresh(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(self.manager.names())
        self.combo.setCurrentIndex(self.manager.contexts.index(self.manager.active))
        self.combo.blockSignals(False)

    def on_activated(self, index):
        if 0 <= index < len(self.manager.contexts):
            self.manager.switch(self.manager.contexts[index])
        # if the switch is refused, show the active project again
        self.refresh()

    def build_menu(self):
        menu = QtWidgets.QMenu(self)
        res = self.manager.app.resource_location
        new_action = menu.addAction(QtGui.QIcon(res + '/file16.png'), _("New Project"))
        open_action = menu.addAction(QtGui.QIcon(res + '/folder16.png'), _("Open Project..."))
        import_action = menu.addAction(QtGui.QIcon(res + '/import.png'), _("Import..."))
        save_action = menu.addAction(QtGui.QIcon(res + '/save_as.png'), _("Save Project"))
        rename_action = menu.addAction(QtGui.QIcon(res + '/edit_file16.png'), _("Rename Project..."))
        menu.addSeparator()
        close_action = menu.addAction(QtGui.QIcon(res + '/delete_file16.png'), _("Close Project"))
        close_all_action = menu.addAction(QtGui.QIcon(res + '/delete_file16.png'), _("Close All Projects"))

        new_action.triggered.connect(lambda: self.manager.new())
        open_action.triggered.connect(lambda: self.manager.app.f_handlers.on_file_open_project())
        import_action.triggered.connect(lambda: self.on_import())
        # File -> Save of the active project: a project without a file opens Save As
        save_action.triggered.connect(lambda: self.manager.app.f_handlers.on_file_save_project())
        rename_action.triggered.connect(self.on_rename)
        close_action.triggered.connect(lambda: self.manager.request_close(self.manager.active))
        close_all_action.triggered.connect(lambda: self.manager.request_close_all())
        return menu

    def on_menu_request(self, pos):
        if self.manager.is_busy():
            self.manager.inform_busy()
            return
        menu = self.build_menu()
        menu.exec(self.combo.mapToGlobal(pos))

    def on_import(self):
        from appGUI.ImportDialog import ImportDialog

        dialog = ImportDialog(self.manager.app, parent=self.manager.app.ui, last_key=self.last_import_key)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.last_import_key = dialog.selected_key()
        # the file dialog of the import opens after this dialog is closed
        dialog.run_selected()

    def on_rename(self):
        ctx = self.manager.active
        name, ok = QtWidgets.QInputDialog.getText(self, _("Rename Project"), _("New name:"), text=ctx.name)
        if not ok:
            return
        if not self.manager.rename(ctx, name):
            self.manager.app.inform.emit('[ERROR_NOTCL] %s' %
                                         _("The name is empty, not valid or used by another project."))
