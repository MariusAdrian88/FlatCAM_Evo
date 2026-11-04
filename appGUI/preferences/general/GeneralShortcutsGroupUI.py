# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# License:  MIT Licence                                    #
# ##########################################################
"""
The Keyboard Shortcuts page of the Preferences (section Application): the table of the actions of the registry
(appGUI/shortcuts.py) with their keys, a search by the name and by a key, and the editing of a key with a combo box for
the modifiers and a field where the key is pressed. A key that is already used where the action works is reported before
it is assigned.

The page is 1 field of the Preferences form (ShortcutsEditor, option 'global_shortcuts'): get_value() gives the keys
that differ from the factory keys, so Save, Apply, Cancel, the marks of the changed settings and "Reset page to
defaults" work as on the other pages. The keys work after Save or Apply.
"""

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appGUI import shortcuts

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# the modifiers of the combo box (Qt's portable text; on macOS Qt shows Ctrl as Cmd)
MODIFIERS = ('', 'Ctrl', 'Alt', 'Shift', 'Ctrl+Alt', 'Ctrl+Shift', 'Alt+Shift', 'Ctrl+Alt+Shift')
# the colour of the mark of a changed shortcut: the one of the changed settings of the other pages
MARK_COLOR = QtGui.QColor(224, 138, 0)


def ltr(text):
    """A key sequence stays left to right in a right-to-left interface ('Ctrl+-' must not become '-+Ctrl')."""
    return '‪%s‬' % text if text else ''


def modifier_text(modifiers):
    """The modifiers as Qt shows them on this system ('Ctrl+Alt' on Windows and Linux, the symbols on macOS)."""
    if not modifiers:
        return _("No modifier")
    text = shortcuts.native_text(shortcuts.join(modifiers, 'A'))
    return text[:-1].rstrip('+')


def _dot_icon():
    pixmap = QtGui.QPixmap(10, 10)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(MARK_COLOR)
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    return QtGui.QIcon(pixmap)


class KeyField(QtWidgets.QLineEdit):
    """A field that takes the key that is pressed (also Tab, Esc and the keys of a menu) instead of typing it."""

    captured = QtCore.pyqtSignal(str)       # the portable text of the key with its modifiers

    def __init__(self, placeholder):
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText(placeholder)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)     # a key sequence reads left to right
        self.setProperty('no_edit_tracking', True)
        self.sequence = ''

    def set_sequence(self, sequence, shown=None):
        self.sequence = sequence
        self.setText(shortcuts.native_text(shown if shown is not None else sequence))

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.ShortcutOverride:
            event.accept()      # the key comes to keyPressEvent, not to a shortcut of a menu
            return True
        if event.type() == QtCore.QEvent.Type.KeyPress:
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        sequence = shortcuts.event_text(event)
        if sequence:
            self.set_sequence(sequence)
            self.captured.emit(sequence)
        event.accept()


