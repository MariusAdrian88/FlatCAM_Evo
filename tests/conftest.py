"""pytest configuration for the FlatCAM tests.

appMain.App parses sys.argv with getopt when the class is defined, and it calls sys.exit(2) on an unknown
option. pytest options such as -q or -x would stop every test that imports appMain (for example Gerber() imports
it). Import appMain 1 time here with a clean sys.argv; the later imports get the module from sys.modules.
"""

import os
import sys
import warnings

# The SWIG wrappers of ortools (pywrapcp) give 3 DeprecationWarnings at import on Python 3.12+:
# "builtin type SwigPyPacked has no __module__ attribute". The text is not from FlatCAM, so do not show it.
_SWIG_WARNING = r'builtin type (SwigPyPacked|SwigPyObject|swigvarlink) has no __module__ attribute'
warnings.filterwarnings('ignore', message=_SWIG_WARNING, category=DeprecationWarning)

# Select native OpenGL support before collection can default Qt to offscreen.
if sys.platform == 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'windows')

# Each test process has its own single-instance socket: a test App or a SingleInstance test must not send files to
# the FlatCAM window of the user, or to the App of another xdist worker that does not run its event loop.
os.environ['FLATCAM_INSTANCE_NAME'] = 'FlatCAM_EVO-test-%d' % os.getpid()

# The updater subprocesses of the tests must not show the progress window: a failed job waits for "Close".
os.environ['FLATCAM_UPDATER_NO_WINDOW'] = '1'

# The G-code Sender is disabled for users until it is finished; its tests need it to open.
os.environ['FLATCAM_SENDER_DEV'] = '1'


# Each test process has its own empty data folder. Without it a test App reads the preferences of the user
# (%APPDATA%/FlatCAM) and can write them; it also copied the appearance of the user into the test QSettings, so a
# later Preferences save in the same worker saw a changed appearance and waited in a hidden "restart" message box.
def _isolate_data_path():
    import atexit
    import shutil
    import tempfile
    folder = tempfile.mkdtemp(prefix='flatcam-test-data-')
    atexit.register(shutil.rmtree, folder, True)
    os.environ['FLATCAM_DATA_PATH'] = folder


_isolate_data_path()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# The tests must not write the settings of the user: a test run wrote the layout 'standard' into the registry, and
# FlatCAM then started with the standard layout. QSettings("Open Source", "FlatCAM_EVO") goes to an INI file of
# this test session. Qt ignores QSettings.setDefaultFormat() for this constructor, so the class is replaced before
# the application modules import it.
def _isolate_qsettings():
    import atexit
    import shutil
    import tempfile
    try:
        from PyQt6 import QtCore
    except ImportError:
        return
    folder = tempfile.mkdtemp(prefix='flatcam-test-settings-')
    atexit.register(shutil.rmtree, folder, True)
    native = QtCore.QSettings

    class TestSettings(native):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and isinstance(args[1], native.Format):
                super().__init__(*args, **kwargs)     # an explicit file: QSettings(path, format)
                return
            names = [arg for arg in args if isinstance(arg, str)] or ['default']
            path = os.path.join(folder, *[name.replace(os.sep, '_') for name in names]) + '.ini'
            super().__init__(path, native.Format.IniFormat)

    QtCore.QSettings = TestSettings


_isolate_qsettings()

_saved_argv = sys.argv
sys.argv = sys.argv[:1]
try:
    import appMain  # noqa: F401
except ImportError:
    # without the GUI packages the tests that need appMain fail on their own
    pass
finally:
    sys.argv = _saved_argv


# Each App makes a process pool of cpu_count / 4 processes, and a test App stays alive until its worker ends: a gate
# run had 44 pool processes beside its 8 workers, and the load made other tests fail at random. All Apps of a test
# process share 1 pool of 2 processes. It starts at its first use, and the quit code of an App does not stop it.
def _share_one_pool():
    module = sys.modules.get('appMain')
    if module is None:
        return
    import multiprocessing.pool
    shared = []

    class SharedPool:
        def __getattr__(self, name):
            if not shared:
                shared.append(multiprocessing.pool.Pool(processes=2, maxtasksperchild=100))
            return getattr(shared[0], name)

        def close(self):
            pass

        def terminate(self):
            pass

        def join(self):
            pass

    module.Pool = lambda *args, **kwargs: SharedPool()


