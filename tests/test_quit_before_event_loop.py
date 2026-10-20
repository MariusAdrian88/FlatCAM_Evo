"""
quit_application() has to end the application when it runs before the Qt event loop starts.

A script given with ``--shellfile`` is executed inside App.__init__(), before ``app.exec()`` in flatcam.py.
A ``quit_app`` at the end of such a script called QApplication.quit() while no event loop was running. Qt ignores
that call, then ``app.exec()`` started and the application stayed open (a hidden window, a process that never ends).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6 import QtCore, QtWidgets

from appHandlers.appLifecycle import AppLifecycle

SAFETY_EXIT_CODE = 99


@pytest.fixture
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    # quit_application() also queues a quit for the start of the next event loop. When the loop was already running
    # that second quit is still queued: let a short loop consume it, so that it does not end the loop of the next test.
    QtCore.QTimer.singleShot(100, QtWidgets.QApplication.quit)
    app.exec()


def _lifecycle():
    """AppLifecycle on an app made of stand-ins; nothing of the real preferences or window is touched."""
    app = MagicMock()
    app.save_in_progress = False
    app.should_we_save = False
    app.tools_db_tab = None
    app.new_launch = SimpleNamespace(thread_exit=False, address=('127.0.0.1', 1))
    app._editor_for = lambda kind: None
    app.ui.saveState = MagicMock(return_value=b'')
    app.ui.isMaximized = MagicMock(return_value=False)
    return AppLifecycle(app)


def _quit_application(lifecycle):
    with patch('appHandlers.appLifecycle.sys.platform', 'linux'), patch('PyQt6.QtCore.QSettings'):
        return lifecycle.quit_application(silent=True)


def _run_event_loop(qt_app):
    """exec() returns 0 when the quit is delivered; the safety timer ends a loop that would run for ever."""
    safety = QtCore.QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(lambda: QtWidgets.QApplication.exit(SAFETY_EXIT_CODE))
    safety.start(1500)
    try:
        return qt_app.exec()
    finally:
        safety.stop()


def test_quit_before_the_event_loop_starts_ends_the_loop(qt_app):
    assert _quit_application(_lifecycle()) is True

    assert _run_event_loop(qt_app) == 0


def test_quit_from_inside_the_running_event_loop_still_ends_it(qt_app):
    lifecycle = _lifecycle()
    results = []
    QtCore.QTimer.singleShot(0, lambda: results.append(_quit_application(lifecycle)))

    assert _run_event_loop(qt_app) == 0
    assert results == [True]


def test_a_refused_quit_does_not_end_the_application(qt_app):
    lifecycle = _lifecycle()
    lifecycle.app.tools_db_tab = MagicMock()
    lifecycle.app.tools_db_tab.confirm_close.return_value = False
    # the user cancelled in the "Tools Database not saved" question: nothing may quit later either
    assert _quit_application(lifecycle) is False

    assert _run_event_loop(qt_app) == SAFETY_EXIT_CODE
