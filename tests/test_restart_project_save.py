"""A restart (language, font size, preferences that need a restart) saves the projects first and waits for the save.

os.execl() ends the process at once, so a project save that still runs in a worker thread is lost. The tests drive
the real restart_program() with os.execl() replaced, the real save (appIO.save_project(), slowed down) and the real
ProjectManager, and check the state of the project file at the moment of the restart.
"""

import json
import os
import sys
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6 import QtCore, QtWidgets

import appTranslation
import appHandlers.appIO as appio_module
from appGUI.GUIElements import FCFileSaveDialog
from appHandlers.appIO import appIO
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appProjects import ProjectManager
from defaults import AppOptions

YES = QtWidgets.QMessageBox.ButtonRole.YesRole
NO = QtWidgets.QMessageBox.ButtonRole.NoRole
CANCEL = QtWidgets.QMessageBox.ButtonRole.RejectRole


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeSettings:
    """QSettings without the registry or the settings file of the user."""
    values = {}

    def __init__(self, *args):
        pass

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class Defaults(dict):
    factory_defaults = {}


class FakeCollection:
    def __init__(self, objects=()):
        self.view = QtWidgets.QWidget()
        self.objects = list(objects)

    def get_list(self):
        return list(self.objects)

    def get_active(self):
        return None

    def has_promises(self):
        return False

    def has_plot_promises(self):
        return False


class Board:
    """An object of a project; save_project() writes its dict."""

    def __init__(self, name):
        self.name = name

    def to_dict(self):
        return {'kind': 'geometry', 'obj_options': {'name': self.name}}


class Workers:
    """app.worker_task: runs each task in a thread, like the workers of the App."""

    def __init__(self):
        self.threads = []

    def emit(self, task):
        thread = threading.Thread(target=task['fcn'], args=task['params'], daemon=True)
        self.threads.append(thread)
        thread.start()

    def running(self):
        return any(thread.is_alive() for thread in self.threads)

    def join(self):
        for thread in self.threads:
            thread.join(10)


class Dialogs(QtCore.QObject):
    """Answers the modal message boxes with the button roles in self.answers, in order."""

    def __init__(self):
        super().__init__()
        self.answers = []
        self.asked = 0
        self.unexpected = []
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        box = QtWidgets.QApplication.activeModalWidget()
        if not isinstance(box, QtWidgets.QMessageBox) or not box.isVisible() or box.property('answered'):
            return
        box.setProperty('answered', True)
        self.asked += 1
        role = self.answers.pop(0) if self.answers else None
        button = next((b for b in box.buttons() if box.buttonRole(b) == role), None)
        if button is None:
            self.unexpected.append((role, [b.text() for b in box.buttons()]))
            box.done(0)
        else:
            button.click()


def project_names(filename):
    with open(filename, 'r') as stream:
        return [obj['obj_options']['name'] for obj in json.load(stream)['objs']]


@pytest.fixture
def restart(qt_app, tmp_path, monkeypatch):
    FakeSettings.values = {}
    monkeypatch.setattr(appTranslation, 'QSettings', FakeSettings)

    window = QtWidgets.QWidget()
    window.set_ui_title = Mock()
    window.general_pref_form = SimpleNamespace(general_app_group=SimpleNamespace(
        language_combo=SimpleNamespace(currentText=lambda: 'Deutsch')))
    defaults = Defaults({'units': 'MM', 'global_systray_icon': True})
    workers = Workers()
    app = SimpleNamespace(
        log=Mock(), inform=Mock(), splash=Mock(), defaults=defaults, app_units='MM', version='test',
        options=AppOptions(version='test', baseline=defaults, defaults=defaults), ui=window,
        new_project_signal=Mock(), worker_task=workers, workers=SimpleNamespace(pending_count=lambda: 0),
        proc_container=SimpleNamespace(new=lambda text: nullcontext(), get_procs_count=lambda: 0),
        call_source='app', app_obj=SimpleNamespace(load_batches=set()), plotcanvas=None,
        collection=FakeCollection([Board('Top')]), all_objects_list=[], project_filename=None,
        should_we_save=True, save_in_progress=False, project_save_pending=False, close_after_save=False,
        resource_location='assets/resources', file_opened=Mock(), file_saved=Mock(),
        get_last_save_folder=lambda: str(tmp_path), start_delayed_quit=Mock(), preferencesUiManager=Mock(),
        new_launch=Mock(), trayIcon=Mock())
    app.f_handlers = appIO(app)
    projects = ProjectManager(app, collection_factory=FakeCollection)
    # the steps that change the canvas and the plugins
    projects.before_switch = Mock()
    projects.deactivate = Mock()
    projects.activate = Mock()
    projects.reset_options = Mock()
    app.projects = projects

    # the Save As dialog
    chosen = {'path': str(tmp_path / 'Board.FlatPrj')}
    save_dialog = Mock(side_effect=lambda *args, **kwargs: (chosen['path'], ''))
    monkeypatch.setattr(FCFileSaveDialog, 'get_saved_filename', save_dialog)

    # a slow project write, as for a big project
    real_write = appio_module.save_project_file

    def slow_write(filename, *args, **kwargs):
        time.sleep(0.3)
        return real_write(filename, *args, **kwargs)

    monkeypatch.setattr(appio_module, 'save_project_file', slow_write)

    # the restart: the state of the save when os.execl() is called
    restarts = []

    def execl(*args):
        path = chosen['path']
        restarts.append(SimpleNamespace(
            args=args, saving=workers.running(), save_pending=app.project_save_pending,
            names=project_names(path) if os.path.exists(path) else None))

    monkeypatch.setattr(os, 'execl', execl)

    dialogs = Dialogs()
    yield SimpleNamespace(app=app, projects=projects, chosen=chosen, save_dialog=save_dialog, restarts=restarts,
                          dialogs=dialogs, workers=workers, tmp_path=tmp_path)
    dialogs.timer.stop()
    workers.join()
    QtWidgets.QApplication.processEvents()
    window.deleteLater()