_share_one_pool()


# The GUI tests show real windows. A window with WA_DontShowOnScreen is shown for Qt (visible, laid out, grab()
# works, the window states change) but it does not appear on the desktop, so the tests do not flash windows.
# Set FLATCAM_TEST_SHOW_WINDOWS=1 to see the windows.
_window_filter = None


def _install_window_filter():
    global _window_filter
    if _window_filter is not None or os.environ.get('FLATCAM_TEST_SHOW_WINDOWS'):
        return
    try:
        from PyQt6 import QtCore, QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    class HideWindows(QtCore.QObject):
        def eventFilter(self, obj, event):
            # Polish comes before the first show of a widget, so the attribute is set in time
            if event.type() == QtCore.QEvent.Type.Polish and isinstance(obj, QtWidgets.QWidget) and obj.isWindow():
                obj.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            elif event.type() == QtCore.QEvent.Type.Show and isinstance(obj, QtWidgets.QMessageBox):
                self.mute_message_box(obj)
            return False

        @staticmethod
        def mute_message_box(box):
            # The Windows platform plugin plays a system sound when a message box with the Information, Warning or
            # Critical icon is shown, also when it is not on the screen. Show it without the icon, then put it back.
            icon = box.icon()
            if icon in (QtWidgets.QMessageBox.Icon.NoIcon, QtWidgets.QMessageBox.Icon.Question):
                return
            box.setIcon(QtWidgets.QMessageBox.Icon.NoIcon)

            def restore():
                try:
                    box.setIcon(icon)
                except RuntimeError:
                    pass    # the box was deleted

            QtCore.QTimer.singleShot(0, restore)

    _window_filter = HideWindows()
    app.installEventFilter(_window_filter)


# The test windows are not on the screen, so nobody can answer a modal dialog that a test did not patch: exec() then
# waits for ever and the whole run hangs with no report. The watchdog closes a modal dialog that stays open longer
# than this limit, and the running test fails with the title and the text of the dialog.
_MODAL_LIMIT_S = 30.0
_modal_watchdog = None


