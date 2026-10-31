"""
FindBar: find / replace bar of the text editor. The search runs with Python `re` on the plain text, cached until the
document changes. Only the matches inside the part of the text that is shown are highlighted, so the cost does not
grow with the number of matches.
"""

import re
from bisect import bisect_left, bisect_right

from PyQt6 import QtCore, QtGui, QtWidgets

from appGUI.GUIElements import FCEntry, FCCheckBox

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

MAX_SHOWN_MATCHES = 2000        # matches highlighted at once (the ones in the text shown)
REPLACE_BY_CURSORS_LIMIT = 2000   # more matches: Replace All sets the whole text at once
RED_BORDER = "QLineEdit {border: 1px solid #d9534f;}"


def _toggle(text, tip):
    button = QtWidgets.QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setCheckable(True)
    button.setAutoRaise(True)
    return button


class FindBar(QtWidgets.QWidget):
    """
    :param editor:  QPlainTextEdit or QTextEdit. If it has set_find_selections() the matches are shown through it
                    (CodeEdit merges them with its current line and bracket match).
    """

    closed = QtCore.pyqtSignal()

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._text = None           # cached plain text
        self._key = None            # (query, flags) of the cached matches
        self._starts = []
        self._ends = []
        self._regex = None
        self._error = ''
        self._shown_key = None
        self._had_selections = False

        outer =QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(2)

        find_row = QtWidgets.QHBoxLayout()
        outer.addLayout(find_row)
        self.entryFind = FCEntry()
        self.entryFind.setPlaceholderText(_("Find"))
        self.entryFind.setToolTip(_("Find box. Enter here the strings to be searched in the text."))
        find_row.addWidget(self.entryFind, 1)
        self.case_btn = _toggle('Aa', _("Match case"))
        self.word_btn = _toggle('W', _("Whole word"))
        self.regex_btn = _toggle('.*', _("Regular expression"))
        for button in (self.case_btn, self.word_btn, self.regex_btn):
            find_row.addWidget(button)
        self.count_label = QtWidgets.QLabel('')
        self.count_label.setMinimumWidth(80)
        find_row.addWidget(self.count_label)
        self.buttonPrev = QtWidgets.QToolButton()
        self.buttonPrev.setText('↑')
        self.buttonPrev.setToolTip(_("Previous match") + ' (Shift+F3)')
        self.buttonFind = QtWidgets.QToolButton()
        self.buttonFind.setText('↓')
        self.buttonFind.setToolTip(_("Next match") + ' (F3)')
        find_row.addWidget(self.buttonPrev)
        find_row.addWidget(self.buttonFind)
        self.close_btn = QtWidgets.QToolButton()
        self.close_btn.setText('×')
        self.close_btn.setToolTip(_("Close") + ' (Esc)')
        self.close_btn.setAutoRaise(True)
        find_row.addWidget(self.close_btn)

        self.replace_row = QtWidgets.QWidget()
        replace_lay = QtWidgets.QHBoxLayout(self.replace_row)
        replace_lay.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.replace_row)
        self.entryReplace = FCEntry()
        self.entryReplace.setPlaceholderText(_("Replace"))
        self.entryReplace.setToolTip(_("String to replace the one in the Find box throughout the text."))
        replace_lay.addWidget(self.entryReplace, 1)
        self.buttonReplace = QtWidgets.QPushButton(_("Replace"))
        self.buttonReplaceAll = QtWidgets.QPushButton(_("All"))
        self.buttonReplaceAll.setToolTip(_("Replace all matches"))
        replace_lay.addWidget(self.buttonReplace)
        replace_lay.addWidget(self.buttonReplaceAll)
        # Replace All through the old API: handleReplaceGCode() with this box checked replaces all matches
        self.sel_all_cb = FCCheckBox(_('All'))
        self.sel_all_cb.hide()

        self.status_label = QtWidgets.QLabel('')
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._update_count_label)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.update_results)

        self.entryFind.textChanged.connect(self._timer.start)
        for button in (self.case_btn, self.word_btn, self.regex_btn):
            button.toggled.connect(lambda *a: self._timer.start())
        self.buttonFind.clicked.connect(self.find_next)
        self.buttonPrev.clicked.connect(self.find_previous)
        self.buttonReplace.clicked.connect(self.replace_current)
        self.buttonReplaceAll.clicked.connect(self.replace_all)
        self.close_btn.clicked.connect(self.close_bar)
        self.entryFind.installEventFilter(self)
        self.entryReplace.installEventFilter(self)

        editor.document().contentsChange.connect(self._on_contents_change)
        editor.updateRequest.connect(self._on_view_changed) if hasattr(editor, 'updateRequest') else None
        try:
            editor.verticalScrollBar().valueChanged.connect(self._on_view_changed)
        except AttributeError:
            pass
        self.hide()

    # ------------------------------------------------------------------------------------------------------------
    # matches
    # ------------------------------------------------------------------------------------------------------------
    def _on_contents_change(self, *args):
        self._text = None
        self._key = None
        self._shown_key = None
        self._timer.start()

    def _on_view_changed(self, *args):
        if self.isVisible() and self.entryFind.text():
            self.refresh_highlights()

    def _flags_key(self):
        return (self.entryFind.text(), self.case_btn.isChecked(), self.word_btn.isChecked(), self.regex_btn.isChecked())

    def _compile(self):
        query, case, word, is_regex = self._flags_key()
        pattern = query if is_regex else re.escape(query)
        if word:
            pattern = r'\b(?:%s)\b' % pattern
        return re.compile(pattern, re.MULTILINE | (0 if case else re.IGNORECASE))

    def ensure_matches(self):
        """Finds the matches of the find box in the text (cached). Returns the number of matches."""
        key = self._flags_key()
        if self._key == key and self._text is not None:
            return len(self._starts)
        self._key = key
        self._starts = []
        self._ends = []
        self._error = ''
        self._shown_key = None
        if not key[0]:
            self.entryFind.setStyleSheet('')
            self.entryFind.setToolTip(_("Find box. Enter here the strings to be searched in the text."))
            return 0
        try:
            self._regex = self._compile()
        except re.error as err:
            self._error = str(err)
            self.entryFind.setStyleSheet(RED_BORDER)
            self.entryFind.setToolTip(self._error)
            return 0
        self.entryFind.setStyleSheet('')
        self.entryFind.setToolTip(_("Find box. Enter here the strings to be searched in the text."))
        if self._text is None:
            self._text = self.editor.toPlainText()
        spans = [m.span() for m in self._regex.finditer(self._text)]
        # an empty match (e.g. '^') is not a match to show
        spans = [s for s in spans if s[1] > s[0]]
        self._starts = [s[0] for s in spans]
        self._ends = [s[1] for s in spans]
        return len(self._starts)

    def match_count(self):
        return self.ensure_matches()

    def update_results(self):
        """Recounts the matches and highlights the ones shown."""
        self.ensure_matches()
        self._update_count_label()
        self.refresh_highlights()

    def _current_index(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return -1
        start = cursor.selectionStart()
        index = bisect_left(self._starts, start)
        if index < len(self._starts) and self._starts[index] == start and self._ends[index] == cursor.selectionEnd():
            return index
        return -1

    def _update_count_label(self):
        total = len(self._starts)
        if self._error:
            self.count_label.setText(_("Invalid"))
        elif not self.entryFind.text():
            self.count_label.setText('')
        elif total == 0:
            self.count_label.setText(_("No results"))
        else:
            index = self._current_index()
            self.count_label.setText(_("%d of %d") % (index + 1, total) if index >= 0 else _("%d found") % total)

    def refresh_highlights(self):
        """Shows the matches inside the part of the text that is shown."""
        editor = self.editor
        set_selections = getattr(editor, 'set_find_selections', editor.setExtraSelections)
        if not self._starts:
            if self._had_selections:
                set_selections([])
                self._had_selections = False
            self._shown_key = None
            return
        viewport = editor.viewport()
        first = editor.cursorForPosition(QtCore.QPoint(0, 0)).position()
        last = editor.cursorForPosition(QtCore.QPoint(viewport.width(), viewport.height())).position()
        current = self._current_index()
        key = (first, last, current, id(self._starts), len(self._starts))
        if key == self._shown_key:
            return
        self._shown_key = key
        low = bisect_left(self._ends, first)
        high = min(bisect_right(self._starts, last), low + MAX_SHOWN_MATCHES)
        normal = QtGui.QTextCharFormat()
        colors = getattr(editor, 'colors', None)
        normal.setBackground(QtGui.QColor(colors.find_match_bg if colors else '#ffe680'))
        strong = QtGui.QTextCharFormat()
        strong.setBackground(QtGui.QColor(colors.find_current_bg if colors else '#ffb347'))
        selections = []
        doc = editor.document()
        for index in range(low, high):
            selection = QtWidgets.QTextEdit.ExtraSelection()
            cursor = QtGui.QTextCursor(doc)
            cursor.setPosition(self._starts[index])
            cursor.setPosition(self._ends[index], QtGui.QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selection.format = strong if index == current else normal
            selections.append(selection)
        self._had_selections = True
        set_selections(selections)

    # ------------------------------------------------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------------------------------------------------
    def _select(self, index):
        cursor = QtGui.QTextCursor(self.editor.document())
        cursor.setPosition(self._starts[index])
        cursor.setPosition(self._ends[index], QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self._shown_key = None
        self._update_count_label()
        self.refresh_highlights()

    def _note(self, text):
        self.count_label.setText(text)
        self._status_timer.start(1500)

    def find_next(self, *args):
        """Selects the next match after the cursor; wraps to the first one. False: no match."""
        if not self.ensure_matches():
            self._update_count_label()
            return False
        cursor = self.editor.textCursor()
        after = cursor.selectionEnd() if cursor.hasSelection() else cursor.position()
        index = bisect_left(self._starts, after)
        wrapped = index >= len(self._starts)
        self._select(0 if wrapped else index)
        if wrapped:
            self._note(_("Wrapped"))
        return True

    def find_previous(self, *args):
        if not self.ensure_matches():
            self._update_count_label()
            return False
        cursor = self.editor.textCursor()
        index = bisect_left(self._starts, cursor.selectionStart()) - 1
        wrapped = index < 0
        self._select(len(self._starts) - 1 if wrapped else index)
        if wrapped:
            self._note(_("Wrapped"))
        return True

    # ------------------------------------------------------------------------------------------------------------
    # replace
    # ------------------------------------------------------------------------------------------------------------
    def _replacement(self, match):
        replacement = self.entryReplace.get_value()
        return match.expand(replacement) if self.regex_btn.isChecked() else replacement

    def replace_current(self, *args):
        """Replaces the selected match and goes to the next one. Without a selected match it only goes to the next."""
        if self.editor.isReadOnly() or not self.ensure_matches():
            return
        index = self._current_index()
        if index < 0:
            self.find_next()
            return
        try:
            new = self._replacement(self._regex.match(self._text, self._starts[index]))
        except (re.error, IndexError, AttributeError) as err:
            self.entryReplace.setToolTip(str(err))
            return
        cursor = self.editor.textCursor()
        cursor.insertText(new)
        self.find_next()

    def replace_all(self, *args):
        """Replaces every match from the start of the text in ONE edit block (one Undo step). Returns the count."""
        if self.editor.isReadOnly() or not self.ensure_matches():
            return 0
        try:
            # every replacement is made first: a wrong group in the replace text changes nothing
            if self.regex_btn.isChecked():
                matches = [m for m in self._regex.finditer(self._text) if m.end() > m.start()]
                news = [self._replacement(m) for m in matches]
            else:
                news = [self.entryReplace.get_value()] * len(self._starts)
        except (re.error, IndexError) as err:
            self.entryReplace.setToolTip(str(err))
            return 0
        editor = self.editor
        doc = editor.document()
        cursor = QtGui.QTextCursor(doc)
        cursor.beginEditBlock()
        if len(news) > REPLACE_BY_CURSORS_LIMIT and not isinstance(editor, QtWidgets.QTextEdit):
            out = []
            position = 0
            for start, end, new in zip(self._starts, self._ends, news):
                out.append(self._text[position:start])
                out.append(new)
                position = end
            out.append(self._text[position:])
            cursor.select(QtGui.QTextCursor.SelectionType.Document)
            cursor.insertText(''.join(out))
        else:
            # from the last match: the positions of the matches above stay valid
            for start, end, new in zip(reversed(self._starts), reversed(self._ends), reversed(news)):
                cursor.setPosition(start)
                cursor.setPosition(end, QtGui.QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(new)
        cursor.endEditBlock()
        editor.moveCursor(QtGui.QTextCursor.MoveOperation.Start)
        return len(news)

    # ------------------------------------------------------------------------------------------------------------
    # show / hide
    # ------------------------------------------------------------------------------------------------------------
    def open_bar(self, replace=False):
        """Shows the bar. A selection on one line goes to the find box."""
        selected = self.editor.textCursor().selectedText()
        if selected and ' ' not in selected and '\n' not in selected:
            self.entryFind.setText(selected)
        self.replace_row.setVisible(not (self.editor.isReadOnly() or self.entryReplace.isHidden()
                                         or self.buttonReplace.isHidden()))
        self.show()
        field = self.entryReplace if replace and self.replace_row.isVisible() else self.entryFind
        field.setFocus()
        field.selectAll()
        self.update_results()

    def close_bar(self, *args):
        self.hide()
        editor = self.editor
        set_selections = getattr(editor, 'set_find_selections', editor.setExtraSelections)
        set_selections([])
        self._shown_key = None
        editor.setFocus()
        self.closed.emit()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            key = event.key()
            shift = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
            if key == QtCore.Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if key == QtCore.Qt.Key.Key_F3:
                self.find_previous() if shift else self.find_next()
                return True
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if obj is self.entryReplace:
                    self.replace_current()
                else:
                    self.find_previous() if shift else self.find_next()
                return True
        return super().eventFilter(obj, event)
