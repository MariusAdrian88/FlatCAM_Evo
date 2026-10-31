"""
The code editor (CodeEdit, AppTextEditor in the code mode): lazy highlighting of the lines shown, Go to Line, Find and
Replace, editing aids. Qt highlighted every line when the text was set: the View Source of a G-code of 94000 lines
took 11 s.

    python tests/test_text_editor.py
"""

import os
import sys
import tempfile
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


class EditorCase(unittest.TestCase):
    LINES = 5000

    def setUp(self):
        self.edit = CodeEdit(theme='default')
        self.edit.resize(600, 400)
        self.edit.show()
        pump()
        self.edit.load(gcode(self.LINES))
        pump()

    def tearDown(self):
        self.edit.close()
        self.edit.deleteLater()
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

    def test_gcode_commands_and_parameters_are_highlighted(self):
        self.replace_line(5, 'G01 X1 Y2 Z-1 F100 M03 T1')
        formats = self.block(5).layout().formats()
        for position in (0, 11, 15, 20, 24):
            self.assertTrue(any(item.start <= position < item.start + item.length for item in formats), position)

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

    def test_load_is_not_an_edit(self):
        changes = []
        self.edit.textChanged.connect(lambda: changes.append(1))
        self.edit.load('G1 X1')
        self.assertEqual((changes, self.edit.toPlainText(), self.edit.document().isModified()), ([], 'G1 X1', False))
        self.assertFalse(self.edit.document().isUndoAvailable())


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

    def test_wrong_line_warns_and_keeps_the_cursor(self):
        # a text that is not a line number went to line 1 without a message
        for text in ('abc', '', '0', '100000'):
            line, messages = self.goto(text)
            self.assertEqual(line, 100, text)
            self.assertEqual(len(messages), 1, text)
            self.assertTrue(messages[0].startswith('[WARNING_NOTCL]'), text)
        # Cancel
        self.assertEqual(self.goto('200', ok=False), (100, []))

    def test_goto_line_method_refuses_a_wrong_line(self):
        self.assertTrue(self.edit.goto_line(50))
        for wrong in (0, 100000, 'x'):
            self.assertFalse(self.edit.goto_line(wrong))
            self.assertEqual(self.edit.textCursor().blockNumber(), 49)


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

    def test_invalid_regex_finds_nothing_and_does_not_raise(self):
        self.editor.load_text('a(b', move_to_start=True)
        self.find('(', regex=True)
        self.assertEqual(self.bar.match_count(), 0)
        self.assertFalse(self.bar.find_next())
        self.assertIn('border', self.editor.entryFind.styleSheet())

    def test_next_and_previous_wrap_around_without_a_dialog(self):
        self.editor.load_text('G1\nG2\nG1', move_to_start=True)
        self.find('G1', case=True)
        with unittest.mock.patch.object(QtWidgets.QDialog, 'exec', side_effect=AssertionError('dialog')):
            seen = []
            for __ in range(3):
                self.assertTrue(self.bar.find_next())
                seen.append(self.edit.textCursor().blockNumber())
            self.assertEqual(seen, [0, 2, 0])
            self.assertTrue(self.bar.find_previous())
            self.assertEqual(self.edit.textCursor().blockNumber(), 2)


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

    def test_replace_all_with_the_find_text_inside_the_new_text(self):
        self.editor.load_text('S1 S1', move_to_start=True)
        self.replace_all('S1', 'S10')
        self.assertEqual(self.edit.toPlainText(), 'S10 S10')

    def test_regex_groups_in_the_replace_text_and_one_undo_step(self):
        self.editor.load_text('X1 Y2\nX3 Y4', move_to_start=True)
        self.replace_all(r'X(\d) Y(\d)', r'Y\2 X\1', regex=True)
        self.assertEqual(self.edit.toPlainText(), 'Y2 X1\nY4 X3')
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), 'X1 Y2\nX3 Y4')

    def test_big_replace_all_is_one_undo_step(self):
        self.editor.load_text('G1\n' * 3000, move_to_start=True)
        self.replace_all('G1', 'G0')
        self.assertEqual(self.edit.toPlainText(), 'G0\n' * 3000)
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), 'G1\n' * 3000)


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

    def test_tab_without_a_selection_inserts_spaces_to_the_next_tab_stop(self):
        self.assertEqual(self.run_aid('ab', (2, 2), Key.Key_Tab), 'ab  ' + '')

    def test_toggle_comment_twice_gives_the_text_back(self):
        text = 'G1 X1\n\nG0 Z5'
        self.edit.load(text)
        self.edit.selectAll()
        self.edit.toggle_comment()
        self.assertEqual(self.edit.toPlainText(), '(G1 X1)\n\n(G0 Z5)')
        self.edit.toggle_comment()
        self.assertEqual(self.edit.toPlainText(), text)

    def test_zoom_keys_change_the_font_size(self):
        size = self.edit.font().pointSizeF()
        QtTest.QTest.keyClick(self.edit, Key.Key_Equal, Mod.ControlModifier)
        self.assertGreater(self.edit.font().pointSizeF(), size)
        QtTest.QTest.keyClick(self.edit, Key.Key_0, Mod.ControlModifier)
        self.assertEqual(self.edit.font().pointSizeF(), size)

    def test_bracket_at_the_cursor_is_matched(self):
        self.edit.load('G1 (a [b] c)')
        cursor = self.edit.textCursor()
        cursor.setPosition(4)
        self.edit.setTextCursor(cursor)
        self.assertEqual(self.edit.matching_brackets(), [3, 11])


