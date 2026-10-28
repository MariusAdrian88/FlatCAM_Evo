# ###########################################################
# FlatCAM: 2D Post-processing for Manufacturing             #
# http://flatcam.org                                        #
# Author: Juan Pablo Caram (c)                              #
# Date: 2/5/2014                                            #
# MIT Licence                                               #
# Modified by Marius Stanciu (2019)                         #
# ###########################################################

from appHandlers.appHistory import history_of, object_state, SnapshotStep
from appCommon.TclExecution import catch_tcl, completion_code
from collections import deque

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa
from PyQt6.QtCore import QSettings, pyqtSlot  # noqa
from PyQt6.QtCore import Qt, pyqtSignal, QMetaObject  # noqa

import os.path
import sys
import threading

from datetime import datetime as dt
from copy import deepcopy
from pathlib import Path

import getopt
import random
import json
import shutil
import traceback
import time
import re
import html

from io import StringIO

import gc

from multiprocessing import Pool, current_process

import tkinter as tk

import libs.qdarktheme
import libs.qdarktheme.themes.dark.stylesheet as qdarksheet
import libs.qdarktheme.themes.light.stylesheet as qlightsheet

from typing import Union
from ast import literal_eval

# ###################################      Imports part of FlatCAM       #############################################
# App appGUI
from appGUI.PlotCanvas import PlotCanvas
from appGUI.NullCanvas import NullCanvas
from appGUI.MainGUI import MainGUI, cursor_screen

from appGUI.GUIElements import (
    FCMessageBox,
    FCFileSaveDialog,
    message_dialog,
    AppSystemTray,
)
from appGUI.UpdateDialog import UpdateDialog
from appGUI.themes import dark_style_sheet, light_style_sheet

# Various
from appCommon.Common import ExclusionAreas
from appCommon.Common import AppLogging
from appCommon.SingleInstance import InstanceServer, FileOpenFilter, add_to_os_recent
from appCommon.StartDir import resolve_user_path, resolve_existing_paths, local_path_from_url
from appCommon.DataPath import data_path_from_env, DATA_PATH_ENV
from appCommon.RegisterFileKeywords import RegisterFK, Extensions, KeyWords
from appCommon.SafeEval import safe_eval

from appHandlers.appIO import appIO
from appHandlers.appEdit import appEditor
from appHandlers.appPlotManager import AppPlotManager
from appHandlers.appCanvasEvents import AppCanvasEvents
from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appSignalConnector import AppSignalConnector
from appHandlers.appUIActions import AppUIActions
from appHandlers.appLifecycle import AppLifecycle

from services.updater.digi.release import prepare_releases, validate_release_roots
from services.updater.github import GitHubUpdateChecker     # noqa
from services.updater.github.staging import stage_update
from services.updater.digi.staging import stage_update_archive
from services.updater.provider import PROVIDER_DIGI, checker_for, create_update_checker, update_provider
from services.updater.launcher import launch_rollback, launch_update
from services.updater.recovery import load_restore_point, restore_dir_for_install
from services.updater.common import DownloadCancelled

from Bookmark import BookmarkManager
# from appDatabase import ToolsDB2

# App defaults (preferences)
from defaults import AppDefaults
from defaults import AppOptions

# App Objects
from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
from appObjects.ObjectCollection import (
    ObjectCollection,
    GerberObject,
    ExcellonObject,
    GeometryObject,
    CNCJobObject,
    ScriptObject,
    DocumentObject,
)
from appHandlers.appProjects import ProjectManager
from appGUI.ProjectSelector import ProjectSelector
from appObjects.AppObject import AppObject
from appObjects.ProjectContext import safe_file_name, unique_name

# App Parsing files
from camlib import to_dict

# App Pre-processors
from appPreProcessor import load_preprocessors

# App appEditors
from appEditors.appGeoEditor import AppGeoEditor
from appEditors.appExcEditor import AppExcEditor
from appEditors.appGerberEditor import AppGerberEditor, edit_copy_name
from appEditors.appTextEditor import AppTextEditor
from appEditors.appGCodeEditor import AppGCodeEditor
from appParsers import feature_access

# App Workers
from appProcess import *
from appWorkerStack import WorkerStack

# App Plugins
from appPlugins import *

try:
    from numpy import Inf
except ImportError:
    from numpy import inf as Inf  # noqa

# App Translation
import gettext
import appTranslation as fcTranslate
import builtins

import darkdetect

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _release_build_identity(app, fallback=""):
    for name in ("build_string", "build", "version_date"):
        try:
            value = getattr(app, name, None)
        except RuntimeError:
            value = None
        if value:
            return str(value)
    return str(fallback or "")


# object kind -> name of the App attribute that holds its editor
_EDITOR_ATTR_BY_KIND = {
    'geometry': 'geo_editor',
    'excellon': 'exc_editor',
    'gerber': 'grb_editor',
    'cncjob': 'gcode_editor',
}


