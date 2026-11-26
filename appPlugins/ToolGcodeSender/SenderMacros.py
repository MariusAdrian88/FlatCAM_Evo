# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import json

from PyQt6 import QtCore, QtWidgets

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

OPTION = "tools_grbl_user_buttons"
MAX_BUTTONS = 24
COLUMNS = 4


def clean_button(item):
    """:return: {'name', 'tip', 'lines'} made of a dict, or None if it is not a button."""
    if not isinstance(item, dict):
        return None
    name = str(item.get('name') or '').strip()
    lines = item.get('lines')
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not name or not isinstance(lines, (list, tuple)):
        return None
    lines = [str(line).strip() for line in lines if str(line).strip()]
    return {'name': name, 'tip': str(item.get('tip') or ''), 'lines': lines}


class MacroDialog(QtWidgets.QDialog):
    """Name, tooltip and G-code lines of one user button."""

    def __init__(self, button=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Button"))
        button = button or {'name': '', 'tip': '', 'lines': []}
        layout = QtWidgets.QFormLayout(self)
        self.name_entry = QtWidgets.QLineEdit(button['name'])
        self.tip_entry = QtWidgets.QLineEdit(button['tip'])
        self.lines_edit = QtWidgets.QPlainTextEdit('\n'.join(button['lines']))
        layout.addRow(_("Name"), self.name_entry)
        layout.addRow(_("Tooltip"), self.tip_entry)
        layout.addRow(_("G-code"), self.lines_edit)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addRow(box)

    def get_button(self):
        """:return: the button of the entries, or None if the name or the lines are empty."""
        return clean_button({'name': self.name_entry.text(), 'tip': self.tip_entry.text(),
                             'lines': self.lines_edit.toPlainText().splitlines()}) or None


class MacroPage(QtWidgets.QWidget):
    """The user buttons: a grid of buttons that send their lines to the controller."""

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self._run_enabled = True
        layout = QtWidgets.QVBoxLayout(self)
        self.grid_widget = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_widget)
        layout.addWidget(self.grid_widget)
        row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton(_("Add"))
        self.import_button = QtWidgets.QPushButton(_("Import"))
        self.export_button = QtWidgets.QPushButton(_("Export"))
        for button in (self.add_button, self.import_button, self.export_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()
        self.add_button.clicked.connect(self.add)
        self.import_button.clicked.connect(self.import_file)
        self.export_button.clicked.connect(self.export_file)
        self.rebuild()

    def buttons(self):
        """:return: the list of the buttons in the options (the one list; edited in place)."""
        return self.tool.app.options.setdefault(OPTION, [])

    def set_run_enabled(self, enabled):
        """The buttons follow action_allowed('macro')."""
        self._run_enabled = bool(enabled)
        for widget in self.grid_widget.findChildren(QtWidgets.QPushButton):
            widget.setEnabled(self._run_enabled)

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, button in enumerate(self.buttons()):
            widget = QtWidgets.QPushButton(button['name'])
            widget.setToolTip(button['tip'])
            widget.setEnabled(self._run_enabled)
            widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            widget.clicked.connect(lambda _checked=False, i=index: self.run(i))
            widget.customContextMenuRequested.connect(lambda pos, i=index, w=widget: self._menu(i, w.mapToGlobal(pos)))
            self.grid.addWidget(widget, index // COLUMNS, index % COLUMNS)
        self.add_button.setEnabled(len(self.buttons()) < MAX_BUTTONS)

    def run(self, index):
        self.tool.run_macro(self.buttons()[index]['lines'])

    def add(self):
        dialog = MacroDialog(parent=self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            button = dialog.get_button()
            if button is not None and len(self.buttons()) < MAX_BUTTONS:
                self.buttons().append(button)
                self.rebuild()

    def edit(self, index):
        dialog = MacroDialog(self.buttons()[index], parent=self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            button = dialog.get_button()
            if button is not None:
                self.buttons()[index] = button
                self.rebuild()

    def delete(self, index):
        del self.buttons()[index]
        self.rebuild()

    def move(self, index, step):
        buttons = self.buttons()
        target = index + step
        if 0 <= target < len(buttons):
            buttons[index], buttons[target] = buttons[target], buttons[index]
            self.rebuild()

    def _menu(self, index, global_pos):
        menu = QtWidgets.QMenu(self)
        menu.addAction(_("Edit"), lambda: self.edit(index))
        menu.addAction(_("Delete"), lambda: self.delete(index))
        menu.addAction(_("Move left"), lambda: self.move(index, -1))
        menu.addAction(_("Move right"), lambda: self.move(index, 1))
        menu.exec(global_pos)

    def import_file(self, filename=None):
        if filename is None:
            filename, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, _("Import the buttons"), '', "JSON (*.json);;%s (*.*)" % _("All Files"))
        if not filename:
            return False
        try:
            with open(filename, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(_("The file has no list of buttons."))
        except (OSError, ValueError) as err:
            QtWidgets.QMessageBox.warning(self, _("Import the buttons"),
                                          '%s\n%s' % (_("The file could not be read."), str(err)))
            return False
        buttons = [button for button in map(clean_button, data) if button is not None][:MAX_BUTTONS]
        self.buttons()[:] = buttons
        self.rebuild()
        return True

    def export_file(self, filename=None):
        if filename is None:
            filename, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, _("Export the buttons"), 'buttons.json', "JSON (*.json)")
        if not filename:
            return False
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(self.buttons(), f, indent=2)
        except OSError as err:
            QtWidgets.QMessageBox.warning(self, _("Export the buttons"), str(err))
            return False
        return True