def _install_modal_watchdog():
    global _modal_watchdog
    if _modal_watchdog is not None:
        try:
            if _modal_watchdog.timer.isActive():
                return
        except RuntimeError:
            pass    # the QApplication that owned it was deleted
        _modal_watchdog = None
    try:
        from PyQt6 import QtCore, QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    import time

    class ModalWatchdog(QtCore.QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.widget = None
            self.since = 0.0
            self.closed = []
            # the timer also runs in the event loop of exec()
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(500)
            self.timer.timeout.connect(self.check)
            self.timer.start()

        def check(self):
            widget = QtWidgets.QApplication.activeModalWidget()
            if widget is not self.widget:
                self.widget, self.since = widget, time.monotonic()
                return
            if widget is None or time.monotonic() - self.since < _MODAL_LIMIT_S:
                return
            text = widget.text() if isinstance(widget, QtWidgets.QMessageBox) else ''
            self.closed.append('%s %r %s' % (type(widget).__name__, widget.windowTitle(), text))
            self.widget = None
            if isinstance(widget, QtWidgets.QDialog):
                widget.reject()
            else:
                widget.close()

    _modal_watchdog = ModalWatchdog(app)


def _raise_modal_timeouts():
    if _modal_watchdog is not None and _modal_watchdog.closed:
        closed = list(_modal_watchdog.closed)
        _modal_watchdog.closed.clear()
        raise RuntimeError('a modal dialog waited for an answer for %d s and was closed (patch it in the test): %s'
                           % (_MODAL_LIMIT_S, '; '.join(closed)))


# PyQt6 aborts the process when a slot or a virtual method raises and sys.excepthook is the default one. The app sets
# its own hook (flatcam.py); without one, the error kills the whole worker and the report names no test. This hook
# keeps the error, and the teardown of the running test fails with it.
_qt_errors = []


def _qt_excepthook(kind, value, trace):
    import traceback
    _qt_errors.append(''.join(traceback.format_exception(kind, value, trace)))


sys.excepthook = _qt_excepthook


def _raise_qt_errors():
    if _qt_errors:
        errors = list(_qt_errors)
        _qt_errors.clear()
        raise RuntimeError('a Qt callback raised during the test:\n' + '\n'.join(errors))


def _release_modifiers():
    # QTest key events with Ctrl / Shift / Alt leave that state in QApplication.keyboardModifiers(); a later plain
    # click in another test then extends a selection. Release the keys after each test.
    try:
        from PyQt6 import QtCore, QtTest, QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is None or QtWidgets.QApplication.keyboardModifiers() == QtCore.Qt.KeyboardModifier.NoModifier:
        return
    target = QtWidgets.QWidget()
    try:
        QtTest.QTest.keyRelease(target, QtCore.Qt.Key.Key_Control, QtCore.Qt.KeyboardModifier.NoModifier)
    finally:
        target.deleteLater()


import pytest  # noqa: E402


def pytest_runtest_setup(item):
    _qt_errors.clear()
    _install_window_filter()
    _install_modal_watchdog()
    if _modal_watchdog is not None:
        _modal_watchdog.closed.clear()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    # unittest runs setUp in the call phase: a QApplication made in setUpClass is here
    _install_window_filter()
    _install_modal_watchdog()
    result = yield
    _raise_qt_errors()
    _raise_modal_timeouts()
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        return (yield)
    finally:
        _release_modifiers()
        _raise_qt_errors()


# Hardware tests (a connected E-Cut USB board or a GRBL serial board) do not run by default.
# Mark such a test with @pytest.mark.hardware and set FLATCAM_TEST_HARDWARE=1 to run it.
# All other tests cannot open a real HID device or serial port: a board that is connected stays untouched.

HARDWARE_TESTS = os.environ.get('FLATCAM_TEST_HARDWARE') == '1'
# Timing tests measure the GUI thread while a worker holds the GIL; the result depends on the machine load and on
# the earlier tests, so they do not run by default. Mark such a test with @pytest.mark.timing and set
# FLATCAM_TEST_TIMING=1 to run it.
TIMING_TESTS = os.environ.get('FLATCAM_TEST_TIMING') == '1'
# DipTrace parser tests take minutes; they run only for a change of the DipTrace importer. Mark such a test with
# @pytest.mark.diptrace and set FLATCAM_TEST_DIPTRACE=1 to run it.
DIPTRACE_TESTS = os.environ.get('FLATCAM_TEST_DIPTRACE') == '1'
# Allegro 16.x board tests need the sample boards and their Gerber films (not in the repository: sizes and licences).
# Mark such a test with @pytest.mark.allegro16 and set ALLEGRO16_EXAMPLES to the corpus folder (D:\1.Development\Allegro_corpus).
ALLEGRO16_TESTS = os.path.isdir(os.environ.get('ALLEGRO16_EXAMPLES', ''))
_HARDWARE_BLOCKED = 'real hardware access is blocked in tests; mark the test hardware and set FLATCAM_TEST_HARDWARE=1'


def pytest_configure(config):
    config.addinivalue_line('markers', 'hardware: needs a connected E-Cut or GRBL board (FLATCAM_TEST_HARDWARE=1)')
    config.addinivalue_line('markers', 'timing: measures GUI responsiveness, not reliable in a full run (FLATCAM_TEST_TIMING=1)')
    config.addinivalue_line('markers', 'diptrace: slow DipTrace importer test (FLATCAM_TEST_DIPTRACE=1)')
    config.addinivalue_line('markers', 'allegro16: Allegro 16.x board test on the sample corpus (ALLEGRO16_EXAMPLES=<folder>)')
    # slow tests are never skipped. The fast default run is: pytest tests -m "not slow". A plain run does all tests.
    config.addinivalue_line('markers', 'slow: takes a long time (benchmarks, big boards, subprocess runs); deselect with -m "not slow"')
    # pytest resets the warning filters for each test; this line keeps the SWIG filter in the tests
    config.addinivalue_line('filterwarnings', f'ignore:{_SWIG_WARNING}:DeprecationWarning')


def pytest_collection_modifyitems(config, items):
    skip_hardware = pytest.mark.skip(reason='hardware test: set FLATCAM_TEST_HARDWARE=1 to run it')
    skip_timing = pytest.mark.skip(reason='timing test: set FLATCAM_TEST_TIMING=1 to run it')
    skip_diptrace = pytest.mark.skip(reason='DipTrace test: set FLATCAM_TEST_DIPTRACE=1 to run it')
    skip_allegro16 = pytest.mark.skip(reason='Allegro 16.x corpus test: set ALLEGRO16_EXAMPLES to the corpus folder')
    for item in items:
        if not DIPTRACE_TESTS and item.get_closest_marker('diptrace'):
            item.add_marker(skip_diptrace)
        if not ALLEGRO16_TESTS and item.get_closest_marker('allegro16'):
            item.add_marker(skip_allegro16)
        if not HARDWARE_TESTS and item.get_closest_marker('hardware'):
            item.add_marker(skip_hardware)
        if not TIMING_TESTS and item.get_closest_marker('timing'):
            item.add_marker(skip_timing)


class _BlockedHid:
    """Stands in for the hidapi module: any use fails, so no test reaches a real USB board."""

    def __getattr__(self, name):
        raise RuntimeError(_HARDWARE_BLOCKED)


@pytest.fixture(autouse=True)
def _no_real_hardware(request, monkeypatch):
    if HARDWARE_TESTS and request.node.get_closest_marker('hardware'):
        return
    monkeypatch.setitem(sys.modules, 'hid', _BlockedHid())
    try:
        import serial
    except ImportError:
        return

    def blocked_open(self, *args, **kwargs):
        raise serial.SerialException(_HARDWARE_BLOCKED)

    # the platform class of serial.Serial; loop:// and the test fakes use other classes
    monkeypatch.setattr(serial.Serial, 'open', blocked_open)


# MainGUI.__init__ sets FCLabel / FCButton.patching_text_color to its theme_safe_color for the whole process. A test
# that makes a real App (headless) would leave the theme colors of that App to the label tests that come after it.
# optional: skip the restore when the GUI package cannot be imported
try:
    from appGUI.GUIElements import FCButton as _FCButton, FCLabel as _FCLabel
    _THEME_COLOR_CLASSES = [(cls, vars(cls)['patching_text_color']) for cls in (_FCLabel, _FCButton)]
except (ImportError, KeyError):
    _THEME_COLOR_CLASSES = []


@pytest.fixture(autouse=True)
def _restore_theme_colors():
    yield
    for cls, original in _THEME_COLOR_CLASSES:
        if vars(cls).get('patching_text_color') is not original:
            cls.patching_text_color = original


# The App of a test module stays alive until the interpreter shuts down. Then Python destroys the Qt / VisPy C++
# objects in a random order and the process can crash after the summary (0xC0000005 on Windows, in sip). flatcam.py
# ends with os._exit() for the same reason, after quit_application() stopped the worker threads and the pool. The
# tests do not stop them, so on Windows also os._exit() can hang (ExitProcess runs the DLL detach code while those
# threads run): end the process with TerminateProcess. Only when pytest is the program (python -m pytest,
# pytest.exe): a script that calls pytest.main() (tests/gates/run_gate.py) reads the results after it.
_session = None


def _pytest_is_the_program():
    spec = getattr(sys.modules.get('__main__'), '__spec__', None)
    if spec is not None:
        return spec.name in ('pytest', 'pytest.__main__')
    return os.path.splitext(os.path.basename(sys.argv[0]))[0] in ('pytest', 'py.test')


def pytest_sessionfinish(session, exitstatus):
    global _session
    _session = session


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    if _session is None or hasattr(config, 'workerinput') or not _pytest_is_the_program():
        return
    try:
        from PyQt6 import QtWidgets
    except ImportError:
        return
    if QtWidgets.QApplication.instance() is None:
        return

    import gc
    import logging
    import multiprocessing.pool
    # stop the pools of the Apps like quit_application() does; a pool starts a new process for each one that ends
    for pool in [o for o in gc.get_objects() if isinstance(o, multiprocessing.pool.Pool)]:
        pool.terminate()
    for child in multiprocessing.active_children():
        child.terminate()
        child.join(2)
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    code = int(_session.exitstatus)
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)