# (language, one line, [(character position, token whose color it must have)])
TOKEN_SAMPLES = [
    ('gcode', 'N10 G01 X-1.5 Z-2 F100 M03 (note)', [(0, 'line_number'), (4, 'gcode_g'), (8, 'axis'),
                                                   (14, 'z_axis'), (19, 'feed'), (24, 'gcode_m'), (29, 'comment')]),
    ('gerber', 'X100Y200D01*', [(0, 'axis'), (8, 'gerber_dcode')]),
    ('gerber', '%FSLAX24Y24*%', [(3, 'gerber_extended')]),
    ('gerber', '%TF.FileFunction,Copper*%', [(3, 'attribute')]),
    ('gerber', 'G04 note*', [(5, 'comment')]),
    ('excellon', 'T01C0.8', [(0, 'tool'), (4, 'tool')]),
    ('excellon', 'X1.5Y-2.5 ;c', [(0, 'axis'), (6, 'axis'), (11, 'comment')]),
    ('tcl', 'set x $y -opt 5 ;# c', [(0, 'keyword'), (6, 'variable'), (9, 'param'), (14, 'number'), (19, 'comment')]),
]


class TestLexers(unittest.TestCase):

    def test_tokens_have_their_colors(self):
        from appEditors.text_editor.lexers import get_lexer
        from appEditors.text_editor.themes import palette_for
        colors = palette_for('default')
        for language, line, expected in TOKEN_SAMPLES:
            edit = CodeEdit(theme='default', lexer=get_lexer(language))
            edit.resize(600, 100)
            edit.show()
            edit.load(line)
            pump()
            formats = edit.document().firstBlock().layout().formats()
            for position, token in expected:
                color = [item.format.foreground().color().name() for item in formats
                         if item.start <= position < item.start + item.length]
                self.assertEqual(color, [getattr(colors, token)], (language, line, position, token))
            edit.close()
            edit.deleteLater()

    def test_tcl_command_names_of_the_app_are_highlighted(self):
        from appEditors.text_editor.lexers import get_lexer
        edit = CodeEdit(theme='default', lexer=get_lexer('tcl', ['open_gerber']))
        edit.show()
        edit.load('open_gerber file.gbr')
        pump()
        formats = edit.document().firstBlock().layout().formats()
        self.assertEqual(formats[0].format.foreground().color().name(), edit.colors.tcl_command)
        edit.close()
        edit.deleteLater()

    def test_fold_regions(self):
        from appEditors.text_editor.lexers import get_lexer
        gcode = '(head)\nG21\nG90\nT1\nM6\nG1 X1\nG1 X2\nT2 M6\nG1 X3\nM30'
        gerber = '%FSLAX24Y24*%\n%AMOC*\n1,1,0.5,0,0*%\nG36*\nX1Y1D02*\nX2Y2D01*\nG37*\nM02*'
        excellon = 'M48\nMETRIC\nT1C0.8\nT2C1.0\n%\nT1\nX1Y1\nX2Y2\nT2\nX3Y3\nM30'
        tcl = 'proc a {} {\n  # } not a brace\n  if {$x} {\n    puts "}"\n  }\n}\nset y 1'
        for language, text, expected in (
                ('gcode', gcode, [(0, 2), (3, 6), (7, 9)]),
                ('gerber', gerber, [(1, 2), (3, 6)]),
                ('excellon', excellon, [(0, 4), (5, 7), (8, 9)]),
                ('tcl', tcl, [(0, 5), (2, 4)])):
            self.assertEqual(get_lexer(language).fold_regions(text), expected, language)
        self.assertEqual(get_lexer('tcl').fold_regions('{ { ( "'), [])
        self.assertEqual(get_lexer('plain').fold_regions(gcode), [])

    def test_language_is_detected_from_the_text(self):
        from appEditors.text_editor.lexers import detect_language
        for language, text in (
                ('gerber', '%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nX1Y1D02*\nM02*'),
                ('excellon', 'M48\nMETRIC,TZ\nT1C0.8\n%\nT1\nX1Y1\nM30'),
                ('gcode', '(test)\nG21\nG90\nG01 X1 Y2 Z-1 F100\nM03\nM05'),
                ('tcl', 'set x 1\nopen_gerber a.gbr -outname a\nproc f {} {}'),
                ('plain', 'hello world\nsome notes'),
                ('plain', '')):
            self.assertEqual(detect_language(text), language, language)

    def test_editor_detects_the_language_on_the_first_load_and_keeps_a_given_one(self):
        editor = make_editor()
        editor.load_text('%FSLAX24Y24*%\n%MOMM*%\nX1Y1D02*\nM02*')
        self.assertEqual((editor.language, editor.lexer.name), ('gerber', 'Gerber'))
        editor.load_text('G21\nG01 X1 Y1 Z-1')
        self.assertEqual(editor.language, 'gerber')
        editor.set_language('gcode')
        self.assertEqual(editor.lexer.name, 'G-code')
        app = SimpleNamespace(options={'global_theme': 'default'}, defaults=Mock(), resource_location='assets/resources')
        fixed = AppTextEditor(app=app, plain_text=True, language='tcl')
        fixed.load_text('G21\nG01 X1 Y1 Z-1')
        self.assertEqual(fixed.lexer.name, 'Tcl')


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

    def test_fold_all_and_unfold_all(self):
        fold = self.edit.fold
        fold.fold_all()
        self.assertEqual([n for n in range(160) if fold.is_folded(n)], [0, 3, 55, 107])
        self.assertEqual(len(self.hidden()), 2 + 51 * 3)
        fold.unfold_all()
        self.assertEqual(self.hidden(), [])

    def test_fold_with_the_keys_folds_the_region_of_the_cursor(self):
        self.edit.setTextCursor(QtGui.QTextCursor(self.edit.document().findBlockByNumber(60)))
        QtTest.QTest.keyClick(self.edit, Key.Key_BraceLeft, Mod.ControlModifier | Mod.ShiftModifier)
        self.assertEqual(self.edit.textCursor().blockNumber(), 55)
        self.assertEqual(self.hidden(), list(range(56, 107)))
        QtTest.QTest.keyClick(self.edit, Key.Key_BraceRight, Mod.ControlModifier | Mod.ShiftModifier)
        self.assertEqual(self.hidden(), [])

    def test_goto_line_and_arrow_keys_never_leave_the_cursor_in_hidden_text(self):
        fold = self.edit.fold
        fold.fold(3)
        self.assertTrue(self.edit.goto_line(10))
        self.assertEqual((self.edit.textCursor().blockNumber(), self.hidden()), (9, []))
        fold.fold(3)
        self.edit.setTextCursor(QtGui.QTextCursor(self.edit.document().findBlockByNumber(3)))
        QtTest.QTest.keyClick(self.edit, Key.Key_Down)
        self.assertTrue(self.edit.textCursor().block().isVisible())

    def test_edit_inside_a_fold_unfolds_it_and_an_edit_above_keeps_it(self):
        fold = self.edit.fold
        fold.fold(55)
        cursor = QtGui.QTextCursor(self.edit.document().findBlockByNumber(0))
        cursor.insertText('(first)\n')
        self.assertEqual((fold.is_folded(56), self.hidden()), (True, list(range(57, 108))))
        cursor = QtGui.QTextCursor(self.edit.document().findBlockByNumber(70))
        cursor.insertText('x')
        self.assertEqual((fold.is_folded(56), self.hidden()), (False, []))

    def test_rescan_keeps_a_fold_that_is_still_a_region_and_drops_the_others(self):
        fold = self.edit.fold
        fold.fold(3)
        fold.fold(107)
        # a line added above and a start line changed: after the debounce only the first fold is left
        QtGui.QTextCursor(self.edit.document().findBlockByNumber(0)).insertText('(first)\n')
        block = self.edit.document().findBlockByNumber(108)
        cursor = QtGui.QTextCursor(block)
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.EndOfBlock, QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText('G4 P1')
        QtTest.QTest.qWait(450)
        self.assertEqual(sorted(n for n in range(170) if fold.is_folded(n)), [4])
        self.assertEqual(self.hidden(), list(range(5, 56)))

    def test_nested_unfold_keeps_the_inner_fold(self):
        from appEditors.text_editor.lexers import get_lexer
        self.edit.set_lexer(get_lexer('tcl'))
        self.edit.load('proc a {} {\n  if {$x} {\n    puts 1\n  }\n}\nset y 1')
        fold = self.edit.fold
        fold.rescan()
        fold.fold(1)
        fold.fold(0)
        self.assertEqual(self.hidden(), [1, 2, 3, 4])
        fold.unfold(0)
        self.assertEqual(self.hidden(), [2, 3])

    def test_click_on_the_marker_toggles_the_fold(self):
        block = self.edit.document().findBlockByNumber(3)
        y = int(self.edit.blockBoundingGeometry(block).translated(self.edit.contentOffset()).top()) + 3
        point = QtCore.QPoint(self.edit.gutter.width() - 5, y)
        QtTest.QTest.mouseClick(self.edit.gutter, QtCore.Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(self.edit.fold.is_folded(3))
        QtTest.QTest.mouseClick(self.edit.gutter, QtCore.Qt.MouseButton.LeftButton, pos=point)
        self.assertFalse(self.edit.fold.is_folded(3))

    def test_print_prints_the_folded_lines_too(self):
        sizes = []
        with tempfile.TemporaryDirectory() as folder:
            for folded in (False, True):
                if folded:
                    self.edit.fold.fold_all()
                path = os.path.join(folder, 'print%d.pdf' % folded)
                self.edit.print(QtGui.QPdfWriter(path))
                sizes.append(os.path.getsize(path))
        self.assertEqual(sizes[0], sizes[1])
        # the folds are back after the print
        self.assertEqual(len(self.hidden()), 2 + 51 * 3)


class TestMinimap(unittest.TestCase):

    def setUp(self):
        self.editor = make_editor()
        self.editor.resize(700, 400)
        self.editor.show()
        pump()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        pump()

    def test_a_short_text_hides_the_minimap_and_a_long_one_shows_it(self):
        self.editor.button_minimap.setChecked(True)
        self.editor.load_text(gcode(100))
        self.assertTrue(self.editor.minimap.isHidden())
        self.editor.load_text(gcode(5000))
        self.assertFalse(self.editor.minimap.isHidden())
        self.assertTrue(self.editor.app.options['text_editor_minimap'])
        self.editor.button_minimap.setChecked(False)
        self.assertTrue(self.editor.minimap.isHidden())

    def test_click_scrolls_the_editor_to_the_part_clicked(self):
        self.editor.button_minimap.setChecked(True)
        self.editor.load_text(gcode(5000))
        pump()
        minimap = self.editor.minimap
        minimap.rebuild()
        bar = self.editor.code_editor.verticalScrollBar()
        for fraction in (0.5, 0.9):
            point = QtCore.QPoint(40, int(minimap.height() * fraction))
            QtTest.QTest.mouseClick(minimap, QtCore.Qt.MouseButton.LeftButton, pos=point)
            wanted = fraction * bar.maximum()
            self.assertAlmostEqual(bar.value() / bar.maximum(), wanted / bar.maximum(), delta=0.1)


if __name__ == '__main__':
    unittest.main()
