"""
The code editor (CodeEdit, AppTextEditor in the code mode): lazy highlighting of the lines shown, Go to Line, Find and
Replace, editing aids. Qt highlighted every line when the text was set: the View Source of a G-code of 94000 lines
took 11 s.

    python tests/test_text_editor.py
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets          # noqa: E402

from appEditors.appTextEditor import AppTextEditor          # noqa: E402
from appEditors.text_editor.code_edit import CodeEdit       # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
Key = QtCore.Qt.Key
Mod = QtCore.Qt.KeyboardModifier


def gcode(lines):
    rows = ['(G-code test)', 'G21', 'G90']
    for k in range(lines):
        rows.append('G1 X%.4f Y%.4f F1000.00' % (k * 0.01, -k * 0.02))
    return '\n'.join(rows)


def pump():
    for __ in range(5):
        APP.processEvents()


def make_editor():
    app = SimpleNamespace(options={'global_theme': 'default'}, defaults=Mock(),
                          resource_location='assets/resources', regFK=SimpleNamespace(myKeywords=[]))
    return AppTextEditor(app=app, plain_text=True)


class TestGotoLine(unittest.TestCase):
    """Go to Line (Ctrl+G, MainGUI.goto_text_line) goes to the line with that number in the number bar."""

    def setUp(self):
        rows = []
        for k, row in enumerate(gcode(300).split('\n')):
            if k % 10 == 0:
                # a long comment: it wraps in a 400 px wide editor
                rows.append('(%s)' % ('a long comment that wraps in a narrow editor ' * 4))
            rows.append(row)
        self.edit = CodeEdit(theme='default')
        self.edit.set_word_wrap(True)
        self.edit.resize(400, 300)
        self.edit.show()
        pump()
        self.edit.load('\n'.join(rows))
        pump()

    def tearDown(self):
        self.edit.close()
        self.edit.deleteLater()
        pump()

    def goto(self, text, ok=True):
        """Go to Line from line 100 with `text` in the dialog; returns the line of the cursor and the messages."""
        from unittest.mock import patch
        from appGUI.MainGUI import MainGUI

        self.edit.setTextCursor(QtGui.QTextCursor(self.edit.document().findBlockByNumber(99)))
        inform = Mock()
        editor_tab = SimpleNamespace(code_editor=self.edit)
        gui = SimpleNamespace(plot_tab_area=SimpleNamespace(currentWidget=lambda: editor_tab),
                              app=SimpleNamespace(resource_location='', inform=SimpleNamespace(emit=inform)))
        with patch('appGUI.MainGUI.Dialog_box', return_value=SimpleNamespace(location=text, ok=ok)):
            MainGUI.goto_text_line(gui)
        return self.edit.textCursor().blockNumber() + 1, [call.args[0] for call in inform.call_args_list]

    def test_goes_to_the_numbered_line_when_long_lines_wrap(self):
        # the wrapped parts of the long lines were counted as lines: Go to Line 200 stopped above line 200
        self.assertNotEqual(self.edit.document().findBlockByLineNumber(199).blockNumber(), 199)
        self.assertEqual(self.goto('200'), (200, []))
        last = self.edit.document().blockCount()
        self.assertEqual(self.goto(str(last)), (last, []))

class FindCase(unittest.TestCase):

    def setUp(self):
        self.editor = make_editor()
        self.edit = self.editor.code_editor
        self.bar = self.editor.find_bar

    def tearDown(self):
        self.editor.deleteLater()
        pump()

    def find(self, text, case=False, word=False, regex=False):
        self.bar.case_btn.setChecked(case)
        self.bar.word_btn.setChecked(word)
        self.bar.regex_btn.setChecked(regex)
        self.editor.entryFind.set_value(text)

    def selected(self):
        return self.edit.textCursor().selectedText()


class TestFind(FindCase):

    def test_count_follows_case_whole_word_and_regex(self):
        self.editor.load_text('Foo foo FOO food', move_to_start=True)
        for args, count in (((('foo',), {}), 4), ((('foo',), {'case': True}), 2),
                            ((('foo',), {'word': True}), 3), ((('fo+d?',), {'regex': True}), 4)):
            self.find(*args[0], **args[1])
            self.assertEqual(self.bar.match_count(), count, args)

        # an invalid regex finds nothing and does not raise
        self.editor.load_text('a(b', move_to_start=True)
        self.find('(', regex=True)
        self.assertEqual(self.bar.match_count(), 0)
        self.assertFalse(self.bar.find_next())

    def test_a_new_search_after_a_shown_match_does_not_recurse(self):
        # the clear of the shown matches emits updateRequest, which cleared them again without end (a crash)
        errors = []
        hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: errors.append(value)
        try:
            self.editor.resize(700, 400)
            self.editor.show()
            self.editor.load_text('\n'.join('G1 X%d S%d' % (k, 45 if k % 2 else 0) for k in range(200)),
                                  move_to_start=True)
            self.bar.open_bar()
            for text, count in (('S', 200), ('S4', 100), ('S40', 0), ('S45', 100)):
                self.find(text)
                self.bar.update_results()
                self.bar.find_next()
                pump()
                self.assertEqual(self.bar.match_count(), count, text)
        finally:
            sys.excepthook = hook
            self.editor.close()
        self.assertEqual(errors, [])

class TestReplaceAll(FindCase):
    """Replace All of AppTextEditor (G-code editor): every match from the document start, in one Undo step."""

    def replace_all(self, old, new, regex=False):
        self.find(old, case=True, regex=regex)
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

        # the find text inside the new text
        self.editor.load_text('S1 S1', move_to_start=True)
        self.replace_all('S1', 'S10')
        self.assertEqual(self.edit.toPlainText(), 'S10 S10')

        # regex groups in the replace text
        self.editor.load_text('X1 Y2\nX3 Y4', move_to_start=True)
        self.replace_all(r'X(\d) Y(\d)', r'Y\2 X\1', regex=True)
        self.assertEqual(self.edit.toPlainText(), 'Y2 X1\nY4 X3')
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), 'X1 Y2\nX3 Y4')


class TestEditingAids(unittest.TestCase):

    def setUp(self):
        self.edit = CodeEdit(theme='default')
        self.edit.resize(400, 300)
        self.edit.show()
        pump()

    def tearDown(self):
        self.edit.close()
        self.edit.deleteLater()
        pump()

    def run_aid(self, text, select, key, mods=Mod.NoModifier):
        """Types `key` with `text` loaded and `select` = (anchor, position); returns the text after the key."""
        self.edit.load(text)
        cursor = self.edit.textCursor()
        cursor.setPosition(select[0])
        cursor.setPosition(select[1], QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.edit.setTextCursor(cursor)
        QtTest.QTest.keyClick(self.edit, key, mods)
        return self.edit.toPlainText()

    def test_each_aid_gives_the_right_text_in_one_undo_step(self):
        cases = (
            ('indent', 'a\nb', (0, 3), Key.Key_Tab, Mod.NoModifier, '    a\n    b'),
            ('tab stop', 'ab', (2, 2), Key.Key_Tab, Mod.NoModifier, 'ab  '),
            ('unindent', '    a\n\tb', (0, 7), Key.Key_Backtab, Mod.ShiftModifier, 'a\nb'),
            ('duplicate', 'a\nb', (0, 0), Key.Key_D, Mod.ControlModifier, 'a\na\nb'),
            ('move down', 'a\nb\nc', (0, 0), Key.Key_Down, Mod.AltModifier, 'b\na\nc'),
            ('move up', 'a\nb\nc', (4, 4), Key.Key_Up, Mod.AltModifier, 'a\nc\nb'),
            ('comment', 'G1 X1\n  G0 Z5', (0, 13), Key.Key_Slash, Mod.ControlModifier, '(G1 X1)\n(  G0 Z5)'),
            ('auto-indent', '  G1', (4, 4), Key.Key_Return, Mod.NoModifier, '  G1\n  '),
        )
        for name, text, select, key, mods, expected in cases:
            with self.subTest(name):
                self.assertEqual(self.run_aid(text, select, key, mods), expected)
                self.edit.undo()
                self.assertEqual(self.edit.toPlainText(), text)

# (language, one line, [(character position, token whose color it must have)])
def tool_gcode(tools=3, moves=50):
    rows = ['(head)', 'G21', 'G90']
    for tool in range(1, tools + 1):
        rows += ['T%d' % tool, 'M6'] + ['G1 X%d Y%d' % (k, tool) for k in range(moves)]
    return '\n'.join(rows)


class FoldCase(unittest.TestCase):
    TEXT = tool_gcode()         # regions: (0, 2), (3, 54), (55, 106), (107, 158)
    LEXER = 'gcode'

    def setUp(self):
        from appEditors.text_editor.lexers import get_lexer
        self.edit = CodeEdit(theme='default', lexer=get_lexer(self.LEXER))
        self.edit.resize(600, 400)
        self.edit.show()
        pump()
        self.edit.load(self.TEXT)
        pump()
        self.edit.fold.rescan()

    def tearDown(self):
        self.edit.close()
        self.edit.deleteLater()
        pump()

    def hidden(self):
        block = self.edit.document().firstBlock()
        numbers = []
        while block.isValid():
            if not block.isVisible():
                numbers.append(block.blockNumber())
            block = block.next()
        return numbers


class TestFolding(FoldCase):

    def test_fold_hides_the_inner_lines_and_unfold_shows_them(self):
        fold = self.edit.fold
        bar = self.edit.verticalScrollBar()
        full = bar.maximum()
        self.assertTrue(fold.fold(3))
        pump()
        self.assertEqual(self.hidden(), list(range(4, 55)))
        self.assertTrue(fold.is_folded(3))
        # the hidden lines are not scrolled through
        self.assertEqual(bar.maximum(), full - 51)
        self.assertTrue(fold.unfold(3))
        pump()
        self.assertEqual((self.hidden(), bar.maximum()), ([], full))
        # a line that starts no region cannot be folded
        self.assertFalse(fold.fold(10))

        # fold all and unfold all
        fold = self.edit.fold
        fold.fold_all()
        self.assertEqual([n for n in range(160) if fold.is_folded(n)], [0, 3, 55, 107])
        self.assertEqual(len(self.hidden()), 2 + 51 * 3)
        fold.unfold_all()
        self.assertEqual(self.hidden(), [])
        # the lines hidden by the fold get their colors when they are shown again
        pump()
        self.assertTrue(self.edit.document().findBlockByNumber(5).layout().formats())

    def test_goto_line_and_arrow_keys_never_leave_the_cursor_in_hidden_text(self):
        fold = self.edit.fold
        fold.fold(3)
        self.assertTrue(self.edit.goto_line(10))
        self.assertEqual((self.edit.textCursor().blockNumber(), self.hidden()), (9, []))
        fold.fold(3)
        self.edit.setTextCursor(QtGui.QTextCursor(self.edit.document().findBlockByNumber(3)))
        QtTest.QTest.keyClick(self.edit, Key.Key_Down)
        self.assertTrue(self.edit.textCursor().block().isVisible())

if __name__ == '__main__':
    unittest.main()
