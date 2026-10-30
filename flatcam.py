
import sys
import os
import traceback
from datetime import datetime

from multiprocessing import freeze_support

# A pool child (spawn runs this file again as __mp_main__; a frozen build runs it as __main__ with
# --multiprocessing-fork) does Shapely and triangulation work on one thread.
# This must come before any numpy import: OpenBLAS reserves buffers for every CPU otherwise.
if __name__ == '__mp_main__' or '--multiprocessing-fork' in sys.argv:
    for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ.setdefault(_name, '1')

# The heavy imports (Qt, appMain) are in the __main__ block below, so a pool child does not load the whole application.

MIN_VERSION_MAJOR = 3
MIN_VERSION_MINOR = 10


def debug_trace():
    """
    Set a tracepoint in the Python debugger that works with Qt
    :return: None
    """
    from PyQt6.QtCore import pyqtRemoveInputHook
    # from pdb import set_trace
    pyqtRemoveInputHook()
    # set_trace()


def _shutdown_message_handler(message_type, context, message):
    from PyQt6 import QtCore
    if message.startswith('QThreadStorage: entry ') and ' destroyed before end of thread ' in message:
        return
    # the views and their selection models are destroyed after their models on exit; the selection is a no-op then
    if message.startswith('QItemSelectionModel: Selecting when no model has been set'):
        return

    prefixes = {
        QtCore.QtMsgType.QtDebugMsg: 'DEBUG',
        QtCore.QtMsgType.QtInfoMsg: 'INFO',
        QtCore.QtMsgType.QtWarningMsg: 'WARNING',
        QtCore.QtMsgType.QtCriticalMsg: 'CRITICAL',
        QtCore.QtMsgType.QtFatalMsg: 'FATAL'
    }
    prefix = prefixes.get(message_type, 'WARNING')
    sys.stderr.write('%s: %s\n' % (prefix, message))


def _install_shutdown_message_handler():
    from PyQt6 import QtCore
    QtCore.qInstallMessageHandler(_shutdown_message_handler)