def assert_no_restart(s):
    assert s.restarts == []
    # the app goes on: the listener of the second instances is not stopped, the settings file is not written
    s.app.new_launch.close.assert_not_called()
    s.app.preferencesUiManager.save_defaults.assert_not_called()
    assert s.app.inform.emit.call_args[0][0].startswith('[WARNING_NOTCL]')


@pytest.mark.parametrize('has_file', [False, True], ids=['save-as', 'save'])
def test_yes_restarts_only_after_the_project_file_is_written(restart, has_file):
    s = restart
    if has_file:
        # an older version of the project file; Save writes the same file without a dialog
        with open(s.chosen['path'], 'w') as stream:
            json.dump({'objs': [], 'options': {}, 'version': 'old'}, stream)
        s.app.project_filename = s.chosen['path']
        s.projects.adopt_filename(s.chosen['path'])
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 1 and s.dialogs.unexpected == []
    assert len(s.restarts) == 1
    done = s.restarts[0]
    assert done.args[0] == sys.executable
    # the save had ended and the file holds the project when the process was replaced
    assert not done.saving and not done.save_pending
    assert done.names == ['Top']
    assert s.app.should_we_save is False
    assert s.app.project_filename == s.chosen['path']
    assert s.save_dialog.call_count == (0 if has_file else 1)


def test_cancelled_save_dialog_does_not_restart(restart):
    s = restart
    s.chosen['path'] = ''
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.unexpected == []
    s.save_dialog.assert_called_once()
    assert_no_restart(s)
    assert s.app.should_we_save is True


def test_failed_save_does_not_restart(restart, monkeypatch):
    s = restart

    def denied(*args, **kwargs):
        raise PermissionError('the file is open in another application')

    monkeypatch.setattr(appio_module, 'save_project_file', denied)
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.unexpected == []
    s.workers.join()
    assert_no_restart(s)
    assert not os.path.exists(s.chosen['path'])
    assert s.app.should_we_save is True
    assert s.app.save_in_progress is False


def test_changed_inactive_project_is_saved_before_the_restart(restart):
    # the same rule as the exit of the app: each project with changes is asked about, not only the active one
    s = restart
    s.app.should_we_save = False
    start = s.projects.active
    other = s.projects.add_context('Board_B')
    other.collection.objects = [Board('Bottom')]
    other.should_we_save = True
    s.chosen['path'] = str(s.tmp_path / 'Board_B.FlatPrj')
    s.dialogs.answers = [YES]

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 1 and s.dialogs.unexpected == []
    assert len(s.restarts) == 1
    assert s.restarts[0].names == ['Bottom']
    assert other.should_we_save is False
    # the canvas still shows the first project: it is the active one again
    assert s.projects.active is start
    assert s.app.collection is start.collection


@pytest.mark.parametrize('entry, state', [('restart_program', 'worker'), ('restart_program', 'result'),
                                          ('language', 'worker'), ('preferences', 'result')])
def test_running_save_is_not_killed_by_a_restart(restart, monkeypatch, entry, state):
    """Every caller of restart_program(): a save that runs (File -> Save, Save As) is never cut by a restart."""
    s = restart
    # _queue_project_save() cleared the dirty flag, the worker writes the file
    s.app.should_we_save = False
    s.app.project_save_pending = True
    # the worker ended but the GUI does not have the result yet
    s.app.save_in_progress = state == 'worker'
    s.dialogs.answers = [YES]

    if entry == 'restart_program':
        appTranslation.restart_program(app=s.app)
    elif entry == 'language':
        FakeSettings.values['language'] = 'English'
        appTranslation.on_language_apply_click(app=s.app, restart=True)
        assert s.dialogs.asked == 1
        assert FakeSettings.values['language'] == 'Deutsch'
    elif entry == 'font_size':
        import appGUI.preferences.general.GeneralGUIPrefGroupUI as gui_prefs
        monkeypatch.setattr(gui_prefs, 'QSettings', FakeSettings)
        gui_prefs.GeneralGUIPrefGroupUI.handle_font_size(s.app, 12)
        assert FakeSettings.values['font_size'] == '12'
    else:
        # Preferences -> Save with a new appearance, style or title bar, and the icon color
        AppLifecycle(s.app).on_app_restart()
        s.app.trayIcon.hide.assert_called_once_with()

    assert s.dialogs.unexpected == []
    assert_no_restart(s)
    # the tray icon is shown again when the app does not restart
    s.app.trayIcon.show.assert_called_once_with()
    s.save_dialog.assert_not_called()


@pytest.mark.parametrize('busy', ['task'])
def test_busy_app_with_changes_does_not_ask_and_does_not_restart(restart, busy):
    # save_context() cannot save now, so the question is not asked and the reason is the last message
    s = restart
    if busy == 'editor':
        s.app.call_source = 'geo_editor'
    else:
        s.app.proc_container = SimpleNamespace(new=lambda text: nullcontext(), get_procs_count=lambda: 1)

    appTranslation.restart_program(app=s.app)

    assert s.dialogs.asked == 0
    assert_no_restart(s)
    text = s.app.inform.emit.call_args[0][0]
    assert ('Close the editor first.' if busy == 'editor' else 'A task is running.') in text
    assert 'FlatCAM did not restart.' in text