class ShortcutsEditor(QtWidgets.QWidget):
    """The field of the option 'global_shortcuts': the table of the shortcuts and the editing of a key."""

    valueChanged = QtCore.pyqtSignal()      # an edit: the Preferences window marks the page and the Apply button

    marks_itself = True     # the Preferences window does not draw its bar over the field: the rows are marked

    COLUMN_COMMAND, COLUMN_SHORTCUT, COLUMN_CONTEXT = range(3)

    def __init__(self, registry=None):
        super().__init__()
        self.registry = registry or shortcuts.registry()
        self.keys = {aid: action.default for aid, action in self.registry.actions.items()}
        self._pending = None        # (action id, key sequence, [the actions that use it]) of the conflict shown
        self._dot = _dot_icon()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ------------------------------------------ search --------------------------------------------------------
        search_row = QtWidgets.QHBoxLayout()
        self.search_entry = QtWidgets.QLineEdit()
        self.search_entry.setPlaceholderText(_("Search commands"))
        self.search_entry.setClearButtonEnabled(True)
        self.search_entry.setProperty('no_edit_tracking', True)
        self.search_entry.setToolTip(_("Show the commands whose name holds the text."))
        search_row.addWidget(self.search_entry, 3)

        self.find_key_entry = KeyField(_("Press a key to find it"))
        self.find_key_entry.setToolTip(_("Press a key combination: the table shows the commands that use it."))
        self.find_key_entry.setClearButtonEnabled(True)
        search_row.addWidget(self.find_key_entry, 2)

        self.context_combo = QtWidgets.QComboBox()
        self.context_combo.setProperty('no_edit_tracking', True)
        self.context_combo.addItem(_("All contexts"), '')
        for context in self.registry.contexts.values():
            if any(a.context == context.id for a in self.registry.actions.values()):
                self.context_combo.addItem(context.name, context.id)
        self.context_combo.setToolTip(_("Show the commands of one context."))
        search_row.addWidget(self.context_combo, 1)
        layout.addLayout(search_row)

        # ------------------------------------------ table ---------------------------------------------------------
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Command"), _("Shortcut"), _("Context")])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COLUMN_COMMAND, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLUMN_SHORTCUT, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLUMN_CONTEXT, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(260)
        self.table.setToolTip(_("Select a command, then choose the modifiers and press the new key below.\n"
                                "A grey command has a fixed key: it is listed so that its key is not used twice."))
        layout.addWidget(self.table, 1)

        self.empty_label = QtWidgets.QLabel()
        self.empty_label.setEnabled(False)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        # ------------------------------------------ edit ----------------------------------------------------------
        edit_row = QtWidgets.QHBoxLayout()
        self.command_label = QtWidgets.QLabel()
        edit_row.addWidget(self.command_label, 1)

        self.modifier_combo = QtWidgets.QComboBox()
        self.modifier_combo.setProperty('no_edit_tracking', True)
        for modifiers in MODIFIERS:
            self.modifier_combo.addItem(modifier_text(modifiers), modifiers)
        self.modifier_combo.setToolTip(_("The modifier keys of the shortcut (on macOS Ctrl is Cmd)."))
        edit_row.addWidget(self.modifier_combo)
        plus = QtWidgets.QLabel('+')
        edit_row.addWidget(plus)

        self.key_entry = KeyField(_("Press a key"))
        self.key_entry.setToolTip(_("Click here and press the key of the shortcut. The modifiers pressed with it\n"
                                    "are set in the box on the left."))
        self.key_entry.setMinimumWidth(110)
        edit_row.addWidget(self.key_entry)

        self.assign_button = QtWidgets.QPushButton(_("Assign"))
        self.assign_button.setToolTip(_("Give the key to the selected command."))
        edit_row.addWidget(self.assign_button)
        self.clear_button = QtWidgets.QPushButton(_("Clear"))
        self.clear_button.setToolTip(_("Remove the key of the selected command."))
        edit_row.addWidget(self.clear_button)
        layout.addLayout(edit_row)

        # the key in use: reported before it is assigned
        self.conflict_frame = QtWidgets.QFrame()
        conflict_lay = QtWidgets.QHBoxLayout(self.conflict_frame)
        conflict_lay.setContentsMargins(0, 0, 0, 0)
        self.conflict_icon = QtWidgets.QLabel()
        self.conflict_icon.setPixmap(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(16, 16))
        conflict_lay.addWidget(self.conflict_icon)
        self.conflict_label = QtWidgets.QLabel()
        self.conflict_label.setWordWrap(True)
        conflict_lay.addWidget(self.conflict_label, 1)
        self.replace_button = QtWidgets.QPushButton(_("Assign it here and clear the other"))
        conflict_lay.addWidget(self.replace_button)
        self.cancel_button = QtWidgets.QPushButton(_("Cancel"))
        conflict_lay.addWidget(self.cancel_button)
        self.conflict_frame.hide()
        layout.addWidget(self.conflict_frame)

        # ------------------------------------------ buttons -------------------------------------------------------
        buttons = QtWidgets.QHBoxLayout()
        self.reset_button = QtWidgets.QPushButton(_("Reset shortcut"))
        self.reset_button.setToolTip(_("Give the selected command its factory key."))
        buttons.addWidget(self.reset_button)
        self.reset_all_button = QtWidgets.QPushButton(_("Reset all shortcuts"))
        self.reset_all_button.setToolTip(_("Give every command its factory key."))
        buttons.addWidget(self.reset_all_button)
        buttons.addStretch(1)
        self.import_button = QtWidgets.QPushButton(_("Import..."))
        self.import_button.setToolTip(_("Load the shortcuts from a JSON file."))
        buttons.addWidget(self.import_button)
        self.export_button = QtWidgets.QPushButton(_("Export..."))
        self.export_button.setToolTip(_("Save the shortcuts that differ from the factory keys in a JSON file."))
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)

        self._fill_table()
        self.search_entry.textChanged.connect(self.apply_filter)
        self.find_key_entry.captured.connect(lambda _seq: self.apply_filter())
        self.find_key_entry.textChanged.connect(self._on_find_key_text)
        self.context_combo.currentIndexChanged.connect(lambda _i: self.apply_filter())
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.key_entry.captured.connect(self._on_key_captured)
        self.assign_button.clicked.connect(lambda: self.assign())
        self.clear_button.clicked.connect(lambda: self.request(self.current_id(), ''))
        self.replace_button.clicked.connect(self.confirm)
        self.cancel_button.clicked.connect(self.cancel)
        self.reset_button.clicked.connect(lambda: self.reset(self.current_id()))
        self.reset_all_button.clicked.connect(self.reset_all)
        self.import_button.clicked.connect(lambda: self.import_file())
        self.export_button.clicked.connect(lambda: self.export_file())
        self._on_selection()

    # ------------------------------------------------------------------------------------------------------------
    # the value of the form
    # ------------------------------------------------------------------------------------------------------------
    def get_value(self):
        """{action id: key sequence} of the keys that differ from the factory keys."""
        return {aid: key for aid, key in self.keys.items()
                if self.registry.actions[aid].editable and key != self.registry.actions[aid].default}

    def set_value(self, value):
        self.keys = {aid: action.default for aid, action in self.registry.actions.items()}
        if isinstance(value, dict):
            for aid, sequence in value.items():
                action = self.registry.actions.get(aid)
                if action is not None and action.editable and isinstance(sequence, str):
                    self.keys[aid] = shortcuts.normalize(sequence)
        self.cancel()
        self._refresh_rows()

    def _changed(self):
        self._refresh_rows()
        self._on_selection()
        aid = self.current_id()
        if aid is not None:
            self.table.scrollToItem(self.table.item(self.rows[aid], self.COLUMN_COMMAND),
                                    QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
        self.valueChanged.emit()

    # ------------------------------------------------------------------------------------------------------------
    # the table
    # ------------------------------------------------------------------------------------------------------------
    def _context_text(self, action):
        name = self.registry.contexts[action.context].name
        if action.source == shortcuts.SOURCE_CANVAS:
            return '%s (%s)' % (name, _("Plot Area"))
        if action.source == shortcuts.SOURCE_PROJECT:
            return '%s (%s)' % (name, _("Project"))
        return name

    def _fill_table(self):
        order = list(self.registry.contexts)
        actions = sorted(self.registry.actions.values(),
                         key=lambda a: (order.index(a.context), not a.editable, a.name.casefold()))
        self.table.setRowCount(len(actions))
        self.rows = {}
        for row, action in enumerate(actions):
            command = QtWidgets.QTableWidgetItem(action.name)
            command.setData(Qt.ItemDataRole.UserRole, action.id)
            command.setToolTip(action.name)
            shortcut = QtWidgets.QTableWidgetItem()
            context = QtWidgets.QTableWidgetItem(self._context_text(action))
            if not action.editable:
                tip = _("This key is handled by its own code; it is listed so that it is not used twice.")
                for item in (command, shortcut, context):
                    item.setForeground(self.palette().brush(QtGui.QPalette.ColorGroup.Disabled,
                                                            QtGui.QPalette.ColorRole.Text))
                    item.setToolTip(tip)
            self.table.setItem(row, self.COLUMN_COMMAND, command)
            self.table.setItem(row, self.COLUMN_SHORTCUT, shortcut)
            self.table.setItem(row, self.COLUMN_CONTEXT, context)
            self.rows[action.id] = row
        self._refresh_rows()

    def _refresh_rows(self):
        bold = self.table.font()
        bold.setBold(True)
        for aid, row in self.rows.items():
            action = self.registry.actions[aid]
            key = self.keys[aid]
            item = self.table.item(row, self.COLUMN_SHORTCUT)
            item.setText(ltr(shortcuts.native_text(key)))
            item.setData(Qt.ItemDataRole.UserRole, key)
            changed = action.editable and key != action.default
            item.setFont(bold if changed else self.table.font())
            command = self.table.item(row, self.COLUMN_COMMAND)
            command.setIcon(self._dot if changed else QtGui.QIcon())
            if action.editable:
                factory = shortcuts.native_text(action.default) or _("None")
                item.setToolTip('%s: %s' % (_("Factory key"), ltr(factory)))

    def apply_filter(self):
        """Show the rows that match the search text, the key of 'find by key' and the context."""
        text = self.search_entry.text().strip().casefold()
        key = self.find_key_entry.sequence if self.find_key_entry.text() else ''
        context = self.context_combo.currentData()
        shown = 0
        for aid, row in self.rows.items():
            action = self.registry.actions[aid]
            visible = ((not text or text in action.name.casefold()) and (not key or self.keys[aid] == key) and
                       (not context or action.context == context))
            self.table.setRowHidden(row, not visible)
            shown += visible
        if self.current_id() is not None and self.table.isRowHidden(self.rows[self.current_id()]):
            self.table.clearSelection()     # the command that is edited below is always one of the table
        if key and not shown:
            self.empty_label.setText(_("No command uses %s.") % ltr(shortcuts.native_text(key)))
        self.empty_label.setVisible(bool(key) and not shown)
        return shown

    def _on_find_key_text(self, text):
        if not text:        # the clear button of the field
            self.find_key_entry.sequence = ''
            self.apply_filter()

    def visible_ids(self):
        return [aid for aid, row in sorted(self.rows.items(), key=lambda item: item[1])
                if not self.table.isRowHidden(row)]

    def current_id(self):
        items = self.table.selectedItems()
        if not items:
            return None
        return self.table.item(items[0].row(), self.COLUMN_COMMAND).data(Qt.ItemDataRole.UserRole)

    def select(self, aid):
        # the whole row is selected (SelectRows); selectRow() would unselect it again in a right-to-left table
        self.table.setCurrentCell(self.rows[aid], self.COLUMN_COMMAND)
        self.table.scrollToItem(self.table.item(self.rows[aid], self.COLUMN_COMMAND),
                                QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _on_selection(self):
        aid = self.current_id()
        action = self.registry.actions.get(aid) if aid else None
        editable = action is not None and action.editable
        for widget in (self.modifier_combo, self.key_entry, self.assign_button, self.clear_button,
                       self.reset_button):
            widget.setEnabled(editable)
        if action is None:
            self.command_label.setText(_("Select a command in the table."))
            self.key_entry.set_sequence('')
            return
        self.command_label.setText('<b>%s</b>' % action.name.replace('&', '&amp;').replace('<', '&lt;'))
        key = self.keys[aid]
        index = self.modifier_combo.findData(shortcuts.modifiers_of(key))
        self.modifier_combo.setCurrentIndex(max(index, 0))
        self.key_entry.set_sequence(shortcuts.key_of(key))
        self.reset_button.setEnabled(editable and key != action.default)

    def _on_key_captured(self, sequence):
        """
        The key pressed in the key field stays in the field; the modifiers pressed with it go to the combo box (a key
        pressed alone keeps the modifiers chosen in the combo box).
        """
        modifiers = shortcuts.modifiers_of(sequence)
        index = self.modifier_combo.findData(modifiers)
        if modifiers and index >= 0:
            self.modifier_combo.setCurrentIndex(index)
        self.key_entry.set_sequence(shortcuts.key_of(sequence))

    # ------------------------------------------------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------------------------------------------------
    def assign(self):
        """The Assign button: the modifiers of the combo box with the key of the key field."""
        aid = self.current_id()
        if aid is None or not self.key_entry.sequence:
            return False
        return self.request(aid, shortcuts.join(self.modifier_combo.currentData(), self.key_entry.sequence))

    def request(self, aid, sequence):
        """
        Give the key to the action, unless an action already uses it where this one works: then the conflict is shown
        and the key is given only by "Assign it here and clear the other". Returns True if the key was given.
        """
        if aid is None or not self.registry.actions[aid].editable:
            return False
        sequence = shortcuts.normalize(sequence)
        self.cancel()
        conflicts = self.registry.conflicts(aid, sequence, keys=self.keys)
        if not conflicts:
            self.keys[aid] = sequence
            self._changed()
            return True
        self._pending = (aid, sequence, conflicts)
        names = ', '.join('"%s" (%s)' % (other.name, self._context_text(other)) for other in conflicts)
        fixed = [other for other in conflicts if not other.editable]
        text = _('"%s" is already used by %s.') % (ltr(shortcuts.native_text(sequence)), names)
        if fixed:
            text += ' ' + _("That key cannot be changed here; choose another key.")
        self.conflict_label.setText(text)
        self.replace_button.setVisible(not fixed)
        self.conflict_frame.show()
        return False

    def confirm(self):
        """Assign it here and clear the other."""
        if self._pending is None:
            return
        aid, sequence, conflicts = self._pending
        for other in conflicts:
            self.keys[other.id] = ''
        self.keys[aid] = sequence
        self.cancel()
        self._changed()

    def cancel(self):
        self._pending = None
        self.conflict_frame.hide()

    def conflict_text(self):
        return self.conflict_label.text() if self.conflict_frame.isVisibleTo(self) else ''

    def reset(self, aid):
        if aid is None or not self.registry.actions[aid].editable:
            return
        self.cancel()
        self.keys[aid] = self.registry.actions[aid].default
        self._changed()

    def reset_all(self):
        self.cancel()
        self.keys = {aid: action.default for aid, action in self.registry.actions.items()}
        self._changed()

    # ------------------------------------------------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------------------------------------------------
    def import_file(self, filename=None):
        if filename is None:
            filename, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, _("Import the keyboard shortcuts"), '', "JSON (*.json);;%s (*.*)" % _("All Files"))
        if not filename:
            return False
        try:
            with open(filename, encoding='utf-8') as f:
                keys = self.registry.from_json(f.read())
        except (OSError, ValueError) as err:
            QtWidgets.QMessageBox.warning(self, _("Import the keyboard shortcuts"),
                                          '%s\n%s' % (_("The file could not be read."), str(err)))
            return False
        self.cancel()
        self.keys = {aid: action.default for aid, action in self.registry.actions.items()}
        self.keys.update(keys)
        self._changed()
        return True

    def export_file(self, filename=None):
        if filename is None:
            filename, _filter = QtWidgets.QFileDialog.getSaveFileName(
                self, _("Export the keyboard shortcuts"), 'shortcuts.json', "JSON (*.json)")
        if not filename:
            return False
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.registry.to_json(self.get_value()))
        return True


class GeneralShortcutsGroupUI(OptionsGroupUI):
    """The Keyboard Shortcuts page of the Preferences window."""

    full_width = True       # the table uses the width of the window

    def __init__(self, app, parent=None):
        super().__init__(self, parent=parent)
        self.setTitle(_("Keyboard Shortcuts"))
        self.app = app
        self.shortcuts_editor = ShortcutsEditor()
        self.shortcuts_editor.setToolTip(
            _("Select a command and press its new key. A key already in use where the command works\n"
              "is reported before it is assigned. The new keys work after Save or Apply."))
        self.layout.addWidget(self.shortcuts_editor, 1)
