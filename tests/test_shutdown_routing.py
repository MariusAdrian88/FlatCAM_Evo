"""User Exit actions share the guarded window-close and lifecycle path."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from appGUI.MainGUI import MainGUI
from appGUI.GUIElements import AppSystemTray
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appSignalConnector import AppSignalConnector


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class CloseWindow(QtWidgets.QMainWindow):
    final_save = QtCore.pyqtSignal()
    closeEvent = MainGUI.closeEvent


@pytest.fixture
def shutdown(qt_app, monkeypatch):
    window = CloseWindow()
    window.lock_action = QtGui.QAction(window)
    window.show_text_action = QtGui.QAction(window)
    window.splitter = SimpleNamespace(sizes=lambda: [120])
    window.on_toggle_gui = Mock()
    app = Mock(ui=window, resource_location='', save_in_progress=False,
               should_we_save=True, _check_in_progress=False, _check_queued=False,
               project_save_pending=False, close_after_save=False, projects=None)
    app._update_operation_active.return_value = False
    app.collection.get_list.return_value = [object()]
    window.app = app
    lifecycle = AppLifecycle(app)
    lifecycle.quit_application = Mock()
    final_save = Mock(side_effect=lifecycle.final_save)
    app.final_save = lambda: final_save()
    window.final_save.connect(app.final_save)
    settings = Mock()
    monkeypatch.setattr('appGUI.MainGUI.QSettings', Mock(return_value=settings))
    action = QtGui.QAction('Exit', window)
    editor = QtGui.QAction('Exit Editor', window)
    editor.setMenuRole(QtGui.QAction.MenuRole.NoRole)
    connector = AppSignalConnector(app)
    connector.ui = Mock(close=window.close, menufile_exit=action, menueditok=editor)
    connector.connect_editmenu_signals()
    window.show()
    yield SimpleNamespace(window=window, app=app, lifecycle=lifecycle, action=action,
                          editor=editor, settings=settings, connector=connector, final_save=final_save)
    window.hide()
    window.deleteLater()


@pytest.mark.parametrize('entry, state', [('file', 'checking'), ('file', 'queued'), ('file', 'operation'),
                                         ('file', 'saving'), ('tray', 'checking')])
def test_user_exit_blocks_before_settings_and_project_prompt(shutdown, entry, state):
    s = shutdown
    s.final_save.assert_not_called()  # Registration must not call close eagerly.
    s.app.should_we_save = False  # A bypass must fail an assertion, not open a modal dialog.
    if state == 'checking':
        s.app._check_in_progress = True
    elif state == 'queued':
        s.app._check_queued = True
    elif state == 'operation':
        s.app._update_operation_active.return_value = True
    else:
        s.app.save_in_progress = True
    tray = AppSystemTray(s.app, QtGui.QIcon(), s.window) if entry == 'tray' else None
    action = tray.contextMenu().actions()[-1] if tray else s.action
    action.trigger()
    assert s.window.isVisible()
    s.final_save.assert_not_called()
    s.settings.setValue.assert_not_called()
    s.lifecycle.quit_application.assert_not_called()
    assert s.app.inform.emit.called


@pytest.mark.parametrize('entry', ['file', 'tray'])
def test_unsaved_project_cancel_keeps_window_open_after_settings_saved(shutdown, entry):
    s = shutdown
    dialogs = []

    def cancel_dialog():
        dialog = QtWidgets.QApplication.activeModalWidget()
        assert isinstance(dialog, QtWidgets.QMessageBox)
        # The real lifecycle constructs and executes the real message box.
        keys = {call.args[0] for call in s.settings.setValue.call_args_list}
        dialogs.append(keys)
        cancel = next(button for button in dialog.buttons() if
                      dialog.buttonRole(button) == QtWidgets.QMessageBox.ButtonRole.RejectRole)
        cancel.click()

    tray = AppSystemTray(s.app, QtGui.QIcon(), s.window) if entry == 'tray' else None
    action = tray.contextMenu().actions()[-1] if tray else s.action
    QtCore.QTimer.singleShot(0, cancel_dialog)
    action.trigger()
    assert len(dialogs) == 1
    assert dialogs[0] == {'saved_gui_state', 'toolbar_lock', 'menu_show_text',
                          'window_geometry', 'splitter_left'}
    assert s.window.isVisible()
    s.final_save.assert_called_once_with()
    s.lifecycle.quit_application.assert_not_called()
    s.app.f_handlers.on_file_save_project_as.assert_not_called()
    s.app.trayIcon.hide.assert_not_called()


def test_editor_exit_still_finishes_editing_without_closing(shutdown):
    shutdown.editor.trigger()
    shutdown.app.on_editing_finished.assert_called_once_with()
    assert shutdown.editor.menuRole() == QtGui.QAction.MenuRole.NoRole
    shutdown.final_save.assert_not_called()
    assert shutdown.window.isVisible()


def end_save(s, tmp_path, should_quit=False):
    """The save timer finds the written project file (AppLifecycle.check_project_file_size)."""
    project = tmp_path / 'Board.FlatPrj'
    project.write_bytes(b'{}')
    s.app.save_timer = Mock()
    s.lifecycle.check_project_file_size(str(project), should_quit=should_quit)
    QtWidgets.QApplication.processEvents()


def test_close_during_a_save_closes_the_app_when_the_save_ends(shutdown, tmp_path):
    s = shutdown
    s.app.should_we_save = False
    s.app.save_in_progress = True
    s.action.trigger()
    # the save goes on: the window stays open and nothing is asked yet
    assert s.window.isVisible()
    s.final_save.assert_not_called()
    assert s.app.close_after_save is True

    end_save(s, tmp_path)

    assert s.app.save_in_progress is False
    assert s.app.close_after_save is False
    # the window is closed the same way as the user did: the settings are saved and the app quits
    s.final_save.assert_called_once_with()
    assert s.settings.setValue.called
    s.lifecycle.quit_application.assert_called_once_with()


def test_close_during_a_save_does_not_wait_for_the_next_task_of_the_worker(shutdown, tmp_path):
    s = shutdown
    project = tmp_path / 'Board.FlatPrj'
    project.write_bytes(b'{}')
    s.app.should_we_save = False
    s.app.save_in_progress = True
    s.action.trigger()
    assert s.app.close_after_save is True
    entered, release = threading.Event(), threading.Event()

    class SaveWorker(QtCore.QThread):
        def run(self):
            # the end of save_project() in the worker, then a long task (an NCC) that waits behind the save
            s.lifecycle.start_delayed_quit(delay=10, filename=str(project), should_quit=False)
            entered.set()
            release.wait(5)

    worker = SaveWorker()
    worker.start()
    try:
        assert entered.wait(5)
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
        while s.window.isVisible() and elapsed.elapsed() < 3000:
            QtTest.QTest.qWait(10)
        assert s.app.save_in_progress is False
        s.lifecycle.quit_application.assert_called_once_with()
    finally:
        release.set()
        assert worker.wait(5000)


def test_save_timer_started_in_the_gui_thread_runs_at_once(shutdown, tmp_path):
    # the project switch (ProjectManager) saves in the GUI thread and stops this timer right after the save
    shutdown.lifecycle.start_delayed_quit(delay=500, filename=str(tmp_path / 'Board.FlatPrj'))
    assert isinstance(shutdown.app.save_timer, QtCore.QTimer) and shutdown.app.save_timer.isActive()
    shutdown.app.save_timer.stop()


def test_close_during_a_failed_save_keeps_the_app_open(shutdown, tmp_path):
    from appHandlers.appIO import appIO
    s = shutdown
    # the worker of the save ended with a failure; the GUI does not have the result yet
    s.app.project_save_pending = True
    s.action.trigger()
    s.final_save.assert_called_once_with()
    assert s.app.close_after_save is True

    appIO(s.app)._on_project_save_done(str(tmp_path / 'Board.FlatPrj'), False, False, True)
    assert s.app.close_after_save is False
    assert s.app.should_we_save is True

    # a later save does not close the app
    s.app.save_in_progress = True
    end_save(s, tmp_path)
    s.final_save.assert_called_once_with()
    s.lifecycle.quit_application.assert_not_called()
    assert s.window.isVisible()

