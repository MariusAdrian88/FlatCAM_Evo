"""
The code editor of FCTextAreaLineNumber (Source Editor, G-code editor) highlights only the lines that are shown; the
other lines are highlighted when they are scrolled into view. Qt highlighted every line when the text was set: the
View Source of a G-code of 94000 lines took 11 s.
Replace All of the editor (AppTextEditor) replaces every match in one Undo step.

    python tests/test_lazy_highlight.py
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtGui, QtWidgets                        # noqa: E402

from appEditors.appTextEditor import AppTextEditor         # noqa: E402
from appGUI.GUIElements import FCTextAreaLineNumber        # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def gcode(lines):
    rows = ['(G-code test)', 'G21', 'G90']
    for k in range(lines):
        rows.append('G1 X%.4f Y%.4f F1000.00' % (k * 0.01, -k * 0.02))
    return '\n'.join(rows)


def pump():
    for __ in range(5):
        APP.processEvents()


class EditorCase(unittest.TestCase):
    LINES = 5000

    def setUp(self):
        self.frame = FCTextAreaLineNumber(theme='default')
        self.frame.resize(600, 400)
        self.frame.show()
        self.edit = self.frame.edit
        pump()
        self.edit.setPlainText(gcode(self.LINES))
        pump()

    def tearDown(self):
        self.frame.close()
        self.frame.deleteLater()
        pump()

    def block(self, number):
        return self.edit.document().findBlockByNumber(number)

    def formatted(self, number):
        return bool(self.block(number).layout().formats())

    def scroll_to(self, number):
        self.edit.verticalScrollBar().setValue(number)
        pump()

    def replace_line(self, number, text):
        # what Replace does to a line that is not shown
        cursor = QtGui.QTextCursor(self.block(number))
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock, QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        pump()


class TestLazyHighlight(EditorCase):

    def test_only_the_lines_shown_are_highlighted_when_the_text_is_set(self):
        self.assertTrue(self.formatted(5))
        self.assertFalse(self.formatted(4000))
        self.assertEqual(self.block(4000).userState(), -1)
        self.assertLess(self.edit.shown_blocks[1], 4000)

    def test_lines_are_highlighted_when_scrolled_into_view(self):
        self.scroll_to(4000)
        shown = self.edit.firstVisibleBlock().blockNumber()
        self.assertTrue(shown >= 3990)
        # every line shown (a 400 px high editor shows more than 15 lines)
        for number in range(shown, shown + 15):
            self.assertTrue(self.formatted(number), number)
        # the lines highlighted before stay highlighted
        self.assertTrue(self.formatted(5))

    def test_typed_line_is_highlighted(self):
        self.edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.edit.insertPlainText('\nG0 X1.0000 Y2.0000')
        pump()
        self.assertTrue(self.formatted(self.edit.document().blockCount() - 1))

    def test_changed_line_not_shown_keeps_its_colors(self):
        self.scroll_to(1000)
        for number in (1001, 1002, 1003):
            self.assertTrue(self.formatted(number))
        self.scroll_to(0)
        self.assertEqual(self.edit.firstVisibleBlock().blockNumber(), 0)

        self.replace_line(1001, 'G1 X1.0000 Y2.0000 F800.00')
        self.assertTrue(self.formatted(1001))
        self.assertTrue(self.formatted(1002))

        # Undo: the line keeps its colors
        self.edit.undo()
        pump()
        self.assertIn('F1000.00', self.block(1001).text())
        self.assertTrue(self.formatted(1001))

class TestPrint(EditorCase):
    LINES = 400

    def test_printed_document_is_highlighted_on_every_line(self):
        self.assertFalse(self.formatted(350))
        changes = []
        self.edit.textChanged.connect(lambda: changes.append(1))
        with tempfile.TemporaryDirectory() as folder:
            self.edit.print(QtGui.QPdfWriter(os.path.join(folder, 'print.pdf')))
        self.assertEqual(changes, [])
        for number in (5, 350, self.LINES + 2):
            self.assertTrue(self.formatted(number), number)
        # the highlighting of the lines shown goes on
        self.assertIs(self.edit.highlighter.editor, self.edit)
        self.assertFalse(self.edit.highlighting_shown)


class TestReplaceAll(unittest.TestCase):
    """Replace All of AppTextEditor (G-code editor): every match from the document start, in one Undo step."""

    def setUp(self):
        app = SimpleNamespace(options={'global_theme': 'default'}, defaults=Mock(),
                              resource_location='assets/resources', regFK=SimpleNamespace(myKeywords=[]))
        self.editor = AppTextEditor(app=app, plain_text=True)
        self.edit = self.editor.code_editor

    def tearDown(self):
        self.editor.deleteLater()
        pump()

    def replace_all(self, old, new):
        self.editor.entryFind.set_value(old)
        self.editor.entryReplace.set_value(new)
        self.editor.sel_all_cb.setChecked(True)
        self.editor.handleReplaceGCode()

    def test_replace_all_after_find_replaces_every_match_in_one_undo_step(self):
        self.editor.load_text('G1 S100\nG1 S100\nG1 S100', move_to_start=True)
        self.editor.entryFind.set_value('S100')
        # Find selects the first match: Replace All left it (and every match above the cursor) unchanged
        self.editor.handleFindGCode()
        self.replace_all('S100', 'S500')
        self.assertEqual(self.edit.toPlainText(), 'G1 S500\nG1 S500\nG1 S500')
        # a later edit still marks the text as not saved (an open edit block stopped the textChanged signal) and it
        # is an Undo step of its own
        self.editor.buttonSave.setStyleSheet('')
        self.edit.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        self.edit.insertPlainText('\nG0 X1')
        self.assertIn('red', self.editor.buttonSave.styleSheet())
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), 'G1 S500\nG1 S500\nG1 S500')
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), 'G1 S100\nG1 S100\nG1 S100')

    def test_replace_all_with_the_find_text_inside_the_new_text(self):
        self.editor.load_text('S1 S1', move_to_start=True)
        self.replace_all('S1', 'S10')
        self.assertEqual(self.edit.toPlainText(), 'S10 S10')


if __name__ == '__main__':
    unittest.main()