class FCSplashScreen(QtWidgets.QSplashScreen):
    """
    The splash screen. The messages are drawn a little away from the left edge.
    """

    def __init__(self, offset: int = 0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._offset = offset

    @property
    def offset_message(self) -> int:
        return self._offset

    @offset_message.setter
    def offset_message(self, value: int):
        self._offset = value
        self.repaint()

    def drawContents(self, painter: QtGui.QPainter):
        painter.save()
        painter.translate(self._offset, 0)
        super().drawContents(painter)
        painter.restore()


class App(QtCore.QObject):
    """
    The main application class. The constructor starts the GUI and all other classes used by the program.
    """

    # #################################### Get Cmd Line Options #####################################################
    cmd_line_shellfile = ''
    cmd_line_shellvar = ''
    cmd_line_command = ''
    cmd_line_headless = False

    # the listener for the second launches that flatcam.py started before the App
    _launch_server = None

    cmd_line_help = (
        "FlatCam.py --shellfile=<cmd_line_shellfile>\n"
        "FlatCam.py --shellvar=<1,'C:\\path',23>\n"
        "FlatCam.py --command=\"<Tcl commands, separated by ;>\"\n"
        "FlatCam.py --headless [--shellfile=...] [--shellvar=...] [--command=...]\n"
        "    no window and no OpenGL; without --shellfile and --command the Tcl commands are read from stdin.\n"
        "    Exit code: 0 done, 1 a Tcl command failed, 2 bad command line, N for 'exit N'."
    )
    try:
        # Multiprocessing pool will spawn additional processes with 'multiprocessing-fork' flag
        cmd_line_options, args = getopt.getopt(
            sys.argv[1:],
            "h:",
            [
                "shellfile=",
                "shellvar=",
                "command=",
                "headless",
                "multiprocessing-fork="
            ]
        )
    except getopt.GetoptError:
        print(cmd_line_help)
        sys.exit(2)

    for opt, arg in cmd_line_options:
        if opt == '-h':
            print(cmd_line_help)
            sys.exit()
        elif opt == '--shellfile':
            cmd_line_shellfile = arg
        elif opt == '--shellvar':
            cmd_line_shellvar = arg
        elif opt == '--command':
            cmd_line_command = arg
        elif opt == '--headless':
            cmd_line_headless = True

    # The application does os.chdir() into its own folder when it starts, so a relative file name would no longer be
    # found. Make the --shellfile absolute here and refuse to start on a file that cannot be read: a failure later in
    # the startup (after the GUI and the process pool exist) does not end the application cleanly.
    # The pool workers (spawn) import this module too, with the command line of the main process but with the working
    # directory of the main process after its chdir(): they must not check the file.
    if cmd_line_shellfile:
        cmd_line_shellfile = resolve_user_path(cmd_line_shellfile)
        if current_process().name == 'MainProcess' and not (os.path.isfile(cmd_line_shellfile) and
                                                            os.access(cmd_line_shellfile, os.R_OK)):
            print("ERROR: The --shellfile '%s' is not a readable file." % cmd_line_shellfile, file=sys.stderr)
            sys.exit(2)

    # The same for the files to open, given as arguments. The words that are no file (quit, save ...) stay as they are.
    args = resolve_existing_paths(args)

    # ################################### Version and VERSION DATE ##################################################
    # version = "Unstable"
    version = 1.0
    version_date = "2026/10/25"
    beta = False

    # current date now
    date = str(dt.today()).rpartition('.')[0]
    date = ''.join(c for c in date if c not in ':-')
    date = date.replace(' ', '_')

    # ############################################ URLS's ###########################################################
    # Retained for compatibility with older integrations; the GitHub Releases updater is the
    # default and the Digi share is the legacy provider.
    version_url = "http://flatcam.org/version"

    # App URL
    app_url = "http://flatcam.org"

    # Manual URL
    manual_url = "http://flatcam.org/manual/index.html"
    video_url = "https://www.youtube.com/playlist?list=PLVvP2SYRpx-AQgNlfoxw93tXUXon7G94_"
    gerber_spec_url = ("https://www.ucamco.com/files/downloads/file/81/"
                       "The_Gerber_File_Format_specification.pdf?7ac957791daba2cdf4c2c913f67a43da")
    excellon_spec_url = "https://www.ucamco.com/files/downloads/file/305/the_xnc_file_format_specification.pdf"
    bug_report_url = "https://github.com/MariusAdrian88/FlatCAM_Evo/issues"
    donate_url = ("https://www.paypal.com/cgi-bin/webscr?cmd="
                  "_donations&business=WLTJJ3Q77D98L&currency_code=USD&source=url")
    # this variable will hold the project status
    # if True it will mean that the project was modified and not saved
    should_we_save = False

    # flag is True if saving action has been triggered
    save_in_progress = False
    # flag is True if the app was closed while the project was saved; it closes when the save ends
    close_after_save = False
    _release_preparation_in_progress = False

    # #######################################    APP Signals   ######################################################

    # Inform the user
    # Handled by: App.info() --> Print on the status bar
    inform = QtCore.pyqtSignal([str], [str, bool])
    # the Undo / Redo labels of the active project changed: (undo label, redo label), '' for nothing
    history_changed = QtCore.pyqtSignal(str, str)
    # Handled by: App.info_shell() --> Print on the shell
    inform_shell = QtCore.pyqtSignal([str], [str, bool])
    inform_no_echo = QtCore.pyqtSignal(str)

    app_quit = QtCore.pyqtSignal()

    # General purpose background task
    worker_task = QtCore.pyqtSignal(dict)

    # File opened
    # Handled by:
    #  * register_folder()
    #  * register_recent()
    # Note: Setting the parameters to Unicode does not seem
    #       to have an effect. Then are received as Qstring
    #       anyway.

    # File type and filename
    file_opened = QtCore.pyqtSignal(str, str)
    # File type and filename
    file_saved = QtCore.pyqtSignal(str, str)
    # close app signal
    close_app_signal = pyqtSignal()
    # will perform the cleanup operation after a Graceful Exit
    # useful for the NCC Tool and Paint Tool where some progressive plotting might leave
    # graphic residues behind
    cleanup = pyqtSignal()
    # emitted when the new_project is created in a threaded way
    new_project_signal = pyqtSignal()
    # Percentage of progress
    progress = QtCore.pyqtSignal(int)
    # Emitted when a new object has been added or deleted from/to the collection
    object_status_changed = QtCore.pyqtSignal(object, str, str)

    message = QtCore.pyqtSignal(str, str, str)

    update_available = QtCore.pyqtSignal(object)
    update_check_result = QtCore.pyqtSignal(object)
    update_staged = QtCore.pyqtSignal(object)
    update_progress = QtCore.pyqtSignal(int, str)
    rollback_ready = QtCore.pyqtSignal(object)
    release_prepared = QtCore.pyqtSignal(object)

    # Emitted when a shell command is finished(one command only)
    shell_command_finished = QtCore.pyqtSignal(object)
    # Emitted when an unhandled exception happens
    # in the worker task.
    thread_exception = QtCore.pyqtSignal(object)
    # used to signal that there are arguments for the app
    args_at_startup = QtCore.pyqtSignal(list)
    # a reusable signal to replot a list of objects
    # should be disconnected after use, so it can be reused
    replot_signal = pyqtSignal(list)
    # signal emitted when jumping
    jump_signal = pyqtSignal(tuple)
    # signal emitted when jumping
    locate_signal = pyqtSignal(tuple, str)

    proj_selection_changed = pyqtSignal(object, object)
    # used by the AppScript object to process a script
    run_script = pyqtSignal(str)
    # used when loading a project and parsing the project file
    restore_project = pyqtSignal(object, str, bool, bool, bool, bool)
    # used when loading a project and restoring objects
    restore_project_objects_sig = pyqtSignal(object, str, bool, bool)
    # post-Edit actions
    post_edit_sig = pyqtSignal()

    # any callback can be bound
    custom_signal = pyqtSignal(object)

    # noinspection PyUnresolvedReferences
    def __init__(self, qapp, user_defaults=True, headless=None, launch_server=None):
        """
        Starts the application.

        :param headless:    no window and no OpenGL; default the --headless option
        :param launch_server:   the InstanceServer that flatcam.py started before the App; default one is made here
        :return:    the application
        :rtype:     QtCore.QObject
        """

        super().__init__()

        # Store qapp reference FIRST before any setup methods
        self.qapp = qapp
        self._launch_server = launch_server

        # headless mode (--headless): no window, no OpenGL canvas, the HeadlessRunner of flatcam.py runs the job
        self.headless = App.cmd_line_headless if headless is None else headless
        # True while the HeadlessRunner runs: the shell output then goes to stdout / stderr
        self.headless_console = False
        # set by quit_app / exit in headless mode
        self.headless_exit_code = None

        # =========================================================================
        # DECLARE ALL INSTANCE ATTRIBUTES HERE
        # =========================================================================

        # Logging
        self.log = None

        # Editors (instantiated later)
        self.exc_editor = None
        self.grb_editor = None
        self.geo_editor = None
        self.gcode_editor = None

        # Thread/process control
        self.abort_flag = False  # when True, the app has to return from any thread
        self.pool = None

        # Data/Lists
        self.recent = []
        self.recent_projects = []
        self.all_objects_list = []
        self.sel_objects_list = []
        self.objects_under_the_click_list = []
        self.app_plugins = []

        # UI State
        self.clipboard = None
        self.project_filename = None
        self.toggle_units_ignore = False
        self.main_thread = None

        # Units and coordinates
        self.units = 'MM'
        self.rel_point1 = (0, 0)
        self.rel_point2 = (0, 0)
        self.pos_jump = (0, 0)
        self._mouse_click_pos = [0, 0]
        self._mouse_pos = [0, 0]
        self.mouse_down = None
        self.dx = 0
        self.dy = 0

        # Mouse/click state
        self.doubleclick = False
        self.event_is_dragging = False
        self.command_active = None
        self.selection_type = None
        self.key_modifiers = None

        # Editor/tabs
        self.toggle_codeeditor = False
        self.click_noproject = False
        self.cursor = None
        self.inhibit_context_menu = False
        self.gcode_edited = ""
        self.old_state_of_tools_toolbar = False
        self.text_editor_tab = None
        self.old_tab_text_color = None
        self.reference_code_editor = None
        self.script_code = ''
        self.source_editor_tab = None
        self.tools_db_changed_flag = False
        self.plugin_tab_locked = False

        # Filters
        self.last_op_gerber_filter = None
        self.last_op_excellon_filter = None
        self.last_op_gcode_filter = None

        # Flags
        self.poly_not_cleared = False
        self.isHovering = False
        self.notHovering = True

        # Window geometry
        self.x_pos = None
        self.y_pos = None
        self.width = None
        self.height = None

        # Misc
        self.pagesize = {}
        self.save_timer = None
        self.call_source = 'app'

        # Paths/config (platform-specific)
        self.new_launch = None
        self.file_open_filter = None
        # the requests to open files wait here; one runs at a time
        self._open_requests = deque()
        self._open_busy = False
        self._open_retry_pending = False
        self._version_check_due = False
        self.data_path = None
        self.os = None
        self.app_home = None
        self.preprocessorpaths = None

        # Splash screen
        self.splash = None
        self._show_splash = False

        # Language/preprocessors
        self.languages = None
        self.preprocessors = None

        # Colors
        self.FC_light_green = None
        self.FC_dark_green = None
        self.FC_light_blue = None
        self.FC_dark_blue = None
        self.cursor_color_3D = None

        # UI/Shell
        self.ui = None
        self.shell = None
        self.regFK = None

        # Preferences/collection
        self.defaults = None
        self.options: dict | AppOptions = {}
        self.app_units = None
        self.default_units = None
        self.decimals = None
        self.resource_location = None
        self._current_theme = None
        self.preferencesUiManager = None

        # Object management
        self.collection = None
        # the Undo / Redo history of the active project (set by ProjectManager)
        self.history = None
        # (object, its state) when an editor changes the object in place; see _history_edit_begin()
        self._history_edit = None
        self.app_obj = None
        self.area_3d_tab = None

        # Canvas/plotting
        self.app_cursor = None
        self.hover_shapes = None
        self.tool_shapes = None
        self.sel_shapes = None
        self.used_time = 0.0
        self.plotcanvas = None

        # Workers
        self.workers = None
        self.proc_container = None

        # Tools (all initialized to None)
        self.dblsidedtool = None
        self.distance_tool = None
        self.distance_min_tool = None
        self.panelize_tool = None
        self.film_tool = None
        self.paste_tool = None
        self.calculator_tool = None
        self.rules_tool = None
        self.sub_tool = None
        self.move_tool = None
        self.cutout_tool = None
        self.ncclear_tool = None
        self.paint_tool = None
        self.isolation_tool = None
        self.follow_tool = None
        self.drilling_tool = None
        self.milling_tool = None
        self.levelling_tool = None
        self.grbl_sender_tool = None
        self.optimal_tool = None
        self.transform_tool = None
        self.report_tool = None
        self.pdf_tool = None
        self.image_tool = None
        self.pcb_wizard_tool = None
        self.qrcode_tool = None
        self.copper_thieving_tool = None
        self.fiducial_tool = None
        self.extract_tool = None
        self.align_objects_tool = None
        self.punch_tool = None
        self.invert_tool = None
        self.markers_tool = None
        self.etch_tool = None

        # Bookmarks/tools DB
        self.book_dialog_tab = None
        self.tools_db_tab = None

        # Handlers
        self.f_handlers = None
        self.edit_class = None
        self.plot_manager = None

        # Exclusion areas
        self.exc_areas = None

        # System
        self.trayIcon = None
        self.parent_w = None

        # Update state
        self.update_checker = None
        self._check_in_progress = False
        self._check_queued = False
        self._manual_update_requested = False
        self._update_dialog = None
        self._update_progress_dialog = None
        self._update_in_progress = False
        self._update_cancel_event = None
        self._rollback_in_progress = False
        self._release_preparation_in_progress = False

        # =========================================================================
        # CALL SETUP METHODS
        # =========================================================================

        self._setup_logging()
        self._setup_state_variables()
        self._setup_paths_and_config()
        self._setup_defaults_and_preferences(user_defaults=user_defaults)
        self._setup_gui()
        self._setup_workers_crew()
        self._setup_canvas_and_plotting()
        self._setup_tools_and_editors()
        self._setup_system_integration()
        self._setup_signal_connections()
        self._setup_startup()

    def _setup_logging(self):
        """Phase 1: Setup logging, qapp reference, and editor stubs."""
        # #############################################################################################################
        # ######################################### LOGGING ###########################################################
        # #############################################################################################################
        self.log = logging.getLogger('base')
        self.log.setLevel(logging.DEBUG)
        # log.setLevel(logging.WARNING)
        formatter = logging.Formatter('[%(levelname)s][%(threadName)s] %(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        if self.headless:
            # the console is the output of a headless run: only warnings and errors of the log go there
            handler.setLevel(logging.WARNING)
        self.log.addHandler(handler)

    def _setup_state_variables(self):
        """Phase 1: Setup all state variables for global usage."""
        # ############################################ Data #########################################################

        self.clipboard = QtWidgets.QApplication.clipboard()
        self.main_thread = QtWidgets.QApplication.instance().thread()

        # ######################################## Variables for global usage #######################################
        # hold the App units
        self.units = 'MM'

        # coordinates for relative position display
        self.rel_point1 = (0, 0)
        self.rel_point2 = (0, 0)

        # variable to store coordinates
        self.pos_jump = (0, 0)

        # variable to store mouse coordinates
        self._mouse_click_pos = [0, 0]
        self._mouse_pos = [0, 0]
        self.mouse_down = None

        # variable to store the delta positions on canvas
        self.dx = 0
        self.dy = 0

        # decide if we have a double click or single click
        self.doubleclick = False

        # store here the is_dragging value
        self.event_is_dragging = False

        # variable to store if a command is active (then the var is not None) and which one it is
        self.command_active = None
        # variable to store the status of moving selection action
        # None value means that it's not a selection action
        # True value = a selection from left to right
        # False value = a selection from right to left
        self.selection_type = None

        # List to store the objects that are currently loaded in FlatCAM
        # This list is updated on each object creation or object delete
        self.all_objects_list = []

        self.objects_under_the_click_list = []

        # List to store the objects that are selected
        self.sel_objects_list = []

        # holds the key modifier if pressed (CTRL, SHIFT or ALT)
        self.key_modifiers = None

        # Variable to store the status of the code editor
        self.toggle_codeeditor = False

        # Variable to be used for situations when we don't want the LMB click on canvas to auto open the Project Tab
        self.click_noproject = False

        # store here the mouse cursor
        self.cursor = None

        # while True no canvas context menu will be shown
        self.inhibit_context_menu = False

        # Variable to store the GCODE that was edited
        self.gcode_edited = ""

        # Variable to store old state of the Tools Toolbar; used in the Editor2Object and in Object2Editor methods
        self.old_state_of_tools_toolbar = False

        self.text_editor_tab = None

        # here store the color of a Tab text before it is changed, so it can be restored in the future
        self.old_tab_text_color = None

        # reference for the self.ui.code_editor
        self.reference_code_editor = None
        self.script_code = ''

        # if Tools DB are changed/edited in the Edit -> Tools Database tab the value will be set to True
        self.tools_db_changed_flag = False

        # last used filters
        self.last_op_gerber_filter = None
        self.last_op_excellon_filter = None
        self.last_op_gcode_filter = None

        # global variable used by NCC Tool to signal that some polygons could not be cleared, if True
        # flag for polygons not cleared
        self.poly_not_cleared = False

        # VisPy visuals
        self.isHovering = False
        self.notHovering = True

        # Window geometry
        self.x_pos = None
        self.y_pos = None
        self.width = None
        self.height = None

        # this holds a widget that is installed in the Plot Area when View Source option is used
        self.source_editor_tab = None

        self.pagesize = {}

        # used in the delayed shutdown self.start_delayed_quit() method
        self.save_timer = None

        # to use for tools like Distance tool who depends on the event sources who are changed inside the appEditors
        # depending on from where those tools are called different actions can be done
        self.call_source = 'app'

        # this is a flag to signal to other tools that the ui tool tab is locked and not accessible
        self.plugin_tab_locked = False

    def _setup_paths_and_config(self):
        """Phase 1: Setup paths, config files, and platform-specific settings."""
        # ################# Listen for another instance launching with files ##########################################
        # a headless run never takes files from a second launch; the second launch hands its files over in flatcam.py
        # before this App exists. The server listens since flatcam.py started; it holds the files until the end of the
        # init, when the App can open them
        if not self.headless:
            self.new_launch = self._launch_server
            if self.new_launch is None:
                self.new_launch = InstanceServer()
                self.new_launch.start()
            self.new_launch.files_received.connect(self.on_files_from_other_instance)
            if sys.platform == 'darwin':
                # a double-clicked file arrives as a FileOpen event, also while the App is still starting
                self.file_open_filter = self.new_launch.file_filter
                if self.file_open_filter is None:
                    self.file_open_filter = FileOpenFilter()
                    self.qapp.installEventFilter(self.file_open_filter)
                self.file_open_filter.files_received.connect(self.on_files_from_other_instance)

        # ########################################## OS-specific #####################################################

        portable = False

        # Folder for user settings.
        if sys.platform == 'win32':
            # ####### CONFIG FILE WITH PARAMETERS REGARDING PORTABILITY #############################################
            config_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                'config',
                'configuration.txt'
            )
            try:
                with open(config_file, 'r'):
                    pass
            except FileNotFoundError:
                config_file = os.path.join(
                    os.path.dirname(os.path.realpath(__file__)),
                    'config',
                    'configuration.txt'
                )

            try:
                with open(config_file, 'r') as f:
                    for line in f:
                        key, _, value = line.partition('=')
                        key = key.strip()
                        value = value.strip()

                        if key == 'portable':
                            try:
                                portable = bool(literal_eval(value))
                            except (ValueError, SyntaxError):
                                portable = False
            except FileNotFoundError as e:
                self.log.error(str(e))
            except Exception as e:
                self.log.error(f'App.__init__() --> {e}')
                return

            if not portable:
                base_appdata = os.getenv('APPDATA') or os.path.join(
                    os.path.expanduser('~'),
                    'AppData',
                    'Roaming'
                )
                self.data_path = os.path.join(base_appdata, 'FlatCAM')
            else:
                self.data_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                    'config'
                )

            self.os = 'windows'
        else:  # Linux/Unix/MacOS
            self.data_path = os.path.join(
                os.path.expanduser('~'),
                '.FlatCAM'
            )
            self.os = 'unix'

        # a folder set in the FLATCAM_DATA_PATH environment variable is used instead (also instead of the portable one)
        env_data_path = data_path_from_env()
        if env_data_path:
            self.data_path = env_data_path
            # keep the absolute path: a restart and the relaunch after an update start from the application folder
            os.environ[DATA_PATH_ENV] = env_data_path
            self.log.debug('Data folder from FLATCAM_DATA_PATH: ' + self.data_path)

        # ################################# Setup folders and files ##################################################
        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path)
            self.log.debug('Created data folder: ' + self.data_path)

        self.preprocessorpaths = self.preprocessors_path()
        if not os.path.exists(self.preprocessorpaths):
            os.makedirs(self.preprocessorpaths)
            self.log.debug('Created preprocessors folder: ' + self.preprocessorpaths)

        # A stable library survives application upgrades; the legacy JSON stays untouched.
        db_path = self.tools_database_path()
        try:
            from appDatabase import initialize_tools_database
            migration_defaults = dict(AppDefaults().defaults)
            if not (os.path.isfile(db_path) and os.path.getsize(db_path)) and os.path.isfile(self.defaults_path()):
                with open(self.defaults_path(), encoding='utf-8') as stream:
                    saved_defaults = json.load(stream)
                if isinstance(saved_defaults, dict):
                    migration_defaults['units'] = saved_defaults.get('units', 'MM')
            # This version's library first, then the libraries of older versions, newest first.
            current_legacy = Path(self.data_path) / ('tools_db_%s.FlatDB' % self.version)
            older_legacy = sorted((path for path in Path(self.data_path).glob('tools_db_*.FlatDB')
                                   if path != current_legacy), key=lambda path: path.stat().st_mtime, reverse=True)
            initialize_tools_database(db_path, [current_legacy] + older_legacy, migration_defaults)
        except (OSError, TypeError, ValueError) as error:
            self.log.error('Could not initialize tools database: %s' % str(error))

        # create current_defaults.FlatConfig file if there is none
        def_path = self.defaults_path()
        try:
            with open(def_path):
                pass
        except IOError:
            self.log.debug('Creating empty current_defaults.FlatConfig')
            with open(def_path, 'w') as f:
                json.dump({}, f)

        # the factory defaults are written only once at the first launch of the application after installation
        AppDefaults.save_factory_defaults(self.factory_defaults_path(), self.version)

        # create a recent files json file if there is none
        rec_f_path = self.recent_files_path()
        try:
            with open(rec_f_path):
                pass
        except IOError:
            self.log.debug('Creating empty recent.json')
            with open(rec_f_path, 'w') as f:
                json.dump([], f)

        # create a recent projects json file if there is none
        rec_proj_path = self.recent_projects_path()
        try:
            with open(rec_proj_path):
                pass
        except IOError:
            self.log.debug('Creating empty recent_projects.json')
            with open(rec_proj_path, 'w') as fp:
                json.dump([], fp)

        # Application directory. CHDIR to it. Otherwise, trying to load GUI icons will fail as their path is relative.
        # This will fail under cx_freeze ...
        self.app_home = os.path.dirname(os.path.realpath(__file__))

        # cx_freeze workaround
        if os.path.isfile(self.app_home):
            self.app_home = os.path.dirname(self.app_home)

        os.chdir(self.app_home)

    def _setup_defaults_and_preferences(self, user_defaults=True):
        """Phase 1: Setup defaults, preferences storage, themes, and object classes."""
        # ################################# DEFAULTS - PREFERENCES STORAGE ###########################################
        self.defaults = AppDefaults(beta=self.beta, version=self.version)

        # current_defaults_path = os.path.join(self.data_path, "current_defaults.FlatConfig")
        current_defaults_path = self.defaults_path()
        
        # ponytail: legacy compatibility - discover versioned files if stable file doesn't exist
        if user_defaults and not os.path.isfile(current_defaults_path):
            legacy_path = AppDefaults.find_legacy_defaults_file(self.data_path, str(self.version))
            if legacy_path:
                self.log.debug(f'Found legacy defaults file: {legacy_path}')
                current_defaults_path = legacy_path
        
        if user_defaults:
            self.defaults.load(filename=current_defaults_path, inform=self.inform)

        # ######################################## UPDATE THE OPTIONS ###############################################
        # The options store only the project keys. They read and write the application keys in self.defaults.
        self.options = AppOptions(version=self.version, baseline=self.defaults, defaults=self.defaults)

        # Set global_theme based on appearance
        appearance = self.options.get("global_appearance", self.defaults.get("global_appearance"))
        if appearance == 'auto':
            if darkdetect.isDark():
                theme = 'dark'
            else:
                theme = 'light'
        else:
            if appearance == 'dark':
                theme = 'dark'
            else:
                theme = 'light'

        self.options["global_theme"] = theme
        self.defaults["global_theme"] = theme
        self.defaults.current_defaults["global_theme"] = theme

        # Cache theme for use in _setup_gui (avoids passing theme as parameter)
        self._current_theme = theme

        self._apply_color_scheme(theme)

        self.app_units = self.options.get("units", self.defaults.get("units"))
        self.default_units = self.defaults.get("units")
        self.decimals = int(self.options.get('units_precision', self.defaults.get('units_precision')))

        self.resource_location = 'assets/resources/dark_resources' if theme == 'dark' else 'assets/resources'
        if self.options.get("global_styling", self.defaults.get("global_styling")) == 'default':
            self.qapp.setStyleSheet('')
        elif theme == 'light':
            qlightsheet.STYLE_SHEET = light_style_sheet.L_STYLE_SHEET
            self.qapp.setStyleSheet(libs.qdarktheme.load_stylesheet('light'))
        else:
            qdarksheet.STYLE_SHEET = dark_style_sheet.D_STYLE_SHEET
            self.qapp.setStyleSheet(libs.qdarktheme.load_stylesheet())

        self.apply_dim_text(self.options.get("global_dim_text", self.defaults.get("global_dim_text")))

        # ################################### Set LOG verbosity ######################################################
        if self.options.get("global_log_verbose", self.defaults.get("global_log_verbose")) == 2:
            if self.log.handlers:
                self.log.handlers.pop()
            self.log = AppLogging(app=self, log_level=2)
        elif self.options.get("global_log_verbose", self.defaults.get("global_log_verbose")) == 0:
            if self.log.handlers:
                self.log.handlers.pop()
            self.log = AppLogging(app=self, log_level=0)

        # #################################### SETUP OBJECT CLASSES #################################################
        # Need lifecycle initialized before setup_objclasses
        self.lifecycle = AppLifecycle(app=self)
        self.update_checker = create_update_checker(self)
        self.setup_obj_classes()

        # ###################################### CREATE MULTIPROCESSING POOL #######################################
        # A child ends after 100 tasks and a new one starts: the memory of big geometries does not pile up in the
        # children, and a restart (about 0.4 s) is rare.
        self.pool = Pool(processes=self.options.get("global_process_number", self.defaults.get("global_process_number")),
                         maxtasksperchild=100)

        # ###################################### Clear GUI Settings - once at first start ###########################
        if self.options.get("first_run", self.defaults.get("first_run")) is True:
            # on first run clear the previous QSettings, therefore clearing the GUI settings
            q_settings = QSettings("Open Source", "FlatCAM_EVO")
            for key in q_settings.allKeys():
                q_settings.remove(key)
            # This will write the setting to the platform specific storage.
            del q_settings

    def _setup_gui(self):
        """Phase 1: Setup GUI, splash screen, languages, preprocessors, main UI, shell, and preferences."""
        # ###################################### Setting the Splash Screen ##########################################
        splash_settings = QSettings("Open Source", "FlatCAM_EVO")
        if splash_settings.contains("splash_screen"):
            show_splash = splash_settings.value("splash_screen", type=int)
        else:
            splash_settings.setValue('splash_screen', 1)

            # This will write the setting to the platform specific storage.
            del splash_settings
            show_splash = 1

        # no splash when the app starts with a file to open (a double-click on an associated file)
        if show_splash and not self.headless and not App.args:  # noqa
            screen = cursor_screen()
            # scale to physical pixels of the screen; a pixmap of logical size is stretched (blurred) on a HiDPI screen
            dpr = screen.devicePixelRatio() if screen else 1.0
            splash_img = QtGui.QImage(self.resource_location + '/splash.png')
            img_dpi = splash_img.dotsPerMeterX() * 0.0254 or 96.0
            logical_w = splash_img.width() * 96.0 / img_dpi
            logical_h = splash_img.height() * 96.0 / img_dpi
            splash_pix = QtGui.QPixmap.fromImage(splash_img.scaled(
                QtCore.QSize(round(logical_w * dpr), round(logical_h * dpr)),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation
            ))
            splash_pix.setDevicePixelRatio(dpr)

            # self.splash = QtWidgets.QSplashScreen(splash_pix, Qt.WindowType.WindowStaysOnTopHint)
            # the splash is made on the screen of the mouse cursor; a splash made on the primary screen and moved
            # later can stay on the primary screen or get the scale of the primary screen
            self.splash = FCSplashScreen(12, screen, splash_pix, Qt.WindowType.SplashScreen)

            # Store for use in other setup methods
            self._show_splash = show_splash
            # self.splash.setMask(splash_pix.mask())

            # center the splash in the available area of the screen of the mouse cursor
            if screen:
                current_screen_center = screen.availableGeometry().center()
                self.splash.move(current_screen_center - self.splash.rect().center())

            self.splash.show()
            self.splash.showMessage(_("The application is initializing ..."),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))
        else:
            self.splash = None

        # ########################################## LOAD LANGUAGES  ################################################
        self.languages = fcTranslate.load_languages()
        aval_languages = []
        for name in sorted(self.languages.values()):
            aval_languages.append(name)
        self.options["global_languages"] = aval_languages

        # ####################################### APPLY APP LANGUAGE ################################################
        ret_val = fcTranslate.apply_language('strings')

        if ret_val == "no language":
            self.inform.emit('[ERROR] %s' % _("Could not find the Language files. The App strings are missing."))
            self.log.debug("Could not find the Language files. The App strings are missing.")
        else:
            # make the current language the current selection on the language combobox
            self.options["global_language_current"] = ret_val
            self.log.debug("App.__init__() --> Applied %s language." % str(ret_val).capitalize())

        # #################################### LOAD PREPROCESSORS ###################################################
        # ----------------------------------------- WARNING --------------------------------------------------------
        # Preprocessors need to be loaded before the Preferences Manager builds the Preferences
        # That's because the number of preprocessors can vary and here the combobox is populated
        # -----------------------------------------------------------------------------------------------------------

        # a dictionary that have as keys the name of the preprocessor files and the value is the class from
        # the preprocessor file
        self.preprocessors = load_preprocessors(self)

        # make sure that always the 'default' preprocessor is the first item in the dictionary
        if 'default' in self.preprocessors.keys():
            # add the 'default' name first in the dict after removing from the preprocessor's dictionary
            default_pp = self.preprocessors.pop('default')
            new_ppp_dict = {
                'default': default_pp
            }

            # then add the rest of the keys
            for name, val_class in self.preprocessors.items():
                new_ppp_dict[name] = val_class

            # and now put back the ordered dict with 'default' key first
            self.preprocessors = new_ppp_dict

        # populate the Plugins Preprocessors
        drill_preprocessors = []
        mill_preprocessors = []
        solderpaste_preprocessors = []
        self.options["tools_drill_preprocessor_list"] = drill_preprocessors
        self.options["tools_mill_preprocessor_list"] = mill_preprocessors
        self.options["tools_solderpaste_preprocessor_list"] = solderpaste_preprocessors
        for name in list(self.preprocessors.keys()):
            lowered_name = name.lower()

            # 'Paste' preprocessors are to be used only in the Solder Paste Dispensing Plugin
            if 'paste' in lowered_name:
                solderpaste_preprocessors.append(name)
                continue

            mill_preprocessors.append(name)

            # HPGL preprocessor is only for Geometry objects therefore it should not be in the Excellon Preferences
            if 'hpgl' not in lowered_name:
                drill_preprocessors.append(name)

        # ######################################### Initialize GUI ##################################################
        # FlatCAM colors used in plotting
        self.FC_light_green = '#BBF268BF'
        self.FC_dark_green = '#006E20BF'
        self.FC_light_blue = '#a5a5ffbf'
        self.FC_dark_blue = '#0000ffbf'

        theme_settings = QtCore.QSettings("Open Source", "FlatCAM_EVO")
        theme_settings.setValue("appearance", self.options.get("global_appearance", self.defaults.get("global_appearance")))
        theme_settings.setValue("styling", self.options.get("global_styling", self.defaults.get("global_styling")))
        theme_settings.setValue("theme", self.options.get("global_theme", self.defaults.get("global_theme")))
        theme_settings.setValue("dark_canvas", self.options.get("global_dark_canvas", self.defaults.get("global_dark_canvas")))

        if self.options.get("global_cursor_color_enabled", self.defaults.get("global_cursor_color_enabled")):
            self.cursor_color_3D = self.options.get("global_cursor_color", self.defaults.get("global_cursor_color"))
        else:
            self.cursor_color_3D = (
                'black'
                if self._current_theme in ('light', 'default')
                   and not self.options.get("global_dark_canvas", self.defaults.get("global_dark_canvas"))
                else 'gray'
            )

        # update the 'options' dict with the setting in QSetting
        # ponytail: this is a no-op, keeping for compatibility
        self.options['global_theme'] = self.options.get("global_theme", self.defaults.get("global_theme"))

        # ########################
        self.ui = MainGUI(self)
        # ########################

        # decide if to show or hide the Notebook side of the screen at startup
        split_sizes = [1, 1] if self.options.get("global_project_at_startup", self.defaults.get("global_project_at_startup")) else [0, 1]
        self.ui.splitter.setSizes(split_sizes)

        # ########################################### Initialize Tcl Shell ##########################################
        # ###########################    always initialize it after the UI is initialized   #########################
        self.shell = FCShell(app=self, version=self.version)
        self.log.debug("StartDate: %s" % self.date)
        self.log.debug("TCL Shell has been initialized.")

        # ####################################### Auto-complete KEYWORDS ############################################
        # ######################## Setup after the Defaults class was instantiated ##################################
        self.regFK = RegisterFK(
            ui=self.ui,
            inform_sig=self.inform,
            options_dict=self.options,
            shell=self.shell,
            log=self.log,
            keywords=KeyWords(),
            extensions=Extensions()
        )

        # ##################################### UPDATE PREFERENCES GUI FORMS ########################################
        self.preferencesUiManager = PreferencesUIManager(
            data_path=self.data_path,
            ui=self.ui,
            inform=self.inform,
            options=self.options,
            defaults=self.defaults
        )

        self.preferencesUiManager.defaults_write_form()

        # When the self.options dictionary changes will update the Preferences GUI forms
        self.options.set_change_callback(self.on_defaults_dict_change)

        # ###################################### CREATE UNIQUE SERIAL NUMBER ########################################
        chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
        serial = self.options.get('global_serial', self.defaults.get('global_serial'))
        if serial == 0 or len(str(serial)) < 10:
            self.options['global_serial'] = ''.join([random.choice(chars) for __ in range(20)])
            self.preferencesUiManager.save_defaults(silent=True, first_time=True)

        self.defaults.propagate_defaults()

        # #################################### SETUP OBJECT COLLECTION ##############################################
        self.collection = ObjectCollection(app=self)
        # ### Adjust tabs width ## ##
        self.collection.view.setMinimumWidth(290)

        # the Project tab can hold more than 1 project; the manager keeps app.collection on the active one
        self.projects = ProjectManager(app=self)
        self.project_selector = ProjectSelector(self.projects)
        self.ui.project_tab_layout.addWidget(self.project_selector)
        self.ui.project_tab_layout.addWidget(self.projects.stack)

        self.app_obj = AppObject(app=self)

        self.log.debug("Finished creating Object Collection.")

        # ######################################## SETUP 3D Area ####################################################
        self.area_3d_tab = QtWidgets.QWidget()
        self.plotcanvas3d = None

        # ######################################## SETUP Plot Area ##################################################
        self.app_cursor = None
        self.hover_shapes = None

        if self._show_splash:
            self.splash.showMessage(_("The application is initializing ...\n"
                                      "Canvas initialization started."),
                                    alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                                    color=QtGui.QColor("lightgray"))
        start_plot_time = time.time()  # debug

        # set up the PlotCanvas
        self.plotcanvas = self.on_plotcanvas_setup()
        if self.plotcanvas == 'fail':
            if self.splash:
                self.splash.finish(self.ui)
            self.log.debug("Failed to start the Canvas.")

            self.pool.terminate()
            self.pool.join()
            self.log.error("Failed to start the Canvas")
            raise SystemError("Failed to start the Canvas")

        # add he PlotCanvas setup to the UI
        if not self.headless:
            self.on_plotcanvas_add(self.plotcanvas, self.ui.right_layout)

        # ################   SHAPES STORAGE   #########################################################################
        # Storage for shapes, storage that can be used by FlatCAm tools for utility geometry
        # VisPy visuals
        try:
            self.tool_shapes = self.plotcanvas.new_shape_collection(layers=1)
        except AttributeError:
            self.tool_shapes = None

        # Storage for Hover Shapes
        self.hover_shapes = self.plotcanvas.new_shape_collection(layers=1)

        # Storage for Selection shapes
        self.sel_shapes = self.plotcanvas.new_shape_collection(layers=1)
        # #############################################################################################################

        end_plot_time = time.time()
        self.used_time = end_plot_time - start_plot_time
        self.log.debug("Finished Canvas initialization in %s seconds." % str(self.used_time))

        if self._show_splash:
            canvas_msg_1 = _("The application is initializing")
            canvas_msg_2 = _("Canvas initialization started")
            canvas_msg_3 = _("Canvas initialization finished in")
            self.splash.showMessage(
                f'{canvas_msg_1}\n'
                f'{canvas_msg_2}\n'
                f'{canvas_msg_3}: {self.used_time:.2f}sec',
                alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                color=QtGui.QColor("lightgray")
            )

        self.ui.splitter.setStretchFactor(1, 2)

        self.proc_container = FCVisibleProcessContainer(self.ui.activity_view, app=self)

        # Note: setup_default_properties_tab() moved to _setup_canvas_and_plotting()
        # because it requires ui_actions which is initialized there

    def _setup_workers_crew(self):
        """Phase 1: Setup workers crew for background tasks."""
        # ############################################### Worker SETUP ##############################################
        w_number = int(self.options.get("global_worker_number", self.defaults.get("global_worker_number")))
        self.workers = WorkerStack(workers_number=w_number)

        self.worker_task.connect(self.workers.add_task)
        # the workers report an exception on the signal of the stack
        self.workers.thread_exception.connect(self.thread_exception)
        self.thread_exception.connect(self.on_thread_exception)
        self.log.debug("Finished creating Workers crew.")

    def on_thread_exception(self, error_obj):
        """
        Reports an unhandled exception from a worker task. The worker catches the exception,
        so without this the user gets no message in the GUI.

        :param error_obj:       the exception raised in the worker task
        :type error_obj:        Exception
        :return:                None
        """
        self.log.error(
            "Unhandled exception in a worker task:\n%s" % ''.join(
                traceback.format_exception(type(error_obj), error_obj, error_obj.__traceback__))
        )
        self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The task failed"), str(error_obj)))

    def _setup_canvas_and_plotting(self):
        """Phase 1: Setup tool/editor stubs, bookmarks, tools database, shell, editors, and exclusion areas."""
        # #########################################################################
        # Initialize ui_actions EARLY - needed by install_bookmarks and other methods
        # #########################################################################
        self.ui_actions = AppUIActions(app=self)

        # always install tools only after the shell is initialized because the self.inform.emit() depends on shell
        try:
            self.install_tools()
        except AttributeError as e:
            self.log.debug("App.__init__() install_tools() --> %s" % str(e))

        # ######################################### BookMarks Manager ###############################################
        # install Bookmark Manager and populate bookmarks in the Help -> Bookmarks
        self.install_bookmarks()
        self.book_dialog_tab = BookmarkManager(app=self, storage=self.options.get("global_bookmarks", self.defaults.get("global_bookmarks")))

        # ########################################### Tools Database ################################################
        self.tools_db_tab = None

        # ### System Font Parsing ###
        # self.f_parse = ParseFont(self)
        # self.parse_system_fonts()

        # ############################################## Shell SETUP ################################################
        # show TCL shell at start-up based on the Menu -? Edit -> Preferences setting.
        self.ui.shell_dock.setVisible(bool(self.options.get("global_shell_at_startup", self.defaults.get("global_shell_at_startup"))))

        # ################################## ADDING FlatCAM EDITORS section #########################################
        # watch out for the position of the editor instantiation ... if it is done before a save of the default values
        # at the first launch of the App , the editors will not be functional.
        try:
            self.geo_editor = AppGeoEditor(self)
        except Exception as es:
            self.log.error("appMain.__init__() --> Geo Editor Error: %s" % str(es))

        try:
            self.exc_editor = AppExcEditor(self)
        except Exception as es:
            self.log.error("appMain.__init__() --> Excellon Editor Error: %s" % str(es))

        try:
            self.grb_editor = AppGerberEditor(self)
        except Exception as es:
            self.log.error("appMain.__init__() --> Gerber Editor Error: %s" % str(es))

        try:
            self.gcode_editor = AppGCodeEditor(self)
        except Exception as es:
            self.log.error("appMain.__init__() --> GCode Editor Error: %s" % str(es))

        self.log.debug("Finished adding FlatCAM Editor's.")

        self.ui.set_ui_title(name=_("New Project - Not saved"))

        # ########################################### EXCLUSION AREAS ###############################################
        self.exc_areas = ExclusionAreas(app=self)

        # ###################################### INSTANTIATE CLASSES THAT HOLD THE MENU HANDLERS ####################
        # Note: self.lifecycle already initialized in _setup_defaults_and_preferences for setup_obj_classes
        self.f_handlers = appIO(app=self)
        self.edit_class = appEditor(app=self)
        self.plot_manager = AppPlotManager(app=self)
        self.canvas_events = AppCanvasEvents(app=self)
        self.object_ops = AppObjectOps(app=self)
        self.signal_connector = AppSignalConnector(app=self)
        # self.ui_actions = AppUIActions(app=self)  # Already initialized in _setup_canvas_and_plotting
        # self.lifecycle = AppLifecycle(app=self)  # Already initialized earlier

        # this is calculated in the class above (somehow?)
        self.options["root_folder_path"] = self.app_home

        # Sets up FlatCAMObj, FCProcess and FCProcessContainer.
        self.ui_actions.setup_default_properties_tab()

    def _setup_tools_and_editors(self):
        """Phase 1: Restore the layout (must run after _setup_canvas_and_plotting for editors)."""
        # ##################################### FIRST RUN SECTION ###################################################
        # ################################ It's done only once after install   #####################################
        if self.options.get("first_run", self.defaults.get("first_run")) is True:
            # ONLY AT FIRST STARTUP INIT THE GUI LAYOUT TO 'minimal'
            self.log.debug("-> First Run: Setting up the first Layout")
            initial_lay = 'minimal'
            self.on_layout(lay=initial_lay, connect_signals=False)

            # Set the combobox in Preferences to the current layout
            idx = self.ui.general_pref_form.general_gui_group.layout_combo.findText(initial_lay)
            self.ui.general_pref_form.general_gui_group.layout_combo.setCurrentIndex(idx)

            # after the first run, this object should be False
            self.options["first_run"] = False
            self.log.debug("-> First Run: Updating the Defaults file with Factory Defaults")
            self.preferencesUiManager.save_defaults(silent=True)
        else:
            q_settings = QSettings("Open Source", "FlatCAM_EVO")
            layout = q_settings.value('layout', 'standard', type=str)
            self.options['global_layout'] = layout
            if layout in ('compact', 'minimal'):
                # Keep a valid custom arrangement; use the preset only as a fallback.
                if not (q_settings.contains('saved_gui_state') and self.ui.restoreState(
                        q_settings.value('saved_gui_state'), 0)):
                    self.on_layout(lay=layout, connect_signals=False)
                self.ui.grbl_console_dock.hide()
                self.ui.exc_edit_toolbar.setDisabled(True)
                self.ui.geo_edit_toolbar.setDisabled(True)
                self.ui.grb_edit_toolbar.setDisabled(True)

    def _setup_system_integration(self):
        """Phase 1: Setup system tray and recent items."""
        # ############################################### SYS TRAY ##################################################
        self.parent_w = QtWidgets.QWidget()
        self.trayIcon = None
        if self.options.get("global_systray_icon", self.defaults.get("global_systray_icon")) and not self.headless:
            self.trayIcon = AppSystemTray(app=self,
                                          icon=QtGui.QIcon(self.resource_location + '/app32.png'),
                                          parent=self.parent_w)

        # ############################################ SETUP RECENT ITEMS ###########################################
        self.setup_recent_items()

    def _setup_signal_connections(self):
        """Phase 1: Setup all signal connections."""
        # ############################################# Signal handling #############################################
        # ########################################## Custom signals  ################################################
        # signal for displaying messages in status bar
        self.inform[str].connect(self.info)
        self.inform[str, bool].connect(self.info)
        self.inform_no_echo[str].connect(lambda txt: self.info(msg=txt, shell_echo=False))  # noqa

        # signals for displaying messages in the Tcl Shell are now connected in the ToolShell class

        # loading a project
        self.restore_project.connect(self.f_handlers.restore_project_handler)  # noqa
        self.restore_project_objects_sig.connect(self.f_handlers.restore_project_objects)  # noqa
        # signal to be called when the app is quiting
        self.app_quit.connect(self.quit_application, type=Qt.ConnectionType.QueuedConnection)
        self.message.connect(
            lambda title, msg, kind: message_dialog(title=title, message=msg, kind=kind, parent=self.ui))
        self.update_available.connect(self.on_update_available)
        self.update_check_result.connect(self.on_update_check_result)
        self.update_staged.connect(self.on_update_staged)
        self.update_progress.connect(self._on_update_progress)
        self.rollback_ready.connect(self.on_rollback_ready)
        self.release_prepared.connect(self.on_release_prepared)
        # self.progress.connect(self.set_progress_bar)

        # signals emitted when file state change
        self.file_opened.connect(self.register_recent)
        self.file_opened.connect(self.add_to_os_recent)
        self.file_opened.connect(lambda kind, filename: self.register_folder(filename))
        self.file_saved.connect(lambda kind, filename: self.register_save_folder(filename))

        # when the options dictionary values change
        self.options.set_change_callback(callback=self.on_options_value_changed)

        # post_edit signal
        self.post_edit_sig.connect(self.on_editing_final_action, type=Qt.ConnectionType.QueuedConnection)

        # ########################################## Standard signals ###############################################
        # File Signals
        self.signal_connector.connect_filemenu_signals()

        # Edit Signals
        self.signal_connector.connect_editmenu_signals()

        # Options Signals
        self.signal_connector.connect_optionsmenu_signals()

        # View Signals
        self.signal_connector.connect_menuview_signals()

        # Tool Signals
        self.ui.menu_plugins_shell.triggered.connect(self.ui.toggle_shell_ui)
        # the rest are auto-inserted

        # Help Signals
        self.signal_connector.connect_menuhelp_signals()

        # Project Context Menu Signals
        self.signal_connector.connect_project_context_signals()

        # ToolBar signals
        self.signal_connector.connect_toolbar_signals()

        # Canvas Context Menu
        self.signal_connector.connect_canvas_context_signals()

        # Notebook tab clicking
        # self.ui.notebook.tabBarClicked.connect(self.on_properties_tab_click)
        self.ui.notebook.currentChanged.connect(self.on_notebook_tab_changed)

        # self.ui.notebook.callback_on_close = self.on_close_notebook_tab

        # Plot Area and Notebook double-clicking
        self.ui.plot_tab_area.tabBarDoubleClicked.connect(self.on_plot_area_tab_double_clicked)
        self.ui.notebook.tabBarDoubleClicked.connect(self.on_plot_area_tab_double_clicked)

        # #################################### GUI PREFERENCES SIGNALS ##############################################
        # ##################################### Workspace Setting Signals ###########################################
        self.ui.general_pref_form.general_app_set_group.wk_cb.currentIndexChanged.connect(
            self.on_workspace_modified)
        self.ui.general_pref_form.general_app_set_group.wk_orientation_radio.activated_custom.connect(
            self.on_workspace_modified
        )

        self.ui.general_pref_form.general_app_set_group.workspace_cb.stateChanged.connect(self.on_workspace)

        # ######################################## GUI SETTINGS SIGNALS #############################################
        self.ui.general_pref_form.general_app_set_group.cursor_radio.activated_custom.connect(self.on_cursor_type)

        # ######################################## Tools related signals ############################################

        # portability changed signal
        self.ui.general_pref_form.general_app_group.portability_cb.stateChanged.connect(self.on_portable_checked)

        # Object selection
        self.ui.menuobjects_selall.triggered.connect(lambda: self.collection.on_objects_selection(True))
        self.ui.menuobjects_unselall.triggered.connect(lambda: self.collection.on_objects_selection(False))

        # when there are arguments at application startup this get launched
        self.args_at_startup[list].connect(self.on_startup_args)

        # ########################################### GUI SIGNALS ###################################################
        self.ui.hud_label.clicked.connect(self.plotcanvas.on_toggle_hud)
        self.ui.axis_status_label.clicked.connect(self.plotcanvas.on_toggle_axis)
        self.ui.pref_status_label.clicked.connect(self.on_toggle_preferences)

        # ####################################### VARIOUS SIGNALS ###################################################
        # connect the abort_all_tasks related slots to the related signals
        self.proc_container.idle_flag.connect(self.app_is_idle)

        # signal emitted when a tab is closed in the Plot Area
        self.ui.plot_tab_area.tab_closed_signal.connect(self.on_plot_area_tab_closed)

        # signal emitted when a tab is closed in the Plot Area
        self.ui.notebook.tab_closed_signal.connect(self.on_notebook_closed)

        # signal to close the application
        self.close_app_signal.connect(self.kill_app)  # noqa

        # signal to process the body of a script
        self.run_script.connect(self.script_processing)  # noqa
        # ################################# FINISHED CONNECTING SIGNALS #############################################

        self.log.debug("Finished connecting Signals.")

    def shellvar_commands(self):
        """
        :return: the Tcl commands that set shellvar_0, shellvar_1 ... from the --shellvar option
        """
        commands = []
        if not self.cmd_line_shellvar:
            return commands
        for cnt, i in enumerate(self.cmd_line_shellvar.split(',')):
            # noinspection PyBroadException
            try:
                command_tcl = safe_eval(i)
            except Exception:
                command_tcl = i
            command_tcl_formatted = 'set shellvar_{nr} "{cmd}"'.format(cmd=str(command_tcl), nr=str(cnt))
            # if there are Windows paths then replace the path separator with a Unix like one
            if sys.platform == 'win32':
                command_tcl_formatted = command_tcl_formatted.replace('\\', '/')
            commands.append(command_tcl_formatted)
        return commands

    def _setup_startup(self):
        """Phase 1: Show GUI and process startup arguments."""
        # ##################################### Finished the CONSTRUCTOR ############################################
        self.log.debug("END of constructor. Releasing control.")
        self.log.debug("... Resistance is futile. You will be assimilated ...")
        self.log.debug("... I disagree. While we live and breath, we can be free!\n")

        if self.headless:
            # no window, no tray, no update check; the HeadlessRunner of flatcam.py runs --shellvar, --shellfile,
            # --command or the Tcl prompt
            return

        # ########################################## SHOW GUI #######################################################
        # before the splash finish: QSplashScreen.finish() makes the native window of the main window
        self.ui.place_on_cursor_screen()
        if self.splash:
            # finish the splash
            self.splash.finish(self.ui)

        mgui_settings = QSettings("Open Source", "FlatCAM_EVO")
        if mgui_settings.contains("maximized_gui"):
            maximized_ui = mgui_settings.value('maximized_gui', type=bool)
            if maximized_ui is True:
                self.ui.showMaximized()
            else:
                self.ui.show()
        else:
            self.ui.show()

        if self.options.get("global_systray_icon", self.defaults.get("global_systray_icon")) and self.trayIcon is not None:
            self.trayIcon.show()

        self.refresh_rollback_action()
        # the check runs after the files given at the start finished loading: it must not compete with them for workers
        self._version_check_due = bool(
            self.options.get("global_version_check", self.defaults.get("global_version_check")))

        # ######################################## START-UP ARGUMENTS ###############################################
        # test if the program was started with a script as parameter
        if self.cmd_line_shellvar:
            try:
                for command_tcl_formatted in self.shellvar_commands():
                    self.shell.exec_command(command_tcl_formatted, no_echo=True)
            except Exception as ext:
                print("ERROR: ", ext)
                sys.exit(2)

        if self.cmd_line_shellfile:
            self.f_handlers.on_file_run_script(name=self.cmd_line_shellfile, silent=True)

        if self.cmd_line_command:
            if self.ui.shell_dock.isHidden():
                self.ui.shell_dock.show()
            self.shell.exec_command(self.cmd_line_command)

        # accept some type file as command line parameter: FlatCAM project, FlatCAM preferences or scripts
        # the path/file_name must be enclosed in quotes, if it contains spaces
        if App.args:  # noqa
            self.open_files_request(App.args)  # noqa
        else:
            self._drain_open_requests()

        # the files that other launches sent while this one was starting open after the ones of the command line
        if self.new_launch is not None:
            self.new_launch.set_ready()
        if self.file_open_filter is not None:
            self.file_open_filter.set_ready(skip=App.args)  # noqa

        if self.defaults.old_defaults_found is True:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Found old default preferences files. "
                                                      "Please reboot the application to update."))
            self.defaults.old_defaults_found = False

    # ######################################### INIT FINISHED  #######################################################

    def _apply_color_scheme(self, theme):
        """
        The 'default' and 'light' themes use colors made for a light window (black text, black ruler ticks, dark blue
        headings ...) and the 'dark' theme colors made for a dark one. Without this the Qt palette and, on macOS, the
        native widgets follow the operating system instead of the theme: with the OS in dark mode the Default theme
        showed dark blue headings and dark project items on a dark window and the Light theme had black ruler ticks on
        a dark gray plot border.

        :param theme: 'default', 'light' or 'dark' (what 'auto' resolved to)
        :return: None
        """

        style_hints = self.qapp.styleHints()
        if not hasattr(style_hints, 'setColorScheme'):
            # Qt before 6.8 cannot be told
            return
        style_hints.setColorScheme(QtCore.Qt.ColorScheme.Dark if theme == 'dark' else QtCore.Qt.ColorScheme.Light)

    def apply_dim_text(self, enabled):
        """
        Show the GUI text that has no color of its own in a darker gray instead of the bright white of the Dark theme.
        Only the base text color changes: the palette text roles and the color of the base 'QWidget' rule of the
        custom stylesheet. Widgets with their own palette or stylesheet color and the Light theme are not changed.

        :param enabled: True to dim the text, False to restore the theme colors
        :return: None
        """

        if not hasattr(self, '_base_palette'):
            self._base_palette = QtGui.QPalette(self.qapp.palette())
            self._base_stylesheet = self.qapp.styleSheet()

        palette = QtGui.QPalette(self._base_palette)
        sheet = self._base_stylesheet
        if enabled and self._current_theme == 'dark':
            color = QtGui.QColor('#8c8c8c')
            for group in (QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorGroup.Inactive):
                for role in (QtGui.QPalette.ColorRole.WindowText, QtGui.QPalette.ColorRole.Text,
                             QtGui.QPalette.ColorRole.ButtonText):
                    palette.setColor(group, role, color)
            sheet = re.sub(r'(?m)(^QWidget\s*\{[^}]*?\n\s*color:\s*)[^;]+', r'\g<1>%s' % color.name(), sheet,
                           count=1)

        self.qapp.setPalette(palette)
        if sheet != self.qapp.styleSheet():
            self.qapp.setStyleSheet(sheet)

    @staticmethod
    def copy_and_overwrite(from_path, to_path):
        """
        From here:
        https://stackoverflow.com/questions/12683834/how-to-copy-directory-recursively-in-python-and-overwrite-all

        :param from_path: source path
        :param to_path: destination path
        :return: None
        """
        if not os.path.exists(from_path):
            raise FileNotFoundError("Source path does not exist: %s" % from_path)
        if os.path.exists(to_path):
            shutil.rmtree(to_path)
        try:
            shutil.copytree(from_path, to_path)
        except FileNotFoundError:
            from_new_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'appGUI', 'VisPyData', 'data')
            try:
                shutil.copytree(from_new_path, to_path)
            except Exception as e:
                raise IOError("Failed to copy fallback directory: %s" % str(e)) from e

    def connect_custom_signal(self, target, params):
        try:
            self.custom_signal.disconnect()
        except TypeError:
            pass

        if target is not None:
            self.custom_signal[params].connect(target)

    def open_files_request(self, files):
        """
        Open the files of one request: the command line at the start, a second launch, a file dropped on the window.
        The requests open one after the other, and not while a dialog is open.

        :param files: list of paths (and command words)
        :return: None
        """
        self._open_requests.append(list(files))
        self._drain_open_requests()

    def _drain_open_requests(self):
        if self._open_busy:
            return
        if not self._open_requests:
            self._start_deferred_version_check()
            return
        if self.qapp.activeModalWidget() is not None:
            # a dialog is open: its nested loop must not stack another dialog on it
            if not self._open_retry_pending:
                self._open_retry_pending = True
                QtCore.QTimer.singleShot(500, lambda: self._retry_open_requests())
            return
        self._open_busy = True
        files = self._open_requests.popleft()
        try:
            self.on_startup_args(files, on_done=self._open_request_done)
        except Exception as e:
            self.log.error("App._drain_open_requests() --> %s" % str(e))
            self._open_request_done()

    def _retry_open_requests(self):
        self._open_retry_pending = False
        self._drain_open_requests()

    def _open_request_done(self):
        self._open_busy = False
        self._drain_open_requests()

    def _start_deferred_version_check(self):
        if self._version_check_due:
            self._version_check_due = False
            self.log.info(f"Checking for updates in background (this is version {self.version}).")
            self._queue_version_check()

    def on_startup_args(self, args, silent=False, on_done=None):
        """
        This will process any arguments provided to the application at startup. Like trying to launch a file or project.
        The arguments open in order: the next one starts when the project before it finished loading.

        :param silent: when True it will not print messages on Tcl Shell and/or status bar
        :param args: a list containing the application args at startup
        :param on_done: called without arguments when every argument was handled
        :return: None
        """

        if args is not None:
            args_to_process = args
        else:
            args_to_process = App.args  # noqa

        self.log.debug("Application was started with arguments: %s. Processing ..." % str(args_to_process))
        # macOS gives a file as an argument and as a FileOpen event: it opens once
        items = []
        seen = set()
        for argument in args_to_process:
            argument = local_path_from_url(argument)
            key = os.path.normcase(os.path.abspath(argument)) if os.path.splitext(argument)[1] else argument
            if key in seen:
                continue
            seen.add(key)
            items.append(argument)
        self._open_startup_items(items, silent, on_done)

    def _open_startup_items(self, items, silent, on_done):
        items = list(items)
        while items:
            argument = items.pop(0)
            if self._open_startup_item(argument, silent):
                # a project loads: the next item waits for it, the objects after it go into that project
                signal = getattr(self.f_handlers, 'project_load_done', None)
                if signal is not None:
                    expected = os.path.normcase(os.path.abspath(argument))
                    timer = QtCore.QTimer()
                    timer.setSingleShot(True)

                    def continue_after_load(_ok=True, name='', _skipped=None, rest=items, timed_out=False): # noqa
                        # the end of another request (an empty name: a refused one) is not the one that is awaited
                        if not timed_out and name and os.path.normcase(os.path.abspath(name)) != expected:
                            return
                        timer.stop()
                        signal.disconnect(continue_after_load)
                        if timed_out:
                            self.inform.emit('[WARNING_NOTCL] %s: %s' % (_("The project did not finish loading"),
                                                                       argument))
                        self._open_startup_items(rest, silent, on_done)

                    timer.timeout.connect(lambda: continue_after_load(timed_out=True))
                    timeout = self.options.get('global_background_timeout') or 600000
                    timer.start(int(timeout))
                    signal.connect(continue_after_load)
                    return
        if on_done is not None:
            on_done()

    def _report_open_failure(self, argument, what, error, silent):
        self.log.error("%s: %s" % (what, str(error)))
        message = "%s\n%s\n%s" % (what, argument, str(error))
        self.inform.emit('[ERROR_NOTCL] %s %s' % (what, argument))
        if silent is False:
            QtWidgets.QMessageBox.critical(self.ui, _("Error"), message)

    def _open_startup_item(self, argument, silent=False):
        """
        :return: True if a project load started and the next argument has to wait for it
        """
        # the file type comes from the last extension and a command word from the whole argument. Searched anywhere
        # in the path, 'top.tcl.gtl' (a Gerber for Windows) or a file in a folder 'board.tcl' was run as a Tcl script
        arg_ext = os.path.splitext(argument)[1].lower()
        arg_word = argument.strip().lower()
        if arg_ext == '.flatprj':
            try:
                project_name = str(argument)

                if project_name == "":
                    if silent is False:
                        self.inform.emit(_("Cancelled."))
                else:
                    self.f_handlers.open_project(filename=project_name, run_from_arg=True)
                    return True
            except Exception as e:
                self._report_open_failure(argument, _("Could not open the project file."), e, silent)

        elif arg_ext == '.flatconfig':
            try:
                file_name = str(argument)

                if file_name == "":
                    if silent is False:
                        self.inform.emit(_("Open Config file failed."))
                else:
                    self.f_handlers.open_config_file(file_name, run_from_arg=True)
            except Exception as e:
                self._report_open_failure(argument, _("Could not open the preferences file."), e, silent)

        elif arg_ext in ('.flatscript', '.tcl'):
            try:
                file_name = str(argument)

                if file_name == "":
                    if silent is False:
                        self.inform.emit(_("Open Script file failed."))
                else:
                    if silent is False:
                        self.f_handlers.on_file_open_script(name=file_name)
                        self.ui.plot_tab_area.setCurrentWidget(self.ui.plot_tab)
                    # a Tcl script can start programs and delete files: a script given as a file to open (a
                    # double-click, a file sent by a second launch) runs only after a Yes. The --shellfile option
                    # and the headless runs (HeadlessRunner) do not come here; they still run unattended.
                    if self.ask_run_script_arg(file_name):
                        self.f_handlers.on_file_run_script(name=file_name)
                    elif silent is False:
                        self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            except Exception as e:
                self._report_open_failure(argument, _("Could not open the script file."), e, silent)

        elif arg_word in ('quit', 'exit'):
            self.log.debug("App.on_startup_args() --> Quit event.")
            self.app_quit.emit()

        elif arg_word == 'save':
            self.log.debug("App.on_startup_args() --> Save event. App Defaults saved.")
            self.defaults.update(self.options)
            self.preferencesUiManager.save_defaults()
        else:
            # the file type is searched in the lists in turn; a file opens once and the next argument follows
            opener = (
                ('fa_excellon_group', 'exc_list_text', 'on_file_open_excellon',
                 _("Open Excellon file failed.")),
                ('fa_gcode_group', 'gco_list_text', 'on_file_open_gcode',
                 _("Open GCode file failed.")),
                ('fa_gerber_group', 'grb_list_text', 'on_file_open_gerber',
                 _("Open Gerber file failed.")),
            )
            try:
                for group_name, list_name, open_name, failed_msg in opener:
                    ext_list = getattr(getattr(self.ui.util_pref_form, group_name), list_name).get_value().split(',')
                    known = any(ext.replace(' ', '') and '.%s' % ext.replace(' ', '').lower() == arg_ext
                                for ext in ext_list)
                    if not known:
                        continue
                    file_name = str(argument)
                    if file_name == "":
                        if silent is False:
                            self.inform.emit(failed_msg)
                    else:
                        getattr(self.f_handlers, open_name)(name=file_name)
                    return False
                self._open_import_item(argument, arg_ext.lstrip('.'), silent)
            except Exception as e:
                self._report_open_failure(argument, _("Could not open the file."), e, silent)
        return False

    def _open_import_item(self, argument, ext, silent):
        """SVG, DXF and PDF files (a drop on the window): imported as Geometry."""
        reg = self.regFK
        if reg is None:
            return
        if ext in reg.svg_list:
            self.worker_task.emit({'fcn': self.f_handlers.import_svg, 'params': [argument, 'geometry', None]})
        elif ext in reg.dxf_list:
            self.worker_task.emit({'fcn': self.f_handlers.import_dxf, 'params': [argument, 'geometry', None]})
        elif ext in reg.pdf_list:
            self.pdf_tool.periodic_check(1000)
            self.worker_task.emit({'fcn': self.pdf_tool.open_pdf, 'params': [argument]})
        elif silent is False:
            self.inform.emit('[WARNING_NOTCL] %s %s' % (_("Not a file that FlatCAM opens:"), argument))

    def bring_to_front(self):
        """Show the main window (it can be minimized or hidden in the tray), raise it and give it the focus."""
        try:
            if self.ui.isMinimized():
                self.ui.showNormal()
            elif not self.ui.isVisible():
                self.ui.show()
            self.ui.raise_()
            self.ui.activateWindow()
        except Exception as e:
            self.log.debug("App.bring_to_front() --> %s" % str(e))

    def on_files_from_other_instance(self, files):
        """
        The files of a second launch (or a file opened from the Finder): bring the window to the front, then open them.

        :param files: list of paths
        :return: None
        """
        self.bring_to_front()
        self.open_files_request(files)

    def add_to_os_recent(self, kind, filename):
        if not self.headless:
            add_to_os_recent(filename)

    def ask_run_script_arg(self, file_name):
        """
        Ask the user if a Tcl script given as a file to open is run. The default answer is No.

        :param file_name:   path of the script
        :return:            True if the user answered Yes
        """

        msgbox = FCMessageBox(parent=self.ui)
        title = _("Run TCL script")
        msgbox.setWindowTitle(title)  # taskbar still shows it
        msgbox.setWindowIcon(QtGui.QIcon(self.resource_location + '/app128.png'))
        msgbox.setText('<b>%s</b>' % _("Run this script?"))
        # rich text with the path escaped: a file name with < > & in it (possible on Linux) is shown as it is
        msgbox.setInformativeText('<p>%s</p><p>%s</p>' % (
            html.escape(file_name),
            html.escape(_("A Tcl script can start programs and change or delete files. "
                          "Run it only if you trust its source."))))
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Warning)

        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)

        msgbox.setDefaultButton(bt_no)
        msgbox.exec()
        return msgbox.clickedButton() == bt_yes

    def tools_database_path(self):
        return os.path.join(self.data_path, 'tools.sqlite3')

    def defaults_path(self):
        return os.path.join(self.data_path, 'current_defaults.FlatConfig')

    def factory_defaults_path(self):
        return os.path.join(self.data_path, 'factory_defaults.FlatConfig')

    def recent_files_path(self):
        return os.path.join(self.data_path, 'recent.json')

    def recent_projects_path(self):
        return os.path.join(self.data_path, 'recent_projects.json')

    def preprocessors_path(self):
        return os.path.join(self.data_path, 'preprocessors')

    def log_path(self):
        return os.path.join(self.data_path, 'log.txt')

    def on_options_value_changed(self, key_changed):
        self.lifecycle.on_options_value_changed(key_changed=key_changed)

    def on_app_restart(self):
        self.lifecycle.on_app_restart()

    def clear_pool(self):
        """
        Free memory after the objects are gone. The pool lives as long as the application: a restart of the
        children is expensive, and maxtasksperchild already frees the memory of the children.

        :return: None
        """
        gc.collect()

    def install_tools(self, init_tcl=False):
        """
        This installs the FlatCAM tools (plugin-like) which reside in their own classes.
        Instantiation of the Tools classes.
        The order that the tools are installed is important as they can depend on each other installing position.

        :return: None
        """

        pending = getattr(self, 'script_pending_work', [])
        if init_tcl and (getattr(self, 'script_state', '') == 'running' or
                         any(not state['finished'] for state in pending)):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Cannot reset Tcl while a script is running."))
            return 'fail'

        if init_tcl:
            # Tcl "Shell" tool has to be initialized always first because other tools print messages in the Shell Dock
            self.shell = FCShell(app=self, version=self.version)
            self.log.debug("TCL was re-instantiated. TCL variables are reset.")

        self.distance_tool = Distance(self)
        self.distance_tool.install(icon=QtGui.QIcon(self.resource_location + '/distance16.png'), pos=self.ui.menuedit,
                                   before=self.ui.menuedit_numeric_move,
                                   separator=False)

        self.distance_min_tool = ObjectDistance(self)
        self.distance_min_tool.install(icon=QtGui.QIcon(self.resource_location + '/distance_min16.png'),
                                       pos=self.ui.menuedit,
                                       before=self.ui.menuedit_numeric_move,
                                       separator=True)

        self.dblsidedtool = DblSidedTool(self)
        self.dblsidedtool.install(icon=QtGui.QIcon(self.resource_location + '/doubleside16.png'), separator=False)

        self.align_objects_tool = AlignObjects(self)
        self.align_objects_tool.install(icon=QtGui.QIcon(self.resource_location + '/align16.png'), separator=False)

        self.extract_tool = ToolExtract(self)
        self.extract_tool.install(icon=QtGui.QIcon(self.resource_location + '/extract32.png'), separator=True)

        self.panelize_tool = Panelize(self)
        self.panelize_tool.install(icon=QtGui.QIcon(self.resource_location + '/panelize16.png'))

        self.film_tool = Film(self)
        self.film_tool.install(icon=QtGui.QIcon(self.resource_location + '/film32.png'))

        self.paste_tool = SolderPaste(self)
        self.paste_tool.install(icon=QtGui.QIcon(self.resource_location + '/solderpastebis32.png'))

        self.calculator_tool = ToolCalculator(self)
        self.calculator_tool.install(icon=QtGui.QIcon(self.resource_location + '/calculator32.png'), separator=True)

        self.sub_tool = ToolSub(self)
        self.sub_tool.install(icon=QtGui.QIcon(self.resource_location + '/sub32.png'),
                              pos=self.ui.menu_plugins, separator=True)

        self.rules_tool = RulesCheck(self)
        self.rules_tool.install(icon=QtGui.QIcon(self.resource_location + '/rules32.png'),
                                pos=self.ui.menu_plugins, separator=False)

        self.optimal_tool = ToolOptimal(self)
        self.optimal_tool.install(icon=QtGui.QIcon(self.resource_location + '/open_excellon32.png'),
                                  pos=self.ui.menu_plugins, separator=True)

        self.move_tool = ToolMove(self)
        self.move_tool.install(icon=QtGui.QIcon(self.resource_location + '/move16.png'), pos=self.ui.menuedit,
                               before=self.ui.menuedit_numeric_move, separator=True)

        self.cutout_tool = CutOut(self)
        self.cutout_tool.install(icon=QtGui.QIcon(self.resource_location + '/cut32.png'), pos=self.ui.menu_plugins,
                                 before=self.sub_tool.menuAction)

        self.ncclear_tool = ToolNcc(self)
        self.ncclear_tool.install(icon=QtGui.QIcon(self.resource_location + '/ncc32.png'), pos=self.ui.menu_plugins,
                                  before=self.sub_tool.menuAction, separator=True)

        self.paint_tool = ToolPaint(self)
        self.paint_tool.install(icon=QtGui.QIcon(self.resource_location + '/paint32.png'), pos=self.ui.menu_plugins,
                                before=self.sub_tool.menuAction, separator=True)

        self.isolation_tool = ToolIsolation(self)
        self.isolation_tool.install(icon=QtGui.QIcon(self.resource_location + '/iso_16.png'), pos=self.ui.menu_plugins,
                                    before=self.sub_tool.menuAction, separator=True)

        self.follow_tool = ToolFollow(self)
        self.follow_tool.install(icon=QtGui.QIcon(self.resource_location + '/follow32.png'), pos=self.ui.menu_plugins,
                                 before=self.sub_tool.menuAction, separator=True)

        self.drilling_tool = ToolDrilling(self)
        self.drilling_tool.install(icon=QtGui.QIcon(self.resource_location + '/drill32.png'),
                                   pos=self.ui.menu_plugins, before=self.sub_tool.menuAction, separator=True)
        self.milling_tool = ToolMilling(self)
        self.milling_tool.install(icon=QtGui.QIcon(self.resource_location + '/milling_tool32.png'),
                                  pos=self.ui.menu_plugins, before=self.sub_tool.menuAction, separator=True)

        self.levelling_tool = ToolLevelling(self)
        self.levelling_tool.install(icon=QtGui.QIcon(self.resource_location + '/level32.png'),
                                    pos=self.ui.menuoptions_experimental, separator=True)

        self.grbl_sender_tool = ToolGCodeSender(self)
        self.grbl_sender_tool.install(icon=QtGui.QIcon(self.resource_location + '/level32.png'),
                                      pos=self.ui.menu_plugins, before=self.sub_tool.menuAction, separator=True)

        self.copper_thieving_tool = ToolCopperThieving(self)
        self.copper_thieving_tool.install(icon=QtGui.QIcon(self.resource_location + '/copperfill32.png'),
                                          pos=self.ui.menu_plugins)

        self.fiducial_tool = ToolFiducials(self)
        self.fiducial_tool.install(icon=QtGui.QIcon(self.resource_location + '/fiducials_32.png'),
                                   pos=self.ui.menu_plugins)

        self.qrcode_tool = QRCode(self)
        self.qrcode_tool.install(icon=QtGui.QIcon(self.resource_location + '/qrcode32.png'),
                                 pos=self.ui.menu_plugins)

        self.punch_tool = ToolPunchGerber(self)
        self.punch_tool.install(icon=QtGui.QIcon(self.resource_location + '/punch32.png'), pos=self.ui.menu_plugins)

        self.invert_tool = ToolInvertGerber(self)
        self.invert_tool.install(icon=QtGui.QIcon(self.resource_location + '/invert32.png'), pos=self.ui.menu_plugins)

        self.markers_tool = ToolMarkers(self)
        self.markers_tool.install(icon=QtGui.QIcon(self.resource_location + '/corners_32.png'),
                                  pos=self.ui.menu_plugins)

        self.etch_tool = ToolEtchCompensation(self)
        self.etch_tool.install(icon=QtGui.QIcon(self.resource_location + '/etch_32.png'), pos=self.ui.menu_plugins)

        self.transform_tool = ToolTransform(self)
        self.transform_tool.install(icon=QtGui.QIcon(self.resource_location + '/transform.png'),
                                    pos=self.ui.menuoptions, separator=True)

        self.report_tool = ObjectReport(self)
        self.report_tool.install(icon=QtGui.QIcon(self.resource_location + '/properties32.png'),
                                 pos=self.ui.menuoptions)

        self.pdf_tool = ToolPDF(self)
        self.pdf_tool.install(icon=QtGui.QIcon(self.resource_location + '/pdf32.png'),
                              pos=self.ui.menufileimport,
                              separator=True)

        try:
            if 'ToolImage' in globals():
                self.image_tool = ToolImage(self)
                self.image_tool.install(icon=QtGui.QIcon(self.resource_location + '/image32.png'),
                                        pos=self.ui.menufileimport,
                                        separator=True)
            else:
                self.log.warning("ToolImage plugin not available. Install rasterio for image import support.")
                self.image_tool = None
        except Exception as im_err:
            self.log.error("Image Import plugin could not be started due of: %s" % str(im_err))
            self.image_tool = None

        self.pcb_wizard_tool = PcbWizard(self)
        self.pcb_wizard_tool.install(icon=QtGui.QIcon(self.resource_location + '/drill32.png'),
                                     pos=self.ui.menufileimport)

        # create a list of plugins references
        self.app_plugins = [
            self.dblsidedtool,
            self.distance_tool,
            self.distance_min_tool,
            self.panelize_tool,
            self.film_tool,
            self.paste_tool,
            self.calculator_tool,
            self.rules_tool,
            self.sub_tool,
            self.move_tool,

            self.cutout_tool,
            self.ncclear_tool,
            self.paint_tool,
            self.isolation_tool,
            self.follow_tool,
            self.drilling_tool,
            self.milling_tool,
            self.levelling_tool,
            self.grbl_sender_tool,

            self.optimal_tool,
            self.transform_tool,
            self.report_tool,
            self.pdf_tool,
            self.image_tool,
            self.pcb_wizard_tool,
            self.qrcode_tool,
            self.copper_thieving_tool,
            self.fiducial_tool,
            self.extract_tool,
            self.align_objects_tool,
            self.punch_tool,
            self.invert_tool,
            self.markers_tool,
            self.etch_tool
        ]

        # Filter out None values (e.g., if image_tool failed to initialize)
        self.app_plugins = [p for p in self.app_plugins if p is not None]

        self.log.debug("Tools are installed.")

    def remove_tools(self):
        """
        Will remove all the actions in the Tool menu.
        :return: None
        """
        for act in self.ui.menu_plugins.actions():
            self.ui.menu_plugins.removeAction(act)

    def init_tools(self, init_tcl=True):
        """
        Initialize the Tool tab in the notebook side of the central widget.
        Remove the actions in the Tools menu.
        Instantiate again the FlatCAM tools (plugins).
        All this is required when changing the layout: standard, compact etc.

        :param init_tcl:    Bool. If True will init the Tcl Shell
        :return:            None
        """

        pending = getattr(self, 'script_pending_work', [])
        if init_tcl and (getattr(self, 'script_state', '') == 'running' or
                         any(not state['finished'] for state in pending)):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Cannot reset Tcl while a script is running."))
            return 'fail'

        self.log.debug("init_tools()")

        # delete the data currently in the Tools Tab and the Tab itself
        found_idx = None
        for tab_idx in range(self.ui.notebook.count()):
            if self.ui.notebook.widget(tab_idx).objectName() == "plugin_tab":
                found_idx = tab_idx
                break
        if found_idx is not None:
            widget = QtWidgets.QTabWidget.widget(self.ui.notebook, found_idx)
            if widget is not None:
                widget.deleteLater()
            self.ui.notebook.removeTab(found_idx)

        # rebuild the Tools Tab
        # self.ui.plugin_tab = QtWidgets.QWidget()
        # self.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.ui.plugin_tab)
        # self.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
        # self.ui.notebook.addTab(self.ui.plugin_tab, _("Tool"))
        # self.ui.plugin_scroll_area = VerticalScrollArea()
        # self.ui.plugin_tab_layout.addWidget(self.ui.plugin_scroll_area)

        # reinstall all the Tools as some may have been removed when the data was removed from the Tools Tab
        # first remove all of them
        self.remove_tools()

        # re-add the TCL "Shell" action to the Tools menu and reconnect it to ist slot function
        self.ui.menu_plugins_shell = self.ui.menu_plugins.addAction(
            QtGui.QIcon(self.resource_location + '/shell16.png'), '&Command Line\tS')
        self.ui.menu_plugins_shell.triggered.connect(self.ui.toggle_shell_ui)

        # third install all of them
        t0 = time.time()
        try:
            self.install_tools(init_tcl=init_tcl)
        except AttributeError:
            pass

        self.log.debug("%s: %s" % ("Tools are initialized in", str(time.time() - t0)))

    # def parse_system_fonts(self):
    #     self.worker_task.emit({'fcn': self.f_parse.get_fonts_by_types,
    #                            'params': []})

    def connect_filemenu_signals(self):
        self.signal_connector.connect_filemenu_signals()

    def connect_editmenu_signals(self):
        self.signal_connector.connect_editmenu_signals()

    def connect_optionsmenu_signals(self):
        self.signal_connector.connect_optionsmenu_signals()

    def connect_menuview_signals(self):
        self.signal_connector.connect_menuview_signals()

    def connect_menuhelp_signals(self):
        self.signal_connector.connect_menuhelp_signals()

    def connect_project_context_signals(self):
        self.signal_connector.connect_project_context_signals()

    def connect_canvas_context_signals(self):
        self.signal_connector.connect_canvas_context_signals()

    def connect_tools_signals_to_toolbar(self):
        self.signal_connector.connect_tools_signals_to_toolbar()

    def connect_editors_toolbar_signals(self):
        self.signal_connector.connect_editors_toolbar_signals()

    def connect_toolbar_signals(self):
        self.signal_connector.connect_toolbar_signals()

    def on_layout(self, lay=None, connect_signals=True):
        """
        Set the toolbars layout (location).

        :param connect_signals: Useful when used in the App.__init__(); bool
        :param lay:             Type of layout to be set on the toolbar
        :return:                None
        """
        self.lifecycle.on_layout(lay=lay, connect_signals=connect_signals)

    def _history_edit_begin(self, obj):
        """Keep the state of an object that its editor changes in place (Geometry, Gerber, Excellon, CNCJob)."""
        self._history_edit = None
        if obj.kind not in ('geometry', 'gerber', 'excellon', 'cncjob'):
            return
        try:
            self._history_edit = (obj, object_state(obj))
        except Exception as e:
            self.log.error("App._history_edit_begin() --> %s" % str(e))
            self._history_edit = None

    def _history_edit_saved(self, obj):
        """Record the editor save of the object as 1 history step, with the state kept by _history_edit_begin()."""
        edit = getattr(self, '_history_edit', None)
        self._history_edit = None
        if edit is None or edit[0] is not obj:
            return
        try:
            history_of(self).push(SnapshotStep(_("Edit"), [(obj.history_uid, edit[1], object_state(obj))]))
        except Exception as e:
            self.log.error("App._history_edit_saved() --> %s" % str(e))

    def on_editing_start(self):
        """
        Send the current Geometry, Gerber, "Excellon" object or CNCJob (if any) its editor.

        :return: None
        """
        self.defaults.report_usage("on_editing_start()")

        if self.call_source == 'geo_editor' and AppGeoEditor.reject_busy(self.geo_editor):
            return 'fail'
        if (self.call_source == 'grb_editor' and isinstance(self.grb_editor, AppGerberEditor) and
                not self.grb_editor.history.ready()):
            return 'fail'

        edited_object = self.collection.get_active()
        if edited_object is None:
            self.inform.emit('[ERROR_NOTCL] %s %s' % (_("The Editor could not start."), _("No object is selected.")))
            return

        if edited_object and edited_object.kind in ['cncjob', 'excellon', 'geometry', 'gerber']:
            if edited_object.kind != 'geometry':
                edited_object.build_ui()
        else:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Select a Geometry, Gerber, Excellon or CNCJob Object to edit."))
            self.ui.menuobjects.setDisabled(False)
            return

        if self.call_source == 'app':
            self._editor_gui_state = self.ui.saveState(0)

        self._history_edit_begin(edited_object)

        self.ui.notebook.setCurrentWidget(self.ui.properties_tab)

        if edited_object.kind == 'geometry':
            if self.geo_editor is None:
                self.ui.menuobjects.setDisabled(False)
                self.inform.emit('[ERROR_NOTCL] %s' % _("The Editor could not start."))
                return

            # store the Geometry Editor Toolbar visibility before entering the Editor
            self.geo_editor.toolbar_old_state = True if self.ui.geo_edit_toolbar.isVisible() else False

            # we set the notebook to hidden
            # self.ui.splitter.setSizes([0, 1])
            if edited_object.multigeo is True:
                sel_rows = set()
                for item in edited_object.ui.geo_tools_table.selectedItems():
                    sel_rows.add(item.row())
                sel_rows = list(sel_rows)

                if len(sel_rows) > 1:
                    self.inform.emit('[WARNING_NOTCL] %s' %
                                     _("Simultaneous editing of tools geometry in a MultiGeo Geometry "
                                       "is not possible.\n"
                                       "Edit only one geometry at a time."))
                    self.ui.menuobjects.setDisabled(False)
                    return

                if not sel_rows:
                    self.inform.emit('[WARNING_NOTCL] %s.' % _("No Tool Selected"))
                    self.ui.menuobjects.setDisabled(False)
                    return

                # determine the tool dia of the selected tool
                # selected_tooldia = float(edited_object.ui.geo_tools_table.item(sel_rows[0], 1).text())
                sel_id = int(edited_object.ui.geo_tools_table.item(sel_rows[0], 5).text())

                multi_tool = sel_id
                self.log.debug("Editing MultiGeo Geometry with tool diameter: %s" % str(multi_tool))
                self.geo_editor.edit_geometry(edited_object, multigeo_tool=multi_tool)
            else:
                self.log.debug("Editing SingleGeo Geometry with tool diameter.")
                self.geo_editor.edit_geometry(edited_object)

            # set call source to the Editor we go into
            self.call_source = 'geo_editor'
        elif edited_object.kind == 'excellon':
            if self.exc_editor is None:
                self.ui.menuobjects.setDisabled(False)
                self.inform.emit('[ERROR_NOTCL] %s' % _("The Editor could not start."))
                return

            # store the Excellon Editor Toolbar visibility before entering the Editor
            self.exc_editor.toolbar_old_state = True if self.ui.exc_edit_toolbar.isVisible() else False

            if self.ui.splitter.sizes()[0] == 0:
                self.ui.splitter.setSizes([1, 1])

            self.exc_editor.edit_fcexcellon(edited_object)

            # set call source to the Editor we go into
            self.call_source = 'exc_editor'
        elif edited_object.kind == 'gerber':
            if self.grb_editor is None:
                self.ui.menuobjects.setDisabled(False)
                self.inform.emit('[ERROR_NOTCL] %s' % _("The Editor could not start."))
                return

            # store the Gerber Editor Toolbar visibility before entering the Editor
            self.grb_editor.toolbar_old_state = True if self.ui.grb_edit_toolbar.isVisible() else False

            if self.ui.splitter.sizes()[0] == 0:
                self.ui.splitter.setSizes([1, 1])

            self.grb_editor.edit_fcgerber(edited_object)

            # set call source to the Editor we go into
            self.call_source = 'grb_editor'

            # reset the following variables so the UI is built again after edit
            edited_object.ui_build = False
        elif edited_object.kind == 'cncjob':
            if self.gcode_editor is None:
                self.ui.menuobjects.setDisabled(False)
                self.inform.emit('[ERROR_NOTCL] %s' % _("The Editor could not start."))
                return

            if self.ui.splitter.sizes()[0] == 0:
                self.ui.splitter.setSizes([1, 1])

            # set call source to the Editor we go into
            self.call_source = 'gcode_editor'

            self.gcode_editor.edit_fcgcode(edited_object)

        for idx in range(self.ui.notebook.count()):
            # store the Properties Tab text color here and change the color and text
            if self.ui.notebook.tabText(idx) == _("Properties"):
                self.old_tab_text_color = self.ui.notebook.tabBar.tabTextColor(idx)
                self.ui.notebook.tabBar.setTabTextColor(idx, QtGui.QColor('red'))
                self.ui.notebook.tabBar.setTabText(idx, _("Editor"))

            # disable the Project Tab
            if self.ui.notebook.tabText(idx) == _("Project"):
                self.ui.notebook.tabBar.setTabEnabled(idx, False)

        # delete any selection shape that might be active as they are not relevant in Editor
        self.delete_selection_shape()

        # hide the Tools Toolbar
        plugins_tb = self.ui.toolbarplugins
        if plugins_tb.isVisible():
            self.old_state_of_tools_toolbar = True
            plugins_tb.hide()
        else:
            self.old_state_of_tools_toolbar = False

        # make sure that we can't select another object while in Editor Mode:
        self.ui.project_frame.setDisabled(True)
        # disable the objects menu as it may interfere with the appEditors
        self.ui.menuobjects.setDisabled(True)
        # disable the tools menu as it makes sense not to be available when in the Editor
        self.ui.menu_plugins.setDisabled(True)

        self.ui.plot_tab_area.setTabText(0, _("EDITOR Area"))
        self.ui.plot_tab_area.protectTab(0)
        self.log.debug("######################### Starting the EDITOR ################################")
        self.inform.emit('[WARNING_NOTCL] %s' % _("Editor is activated ..."))

        self.should_we_save = True

    def _editor_for(self, kind):
        """
        :param kind:    object kind: 'geometry', 'excellon', 'gerber' or 'cncjob'
        :return:        the editor of that kind (None if it was not created) or None for an unknown kind
        """
        attr = _EDITOR_ATTR_BY_KIND.get(kind)
        return getattr(self, attr, None) if attr else None

    def _deactivate_editor(self, kind):
        """
        Deactivate the editor of the given object kind. An unknown kind or an editor that does not exist: no-op.
        """
        editor = self._editor_for(kind)
        if editor is not None:
            editor.deactivate()

    def _edited_obj_is_empty(self, obj):
        """
        True if an object that leaves its editor has no content and the source object should be deleted.
        Only Gerber and Excellon are deleted when empty; a Geometry or a CNCJob object never is.
        """
        if obj.kind in ('gerber', 'excellon'):
            return feature_access.is_empty(obj)
        return False

    def _copy_geometry_for_save_as_new(self, source):
        """
        Save as New of the Geometry Editor: make the copy of the source object that gets the edited content (its
        name is <name>_edit, <name>_edit_1...). The copy has the content and the options of the source, so the
        editor can write the edited content into it like into the source. It is not plotted, not selected and not
        in the history yet: the caller records it after the content is in.

        :param source:  the GeometryObject that is edited
        :return:        the new GeometryObject, or None if it could not be made
        """
        def init(new_obj, app_obj):
            new_obj.copy_from(source)
            for key, val in source.obj_options.items():
                if key != 'name':
                    new_obj.obj_options[key] = deepcopy(val)

        try:
            new_obj = self.app_obj.new_object('geometry', edit_copy_name(source.obj_options['name']), init,
                                              plot=False, autoselected=False, record_history=False)
        except Exception as e:
            self.log.error("App._copy_geometry_for_save_as_new() --> %s" % str(e))
            new_obj = None
        if new_obj is None or isinstance(new_obj, str):
            self.inform.emit('[ERROR_NOTCL] %s' % _("The copy of the object could not be made."))
            return None
        return new_obj

    def on_editing_finished(self, cleanup=None, force_cancel=None):
        """
        Transfers the Geometry or an "Excellon", from its editor to the current object.

        :param cleanup:         if True then we closed the app when the editor was open, so we close first the editor
        :param force_cancel:    if True always add Cancel button
        :return:                'fail' if busy, 'pending' while geometry source generation is deferred, else None
        """
        self.defaults.report_usage("on_editing_finished()")

        # do not update a Geometry/"Excellon"/Gerber/GCode object unless it comes out of an editor
        if self.call_source == 'app':
            return

        if self.call_source == 'geo_editor' and AppGeoEditor.reject_busy(self.geo_editor):
            return 'fail'
        if (self.call_source == 'grb_editor' and isinstance(self.grb_editor, AppGerberEditor) and
                not self.grb_editor.history.ready()):
            return 'fail'

        # make sure that when we exit an Editor with a tool active then we make some clean-up
        try:
            self.plotcanvas.text_cursor.parent = None
            self.plotcanvas.view.camera.zoom_callback = lambda *args: None
        except Exception:
            pass

        # This is the object that exit from the Editor. It may be the edited object, but it can be a new object
        # created by the Editor
        edited_obj = self.collection.get_active()

        if edited_obj is None:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
            return

        if cleanup is None:
            msgbox = FCMessageBox(parent=self.ui)
            title = _("Exit Editor")
            txt = _("Do you want to save the changes?")
            msgbox.setWindowTitle(title)  # taskbar still shows it
            msgbox.setWindowIcon(QtGui.QIcon(self.resource_location + '/app128.png'))
            msgbox.setText('<b>%s</b>' % title)
            msgbox.setInformativeText(txt)
            msgbox.setIconPixmap(QtGui.QPixmap(self.resource_location + '/save_as.png'))

            # Save changes the source object, Save as New leaves it unchanged and makes a new object
            has_save_as_new = edited_obj.kind in ["geometry", "gerber", "excellon"]
            bt_yes = msgbox.addButton(_('Save') if has_save_as_new else _('Yes'),
                                      QtWidgets.QMessageBox.ButtonRole.YesRole)
            bt_new = msgbox.addButton(_('Save as New'), QtWidgets.QMessageBox.ButtonRole.ActionRole) \
                if has_save_as_new else None
            bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
            if edited_obj.kind in ["geometry", "gerber", "excellon"] or force_cancel is not None:
                msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)

            msgbox.setDefaultButton(bt_yes)
            msgbox.exec()
            response = msgbox.clickedButton()

            # The modal dialog processes events: recheck before accepting either Save or Discard.
            if self.call_source == 'geo_editor' and AppGeoEditor.reject_busy(self.geo_editor):
                return 'fail'
            if (self.call_source == 'grb_editor' and isinstance(self.grb_editor, AppGerberEditor) and
                    not self.grb_editor.history.ready()):
                return 'fail'

            if response == bt_yes or (bt_new is not None and response == bt_new):
                save_as_new = response != bt_yes
                # the object that gets the edited content: the source, or its copy (Geometry, Save as New)
                saved_obj = edited_obj

                def finish_save():
                    # GUI-only continuation: geometry source generation may still be queued when Save returns.
                    # show the Tools Toolbar
                    plugins_tb = self.ui.toolbarplugins
                    if self.old_state_of_tools_toolbar is True:
                        plugins_tb.show()

                    # clean the Tools Tab
                    found_idx = None
                    for idx in range(self.ui.notebook.count()):
                        if self.ui.notebook.widget(idx).objectName() == "plugin_tab":
                            found_idx = idx
                            break
                    if found_idx is not None:
                        self.ui.notebook.setCurrentWidget(self.ui.properties_tab)
                        self.ui.notebook.removeTab(found_idx)

                    if edited_obj.kind == 'geometry':
                        obj_type = "Geometry"
                        # self.geo_editor.update_options(edited_obj)

                        # restore GUI to the Selected TAB
                        # Remove anything else in the appGUI
                        self.ui.plugin_scroll_area.takeWidget()

                        # update the geo object options, so it is including the bounding box values
                        try:
                            xmin, ymin, xmax, ymax = saved_obj.bounds(flatten=True)
                            saved_obj.obj_options['xmin'] = xmin
                            saved_obj.obj_options['ymin'] = ymin
                            saved_obj.obj_options['xmax'] = xmax
                            saved_obj.obj_options['ymax'] = ymax
                        except (AttributeError, ValueError) as e:
                            self.inform.emit('[WARNING] %s' % _("Object empty after edit."))
                            self.log.debug("App.on_editing_finished() --> Geometry --> %s" % str(e))

                        saved_obj.build_ui()
                        saved_obj.plot()
                        if save_as_new:
                            # the copy got its content after it was created: record it as new only now
                            history_of(self).record_create('%s: %s' % (_("New"), saved_obj.obj_options['name']),
                                                           [saved_obj])
                            # select the new object, as the Gerber and the Excellon Save as New do (after the
                            # editor teardown, which still works on the selection)
                            new_name = saved_obj.obj_options['name']

                            def select_new():
                                self.collection.set_all_inactive()
                                self.collection.set_active(new_name)
                            QtCore.QTimer.singleShot(0, select_new)
                        self.inform.emit('[success] %s' % _("Editor exited. Editor content saved."))

                    elif edited_obj.kind == 'gerber':
                        obj_type = "Gerber"
                        if save_as_new:
                            self.grb_editor.update_fcgerber()
                            # self.grb_editor.update_options(edited_obj)

                            # delete the old object (the source object) if it was an empty one
                            if self._edited_obj_is_empty(edited_obj):
                                old_name = edited_obj.obj_options['name']
                                history_of(self).record_delete(_("Edit"), [edited_obj])
                                self.collection.delete_by_name(old_name)

                        self.inform.emit('[success] %s' % _("Editor exited. Editor content saved."))

                        # restore GUI to the Selected TAB
                        # Remove anything else in the GUI
                        self.ui.properties_scroll_area.takeWidget()
                        if not save_as_new:
                            # the source was updated before this continuation
                            self.grb_editor.refresh_edited_gerber(edited_obj)

                    elif edited_obj.kind == 'excellon':
                        obj_type = "Excellon"

                        # restore GUI to the Selected TAB
                        # Remove anything else in the GUI
                        self.ui.plugin_scroll_area.takeWidget()

                        if not save_as_new:
                            # the source was updated before this continuation
                            self.exc_editor.refresh_edited_excellon(edited_obj)
                        self.inform.emit('[success] %s' % _("Editor exited. Editor content saved."))

                    elif edited_obj.kind == 'cncjob':
                        obj_type = "CNCJob"
                        self.gcode_editor.update_fcgcode(edited_obj)
                        # self.exc_editor.update_options(edited_obj)

                        # restore GUI to the Selected TAB
                        # Remove anything else in the GUI
                        self.ui.plugin_scroll_area.takeWidget()
                        edited_obj.build_ui()

                        # close the open tab
                        for idx in range(self.ui.plot_tab_area.count()):
                            if self.ui.plot_tab_area.widget(idx).objectName() == 'gcode_editor_tab':
                                self.ui.plot_tab_area.closeTab(idx)
                        self.inform.emit('[success] %s' % _("Editor exited. Editor content saved."))

                    else:
                        self.inform.emit('[WARNING_NOTCL] %s' %
                                         _("Select a Gerber, Geometry, Excellon or CNCJob Object to update."))
                        return

                    # Save changes the source object in place: 1 history step (the earlier steps stay valid, the
                    # step has the full before / after state). Save as New makes a new object (a New step) and
                    # the source stays as it was.
                    if save_as_new:
                        self._history_edit = None
                    else:
                        self._history_edit_saved(edited_obj)

                    # make sure to update the Offset field in Properties Tab
                    try:
                        saved_obj.set_offset_values()
                    except AttributeError:
                        # not all objects have this attribute
                        pass

                    self.inform.emit('[selected] %s %s' % (obj_type, _("is updated, returning to App...")))
                    self.post_edit_sig.emit()

                if edited_obj.kind == 'geometry':
                    if save_as_new:
                        saved_obj = self._copy_geometry_for_save_as_new(edited_obj)
                        if saved_obj is None:
                            return 'fail'
                    result = self.geo_editor.update_editor_geometry(saved_obj, on_complete=finish_save)
                    if result == 'fail' and save_as_new:
                        # the save was rejected: the copy is not needed
                        self.collection.delete_by_name(saved_obj.obj_options['name'])
                    return result
                if edited_obj.kind == 'gerber' and not save_as_new:
                    # the source is updated by a worker task and on the GUI thread; finish_save is the continuation
                    self.grb_editor.update_fcgerber_in_place(on_complete=finish_save)
                    return 'pending'
                if edited_obj.kind == 'excellon':
                    try:
                        result = self.exc_editor.update_fcexcellon(
                            edited_obj, in_place=not save_as_new, on_complete=finish_save)
                    except Exception as e:
                        self.log.error("App.on_editing_finished() --> Excellon --> %s" % str(e))
                        self.inform.emit('[ERROR_NOTCL] %s' % _("The edited content could not be saved. "
                                                                "The object is unchanged."))
                        return 'fail'
                    return 'fail' if result == 'fail' else 'pending'
                return finish_save()
            elif response == bt_no:
                self._history_edit = None
                # show the Tools Toolbar
                plugins_tb = self.ui.toolbarplugins
                if self.old_state_of_tools_toolbar is True:
                    plugins_tb.show()

                # clean the Tools Tab
                found_idx = None
                for idx in range(self.ui.notebook.count()):
                    if self.ui.notebook.widget(idx).objectName() == "plugin_tab":
                        found_idx = idx
                        break
                if found_idx is not None:
                    self.ui.notebook.setCurrentWidget(self.ui.properties_tab)
                    self.ui.notebook.removeTab(found_idx)

                self.inform.emit('[WARNING_NOTCL] %s' % _("Editor exited. Editor content was not saved."))

                self._deactivate_editor(edited_obj.kind)
                if edited_obj.kind == 'geometry':
                    edited_obj.build_ui()
                    edited_obj.plot()
                elif edited_obj.kind in ('gerber', 'excellon'):
                    edited_obj.build_ui()
                elif edited_obj.kind == 'cncjob':
                    edited_obj.build_ui()

                    # close the open tab
                    for idx in range(self.ui.plot_tab_area.count()):
                        try:
                            if self.ui.plot_tab_area.widget(idx).objectName() == 'gcode_editor_tab':
                                self.ui.plot_tab_area.closeTab(idx)
                                break
                        except AttributeError:
                            continue
            else:
                self.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Select a Gerber, Geometry, Excellon or CNCJob Object to update."))
                return

            # edited_obj.set_ui(edited_obj.ui_type(decimals=self.decimals))
            # edited_obj.build_ui()
            # Switch notebook to Properties page
            # self.ui.notebook.setCurrentWidget(self.ui.properties_tab)
        else:
            self._history_edit = None
            # show the Tools Toolbar
            plugins_tb = self.ui.toolbarplugins
            if self.old_state_of_tools_toolbar is True:
                plugins_tb.show()

            if edited_obj.kind in _EDITOR_ATTR_BY_KIND:
                self._deactivate_editor(edited_obj.kind)
            else:
                self.inform.emit(
                    '[WARNING_NOTCL] %s' % _("Select a Gerber, Geometry, Excellon or CNCJob Object to update.")
                )
                return

        self.post_edit_sig.emit()

    def on_editing_final_action(self):
        self.log.debug("######################### Closing the EDITOR ################################")
        self.call_source = 'app'

        # if notebook is hidden we show it
        if self.ui.splitter.sizes()[0] == 0:
            self.ui.splitter.setSizes([1, 1])

        # change back the tab name
        for idx in range(self.ui.notebook.count()):
            # restore the Properties Tab text and color
            if self.ui.notebook.tabText(idx) == _("Editor"):
                self.ui.notebook.tabBar.setTabTextColor(idx, self.old_tab_text_color)
                self.ui.notebook.tabBar.setTabText(idx, _("Properties"))
                self.ui.app.on_notebook_tab_changed()

            # enable the Project Tab
            if self.ui.notebook.tabText(idx) == _("Project"):
                self.ui.notebook.tabBar.setTabEnabled(idx, True)

        self.ui.plot_tab_area.setTabText(0, _("Plot Area"))
        self.ui.plot_tab_area.protectTab(0)

        # make sure that we re-enable the selection on Project Tab after returning from Editor Mode:
        self.ui.project_frame.setDisabled(False)

        QMetaObject.invokeMethod(self, "modify_menu_items", Qt.ConnectionType.QueuedConnection)

    @QtCore.pyqtSlot()
    def modify_menu_items(self):
        # re-enable the objects menu that was disabled on entry in Editor mode
        self.ui.menuobjects.setDisabled(False)
        # re-enable the tool menu that was disabled on entry in Editor mode
        self.ui.menu_plugins.setDisabled(False)

    def get_last_folder(self):
        """
        Get the folder path from where the last file was opened.
        :return: String, last opened folder path
        """
        return self.options.get("global_last_folder", self.defaults.get("global_last_folder"))

    def get_last_save_folder(self):
        """
        Get the folder path from where the last file was saved.
        :return: String, last saved folder path
        """
        loc = self.options.get("global_last_save_folder", self.defaults.get("global_last_save_folder"))
        if loc is None:
            loc = self.options.get("global_last_folder", self.defaults.get("global_last_folder"))
        if loc is None:
            loc = os.path.dirname(__file__)
        return loc

    @QtCore.pyqtSlot(str)
    @QtCore.pyqtSlot(str, bool)
    def info(self, msg, shell_echo=True):
        """
        Informs the user. Normally on the status bar, optionally also on the shell.

        :param msg:         Text to write. Composed of a first part between brackets which is the level and the rest
                            which is the message. The level part will control the text color and the used icon
        :type msg:          str
        :param shell_echo:  Control if to display the message msg in the Shell
        :type shell_echo:   bool
        :return: None
        """

        # Type of message in brackets at the beginning of the message.
        match = re.search(r"^\[(.*?)](.*)", msg)
        if match:
            level = match.group(1)
            msg_ = match.group(2)
            self.ui.fcinfo.set_status(str(msg_), level=level)

            if shell_echo is True:
                if level.lower() == "error":
                    self.shell_message(msg, error=True, show=True)
                elif level.lower() == "warning":
                    self.shell_message(msg, warning=True, show=True)

                elif level.lower() == "error_notcl":
                    self.shell_message(msg, error=True, show=False)

                elif level.lower() == "warning_notcl":
                    self.shell_message(msg, warning=True, show=False)

                elif level.lower() == "success":
                    self.shell_message(msg, success=True, show=False)

                elif level.lower() == "selected":
                    self.shell_message(msg, selected=True, show=False)

                else:
                    self.shell_message(msg, show=False)

        else:
            self.ui.fcinfo.set_status(str(msg), level="info")

            # make sure that if the message is to clear the infobar with a space
            # is not printed over and over on the shell
            if msg != '' and shell_echo is True:
                self.shell_message(msg)
        # QtWidgets.QApplication.processEvents()

    def info_shell(self, msg, new_line=True):
        """
        A handler for a signal that call for printing directly on the Tcl Shell without printing in status bar.

        :param msg:         The message to be printed
        :type msg:          str
        :param new_line:    if True then after printing the message add a new line char
        :type new_line:     bool
        :return:
        :rtype:
        """
        self.shell_message(msg=msg, new_line=new_line)

    def save_to_file(self, content_to_save, txt_content):
        """
        Save something to a file.

        :param content_to_save: text when is in HTML
        :type content_to_save:  str
        :param txt_content:     text that is not HTML
        :type txt_content:      str
        :return:
        :rtype:
        """
        self.defaults.report_usage("save_to_file")
        self.log.debug("save_to_file()")

        date = str(dt.today()).rpartition('.')[0]
        date = ''.join(c for c in date if c not in ':-')
        date = date.replace(' ', '_')

        filter__ = "HTML File .html (*.html);;TXT File .txt (*.txt);;All Files (*.*)"
        last_save_folder = self.options.get("global_last_save_folder", self.defaults.get("global_last_save_folder"))
        path_to_save = last_save_folder if last_save_folder is not None else self.data_path
        final_path = os.path.join(path_to_save, 'file_%s' % str(date))

        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save to file"),
                directory=final_path,
                ext_filter=filter__
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save to file"),
                ext_filter=filter__)

        filename = str(filename)

        if filename == "":
            self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
            return
        else:
            if os.path.exists(filename) and not os.access(filename, os.W_OK):
                self.inform.emit('[WARNING] %s' %
                                 _("Permission denied, saving not possible.\n"
                                   "Most likely another app is holding the file open and not accessible."))
                return

            # Save content
            if filename.rpartition('.')[2].lower() == 'html':
                file_content = content_to_save
            else:
                file_content = txt_content

            try:
                with open(filename, "w") as f:
                    f.write(file_content)
            except Exception as e:
                self.log.error("App.save_to_file() --> Failed to write to %s: %s" % (str(filename), str(e)))
                self.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed to write to file."), str(filename)))
                return

        self.inform.emit('[success] %s: %s' % (_("Exported file to"), filename))

    def register_recent(self, kind, filename):
        """
        Will register the files opened into record dictionaries. The FlatCAM projects has its own
        dictionary.

        :param kind:        type of file that was opened
        :param filename:    the path and file name for the file that was opened
        :return:
        """
        self.log.debug("register_recent()")
        self.log.debug("   %s" % kind)
        self.log.debug("   %s" % filename)

        record = {'kind': str(kind), 'filename': str(filename)}
        # a file that is opened again moves to the top of the list
        target = self.recent_projects if record['kind'] == 'project' else self.recent
        if record in target:
            target.remove(record)
        target.insert(0, record)

        if len(self.recent) > self.options.get('global_recent_limit', self.defaults.get('global_recent_limit')):  # Limit reached
            self.recent.pop()

        if len(self.recent_projects) > self.options.get('global_recent_limit', self.defaults.get('global_recent_limit')):  # Limit reached
            self.recent_projects.pop()

        if self.app_obj.load_batches:
            return
        self.flush_recent_items()

    def flush_recent_items(self):
        """Persist and rebuild recent items once after batch integration."""
        try:
            with open(os.path.join(self.data_path, 'recent.json'), 'w') as f:
                json.dump(self.recent, f, default=to_dict, indent=2, sort_keys=True)
        except IOError:
            self.log.error("Failed to open recent items file for writing.")
            self.inform.emit('[ERROR_NOTCL] %s' %
                             _('Failed to open recent files file for writing.'))
            return

        try:
            with open(os.path.join(self.data_path, 'recent_projects.json'), 'w') as fp:
                json.dump(self.recent_projects, fp, default=to_dict, indent=2, sort_keys=True)
        except IOError:
            self.log.error("Failed to open recent items file for writing.")
            self.inform.emit('[ERROR_NOTCL] %s' %
                             _('Failed to open recent projects file for writing.'))
            return

        # Re-build the recent items menu
        self.setup_recent_items()

    def on_about(self):
        self.ui_actions.on_about()

    def on_howto(self):
        self.ui_actions.on_howto()

    def install_bookmarks(self, book_dict=None):
        self.ui_actions.install_bookmarks(book_dict=book_dict)

    def on_bookmarks_manager(self):
        self.ui_actions.on_bookmarks_manager()

    def on_backup_site(self):
        self.ui_actions.on_backup_site()

    def on_check_for_updates(self):
        """Start a user-requested update check without changing preferences."""
        self._queue_version_check(forced=True)

    def _run_queued_version_check(self, forced=False):
        self._check_queued = False
        self.version_check(forced=forced)

    def _queue_version_check(self, forced=False):
        if self._check_queued or self._check_in_progress:
            return
        self._check_queued = True
        try:
            self.worker_task.emit({
                'fcn': self._run_queued_version_check,
                'params': [forced],
            })
        except Exception as exc:
            self._check_queued = False
            self.log.error("Could not queue update check: %s" % str(exc))

    def _update_operation_active(self):
        if (
            getattr(self, "_update_in_progress", False)
            or getattr(self, "_rollback_in_progress", False)
            or getattr(self, "_release_preparation_in_progress", False)
        ):
            return True
        for name in ("_update_progress_dialog", "_update_dialog"):
            dialog = getattr(self, name, None)
            if dialog is None:
                continue
            try:
                if dialog.isVisible():
                    return True
            except (AttributeError, RuntimeError):
                continue
        return False

    def _updater_shutdown_preflight(self):
        """Confirm data safety before an external updater is allowed to start."""
        lifecycle = getattr(self, "lifecycle", None)
        confirm_database = getattr(lifecycle, "_confirm_tools_database", None)
        if callable(confirm_database) and not confirm_database():
            self.inform.emit('[WARNING_NOTCL] %s' % _("Update canceled while closing the tools database."))
            return False
        if getattr(self, "save_in_progress", False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("Please wait for the project save to finish before updating."))
            return False
        if getattr(self, "should_we_save", False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("Save the current project before updating."))
            return False
        return True

    def _close_update_progress(self):
        dialog = getattr(self, "_update_progress_dialog", None)
        if dialog is not None:
            try:
                # QProgressDialog emits canceled when it closes; a programmatic close is not a user cancel.
                dialog.canceled.disconnect()
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                dialog.close()
            except (AttributeError, RuntimeError):
                pass
        self._update_progress_dialog = None

    def _on_update_progress(self, value, text):
        dialog = getattr(self, "_update_progress_dialog", None)
        if dialog is None:
            return
        try:
            dialog.setLabelText(text)
            if value < 0:
                # A negative value means the transport cannot report a total yet.
                if dialog.maximum() != 0:
                    dialog.setRange(0, 0)
                return
            if dialog.maximum() == 0:
                dialog.setRange(0, 100)
            dialog.setValue(min(value, dialog.maximum()))
        except (AttributeError, RuntimeError):
            pass

    def prepare_update_files(self):
        """Collect local release inputs on the GUI thread and queue preparation."""
        if self._update_operation_active():
            return

        windows_root = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui,
            _("Select completed Windows build"),
        )
        if not windows_root:
            return

        output_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui,
            _("Select update output directory"),
        )
        if not output_dir:
            return

        release_notes, accepted = QtWidgets.QInputDialog.getMultiLineText(
            self.ui,
            _("Release notes"),
            _("Enter release notes:"),
        )
        if not accepted:
            return

        source_root = Path(getattr(self, "app_home", Path(__file__).resolve().parent))
        version = str(getattr(self, "version", ""))
        version_date = str(getattr(self, "version_date", ""))
        build_string = _release_build_identity(self, version_date)
        confirmation = QtWidgets.QMessageBox.question(
            self.ui,
            _("Prepare update files"),
            _("Version: %s\nBuild: %s\nDate: %s\nSource root: %s\n\n"
              "Create local Digi upload files now?")
            % (version, build_string, version_date, source_root),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            validate_release_roots(Path(windows_root), source_root)
        except Exception as exc:
            self.log.error("Release input validation failed:\n%s" % traceback.format_exc())
            self.inform.emit('[ERROR_NOTCL] %s' % _("Release inputs are incomplete: %s") % str(exc))
            return

        self._release_preparation_in_progress = True
        progress = QtWidgets.QProgressDialog(
            _("Preparing update files..."), "", 0, 100, self.ui
        )
        progress.setWindowTitle(_("Preparing update files"))
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        self._update_progress_dialog = progress
        progress.show()

        try:
            self.worker_task.emit({
                'fcn': self._prepare_releases_worker,
                'params': [Path(windows_root), source_root, Path(output_dir), str(release_notes)],
            })
        except Exception as exc:
            self._release_preparation_in_progress = False
            self._close_update_progress()
            self.log.error("Could not queue release preparation:\n%s" % traceback.format_exc())
            self.inform.emit('[ERROR_NOTCL] %s' % _("Release preparation could not be started."))

    def _prepare_releases_worker(self, windows_root, source_root, output_dir, release_notes):
        """Prepare both local release pairs off the GUI thread."""
        try:
            build_string = _release_build_identity(self, getattr(self, "version_date", ""))

            def progress(value, text):
                self.update_progress.emit(value, text)

            preparation = prepare_releases(
                {"windows": Path(windows_root), "linux": Path(source_root)},
                Path(output_dir),
                version=getattr(self, "version", ""),
                build_string=build_string,
                version_date=str(getattr(self, "version_date", "")),
                minimum_required_version="0",
                release_notes=release_notes,
                progress_cb=progress,
            )
            self.release_prepared.emit({
                "success": True,
                "output_dir": str(output_dir),
                "upload_paths": [str(path) for path in preparation.upload_paths],
            })
        except Exception as exc:
            self.log.error("Release preparation failed:\n%s" % traceback.format_exc())
            self.release_prepared.emit({
                "success": False,
                "output_dir": str(output_dir),
                "error": str(exc),
            })

    def on_release_prepared(self, data):
        """Finish local release preparation on the GUI thread."""
        self._release_preparation_in_progress = False
        self._close_update_progress()
        if not data.get("success"):
            self.log.error("Release preparation failed: %s" % data.get("error", "unknown error"))
            self.inform.emit(
                '[ERROR_NOTCL] %s' % _("Release preparation failed: %s") % data.get("error", "unknown error")
            )
            return

        paths = tuple(data.get("upload_paths", ()))
        self.inform.emit('[success] %s\n%s' % (_("Local Digi upload files are ready:"), "\n".join(paths)))
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(data["output_dir"])))

    def on_update_check_result(self, payload):
        self._check_in_progress = False
        if payload.get("status") == "up_to_date" and self._manual_update_requested:
            if payload.get("no_releases"):
                self.inform.emit('[WARNING_NOTCL] %s' % _("No releases have been published yet."))
            else:
                self.inform.emit('[success] %s' % _("The application is up to date!"))
        if payload.get("status") != "update_available":
            self._manual_update_requested = False

    def on_update_available(self, payload):
        if self._update_operation_active():
            self.log.info("Ignoring duplicate update offer while an update operation is active.")
            return

        manual_request = bool(getattr(self, "_manual_update_requested", False))
        self._manual_update_requested = False
        if payload.get("manual") and not manual_request:
            # Automatic checks must not nag about installs that are always replaced by hand.
            self.log.info("A manual update is available; automatic checks stay nonintrusive.")
            return

        dialog = UpdateDialog(parent=self.ui, payload=payload)
        self._update_dialog = dialog

        def finished(_result):
            self._update_dialog = None
            if dialog.choice == "later":
                return
            if dialog.choice == "exit":
                if self._updater_shutdown_preflight():
                    self.quit_application()
                return
            if payload.get("manual"):
                return

            self._start_update_download(payload)

        dialog.finished.connect(finished)
        dialog.open()

    def _start_update_download(self, payload):
        if self._update_operation_active():
            return
        self._update_in_progress = True
        cancel_event = threading.Event()
        self._update_cancel_event = cancel_event
        progress = QtWidgets.QProgressDialog(
            _("Downloading update..."), _("Cancel"), 0, 100, self.ui
        )
        progress.setWindowTitle(_("Downloading update"))
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else {}
        known_size = asset.get("size") if payload.get("install") == "windows_asset" else None
        if not known_size:
            # Source archives only report their total while streaming.
            progress.setRange(0, 0)
        progress.canceled.connect(cancel_event.set)
        self._update_progress_dialog = progress
        progress.show()

        try:
            self.worker_task.emit({
                'fcn': self._download_update_worker,
                'params': [payload, cancel_event],
            })
        except Exception as exc:
            self.log.error("Could not queue update download: %s" % str(exc))
            self._update_in_progress = False
            self._update_cancel_event = None
            self._close_update_progress()
            self.inform.emit('[ERROR_NOTCL] %s' % _("Update download could not be started."))

    def _download_update_worker(self, payload, cancel_event):
        # The offer records the provider and address that produced it; a later preference change must
        # not route its download through the other provider.
        provider = payload.get("provider") or update_provider(self)
        checker = checker_for(self)
        if getattr(checker, "provider", provider) != provider:
            checker = create_update_checker(self, provider=provider, url=payload.get("provider_url"))
        options = self.options
        defaults = self.defaults
        repository_url = payload.get("provider_url") or options.get(
            "global_update_url",
            defaults.get(
                "global_update_url",
                AppDefaults.factory_defaults.get("global_update_url"),
            ),
        )
        share_url = payload.get("provider_url") or options.get(
            "global_legacy_update_url",
            defaults.get(
                "global_legacy_update_url",
                AppDefaults.factory_defaults.get("global_legacy_update_url"),
            ),
        )

        def progress(written, total):
            if not total:
                self.update_progress.emit(-1, _("Downloading update..."))
                return
            value = min(int((written * 100) / total), 100)
            self.update_progress.emit(value, _("Downloading update: %s%%") % value)

        try:
            if provider == PROVIDER_DIGI:
                transport = checker.create_transport(self, share_url=share_url)
                archive_path = stage_update_archive(
                    payload,
                    self.data_path,
                    transport,
                    cancel_cb=cancel_event.is_set,
                    progress_cb=progress,
                )
                if cancel_event.is_set():
                    shutil.rmtree(str(archive_path.parent), ignore_errors=True)
                    self.update_staged.emit({"canceled": True, "payload": payload})
                    return
                self.update_staged.emit({
                    "canceled": False,
                    "payload": payload,
                    "staging_dir": str(archive_path.parent),
                    "archive_path": str(archive_path),
                    "manifest": payload["manifest"],
                    "channel": payload.get("channel"),
                })
                return

            transport = checker.create_transport(self, repository_url=repository_url)
            staged = stage_update(
                self,
                payload,
                transport=transport,
                cancel_cb=cancel_event.is_set,
                progress_cb=progress,
            )
            if cancel_event.is_set():
                shutil.rmtree(str(staged.staging_dir), ignore_errors=True)
                self.update_staged.emit({"canceled": True, "payload": payload})
                return
            self.update_staged.emit({
                "canceled": False,
                "payload": payload,
                "stage": staged,
                "staging_dir": str(staged.staging_dir),
                "archive_path": str(staged.archive_path),
                "manifest": staged.manifest,
                "channel": staged.channel,
                "install": staged.install,
                "trust": staged.trust,
                "dependencies": staged.dependencies,
            })
        except DownloadCancelled:
            self.update_staged.emit({"canceled": True, "payload": payload})
        except Exception as exc:
            self.log.error("Update download failed: %s" % str(exc))
            self.update_staged.emit({"canceled": False, "payload": payload, "error": str(exc)})

    def _cleanup_staged_update(self, data):
        staging_dir = data.get("staging_dir")
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    def _dependency_warning(dependencies):
        detail = []
        for label, key in (("Added", "added"), ("Removed", "removed")):
            values = dependencies.get(key) or []
            if values:
                detail.append("%s: %s" % (label, ", ".join(sorted(str(value) for value in values))))
        return "\n".join(detail)

    def _confirm_staged_update(self, manifest, channel, dependencies, trust):
        """Ask on the GUI thread before the external helper may touch the installation."""
        dependencies = dependencies if isinstance(dependencies, dict) else {}
        warning = ""
        if channel == "linux" and dependencies.get("changed"):
            warning = _(
                "The application requirements changed. Update the Python environment and "
                "restart afterwards; otherwise FlatCAM may fail to start. FlatCAM does not "
                "run pip or change the environment itself."
            )
            detail = self._dependency_warning(dependencies)
            if detail:
                warning = "%s\n%s" % (warning, detail)
        if not trust and not warning:
            return True

        version = manifest.get("version", "") if isinstance(manifest, dict) else getattr(manifest, "version", "")
        build = manifest.get("build", "") if isinstance(manifest, dict) else getattr(manifest, "build_string", "")
        message = ["%s (%s)" % (version, build)]
        if trust:
            message.append(_("Trust: %s") % trust)
        if warning:
            message.append(warning)
        default = (
            QtWidgets.QMessageBox.StandardButton.Cancel
            if warning
            else QtWidgets.QMessageBox.StandardButton.Yes
        )
        box = QtWidgets.QMessageBox(self.ui)
        box.setWindowTitle(_("Install update"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText("\n\n".join(message))
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(default)
        answer = box.exec()
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def on_update_staged(self, data):
        self._close_update_progress()
        self._update_in_progress = True
        launched = False
        try:
            cancel_event = getattr(self, "_update_cancel_event", None)
            if data.get("canceled") or (cancel_event is not None and cancel_event.is_set()):
                self._cleanup_staged_update(data)
                self.inform.emit('[WARNING_NOTCL] %s' % _("Update download canceled."))
                return

            stage = data.get("stage")
            manifest = data.get("manifest")
            archive_path = data.get("archive_path")
            payload = data.get("payload") or {}
            if manifest is None:
                manifest = payload.get("manifest")
            if archive_path is None and stage is not None:
                archive_path = str(stage.archive_path)
            if data.get("error") or not manifest or not archive_path:
                self._cleanup_staged_update(data)
                self.inform.emit('[ERROR_NOTCL] %s' % _("Update download failed; the installation was not changed."))
                return

            channel = data.get("channel") or getattr(stage, "channel", "")
            trust = data.get("trust") or getattr(stage, "trust", "")
            dependencies = data.get("dependencies") or getattr(stage, "dependencies", {})
            if not self._confirm_staged_update(manifest, channel, dependencies, trust):
                self._cleanup_staged_update(data)
                self.inform.emit('[WARNING_NOTCL] %s' % _("Update was canceled; the installation was not changed."))
                return
            if not self._updater_shutdown_preflight():
                self._cleanup_staged_update(data)
                return

            if not launch_update(self, manifest, archive_path):
                self._cleanup_staged_update(data)
                self.inform.emit('[WARNING_NOTCL] %s' % _("The updater could not be started."))
                return
            # The launcher owns a verified archive copy, so the download stage is disposable.
            self._cleanup_staged_update(data)
            launched = True
            self.quit_application(mode="updater")
        finally:
            self._update_cancel_event = None
            if not launched:
                self._update_in_progress = False

    def _load_rollback_restore_point(self):
        install_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        restore_dir = restore_dir_for_install(self.data_path, install_dir)
        metadata = load_restore_point(restore_dir, install_dir, verify_files=True)
        return restore_dir, metadata

    def refresh_rollback_action(self):
        action = getattr(getattr(self, "ui", None), "menuhelp_revert_update", None)
        if action is None:
            return
        if self._update_operation_active():
            action.setEnabled(False)
            return
        try:
            _restore, metadata = self._load_rollback_restore_point()
        except (OSError, TypeError, ValueError):
            action.setEnabled(False)
            return
        action.setEnabled(True)
        action.setToolTip(
            _("Revert to %s (%s)") %
            (metadata["previous_version"], metadata["previous_build_string"])
        )

    def on_revert_update(self):
        if self._update_operation_active():
            return
        try:
            _restore, metadata = self._load_rollback_restore_point()
        except (OSError, TypeError, ValueError):
            self.inform.emit('[WARNING_NOTCL] %s' % _("No usable previous version is available."))
            return

        answer = QtWidgets.QMessageBox.question(
            self.ui,
            _("Revert application update"),
            _("Revert from %s (%s) to %s (%s)?") % (
                metadata["version"], metadata["build_string"],
                metadata["previous_version"], metadata["previous_build_string"],
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes or not self._updater_shutdown_preflight():
            return

        self._rollback_in_progress = True

        def rollback_worker():
            try:
                success = launch_rollback(self)
                error = ""
            except Exception as exc:
                success = False
                error = str(exc)
            self.rollback_ready.emit({"success": success, "error": error})

        try:
            self.worker_task.emit({'fcn': rollback_worker, 'params': []})
        except Exception as exc:
            self._rollback_in_progress = False
            self.log.error("Could not queue rollback: %s" % str(exc))

    def on_rollback_ready(self, result):
        if not self._rollback_in_progress:
            return
        self._rollback_in_progress = False
        if not result.get("success", False):
            self.inform.emit('[WARNING_NOTCL] %s' % _("The previous version could not be started."))
            self.refresh_rollback_action()
            return
        self.quit_application(mode="updater")

    def final_save(self):
        """
        Callback for doing a preferences save to file whenever the application is about to quit.
        If the project has changes, it will ask the user to save the project.
        Directly call lifecycle.final_save() to avoid circular delegation.
        """
        self.lifecycle.final_save()

    def quit_application(self, silent=False, mode=None):
        if mode is None:
            self.lifecycle.quit_application(silent=silent)
        else:
            self.lifecycle.quit_application(silent=silent, mode=mode)

    @staticmethod
    def kill_app():
        AppLifecycle.kill_app()

    def on_portable_checked(self, state):
        self.lifecycle.on_portable_checked(state=state)

    def on_defaults_dict_change(self, field):
        self.lifecycle.on_defaults_dict_change(field=field)

    def on_deselect_all(self):
        self.object_ops.on_deselect_all()

    def on_workspace_modified(self):
        self.ui_actions.on_workspace_modified()

    def on_workspace(self):
        self.ui_actions.on_workspace()

    def on_workspace_toggle(self):
        self.ui_actions.on_workspace_toggle()

    def on_show_log(self):
        self.ui_actions.on_show_log()

    def on_cursor_type(self, val, control_cursor=True):
        self.ui_actions.on_cursor_type(val=val, control_cursor=control_cursor)

    def on_tool_add_keypress(self):
        self.ui_actions.on_tool_add_keypress()

    # It's meant to delete tools in tool tables via a 'Delete' shortcut key but only if certain conditions are met
    # See description below.
    def on_delete_keypress(self):
        self.object_ops.on_delete_keypress()

    # It's meant to delete selected objects. It may work also activated by a shortcut key 'Delete' same as above so in
    # some screens you have to be careful where you hover with your mouse.
    # Hovering over Selected tab, if the selected tab is a Geometry it will delete tools in tool table. But even if
    # there is a Selected tab in focus with a Geometry inside, if you hover over canvas it will delete an object.
    # Complicated, I know :)
    def on_delete(self, force_deletion=False):
        self.object_ops.on_delete(force_deletion=force_deletion)

    def delete_first_selected(self, del_obj=None):
        self.object_ops.delete_first_selected(del_obj=del_obj)

    def delete_objects_now(self, objects):
        self.object_ops.delete_objects_now(objects)

    def on_set_origin(self):
        self.object_ops.on_set_origin()

    def on_set_zero_click(self, event, location=None, noplot=False, use_thread=True):
        return self.object_ops.on_set_zero_click(
            event,
            location=location,
            noplot=noplot,
            use_thread=use_thread
        )

    def on_move2origin(self, use_thread=True):
        self.object_ops.on_move2origin(use_thread=use_thread)

    def on_jump_to(self, custom_location=None, fit_center=True):
        return self.object_ops.on_jump_to(custom_location=custom_location, fit_center=fit_center)

    def on_locate(self, obj, fit_center=True):
        return self.object_ops.on_locate(obj, fit_center=fit_center)

    def on_numeric_move(self, val=None):
        self.object_ops.on_numeric_move(val=val)

    def on_copy_command(self):
        self.object_ops.on_copy_command()

    def on_copy_object2(self, custom_name):
        return self.object_ops.on_copy_object2(custom_name)

    def on_rename_object(self, text):
        self.object_ops.on_rename_object(text)

    def abort_all_tasks(self):
        """
        Executed when a certain key combo is pressed (Ctrl+Alt+X). Will abort current task
        on the first possible occasion.

        :return:
        """
        interrupt = getattr(self, 'script_interrupt', None)
        if interrupt is not None:
            interrupt.cancel.set()
        if self.abort_flag is False:
            msg = "%s %s" % (_("Aborting."), _("The current task will be gracefully closed as soon as possible..."))
            self.inform.emit(msg)
            self.abort_flag = True
            self.cleanup.emit()  # noqa

    def app_is_idle(self):
        if self.abort_flag:
            self.inform.emit('[WARNING_NOTCL] %s' % _("The current task was gracefully closed on user request..."))
            self.abort_flag = False

    def on_selectall(self):
        self.object_ops.on_selectall()

    def on_toggle_preferences(self):
        """Facade: delegate to ui_actions."""
        self.ui_actions.on_toggle_preferences()

    def on_preferences(self):
        """Facade: delegate to ui_actions."""
        self.ui_actions.on_preferences()

    def on_tools_database(self, source='app'):
        """Facade: delegate to ui_actions."""
        return self.ui_actions.on_tools_database(source=source)

    def on_3d_area(self):
        return self.ui_actions.on_3d_area()

    def on_geometry_tool_add_from_db_executed(self, tool):
        return self.ui_actions.on_geometry_tool_add_from_db_executed(tool)

    def on_plot_area_tab_closed(self, tab_obj_name):
        self.ui_actions.on_plot_area_tab_closed(tab_obj_name)

    def on_plot_area_tab_double_clicked(self):
        self.ui_actions.on_plot_area_tab_double_clicked()

    def on_notebook_closed(self):
        self.ui_actions.on_notebook_closed()

    def on_gui_coords_clicked(self):
        self.distance_tool.run(toggle=True)

    def on_flipy(self):
        self.object_ops.on_flipy()

    def on_flipx(self):
        self.object_ops.on_flipx()

    def on_rotate(self, silent=False, preset=None):
        self.object_ops.on_rotate(silent=silent, preset=preset)

    def on_skewx(self):
        self.object_ops.on_skewx()

    def on_skewy(self):
        self.object_ops.on_skewy()

    def on_plots_updated(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.on_plots_updated()

    def on_toolbar_replot(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.on_toolbar_replot()

    def grid_status(self):
        return self.ui_actions.grid_status()

    def populate_cmenu_grids(self):
        self.ui_actions.populate_cmenu_grids()

    def set_grid(self):
        self.ui_actions.set_grid()

    def on_grid_add(self):
        self.ui_actions.on_grid_add()

    def on_grid_delete(self):
        self.ui_actions.on_grid_delete()

    def on_copy_name(self):
        self.object_ops.on_copy_name()

    def on_mouse_click_over_plot(self, event):
        self.canvas_events.on_mouse_click_over_plot(event)

    def on_mouse_double_click_over_plot(self, event):
        self.canvas_events.on_mouse_double_click_over_plot(event)

    def on_mouse_move_over_plot(self, event, origin_click=None):
        self.canvas_events.on_mouse_move_over_plot(event, origin_click)

    def on_mouse_click_release_over_plot(self, event):
        self.canvas_events.on_mouse_click_release_over_plot(event)

    def on_mouse_and_key_modifiers(self, position, modifiers):
        self.canvas_events.on_mouse_and_key_modifiers(position, modifiers)

    def on_mouse_context_menu(self):
        self.canvas_events.on_mouse_context_menu()

    @property
    def mouse_click_pos(self) -> list[float]:
        return [self._mouse_click_pos[0], self._mouse_click_pos[1]]

    @mouse_click_pos.setter
    def mouse_click_pos(self, m_pos: Union[list[float], tuple[float]]):
        self._mouse_click_pos = m_pos

    @property
    def mouse_pos(self) -> list[float]:
        return [self._mouse_pos[0], self._mouse_pos[1]]

    @mouse_pos.setter
    def mouse_pos(self, m_pos: Union[list[float], tuple[float]]):
        self._mouse_pos = m_pos

    def selection_area_handler(self, start_pos, end_pos, sel_type):
        self.canvas_events.selection_area_handler(start_pos, end_pos, sel_type)

    def select_objects(self, key=None):
        self.canvas_events.select_objects(key)

    def selected_message(self, curr_sel_obj):
        self.canvas_events.selected_message(curr_sel_obj)

    def on_plugin_mouse_click_release(self, pos):
        self.canvas_events.on_plugin_mouse_click_release(pos)

    def on_plugin_mouse_move(self, pos):
        self.canvas_events.on_plugin_mouse_move(pos)

    def delete_hover_shape(self):
        self.canvas_events.delete_hover_shape()

    def draw_hover_shape(self, sel_obj, color=None):
        self.canvas_events.draw_hover_shape(sel_obj, color)

    def delete_selection_shape(self):
        self.canvas_events.delete_selection_shape()

    def draw_selection_shape(self, sel_obj, color=None):
        self.canvas_events.draw_selection_shape(sel_obj, color)

    def draw_moving_selection_shape(self, old_coords, coords, **kwargs):
        self.canvas_events.draw_moving_selection_shape(old_coords, coords, **kwargs)

    def obj_properties(self):
        """
        Will launch the object Properties Tool

        :return:
        """
        sel_objs = self.collection.get_selected()
        if sel_objs:
            self.report_tool.run(toggle=True)
        else:
            # if the splitter is hidden, display it
            if self.ui.splitter.sizes()[0] == 0:
                self.ui.splitter.setSizes([1, 1])
            self.ui.notebook.setCurrentWidget(self.ui.properties_tab)

    def on_project_context_save(self):
        """
        Wrapper, will save the object function of it's type

        :return:
        """

        sel_objects = self.collection.get_selected()
        len_objects = len(sel_objects)

        cnt = 0
        if len_objects > 1:
            for o in sel_objects:
                if o.kind == 'cncjob':
                    cnt += 1

            if len_objects == cnt:
                # all selected objects are of type CNCJOB therefore we issue a multiple save
                cncjob_filters = self.options.get('cncjob_save_filters', self.defaults.get('cncjob_save_filters'))
                _filter_ = cncjob_filters + \
                           ";;RML1 Files .rol (*.rol);;HPGL Files .plt (*.plt);;KNC Files .knc (*.knc)"

                dir_file_to_save = self.get_last_save_folder() + '/multi_save'

                try:
                    filename, _f = FCFileSaveDialog.get_saved_filename(
                        caption=_("Export Code ..."),
                        directory=dir_file_to_save,
                        ext_filter=_filter_
                    )
                except TypeError:
                    filename, _f = FCFileSaveDialog.get_saved_filename(
                        caption=_("Export Code ..."),
                        ext_filter=_filter_)

                if not filename:
                    return

                path = filename.rpartition('/')[0]
                file_extension = filename.rpartition('.')[2]

                used_names = set()
                for ob in sel_objects:
                    ob.read_form()
                    # the name can come from a project file ('..\\..\\job'): only a file name in the chosen folder
                    file_name = unique_name(safe_file_name(ob.obj_options['name'], 'cncjob'), used_names)
                    used_names.add(file_name)
                    fname = os.path.join(path, '%s.%s' % (file_name, file_extension))
                    ob.export_gcode_handler(fname, is_gcode=True, rename_object=False)
                return

        obj = self.collection.get_active()
        if isinstance(obj, GeometryObject):
            self.f_handlers.on_file_export_dxf()
        elif isinstance(obj, ExcellonObject):
            self.f_handlers.on_file_save_excellon()
        elif isinstance(obj, CNCJobObject):
            obj.on_exportgcode_button_click()
        elif isinstance(obj, GerberObject):
            self.f_handlers.on_file_save_gerber()
        elif isinstance(obj, ScriptObject):
            self.f_handlers.on_file_save_script()
        elif isinstance(obj, DocumentObject):
            self.f_handlers.on_file_save_document()

    def obj_move(self):
        """
        Callback for the Move menu entry in various Context Menu's.

        :return:
        """

        self.defaults.report_usage("obj_move()")
        self.move_tool.run(toggle=False)

    # ###############################################################################################################
    # ### The following section has the functions that are displayed and call the Editor tab CNCJob Tab #############
    # ###############################################################################################################
    def init_code_editor(self, name):

        self.text_editor_tab = AppTextEditor(app=self, plain_text=True)

        # add the tab if it was closed
        self.ui.plot_tab_area.addTab(self.text_editor_tab, '%s' % name)
        self.text_editor_tab.setObjectName('text_editor_tab')

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")
        # hide coordinates toolbars in the infobar while in DB
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        self.toggle_codeeditor = True
        self.text_editor_tab.code_editor.completer_enable = False
        self.text_editor_tab.buttonRun.hide()

        # make sure to keep a reference to the code editor
        self.reference_code_editor = self.text_editor_tab.code_editor

        # Switch plot_area to CNCJob tab
        self.ui.plot_tab_area.setCurrentWidget(self.text_editor_tab)

    def on_view_source(self):
        """
        Called when the user wants to see the source file of the selected object
        :return:
        """

        try:
            obj = self.collection.get_active()
        except Exception as e:
            self.log.debug("App.on_view_source() --> %s" % str(e))
            self.inform.emit('[WARNING_NOTCL] %s' % _("Select an Gerber or Excellon file to view it's source file."))
            return 'fail'

        if obj is None:
            self.inform.emit('[WARNING_NOTCL] %s' % _("Select an Gerber or Excellon file to view it's source file."))
            return 'fail'

        # checked before the Source Editor tab is added: for an object without a source (e.g. a Geometry made by a
        # tool) the tab stayed empty and the app stayed busy
        if not self.collection.has_source(obj):
            self.inform.emit('[WARNING_NOTCL] %s: %s' % (_("The object has no source file to show"),
                                                         obj.obj_options['name']))
            return 'fail'

        self.inform.emit('%s' % _("Viewing the source code of the selected object."))
        self.proc_container.view.set_busy('%s...' % _("Loading"))

        flt = "All Files (*.*)"
        if obj.kind == 'gerber':
            flt = "Gerber Files .gbr (*.GBR);;PDF Files .pdf (*.PDF);;All Files (*.*)"
        elif obj.kind == 'excellon':
            flt = "Excellon Files .drl (*.DRL);;PDF Files .pdf (*.PDF);;All Files (*.*)"
        elif obj.kind == 'cncjob':
            flt = "GCode Files .nc (*.NC);;PDF Files .pdf (*.PDF);;All Files (*.*)"

        self.source_editor_tab = AppTextEditor(app=self, plain_text=True)

        # add the tab if it was closed
        self.ui.plot_tab_area.addTab(self.source_editor_tab, '%s' % _("Source Editor"))
        self.source_editor_tab.setObjectName('source_editor_tab')

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")
        # hide coordinates toolbars in the infobar while in DB
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        self.source_editor_tab.code_editor.completer_enable = False
        self.source_editor_tab.buttonRun.hide()

        # Switch plot_area to CNCJob tab
        self.ui.plot_tab_area.setCurrentWidget(self.source_editor_tab)

        try:
            self.source_editor_tab.buttonOpen.clicked.disconnect()
        except TypeError:
            pass
        self.source_editor_tab.buttonOpen.clicked.connect(lambda: self.source_editor_tab.handleOpen(filt=flt))

        try:
            self.source_editor_tab.buttonSave.clicked.disconnect()
        except TypeError:
            pass
        self.source_editor_tab.buttonSave.clicked.connect(lambda: self.source_editor_tab.handleSaveGCode(filt=flt))

        # then append the text from GCode to the text editor
        if obj.kind == 'cncjob':
            try:
                file = obj.export_gcode(to_file=True)
                if file == 'fail':
                    return 'fail'
            except AttributeError:
                self.inform.emit('[WARNING_NOTCL] %s' %
                                 _("There is no selected object for which to see it's source file code."))
                return 'fail'
        else:
            try:
                file = StringIO(obj.source_file)
            except (AttributeError, TypeError):
                self.inform.emit('[WARNING_NOTCL] %s' %
                                 _("There is no selected object for which to see it's source file code."))
                return 'fail'

        self.source_editor_tab.t_frame.hide()
        try:
            source_text = file.getvalue()
            self.source_editor_tab.load_text(source_text, clear_text=True, move_to_start=True)
        except Exception as e:
            self.log.error('App.on_view_source() -->%s' % str(e))
            self.inform.emit('[ERROR] %s: %s' % (_('Failed to load the source code for the selected object'), str(e)))
            return

        self.source_editor_tab.t_frame.show()
        self.proc_container.view.set_idle()
        # self.ui.show()

    def on_toggle_code_editor(self):
        self.defaults.report_usage("on_toggle_code_editor()")

        if self.toggle_codeeditor is False:
            self.init_code_editor(name=_("Code Editor"))

            try:
                self.text_editor_tab.buttonOpen.clicked.disconnect()
            except TypeError:
                pass
            self.text_editor_tab.buttonOpen.clicked.connect(self.text_editor_tab.handleOpen)
            try:
                self.text_editor_tab.buttonSave.clicked.disconnect()
            except TypeError:
                pass
            self.text_editor_tab.buttonSave.clicked.connect(self.text_editor_tab.handleSaveGCode)
        else:
            for idx in range(self.ui.plot_tab_area.count()):
                if self.ui.plot_tab_area.widget(idx).objectName() == "text_editor_tab":
                    self.ui.plot_tab_area.closeTab(idx)
                    break
            self.toggle_codeeditor = False

    def on_code_editor_close(self):
        self.toggle_codeeditor = False

    def plot_all(self, fit_view=True, muted=False, use_thread=True):
        """Facade: delegate to plot_manager."""
        self.plot_manager.plot_all(fit_view=fit_view, muted=muted, use_thread=use_thread)

    def register_folder(self, filename):
        """
        Register the last folder used by the app to open something

        :param filename:    the last folder is extracted from the filename
        :return:            None
        """
        self.options["global_last_folder"] = os.path.split(str(filename))[0]

    def register_save_folder(self, filename):
        """
        Register the last folder used by the app to save something

        :param filename:    the last folder is extracted from the filename
        :return:            None
        """
        self.options["global_last_save_folder"] = os.path.split(str(filename))[0]

    # def set_progress_bar(self, percentage, text=""):
    #     """
    #     Set a progress bar to a value (percentage)
    #
    #     :param percentage:  Value set to the progressbar
    #     :param text:        Not used
    #     :return:            None
    #     """
    #     self.ui.progress_bar.setValue(int(percentage))

    def setup_recent_items(self):
        """
        Set up a dictionary with the recent files accessed, organized by type

        :return:
        """
        icons = {
            "gerber": self.resource_location + "/flatcam_icon16.png",
            "pcbdoc": self.resource_location + "/flatcam_icon16.png",
            "proteus": self.resource_location + "/flatcam_icon16.png",
            "kicad_pcb": self.resource_location + "/flatcam_icon16.png",
            "sprint_lay6": self.resource_location + "/flatcam_icon16.png",
            "eagle_brd": self.resource_location + "/flatcam_icon16.png",
            "allegro_brd": self.resource_location + "/flatcam_icon16.png",
            "excellon": self.resource_location + "/drill16.png",
            'geometry': self.resource_location + "/geometry16.png",
            "cncjob": self.resource_location + "/cnc16.png",
            "script": self.resource_location + "/script_new24.png",
            "document": self.resource_location + "/notes16_1.png",
            "project": self.resource_location + "/project16.png",
            "svg": self.resource_location + "/geometry16.png",
            "dxf": self.resource_location + "/dxf16.png",
            "pdf": self.resource_location + "/pdf32.png",
            "image": self.resource_location + "/image16.png"

        }

        try:
            image_opener = self.image_tool.import_image
        except AttributeError:
            def image_opener_fallback(filename):  # noqa
                self.inform.emit(
                    '[WARNING] %s' % _("Cannot open image file. Image tool is not available.")
                )

            image_opener = image_opener_fallback

        openers = {
            'pcbdoc': self.f_handlers.on_file_import_pcbdoc,
            'proteus': self.f_handlers.on_file_import_proteus,
            'kicad_pcb': self.f_handlers.on_file_import_kicad_pcb,
            'sprint_lay6': self.f_handlers.on_file_import_sprint_layout,
            'eagle_brd': self.f_handlers.on_file_import_eagle_brd,
            'allegro_brd': self.f_handlers.on_file_import_allegro_brd,
            'gerber': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.open_gerber,
                'params': [fname]
            }),
            'excellon': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.open_excellon,
                'params': [fname]
            }),
            'geometry': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.import_dxf,
                'params': [fname]
            }),
            'cncjob': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.open_gcode,
                'params': [fname]
            }),
            "script": lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.open_script,
                'params': [fname]
            }),
            "document": None,
            'project': self.f_handlers.open_project,
            'svg': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.import_svg,
                'params': [fname]
            }),
            'dxf': lambda fname: self.worker_task.emit({
                'fcn': self.f_handlers.import_dxf,
                'params': [fname]
            }),
            'image': lambda fname: self.worker_task.emit({
                'fcn': image_opener,
                'params': [fname]
            }),
            'pdf': self.f_handlers.import_pdf
        }

        # Open recent file for files
        try:
            with open(os.path.join(self.data_path, 'recent.json')) as f:
                try:
                    self.recent = json.load(f)
                except json.JSONDecodeError:
                    self.log.error("Failed to parse recent item list.")
                    self.inform.emit('[ERROR_NOTCL] %s' % _("Failed to parse recent item list."))
                    return
        except IOError:
            self.log.error("Failed to load recent item list.")
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed to load recent item list."))
            return

        # Open recent file for projects
        try:
            with open(os.path.join(self.data_path, 'recent_projects.json')) as fp:
                try:
                    self.recent_projects = json.load(fp)
                except json.JSONDecodeError:
                    self.log.error("Failed to parse recent project item list.")
                    self.inform.emit('[ERROR_NOTCL] %s' % _("Failed to parse recent project item list."))
                    return
        except IOError:
            self.log.error("Failed to load recent project item list.")
            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed to load recent projects item list."))
            return

        # Closure needed to create callbacks in a loop.
        # Otherwise, late binding occurs.
        def make_callback(func, fname):
            def opener():
                func(fname)

            return opener

        def reset_recent_files():
            # Reset menu
            self.ui.recent.clear()
            self.recent = []
            try:
                with open(os.path.join(self.data_path, 'recent.json'), 'w') as ff:
                    json.dump(self.recent, ff)
            except IOError:
                self.log.error("Failed to open recent items file for writing.")
                return

            self.inform.emit('%s' % _("Recent files list was reset."))

        def reset_recent_projects():
            # Reset menu
            self.ui.recent_projects.clear()
            self.recent_projects = []

            try:
                with open(os.path.join(self.data_path, 'recent_projects.json'), 'w') as frp:
                    json.dump(self.recent_projects, frp)
            except IOError:
                self.log.error("Failed to open recent projects items file for writing.")
                return
            self.inform.emit('%s' % _("Recent projects list was reset."))

        # Reset menu
        self.ui.recent.clear()
        self.ui.recent_projects.clear()

        # Create menu items for projects
        for recent in self.recent_projects:
            filename = recent['filename'].split('/')[-1].split('\\')[-1]

            if recent['kind'] == 'project':
                try:
                    action = QtGui.QAction(QtGui.QIcon(icons[recent["kind"]]), filename, self)

                    # Attach callback
                    o = make_callback(openers[recent["kind"]], recent['filename'])
                    action.triggered.connect(o)

                    self.ui.recent_projects.addAction(action)

                except KeyError:
                    self.log.error("Unsupported file type: %s" % recent["kind"])

        # Last action in Recent Files menu is one that Clear the content
        clear_action_proj = QtGui.QAction(QtGui.QIcon(self.resource_location + '/trash32.png'),
                                          (_("Clear Recent projects")), self)
        clear_action_proj.triggered.connect(reset_recent_projects)
        self.ui.recent_projects.addSeparator()
        self.ui.recent_projects.addAction(clear_action_proj)

        # Create menu items for files
        for recent in self.recent:
            filename = recent['filename'].split('/')[-1].split('\\')[-1]

            if recent['kind'] != 'project':
                try:
                    action = QtGui.QAction(QtGui.QIcon(icons[recent["kind"]]), filename, self)

                    # Attach callback
                    o = make_callback(openers[recent["kind"]], recent['filename'])
                    action.triggered.connect(o)

                    self.ui.recent.addAction(action)

                except KeyError:
                    self.log.error("Unsupported file type: %s" % recent["kind"])

        # Last action in Recent Files menu is one that Clear the content
        clear_action = QtGui.QAction(QtGui.QIcon(self.resource_location + '/trash32.png'),
                                     (_("Clear Recent files")), self)
        clear_action.triggered.connect(reset_recent_files)
        self.ui.recent.addSeparator()
        self.ui.recent.addAction(clear_action)

        # self.builder.get_object('open_recent').set_submenu(recent_menu)
        # self.ui.menufilerecent.set_submenu(recent_menu)
        # recent_menu.show_all()
        # self.ui.recent.show()

        self.log.debug("Recent items list has been populated.")

    def on_properties_tab_click(self):
        self.ui_actions.on_properties_tab_click()

    def on_notebook_tab_changed(self):
        self.ui_actions.on_notebook_tab_changed()

    def setup_default_properties_tab(self):
        self.ui_actions.setup_default_properties_tab()

    def setup_obj_classes(self):
        self.lifecycle.setup_obj_classes()

    def version_check(self, forced=False):
        if not hasattr(self, "_check_in_progress"):
            if forced:
                self.lifecycle.version_check(forced=True)
            else:
                self.lifecycle.version_check()
            return None
        if self._check_in_progress:
            return None
        self._check_in_progress = True
        self._manual_update_requested = bool(forced)
        try:
            if forced:
                self.lifecycle.version_check(forced=True)
            else:
                self.lifecycle.version_check()
            return None
        except Exception as exc:
            self._check_in_progress = False
            self._manual_update_requested = False
            self.log.error("Update check failed safely: %s" % str(exc))
            self.inform.emit('[WARNING_NOTCL] %s' % _("Failed checking for the latest version."))
            return None

    def on_plotcanvas_setup(self):
        """
        This is doing the setup for the plot area (canvas).

        :return:            None
        """

        if self.headless:
            self.log.debug("Setting up canvas: none (headless)")
            plotcanvas = NullCanvas(self)
            self.app_cursor = plotcanvas.new_cursor()
            return plotcanvas

        self.log.debug("Setting up canvas: VisPy")

        try:
            plotcanvas = PlotCanvas(self)
        except Exception as er:
            msg_txt = traceback.format_exc()
            self.log.error("App.on_plotcanvas_setup() failed -> %s" % str(er))
            self.log.error("OpenGL canvas initialization failed with the following error.\n" + msg_txt)
            msg = _("OpenGL canvas initialization failed.\n"
                    "Check your graphics drivers and OpenGL support, including remote desktop or VM graphics settings.\n"
                    "Technical details are available in the application log and error details.")
            self.log.error(msg)
            msgbox = FCMessageBox(parent=self.ui)
            msgbox.setWindowTitle(_("OpenGL initialization failed"))
            msgbox.setInformativeText(msg)
            msgbox.setDetailedText(msg_txt)
            msgbox.exec()
            return 'fail'

        # So it can receive key presses
        plotcanvas.native.setFocus()

        pan_button = 2 if self.options.get("global_pan_button", self.defaults.get("global_pan_button")) == '2' else 3
        # Set the mouse button for panning
        plotcanvas.view.camera.pan_button_setting = pan_button

        plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move_over_plot)
        plotcanvas.graph_event_connect('mouse_press', self.on_mouse_click_over_plot)
        plotcanvas.graph_event_connect('mouse_release', self.on_mouse_click_release_over_plot)
        plotcanvas.graph_event_connect('mouse_double_click', self.on_mouse_double_click_over_plot)

        # Keys over plot enabled
        plotcanvas.graph_event_connect('key_press', self.ui.keyPressEvent)

        if self.options.get('global_cursor_type', self.defaults.get('global_cursor_type')) == 'small':
            self.app_cursor = plotcanvas.new_cursor()
        else:
            self.app_cursor = plotcanvas.new_cursor(big=True)

        if self.ui.grid_snap_btn.isChecked():
            self.app_cursor.enabled = True
        else:
            self.app_cursor.enabled = False

        return plotcanvas

    @staticmethod
    def on_plotcanvas_add(plotcanvas_obj, container):
        """

        :param plotcanvas_obj:  the class that set up the canvas
        :type plotcanvas_obj:   class
        :param container:       a layout where to add the native widget of the plotcanvas_obj class
        :type container:
        :return:                Nothing
        :rtype:                 None
        """
        container.addWidget(plotcanvas_obj.native)

    def on_zoom_fit(self):
        """
        Callback for zoom-fit request. This can be either from the corresponding
        toolbar button or the '1' key when the canvas is focused. Calls ``self.plotcanvas.fit_view()``
        to fit the view to the geometry bounds of all objects.

        :return:        None
        """
        self.plotcanvas.fit_view()

    def on_zoom_in(self):
        """
        Callback for zoom-in request.
        :return:
        """
        self.plotcanvas.zoom(1 / float(self.options.get('global_zoom_ratio', self.defaults.get('global_zoom_ratio'))))

    def on_zoom_out(self):
        """
        Callback for zoom-out request.

        :return:
        """
        self.plotcanvas.zoom(float(self.options.get('global_zoom_ratio', self.defaults.get('global_zoom_ratio'))))

    def disable_all_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.disable_all_plots()

    def disable_other_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.disable_other_plots()

    def enable_all_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.enable_all_plots()

    def enable_other_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.enable_other_plots()

    def on_enable_sel_plots(self, silent=False):
        """Facade: delegate to plot_manager."""
        self.plot_manager.on_enable_sel_plots(silent=silent)

    def on_disable_sel_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.on_disable_sel_plots()

    def enable_plots(self, objects, silent=False):
        """Facade: delegate to plot_manager."""
        self.plot_manager.enable_plots(objects=objects, silent=silent)

    def disable_plots(self, objects):
        """Facade: delegate to plot_manager."""
        self.plot_manager.disable_plots(objects=objects)

    def toggle_plots(self, objects):
        """Facade: delegate to plot_manager."""
        self.plot_manager.toggle_plots(objects=objects)

    def clear_plots(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.clear_plots()

    def gerber_redraw(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.gerber_redraw()

    def on_set_color_action_triggered(self):
        """Facade: delegate to plot_manager."""
        self.plot_manager.on_set_color_action_triggered()

    def set_obj_color_in_preferences_dict(self, list_of_obj, fill_color, outline_color):
        """Facade: delegate to plot_manager."""
        self.plot_manager.set_obj_color_in_preferences_dict(list_of_obj, fill_color, outline_color)

    def start_delayed_quit(self, delay, filename, should_quit=None):
        self.lifecycle.start_delayed_quit(delay=delay, filename=filename, should_quit=should_quit)

    def check_project_file_size(self, filename, should_quit=None):
        self.lifecycle.check_project_file_size(filename=filename, should_quit=should_quit)

    def on_defaults2options(self):
        self.lifecycle.on_defaults2options()

    def shell_message(self, msg, show=False, error=False, warning=False, success=False, selected=False, new_line=True):
        """
        Shows a message on the FlatCAM Shell

        :param new_line:
        :param msg:         Message to display.
        :param show:        Opens the shell.
        :param error:       Shows the message as an error.
        :param warning:     Shows the message as a warning.
        :param success:     Shows the message as a success.
        :param selected:    Indicate that something was selected on canvas
        :return: None
        """
        end = '\n' if new_line is True else ''

        if show:
            self.ui.shell_dock.show()
        try:
            if error:
                self.shell.append_error(msg + end)
            elif warning:
                self.shell.append_warning(msg + end)
            elif success:
                self.shell.append_success(msg + end)
            elif selected:
                self.shell.append_selected(msg + end)
            else:
                self.shell.append_output(msg + end)
        except AttributeError:
            self.log.debug(
                "shell_message() is called before Shell Class is instantiated. The message is: %s" % str(msg))

    def script_processing(self, script_code, origin='', owner=None):
        """Evaluate one unchanged script on the Tcl/GUI thread, never retry a failed prefix."""
        collection = getattr(owner, '_script_collection', None)
        if collection is not None and collection is not getattr(self, 'collection', None):
            self.inform.emit('[ERROR_NOTCL] %s' % _("Activate the script's project before running it."))
            return 'fail'
        if getattr(self, 'script_state', '') == 'running':
            self.inform.emit('[ERROR_NOTCL] %s' % _("A script is already running."))
            return 'fail'
        pending = getattr(self, 'script_pending_work', None)
        if pending is not None:
            pending[:] = [state for state in pending if not state['finished']]
            if pending:
                self.script_state = 'timed_out_work'
                self.inform.emit('[ERROR_NOTCL] %s' % _("Timed-out script work is still running; changes are not rolled back."))
                return 'fail'
        if self.ui.shell_dock.isHidden():
            self.ui.shell_dock.show()
        tcl = self.shell.tcl
        self.script_state = 'running'
        self.script_cancelled = False
        self.script_error_line = None
        self.script_owner = owner
        self.script_project = getattr(self, 'collection', None)
        self.shell.open_processing()
        previous_origin = tcl.call('info', 'script')
        flush_output = getattr(self.shell, 'flush_tcl_output', lambda: None)
        details = ''
        result_var = '::flatcam_script_result'
        options_var = '::flatcam_script_options'
        try:
            if not tcl.getboolean(tcl.call('info', 'complete', script_code)):
                raise tk.TclError('Incomplete Tcl script; no commands were executed.')
            tcl.call('info', 'script', origin or '')
            code = catch_tcl(self, tcl, script_code, result_var, options_var)
            result = tcl.eval('set ::flatcam_script_result')
            options = tcl.getvar(options_var)
            code = completion_code(tcl, code, options)
            if getattr(self, 'script_cancelled', False):
                details = 'Script cancelled; completed changes are not rolled back.'
                raise tk.TclError(details)
            if code != 0:
                if code == 1:
                    if tcl.getboolean(tcl.call('dict', 'exists', options, '-errorline')):
                        self.script_error_line = int(tcl.call('dict', 'get', options, '-errorline'))
                    if tcl.getboolean(tcl.call('dict', 'exists', options, '-errorinfo')):
                        details = str(tcl.call('dict', 'get', options, '-errorinfo'))
                else:
                    details = 'Unexpected Tcl completion code: %s' % code
                raise tk.TclError(result or details)
            flush_output()
            if result not in ('', 'None'):
                self.shell.append_output(result + '\n')
            self.script_state = 'completed'
            return result
        except tk.TclError as error:
            flush_output()
            pending = getattr(self, 'script_pending_work', None)
            self.script_state = ('timed_out_work' if pending and any(not s['finished'] for s in pending)
                                 else 'cancelled' if getattr(self, 'script_cancelled', False) else 'failed')
            details = details or str(error)
            location = origin or '<script>'
            if self.script_error_line is not None:
                location += ':%s' % self.script_error_line
            message = '%s: %s' % (location, details)
            self.log.error(message)
            self.shell.append_error('ERROR: %s\n' % message)
            return 'fail'
        except Exception:
            self.script_state = 'failed'
            raise
        finally:
            flush_output()
            tcl.call('unset', '-nocomplain', result_var, options_var)
            tcl.call('info', 'script', previous_origin)
            self.script_owner = None
            self.script_project = None
            self.shell.close_processing()

    def dec_format(self, val, dec=None):
        """
        Returns a formatted float value with a certain number of decimals
        """
        dec_nr = dec if dec is not None else self.decimals

        return float('%.*f' % (dec_nr, float(val)))


# end of file
