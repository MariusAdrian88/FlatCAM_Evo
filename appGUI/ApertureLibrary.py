"""Gerber editor aperture presets in the application's tools database."""

from copy import deepcopy
import gettext
import builtins

from PyQt6 import QtCore, QtWidgets
from appCommon.ToolDatabase import read_apertures, write_apertures, normalize_apertures
from appGUI.GUIElements import FCButton, FCComboBox, FCLabel, FCTable
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def open_aperture_library(app, parent=None, apertures=None):
    try:
        dialog = ApertureLibrary(app, parent=parent, apertures=apertures)
        dialog.exec()
    except (OSError, ValueError, TypeError) as error:
        app.inform.emit('[ERROR_NOTCL] %s' % str(error))


class ApertureLibrary(QtWidgets.QDialog):
    def __init__(self, app, parent=None, apertures=None):
        super().__init__(parent)
        self.app = app
        self.filename = app.tools_database_path()
        self.original = read_apertures(self.filename)
        records = deepcopy(self.original)
        # A board's D-code can already belong to a different preset. Never replace it implicitly.
        for code, aperture in (apertures or {}).items():
            candidate = normalize_apertures({code: dict(aperture, units=app.app_units.upper(), autoload=False)})[code]
            if any({key: value for key, value in item.items() if key != 'autoload'} ==
                   {key: value for key, value in candidate.items() if key != 'autoload'}
                   for item in records.values()):
                continue
            if code in records:
                code = max(records, default=9) + 1
            records[code] = candidate

        self.setWindowTitle(_("Gerber Apertures Database"))
        self.resize(760, 420)
        layout = QtWidgets.QVBoxLayout(self)
        note = FCLabel(_("Checked Autoload apertures are loaded only into an empty Gerber editor.\n"
                         "C: edit Size. R/O: edit Width and Height. Dimensions use the row's units."))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = FCTable()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([_("Code"), _("Type"), _("Size"), _("Width"), _("Height"),
                                              _("Units"), _("Autoload")])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        for code, record in records.items():
            self.add_row(code, record)

        buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(buttons)
        for label, callback in ((_("Add"), self.add_new), (_("Delete"), self.delete_selected),
                                (_("Save"), self.save_library), (_("Close"), self.reject)):
            button = FCButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        self.status = FCLabel('')
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

    def add_row(self, code, record):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(code)))
        kind = FCComboBox()
        kind.addItems(['C', 'R', 'O'])
        kind.setCurrentText(record['type'])
        self.table.setCellWidget(row, 1, kind)
        for column, field in ((2, 'size'), (3, 'width'), (4, 'height')):
            self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(record.get(field, 0.0))))
        units = QtWidgets.QTableWidgetItem(record['units'])
        units.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 5, units)
        autoload = QtWidgets.QTableWidgetItem()
        autoload.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled |
                          QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        autoload.setCheckState(QtCore.Qt.CheckState.Checked if record['autoload'] else QtCore.Qt.CheckState.Unchecked)
        self.table.setItem(row, 6, autoload)

    def add_new(self):
        try:
            code = max([int(self.table.item(row, 0).text()) for row in range(self.table.rowCount())] + [9]) + 1
            self.add_row(code, {'type': 'C', 'size': 1.0, 'units': self.app.app_units.upper(), 'autoload': False})
        except ValueError as error:
            self.status.setText(str(error))

    def delete_selected(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def save_library(self):
        try:
            records = {}
            for row in range(self.table.rowCount()):
                code = int(self.table.item(row, 0).text())
                if code in records:
                    raise ValueError(_("Aperture codes must be unique."))
                kind = self.table.cellWidget(row, 1).currentText()
                record = {'type': kind, 'units': self.table.item(row, 5).text(),
                          'autoload': self.table.item(row, 6).checkState() == QtCore.Qt.CheckState.Checked}
                for column, field in (((2, 'size'),) if kind == 'C' else ((3, 'width'), (4, 'height'))):
                    record[field] = float(self.table.item(row, column).text())
                records[code] = record
            self.original = write_apertures(self.filename, records, expected=self.original)
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(str(error))
            self.app.inform.emit('[ERROR_NOTCL] %s' % str(error))
            return False
        self.status.setText(_("Aperture database saved."))
        self.app.inform.emit('[success] %s' % _("Aperture database saved."))
        return True
