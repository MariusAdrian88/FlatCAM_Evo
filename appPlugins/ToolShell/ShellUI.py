# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# https://github.com/MariusAdrian88/FlatCAM_Evo            #
# Author: Juan Pablo Caram (c)                             #
# Date: 2/5/2014                                           #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtCore
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QTextCursor, QPixmap
from PyQt6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout

from appGUI.GUIElements import _BrowserTextEdit, _ExpandableTextEdit, FCLabel
from appCommon.Headless import console_write

import html
import sys
import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class TermWidget(QWidget):
    """
    Widget which represents terminal. It only displays text and allows to enter text.
    All high level logic should be implemented by client classes

    User pressed Enter. Client class should decide, if command must be executed or user may continue edit it
    """

    def __init__(self, version, app, *args):
        QWidget.__init__(self, *args)
        # Tcl commands and their output are left to right, in a right-to-left application too
        self.setLayoutDirection(QtCore.Qt.LayoutDirection.LeftToRight)

        self.app = app

        self._browser = _BrowserTextEdit(version=version, app=app)

        q_settings = QSettings("Open Source", "FlatCAM_EVO")
        if q_settings.contains("textbox_font_size"):
            tb_fsize = q_settings.value('textbox_font_size', type=int)
        else:
            tb_fsize = 9
        self._browser.setStyleSheet("font: {0}pt \"Courier\";".format(tb_fsize))

        self._browser.setReadOnly(True)
        self._browser.document().setDefaultStyleSheet(
            self._browser.document().defaultStyleSheet() +
            "span {white-space:pre;}")

        self._edit = _ExpandableTextEdit(self, self)
        self._edit.setPlaceholderText(_("Type a command to be executed ..."))
        self._edit.historyNext.connect(self._on_history_next)
        self._edit.historyPrev.connect(self._on_history_prev)
        self._edit.setFocus()
        self.setFocusProxy(self._edit)

        self._delete_line = FCLabel()
        self._delete_line.setPixmap(QPixmap(self.app.resource_location + '/clear_line16.png'))
        self._delete_line.setMargin(3)
        self._delete_line.setToolTip(_("Clear the text."))

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browser)

        hlay = QHBoxLayout()
        hlay.addWidget(self._delete_line)
        hlay.addWidget(FCLabel(" "))
        hlay.addWidget(self._edit)
        layout.addLayout(hlay)

        self._history = ['']  # current empty line
        self._historyIndex = 0

        self._delete_line.clicked.connect(self.on_delete_line_clicked)

    def command_line(self):
        return self._edit

    def on_delete_line_clicked(self):
        self._edit.clear()

    def open_processing(self, detail=None):
        """
        Open processing and disable using shell commands again until all commands are finished

        :param detail: text detail about what is currently called from TCL to python
        :return: None
        """

        self._edit.setTextColor(QtCore.Qt.GlobalColor.white)
        self._edit.setTextBackgroundColor(QtCore.Qt.GlobalColor.darkGreen)
        if detail is None:
            self._edit.setPlainText(_("...processing..."))
        else:
            self._edit.setPlainText('%s [%s]' % (_("...processing..."),  detail))

        self._edit.setDisabled(True)
        self._edit.setFocus()

    def close_processing(self):
        """
        Close processing and enable using shell commands  again
        :return:
        """

        self._edit.setTextColor(QtCore.Qt.GlobalColor.black)
        self._edit.setTextBackgroundColor(QtCore.Qt.GlobalColor.white)
        self._edit.setPlainText('')
        self._edit.setDisabled(False)
        self._edit.setFocus()

    def _append_to_browser(self, style, text):
        """
        Convert text to HTML for inserting it to browser
        """
        if getattr(self.app, 'headless_console', False):
            # headless mode: the widget is never shown, the console is the output
            console_write(style, text)
            return

        assert style in ('in', 'out', 'err', 'warning', 'success', 'selected', 'raw')

        if style != 'raw':
            text = html.escape(text)
            text = text.replace('\n', '<br/>')
        else:
            text = text.replace('\n', '<br>')
            text = text.replace('\t', '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;')

        idx = text.find(']')
        mtype = text[:idx+1].upper()
        mtype = mtype.replace('_NOTCL', '')
        body = text[idx+1:]
        if style.lower() == 'in':
            text = '<span style="font-weight: bold;">%s</span>' % text
        elif style.lower() == 'err':
            text = '<span style="font-weight: bold; color: red;">%s</span>'\
                   '<span style="font-weight: bold;">%s</span>'\
                   % (mtype, body)
        elif style.lower() == 'warning':
            # text = '<span style="font-weight: bold; color: #f4b642;">%s</span>' % text
            text = '<span style="font-weight: bold; color: #f4b642;">%s</span>' \
                   '<span style="font-weight: bold;">%s</span>' \
                   % (mtype, body)
        elif style.lower() == 'success':
            # text = '<span style="font-weight: bold; color: #15b300;">%s</span>' % text
            text = '<span style="font-weight: bold; color: #15b300;">%s</span>' \
                   '<span style="font-weight: bold;">%s</span>' \
                   % (mtype, body)
        elif style.lower() == 'selected':
            text = ''
        elif style.lower() == 'raw':
            text = text
        else:
            if text.startswith('[log]'):
                text = text.replace('[log]', '', 1)
                text = '<span style="color: gray;">%s</span>' % text
            else:
                # without span <br/> is ignored!!!
                text = '<span>%s</span>' % text

        scrollbar = self._browser.verticalScrollBar()
        old_value = scrollbar.value()
        # scrollattheend = old_value == scrollbar.maximum()

        self._browser.moveCursor(QTextCursor.MoveOperation.End)
        self._browser.insertHtml(text)

        """TODO When user enters second line to the input, and input is resized, scrollbar changes its position
        and stops moving. As quick fix of this problem, now we always scroll down when add new text.
        To fix it correctly, scroll to the bottom, if before input has been resized,
        scrollbar was in the bottom, and remove next line
        """
        scrollattheend = True

        if scrollattheend:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(old_value)

    def exec_current_command(self):
        """
        Save current command in the history. Append it to the log. Clear edit line
        Re-implement in the child classes to actually execute command
        """
        text = str(self._edit.toPlainText())

        # in Windows replace all backslash symbols '\' with '\\' slash because Windows paths are made with backslash
        # and in Python single slash is the escape symbol
        if sys.platform == 'win32':
            text = text.replace('\\', '\\\\')

        self._append_to_browser('in', '> ' + text + '\n')

        if len(self._history) < 2 or self._history[-2] != text:  # don't insert duplicating items
            try:
                if text[-1] == '\n':
                    self._history.insert(-1, text[:-1])
                else:
                    self._history.insert(-1, text)
            except IndexError:
                return

        self._historyIndex = len(self._history) - 1

        self._history[-1] = ''
        self._edit.clear()

        if not text[-1] == '\n':
            text += '\n'

        self.child_exec_command(text)

    def child_exec_command(self, text):
        """
        Re-implement in the child classes
        """
        pass

    def add_line_break_to_input(self):
        self._edit.textCursor().insertText('\n')

    def append_input(self, text):
        """
        Append text to output widget in bolded style
        """
        self._append_to_browser('in', text)

    def append_output(self, text):
        """
        Append text to output widget
        """
        self._append_to_browser('out', text)

    def append_raw(self, text):
        """
        Append text to output widget as it is
        """
        self._append_to_browser('raw', text)

    def append_success(self, text):
        """Append text to output widget
        """
        self._append_to_browser('success', text)

    def append_selected(self, text):
        """Append text to output widget
        """
        self._append_to_browser('selected', text)

    def append_warning(self, text):
        """Append text to output widget
        """
        self._append_to_browser('warning', text)

    def append_error(self, text):
        """Append error text to output widget. Text is drawn with red background
        """
        self._append_to_browser('err', text)

    def is_command_complete(self, text):
        """
        Executed by _ExpandableTextEdit. Re-implement this function in the child classes.
        """
        return True

    def browser(self):
        return self._browser

    def _on_history_next(self):
        """
        Down pressed, show next item from the history
        """
        if (self._historyIndex + 1) < len(self._history):
            self._historyIndex += 1
            self._edit.setPlainText(self._history[self._historyIndex])
            self._edit.moveCursor(QTextCursor.MoveOperation.End)

    def _on_history_prev(self):
        """
        Up pressed, show previous item from the history
        """
        if self._historyIndex > 0:
            if self._historyIndex == (len(self._history) - 1):
                self._history[-1] = self._edit.toPlainText()
            self._historyIndex -= 1
            self._edit.setPlainText(self._history[self._historyIndex])
            self._edit.moveCursor(QTextCursor.MoveOperation.End)
