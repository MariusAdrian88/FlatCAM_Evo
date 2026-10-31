"""EditorStatusBar: cursor position, selection, line count, language, line ending, modified mark and zoom."""

from PyQt6 import QtWidgets

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class EditorStatusBar(QtWidgets.QWidget):

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(14)
        self.position_label = QtWidgets.QLabel()
        self.selection_label = QtWidgets.QLabel()
        self.lines_label = QtWidgets.QLabel()
        self.language_label = QtWidgets.QLabel()
        self.eol_label = QtWidgets.QLabel('LF')
        self.modified_label = QtWidgets.QLabel()
        self.zoom_label = QtWidgets.QLabel('100%')
        layout.addWidget(self.position_label)
        layout.addWidget(self.selection_label)
        layout.addStretch(1)
        layout.addWidget(self.lines_label)
        layout.addWidget(self.language_label)
        layout.addWidget(self.eol_label)
        layout.addWidget(self.modified_label)
        layout.addWidget(self.zoom_label)

        editor.cursorPositionChanged.connect(self.update_cursor)
        editor.selectionChanged.connect(self.update_cursor)
        editor.blockCountChanged.connect(self.update_lines)
        editor.document().modificationChanged.connect(self.update_modified)
        if hasattr(editor, 'zoomChanged'):
            editor.zoomChanged.connect(self.set_zoom)
        self.refresh()

    def refresh(self):
        self.update_cursor()
        self.update_lines()
        self.update_modified()

    def update_cursor(self):
        cursor = self.editor.textCursor()
        self.position_label.setText(_("Ln %d, Col %d") % (cursor.blockNumber() + 1, cursor.positionInBlock() + 1))
        length = cursor.selectionEnd() - cursor.selectionStart()
        self.selection_label.setText(_("%d selected") % length if length else '')

    def update_lines(self, *args):
        self.lines_label.setText(_("%d lines") % self.editor.blockCount())

    def update_modified(self, *args):
        self.modified_label.setText('●' if self.editor.document().isModified() else '')
        self.modified_label.setToolTip(_("Modified") if self.editor.document().isModified() else '')

    def set_language(self, name):
        self.language_label.setText(name)

    def set_eol(self, eol):
        self.eol_label.setText(eol)

    def set_zoom(self, percent):
        self.zoom_label.setText('%d%%' % percent)