if __name__ == '__main__':
    # All X11 calling should be thread safe otherwise we have strange issues
    # QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_X11InitThreads)
    # NOTE: Never talk to the GUI from threads! This is why I commented the above.
    freeze_support()

    from PyQt6 import QtWidgets, QtGui, QtCore
    from PyQt6.QtCore import QSettings, QTimer
    from appMain import App
    from appCommon.DataPath import data_path_from_env
    from appGUI import VisPyPatches
    from appGUI.GUIElements import FCMessageBox
    from appCommon.SafeEval import safe_eval

    portable = False
    # Resolve the override before reading the default or portable configuration.
    data_path = data_path_from_env()
    if data_path is None and sys.platform == 'win32':
        # #######################################################################################################
        # ####### CONFIG FILE WITH PARAMETERS REGARDING PORTABILITY #############################################
        # #######################################################################################################
        config_file = os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + '\\config\\configuration.txt'
        try:
            with open(config_file, 'r'):
                pass
        except FileNotFoundError:
            config_file = os.path.dirname(os.path.realpath(__file__)) + '\\config\\configuration.txt'

        with open(config_file, 'r') as f:
            for line in f:
                param = str(line).replace('\n', '').rpartition('=')

                if param[0] == 'portable':
                    try:
                        portable = safe_eval(param[2])
                    except (NameError, ValueError):
                        portable = False

        if portable is False:
            # data_path = shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, None, 0) + '\\FlatCAM'
            data_path = os.path.join(os.getenv('appdata'), 'FlatCAM')
        else:
            data_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__))) + '\\config'
    elif data_path is None:
        data_path = os.path.expanduser('~') + '/.FlatCAM'

    if not os.path.exists(data_path):
        os.makedirs(data_path)

    log_file_path = os.path.join(data_path, "log.txt")

    major_v = sys.version_info.major
    minor_v = sys.version_info.minor

    v_msg = "FlatCAM Evo needs Python %s.%s or newer, this is Python %s.%s" % (
        MIN_VERSION_MAJOR, MIN_VERSION_MINOR, str(major_v), str(minor_v))

    # Supported Python version is >= 3.10
    if (major_v, minor_v) < (MIN_VERSION_MAJOR, MIN_VERSION_MINOR):
        print(v_msg, file=sys.stderr)
        msg = '%s\n' % str(datetime.today())
        msg += v_msg

        try:
            with open(log_file_path) as f:
                log_file = f.read()
            log_file += '\n' + msg

            with open(log_file_path, 'w') as f:
                f.write(log_file)
        except IOError:
            with open(log_file_path, 'w') as f:
                f.write(msg)

        # if minor_v >= 8:
        #     os._exit(0)
        # else:
        #     sys.exit(0)
        sys.exit(0)

    debug_trace()
    VisPyPatches.apply_patches()

    def excepthook(exc_type, exc_value, exc_tb):
        # The machine stops only when the application ends: an error in a GUI slot does not stop a running job.
        # The headless run ends here; the GUI ends with QApplication.quit(), and aboutToQuit runs MachineSafety.stop_all().
        if App.cmd_line_headless:
            # first the machine: stop the motion, the spindle and the outputs of the connected controllers
            from appCommon import MachineSafety
            MachineSafety.stop_all()
            # no window for a message box: the traceback goes to stderr and the run ends with exit code 1
            sys.stderr.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            sys.stderr.flush()
            os._exit(1)
        msg = '%s\n' % str(datetime.today())
        if exc_type != KeyboardInterrupt:
            msg += "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

            try:
                with open(log_file_path) as f:
                    log_file = f.read()
                log_file += '\n' + msg

                with open(log_file_path, 'w') as f:
                    f.write(log_file)
            except IOError:
                with open(log_file_path, 'w') as f:
                    f.write(msg)

            # show the message
            try:
                msgbox = FCMessageBox()
                displayed_msg = "The application encountered a critical error and it will close.\n"\
                                "Please report this error to the developers."
                title = "Critical Error"
                msgbox.setWindowTitle(title)  # taskbar still shows it
                ic = QtGui.QIcon()
                ic.addPixmap(QtGui.QPixmap("assets/resources/warning.png"), QtGui.QIcon.Mode.Normal)
                msgbox.setWindowIcon(ic)
                msgbox.setText('<b>%s</b>' % displayed_msg)
                msgbox.setDetailedText(msg)
                msgbox.setIcon(QtWidgets.QMessageBox.Icon.Critical)

                bt_yes = msgbox.addButton("Quit", QtWidgets.QMessageBox.ButtonRole.YesRole)
                bt_ret = msgbox.addButton("Return", QtWidgets.QMessageBox.ButtonRole.NoRole)

                msgbox.setDefaultButton(bt_yes)
                # msgbox.setTextFormat(Qt.TextFormat.RichText)
                msgbox.exec()

                # Quit ends the application; Return lets it (and a running job) continue
                if msgbox.clickedButton() == bt_yes:
                    # stop the machine now: a window can refuse the close (an updater or a save is active)
                    from appCommon import MachineSafety
                    MachineSafety.stop_all()
                    QtWidgets.QApplication.quit()
            except Exception:
                QtWidgets.QApplication.quit()
        else:
            QtWidgets.QApplication.quit()
        # or QtWidgets.QApplication.exit(0)

    sys.excepthook = excepthook

    if App.cmd_line_headless:
        # no display and no GPU: Qt draws nothing and needs no X11, Wayland or Windows desktop
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        if sys.platform == 'win32':
            # the offscreen platform does not know the Windows fonts ("QFontDatabase: Cannot find font directory")
            os.environ.setdefault('QT_QPA_FONTDIR', os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts'))
        from appCommon.Headless import quiet_logs, headless_qt_message_handler
        quiet_logs()
        # the Qt warnings are about widgets nobody sees; only the critical and fatal messages reach stderr
        QtCore.qInstallMessageHandler(headless_qt_message_handler)

    if sys.platform == 'win32' and not getattr(sys, 'frozen', False):
        # Started by pythonw.exe (a double click on an associated file), the window got the taskbar button of
        # pythonw.exe, without the FlatCAM icon. An own AppUserModelID gives it a FlatCAM button with the window icon.
        # Not for FlatCAM.exe: it has the icon, and the ID would split a pinned shortcut from the running window.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('FlatCAM.EVO')
        except (AttributeError, OSError):
            pass

    app = QtWidgets.QApplication(sys.argv)
    app.aboutToQuit.connect(_install_shutdown_message_handler)

    # The listener for the files of a second launch starts here, before the App is built: files opened together in the
    # Explorer start several processes at once and only one of them keeps the window. The others hand their files to
    # it and end here. The files that arrive while the App is starting are kept until it is ready.
    # The options (--shellfile, --command, --shellvar) belong to this process: they start a new one.
    launch_server = None
    no_answer = False
    if not App.cmd_line_headless:
        from appCommon.SingleInstance import start_listening, send_to_running
        launch_server, listening = start_listening(app)
        if (not listening and App.args and not App.cmd_line_shellfile and not App.cmd_line_command and
                not App.cmd_line_shellvar):
            from appCommon.StartDir import absolute_paths
            if send_to_running(absolute_paths(App.args)):
                os._exit(0)
            # no answer: the files are not lost, this launch opens them itself
            sys.stderr.write("The open FlatCAM window did not answer: the files open in this one.\n")
            no_answer = True

    # apply style
    settings = QSettings("Open Source", "FlatCAM_EVO")
    styles = QtWidgets.QStyleFactory.keys()
    style = app.style().objectName()
    if settings.contains("style"):
        saved_style = settings.value('style', type=str)
        # Older versions stored a position in Qt's platform-dependent style list.
        try:
            idx = int(saved_style)
        except ValueError:
            style = next((name for name in styles if name.lower() == saved_style.lower()), style)
        else:
            if 0 <= idx < len(styles):
                style = styles[idx]
        app.setStyle(style)
        settings.setValue('style', style)
    # Application stylesheets wrap QStyle and hide its objectName().
    app.setProperty('flatcam_style', style)

    if settings.contains("font_size"):
        font_size = int(settings.value("font_size", type=str))      # noqa
        font = QtGui.QFont()
        font.setPointSize(font_size)
        app.setFont(font)

    fc = App(qapp=app, launch_server=launch_server)

    if fc.headless:
        from appCommon.Headless import HeadlessRunner
        os._exit(HeadlessRunner(fc).run())

    if no_answer:
        # the user sees that this is a second window: the open one did not answer
        import builtins
        _ = builtins.__dict__.get('_', lambda text: text)
        QTimer.singleShot(0, lambda: fc.inform.emit(
            '[WARNING_NOTCL] %s' % _("The open FlatCAM window did not answer. The files open in this new window.")))

    # interrupt the Qt loop such that Python events have a chance to be responsive
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(100)

    # Use os._exit() after the event loop ends to avoid Python GC destroying
    # Qt/VisPy C++ objects in unpredictable order, which causes 0xC0000005 on Windows.
    # All important cleanup (preferences, threads, pool) is done in quit_application().
    ret = app.exec()
    os._exit(ret)
