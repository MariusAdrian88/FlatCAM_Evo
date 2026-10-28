"""In headless mode the shell output goes to stdout, the errors go to stderr, without HTML."""

import io
from types import SimpleNamespace

from appCommon.Headless import HeadlessExit, console_text, console_write


def _write(style, text):
    out, err = io.StringIO(), io.StringIO()
    console_write(style, text, out=out, err=err)
    return out.getvalue(), err.getvalue()


def test_output_goes_to_stdout():
    assert _write('out', 'hello\n') == ('hello\n', '')
    assert _write('success', '[success] Saved\n') == ('[success] Saved\n', '')
    assert _write('warning', '[WARNING] careful\n') == ('[WARNING] careful\n', '')


def test_errors_go_to_stderr():
    assert _write('err', 'ERROR Report: boom\n') == ('', 'ERROR Report: boom\n')
    assert _write('out', '[ERROR_NOTCL] Failed\n') == ('', '[ERROR_NOTCL] Failed\n')


def test_only_critical_qt_messages_reach_stderr(capsys):
    from PyQt6 import QtCore
    from appCommon.Headless import headless_qt_message_handler
    headless_qt_message_handler(QtCore.QtMsgType.QtWarningMsg, None, 'QWidget::setMaximumSize: Negative sizes')
    headless_qt_message_handler(QtCore.QtMsgType.QtCriticalMsg, None, 'broken')
    assert capsys.readouterr().err == 'Qt: broken\n'


def test_the_shell_widget_writes_to_the_console_in_headless_mode(capsys):
    from appPlugins.ToolShell import TermWidget
    fake = SimpleNamespace(app=SimpleNamespace(headless_console=True))
    TermWidget._append_to_browser(fake, 'out', 'from the shell\n')
    assert capsys.readouterr().out == 'from the shell\n'


# the output pane of the shell (GUI)

def _shell_output(app):
    from PyQt6 import QtWidgets
    from appGUI.GUIElements import _BrowserTextEdit
    app.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    browser = _BrowserTextEdit(version='test', app=app)
    browser.setReadOnly(True)
    return browser


def test_the_find_of_the_shell_output_menu_runs_the_find_of_the_shell():
    # the action was connected to the placeholder that the Tcl shell replaces later: the menu Find did nothing
    browser = _shell_output(SimpleNamespace())
    found = []
    browser.find_text = lambda: found.append('find')
    browser.find_action.trigger()
    assert found == ['find']


def test_page_down_in_the_command_line_scrolls_the_shell_output():
    # keyPressEvent of the output pane never passed a key on, so the PageUp / PageDown that the command line sends
    # to it did not scroll; S still toggles the shell
    from PyQt6 import QtCore
    from PyQt6.QtTest import QTest
    from appGUI.GUIElements import _ExpandableTextEdit
    app = SimpleNamespace(ui=SimpleNamespace(toggle_shell_ui=lambda: toggles.append('S')))
    toggles = []
    browser = _shell_output(app)
    browser.resize(300, 100)
    browser.setPlainText('\n'.join('line %d' % i for i in range(200)))
    browser.show()
    app.qt_app.processEvents()
    bar = browser.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == 0
    command_line = _ExpandableTextEdit(SimpleNamespace(browser=lambda: browser))
    QTest.keyClick(command_line, QtCore.Qt.Key.Key_PageDown)
    assert bar.value() > 0
    QTest.keyClick(browser, QtCore.Qt.Key.Key_S)
    assert toggles == ['S']
    browser.close()
