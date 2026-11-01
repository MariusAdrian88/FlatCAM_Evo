"""
CodeEdit: the plain text editor of the code mode of AppTextEditor (Script, CNCJob review, Source view, G-code editor,
Levelling, SolderPaste, GRBL program view). Line number gutter, current line, bracket match, editing aids, zoom,
lazy syntax highlighting.
"""

from PyQt6 import QtCore, QtGui, QtWidgets

from appGUI.GUIElements import FCPlainTextAreaExtended
from appEditors.text_editor.folding import FoldModel
from appEditors.text_editor.highlighter import LazyHighlighter
from appEditors.text_editor.lexers import GCodeLexer
from appEditors.text_editor.themes import palette_for

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

Qt = QtCore.Qt
OPEN_BRACKETS = '([{'
CLOSE_BRACKETS = ')]}'
BRACKET_SCAN_LIMIT = 20000      # characters searched for the bracket that matches the one at the cursor
MIN_FONT_SIZE = 4.0
MAX_FONT_SIZE = 72.0
FOLD_STRIP = 14         # pixels of the fold markers at the right of the line numbers
FOLDED_MARK = '…'  # painted after the text of a folded line


class _Gutter(QtWidgets.QWidget):
    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit

    def sizeHint(self):
        return QtCore.QSize(self.edit.gutter_width(), 0)

    def paintEvent(self, event):
        self.edit.paint_gutter(self, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().x() >= self.width() - FOLD_STRIP:
            self.edit.toggle_fold_at_y(int(event.position().y()))


class CodeEdit(FCPlainTextAreaExtended):
    """
    :ivar shown_blocks:     (first, last) numbers of the blocks shown; only they are highlighted
    :ivar highlighter:      the LazyHighlighter of the document
    """

    zoomChanged = QtCore.pyqtSignal(int)        # zoom in percent of the base font size

    def __init__(self, parent=None, theme='default', lexer=None, tab_width=4):
        super().__init__(parent)

        self.theme = theme
        self.colors = palette_for(theme, self._background())
        self.lexer = lexer if lexer is not None else GCodeLexer()
        self.tab_width = tab_width
        self._find_selections = []
        self._base_font_size = None

        self.setFrameStyle(QtWidgets.QFrame.Shape.NoFrame)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        self.gutter = _Gutter(self)
        self._gutter_width = 0
        self._right_margin = 0
        self.minimap = None
        self.fold = FoldModel(self)

        # Only the blocks (lines) that are shown are highlighted, the others when they are scrolled into view.
        self.shown_blocks = (0, 300)
        # True while highlight_shown_blocks() highlights: each block highlighted emits updateRequest again
        self.highlighting_shown = False
        self.highlighter = LazyHighlighter(self.document(), self.lexer, self.colors)
        self.highlighter.editor = self

        self.updateRequest.connect(self.highlight_shown_blocks)
        self.updateRequest.connect(self._on_update_request)
        self.blockCountChanged.connect(self.adjust_gutter_width)
        self.cursorPositionChanged.connect(self.highlight)
        self.cursorPositionChanged.connect(self.gutter.update)

        self.adjust_gutter_width()
        self.set_tab_width(tab_width)
        self.refresh_selections()

        self.add_action_to_context_menu(_("Fold All"), callback=self.fold.fold_all, separator='before')
        self.add_action_to_context_menu(_("Unfold All"), callback=self.fold.unfold_all)

    # ------------------------------------------------------------------------------------------------------------
    # theme and language
    # ------------------------------------------------------------------------------------------------------------
    def _background(self):
        return self.palette().color(QtGui.QPalette.ColorRole.Base)

    def set_theme(self, theme):
        self.theme = theme
        self.colors = palette_for(theme, self._background())
        self.highlighter.set_palette(self.colors)
        self.highlighter.rehighlight()
        self.refresh_selections()
        self.gutter.update()
        if self.minimap is not None:
            self.minimap.schedule()

    def set_lexer(self, lexer):
        self.lexer = lexer
        self.highlighter.set_lexer(lexer)
        self.highlighter.rehighlight()
        self.fold.schedule()

    def attach_minimap(self, minimap):
        self.minimap = minimap

    # ------------------------------------------------------------------------------------------------------------
    # gutter
    # ------------------------------------------------------------------------------------------------------------
    def gutter_width(self):
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance('9') * digits + FOLD_STRIP

    def adjust_gutter_width(self, *args):
        width = self.gutter_width()
        if width != self._gutter_width:
            self._gutter_width = width
            self._apply_margins()

    def set_right_margin(self, width):
        """Room at the right of the text for the minimap."""
        if width != self._right_margin:
            self._right_margin = width
            self._apply_margins()

    def _apply_margins(self):
        self.setViewportMargins(self._gutter_width, 0, self._right_margin, 0)
        self._place_gutter()

    def _place_gutter(self):
        rect = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(rect.left(), rect.top(), self._gutter_width, rect.height()))
        if self.minimap is not None:
            area = self.viewport().geometry()
            self.minimap.setGeometry(area.right() + 1, area.top(), self.minimap.width(), area.height())

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QtCore.QEvent.Type.Resize and self.minimap is not None:
            # (a scroll bar that appears or goes away moves the right edge of the text)
            self._place_gutter()
        return result

    def toggle_fold_at_y(self, y):
        block = self.cursorForPosition(QtCore.QPoint(0, y)).block()
        if block.isValid() and block.isVisible():
            self.fold.toggle(block.blockNumber())

    def _on_update_request(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            # (the whole gutter: the bold of the current line must go away when word wrap is on)
            self.gutter.update()
        if rect.contains(self.viewport().rect()):
            self.adjust_gutter_width()

    def paint_gutter(self, gutter, event):
        colors = self.colors
        painter = QtGui.QPainter(gutter)
        painter.fillRect(event.rect(), QtGui.QColor(colors.gutter_bg))
        font = self.font()
        painter.setFont(font)
        height = self.fontMetrics().height()
        current = self.textCursor().blockNumber()
        right = gutter.width() - 6 - FOLD_STRIP
        starts = self.fold.starts
        marker_pen = QtGui.QColor(colors.fold_marker_fg)

        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_current = number == current
                font.setBold(is_current)
                painter.setFont(font)
                painter.setPen(QtGui.QColor(colors.gutter_current_fg if is_current else colors.gutter_fg))
                painter.drawText(QtCore.QRect(0, top, right, height), Qt.AlignmentFlag.AlignRight, str(number + 1))
                if number in starts:
                    font.setBold(False)
                    painter.setFont(font)
                    painter.setPen(marker_pen)
                    painter.drawText(QtCore.QRect(gutter.width() - FOLD_STRIP, top, FOLD_STRIP, height),
                                     Qt.AlignmentFlag.AlignCenter,
                                     '▸' if self.fold.is_folded(number) else '▾')
            block = self._next_visible_block(block)
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            number = block.blockNumber()
        painter.end()

    def _next_visible_block(self, block):
        """The block after `block`, past the blocks hidden by a fold (they have no height; a run can be 100000 long)."""
        block = block.next()
        if block.isValid() and not block.isVisible():
            block = self.document().findBlockByNumber(self.fold.next_visible(block.blockNumber()))
        return block

    # ------------------------------------------------------------------------------------------------------------
    # extra selections: current line + find matches + bracket match
    # ------------------------------------------------------------------------------------------------------------
    def highlight(self, *args):
        self.refresh_selections()

    def set_find_selections(self, selections):
        """The matches of the find bar (a list of QTextEdit.ExtraSelection) shown with the current line."""
        self._find_selections = selections
        self.refresh_selections()

    def refresh_selections(self):
        colors = self.colors
        line = QtWidgets.QTextEdit.ExtraSelection()
        line.format.setBackground(QtGui.QColor(colors.current_line_bg))
        line.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections = [line]
        selections.extend(self._find_selections)
        selections.extend(self._bracket_selections())
        QtWidgets.QPlainTextEdit.setExtraSelections(self, selections)

    def _bracket_selections(self):
        found = self.matching_brackets()
        if not found:
            return []
        selections = []
        for position in found:
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(QtGui.QColor(self.colors.bracket_match_bg))
            selection.cursor = QtGui.QTextCursor(self.document())
            selection.cursor.setPosition(position)
            selection.cursor.setPosition(position + 1, QtGui.QTextCursor.MoveMode.KeepAnchor)
            selections.append(selection)
        return selections

    def matching_brackets(self):
        """Positions of the bracket at the cursor and of the one that matches it, or [] (none or not found)."""
        doc = self.document()
        cursor_pos = self.textCursor().position()
        for position in (cursor_pos - 1, cursor_pos):
            if position < 0:
                continue
            char = doc.characterAt(position)
            if char and char in OPEN_BRACKETS:
                other = self._scan_bracket(position, char, CLOSE_BRACKETS[OPEN_BRACKETS.index(char)], True)
            elif char and char in CLOSE_BRACKETS:
                other = self._scan_bracket(position, char, OPEN_BRACKETS[CLOSE_BRACKETS.index(char)], False)
            else:
                continue
            return [position, other] if other is not None else []
        return []

    def _scan_bracket(self, position, char, partner, forward):
        doc = self.document()
        cursor = QtGui.QTextCursor(doc)
        end = doc.characterCount() - 1
        if forward:
            cursor.setPosition(position + 1)
            cursor.setPosition(min(end, position + 1 + BRACKET_SCAN_LIMIT), QtGui.QTextCursor.MoveMode.KeepAnchor)
            text = cursor.selectedText()
            indices = range(len(text))
            base = position + 1
        else:
            start = max(0, position - BRACKET_SCAN_LIMIT)
            cursor.setPosition(start)
            cursor.setPosition(position, QtGui.QTextCursor.MoveMode.KeepAnchor)
            text = cursor.selectedText()
            indices = range(len(text) - 1, -1, -1)
            base = start
        depth = 0
        for index in indices:
            current = text[index]
            if current == char:
                depth += 1
            elif current == partner:
                if depth == 0:
                    return base + index
                depth -= 1
        return None

    # ------------------------------------------------------------------------------------------------------------
    # lazy highlighting
    # ------------------------------------------------------------------------------------------------------------
    def highlight_shown_blocks(self, *args):
        """
        Highlights the blocks shown that were never highlighted (their state is still -1).
        Connected to the updateRequest signal: it is emitted on scroll, resize and text changes.
        """
        if self.highlighting_shown:
            return

        block = self.firstVisibleBlock()
        if not block.isValid():
            return
        first = block.blockNumber()
        height = self.viewport().height()
        offset = self.contentOffset()
        last = first
        while block.isValid():
            last = block.blockNumber()
            if self.blockBoundingGeometry(block).translated(offset).top() > height:
                break
            block = self._next_visible_block(block)
        self.shown_blocks = (first, last)

        self.highlighting_shown = True
        try:
            # Formatting emits textChanged too; scrolling must not mark the text as edited.
            with QtCore.QSignalBlocker(self):
                block = self.document().findBlockByNumber(first)
                while block.isValid() and block.blockNumber() <= last:
                    if block.userState() == -1:
                        self.highlighter.rehighlightBlock(block)
                    block = self._next_visible_block(block)
        finally:
            self.highlighting_shown = False

    def paintEvent(self, event):
        super().paintEvent(event)
        folded = self.fold.folded
        if not folded:
            return
        # the mark of a folded line: painted after its text, not part of the text
        painter = QtGui.QPainter(self.viewport())
        painter.setPen(QtGui.QColor(self.colors.fold_marker_fg))
        painter.setFont(self.font())
        offset = self.contentOffset()
        block = self.firstVisibleBlock()
        height = self.viewport().height()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(offset).top()
            if top > height:
                break
            if block.isVisible() and block.blockNumber() in folded:
                layout = block.layout()
                line = layout.lineAt(layout.lineCount() - 1)
                position = layout.position()
                x = position.x() + offset.x() + line.x() + line.naturalTextWidth() + 6
                y = position.y() + offset.y() + line.y() + line.ascent()
                painter.drawText(QtCore.QPointF(x, y), FOLDED_MARK)
            block = self._next_visible_block(block)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_gutter()
        # a resize that changes only the height emits no updateRequest: the lines that come into view
        self.highlight_shown_blocks()

    def print(self, printer):
        """
        Prints the document. The blocks never shown are not highlighted yet: they are highlighted first, so the
        printed text has the colors on every line.
        """
        block = self.document().firstBlock()
        while block.isValid() and block.userState() != -1:
            block = block.next()
        # (once every block is highlighted, a second print or Print Preview render does not highlight again)
        if block.isValid():
            # without the editor the highlighter highlights every block
            self.highlighter.editor = None
            # each block highlighted emits updateRequest: highlight_shown_blocks() must not run for each of them
            self.highlighting_shown = True
            try:
                with QtCore.QSignalBlocker(self):
                    self.highlighter.rehighlight()
            finally:
                self.highlighter.editor = self
                self.highlighting_shown = False
        # every line is printed, the folded ones too
        with self.fold.all_visible():
            super().print(printer)

    # ------------------------------------------------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------------------------------------------------
    def load(self, text):
        """Replaces the whole text fast: no signals, no highlighting and no undo history while it is set."""
        doc = self.document()
        highlighter = self.highlighter
        self.fold.reset()
        with QtCore.QSignalBlocker(self):
            highlighter.setDocument(None)
            doc.setUndoRedoEnabled(False)
            self.setPlainText(text)
            doc.setUndoRedoEnabled(True)
            self.shown_blocks = (0, 300)
            highlighter.setDocument(doc)
            highlighter.editor = self
            doc.setModified(False)
        self.undo_flag = False
        self.redo_flag = False
        # (the signals were blocked: the line count of the status bar, the gutter width and the minimap follow it)
        self.blockCountChanged.emit(doc.blockCount())
        self.highlight_shown_blocks()
        self.refresh_selections()
        self.gutter.update()
        self.viewport().update()
        # the fold scan and the minimap image follow on their timers
        self.fold.schedule()
        if self.minimap is not None:
            self.minimap.update_visibility()
            self.minimap.schedule()

    # ------------------------------------------------------------------------------------------------------------
    # view options
    # ------------------------------------------------------------------------------------------------------------
    def set_show_whitespace(self, show):
        option = self.document().defaultTextOption()
        flags = option.flags()
        flag = QtGui.QTextOption.Flag.ShowTabsAndSpaces
        option.setFlags(flags | flag if show else flags & ~flag)
        self.document().setDefaultTextOption(option)

    def set_word_wrap(self, wrap):
        mode = QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth if wrap else QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap
        self.setLineWrapMode(mode)

    def set_tab_width(self, spaces):
        self.tab_width = max(1, int(spaces))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * self.tab_width)

    def set_editor_font(self, family='', size=10):
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        if family:
            font.setFamily(family)
        self._base_font_size = float(size)
        font.setPointSizeF(self._base_font_size)
        self.setFont(font)
        self.zoomChanged.emit(100)

    def zoom(self, step):
        font = self.font()
        base = self._base_font_size or font.pointSizeF()
        self._base_font_size = base
        size = min(MAX_FONT_SIZE, max(MIN_FONT_SIZE, font.pointSizeF() + step))
        font.setPointSizeF(size)
        self.setFont(font)
        self.zoomChanged.emit(round(size / base * 100))

    def reset_zoom(self):
        if self._base_font_size:
            font = self.font()
            font.setPointSizeF(self._base_font_size)
            self.setFont(font)
        self.zoomChanged.emit(100)

    def changeEvent(self, event):
        super().changeEvent(event)
        kind = event.type()
        if kind == QtCore.QEvent.Type.FontChange and hasattr(self, 'gutter'):
            self.set_tab_width(self.tab_width)
            self._gutter_width = -1
            self.adjust_gutter_width()
            self.gutter.update()
        elif kind == QtCore.QEvent.Type.PaletteChange and hasattr(self, 'highlighter'):
            # the system changed to dark or light mode: the colors follow the new background
            if palette_for(self.theme, self._background()) is not self.colors:
                self.set_theme(self.theme)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.zoom(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def goto_line(self, number):
        """Goes to the line `number` (1 based; the lines are the blocks, not the wrapped parts). False: not a line."""
        try:
            number = int(number)
        except (ValueError, TypeError):
            return False
        doc = self.document()
        if not 1 <= number <= doc.blockCount():
            return False
        self.fold.reveal(number - 1)
        # the cursor goes to the end first so the line is positioned at the top of the window
        # (a fold at the end of the text stays folded)
        self.fold.reveal_enabled = False
        try:
            self.moveCursor(QtGui.QTextCursor.MoveOperation.End)
        finally:
            self.fold.reveal_enabled = True
        self.setTextCursor(QtGui.QTextCursor(doc.findBlockByNumber(number - 1)))
        return True

    # ------------------------------------------------------------------------------------------------------------
    # editing aids (each one is one Undo step)
    # ------------------------------------------------------------------------------------------------------------
    def _line_range(self):
        """(first, last) block numbers of the lines the selection touches."""
        cursor = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cursor.selectionStart())
        last = doc.findBlock(cursor.selectionEnd())
        if last.blockNumber() > first.blockNumber() and cursor.selectionEnd() == last.position():
            # the selection ends at the start of a line: that line is not selected
            last = last.previous()
        return first.blockNumber(), last.blockNumber()

    def _select_lines(self, first, last):
        doc = self.document()
        cursor = QtGui.QTextCursor(doc.findBlockByNumber(first))
        end = doc.findBlockByNumber(last)
        cursor.setPosition(end.position() + end.length() - 1, QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def indent_lines(self):
        first, last = self._line_range()
        doc = self.document()
        pad = ' ' * self.tab_width
        edit = self.textCursor()
        edit.beginEditBlock()
        for number in range(first, last + 1):
            block = doc.findBlockByNumber(number)
            if block.length() > 1:
                QtGui.QTextCursor(block).insertText(pad)
        edit.endEditBlock()
        self._select_lines(first, last)

    def unindent_lines(self):
        first, last = self._line_range()
        doc = self.document()
        multi = self.textCursor().hasSelection()
        position = self.textCursor().position()
        removed_current = 0
        edit = self.textCursor()
        edit.beginEditBlock()
        for number in range(first, last + 1):
            block = doc.findBlockByNumber(number)
            text = block.text()
            count = 0
            while count < self.tab_width and count < len(text) and text[count] in ' \t':
                count += 1
                if text[count - 1] == '\t':
                    break
            if count:
                cursor = QtGui.QTextCursor(block)
                cursor.setPosition(block.position() + count, QtGui.QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                if not multi:
                    removed_current = min(count, max(0, position - block.position()))
        edit.endEditBlock()
        if multi:
            self._select_lines(first, last)
        else:
            cursor = self.textCursor()
            cursor.setPosition(position - removed_current)
            self.setTextCursor(cursor)

    def duplicate_lines(self):
        first, last = self._line_range()
        doc = self.document()
        had_selection = self.textCursor().hasSelection()
        column = self.textCursor().positionInBlock()
        lines = [doc.findBlockByNumber(n).text() for n in range(first, last + 1)]
        end_block = doc.findBlockByNumber(last)
        edit = self.textCursor()
        edit.beginEditBlock()
        cursor = QtGui.QTextCursor(doc)
        cursor.setPosition(end_block.position() + end_block.length() - 1)
        cursor.insertText('\n' + '\n'.join(lines))
        edit.endEditBlock()
        count = last - first + 1
        if had_selection:
            self._select_lines(last + 1, last + count)
        else:
            cursor = QtGui.QTextCursor(doc.findBlockByNumber(last + 1))
            cursor.setPosition(cursor.position() + column)
            self.setTextCursor(cursor)

    def move_lines(self, up):
        first, last = self._line_range()
        doc = self.document()
        if (up and first == 0) or (not up and last >= doc.blockCount() - 1):
            return
        edit = self.textCursor()
        edit.beginEditBlock()
        cursor = QtGui.QTextCursor(doc)
        if up:
            previous = doc.findBlockByNumber(first - 1)
            text = previous.text()
            cursor.setPosition(previous.position())
            cursor.setPosition(doc.findBlockByNumber(first).position(), QtGui.QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            end_block = doc.findBlockByNumber(last - 1)
            cursor = QtGui.QTextCursor(doc)
            cursor.setPosition(end_block.position() + end_block.length() - 1)
            cursor.insertText('\n' + text)
            new_first, new_last = first - 1, last - 1
        else:
            following = doc.findBlockByNumber(last + 1)
            text = following.text()
            end_block = doc.findBlockByNumber(last)
            cursor.setPosition(end_block.position() + end_block.length() - 1)
            cursor.setPosition(following.position() + following.length() - 1, QtGui.QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor = QtGui.QTextCursor(doc)
            cursor.setPosition(doc.findBlockByNumber(first).position())
            cursor.insertText(text + '\n')
            new_first, new_last = first + 1, last + 1
        edit.endEditBlock()
        self._select_lines(new_first, new_last)

    def toggle_comment(self):
        prefix = self.lexer.comment_prefix
        suffix = self.lexer.comment_suffix
        if not prefix:
            return
        first, last = self._line_range()
        doc = self.document()
        blocks = [doc.findBlockByNumber(n) for n in range(first, last + 1)]
        blocks = [b for b in blocks if b.text().strip()]
        if not blocks:
            return
        bare_prefix = prefix.rstrip()
        bare_suffix = suffix.strip()

        def is_commented(text):
            text = text.strip()
            return text.startswith(bare_prefix) and (not bare_suffix or text.endswith(bare_suffix))

        uncomment = all(is_commented(b.text()) for b in blocks)
        indent = min(len(b.text()) - len(b.text().lstrip()) for b in blocks)
        edit = self.textCursor()
        edit.beginEditBlock()
        for block in blocks:
            text = block.text()
            cursor = QtGui.QTextCursor(block)
            if uncomment:
                start = len(text) - len(text.lstrip())
                end_cut = len(text.rstrip())
                if bare_suffix:
                    cursor.setPosition(block.position() + end_cut - len(bare_suffix))
                    cursor.setPosition(block.position() + end_cut, QtGui.QTextCursor.MoveMode.KeepAnchor)
                    cursor.removeSelectedText()
                length = len(bare_prefix)
                if prefix.endswith(' ') and text[start + length:start + length + 1] == ' ':
                    length += 1
                cursor.setPosition(block.position() + start)
                cursor.setPosition(block.position() + start + length, QtGui.QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            else:
                if suffix:
                    cursor.setPosition(block.position() + len(text.rstrip()))
                    cursor.insertText(suffix)
                cursor = QtGui.QTextCursor(block)
                cursor.setPosition(block.position() + indent)
                cursor.insertText(prefix)
        edit.endEditBlock()
        self._select_lines(first, last)

    def auto_indent(self):
        cursor = self.textCursor()
        text = cursor.block().text()
        indent = text[:len(text) - len(text.lstrip())][:cursor.positionInBlock()]
        cursor.insertText('\n' + indent)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def insert_tab(self):
        cursor = self.textCursor()
        column = cursor.positionInBlock()
        cursor.insertText(' ' * (self.tab_width - column % self.tab_width))

    def _edit_shortcut(self, event):
        """The name of the editing command of a key event, or None."""
        key = event.key()
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl = Qt.KeyboardModifier.ControlModifier
        alt = Qt.KeyboardModifier.AltModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        if mods == ctrl:
            return {Qt.Key.Key_D: 'duplicate', Qt.Key.Key_Slash: 'comment', Qt.Key.Key_Equal: 'zoom_in',
                    Qt.Key.Key_Plus: 'zoom_in', Qt.Key.Key_Minus: 'zoom_out', Qt.Key.Key_0: 'zoom_reset'}.get(key)
        if mods == ctrl | shift and key == Qt.Key.Key_Plus:
            return 'zoom_in'
        if mods == ctrl | shift:
            # (Shift turns [ and ] into { and } on most keyboards)
            return {Qt.Key.Key_BracketLeft: 'fold', Qt.Key.Key_BraceLeft: 'fold',
                    Qt.Key.Key_BracketRight: 'unfold', Qt.Key.Key_BraceRight: 'unfold'}.get(key)
        if mods == alt:
            return {Qt.Key.Key_Up: 'move_up', Qt.Key.Key_Down: 'move_down'}.get(key)
        if key == Qt.Key.Key_Tab and mods == Qt.KeyboardModifier.NoModifier:
            return 'indent'
        if key == Qt.Key.Key_Backtab and mods in (shift, Qt.KeyboardModifier.NoModifier):
            return 'unindent'
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods == Qt.KeyboardModifier.NoModifier:
            return 'enter'
        return None

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.ShortcutOverride:
            command = self._edit_shortcut(event)
            if command is not None and command not in ('indent', 'unindent', 'enter'):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        command = self._edit_shortcut(event)
        if command is not None:
            if command.startswith('zoom'):
                {'zoom_in': lambda: self.zoom(1), 'zoom_out': lambda: self.zoom(-1),
                 'zoom_reset': self.reset_zoom}[command]()
                return
            if command in ('fold', 'unfold'):
                (self.fold.fold_at_cursor if command == 'fold' else self.fold.unfold_at_cursor)()
                return
            if not self.isReadOnly() and not self.completer.popup().isVisible():
                if command == 'enter':
                    self.auto_indent()
                    return
                if command == 'indent':
                    first, last = self._line_range()
                    if first != last:
                        self.indent_lines()
                    else:
                        self.insert_tab()
                    return
                if command == 'unindent':
                    self.unindent_lines()
                    return
                {'duplicate': self.duplicate_lines, 'comment': self.toggle_comment,
                 'move_up': lambda: self.move_lines(True), 'move_down': lambda: self.move_lines(False)}[command]()
                return
            if self.isReadOnly() and command in ('duplicate', 'comment', 'move_up', 'move_down'):
                # the base class would comment a read only text with '#'
                return
        super().keyPressEvent(event)
