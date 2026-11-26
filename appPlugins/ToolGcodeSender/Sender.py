# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import collections
import dataclasses
import logging
import ipaddress
import math
import operator
import os
import random
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import QtWidgets, QtCore, QtGui, sip

import serial
import serial.tools.list_ports

from appTool import AppTool
from appCommon import MachineSafety
from defaults import AppDefaults
from appGUI.GUIElements import (
    VerticalScrollArea, FCFileSaveDialog, FCMessageBox, RadioSet, FCSpinner)
from appEditors.text_editor.code_edit import CodeEdit
from appGUI.machine_profile_ui import report_gate
from camlib import CNCjob, flatten_shapely_geometry

from appPlugins.Common.levelling_core import LevellingCoreMixin
from appPlugins.Common.grbl.grbl_protocol import (
    parse_grbl_status_state, parse_grbl_setting, parse_grbl_active_wcs, grbl_reply_error, probe_timeout_s,
    parse_grbl_status_report, work_position, grbl_report_scale, grbl_modes_confirmed,
    parse_grbl_probe_output, parse_grbl_offsets, grbl_wcs_p_number)
from appPlugins.Common.gcode_segment import split_long_moves, parse_toolpath, _words, _g_key
from appPlugins.Common.levelling_interp import build_bilinear_grid
from appPlugins.ToolGcodeSender import SenderGen, SenderKeys, SenderEstimate, SenderMacros
from appPlugins.Common.grbl.grbl_client import (
    GrblClientMixin, STATUS_POLL, IO_STATUS, IO_STATUS_PERIOD, MONITOR_OPS, is_controller_banner, is_grblhal_banner, is_native_jog_banner,
    stream_lines, validate_stream_lines, _GrblProbeAbort, GRBL_RX_BUFFER, GRBL_MAX_LINE)
from appPlugins.Common.grbl.grbl_protocol import parse_grbl_identity, grbl_capabilities
from appPlugins.Common.grbl.grbl_messages import code_text
from appCommon.machine_profile.correct import CorrectionRefused
from appCommon.machine_profile.facts import active_profile_of
from appCommon.machine_profile.output import (
    calibration_hash, check_probe_moves, correction_matrix, has_reject, map_point, prepare_output,
    setup_from_controller, setup_from_profile)
from appCommon.machine_profile.records import CheckStatus, Purpose, ViolationKind
from appCommon.machine_profile.writer import FileGate, gate_file_text
from appPlugins.ToolGcodeSender.SenderDock import CommandHistory, GrblConfiguration, GrblConsole
from appPlugins.ToolGcodeSender.SenderIO import SenderIOController
from appPlugins.ToolGcodeSender.SenderCameraPane import CameraPane
from appPlugins.ToolGcodeSender.SenderUI import GcodeSenderUI
from appPlugins.ToolGcodeSender.SenderSimulation import SimulationDialog, VirtualGrbl
from appPlugins.ToolGcodeSender.SenderView3d import SenderView3d, TOOL_COLOR
from appPlugins.ToolGcodeSender.CameraCalibration import (
    CameraCalibration, CAMERA_NONE, CAMERA_STORED, CAMERA_STALE, CAMERA_Z_MISMATCH)
from appPlugins.ToolGcodeSender.ToolChange import (
    plate_mode, ToolChangeSettings, ToolChangeMeasurement, probe_target, check_contact,
    PLATE_NONE, PLATE_FIXED, PLATE_REMOVABLE,
    STATE_OFF, STATE_NO_REFERENCE, STATE_REFERENCE, STATE_MEASURED, STATE_NEEDS_REMOVAL, STATE_READY)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

# The Sender is not finished or verified: it can move a machine in an unsafe way. run() opens it only when
# the environment variable FLATCAM_SENDER_DEV is 1 (development work). The test suite sets the variable.
SENDER_DEV_ENV = 'FLATCAM_SENDER_DEV'


def sender_enabled():
    """:return: True if the G-code Sender can open (development switch FLATCAM_SENDER_DEV=1)."""
    return os.environ.get(SENDER_DEV_ENV) == '1'


SENDER_ACTIONS = (
    'connect', 'start', 'hold', 'resume', 'next_block', 'stop', 'estop', 'reset', 'unlock', 'home', 'jog', 'zero',
    'wcs', 'spindle', 'spindle_off', 'override', 'rapid_override', 'console', 'macro', 'probe', 'edit', 'config')

# The machine situations in order: the first row that matches is used (see ToolGCodeSender._machine_situation).
# Row = (situation, allowed actions). Monitor operations (MONITOR_OPS) are no operation for these rules.
ACTION_RULES = (
    ('not_connected', ('connect', 'estop')),
    ('needs_reset', ('reset', 'stop', 'estop', 'spindle_off')),
    ('needs_home', ('home', 'reset', 'unlock', 'estop', 'spindle_off')),
    ('alarm', ('unlock', 'home', 'reset', 'stop', 'estop', 'console', 'spindle_off')),
    ('operation', ('stop', 'estop')),
    ('tool_pause', ('resume', 'stop', 'estop', 'reset', 'jog', 'probe', 'zero', 'console', 'spindle', 'macro',
                    'home', 'spindle_off')),
    ('stream_check', ('stop', 'estop')),
    ('stream_hold', ('resume', 'stop', 'estop', 'override', 'rapid_override')),
    ('stream_tool', ('jog', 'console', 'resume', 'stop', 'estop')),
    ('stream_step', ('next_block', 'hold', 'stop', 'estop', 'override', 'rapid_override')),
    ('stream', ('hold', 'stop', 'estop', 'override', 'rapid_override')),
    ('held', ('resume', 'stop', 'estop', 'reset', 'spindle_off', 'console')),
    ('idle', tuple(name for name in SENDER_ACTIONS if name not in ('resume', 'next_block', 'hold'))),
)

# The operator widgets of SenderUI (dotted attribute paths) and the action that enables each of them.
# The real-time bytes of the Rapid override buttons: they take the action 'rapid_override', the others 'override'.
RAPID_OVERRIDE_BYTES = (b'\x95', b'\x96', b'\x97')

# The override buttons (ui.ov_buttons) take the action 'override'; ui.jog_wdg takes 'jog' (see _apply_machine_state).
WIDGET_ACTIONS = {
    'start_button': 'start', 'hold_button': 'hold', 'resume_button': 'resume',
    'stop_button': 'stop', 'jog_stop_button': 'stop', 'stop_probing_button': 'stop',
    'estop_button': 'estop', 'estop_dock_button': 'estop',
    'controller_reset_button': 'reset', 'unlock_button': 'unlock',
    'zero_axs_wdg.grbl_homing_button': 'home',
    'jog_step_entry': 'jog', 'jog_fr_entry': 'jog', 'jog_presets_wdg': 'jog',
    'jog_wdg.jog_up_left_button': 'jog', 'jog_wdg.jog_up_right_button': 'jog',
    'jog_wdg.jog_down_left_button': 'jog', 'jog_wdg.jog_down_right_button': 'jog',
    'zero_axs_wdg.grbl_zerox_button': 'zero', 'zero_axs_wdg.grbl_zeroy_button': 'zero',
    'zero_axs_wdg.grbl_zeroz_button': 'zero', 'zero_axs_wdg.grbl_zero_all_button': 'zero',
    'spindle_button': 'spindle',
    'grbl_command_entry': 'console', 'grbl_send_button': 'console',
    'edge_probe_button': 'probe', 'edge_center_button': 'probe',
    'config_tab': 'config', 'wcs_combo': 'wcs',
}


def _qt_deleted(obj):
    """:return: True if obj is a Qt object that Qt has deleted (False for None and for other objects)."""
    if obj is None:
        return False
    try:
        return sip.isdeleted(obj)
    except TypeError:  # not a Qt object
        return False


class GrblStartDialog(QtWidgets.QDialog):
    """Choose a 1-based line in the prepared console program."""

    def __init__(self, line_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Start job"))
        layout = QtWidgets.QVBoxLayout(self)
        self.start_radio = RadioSet([
            {'label': _("Beginning"), 'value': 'beginning'},
            {'label': _("Line"), 'value': 'line'}])
        self.start_radio.setToolTip(_(
            "Select from where the job starts:\n"
            "- Beginning: start from the first line\n"
            "- Line: start from a custom line"))
        layout.addWidget(self.start_radio)
        self.line_entry = FCSpinner()
        self.line_entry.set_range(1, line_count)
        self.line_entry.set_value(1)
        self.line_entry.setToolTip(_("Line number in the G-code program."))
        layout.addWidget(self.line_entry)
        self.start_radio.activated_custom.connect(
            lambda value: self.line_entry.setVisible(value == 'line'))
        self.start_radio.set_value('beginning')
        self.line_entry.hide()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _StartRequest:
    """1 levelled Start: the live frame result is valid only for this request."""

    def __init__(self, seq, al_ctx, target_obj, map_frame, map_imported, safety_generation, check_mode):
        self.seq = seq                          # value of ToolGcodeSender._start_seq at the request
        self.al_ctx = al_ctx                    # _al_capture_context(): map generation, target, units
        self.target_obj = target_obj
        self.map_frame = map_frame              # _al_map_frame at the request
        self.map_imported = map_imported
        self.safety_generation = safety_generation
        self.check_mode = check_mode
        self.start_line = None


class EcutBoardError(Exception):
    """The E-Cut board check failed (no board, no hidapi): the text is shown to the user as it is."""


class ToolGCodeSender(LevellingCoreMixin, GrblClientMixin, CNCjob, AppTool):
    grbl_port_closed_sig = QtCore.pyqtSignal()
    # the hash of the correction that the height map was probed under (None: no correction)
    _al_map_calibration = None
    # the host tool change pause (T1) and the wait at the single block gate (F8): rows of ACTION_RULES
    _tool_pause = None
    _step_waiting = False
    macro_page = None       # the user buttons page of the dock (SenderMacros.MacroPage)
    # the active WCS as the controller reported it ('G54'..'G59', None: not read) and the last work position
    # shown (display units) for the typed work position
    _active_wcs = None
    _last_work = None
    _gc_inches = False      # the controller's modal units were G20 at the last $G
    _homed_this_session = False     # a home cycle finished since the connect
    # (request, {'frame': ...} or {'error': ...}) from the frame worker; emitted after the port is released
    frame_ready_sig = QtCore.pyqtSignal(object, object)
    al_result_ready_sig = QtCore.pyqtSignal(object)
    build_al_table_sig = QtCore.pyqtSignal()
    show_probing_geo_sig = QtCore.pyqtSignal(bool, bool)
    # (done_lines, total_lines) from the stream worker
    stream_progress_sig = QtCore.pyqtSignal(int, int)
    # a parsed status report (dict) from a worker
    status_report_sig = QtCore.pyqtSignal(object)
    # a port operation (not a monitor operation) was reserved or released, from any thread
    op_changed_sig = QtCore.pyqtSignal()
    # the spindle runs (True) or stopped (False): an accepted M3/M4/M5 line, from a worker
    spindle_state_sig = QtCore.pyqtSignal(bool)
    # how the stream worker ended: 'finished', 'stopped', 'check_passed', ... (see _on_stream_ended)
    stream_ended_sig = QtCore.pyqtSignal(str)
    # the result of a $X from the unlock task: True clears the alarm row (GUI slot)
    unlock_result_sig = QtCore.pyqtSignal(bool)
    # set by a 'port_error' failure (worker thread); the GUI slot closes the port
    _grbl_port_lost = False
    # a text line for the console, from any thread
    console_line_sig = QtCore.pyqtSignal(str)
    preview_ready_sig = QtCore.pyqtSignal(object)
    # the prepared job of a Start (SenderGen._PreparedJob), and the prepared restart of a start from a line
    job_prepared_sig = QtCore.pyqtSignal(object)
    restart_prepared_sig = QtCore.pyqtSignal(object)
    probe_finished_sig = QtCore.pyqtSignal(object, object)
    # a complete directional-probe result, from a worker
    edge_probe_result_sig = QtCore.pyqtSignal(object)
    simulation_event_sig = QtCore.pyqtSignal(object, str, object)
    ecut_tool_change_sig = QtCore.pyqtSignal(object, int, object)
    ecut_motion_result_sig = QtCore.pyqtSignal(object)
    ecut_connect_result_sig = QtCore.pyqtSignal(object)
    connect_result_sig = QtCore.pyqtSignal(object)
    configuration_result_sig = QtCore.pyqtSignal(object)
    # a fresh status query for a camera capture or observation, from a worker
    camera_capture_sig = QtCore.pyqtSignal(object)
    # the calibrated spindle-equivalent position of a camera-centered point
    camera_observation_sig = QtCore.pyqtSignal(object)
    # a fresh no-motion reference check before a board registration is applied
    camera_reference_sig = QtCore.pyqtSignal(object)
    # the request token of an observation or reference, so the tab can match its answer
    camera_request_sig = QtCore.pyqtSignal(str, int)
    # session, zero/home/reset, camera or calibration changed: a pending registration is old
    camera_context_changed_sig = QtCore.pyqtSignal(str)
    # the answer of a tool-change measurement or apply, from a worker
    toolchange_result_sig = QtCore.pyqtSignal(object)
    # the connect read ended on this port (the IO tab builds its signals), from a worker
    io_discovery_sig = QtCore.pyqtSignal(object)
    # (request, status, text) of an IO tab command, from a worker
    io_result_sig = QtCore.pyqtSignal(object)
    # (port, generation, request) of a Stop whose hold wait ended without a reset byte, from a worker
    brake_ended_sig = QtCore.pyqtSignal(object)

    # Stop phase: 'braking' (feed hold sent, waiting for it to complete), 'reset_sent' (waiting for the banner)
    # or None. The request counter is raised by each on_grbl_reset() call: a brake task owns the stop only
    # while the counter and the port are still its own.
    _grbl_stop_phase = None
    _grbl_brake_request = 0

    @property
    def sender_io(self):
        """The controller of the IO tab (made at the first use)."""
        controller = self.__dict__.get('_sender_io')
        if controller is None:
            controller = self.__dict__['_sender_io'] = SenderIOController(self)
        return controller

    @property
    def view3d(self):
        """The 3D view, or None. A closed Plot 3D tab deletes the canvas: then the view is gone too."""
        view = self.__dict__.get('_view3d')
        if view is not None and _qt_deleted(getattr(view.canvas, 'native', None)):
            self.__dict__['_view3d'] = None
            return None
        return view

    @view3d.setter
    def view3d(self, view):
        self.__dict__['_view3d'] = view

    def uninstall(self):
        """
        A new project replaces the plugin: the replaced plugin stops its controller and leaves the machine-stop
        registry and the application-quit signal, which hold it otherwise.
        """
        AppTool.uninstall(self)
        self._camera_public_mode = False
        self._clear_dock()
        try:
            self._grbl_shutdown()
        except Exception:
            log.exception('ToolGcodeSender.uninstall() -> the controller did not stop')
        MachineSafety.unregister(self._grbl_shutdown)
        qt_app = QtWidgets.QApplication.instance()
        if qt_app is not None:
            try:
                qt_app.aboutToQuit.disconnect(self._close_simulation)
            except (TypeError, RuntimeError):
                pass  # never connected

    def machine_connected(self):
        """True while the controller is connected (idle, or a job runs or is held)."""
        return self._grbl_port_ready()

    def confirm_panel_release(self, reason):
        """
        The panel is about to close or to be replaced ('panel') or the project switches ('project'). A connected
        machine is kept (the preference or the user) or stopped; the user can cancel the action.

        :return: False if the action must not happen
        """
        decision = self.app.decide_machine(reason)
        self.apply_machine_decision(decision)
        return decision != 'cancel'

    def apply_machine_decision(self, decision):
        """
        The answer of App.decide_machine() before the panel hides: 'stop' stops the machine now. 'keep' needs
        nothing: the panel hide never stops a connected machine (on_plugin_cleanup()).
        """
        if decision == 'stop':
            self._grbl_shutdown()

    def detach_for_reset(self):
        """
        A new project keeps this plugin and its connection. Take the panel out of the Plugin tab before the tab is
        deleted (it would delete this widget, and its hide would stop the machine); run() shows it again.
        """
        self._kept_across_reset = True
        self._clear_dock()
        scroll = self.app.ui.plugin_scroll_area
        if scroll.widget() is self:
            scroll.takeWidget()
            self.hide()
        button = getattr(self.ui, 'estop_button', None)
        if button is not None:
            button.hide()
            button.setParent(self)      # the footer layout of the deleted tab owned it
        self._estop_layout = None

    def _clear_dock(self):
        """The UI of this plugin goes away (new project): hide the dock and remove the widgets of that UI from it,
        so no click reaches a dead tool. The next build_ui() mounts its own."""
        try:
            dock = self.app.ui.grbl_console_dock
            console = dock.widget()
            if isinstance(console, GrblConsole) and console.tool is self:
                dock.hide()
                console.clear_mounted()
                self.sender_io.detach()
        except RuntimeError:
            pass  # the application already deleted the dock

    def _close_simulation(self):
        if self._simulation:
            self._simulation.close()

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        CNCjob.__init__(
            self,
            steps_per_circle=self.app.options.get(
                "cncjob_steps_per_circle",
                app_defaults.get("cncjob_steps_per_circle", AppDefaults.factory_defaults.get("cncjob_steps_per_circle"))),
            app=app
        )
        self._al_init_state()
        self._grbl_init_state()
        # the GRBL frame (WCS, work offset, report in inches) that the height map is bound to
        self._al_map_frame = None
        self._al_map_imported = False

        # the stream job: the lines that the sender streams, and the height-map
        # generation that they were levelled with (None: not levelled)
        self.job_lines = []
        self.job_map_generation = None
        self.job_target_name = None
        # the line number in the G-code text of each line of job_lines (for the messages)
        self.job_line_numbers = []
        self.view3d = None
        self._preview_generation = 0
        self._probe_start_op = None
        self._plugin_active = False
        self._kept_across_reset = False
        self._bound_collection = None
        # the public launchers run the sender only for the camera calibration workflow
        self._camera_public_mode = False
        self._last_wco = None
        # what the stream worker tells the GUI thread with stream_ended_sig: {'done', 'last_text', 'errors'}
        self._stream_result = {}
        self._simulation = None
        self._controller_state = 'Idle'
        # no report yet: the default state is not evidence that the machine stands still
        self._controller_state_time = float('-inf')
        # drill-referenced camera calibration: stored values, per-session verification and
        # the fresh machine position of the last camera capture
        self.camera_calibration = CameraCalibration.from_options(self.app.options)
        self._camera_session_id = 0
        self._camera_last_z = None
        # the token of the newest camera status request; an older worker answer is stale
        self._camera_request_id = 0

        # calibrated manual tool change: a session-only fixed reference and 1 measurement
        self.toolchange = ToolChangeMeasurement()
        self._toolchange_session_id = 0
        self._toolchange_request_id = 0
        self._toolchange_busy = False
        self._toolchange_action = None

        # the UI and its camera (Qt Multimedia) are built at the first run of the plugin: a headless run or a
        # session that never opens the Sender does not load the camera stack (issue #82)
        self.ui = None
        self.pluginName = GcodeSenderUI.pluginName
        self._continuous_jog = None
        self._continuous_jog_click_button = None
        self._continuous_jog_timer = QtCore.QTimer(self)
        self._continuous_jog_timer.setInterval(50)
        self._continuous_jog_timer.timeout.connect(self._refresh_continuous_jog)
        self._ecut_motion_dialog = None
        self._ecut_motion_port = None
        self._ecut_motion_revision = None
        self._ecut_motion_loading = False
        self._ecut_motion_io_timer = QtCore.QTimer(self)
        self._ecut_motion_io_timer.setInterval(100)
        self._ecut_motion_io_timer.timeout.connect(self._refresh_ecut_motion_io)

        self._al_core_signals_connected = False
        self.connect_signals_at_init()
        self.simulation_event_sig.connect(self._on_simulation_event)
        self._sim_ui_timer = QtCore.QTimer(self)
        self._sim_ui_timer.setInterval(100)
        self._sim_ui_timer.timeout.connect(self._update_sim_button)
        self._sim_ui_timer.start()
        # an idle controller: its status keeps the Spindle button and the position up to date
        self._status_poll_timer = QtCore.QTimer(self)
        self._status_poll_timer.setInterval(1000)
        self._status_poll_timer.timeout.connect(self._poll_idle_status)
        self._status_poll_timer.start()
        # the stream worker only stores the progress and the console lines; this timer shows them
        self._console_pending = collections.deque(maxlen=5000)
        self._progress_latest = None
        self._progress_shown = None
        self._stream_ui_timer = QtCore.QTimer(self)
        self._stream_ui_timer.setInterval(100)
        self._stream_ui_timer.timeout.connect(self._flush_stream_ui)

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='', **kwargs)

    def run(self, toggle=True):
        if self.app.plugin_tab_locked is True or getattr(self.app, 'headless', False) is True:
            return
        if not sender_enabled():
            self.app.inform.emit("[WARNING_NOTCL] Cancelled. Unsafe. This tool is not ready/verified yet.")
            return  # TODO re-enable after it is finished (remove the FLATCAM_SENDER_DEV switch)

        self.app.defaults.report_usage("ToolGcodeSender()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            # show the Tab
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                # focus on Tool Tab
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        if not self.confirm_panel_release('panel'):
                            return
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(found_idx)
                        self.on_plugin_cleanup()

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
                        return
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        self._kept_across_reset = False
        self._camera_public_mode = True
        self._plugin_active = True
        self._sync_console_filter()
        super().run()
        self.set_tool_ui()
        self.show_console_if_wanted()

        self.app.ui.notebook.setTabText(2, self.pluginName)
        self._fit_notebook_width()
        # the layout is polished when the panel is first shown: fit again once the event loop settled
        QtCore.QTimer.singleShot(0, self._fit_notebook_width)

    MAX_PANEL_WIDTH = 720

    def _fit_notebook_width(self):
        """Open the notebook wide enough for the widgets of the panel (the splitter keeps its width only when it is
        already wide enough; never wider than MAX_PANEL_WIDTH or the half of the window)."""
        try:
            ui = self.app.ui
            content = ui.plugin_scroll_area.widget()
            if content is None:
                return
            sizes = ui.splitter.sizes()
            total = int(sum(sizes))
            if len(sizes) != 2 or total <= 0:
                return
            scrollbar = int(ui.plugin_scroll_area.verticalScrollBar().sizeHint().width())
            hint = max(int(content.sizeHint().width()), int(content.minimumSizeHint().width()))
        except RuntimeError:
            return  # the Qt widgets were deleted (the tool was closed before the timer fired)
        # + the margins of the plugin tab and the notebook frame
        wanted = hint + scrollbar + 16
        wanted = min(wanted, self.MAX_PANEL_WIDTH, total // 2)
        if sizes[0] < wanted:
            ui.splitter.setSizes([wanted, total - wanted])

    # The camera mode (public workflow: Connect, Camera and manual positioning only) as data.
    # ui widgets that are hidden (dotted names go through nested attributes). The state strip, Unlock, the status
    # text, the E-Stop, Home and the Program page stay.
    CAMERA_MODE_HIDDEN_WIDGETS = (
        'level', 'job_row', 'progress_bar', 'heightmap_frame', 'probe_points_frame',
        'zero_axs_wdg.grbl_zerox_button', 'zero_axs_wdg.grbl_zeroy_button', 'zero_axs_wdg.grbl_zeroz_button',
        'zero_axs_wdg.grbl_zero_all_button', 'wcs_combo', 'spindle_button', 'spindle_rpm_entry', 'spindle_rpm_label')
    # pages of the Plugin tab (ui attribute names) that are hidden in al_toolbar
    CAMERA_MODE_HIDDEN_TABS = ('job_tab',)
    # pages of the dock (keys) that are hidden, and that are disabled; the dock is not rebuilt with the UI, so
    # the full mode applies the same lists the other way round
    CAMERA_MODE_HIDDEN_PAGES = ('overrides',)
    # the `$$` values of the controller ({110: v, 111: v}), the cumulative line durations of the job (s), the
    # feed override of the last status report (%) and the clock of the stream
    _grbl_settings = None
    job_cumulative = None
    _feed_override_pct = 100
    _stream_clock = None
    CAMERA_MODE_DISABLED_PAGES = ('console',)

    def _apply_camera_public_mode(self):
        """Apply the camera mode table. The Plugin tab UI is built new on each set_tool_ui(), so only the dock pages
        (kept across rebuilds) are set back to visible and enabled in the full mode."""
        camera = self.__dict__.get('_camera_public_mode', False)
        ui = self.ui
        console = self._console()
        if camera:
            for name in self.CAMERA_MODE_HIDDEN_WIDGETS:
                widget = ui
                for part in name.split('.'):
                    widget = getattr(widget, part)
                widget.hide()
            # Home is then alone in the zero frame: no frame around a single button
            ui.zero_axs_wdg.setStyleSheet("")
            ui.zero_axs_wdg.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            for name in self.CAMERA_MODE_HIDDEN_TABS:
                ui.al_toolbar.setTabVisible(ui.al_toolbar.indexOf(getattr(ui, name)), False)
        for key in self.CAMERA_MODE_HIDDEN_PAGES:
            console.set_page_visible(key, not camera)
        for key in self.CAMERA_MODE_DISABLED_PAGES:
            console.set_page_enabled(key, not camera)

    def set_project_collection(self, old_collection, collection):
        if old_collection is collection:
            return
        super().set_project_collection(old_collection, collection)
        self._bound_collection = collection
        for name in ('rowsInserted', 'rowsRemoved', 'modelReset'):
            getattr(old_collection, name).disconnect(self._select_available_excellon)
            getattr(collection, name).connect(self._select_available_excellon)
        self._select_available_excellon()

    def connect_signals_at_init(self):
        # the signals of the tool and of the app live as long as the tool;
        # set_tool_ui() calls this method again for each new LevelUI, so
        # connect them only 1 time
        # (not the selection signal: the close of the plugin tab disconnects it)
        self.connect_selection_signal()
        if not getattr(self, '_al_core_signals_connected', False):
            self._al_core_signals_connected = True
            self.grbl_port_closed_sig.connect(self._on_grbl_port_closed)
            self.ecut_tool_change_sig.connect(self._on_ecut_tool_change)
            self.ecut_motion_result_sig.connect(self._on_ecut_motion_result)
            self.ecut_connect_result_sig.connect(self._on_ecut_connect_result)
            self.connect_result_sig.connect(self._on_connect_result)
            self.configuration_result_sig.connect(self._on_configuration_result)
            self.al_result_ready_sig.connect(self._on_al_result_ready)
            self.build_al_table_sig.connect(self.build_al_table)
            self.show_probing_geo_sig.connect(self._on_show_probing_geo_sig)
            self.stream_progress_sig.connect(self._on_stream_progress)
            self.status_report_sig.connect(self._on_status_report)
            self.op_changed_sig.connect(self._apply_machine_state)
            self.io_discovery_sig.connect(lambda port: self.sender_io.discover(port))
            self.io_result_sig.connect(lambda result: self.sender_io.on_result(result))
            self.camera_capture_sig.connect(self._on_camera_capture)
            self.toolchange_result_sig.connect(self._on_toolchange_result)
            self.spindle_state_sig.connect(self._on_spindle_state)
            self.stream_ended_sig.connect(self._on_stream_ended)
            self.brake_ended_sig.connect(self._on_brake_ended)
            self.frame_ready_sig.connect(self._on_frame_ready)
            self.console_line_sig.connect(lambda line: self._console().append(line))
            self.preview_ready_sig.connect(self._on_preview_ready)
            self.job_prepared_sig.connect(self._on_job_prepared)
            self.restart_prepared_sig.connect(self._on_restart_prepared)
            self.probe_finished_sig.connect(self._on_probe_finished)
            self.edge_probe_result_sig.connect(self._on_edge_probe_result)
            self._bound_collection = self.app.collection
            self.app.collection.rowsInserted.connect(self._select_available_excellon)
            self.app.collection.rowsRemoved.connect(self._select_available_excellon)
            self.app.collection.modelReset.connect(self._select_available_excellon)
            # Cleanup on Graceful exit (CTRL+ALT+X combo key)
            self.app.cleanup.connect(self.on_reset_tool)
            self.app.cleanup.connect(self._grbl_disconnect)
            # the application closes or crashes: stop the motion, the spindle and the outputs, then close the port
            MachineSafety.register(self._grbl_shutdown)
            # a new project calls uninstall(): the replaced plugin stops its controller and leaves the registry
            qt_app = QtWidgets.QApplication.instance()
            if qt_app is not None:
                MachineSafety.connect_quit(qt_app)
                qt_app.aboutToQuit.connect(self._close_simulation)
        if self.ui is None:
            return
        self.ui.level.toggled.connect(self.on_level_changed)
        self._select_available_excellon()

        self.ui.avoid_exc_holes_cb.toggled.connect(self.on_avoid_exc_holes)
        self.ui.avoid_exc_combo.currentIndexChanged.connect(self._update_3d_excellon)

        self.ui.al_mode_radio.activated_custom.connect(self.on_mode_radio)
        self.ui.al_method_radio.activated_custom.connect(self.on_method_radio)
        self.ui.precision_probe_cb.toggled.connect(self.on_precision_probe_changed)
        self.ui.precision_feed_entry.valueChanged.connect(self.on_precision_probe_changed)
        self.ui.precision_retract_entry.valueChanged.connect(self.on_precision_probe_changed)
        self.ui.plot_probing_pts_cb.toggled.connect(self.show_probing_geo)
        self.ui.plot_height_map_cb.toggled.connect(self._update_3d_height_map)
        # GRBL
        self.ui.com_search_button.clicked.connect(self.on_grbl_search_ports)
        self.ui.add_bd_button.clicked.connect(self.on_grbl_add_baudrate)
        self.ui.del_bd_button.clicked.connect(self.on_grbl_delete_baudrate_grbl)
        self.ui.baudrates_list_combo.currentTextChanged.connect(self.on_baudrate_changed)
        self.ui.controller_reset_button.clicked.connect(lambda: self.on_grbl_reset())
        self.ui.recovery_button.clicked.connect(lambda: self.on_grbl_reset())
        self.unlock_result_sig.connect(self._on_unlock_result)
        self.ui.com_connect_button.clicked.connect(self.on_grbl_connect)
        self.ui.controller_combo.currentIndexChanged.connect(self._save_connection_settings)
        self.ui.connection_combo.currentIndexChanged.connect(self._save_connection_settings)
        self.ui.com_list_combo.currentIndexChanged.connect(self._save_connection_settings)
        self.ui.network_host_entry.editingFinished.connect(self._save_connection_settings)
        self.ui.network_port_entry.valueChanged.connect(self._save_connection_settings)
        for widget in (self.ui.spindle_rpm_entry, self.ui.edge_probe_distance_entry, self.ui.edge_probe_feed_entry,
                       self.ui.edge_probe_pulloff_entry, self.ui.edge_probe_travelz_entry):
            widget.valueChanged.connect(self._save_run_settings)
        self.ui.grbl_send_button.clicked.connect(self.on_grbl_send_command)
        self.ui.grbl_command_entry.returnPressed.connect(self.on_grbl_send_command)
        # a copy: the options share the list of the factory defaults until the first command
        history = self.app.options["tools_grbl_console_history"] = [
            str(command) for command in (self.app.options.get("tools_grbl_console_history") or [])]
        self._command_history = CommandHistory(self.ui.grbl_command_entry, history)
        # the same: the options keep their own list of the user buttons
        self.app.options["tools_grbl_user_buttons"] = [
            dict(button) for button in (self.app.options.get("tools_grbl_user_buttons") or [])]
        self.ui.ecut_motion_button.clicked.connect(self.on_ecut_motion_settings)

        # Jog
        for button, direction in ((self.ui.jog_wdg.jog_up_button, 'yplus'),
                                  (self.ui.jog_wdg.jog_down_button, 'yminus'),
                                  (self.ui.jog_wdg.jog_right_button, 'xplus'),
                                  (self.ui.jog_wdg.jog_left_button, 'xminus'),
                                  (self.ui.jog_wdg.jog_z_up_button, 'zplus'),
                                  (self.ui.jog_wdg.jog_z_down_button, 'zminus')):
            button.pressed.connect(lambda button=button, direction=direction:
                                   self._on_jog_pressed(button, direction))
            button.released.connect(self._stop_continuous_jog)
            button.clicked.connect(lambda checked=False, button=button, direction=direction:
                                   self._on_jog_clicked(button, direction))
        self.ui.continuous_jog_cb.toggled.connect(
            lambda checked: None if checked else self._stop_continuous_jog())
        self.ui.jog_stop_button.clicked.connect(self.on_stream_stop)
        self.ui.jog_wdg.jog_origin_button.clicked.connect(lambda: self.on_grbl_jog(direction='origin'))
        for button, direction in ((self.ui.jog_wdg.jog_up_left_button, 'xminus_yplus'),
                                  (self.ui.jog_wdg.jog_up_right_button, 'xplus_yplus'),
                                  (self.ui.jog_wdg.jog_down_left_button, 'xminus_yminus'),
                                  (self.ui.jog_wdg.jog_down_right_button, 'xplus_yminus')):
            button.clicked.connect(lambda checked=False, direction=direction: self.on_grbl_jog(direction=direction))
        for index, button in enumerate(self.ui.jog_preset_buttons):
            button.clicked.connect(lambda checked=False, index=index: self.set_jog_preset(index))

        # Zero
        self.ui.zero_axs_wdg.grbl_zerox_button.clicked.connect(lambda: self.on_grbl_zero(axis='x'))
        self.ui.zero_axs_wdg.grbl_zeroy_button.clicked.connect(lambda: self.on_grbl_zero(axis='y'))
        self.ui.zero_axs_wdg.grbl_zeroz_button.clicked.connect(lambda: self.on_grbl_zero(axis='z'))
        self.ui.zero_axs_wdg.grbl_zero_all_button.clicked.connect(lambda: self.on_grbl_zero(axis='all'))
        self.ui.zero_axs_wdg.grbl_homing_button.clicked.connect(self.on_grbl_homing)
        self.ui.wcs_combo.activated.connect(self.on_wcs_selected)
        self.ui.status_strip.dro.dro_clicked.connect(self.on_dro_clicked)

        # Sender
        self.ui.grbl_report_button.clicked.connect(self.on_grbl_report)
        self.ui.grbl_get_param_button.clicked.connect(self._on_get_parameter_clicked)
        self.ui.grbl_parameter_entry.returnPressed.connect(self._on_get_parameter_clicked)
        self.ui.import_heights_button.clicked.connect(self.on_import_height_map)
        self.ui.grbl_get_heightmap_button.clicked.connect(self.on_grbl_autolevel)
        self.ui.stop_probing_button.clicked.connect(self.on_stream_stop)
        self.ui.grbl_save_height_map_button.clicked.connect(self.on_grbl_heightmap_save)
        self.ui.edge_probe_button.clicked.connect(self.on_grbl_edge_probe)
        self.ui.edge_center_button.clicked.connect(self.on_grbl_center_probe)

        # Reset Tool
        self.ui.reset_button.clicked.connect(self.on_reset_tool)

        # Camera calibration
        camera_ui = self.ui.camera_widget
        camera_ui.capture_spindle_sig.connect(self.on_camera_capture_spindle)
        camera_ui.capture_camera_sig.connect(self.on_camera_capture_camera)
        camera_ui.verify_sig.connect(self.on_camera_verify)
        camera_ui.clear_sig.connect(self.on_camera_clear)
        camera_ui.device_changed_sig.connect(self._camera_settings_changed)
        camera_ui.orientation_changed_sig.connect(self._camera_settings_changed)
        camera_ui.tolerance_changed_sig.connect(self._camera_tolerances_changed)
        camera_ui.preview_available_sig.connect(self._camera_preview_available)

        registration_ui = self.ui.registration_widget
        registration_ui.capture_sig.connect(self.request_camera_observation)
        registration_ui.validate_sig.connect(self.request_camera_reference)
        registration_ui.stop_sig.connect(self.on_grbl_stop)
        self.camera_observation_sig.connect(registration_ui.on_camera_observation)
        self.camera_reference_sig.connect(registration_ui.on_camera_reference)
        self.camera_request_sig.connect(registration_ui.on_camera_request)
        self.camera_context_changed_sig.connect(registration_ui.on_context_changed)

        toolchange_ui = self.ui.toolchange_widget
        toolchange_ui.calibrate_sig.connect(self.on_toolchange_calibrate)
        toolchange_ui.measure_sig.connect(self.on_toolchange_measure)
        toolchange_ui.apply_sig.connect(self.on_toolchange_apply)
        toolchange_ui.acknowledge_sig.connect(self.on_toolchange_acknowledge)
        toolchange_ui.clear_sig.connect(self.on_toolchange_clear)
        toolchange_ui.stop_sig.connect(self.on_grbl_stop)

        self.ui.console_cb.toggled.connect(self.on_console_toggled)
        self.ui.fit_view_cb.toggled.connect(self.on_fit_view_toggled)
        self.ui.check_mode_cb.toggled.connect(self._show_check_mode)
        self._show_check_mode()
        self.ui.start_button.clicked.connect(self.on_start)
        self.ui.hold_button.clicked.connect(self._on_hold_button)
        self.ui.resume_button.clicked.connect(self._on_resume_button)
        self.ui.stop_button.clicked.connect(self.on_stream_stop)
        self.ui.estop_button.clicked.connect(self.on_emergency_stop)
        self.ui.estop_dock_button.clicked.connect(self.on_emergency_stop)
        self.ui.spindle_button.clicked.connect(self.on_spindle_button)
        self.ui.unlock_button.clicked.connect(self.on_unlock)
        self.ui.sim_button.clicked.connect(self.on_simulation)
        self.ui.save_levelled_button.clicked.connect(self.on_save_levelled_gcode)
        self.ui.view3d_button.clicked.connect(self.on_show_3d)
        for (name, text), (btn, byte) in self.ui.ov_buttons.items():
            btn.clicked.connect(lambda checked=False, b=byte: self.on_grbl_override(b))

    def on_reset_tool(self):
        if self.ui is None:
            return  # never run: there is nothing to reset, and a reset must not build the UI
        if self._grbl_op is not None:
            return  # a running operation owns the widgets: app.cleanup does not rebuild them
        self._probe_start_op = None
        self._clear_probe_data()
        self.set_tool_ui()

    def set_tool_ui(self):
        if self.__dict__.get('_toolchange_busy', False):
            self._hold_now()
        self._toolchange_new_session('ui')
        self._stop_continuous_jog()
        self._camera_stop_preview()
        # closing or resetting the tool while adding manual points must give
        # the canvas events back to the app
        if self.__dict__.get('_simulation') is not None:
            self._simulation.close()
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        self._release_manual_mouse_events()
        old_registration = getattr(getattr(self, 'ui', None), 'registration_widget', None)
        if old_registration is not None:
            try:
                old_registration.shutdown()
            except (AttributeError, RuntimeError):
                pass

        self.units = self.app.app_units.upper()
        self.al_heights_valid = bool(getattr(self, 'al_heights_valid', False)) and \
            self._height_map_is_complete()
        # the check mode and the levelling request stay for the session; the first UI starts unchecked
        check_mode = autolevel = False
        if self.ui is not None:
            try:
                check_mode = self.ui.check_mode_cb.isChecked()
                autolevel = self.ui.autolevel_cb.isChecked()
            except RuntimeError:
                pass    # the old widgets are already deleted
        self.clear_ui(self.layout)
        self.build_ui()

        mode_blocked = self.ui.al_mode_radio.blockSignals(True)
        method_blocked = self.ui.al_method_radio.blockSignals(True)
        try:
            self._populate_tool_ui()
        finally:
            self.ui.al_method_radio.blockSignals(method_blocked)
            self.ui.al_mode_radio.blockSignals(mode_blocked)
        self.ui.check_mode_cb.setChecked(check_mode)
        self.ui.autolevel_cb.setChecked(autolevel)
        self.ui.al_frame.setVisible(autolevel)
        self._toolchange_settings_changed()
        if self.view3d is not None and not self.__dict__.get('_camera_public_mode', False):
            self._schedule_3d_preview()

    def build_ui(self):
        """Make a new GcodeSenderUI (with its dock parts) and connect its signals."""
        old_stop = getattr(getattr(self, 'ui', None), 'estop_button', None)
        if old_stop is not None:
            old_stop.hide()
            old_stop.deleteLater()
        self.ui = GcodeSenderUI(layout=self.layout, app=self.app)
        self.ui.estop_button = QtWidgets.QPushButton(_('E-Stop'), self)
        self.ui.estop_button.setMinimumHeight(56)
        self.ui.estop_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.ui.estop_button.setStyleSheet(
            'QPushButton {background-color: #800020; color: white; font-size: 20px; font-weight: bold;}')
        self.ui.estop_button.setToolTip(_(
            'SOFTWARE emergency-stop request: abort motion, then reset to stop spindle and outputs.\n'
            'Not a replacement for a physical E-stop circuit. Communication failure can prevent hardware response.'))
        self.layout.addWidget(self.ui.estop_button)
        # the same E-Stop at the bottom of the dock header, visible on every tab
        self.ui.estop_dock_button = QtWidgets.QPushButton(self.ui.estop_button.text())
        self.ui.estop_dock_button.setMinimumHeight(40)
        self.ui.estop_dock_button.setStyleSheet(self.ui.estop_button.styleSheet())
        self.ui.estop_dock_button.setToolTip(self.ui.estop_button.toolTip())
        self.ui.operate_header.layout().addWidget(self.ui.estop_dock_button)
        # the $$ table fills the free height of the Configuration page
        self.ui.configuration = GrblConfiguration(self)
        self.ui.config_tab_layout.addWidget(self.ui.configuration, 1)
        self._set_estop_enabled(self._grbl_port_ready())
        self._on_spindle_state(self.__dict__.get('_spindle_on', False))
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()
        # the camera view moves into a pane beside the plot while the preview runs; the dock keeps the controls
        self.camera_pane = CameraPane(
            self.app, self.ui.camera_view, self.ui.camera_widget.stop_preview)
        self.ui.camera_widget.preview_button.toggled.connect(self._on_camera_preview_toggled)
        # the job buttons live in the dock header: mount it now, so the widgets always have a parent
        self._console()
        if self.isVisible():
            self._mount_emergency_stop()
        # the new widgets show the state of the machine, not their defaults
        self._grbl_set_ui_connected(self._grbl_port_ready())

    def _set_estop_enabled(self, enabled):
        """Both E-Stop buttons (the Plugin tab footer and the dock header) share one state."""
        self.ui.estop_button.setEnabled(enabled)
        self.ui.estop_dock_button.setEnabled(enabled)
        self.app.ui.sender_estop_button.setVisible(enabled)     # the status bar, always in view

    def _mount_emergency_stop(self):
        button = getattr(getattr(self, 'ui', None), 'estop_button', None)
        layout = getattr(self.app.ui, 'plugin_tab_layout', None)
        if button is not None and isinstance(layout, QtWidgets.QLayout):
            # A sibling of the plugin scroll area, not part of its scrolling contents.
            footer = getattr(self, '_estop_layout', None)
            if footer is None:
                footer = self._estop_layout = QtWidgets.QHBoxLayout()
                footer.setContentsMargins(0, 0, 0, 0)
                footer.setSpacing(0)
                layout.addLayout(footer)
            footer.insertWidget(0, button, 1)
            button.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._mount_emergency_stop()

    def hideEvent(self, event):
        button = getattr(getattr(self, 'ui', None), 'estop_button', None)
        if button is not None:
            button.hide()
        super().hideEvent(event)

    def _populate_tool_ui(self):
        # Both radio groups stay blocked, including the initial to_form().
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        # try to select in the CNCJob combobox the active object
        try:
            selected_obj = self.app.collection.get_active()
            if selected_obj.kind == 'cncjob':
                current_name = selected_obj.obj_options['name']
                self.ui.object_combo.set_value(current_name)
        except Exception:
            pass

        loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        # kept probe data belongs to its owner object; drop it if the tool opens on another object
        if getattr(self, '_al_target_name', None) is not None and (
                self.ui.object_combo.get_value() != self._al_target_name or
                loaded_obj is not getattr(self, '_al_target_obj', None)):
            self._clear_probe_data()
        if loaded_obj and loaded_obj.kind == 'cncjob':
            name = loaded_obj.obj_options['name']
        else:
            name = ''

        # Shapes container for the Voronoi cells in Autolevelling
        self._new_probing_shapes(name)

        self.form_fields.update({
            "tools_al_probe_tip_dia":           self.ui.probe_tip_dia_entry,
            "tools_al_travel_z":                self.ui.ptravelz_entry,
            "tools_al_probe_depth":             self.ui.pdepth_entry,
            "tools_al_probe_fr":                self.ui.feedrate_probe_entry,
            "tools_al_method":                  self.ui.al_method_radio,
            "tools_al_mode":                    self.ui.al_mode_radio,
            "tools_al_avoid_exc_holes_size":    self.ui.avoid_exc_holes_size_entry,
            "tools_al_rows":                    self.ui.al_rows_entry,
            "tools_al_columns":                 self.ui.al_columns_entry,
        })

        # Fill Form fields
        self.to_form()
        self._jog_defaults_to_form(app_defaults)
        self.on_grbl_search_ports(muted=True)
        self._restore_baudrate(app_defaults)
        self._restore_connection_settings()
        self._restore_run_settings()

        self.ui.plot_probing_pts_cb.set_value(
            self.app.options.get(
                "tools_al_plot_points",
                app_defaults.get("tools_al_plot_points", AppDefaults.factory_defaults.get("tools_al_plot_points")))
        )
        self.ui.avoid_exc_holes_cb.set_value(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

        self.ui.al_probe_points_table.setRowCount(0)
        self.ui.al_probe_points_table.resizeColumnsToContents()
        self.ui.al_probe_points_table.resizeRowsToContents()
        v_header = self.ui.al_probe_points_table.verticalHeader()
        v_header.hide()
        self.ui.al_probe_points_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        h_header = self.ui.al_probe_points_table.horizontalHeader()
        h_header.setMinimumSectionSize(10)
        h_header.setDefaultSectionSize(70)
        h_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h_header.resizeSection(0, 20)
        h_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.al_probe_points_table.setMinimumHeight(self.ui.al_probe_points_table.getHeight())
        self.ui.al_probe_points_table.setMaximumHeight(self.ui.al_probe_points_table.getHeight())

        # Set initial UI
        self.ui.al_rows_entry.setDisabled(True)
        self.ui.al_rows_label.setDisabled(True)
        self.ui.al_columns_entry.setDisabled(True)
        self.ui.al_columns_label.setDisabled(True)
        self.ui.al_method_lbl.setDisabled(True)
        self.ui.al_method_radio.set_value('v')
        self.ui.al_method_radio.setDisabled(True)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        try:
            self.ui.object_combo.currentIndexChanged.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.object_combo.currentIndexChanged.connect(self.on_object_changed)

        self.build_tool_ui()

        if self._is_supported_target(loaded_obj):
            self.ui.al_frame.setDisabled(False)
            self._restore_al_radios(loaded_obj)
            self._restore_al_precision(loaded_obj)
        else:
            self.ui.al_frame.setDisabled(True)

        # a retained (probed/imported) height map was preserved above (its
        # validity was already computed at the top of this method); rebuild
        # the table so the kept points show up again
        if self.al_heights_valid:
            self.build_al_table()
            # the slot plots only if 'Plot probing points' is checked
            self._on_show_probing_geo_sig(True, True)

        self.on_avoid_exc_holes(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

        self.ui.fit_view_cb.set_value(bool(self.app.options.get(
            "tools_gcode_fit_view", app_defaults.get("tools_gcode_fit_view", True))))
        self.ui.console_cb.set_value(bool(self.app.options.get(
            "tools_grbl_console_visible",
            app_defaults.get("tools_grbl_console_visible", True))))
        self.ui.autolevel_cb.set_value(False)
        self.ui.camera_widget.restore(self.app.options)
        # a rebuilt UI must not keep a verification made under other limits
        self.camera_calibration.tolerances_changed(*self._camera_tolerances())
        self.ui.registration_widget.restore(self.app.options)
        self._camera_refresh_ui()
        self._grbl_set_ui_connected(self._grbl_port_ready())
        self._apply_camera_public_mode()

    def on_object_changed(self):
        # first: a pending levelled Start request is old before the new target is published
        self._invalidate_start_requests()
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        self._probe_start_op = None

        # the job and its toolpath belong to the old object
        self.job_lines = []
        self.job_line_numbers = []
        self.job_map_generation = None
        if self.view3d is not None:
            self.view3d.set_toolpath([])

        # load the object
        obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            target_obj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            self._reload_console_program(None)
            return

        self._reload_console_program(target_obj)

        # probe points, heights and probing G-code belong to 1 object (name and
        # identity); forget them when the user selects a different object
        if obj_name != getattr(self, '_al_target_name', None) or \
                target_obj is not getattr(self, '_al_target_obj', None):
            self._clear_probe_data()

        if self._is_supported_target(target_obj):
            self.ui.al_frame.setDisabled(False)

            # Shapes container for the Voronoi cells in Autolevelling
            self._new_probing_shapes(obj_name)

            self._restore_al_radios(target_obj)
            self._restore_al_precision(target_obj)

            # the new container is empty: draw the kept points again
            if self.al_voronoi_geo_storage:
                self._on_show_probing_geo_sig(True, True)
        else:
            self.ui.al_frame.setDisabled(True)

        if self.view3d is not None and self._is_supported_target(target_obj):
            self._schedule_3d_preview()

    @staticmethod
    def _is_supported_target(target_obj):
        """The sender accepts every CNCJob; it segments the G-code itself."""
        return target_obj is not None and getattr(target_obj, 'kind', None) == 'cncjob'

    # the mixin methods on_mode_radio() and on_method_radio() call this name
    _is_supported_al_target = _is_supported_target

    def _jog_defaults_to_form(self, app_defaults):
        """The jog step and feed are stored in mm; the entries show them in the units of the app."""
        factor = 25.4 if self.units == 'IN' else 1.0
        self._update_jog_presets()
        for key, entry in (("tools_grbl_jog_step", self.ui.jog_step_entry),
                           ("tools_grbl_jog_fr", self.ui.jog_fr_entry)):
            stored = self.app.options.get(
                key, app_defaults.get(key, AppDefaults.factory_defaults.get(key)))
            entry.set_value(float(stored) / factor)  # the range of the entry clamps it
        # the option changes only when the user edits a field, never with the display rounding of a rebuild
        for entry, slot in ((self.ui.jog_step_entry, self._on_jog_step_edited),
                            (self.ui.jog_fr_entry, self._on_jog_fr_edited)):
            try:
                entry.spinner.editingFinished.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
            entry.spinner.editingFinished.connect(slot)

    def _store_jog_option(self, key, entry):
        self.app.options[key] = entry.get_value() * (25.4 if self.units == 'IN' else 1.0)

    def _on_jog_step_edited(self):
        self._store_jog_option("tools_grbl_jog_step", self.ui.jog_step_entry)

    def _on_jog_fr_edited(self):
        self._store_jog_option("tools_grbl_jog_fr", self.ui.jog_fr_entry)

    def _jog_presets(self):
        """The 4 step presets in mm from the option; a bad entry keeps the default of its place."""
        default = AppDefaults.factory_defaults["tools_grbl_jog_presets"]
        values = [float(v) for v in re.findall(r'\d+(?:\.\d+)?', default)]
        for place, text in enumerate(str(self.app.options.get("tools_grbl_jog_presets", default)).split(',')[:4]):
            try:
                value = float(text)
            except ValueError:
                continue
            if math.isfinite(value) and value > 0:
                values[place] = value
        return values

    def _update_jog_presets(self):
        factor = 25.4 if self.units == 'IN' else 1.0
        for button, value in zip(self.ui.jog_preset_buttons, self._jog_presets()):
            button.setText('%g' % round(value / factor, 4))

    def set_jog_preset(self, index):
        """Set the jog step to preset 'index' (0 to 3) as an edit of the step field does."""
        if not 0 <= index < 4:
            return
        entry = self.ui.jog_step_entry
        entry.set_value(self._jog_presets()[index] / (25.4 if self.units == 'IN' else 1.0))
        self._store_jog_option("tools_grbl_jog_step", entry)

    def _step_jog_preset(self, delta):
        presets = self._jog_presets()
        step = self.app.options.get("tools_grbl_jog_step", presets[0])
        # the preset nearest to the step is the current one; the ends stay
        current = min(range(4), key=lambda i: abs(presets[i] - step))
        self.set_jog_preset(max(0, min(3, current + delta)))

    def next_jog_preset(self):
        self._step_jog_preset(1)

    def previous_jog_preset(self):
        self._step_jog_preset(-1)

    def to_form(self, storage=None):
        if storage is None:
            storage = self.app.options

        for k in self.form_fields:
            for option in storage:
                if option.startswith('tools_al_') or option.startswith('tools_grbl_'):
                    if k == option:
                        try:
                            self.form_fields[k].set_value(storage[option])
                        except Exception:
                            # it may fail for form fields found in the tools tables if there are no rows
                            pass

    def on_grbl_search_ports(self, muted=None):
        selected = self.app.options.get('tools_grbl_serial_port', '')
        if selected == 'SH-9001 E-Cut USB':
            selected = ''  # the legacy adapter sentinel isn't an OS serial endpoint
        port_list = self.on_grbl_list_serial_ports()
        if selected and selected not in port_list:
            port_list.insert(0, selected)
        blocked = self.ui.com_list_combo.blockSignals(True)
        self.ui.com_list_combo.clear()
        self.ui.com_list_combo.addItems(port_list)
        if selected:
            self.ui.com_list_combo.setCurrentText(selected)
        self.ui.com_list_combo.blockSignals(blocked)
        if muted is not True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("COM list updated ..."))

    def _connection_controls(self):
        if self.ui is None:
            return
        self.ui.update_connection_controls(
            locked=self.grbl_ser_port is not None or self.__dict__.get('_grbl_connect_attempt') is not None)
        connected = self._grbl_connected
        pending = self.__dict__.get('_grbl_connect_attempt') is not None and not connected
        self.ui.com_connect_button.setText(
            _("Connecting... Cancel") if pending else (_("Disconnect") if connected else _("Connect")))
        self.ui.com_connect_button.setToolTip(
            _("Cancel the pending connection attempt.") if pending else
            (_("Disconnect from the controller.") if connected else _("Connect to the selected controller.")))

    def _save_connection_settings(self, *_args):
        if self.grbl_ser_port is None and self.__dict__.get('_grbl_connect_attempt') is None:
            for key, widget, method in (
                    ('controller', 'controller_combo', 'currentData'),
                    ('connection', 'connection_combo', 'currentData'),
                    ('serial_port', 'com_list_combo', 'currentText'),
                    ('network_host', 'network_host_entry', 'get_value'),
                    ('network_port', 'network_port_entry', 'get_value')):
                value = getattr(getattr(self.ui, widget), method)()
                if isinstance(value, (str, int)):
                    self.app.options['tools_grbl_' + key] = value
        self._connection_controls()

    def _restore_connection_settings(self):
        # a legacy E-Cut save stored the USB adapter as the serial port; the port alone fails the
        # serial check, so a controller left at its factory default follows the saved port
        legacy_ecut = (self.app.options.get('tools_grbl_serial_port') == 'SH-9001 E-Cut USB' and
                       self.app.options.get('tools_grbl_controller',
                                            AppDefaults.factory_defaults['tools_grbl_controller']) ==
                       AppDefaults.factory_defaults['tools_grbl_controller'])
        for key, widget in (('controller', 'controller_combo'), ('connection', 'connection_combo'),
                            ('network_host', 'network_host_entry'), ('network_port', 'network_port_entry')):
            field = getattr(self.ui, widget)
            value = 'ecut' if key == 'controller' and legacy_ecut else self.app.options.get(
                'tools_grbl_' + key, AppDefaults.factory_defaults.get('tools_grbl_' + key))
            blocked = field.blockSignals(True)
            try:
                if key in ('controller', 'connection'):
                    index = field.findData(value)
                    field.setCurrentIndex(index if index >= 0 else field.findData(
                        AppDefaults.factory_defaults['tools_grbl_' + key]))
                else:
                    field.set_value(value)
            finally:
                field.blockSignals(blocked)
        self._connection_controls()

    def _on_get_parameter_clicked(self):
        self.on_grbl_get_parameter(param=self.ui.grbl_parameter_entry.get_value())

    def _length_fields(self):
        """The probe fields that hold lengths or feeds in application units, by option key."""
        ui = self.ui
        return (('tools_grbl_edge_distance', ui.edge_probe_distance_entry),
                ('tools_grbl_edge_feed', ui.edge_probe_feed_entry),
                ('tools_grbl_edge_pulloff', ui.edge_probe_pulloff_entry),
                ('tools_grbl_edge_travelz', ui.edge_probe_travelz_entry))

    def _save_run_settings(self, *_args):
        """Keep the spindle RPM and the edge probe values for the session, with the units of the lengths."""
        options = self.app.options
        options['tools_grbl_spindle_rpm'] = self.ui.spindle_rpm_entry.get_value()
        for key, field in self._length_fields():
            options[key] = field.get_value()
        options['tools_grbl_edge_units'] = self.app.app_units.upper()

    def _restore_run_settings(self):
        """Set the spindle RPM and the edge probe values that an earlier UI stored; none stored: the defaults stay."""
        options = self.app.options
        fields = [('tools_grbl_spindle_rpm', self.ui.spindle_rpm_entry)] + list(self._length_fields())
        units = self.app.app_units.upper()
        stored_units = options.get('tools_grbl_edge_units', units)
        factor = 1.0 if stored_units == units else (25.4 if units == 'MM' else 1.0 / 25.4)
        for key, field in fields:
            if key not in options:
                continue
            blocked = field.blockSignals(True)
            try:
                if key == 'tools_grbl_spindle_rpm':
                    field.set_value(int(options[key]))
                else:
                    field.set_value(float(options[key]) * factor)
            finally:
                field.blockSignals(blocked)

    @staticmethod
    def _network_endpoint(host, port):
        if not isinstance(host, str) or any(ord(char) < 32 or ord(char) == 127 for char in host):
            raise ValueError(_("Invalid network host"))
        host = host.strip()
        if not host or any(char.isspace() or char in '/\\?@#%' or not char.isascii() for char in host):
            raise ValueError(_("Enter a hostname or IP address, not a URL"))
        if ':' in host:
            address = host[1:-1] if host.startswith('[') and host.endswith(']') else host
            host = '[%s]' % ipaddress.IPv6Address(address)
        elif not all(char.isalnum() or char in '.-_' for char in host):
            raise ValueError(_("Enter a hostname or IPv4 address, not a URL"))
        if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError(_("Invalid network port"))
        return 'socket://%s:%d' % (host, int(port))

    def on_grbl_connect(self):
        if self.__dict__.get('_simulation') is not None:
            return
        if self.__dict__.get('_grbl_connect_attempt') is not None and not self._grbl_connected:
            self._grbl_disconnect()
            self._connection_controls()
            return
        if self.__dict__.get('_continuous_jog') is not None and self.grbl_ser_port is not None:
            self._stop_continuous_jog()
            self._grbl_disconnect()
            return
        if self._grbl_op is not None or self._grbl_close_pending:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The controller is busy. Wait until the running operation stops."))
            return

        # the Connect button toggles: a click while a port is open closes it
        if self.grbl_ser_port is not None:
            self._grbl_disconnect()
            return

        # the previous connection did not close: it can still hold the device
        with self._grbl_op_lock:
            closing = bool(self._grbl_closing_ports)
            unclosed = list(self._grbl_unclosed_ports)
        if closing:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The previous connection is still closing. Try again."))
            return
        for unclosed_port in unclosed:
            try:
                unclosed_port.close()
            except Exception as err:
                self.app.log.error("ToolGCodeSender.on_grbl_connect() -> %s" % str(err))
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The previous connection did not stop. Wait a moment and try again, or restart the application."))
                return
            with self._grbl_op_lock:
                self._grbl_unclosed_ports.remove(unclosed_port)

        port_name = self.ui.com_list_combo.currentText()
        if " (" in port_name:
            port_name = port_name.rpartition(" (")[0]

        controller_widget = getattr(self.ui, 'controller_combo', None)
        controller = controller_widget.currentData() if controller_widget is not None else None
        explicit = controller in ('grbl', 'grblhal', 'ecut')
        if isinstance(controller_widget, QtWidgets.QComboBox) and not explicit:
            controller, explicit = 'grbl', True
        # a legacy E-Cut save leaves the controller at grbl; the USB adapter port is not a serial port
        ecut = controller == 'ecut' or (controller == 'grbl' and port_name == 'SH-9001 E-Cut USB')
        transport = self.ui.connection_combo.currentData() if controller == 'grblhal' else 'serial'
        if ecut:
            port_name = 'SH-9001 E-Cut USB'
        elif port_name == 'SH-9001 E-Cut USB' and transport != 'network':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Select a serial port."))
            return
        try:
            if transport == 'network':
                port_name = self._network_endpoint(self.ui.network_host_entry.get_value(),
                                                   self.ui.network_port_entry.get_value())
            baudrate = 0 if ecut else (115200 if transport in ('native', 'network')
                                      else int(self.ui.baudrates_list_combo.currentText()))
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s' % (
                str(err) if transport == 'network' else _("Select a valid baudrate.")))
            return

        self._save_connection_settings()
        attempt = object()
        with self._grbl_op_lock:
            self._start_seq += 1
            self._grbl_connected = False
            self._grbl_connect_attempt = attempt
        # a new controller session: the WCO and the report units of the old one do not apply
        self._last_wco = None
        self._grbl_report_inches = None
        self._grbl_settings = None
        self._clear_connection_capabilities()
        self._grbl_set_ui_connected(False)
        if ecut:
            self._ecut_connect_start(port_name, attempt)
            return
        if transport == 'network':
            self._network_connect_start(port_name, attempt)
            return
        # Both requests of the worker are non-mutating. Legacy GRBL ignores 0x80; RT- ignores '?'.
        self._serial_connect_start(port_name, baudrate, attempt)

    @staticmethod
    def _connection_snapshot(port):
        # A snapshot is bounded even if the peer reports continuously; never use readlines().
        return port.read(min(65536, max(1, port.in_waiting)))

    def _network_connect_start(self, port_name, attempt):
        self._connect_worker_start(port_name, attempt, lambda: serial.serial_for_url(
            port_name, timeout=0.1, write_timeout=1.0, do_not_open=True))

    def _serial_connect_start(self, port_name, baudrate, attempt):
        def make_port():
            port = serial.serial_for_url(port_name, baudrate,
                                         bytesize=serial.EIGHTBITS,
                                         parity=serial.PARITY_NONE,
                                         stopbits=serial.STOPBITS_ONE,
                                         timeout=0.1,
                                         write_timeout=1.0,
                                         xonxoff=False,
                                         rtscts=False,
                                         do_not_open=True)
            # Don't reset an already running controller just to discover it.
            try:
                port.dtr = False
                port.rts = False
            except (OSError, serial.SerialException):
                pass  # some native USB drivers have no modem control lines
            return port

        self._connect_worker_start(port_name, attempt, make_port)

    def _connect_worker_start(self, port_name, attempt, make_port):
        """Open the port, send the wake-up requests and read the answer in a worker, not on the GUI thread."""
        def create_port():
            port = error = None
            opened = False
            answer = b''
            try:
                with self._grbl_op_lock:
                    if self._grbl_connect_attempt is not attempt:
                        return
                port = make_port()
                port.open()
                opened = True
                with self._grbl_op_lock:
                    if self._grbl_connect_attempt is not attempt:
                        port.close()
                        return
                port.write(b'?')
                port.write(b'\x80')
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and len(answer) < 65536:
                    with self._grbl_op_lock:
                        if self._grbl_connect_attempt is not attempt:
                            port.close()
                            return
                    answer += self._connection_snapshot(port)
            except Exception as failure:
                error = failure
                if port is not None:
                    port.close()
                if not opened:
                    port = None  # the device was never reached
            try:
                self.connect_result_sig.emit((port_name, attempt, port, answer, error))
            except RuntimeError:
                if port is not None:
                    port.close()  # the plugin was deleted while the worker was connecting

        self.app.worker_task.emit({'fcn': create_port, 'params': []})

    def _on_connect_result(self, response):
        port_name, attempt, port, answer, error = response
        with self._grbl_op_lock:
            if self._grbl_connect_attempt is not attempt:
                if port is not None:
                    self._close_ecut_port_async(port)
                return
            if error is None:
                self.grbl_ser_port = port
                self._grbl_rx_port = port
                self._grbl_rx_buffer = b''
                self._grbl_reply_pending = False
                self._connect_handshake = (attempt, answer)
        if error is not None:
            # The connection worker already closed this failed port; no motion was submitted.
            if port is not None:
                with self._grbl_op_lock:  # the wake-up failed after the open: the state of the machine is unknown
                    self._grbl_failure_locked(None, 'port_error')
            self._grbl_close_port(attempt=attempt)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not connect to port"), port_name))
            self._connection_controls()
            return
        self._grbl_connect_finish(port_name, port, attempt)

    def _ecut_connect_start(self, port_name, attempt):
        """
        Create the E-Cut port in a worker: its start waits for the HID worker and would stop the GUI.
        The result comes back on the GUI thread through ecut_connect_result_sig.
        """
        settings = Path(QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.AppDataLocation)) / 'ecut' / 'grbl_settings.json'

        def create_port():
            port = error = None
            try:
                from appPlugins.Common.ecut.ecut_config import EcutBoardConfig
                from appPlugins.Common.ecut.ecut_protocol import find_boards, EcutUsbError
                from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
                board = EcutBoardConfig()
                try:
                    found = find_boards(board.vid, board.pid)
                except EcutUsbError as err:
                    raise EcutBoardError(str(err)) from err
                if not found:
                    raise EcutBoardError(
                        _("E-Cut board not found (USB VID %04X, PID %04X). Connect the board and try again.")
                        % (board.vid, board.pid))
                port = EcutSenderPort(settings_path=settings, timeout=0.1, write_timeout=1.0)
            except Exception as failure:
                error = failure
            finally:
                self.ecut_connect_result_sig.emit((port_name, attempt, port, error))

        self.app.worker_task.emit({'fcn': create_port, 'params': []})

    def _close_ecut_port_async(self, port):
        """Close an E-Cut port that is not used (close() joins the worker and can block)."""
        def close_port():
            try:
                port.close()
            except Exception as err:
                # the port can still hold the device: the next Connect retries the close first
                with self._grbl_op_lock:
                    self._grbl_unclosed_ports.append(port)
                self.app.log.error("ToolGcodeSender._close_ecut_port_async() -> %s" % str(err))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The port did not close"), str(err)))

        self.app.worker_task.emit({'fcn': close_port, 'params': []})

    def _on_ecut_connect_result(self, response):
        """GUI thread: finish the E-Cut connection that a worker started."""
        port_name, attempt, port, error = response
        if error is not None:
            if self._grbl_connect_attempt is attempt:
                self.app.log.error("ToolGcodeSender._on_ecut_connect_result() -> %s" % str(error))
                self._grbl_close_port(attempt=attempt)
                if isinstance(error, EcutBoardError):
                    self.app.inform.emit("[ERROR_NOTCL] %s" % str(error))
                else:
                    self.app.inform.emit("[ERROR_NOTCL] %s: %s" % (_("Could not connect to port"), port_name))
            return

        try:
            port.tool_change_callback = lambda tool, connection=port: self.ecut_tool_change_sig.emit(
                connection, tool, connection.tool_change_token)
            with self._grbl_op_lock:
                stale = self._grbl_connect_attempt is not attempt
                if not stale:
                    self.grbl_ser_port = port
                    self._grbl_rx_port = port
                    self._grbl_rx_buffer = b''
                    self._grbl_reply_pending = False
            if stale:
                # a new Connect or a Disconnect came first
                self._close_ecut_port_async(port)
                return

            # The HID adapter has initialized RAM and queued its verified connection banner.
            port.write("\r\n\r\n".encode('utf-8'))
        except (serial.SerialException, OSError, ValueError) as err:
            self.app.log.error("ToolGcodeSender._on_ecut_connect_result() -> %s" % str(err))
            self._grbl_close_port(attempt=attempt)
            self.app.inform.emit("[ERROR_NOTCL] %s: %s" % (_("Could not connect to port"), port_name))
            return

        QtCore.QTimer.singleShot(1000, lambda: self._grbl_connect_finish(port_name, port, attempt))

    def _grbl_connect_finish(self, port_name, port, attempt):
        """
        Read the GRBL answer to the wake-up lines. Mark the port as connected
        if GRBL answered, else close the port.
        """
        with self._grbl_op_lock:
            if self.grbl_ser_port is not port or self._grbl_connect_attempt is not attempt:
                return
            if port is None or not getattr(port, 'is_open', False):
                return
            safety_generation = self._grbl_safety_generation

        error = None
        try:
            snapshot = self.__dict__.pop('_connect_handshake', None)
            answer = [snapshot[1] if snapshot is not None and snapshot[0] is attempt
                      else self._connection_snapshot(port)]
        except (serial.SerialException, OSError) as err:
            error = err
            answer = []

        connected = False
        with self._grbl_op_lock:
            if self.grbl_ser_port is not port or self._grbl_connect_attempt is not attempt:
                return
            if error is not None:
                self.app.log.error("ToolLevelling._grbl_connect_finish() -> %s" % str(error))
                self._grbl_failure_locked(None, 'port_error')
            buffered = self._grbl_rx_buffer if self._grbl_rx_port is port else b''
            # Only complete records are classified. A fragment stays buffered so the
            # transaction reader can finish it, for example the rest of an ALARM line.
            complete, _sep, suffix = (buffered + b''.join(answer)).rpartition(b'\n')
            lines = [text.strip() for text in complete.decode('utf-8', errors='replace').splitlines()]
            alarm = any(text.lower().startswith('alarm:') or parse_grbl_status_state(text) == 'Alarm'
                        for text in lines)
            banner = any(is_controller_banner(text) for text in lines)
            self._grbl_failure_locked(None, 'handshake', '\n'.join(lines))
            # unclassified old records stay buffered, a classified alarm does not
            self._grbl_rx_buffer = (b'' if alarm else buffered[:buffered.rfind(b'\n') + 1]) + suffix
            self._grbl_rx_port = port
            reports = [report for text in lines if (report := parse_grbl_status_report(text)) is not None]
            if error is None and (banner or reports or any(text.lower() == 'ok' for text in lines)):
                self._start_seq += 1
                self._grbl_stop_phase = None
                # Only a startup banner confirms reset; DTR and ok do not.
                if (banner and not alarm and self._grbl_recovery == 'needs_reset'
                        and safety_generation == self._grbl_safety_generation):
                    self._grbl_recovery = 'needs_home'
                    self._grbl_reset_pending = False
                    self._grbl_reply_pending = False
                    self._grbl_rx_buffer = suffix  # drop the classified part, keep the fragment
                self._grbl_connected = True
                self._grbl_port_lost = False
                # Capability belongs to this handshake's port, never a previous connection.
                self._native_jog_port = port if any(is_native_jog_banner(text) for text in lines) else None
                # grblHAL prints its own banner; its capabilities ($I+) belong to this session only
                self._grbl_grblhal = any(is_grblhal_banner(text) for text in lines)
                self._grbl_identity_pending = not callable(getattr(port, 'prepare_program', None))
                self._grbl_identity = None
                self._grbl_caps = None
                self._grbl_top_bit_realtime = False
                self._grbl_motion_blocked = None
                self._grbl_tool_change_pending = False
                self._grbl_mpg_owned = False
                self._grbl_sd_owned = False
                for report in reports:
                    self._grbl_update_ownership(report)
                self._last_wco = None
                connected = True

        if connected:
            # the UI reads the machine situation, which takes the operation lock: after the lock
            self._grbl_set_ui_connected(True)
            self.app.inform.emit("%s: %s" % (_("Port connected"), port_name))
            if callable(getattr(port, 'prepare_program', None)) and not any(
                    entry['readable'] and (entry['name'] == 'ESTOP' or 'LIMIT' in entry['name'])
                    for entry in port.effective_inputs()):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("E-Cut: no E-stop or limit input is enabled."))
            if self._grbl_recovery is not None:
                self.app.inform.emit('[WARNING_NOTCL] %s' % self._grbl_recovery_text())
            self._camera_new_session('connect')
            self._toolchange_new_session('connect')
            # after this slot ended: the read is a new operation, so the port must be free for the caller
            QtCore.QTimer.singleShot(0, self._grbl_read_units_after_connect)
            return

        if self._grbl_close_port(port=port, attempt=attempt):
            self.app.inform.emit("[ERROR_NOTCL] %s: %s" % (_("Could not connect to the controller on port"), port_name))

    def _grbl_read_report_units(self, op):
        """
        Read $13 (the report units of GRBL) into self._grbl_report_inches: True (inches), False (mm) or
        None (not known). A worker thread, under the operation `op`; no motion.

        :return: the status of the transaction ('ok', 'error', 'timeout', 'cancelled' or 'port_error').
        """
        self.sender_io.pin_report = None        # not known until this read succeeds
        text, status = self._grbl_transact(op, '$$', timeout=5.0, echo=False)
        value = parse_grbl_setting(text, 13) if status == 'ok' else None
        self._grbl_report_inches = (value == 1.0) if value in (0.0, 1.0) else None
        self._grbl_settings = ({number: parse_grbl_setting(text, number) for number in (22, 110, 111)}
                               if status == 'ok' else None)
        if status == 'ok':
            # GRBL 1.1 always reports the pins; grblHAL does it with $10 bit 4
            mask = parse_grbl_setting(text, 10)
            self.sender_io.pin_report = (not self.__dict__.get('_grbl_grblhal', False)
                                         or (mask is not None and bool(int(mask) & 16)))
        return status

    def _grbl_read_after_connect(self, op):
        """
        Read the identity of a grblHAL controller ($I+) and the report units ($13): no motion, a worker
        thread under `op`, no widgets. The capabilities are applied before the first motion is allowed.
        """
        self._grbl_grblhal = self.__dict__.get('_grbl_grblhal', False)
        if not callable(getattr(op.port, 'prepare_program', None)):
            if self._grbl_identity_pending:
                report = parse_grbl_status_report(self._grbl_query_status(op))
                if (report is None or report['state'] not in ('Idle', 'Alarm') or
                        self._grbl_mpg_owned or self._grbl_sd_owned):
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "Controller discovery refused during external execution or MPG ownership. "
                        "Reconnect after it finishes; no reset was sent."))
                    return 'error'
            text, status = self._grbl_transact(op, '$I+', timeout=5.0, echo=False)
            if status not in ('ok', 'error'):
                return status
            if status == 'error' or ('[VER:' not in text and '[OPT:' not in text):
                # standard GRBL has no $I+: its plain $I answers the version and the options
                text, status = self._grbl_transact(op, '$I', timeout=5.0, echo=False)
                if status not in ('ok', 'error'):
                    return status
            if status == 'ok':
                identity = parse_grbl_identity(text)
                self._grbl_identity = identity
                self._grbl_caps = grbl_capabilities(identity)
                self._grbl_grblhal = self._grbl_caps['grblhal'] or self.__dict__.get('_grbl_grblhal', False)
                if self._grbl_grblhal:
                    self._native_jog_port = op.port
                self._grbl_apply_capabilities()
            self._grbl_identity_pending = False
        status = self._grbl_read_report_units(op)
        if status == 'ok':
            self._grbl_read_active_wcs(op)
        return status

    def _line_limits(self):
        """
        :return: (rx_size, max_line) of the connected controller: its receive buffer less 1 byte (127 for GRBL
            when OPT does not report it) and the longest line with the line end. The one source of the limits
            of every line check and of the stream.
        """
        caps = self.__dict__.get('_grbl_caps') or {}
        rx_buffer = caps.get('rx_buffer')
        return (rx_buffer - 1 if rx_buffer else GRBL_RX_BUFFER), caps.get('max_line', GRBL_MAX_LINE)

    def _grbl_apply_capabilities(self):
        """Apply what $I+ reported: the real-time command form, and the configurations that are not supported."""
        caps = self._grbl_caps or {}
        self._grbl_top_bit_realtime = caps.get('top_bit_realtime', False)
        blocked = None
        if caps.get('xyz_only') is False:
            blocked = _("This grblHAL controller has more than the X, Y and Z axes. "
                        "Only XYZ machines are supported.")
        self._grbl_motion_blocked = blocked
        if blocked:
            self.app.inform.emit('[ERROR_NOTCL] %s' % blocked)
        elif caps.get('probe') is False:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The controller reports that it has no probe input. Probing and levelling will not work."))

    def _grbl_read_units_after_connect(self):
        """
        Read $I+ and $13 after the connect, in a non-motion operation (GUI thread). The connect finish holds no
        operation, so the read is a new one. It is skipped when GRBL can hold lines of an old job: the
        Start reads $13 again.
        """
        if not self._grbl_port_ready() or self._grbl_stream_dirty or self._grbl_reset_pending:
            return
        def task(op):
            status = self._grbl_read_after_connect(op)
            if status in ('ok', 'error') and not self._grbl_identity_pending:
                # the controller is known: the IO tab builds its signals
                self.io_discovery_sig.emit(op.port)
            return status

        self._grbl_start_op('units', task)

    def _grbl_set_ui_connected(self, connected):
        """Show the connection state on the Connect button and on the tabs."""
        self._connection_controls()
        self._invalidate_coordinates()
        self._invalidate_configuration()
        if not connected:
            self.ui.status_strip.clear()
            self.ui.status_label.setText(_("Idle"))
            self.ui.progress_bar.setValue(0)
            self.ui.ov_values_label.setText(_('F: %d%%  R: %d%%  S: %d%%') % (100, 100, 100))
            self._unlock_execution_view()
        self.app.ui.sender_estop_button.setVisible(connected)
        self.sender_io.refresh()
        camera_mode = bool(self.__dict__.get('_camera_public_mode', False))
        # fixed-step jog and the position readouts need a connected controller in either workflow
        self.ui.ctrl_grbl_frame.setEnabled(connected)
        self.ui.ctrl_grbl_frame2.setEnabled(connected)
        self.ui.ecut_motion_button.setVisible(
            connected and not camera_mode and self._ecut_settings_capable(self.grbl_ser_port))
        if not connected:
            self._close_ecut_motion_settings()
        # continuous jog stays visible, explained and disabled unless this controller has native jog
        continuous = connected and self._continuous_jog_capable(self.grbl_ser_port)
        self.ui.continuous_jog_cb.setEnabled(continuous)
        self.ui.continuous_jog_cb.setToolTip(
            _("Press and hold to jog; release to cancel. Native GRBL requires a verified 1.1 connection.")
            if continuous else _("Continuous jog requires GRBL 1.1 startup identification or native E-Cut support."))
        if not continuous:
            self.ui.continuous_jog_cb.setChecked(False)
            self._stop_continuous_jog()
        color = 'seagreen' if connected else 'red'
        self.ui.com_connect_button.setStyleSheet("QPushButton {background-color: %s;}" % color)
        # zero and Home sit in the jog block, outside the frames that are enabled above
        self.ui.zero_axs_wdg.setEnabled(connected)
        self._homed_this_session = False
        if not connected:
            self._active_wcs = None
            self._last_work = None
        # Reset Tool rebuilds the UI: not while a port is ready
        self.ui.reset_button.setEnabled(not connected)
        self._apply_machine_state()
        self.ui.camera_widget.set_connected(connected)
        self.ui.registration_widget.set_connected(connected)
        self.ui.toolchange_widget.set_connected(connected)

        toolbar = self.ui.al_toolbar
        idx = toolbar.indexOf(self.ui.gr_conn_tab)
        if idx >= 0:
            toolbar.tabBar.setTabTextColor(idx, QtGui.QColor(color))
        for tab in (self.ui.probing_tab, self.ui.toolchange_tab, self.ui.config_tab):
            idx = toolbar.indexOf(tab)
            if idx >= 0:
                toolbar.tabBar.setTabEnabled(idx, connected)

    def _on_grbl_port_closed(self):
        """Slot of grbl_port_closed_sig (GUI thread)."""
        if self.grbl_ser_port is not None or self.__dict__.get('_grbl_connect_attempt') is not None:
            return  # a queued close notification belongs to an older session
        self._clear_connection_capabilities()
        self._controller_state = 'Idle'
        # no report yet: the default state is not evidence that the machine stands still
        self._controller_state_time = float('-inf')
        self._program_paused = False
        self._on_spindle_state(False)
        self._camera_new_session('disconnect')
        self._toolchange_new_session('disconnect')
        self.sender_io.on_disconnect()
        try:
            self._grbl_set_ui_connected(False)
        except (AttributeError, RuntimeError):
            # the UI can be already deleted when the application ends
            pass

    def _clear_connection_capabilities(self):
        self._invalidate_configuration()
        self._grbl_tool_fence_op = None
        self._native_jog_port = None
        self._grbl_identity = None
        self._grbl_caps = None
        self._grbl_identity_pending = True
        self._grbl_grblhal = False
        self._grbl_top_bit_realtime = False
        self._grbl_motion_blocked = None
        self._grbl_tool_change_pending = False
        self._grbl_mpg_owned = False
        self._grbl_sd_owned = False

    @staticmethod
    def _ecut_settings_capable(port):
        return all(callable(getattr(port, name, None)) for name in (
            'get_motion_settings', 'save_motion_settings', 'calibration_move',
            'get_io_state', 'set_manual_output'))

    def on_ecut_motion_settings(self):
        port = self.grbl_ser_port
        if not self._ecut_settings_capable(port):
            return
        dialog = self._ecut_motion_dialog
        if dialog is not None and self._ecut_motion_port is port:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        if self._ecut_motion_loading:
            return
        self._ecut_motion_loading = True

        def read_settings():
            result = error = None
            try:
                with self._grbl_op_lock:
                    valid = (self.grbl_ser_port is port and self._grbl_connected
                             and not self._grbl_close_pending)
                if not valid:
                    return
                result = port.get_motion_settings()
            except Exception as failure:
                error = str(failure)
            finally:
                self.ecut_motion_result_sig.emit((port, None, 'load', result, error))

        # This is an immutable worker-published snapshot, not a serial transaction.
        self.app.worker_task.emit({'fcn': read_settings, 'params': []})

    def _ecut_settings_task(self, op, dialog, action, args):
        port = op.port
        if dialog is not None:
            dialog.set_busy(True)
            dialog.set_calibration_running(action == 'calibrate')

        def task():
            result = error = None
            try:
                with self._grbl_op_lock:
                    valid = (self._grbl_op is op and self.grbl_ser_port is port
                             and not op.cancel.is_set() and not self._grbl_close_pending)
                if not valid:
                    raise ValueError(_("The controller operation was cancelled."))
                method = getattr(port, {'load': 'get_motion_settings', 'save': 'save_motion_settings',
                                        'calibrate': 'calibration_move', 'output': 'set_manual_output',
                                        'reference': 'reference_axes'}[action])
                result = method(*args)
                if action == 'reference' and {0, 1, 2} <= set(args[0]):
                    with self._grbl_op_lock:
                        # without home switches, a manual machine home of X, Y and Z replaces Home
                        if (self._grbl_recovery == 'needs_home' and not op.cancel.is_set()
                                and op.safety_generation == self._grbl_safety_generation):
                            self._grbl_recovery = None
                if op.cancel.is_set():
                    # the call returned: the board already took effect, so the result is reported
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                        "Stop came after the E-Cut settings were applied; the board has the new settings."))
            except Exception as failure:
                error = str(failure)
            finally:
                self._grbl_release(op)
                self.ecut_motion_result_sig.emit((port, dialog, action, result, error))

        self.app.worker_task.emit({'fcn': task, 'params': []})

    def _ecut_motion_action(self, dialog, action, args):
        if (dialog is not self._ecut_motion_dialog or self.grbl_ser_port is not self._ecut_motion_port
                or not self._grbl_connected):
            dialog.show_error(_("The controller connection changed. Reopen its settings."))
            return
        op = self._grbl_reserve('E-Cut calibration' if action == 'calibrate' else 'E-Cut settings',
                                motion=action == 'calibrate')
        if op is not None:
            self._ecut_settings_task(op, dialog, action, args)
        else:
            dialog.show_error(_("The controller is busy. Wait until it is idle, then try again."))

    def _on_ecut_motion_result(self, response):
        port, dialog, action, result, error = response
        if action == 'load' and self.grbl_ser_port is port:
            self._ecut_motion_loading = False
        if self.grbl_ser_port is not port or not self._grbl_connected or not port.is_open:
            return
        if dialog is None and action == 'io_revision':
            self.sender_io.on_revision(port, result, error)
            return
        if dialog is None and action == 'output':
            self.sender_io.on_ecut_output_result(port, result, error)
            return
        if dialog is not None and dialog is not self._ecut_motion_dialog:
            return
        if error is not None:
            if dialog is not None:
                dialog.set_busy(False)
                dialog.show_error(error)
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("E-Cut settings"), error))
            return
        if action == 'load':
            from appPlugins.ToolGcodeSender.SenderMotion import EcutMotionDialog
            try:
                dialog = EcutMotionDialog(result, parent=self)
            except (ValueError, TypeError, KeyError, OverflowError) as failure:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Invalid E-Cut settings"), failure))
                return
            dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            from appCommon.machine_profile.facts import active_profile_of
            dialog.set_machine_profile(active_profile_of(self.app))
            self._ecut_motion_dialog, self._ecut_motion_port = dialog, port
            self._ecut_motion_revision = result['revision']
            dialog.apply_requested.connect(lambda values: self._ecut_motion_action(dialog, 'save', (values,)))
            dialog.calibration_requested.connect(lambda axis, distance, feed, revision:
                self._ecut_motion_action(dialog, 'calibrate', (axis, distance, feed, revision)))
            dialog.output_requested.connect(lambda pin, active:
                self._ecut_motion_action(dialog, 'output', (pin, active, self._ecut_motion_revision)))
            dialog.stop_requested.connect(self._stop_ecut_calibration)
            dialog.reference_requested.connect(lambda axes: self._ecut_motion_action(dialog, 'reference', (axes,)))
            dialog.sensor_home_requested.connect(lambda: self.on_grbl_homing())
            dialog.finished.connect(lambda _result: self._closed_ecut_motion_settings(dialog))
            dialog.show()
            self._ecut_motion_io_timer.start()
        elif action == 'save':
            try:
                dialog.load_settings(result)
                self._ecut_motion_revision = result['revision']
                self.sender_io.on_ecut_config_saved(port, result['revision'])
            except (ValueError, TypeError, KeyError, OverflowError) as failure:
                dialog.show_error(str(failure))
            dialog.set_busy(False)
        elif action == 'calibrate':
            dialog.calibration_finished(result)
        else:
            dialog.set_busy(False)
        self._refresh_ecut_motion_io()

    def _stop_ecut_calibration(self):
        with self._grbl_op_lock:
            op = self._grbl_op
            if op is None or op.name != 'E-Cut calibration':
                return
            op.cancel.set()
        self._grbl_realtime(b'\x85')

    def _refresh_ecut_motion_io(self):
        dialog, port = self._ecut_motion_dialog, self._ecut_motion_port
        if dialog is None:
            self._ecut_motion_io_timer.stop()
            return
        if port is not self.grbl_ser_port or not self._grbl_connected:
            self._close_ecut_motion_settings()
            return
        try:
            state = port.get_io_state()
        except (OSError, ValueError, RuntimeError):
            state = {'connected': False, 'age_s': float('inf'), 'inputs': 0, 'outputs': 0, 'busy': True}
        dialog.update_io(state)

    def _closed_ecut_motion_settings(self, dialog):
        if self._ecut_motion_dialog is dialog:
            self._ecut_motion_io_timer.stop()
            self._ecut_motion_dialog = self._ecut_motion_port = self._ecut_motion_revision = None

    def _ecut_queue_suffix(self):
        """Host-side queue of an E-Cut port for the status line; empty for any other controller."""
        port = self.grbl_ser_port
        if not callable(getattr(port, 'get_queue_state', None)):
            return ''
        from .SenderEcutIO import ecut_queue_text
        try:
            return '   ' + ecut_queue_text(port.get_queue_state())
        except (OSError, ValueError, RuntimeError):
            return ''

    def _close_ecut_motion_settings(self):
        self._ecut_motion_loading = False
        dialog = self.__dict__.get('_ecut_motion_dialog')
        if dialog is not None:
            dialog.set_busy(False)
            dialog.reject()
            self._closed_ecut_motion_settings(dialog)

    def _on_ecut_tool_change(self, port, tool, token):
        def current():
            return (self.grbl_ser_port is port and port.is_open
                    and port.pending_tool_change == tool and port.tool_change_token is token)

        if not current():
            return
        answer = QtWidgets.QMessageBox.question(
            self.app.ui, _("Manual tool change"),
            _("Install tool %d, then confirm to continue. The spindle remains off until a spindle command.") % tool,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel)
        if not current():
            return
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            port.confirm_tool_change(tool, token=token)
        else:
            self._reset_now()

    def on_grbl_add_baudrate(self):
        new_bd = str(self.ui.new_baudrate_entry.get_value())
        if int(new_bd) >= 40 and self.ui.baudrates_list_combo.findText(new_bd) == -1:
            self.ui.baudrates_list_combo.addItem(new_bd)
            self.ui.baudrates_list_combo.setCurrentText(new_bd)

    def _restore_baudrate(self, app_defaults):
        """Select the saved baud rate in the combo; a custom rate that is not in the list is added."""
        baudrate = str(self.app.options.get(
            "tools_grbl_baudrate",
            app_defaults.get("tools_grbl_baudrate", AppDefaults.factory_defaults.get("tools_grbl_baudrate"))))
        combo = self.ui.baudrates_list_combo
        if not baudrate.isdigit():
            return
        if combo.findText(baudrate) == -1:
            combo.addItem(baudrate)
        combo.setCurrentText(baudrate)

    def on_baudrate_changed(self, text):
        # keep the selected baud rate, so the next session starts with it
        if text and text.isdigit():
            self.app.options["tools_grbl_baudrate"] = text

    def on_grbl_delete_baudrate_grbl(self):
        current_idx = self.ui.baudrates_list_combo.currentIndex()
        self.ui.baudrates_list_combo.removeItem(current_idx)

    def on_grbl_send_command(self):
        cmd = str(self.ui.grbl_command_entry.get_value() or '').strip()
        if not cmd or not self._action_ok('console'):
            return
        # the dock entry cleared itself after a send: the one entry does it now
        self.ui.grbl_command_entry.clear()
        self._command_history.add(cmd)
        if self._grbl_queue_tool_command(cmd):
            return
        self._invalidate_coordinates()
        changes_units = re.match(r'\$13\s*=|\$RST', cmd, re.IGNORECASE) is not None
        if changes_units:
            self._grbl_report_inches = None
            self._invalidate_configuration()
        # $10 sets the pin reporting of grblHAL: the IO tab reads it again
        changes_units = changes_units or re.match(r'\$10\s*=', cmd) is not None

        def task(op):
            status = self._grbl_transact(op, cmd, timeout=30.0)[1]
            if changes_units and status == 'ok':
                status = self._grbl_read_report_units(op)
            if status == 'ok' and re.search(r'\bG5[4-9](\.\d)?\b', cmd, re.IGNORECASE):
                self._grbl_read_active_wcs(op)
            return status == 'ok'

        # a typed command can move the machine, so it needs a machine that is not in recovery
        self._grbl_start_op('command', task, motion=True)

    def _macro_profile_gate(self, op, lines):
        """Hook of the machine profile check of a macro (worker thread, before the first write).

        :return: True if the macro may be sent.
        """
        return True

    def run_macro(self, lines):
        """Send the lines of a user button to the controller, in one operation; stop at the first error."""
        if not self._action_ok('macro'):
            return False
        stream = SenderGen.clean_lines('\n'.join(str(line) for line in lines))
        if not stream:
            return False
        too_long = validate_stream_lines(stream, *self._line_limits())
        if too_long is not None:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The line is too long"), stream[too_long]))
            return False
        self._invalidate_coordinates()

        def task(op):
            if not self._macro_profile_gate(op, stream):
                return False
            for line in stream:
                text, status = self._grbl_transact(op, line, timeout=30.0)
                if status != 'ok':
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s -> %s' % (
                        _("The macro stopped"), line, code_text(grbl_reply_error(text), self._grbl_grblhal) or status))
                    return False
            if any(re.search(r'\bG5[4-9](\.\d)?\b', line, re.IGNORECASE) for line in stream):
                self._grbl_read_active_wcs(op)
            return True

        return self._grbl_start_op('macro', task, motion=True)

    def on_spindle_button(self, checked):
        """
        The Spindle button: M3 S<RPM> or M5. The button shows the spindle state, so it changes only
        when the controller accepts the command (spindle_state_sig).
        """
        self.ui.spindle_button.setChecked(self.__dict__.get('_spindle_on', False))
        if not self._action_ok('spindle' if checked else 'spindle_off'):
            return
        cmd = 'M3 S%d' % self.ui.spindle_rpm_entry.get_value() if checked else 'M5'

        def task(op):
            return self._grbl_transact(op, cmd, timeout=30.0)[1] == 'ok'

        # a start needs a machine that is not in recovery; M5 is always allowed
        self._grbl_start_op('spindle', task, motion=checked)

    def _on_spindle_state(self, running):
        """Slot of spindle_state_sig (GUI thread): the Spindle button follows the spindle."""
        self._spindle_on = bool(running)
        button = getattr(self.__dict__.get('ui'), 'spindle_button', None)
        if button is not None:
            try:
                button.setChecked(self._spindle_on)
                button.setText(_("Spindle ON") if self._spindle_on else _("Spindle OFF"))
            except RuntimeError:
                pass  # the UI was deleted

    def _poll_idle_status(self):
        """
        Timer slot (1 s): read the status of an idle controller, so the Spindle button and the
        position follow changes that the sender did not send (alarm, E-stop, another program).
        Not in recovery: after a reset with the homing lock, GRBL reports Alarm, and a status
        query takes that as a new alarm.
        """
        monitor = self._io_monitor_active
        if (not self.__dict__.get('_plugin_active') or self.__dict__.get('_simulation') is not None
                or not self._grbl_port_ready() or self._grbl_close_pending or self._grbl_op is not None
                or self._grbl_reset_pending
                or (not monitor and (self._grbl_stream_dirty or self._grbl_recovery is not None))):
            return

        def task(op):
            # the query publishes the report it reads
            self._grbl_query_status(op, timeout=0.5, observe=monitor)

        # its own thread: in the app worker pool it can wait behind a long job while it holds the port
        if monitor:
            # read only: also in recovery and with a dirty stream (the IO tab shows the inputs in an Alarm)
            self._grbl_start_op(IO_STATUS, task, own_thread=True, read_only=True)
        else:
            self._grbl_start_op(STATUS_POLL, task, own_thread=True)

    def set_io_monitor(self, active):
        """
        Start or stop the IO monitor: the idle status poll runs every IO_STATUS_PERIOD s (else 1 s)
        and it also runs in recovery and with a dirty stream. The long transactions request the status
        themselves while it is active.
        """
        self._io_monitor_active = bool(active)
        self._status_poll_timer.setInterval(int(IO_STATUS_PERIOD * 1000) if active else 1000)

    def on_grbl_report(self):
        def task(op):
            line = self._grbl_query_status(op)
            self.console_line_sig.emit(line if line else _("No answer"))

        self._grbl_start_op('report', task)

    def on_grbl_get_parameter(self, param):
        if not self._action_ok('config'):
            return
        try:
            number = int(str(param).replace('$', '').strip())
        except ValueError:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not a valid GRBL parameter number."))
            return

        def task(op):
            text, status = self._grbl_transact(op, '$$', timeout=5.0, echo=False)
            value = parse_grbl_setting(text, number) if status == 'ok' else None
            if value is None:
                self.app.inform.emit('[ERROR_NOTCL] %s: $%d' % (_("GRBL did not report the parameter"), number))
                return
            self.console_line_sig.emit("GRBL Parameter: $%d = %s" % (number, str(value)))

        self._grbl_start_op('parameter', task)

    def _configuration_supported(self):
        return (self._grbl_port_ready() and not callable(getattr(self.grbl_ser_port, 'prepare_program', None))
                and not self._grbl_identity_pending)

    def _invalidate_configuration(self):
        # the table lives in the Configuration page of the plugin tab; it does not exist before build_ui ends
        configuration = self.ui.__dict__.get('configuration') if self.__dict__.get('ui') is not None else None
        if configuration is not None:
            configuration.invalidate()

    def _configuration_request(self, page, changes=None):
        if not self._action_ok('config'):
            return
        changes = dict(changes or {})
        identity = (self.grbl_ser_port, self._grbl_safety_generation)
        if not self._configuration_supported() or page.tool is not self:
            page.status.setText(_('Configuration requires a connected GRBL/grblHAL controller, not an E-Cut adapter.'))
            return
        if changes and (page.identity != identity or self._grbl_recovery is not None or self._grbl_reset_pending):
            page.status.setText(_('Refresh this connection first; recovery must finish before writes.'))
            return
        for number, value in changes.items():
            if (number not in page.values or not re.fullmatch(r'\d+', number) or
                    not re.fullmatch(r'[\x20-\x7e]*', value) or any(char in value for char in '!~?') or
                    validate_stream_lines(['$%s=%s' % (number, value)], *self._line_limits()) is not None):
                page.status.setText(_('Invalid setting value: control/realtime characters and multiline commands are forbidden.'))
                return
            if int(number) in (*range(0, 7), 10, 11, 12, 13, *range(20, 28), 30, 31, 32,
                               *range(100, 103), *range(110, 113), *range(120, 123), *range(130, 133)):
                try:
                    valid = math.isfinite(float(value))
                except ValueError:
                    valid = False
                if not valid:
                    page.status.setText(_('This standard GRBL setting requires a finite numeric value.'))
                    return
        epoch = page.epoch

        def task(op):
            values, outcomes, read_status = {}, {}, 'not read'
            try:
                state = parse_grbl_status_state(self._grbl_query_status(op))
                if state != 'Idle' or self._grbl_mpg_owned or self._grbl_sd_owned:
                    read_status = 'controller not idle or externally owned'
                    return
                for number, value in changes.items():
                    text, status = self._grbl_transact(op, '$%s=%s' % (number, value), timeout=5.0, echo=False)
                    outcomes[number] = (status, text.strip())
                    if status not in ('ok', 'error'):
                        read_status = status
                        return
                text, read_status = self._grbl_transact(op, '$$', timeout=5.0, echo=False)
                if read_status == 'ok':
                    values = GrblConfiguration.parse_settings(text)
                    if not values:
                        read_status = 'no settings reported'
                    with self._grbl_op_lock:
                        if self._grbl_active_locked(op):
                            self._grbl_report_inches = {'0': False, '1': True}.get(values.get('13'))
            except (serial.SerialException, OSError) as err:
                read_status = str(err)
                raise
            finally:
                self.configuration_result_sig.emit((page, epoch, op.port, op.safety_generation,
                                                     values, changes, outcomes, read_status))

        page.status.setText(_('Reading controller settings...') if not changes else _('Applying explicit changes; waiting for readback...'))
        if not self._grbl_start_op('configuration', task):
            page.status.setText(_('Controller busy or unavailable. Cached values have not changed.'))

    def _on_configuration_result(self, result):
        page, epoch, port, generation, values, changes, outcomes, status = result
        if (_qt_deleted(page) or page.tool is not self or page.epoch != epoch or
                port is not self.grbl_ser_port or generation != self._grbl_safety_generation or
                not self._grbl_port_ready()):
            return
        page.show_result(values, changes, outcomes, (port, generation), status)
        if changes:
            self._invalidate_coordinates()

    def _continuous_jog_capable(self, port):
        return (port is not None and port is self.__dict__.get('_native_jog_port')) or all(
            callable(getattr(port, name, None)) for name in ('start_jog', 'refresh_jog', 'stop_jog'))

    def _on_jog_clicked(self, button, direction):
        if self._continuous_jog_click_button is button:
            self._continuous_jog_click_button = None
            return
        self.on_grbl_jog(direction=direction)

    def _on_jog_pressed(self, button, direction):
        self._continuous_jog_click_button = None
        port = self.grbl_ser_port
        if not self.ui.continuous_jog_cb.isChecked():
            return
        self._continuous_jog_click_button = button
        if not self._continuous_jog_capable(port):
            return
        if self.ui.check_mode_cb.isChecked():
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Turn off check mode before jogging."))
            return
        op = self._grbl_reserve('continuous jog', motion=True)
        if op is None:
            return
        try:
            feed = self.ui.jog_fr_entry.get_value() * (25.4 if self.units == 'IN' else 1.0)
            step = self.ui.jog_step_entry.get_value()
            if (self.units not in ('MM', 'IN') or getattr(self.app, 'app_units', None) != self.units
                    or not all(math.isfinite(value) and value > 0 for value in (feed, step))):
                raise ValueError('Invalid jog units, step or feed')
            self._continuous_jog_units = self.units
            if port is self.__dict__.get('_native_jog_port'):
                self._start_native_jog(op, button, direction, feed)
                return
            port.start_jog('xyz'.index(direction[0]), int(direction.endswith('minus')), feed)
        except (serial.SerialException, OSError, ValueError) as error:
            self._grbl_release(op)
            self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Jog failed"), error))
            return
        self._continuous_jog = (port, button)
        self._continuous_jog_timer.start()

        def wait_for_stop():
            try:
                self._grbl_wait_idle(op)
            except (serial.SerialException, OSError) as error:
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Jog failed"), error))
            finally:
                self._grbl_release(op)

        self.app.worker_task.emit({'fcn': wait_for_stop, 'params': []})

    def _start_native_jog(self, op, button, direction, feed):
        # One 200 ms segment, then confirmed Idle: no planner/serial backlog.
        state = {'op': op, 'until': time.monotonic() + 0.15, 'stopped': False,
                 'units': self.units, 'feed': self.ui.jog_fr_entry.get_value(),
                 'step': self.ui.jog_step_entry.get_value(), 'session': self._start_seq}
        self._native_jog = state
        self._continuous_jog = (op.port, button)
        self.ui.jog_fr_entry.setEnabled(False)
        self.ui.jog_step_entry.setEnabled(False)
        self._continuous_jog_timer.start()
        distance = feed * 0.2 / 60.0 * (-1 if direction.endswith('minus') else 1)
        command = '$J=G91 G21 %s%.9f F%.9f' % (direction[0].upper(), distance, feed)

        def guard():
            if (state['stopped'] or time.monotonic() > state['until']
                    or self.units != state['units']
                    or getattr(self.app, 'app_units', None) != state['units']
                    or self._start_seq != state['session']
                    or op.safety_generation != self._grbl_safety_generation):
                raise InterruptedError('Jog hold retired')

        def worker():
            try:
                if parse_grbl_status_state(self._grbl_query_status(op, timeout=0.5)) != 'Idle':
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("The controller must be Idle before jogging."))
                    return
                while True:
                    with self._grbl_op_lock:
                        guard()
                    if self._grbl_transact(op, command, timeout=0.5, echo=False, guard=guard)[1] != 'ok':
                        break
                    deadline = time.monotonic() + 2.0
                    while True:
                        with self._grbl_op_lock:
                            guard()
                        report = self._grbl_query_status(op, timeout=0.5)
                        status = parse_grbl_status_state(report)
                        parsed = parse_grbl_status_report(report) if report else None
                        if parsed is not None:
                            self._emit_sender_status(parsed, op)
                        if status == 'Idle':
                            break
                        if status != 'Jog' or time.monotonic() >= deadline:
                            with self._grbl_op_lock:
                                self._grbl_failure_locked(op, 'probe_invalid')
                            return
                        time.sleep(0.02)
            except InterruptedError:
                pass
            except (serial.SerialException, OSError):
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
            finally:
                with self._grbl_op_lock:
                    if self._grbl_op is op and self.grbl_ser_port is op.port:
                        if not state['stopped']:
                            state['stopped'] = True
                            if not self._grbl_write_realtime_locked(b'\x85'):
                                self._grbl_failure_locked(op, 'port_error')
                # Drain the pending acknowledgement and confirm cancellation before reuse.
                try:
                    deadline = time.monotonic() + 2.0
                    while True:
                        with self._grbl_op_lock:
                            if not self._grbl_active_locked(op):
                                break
                        status = parse_grbl_status_state(self._grbl_query_status(op, timeout=0.5))
                        if status == 'Idle':
                            break
                        if status != 'Jog' or time.monotonic() >= deadline:
                            with self._grbl_op_lock:
                                self._grbl_failure_locked(op, 'probe_invalid')
                            break
                        time.sleep(0.02)
                except (serial.SerialException, OSError):
                    with self._grbl_op_lock:
                        self._grbl_failure_locked(op, 'port_error')
                finally:
                    self._grbl_release(op)

        threading.Thread(target=worker, name='GrblSender-continuous-jog', daemon=True).start()

    def _stop_continuous_jog(self):
        active = self.__dict__.get('_continuous_jog')
        if active is None:
            return
        self._continuous_jog = None
        self._continuous_jog_timer.stop()
        native = self.__dict__.get('_native_jog')
        if native is not None:
            with self._grbl_op_lock:
                if not native['stopped']:
                    native['stopped'] = True
                    if self.grbl_ser_port is active[0] and not self._grbl_write_realtime_locked(b'\x85'):
                        self._grbl_failure_locked(native['op'], 'port_error')
            self.ui.jog_fr_entry.setEnabled(True)
            self.ui.jog_step_entry.setEnabled(True)
            self._native_jog = None
            return
        active[0].stop_jog()

    def _refresh_continuous_jog(self):
        active = self._continuous_jog
        if active is None:
            return
        port, button = active
        dock = self.app.ui.grbl_console_dock
        console = dock.widget()
        jog_visible = isinstance(console, GrblConsole) and console.jog_block_visible()
        native = self.__dict__.get('_native_jog')
        if (self.grbl_ser_port is not port or not self._grbl_connected or not button.isDown()
                or QtWidgets.QApplication.applicationState() != QtCore.Qt.ApplicationState.ApplicationActive
                or not button.window().isActiveWindow() or not jog_visible
                or not self.ui.continuous_jog_cb.isChecked()
                or self.units != self._continuous_jog_units
                or getattr(self.app, 'app_units', None) != self._continuous_jog_units
                or (native is not None and (
                    self._grbl_op is not native['op'] or native['stopped']
                    or time.monotonic() > native['until']
                    or self.units != native['units']
                    or self.ui.jog_fr_entry.get_value() != native['feed']
                    or self.ui.jog_step_entry.get_value() != native['step']))
                or (native is None and not port.refresh_jog())):
            self._stop_continuous_jog()
        elif native is not None:
            with self._grbl_op_lock:
                native['until'] = time.monotonic() + 0.15

    def _on_console_dock_visibility(self, visible):
        if not visible:
            self._stop_continuous_jog()
        self.sender_io.evaluate()

    def _on_console_page_changed(self, key):
        """The IO tab reads only while it is current. The camera runs while its pane or window is shown."""
        self.sender_io.evaluate()

    def key_jog(self, direction, pressed):
        """A jog key: with Continuous jog on a capable port the press starts the jog and the release stops it, else a
        press is 1 step."""
        holds = self.__dict__.setdefault('_key_holds', {})
        if not pressed:
            hold = holds.pop(direction, None)
            if hold is not None:
                hold.release()
                self._stop_continuous_jog()
            return
        if direction in holds:
            return
        if self.ui.continuous_jog_cb.isChecked() and self._continuous_jog_capable(self.grbl_ser_port):
            hold = SenderKeys.KeyHold(self.app.ui.grbl_console_dock.window())
            holds[direction] = hold
            self._on_jog_pressed(hold, direction)
        else:
            self.on_grbl_jog(direction)

    def toggle_jog_page(self):
        """Tab while the Sender is the Plugin tab: show the dock with the jog block open; a shown dock collapses or expands the
        block and stays visible."""
        dock = self.app.ui.grbl_console_dock
        console = self._console()
        if dock.isVisible() and console.jog_block_visible():
            console.collapse_jog()
            return
        self.show_jog_page()

    def show_jog_page(self):
        """Show the dock and open its jog block without toggling an already visible dock."""
        dock = self.app.ui.grbl_console_dock
        console = self._console()
        dock.show()
        dock.raise_()
        console.expand_jog()

    def on_grbl_jog(self, direction=None):
        if direction is None or not self._action_ok('jog'):
            return

        step = self.ui.jog_step_entry.get_value()
        feedrate = self.ui.jog_fr_entry.get_value()
        units, port, session = self.units, self.grbl_ser_port, self._start_seq
        if (units not in ('MM', 'IN') or getattr(self.app, 'app_units', None) != units
                or not all(math.isfinite(value) and value > 0 for value in (step, feedrate))):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Jog requires positive finite step and feed."))
            return
        decimals = self.app.decimals
        if float('%.*f' % (decimals, step)) == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % (
                _("The jog step is below the resolution %s.") % ('%.*f' % (decimals, 10 ** -decimals))))
            return
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        travelz = float(
            self.app.options.get(
                "tools_grbl_travelz",
                app_defaults.get("tools_grbl_travelz", AppDefaults.factory_defaults.get("tools_grbl_travelz")))
        ) / (25.4 if units == 'IN' else 1.0)  # stored in mm
        units_cmd = {'IN': 'G20', 'MM': 'G21'}[units]

        step_text = '%.*f' % (decimals, step)
        axis_words = {
            'xplus': 'X%s' % step_text,
            'xminus': 'X-%s' % step_text,
            'yplus': 'Y%s' % step_text,
            'yminus': 'Y-%s' % step_text,
            'zplus': 'Z%s' % step_text,
            'zminus': 'Z-%s' % step_text,
            'xminus_yplus': 'X-%s Y%s' % (step_text, step_text),
            'xplus_yplus': 'X%s Y%s' % (step_text, step_text),
            'xminus_yminus': 'X-%s Y-%s' % (step_text, step_text),
            'xplus_yminus': 'X%s Y-%s' % (step_text, step_text),
        }
        if direction == 'origin':
            commands = [
                "$J=G90 %s Z%s F%s" % (units_cmd, '%.*f' % (decimals, travelz), str(feedrate)),
                "$J=G90 %s X0.0 Y0.0 F%s" % (units_cmd, str(feedrate)),
            ]
        elif direction in axis_words:
            commands = ["$J=G91 %s %s F%s" % (units_cmd, axis_words[direction], str(feedrate))]
        else:
            return

        if self.__dict__.get('_grbl_tool_change_pending'):
            if direction == 'origin':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Use single-axis jogs during a native tool change."))
            else:
                self._grbl_queue_tool_command(commands[0])
            return

        def task(op):
            def guard():
                if (self.units != units or getattr(self.app, 'app_units', None) != units
                        or self.grbl_ser_port is not port or op.port is not port
                        or self._start_seq != session
                        or op.safety_generation != self._grbl_safety_generation):
                    raise InterruptedError('Jog context changed')

            for idx, cmd in enumerate(commands):
                try:
                    text, status = self._grbl_transact(op, cmd, timeout=10.0, echo=False, guard=guard)
                except InterruptedError:
                    return False
                if status != 'ok':
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s -> %s' % (
                        _("Jog failed"), cmd, code_text(grbl_reply_error(text), self._grbl_grblhal) or status))
                    return False
                if idx < len(commands) - 1 and not self._grbl_wait_idle(op):
                    return False

        self._grbl_start_op('jog', task, motion=True)

    # #############################################################################################################
    # Drill-referenced camera calibration
    # #############################################################################################################

    # a fresh report can need a few frames before it carries WCO; the poll stays inside the operation
    CAMERA_REPORT_DEADLINE = 1.5
    CAMERA_REPORT_PAUSE = 0.05

    @staticmethod
    def _report_machine_position(report, last_wco=None):
        """:return: the machine XYZ of a status report, or None; work is never assumed to be machine."""
        if report is None:
            return None
        if 'mpos' in report:
            return report['mpos']
        wco = report.get('wco', last_wco)
        if 'wpos' in report and wco is not None:
            # GRBL reports work position as machine position minus the work offset
            return tuple(m + o for m, o in zip(report['wpos'], wco))
        return None

    @staticmethod
    def _fresh_report_wco(report):
        """
        :return: the work offset of THIS report, or None when it does not carry one.

        The report's own WCO wins; otherwise MPos and WPos in the same report give it.
        A cached offset is never substituted: registration must not invent a frame.
        """
        if not isinstance(report, dict):
            return None
        raw = report.get('wco')
        if raw is None and 'mpos' in report and 'wpos' in report:
            try:
                raw = tuple(float(m) - float(w) for m, w in zip(report['mpos'], report['wpos']))
            except (TypeError, ValueError):
                return None
        if raw is None:
            return None
        try:
            wco = tuple(float(value) for value in raw[:2])
        except (TypeError, ValueError):
            return None
        return wco if all(math.isfinite(value) for value in wco) else None

    def _camera_report_scale(self):
        """:return: the factor from the GRBL report units to the application units, or None."""
        inches = getattr(self, '_grbl_report_inches', None)
        if inches is None or str(getattr(self, 'units', '')).upper() not in ('MM', 'IN'):
            return None
        return grbl_report_scale(self.units, inches)

    def _camera_settings(self):
        """:return: (camera identity, rotation, mirror) of the Camera page."""
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if camera_ui is None:
            return '', 0, False
        return camera_ui.current_camera_id(), camera_ui.rotation(), camera_ui.mirror()

    def _camera_tolerances(self):
        """:return: (repeatability, viewing Z) limits selected by the user, in application units."""
        camera_ui = self.ui.camera_widget
        return camera_ui.verify_tolerance(), camera_ui.view_tolerance()

    def _camera_preview_usable(self):
        """:return: True only while the camera is delivering current frames."""
        camera_ui = getattr(self.ui, 'camera_widget', None)
        return bool(camera_ui is not None and camera_ui.preview_usable())

    def _camera_state_for(self, units, camera_id, rotation, mirror, machine_z=None):
        tolerance, view_tolerance = self._camera_tolerances()
        return self.camera_calibration.state(
            self._camera_session_id, units, camera_id, rotation, mirror, machine_z=machine_z,
            view_tolerance=view_tolerance, tolerance=tolerance)

    def _camera_invalidate_requests(self):
        """A queued camera answer belongs to the old state; a newer request supersedes it."""
        self.__dict__['_camera_request_id'] = self.__dict__.get('_camera_request_id', 0) + 1
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if camera_ui is not None:
            try:
                camera_ui.set_busy(False)
            except RuntimeError:
                pass  # the Qt widget was deleted while the application closes

    def _camera_context_changed(self, reason):
        """Tell the registration tab that the machine reference is not the previewed one anymore."""
        try:
            self.camera_context_changed_sig.emit(str(reason))
        except (AttributeError, RuntimeError):
            pass  # a test double or a tool built without the Qt part

    def _on_camera_preview_toggled(self, checked):
        """The Preview button opens the camera pane beside the plot (or shows it); switching it off closes the pane."""
        if checked:
            if self.ui.camera_widget.camera is not None:    # a failed start leaves no camera: no pane
                self.camera_pane.open()
        else:
            self.camera_pane.close()

    def _camera_stop_preview(self):
        pane = self.__dict__.get('camera_pane')
        if pane is not None:
            pane.close()
        camera_ui = getattr(getattr(self, 'ui', None), 'camera_widget', None)
        if camera_ui is not None:
            try:
                camera_ui.stop_preview()
            except (AttributeError, RuntimeError):
                pass

    def _camera_new_session(self, reason=''):
        """A new connection or reference: pending captures and the session verification are old."""
        self.__dict__['_camera_session_id'] = self.__dict__.get('_camera_session_id', 0) + 1
        self.__dict__['_camera_last_z'] = None
        self._camera_invalidate_requests()
        self._camera_context_changed(str(reason or 'session'))
        calibration = self.__dict__.get('camera_calibration')
        if calibration is None:
            # a test double or a tool built without the camera state
            return
        had_pending = calibration.pending_spindle is not None
        was_verified = calibration.verified
        calibration.start_session(self.__dict__['_camera_session_id'])
        self._camera_refresh_ui()
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if camera_ui is not None and had_pending:
            camera_ui.set_message(_("The session or reference changed. Capture the spindle reference again."), error=True)
        elif camera_ui is not None and was_verified:
            camera_ui.set_message(_("The session or reference changed. Verify the saved calibration again."), error=True)

    def _camera_refresh_ui(self):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        calibration = self.__dict__.get('camera_calibration')
        if camera_ui is None or calibration is None:
            return
        try:
            camera_id, rotation, mirror = self._camera_settings()
            state, reason = self._camera_state_for(
                self.units, camera_id, rotation, mirror, getattr(self, '_camera_last_z', None))
            camera_ui.set_calibration_state(
                state, reason, calibration.offset, calibration.view_z, calibration.has_calibration)
        except RuntimeError:
            # the Qt widget was deleted while the application closes
            pass

    @staticmethod
    def _camera_reason_text(reason):
        messages = {
            'no_report': _("No fresh status report from the controller."),
            'not_idle': _("The machine must be idle and stationary before a capture."),
            'units_unknown': _("The application units are not known."),
            'report_units_unknown': _("The GRBL report units ($13) are not known. Reconnect."),
            'no_machine_position': _(
                "The status report has neither a machine position nor a current work offset. Try again."),
            'invalid_position': _("The controller reported an invalid position."),
            'no_spindle_reference': _("Capture the spindle reference first."),
            'capture_sequence_changed': _("The camera, orientation or units changed. Capture the sequence again."),
            'no_camera': _("Select a camera before capturing."),
            'not_connected': _("Connect to the controller first."),
            'camera_changed': _("The camera or orientation differs from the saved calibration."),
            'units_changed': _("The units differ from the saved calibration."),
            'view_z_mismatch': _("The camera is not at the calibrated viewing Z."),
            'not_verified_this_session': _("Verify the saved calibration at the marked point first."),
            'repeatability_failed': _("The marked point does not repeat within the tolerance."),
            'tolerance_changed': _("The verification tolerance changed. Verify the saved calibration again."),
            'no_calibration': _("No camera calibration is saved."),
            'no_fresh_z': _("A fresh machine Z is required for a camera measurement."),
            'invalid_tolerance': _("The selected tolerance is not a positive number."),
            'camera_preview_unavailable': _("Start the live camera preview and wait for an image first."),
            'stale_result': _("The camera answer belongs to an older state and was discarded."),
            'no_work_offset': _(
                "The controller did not report the current work offset in time. Try again; if it persists check "
                "that the status report carries WCO."),
            'work_frame_required': _("The camera observation is not in the work frame. Reconnect and observe again."),
        }
        return messages.get(reason, _("The camera operation was refused."))

    def _camera_message(self, text, error=False):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if camera_ui is not None:
            camera_ui.set_message(text, error=error)
        self.app.inform.emit(('[ERROR_NOTCL] ' if error else '[success] ') + text)

    def _camera_fresh_machine_xyz(self, payload):
        """:return: (machine XYZ in application units, None) or (None, reason code)."""
        report = payload.get('report')
        if report is None:
            return None, 'no_report'
        if str(report.get('state', '')).split(':')[0] != 'Idle':
            return None, 'not_idle'
        units = str(payload.get('units', '')).upper()
        if units not in ('MM', 'IN'):
            return None, 'units_unknown'
        inches = payload.get('report_inches')
        if inches is None:
            return None, 'report_units_unknown'
        machine = self._report_machine_position(report)
        if machine is None:
            return None, 'no_machine_position'
        try:
            scale = grbl_report_scale(units, inches)
            machine = tuple(float(value) * scale for value in machine)
        except (TypeError, ValueError):
            return None, 'invalid_position'
        if any(not math.isfinite(value) for value in machine):
            return None, 'invalid_position'
        return machine, None

    def _camera_wait_report(self, op, need_wco, deadline=None):
        """
        A fresh status report from bounded, non-motion polling inside the current operation.

        GRBL omits WCO from most status frames; the frame contract needs 1 report that carries it,
        or one whose MPos and WPos give it. No cached offset is ever substituted.
        """
        end = time.monotonic() + (self.CAMERA_REPORT_DEADLINE if deadline is None else deadline)
        last = None
        while True:
            if op.cancel.is_set() or op.failed:
                return None
            remaining = end - time.monotonic()
            if remaining <= 0.0:
                return last
            line = self._grbl_query_status(op, timeout=min(0.5, remaining))
            if op.cancel.is_set() or op.failed:
                return None
            report = parse_grbl_status_report(line) if line else None
            if report is not None:
                last = report
                if (str(report.get('state', '')).split(':')[0] != 'Idle' or
                        not need_wco or self._fresh_report_wco(report) is not None):
                    return report
            time.sleep(min(self.CAMERA_REPORT_PAUSE, max(0.0, end - time.monotonic())))

    def _camera_query(self, role):
        """Ask for a fresh status report; this is a non-motion operation. Returns its request token."""
        camera_id, rotation, mirror = self._camera_settings()
        units = self.units
        report_inches = getattr(self, '_grbl_report_inches', None)
        session_id = self._camera_session_id
        request_id = self.__dict__.get('_camera_request_id', 0) + 1
        self.__dict__['_camera_request_id'] = request_id
        need_wco = role in ('observe', 'reference')

        def task(op):
            report = self._camera_wait_report(op, need_wco)
            self.camera_capture_sig.emit({
                'role': role, 'report': report, 'session': session_id, 'units': units,
                'report_inches': report_inches, 'camera_id': camera_id, 'rotation': rotation,
                'mirror': mirror, 'wco': None, 'request': request_id,
            })

        return request_id if self._grbl_start_op('camera', task) else None

    def _camera_capture(self, role):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if not self._grbl_port_ready():
            self._camera_message(self._camera_reason_text('not_connected'), error=True)
            return
        if getattr(self, '_grbl_report_inches', None) is None:
            self._camera_message(self._camera_reason_text('report_units_unknown'), error=True)
            return
        if str(getattr(self, 'units', '')).upper() not in ('MM', 'IN'):
            self._camera_message(self._camera_reason_text('units_unknown'), error=True)
            return
        if not self._camera_settings()[0]:
            self._camera_message(self._camera_reason_text('no_camera'), error=True)
            return
        if role == 'camera' and not self._camera_preview_usable():
            self._camera_message(self._camera_reason_text('camera_preview_unavailable'), error=True)
            return
        if camera_ui is not None:
            camera_ui.set_busy(True)
        if not self._camera_query(role) and camera_ui is not None:
            camera_ui.set_busy(False)

    def on_camera_capture_spindle(self):
        self._camera_capture('spindle')

    def on_camera_capture_camera(self):
        self._camera_capture('camera')

    def on_camera_verify(self):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if not self._grbl_port_ready():
            self._camera_message(self._camera_reason_text('not_connected'), error=True)
            return
        if not self.camera_calibration.has_calibration:
            self._camera_message(self._camera_reason_text('no_calibration'), error=True)
            return
        if not self._camera_preview_usable():
            self._camera_message(self._camera_reason_text('camera_preview_unavailable'), error=True)
            return
        if camera_ui is not None:
            camera_ui.set_busy(True)
        if not self._camera_query('verify') and camera_ui is not None:
            camera_ui.set_busy(False)

    def on_camera_clear(self):
        self._camera_invalidate_requests()
        self.camera_calibration.reset()
        self._camera_last_z = None
        self.camera_calibration.start_session(self._camera_session_id)
        self.camera_calibration.save(self.app.options)
        self._camera_refresh_ui()
        self._camera_context_changed('calibration')
        self._camera_message(_("Camera calibration cleared."))

    def _camera_settings_changed(self, *args):
        self._camera_invalidate_requests()
        camera_id, rotation, mirror = self._camera_settings()
        self.camera_calibration.settings_changed(camera_id, rotation, mirror)
        self._camera_refresh_ui()
        self._camera_context_changed('camera')

    def _camera_tolerances_changed(self, *args):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if camera_ui is not None:
            camera_ui.remember_settings()
        self.camera_calibration.tolerances_changed(*self._camera_tolerances())
        self._camera_invalidate_requests()
        self._camera_refresh_ui()

    def _camera_preview_available(self, available):
        """A stopped or failed preview cannot support a camera-centered measurement."""
        if not available:
            self._camera_invalidate_requests()

    def _camera_payload_current(self, payload):
        """:return: True only while the answer belongs to this request, session and settings."""
        if not payload or payload.get('request') != self.__dict__.get('_camera_request_id'):
            return False
        if payload.get('session') != self.__dict__.get('_camera_session_id'):
            return False
        if self.__dict__.get('_grbl_connected') is False:
            return False
        if str(payload.get('units', '')).upper() != str(getattr(self, 'units', '')).upper():
            return False
        if str(payload.get('units', '')).upper() != str(self.app.app_units).upper():
            return False
        if payload.get('report_inches') != getattr(self, '_grbl_report_inches', None):
            return False
        camera_id, rotation, mirror = self._camera_settings()
        if (str(payload.get('camera_id', '')) != str(camera_id) or
                int(payload.get('rotation', 0) or 0) != int(rotation) or
                bool(payload.get('mirror', False)) != bool(mirror)):
            return False
        return True

    def _camera_emit_result(self, payload, role, result):
        """Emit a registration answer with the request token, so an old answer can be discarded."""
        if payload and payload.get('request') is not None:
            result['request'] = payload.get('request')
        if role == 'observe':
            self.camera_observation_sig.emit(result)
        elif role == 'reference':
            self.camera_reference_sig.emit(result)
        else:
            self._camera_message(self._camera_reason_text(result.get('reason')), error=True)
        return result

    def _on_camera_capture(self, payload):
        camera_ui = getattr(self.ui, 'camera_widget', None)
        if not payload:
            return
        role = payload.get('role')
        if str(self.app.app_units).upper() != str(self.units).upper():
            self._camera_new_session('units_changed')
        if not self._camera_payload_current(payload):
            if role in ('observe', 'reference'):
                state, _state_reason = self._camera_state_for(self.units, *self._camera_settings())
                self._camera_emit_result(
                    payload, role, {'ok': False, 'state': state, 'reason': 'stale_result'})
            return
        if camera_ui is not None:
            camera_ui.set_busy(False)

        if role in ('camera', 'verify', 'observe') and not self._camera_preview_usable():
            # the preview can stop while the status query is in flight
            if role == 'observe':
                state, _state_reason = self._camera_state_for(self.units, *self._camera_settings())
                self._camera_emit_result(
                    payload, role, {'ok': False, 'state': state, 'reason': 'camera_preview_unavailable'})
            else:
                self._camera_message(self._camera_reason_text('camera_preview_unavailable'), error=True)
            return

        machine, reason = self._camera_fresh_machine_xyz(payload)
        if machine is None:
            if role in ('observe', 'reference'):
                state, _state_reason = self._camera_state_for(
                    str(payload.get('units', self.units)).upper(), *self._camera_settings())
                self._camera_emit_result(
                    payload, role, {'ok': False, 'state': state, 'reason': reason})
                return
            self._camera_message(self._camera_reason_text(reason), error=True)
            return
        units = str(payload.get('units')).upper()
        camera_id = payload.get('camera_id', '')
        rotation = payload.get('rotation', 0)
        mirror = payload.get('mirror', False)
        session_id = payload.get('session')
        tolerance, view_tolerance = self._camera_tolerances()

        if role == 'observe':
            self._publish_camera_observation(payload, machine, tolerance, view_tolerance)
            return

        if role == 'reference':
            self._publish_camera_reference(payload, machine, tolerance, view_tolerance)
            return

        if role == 'verify':
            ok, reason, distance = self.camera_calibration.verify(
                (machine[0], machine[1]), machine[2], units, session_id, camera_id, rotation, mirror,
                tolerance=tolerance, view_tolerance=view_tolerance)
            self._camera_last_z = machine[2]
            self._camera_refresh_ui()
            if ok:
                self._camera_message(_(
                    "Repeatability verified: the predicted spindle position is %.*f %s from the first reference.")
                    % (self.decimals, distance, units))
            else:
                self._camera_message(self._camera_reason_text(reason), error=True)
            return

        ok, reason = self.camera_calibration.capture(
            role, machine, units, session_id, camera_id, rotation, mirror)
        self._camera_last_z = machine[2]
        if not ok:
            self._camera_message(self._camera_reason_text(reason), error=True)
            self._camera_refresh_ui()
            return
        if role == 'camera':
            self.camera_calibration.save(self.app.options)
            self._camera_context_changed('calibration')
            text = _("Calibration saved. Offset X %.*f  Y %.*f, viewing Z %.*f.") % (
                self.decimals, self.camera_calibration.offset[0],
                self.decimals, self.camera_calibration.offset[1],
                self.decimals, self.camera_calibration.view_z)
        else:
            text = _("Spindle reference captured at X %.*f  Y %.*f. Retract, set the camera viewing height "
                     "and center the same mark.") % (
                         self.decimals, machine[0], self.decimals, machine[1])
        self._camera_refresh_ui()
        self._camera_message(text)

    def _publish_camera_observation(self, payload, machine, tolerance, view_tolerance):
        units = str(payload.get('units', self.units)).upper()
        camera_id = payload.get('camera_id', '')
        rotation = payload.get('rotation', 0)
        mirror = payload.get('mirror', False)
        try:
            scale = grbl_report_scale(units, payload.get('report_inches'))
        except (TypeError, ValueError):
            self._camera_emit_result(
                payload, 'observe', {'ok': False, 'state': CAMERA_STALE, 'reason': 'report_units_unknown'})
            return
        raw_wco = self._fresh_report_wco(payload.get('report'))
        if raw_wco is None:
            state, _reason = self._camera_state_for(units, camera_id, rotation, mirror)
            self._camera_emit_result(
                payload, 'observe', {'ok': False, 'state': state, 'reason': 'no_work_offset'})
            return
        wco = (raw_wco[0] * scale, raw_wco[1] * scale)
        result = self.camera_calibration.observe(
            (machine[0], machine[1]), machine[2], units, payload.get('session'),
            camera_id, rotation, mirror, wco=wco, tolerance=tolerance, view_tolerance=view_tolerance)
        self._camera_last_z = machine[2]
        self._camera_emit_result(payload, 'observe', result)

    def _publish_camera_reference(self, payload, machine, tolerance, view_tolerance):
        """A fresh no-motion check that the work frame and the calibration are still the previewed ones."""
        units = str(payload.get('units', self.units)).upper()
        camera_id = payload.get('camera_id', '')
        rotation = payload.get('rotation', 0)
        mirror = payload.get('mirror', False)
        try:
            scale = grbl_report_scale(units, payload.get('report_inches'))
        except (TypeError, ValueError):
            self._camera_emit_result(
                payload, 'reference', {'ok': False, 'state': CAMERA_STALE, 'reason': 'report_units_unknown'})
            return
        raw_wco = self._fresh_report_wco(payload.get('report'))
        if raw_wco is None:
            self._camera_emit_result(
                payload, 'reference',
                {'ok': False, 'state': self._camera_state_for(units, camera_id, rotation, mirror)[0],
                 'reason': 'no_work_offset'})
            return
        wco = (raw_wco[0] * scale, raw_wco[1] * scale)
        result = self.camera_calibration.observe(
            (machine[0], machine[1]), machine[2], units, payload.get('session'),
            camera_id, rotation, mirror, wco=wco, tolerance=tolerance, view_tolerance=view_tolerance)
        self._camera_last_z = machine[2]
        self._camera_emit_result(payload, 'reference', result)

    def camera_observe(self, machine_x, machine_y, machine_z=None, wco=None):
        """
        Resolve a camera-centered point to the spindle position, using the verified calibration.

        The caller supplies a fresh machine position and Z (from a status report). A fresh WCO in
        application units may be supplied with them; the result then uses the work frame, else the
        machine frame. A cached offset is never substituted. A missing or non-finite Z is refused.

        :return: dict with ok, state, x, y, machine_x, machine_y, frame, units, view_z and
            the camera identity/orientation. ok is False unless the calibration is verified in
            this connection session at this viewing Z.
        """
        camera_id, rotation, mirror = self._camera_settings()
        units = self.units
        if not self._camera_preview_usable():
            return {'ok': False, 'state': self._camera_state_for(units, camera_id, rotation, mirror)[0],
                    'reason': 'camera_preview_unavailable'}
        tolerance, view_tolerance = self._camera_tolerances()
        return self.camera_calibration.observe(
            (machine_x, machine_y), machine_z, units, self._camera_session_id,
            camera_id, rotation, mirror, wco=wco, tolerance=tolerance, view_tolerance=view_tolerance)

    def request_camera_observation(self):
        """
        Read a fresh stationary machine position (no motion) and emit camera_observation_sig.

        Task 2 (board fiducial registration) connects to camera_observation_sig, calls this while
        the fiducial is centered under the crosshair, and receives the calibrated spindle-equivalent
        position plus the validity state.
        """
        camera_id, rotation, mirror = self._camera_settings()
        # the fresh report decides the viewing Z; a stale last Z must not refuse the request
        state, reason = self._camera_state_for(self.units, camera_id, rotation, mirror)
        if not self._grbl_port_ready():
            self.camera_observation_sig.emit({'ok': False, 'state': state, 'reason': 'not_connected'})
            return
        if not self.camera_calibration.has_calibration:
            self.camera_observation_sig.emit({'ok': False, 'state': CAMERA_NONE, 'reason': 'no_calibration'})
            return
        if not self._camera_preview_usable():
            self.camera_observation_sig.emit(
                {'ok': False, 'state': state, 'reason': 'camera_preview_unavailable'})
            return
        if getattr(self, '_grbl_report_inches', None) is None:
            self.camera_observation_sig.emit({'ok': False, 'state': state, 'reason': 'report_units_unknown'})
            return
        if state != 'verified':
            self.camera_observation_sig.emit({'ok': False, 'state': state, 'reason': reason})
            return
        request = self._camera_query('observe')
        if request is None:
            self.camera_observation_sig.emit({'ok': False, 'state': state, 'reason': 'not_connected'})
            return
        self.camera_request_sig.emit('observe', request)

    def request_camera_reference(self):
        """
        Read a fresh stationary reference (no motion) before applying a board registration.

        The answer carries the current work offset, session, units, camera settings and
        calibration identity so a fit made under another reference can not be applied.
        """
        camera_id, rotation, mirror = self._camera_settings()
        state, reason = self._camera_state_for(self.units, camera_id, rotation, mirror)
        if not self._grbl_port_ready():
            self.camera_reference_sig.emit({'ok': False, 'state': state, 'reason': 'not_connected'})
            return
        if not self.camera_calibration.has_calibration:
            self.camera_reference_sig.emit({'ok': False, 'state': CAMERA_NONE, 'reason': 'no_calibration'})
            return
        if getattr(self, '_grbl_report_inches', None) is None:
            self.camera_reference_sig.emit({'ok': False, 'state': state, 'reason': 'report_units_unknown'})
            return
        if state != 'verified':
            self.camera_reference_sig.emit({'ok': False, 'state': state, 'reason': reason})
            return
        request = self._camera_query('reference')
        if request is None:
            self.camera_reference_sig.emit({'ok': False, 'state': state, 'reason': 'not_connected'})
            return
        self.camera_request_sig.emit('reference', request)

    # ------------------------------------------------------------------
    # Calibrated manual tool change (GRBL fixed or removable touchplate)
    # ------------------------------------------------------------------
    def _toolchange_ui(self):
        return getattr(self.ui, 'toolchange_widget', None)

    def _toolchange_mode(self):
        return plate_mode(self.app.options)

    def _toolchange_settings(self):
        return ToolChangeSettings.from_options(self.app.options, units=str(self.units).upper())

    def _toolchange_levelling_active(self):
        """True if a height map (bound, imported or in progress) is present."""
        return bool(self.__dict__.get('al_heights_valid', False) or
                    self.__dict__.get('_al_map_frame', None) is not None or
                    self.__dict__.get('_al_map_imported', False))

    def _toolchange_map_state(self, units):
        """
        :return: None if no height map is present, else {'ok', 'reason', 'frame'}.
            A map is provably usable when it was probed in this application, bound to a
            known frame, complete, and in the application units. An imported map or a
            map without a bound frame has no verified provenance and is refused.
        """
        frame = self.__dict__.get('_al_map_frame')
        valid = bool(self.__dict__.get('al_heights_valid', False))
        imported = bool(self.__dict__.get('_al_map_imported', False))
        if not valid and frame is None and not imported:
            return None
        if not valid or not self._height_map_is_complete():
            return {'ok': False, 'reason': 'map_incomplete', 'frame': frame}
        if imported or frame is None:
            return {'ok': False, 'reason': 'map_provenance', 'frame': frame}
        if str(self.__dict__.get('_al_data_units') or '').upper() != str(units).upper():
            return {'ok': False, 'reason': 'map_units', 'frame': frame}
        try:
            if (len(frame) != 3 or len(frame[1]) != 3 or
                    not all(math.isfinite(float(value)) for value in frame[1])):
                raise ValueError('invalid frame')
        except (TypeError, ValueError):
            return {'ok': False, 'reason': 'map_provenance', 'frame': None}
        return {'ok': True, 'reason': None, 'frame': frame}

    @staticmethod
    def _toolchange_frame_epsilon(report_inches, scale):
        return 2.0 * (0.0001 if report_inches else 0.001) * scale + 0.00005

    def _toolchange_map_matches_reference(self, frame, reference, units):
        """Compare a bound map frame (raw report units) with the fixed-plate reference (app units)."""
        if not frame or not reference or 'report_inches' not in reference:
            return False
        report_inches = bool(frame[2])
        if str(frame[0]) != str(reference.get('wcs')):
            return False
        if reference['report_inches'] is None or report_inches != bool(reference['report_inches']):
            return False
        scale = grbl_report_scale(units, report_inches)
        epsilon = self._toolchange_frame_epsilon(report_inches, scale)
        return all(abs(float(raw) * scale - float(reference[key])) <= epsilon
                   for raw, key in zip(frame[1], ('offset_x', 'offset_y', 'offset_z')))

    @staticmethod
    def _toolchange_map_matches_live(frame, map_frame):
        """Compare a bound map frame (raw report units) with a fresh _toolchange_frame()."""
        if not map_frame or frame is None:
            return False
        if str(map_frame[0]) != str(frame['wcs']) or bool(map_frame[2]) != bool(frame['report_inches']):
            return False
        return all(abs(float(raw) * frame['scale'] - app) <= frame['epsilon']
                   for raw, app in zip(map_frame[1], frame['offset']))

    def _toolchange_map_unchanged(self, context):
        """The map captured when the operation started must not change under it."""
        return (self.__dict__.get('_al_map_frame') == context.get('map_frame') and
                bool(self.__dict__.get('_al_map_imported', False)) == context.get('map_imported') and
                bool(self.__dict__.get('al_heights_valid', False)) == context.get('map_heights_valid') and
                self.__dict__.get('_al_generation', 0) == context.get('map_generation') and
                self.__dict__.get('_al_data_units') == context.get('map_units'))

    def _toolchange_invalidate_map(self, frame=None):
        """Retire the height map (and the levelled job) when its frame can not be proven."""
        current = self.__dict__.get('_al_map_frame')
        if frame is not None and current != frame:
            return False
        if (current is None and not self.__dict__.get('al_heights_valid', False)
                and not self.__dict__.get('_al_map_imported', False)):
            return False
        self.__dict__['al_heights_valid'] = False
        self.__dict__['_al_map_frame'] = None
        self.__dict__['_al_map_imported'] = False
        self.__dict__['job_map_generation'] = None
        self._al_bump_generation()
        self._invalidate_start_requests()
        try:
            self._update_3d_height_map()
        except (AttributeError, RuntimeError):
            pass  # the Qt view was deleted while the application closes
        return True

    def _toolchange_reconcile_map(self, old_offset_z, new_offset_z):
        """
        Bind the map to a verified fixed-reference tool correction. The surface contact
        and WCS move by the same tool-length delta: board work heights stay unchanged.
        """
        frame = self.__dict__.get('_al_map_frame')
        if frame is None or not self.__dict__.get('al_heights_valid', False):
            return False
        if self._toolchange_mode() != PLATE_FIXED:
            self._toolchange_invalidate_map(frame)
            return False
        units = str(self.units).upper()
        try:
            delta = float(new_offset_z) - float(old_offset_z)
            scale = grbl_report_scale(units, bool(frame[2]))
            if not math.isfinite(delta) or not math.isfinite(scale) or scale <= 0.0:
                raise ValueError('invalid offset')
        except (TypeError, ValueError):
            self._toolchange_invalidate_map(frame)
            return False
        heights = self.__dict__.get('al_voronoi_geo_storage') or {}
        if not heights or any(
                not isinstance(value.get('height'), (int, float)) or
                not math.isfinite(float(value['height'])) for value in heights.values()):
            self._toolchange_invalidate_map(frame)
            return False
        self.__dict__['_al_map_frame'] = (
            frame[0], (frame[1][0], frame[1][1], frame[1][2] + delta / scale), bool(frame[2]))
        self.__dict__['job_map_generation'] = None
        self._al_bump_generation()
        self._invalidate_start_requests()
        try:
            self._update_3d_height_map()
        except (AttributeError, RuntimeError):
            pass  # the Qt view was deleted while the application closes
        return True

    def _toolchange_message(self, text, error=False):
        ui = self._toolchange_ui()
        if ui is not None:
            try:
                ui.set_message(text, error=error)
            except (AttributeError, RuntimeError):
                pass

    def _toolchange_refresh_ui(self, message='', error=False):
        ui = self._toolchange_ui()
        measurement = self.__dict__.get('toolchange')
        if ui is None or measurement is None:
            return
        try:
            ui.set_mode(self._toolchange_mode(), self._toolchange_settings() is not None)
            ui.set_connected(self._grbl_port_ready())
            ui.set_busy(self.__dict__.get('_toolchange_busy', False))
            ui.set_state(measurement.state())
            if message:
                ui.set_message(message, error=error)
        except (AttributeError, RuntimeError):
            pass  # the Qt widget was deleted while the application closes

    def _toolchange_new_session(self, reason=''):
        """Connect, disconnect, reset or home: a fixed reference and a measurement are old."""
        was_busy = bool(self.__dict__.get('_toolchange_busy', False))
        map_frame = self.__dict__.get('_al_map_frame')
        self.__dict__['_toolchange_session_id'] = self.__dict__.get('_toolchange_session_id', 0) + 1
        self.__dict__['_toolchange_request_id'] = self.__dict__.get('_toolchange_request_id', 0) + 1
        self.__dict__['_toolchange_busy'] = False
        self.__dict__['_toolchange_action'] = None
        measurement = self.__dict__.get('toolchange')
        if measurement is not None:
            measurement.start_session(self.__dict__['_toolchange_session_id'])
        invalidated = False
        has_map = (map_frame is not None or bool(self.__dict__.get('al_heights_valid', False))
                   or bool(self.__dict__.get('_al_map_imported', False)))
        if has_map and (was_busy or reason in ('zero', 'disconnect', 'reset')):
            invalidated = self._toolchange_invalidate_map()
        if reason:
            text = _("The machine session changed. Measure the tool or calibrate the plate again.")
            if invalidated:
                text += ' ' + _("The active height map was invalidated. Probe it again.")
            self._toolchange_refresh_ui(text, error=True)

    def _toolchange_settings_changed(self):
        measurement = self.__dict__.get('toolchange')
        if measurement is None:
            return
        changed = measurement.configure(self._toolchange_mode(), self._toolchange_settings())
        if measurement.units is not None and measurement.units != str(self.app.app_units).upper():
            changed = True
        if changed:
            if self.__dict__.get('_toolchange_busy', False):
                self._hold_now()
            self._toolchange_new_session()
        message = ''
        if changed and measurement.session is not None:
            message = _("The tool-change mode or its settings changed. Measure the tool again.")
        self._toolchange_refresh_ui(message, error=bool(message))

    def _toolchange_reason_text(self, reason):
        return {
            'not_idle': _("GRBL must be Idle before a tool-change measurement."),
            'unknown_units': _("GRBL did not report setting $13 (report units). The probe was not started."),
            'modes_not_confirmed': _("The units, G90 or G94 were not confirmed by GRBL. The probe was not started."),
            'report_units_unknown': _("The GRBL report units are not known. Reconnect, then try again."),
            'unsupported_offsets': _(
                "An active G92 offset or tool length offset is set. Clear it before a calibrated tool change."),
            'invalid_frame': _("The active work coordinate system could not be read."),
            'already_triggered': _("The probe was already touching at the start. Raise the tool and try again."),
            'probe_failed': _("The probe did not reach the plate inside the configured distance."),
            'probe_invalid': _("The probe result was not plausible. The work offset was not changed."),
            'retract_failed': _("The Z retract could not be verified. No coordinate was changed."),
            'frame_changed': _("The coordinate frame changed. Check the work offset before continuing."),
            'apply_not_verified': _("GRBL did not confirm and report the new work offset. Check the value."),
            'plate_moved': _("The plate was probed too far from the fixed reference position."),
            'stale_reference': _("The fixed plate reference is from an older session. Calibrate it again."),
            'stale_measurement': _("The measurement is from an older session. Measure the tool again."),
            'no_reference': _("Calibrate the fixed plate with the reference tool first."),
            'no_measurement': _("Measure the installed tool first."),
            'settings_changed': _("The tool-change settings changed. Measure the tool again."),
            'cancelled': _("The tool-change operation was stopped."),
            'invalid_position': _("The reported position is invalid. Check the work offset before continuing."),
            'no_report': _("GRBL did not send a fresh position report. The probe was not started."),
            'timeout': _("The tool-change operation timed out. Follow the GRBL recovery message."),
            'port_error': _("The serial port failed. Check the work offset before continuing."),
            'spindle_active': _("Stop the spindle before measuring a tool."),
            'map_incomplete': _("The height map is incomplete or its heights were invalidated. Probe the map again."),
            'map_provenance': _(
                "The height map has no verified machine frame (an imported map, or one made before its frame "
                "was recorded). It can not be used for a calibrated tool change. Probe the map again."),
            'map_units': _("The units of the height map do not match the project units. Probe the map again."),
            'map_removable': _(
                "A removable plate can reset the board origin and can not preserve an active height map. "
                "Clear the map, set the board Z zero, then probe the map again."),
            'map_reference': _(
                "The height map was made in another WCS or work offset than the fixed plate reference. "
                "Calibrate the plate and probe the map again in the same reference."),
        }.get(reason, _("The tool-change operation failed: %s") % reason)

    def _toolchange_ready(self):
        """:return: (mode, settings), or None after showing why the operation can not start."""
        self._toolchange_settings_changed()
        mode = self._toolchange_mode()
        settings = self._toolchange_settings()
        if mode == PLATE_NONE:
            self._toolchange_message(_("Calibrated tool-change measurement is disabled in Preferences."), error=True)
            return None
        if settings is None:
            self._toolchange_message(
                _("The tool-change settings are invalid. Use finite, positive probe limits."), error=True)
            return None
        if not self._grbl_port_ready():
            self._toolchange_message(_("Not connected to a controller."), error=True)
            return None
        if self._ecut_settings_capable(self.grbl_ser_port):
            self._toolchange_message(
                _("Calibrated tool-change measurement supports stock GRBL only, not E-Cut."), error=True)
            return None
        if (str(getattr(self, 'units', '')).upper() not in ('MM', 'IN') or
                str(self.app.app_units).upper() != str(self.units).upper()):
            self._toolchange_message(_("The application units are not known."), error=True)
            return None
        units = str(self.units).upper()
        map_state = self._toolchange_map_state(units)
        if map_state is not None and not map_state['ok']:
            self._toolchange_message(self._toolchange_reason_text(map_state['reason']), error=True)
            return None
        if map_state is not None and mode == PLATE_REMOVABLE:
            self._toolchange_message(self._toolchange_reason_text('map_removable'), error=True)
            return None
        measurement = self.__dict__.get('toolchange')
        if (map_state is not None and mode == PLATE_FIXED and measurement is not None and
                measurement.reference is not None and
                not self._toolchange_map_matches_reference(map_state['frame'], measurement.reference, units)):
            self._toolchange_message(self._toolchange_reason_text('map_reference'), error=True)
            return None
        if self.__dict__.get('_toolchange_busy', False):
            self._toolchange_message(_("A tool-change operation is already running."), error=True)
            return None
        return mode, settings

    def on_toolchange_calibrate(self):
        if self._action_ok('probe'):
            self._toolchange_start('reference')

    def on_toolchange_measure(self):
        if self._action_ok('probe'):
            self._toolchange_start('measure')

    def _toolchange_start(self, action):
        if (self.__dict__.get('_grbl_caps') or {}).get('probe') is False:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The controller has no probe input. Probing is unavailable."))
            return
        ready = self._toolchange_ready()
        if ready is None:
            return
        mode, settings = ready
        measurement = self.toolchange
        measurement.configure(mode, settings)
        if action == 'reference' and mode != PLATE_FIXED:
            return
        if action == 'measure' and mode == PLATE_FIXED and measurement.reference is None:
            self._toolchange_message(_("Calibrate the fixed plate with the reference tool first."), error=True)
            return
        reference = dict(measurement.reference) if action == 'measure' and measurement.reference else None
        measurement.measurement = None
        measurement.applied = False
        measurement.plate_removed = False
        if action == 'reference':
            measurement.forget()
        units = str(self.units).upper()
        session = self._toolchange_session_id
        request = self.__dict__.get('_toolchange_request_id', 0) + 1
        self.__dict__['_toolchange_request_id'] = request
        self.__dict__['_toolchange_busy'] = True
        self.__dict__['_toolchange_action'] = action
        context = self._toolchange_context(mode, settings, units, session, request)
        ui = self._toolchange_ui()
        if ui is not None:
            ui.set_busy(True)
            ui.set_message(_("Probing the plate..."))

        def task(op):
            payload = self._toolchange_probe_worker(op, settings, context, reference)
            payload.update({'kind': 'toolchange', 'action': action, 'request': request,
                            'mode': mode, 'session': session, 'units': units,
                            'settings': context['settings'], 'generation': op.safety_generation,
                            'map_frame': context.get('map_frame')})
            self.toolchange_result_sig.emit(payload)
            return False  # the worker already verified Idle, including on its success path

        if not self._grbl_start_op('tool change', task, motion=True):
            self.__dict__['_toolchange_busy'] = False
            self.__dict__['_toolchange_action'] = None
            if ui is not None:
                ui.set_busy(False)

    def _toolchange_context(self, mode, settings, units, session, request):
        return {'mode': mode, 'settings': settings.signature(), 'units': units, 'session': session,
                'request': request, 'port': self.grbl_ser_port,
                'stored_units': self.app.options.get('tools_grbl_toolchange_units', 'MM'),
                'map_frame': self.__dict__.get('_al_map_frame'),
                'map_imported': bool(self.__dict__.get('_al_map_imported', False)),
                'map_heights_valid': bool(self.__dict__.get('al_heights_valid', False)),
                'map_generation': self.__dict__.get('_al_generation', 0),
                'map_units': self.__dict__.get('_al_data_units')}

    def _toolchange_guard(self, op, context):
        """No widgets: also used under the transaction's Stop lock immediately before a write."""
        settings = self._toolchange_settings()
        if (op.cancel.is_set() or op.failed or self.__dict__.get('_grbl_op') is not op or
                context['port'] is not self.grbl_ser_port or not self._grbl_connected or
                context['request'] != self._toolchange_request_id or
                context['session'] != self._toolchange_session_id or
                context['mode'] != self._toolchange_mode() or settings is None or
                context['settings'] != settings.signature() or
                context['stored_units'] != self.app.options.get('tools_grbl_toolchange_units', 'MM') or
                context['units'] != str(self.units).upper() or
                context['units'] != str(self.app.app_units).upper() or
                not self._toolchange_map_unchanged(context) or
                self._ecut_settings_capable(self.grbl_ser_port)):
            raise _GrblProbeAbort('', '', 'cancelled')

    def _toolchange_step(self, op, context, command, timeout=10.0):
        guard = lambda: self._toolchange_guard(op, context)
        text, status = self._grbl_transact(op, command, timeout=timeout, guard=guard)
        if status != 'ok':
            raise _GrblProbeAbort(command, text, status)
        guard()
        return text

    def _toolchange_recovery(self, op):
        with self._grbl_op_lock:
            # An old worker must not hold a replacement session's operation.
            self._grbl_failure_locked(op, 'probe_invalid')

    def _toolchange_frame(self, op, context, expected=None, position=None, probe_clear=False):
        """Fresh modal, offset and coherent full XYZ status evidence, never cached WCO."""
        step = lambda command: self._toolchange_step(op, context, command)
        report_setting = parse_grbl_setting(step('$$'), 13)
        if report_setting not in (0.0, 1.0):
            raise _GrblProbeAbort('$$', '', 'unknown_units')
        units = context['units']
        scale = grbl_report_scale(units, report_setting == 1.0)
        # Stock GRBL prints .001 mm or .0001 inch; allow two report quanta plus command rounding.
        epsilon = 2.0 * (0.0001 if report_setting else 0.001) * scale + 0.00005
        gc = step('$G')
        words = gc.replace('[GC:', '').replace(']', '').split()
        if 'M5' not in words or any(word in words for word in ('M3', 'M4')):
            raise _GrblProbeAbort('$G', gc, 'spindle_active')
        if 'G43.1' in words:
            raise _GrblProbeAbort('$G', gc, 'unsupported_offsets')
        units_cmd = 'G20' if units == 'IN' else 'G21'
        if (not grbl_modes_confirmed(gc, units_cmd) or
                any(word in words for word in ('G91', 'G93', 'G21' if units == 'IN' else 'G20'))):
            raise _GrblProbeAbort('$G', gc, 'modes_not_confirmed')
        wcs = parse_grbl_active_wcs(gc)
        offset_text = step('$#')
        offsets = parse_grbl_offsets(offset_text)
        if '[G92:' not in offset_text or '[TLO:' not in offset_text:
            raise _GrblProbeAbort('$#', '', 'invalid_frame')
        if wcs not in offsets or 'G92' not in offsets or 'TLO' not in offsets:
            raise _GrblProbeAbort('$#', '', 'invalid_frame')
        tlo = offsets['TLO']
        if (any(value != 0.0 for value in offsets['G92']) or
                any(value != 0.0 for value in (tlo if isinstance(tlo, tuple) else (tlo,)))):
            raise _GrblProbeAbort('$#', '', 'unsupported_offsets')
        offset = tuple(value * scale for value in offsets[wcs])
        if any(not math.isfinite(value) for value in offset):
            raise _GrblProbeAbort('$#', '', 'invalid_position')
        if expected is not None and (wcs != expected['wcs'] or
                bool(report_setting) != expected['report_inches'] or
                any(abs(a - b) > epsilon for a, b in zip(offset, expected['offset']))):
            raise _GrblProbeAbort('$#', '', 'frame_changed')
        end = time.monotonic() + self.CAMERA_REPORT_DEADLINE
        while True:
            self._toolchange_guard(op, context)
            report = parse_grbl_status_report(self._grbl_query_status(op))
            self._toolchange_guard(op, context)
            if report is None:
                raise _GrblProbeAbort('?', '', 'no_report')
            if report.get('state') != 'Idle':
                raise _GrblProbeAbort('?', '', 'not_idle')
            wco = report.get('wco')
            if wco is None and 'mpos' in report and 'wpos' in report:
                wco = tuple(m - w for m, w in zip(report['mpos'], report['wpos']))
            if wco is not None and len(wco) == 3:
                break
            if time.monotonic() >= end:
                raise _GrblProbeAbort('?', '', 'no_report')
            time.sleep(self.CAMERA_REPORT_PAUSE)
        machine = self._report_machine_position(report)
        if machine is None or any(abs(a * scale - b) > epsilon for a, b in zip(wco, offset)):
            raise _GrblProbeAbort('?', '', 'invalid_position')
        machine = tuple(value * scale for value in machine)
        if any(not math.isfinite(value) for value in machine):
            raise _GrblProbeAbort('?', '', 'invalid_position')
        if 'wpos' in report and any(abs(m - w * scale - o) > epsilon for m, w, o in
                                    zip(machine, report['wpos'], offset)):
            raise _GrblProbeAbort('?', '', 'invalid_position')
        if position is not None and any(abs(a - b) > epsilon for a, b in zip(machine, position)):
            raise _GrblProbeAbort('?', '', 'invalid_position')
        if probe_clear and 'P' in report.get('pins', ''):
            raise _GrblProbeAbort('?', '', 'already_triggered')
        return {'wcs': wcs, 'offset': offset, 'machine': machine,
                'report_inches': bool(report_setting), 'scale': scale, 'epsilon': epsilon}

    def _toolchange_probe_worker(self, op, settings, context, reference):
        """Bounded probe; a worker thread that reads no widgets. Returns a result payload."""
        evidence = {'ok': False, 'reason': 'probe_invalid'}
        motion_started = False
        step = lambda command, timeout=10.0: self._toolchange_step(op, context, command, timeout)
        try:
            self._toolchange_guard(op, context)
            state = parse_grbl_status_state(self._grbl_query_status(op))
            if state != 'Idle':
                raise _GrblProbeAbort('?', state or _("no answer"), 'not_idle')
            step('G20' if context['units'] == 'IN' else 'G21')
            step('G90')
            step('G94')
            step('G4 P0', timeout=10.0)
            frame = self._toolchange_frame(op, context, probe_clear=True)
            machine, offset = frame['machine'], frame['offset']
            if (context.get('map_frame') is not None and
                    not self._toolchange_map_matches_live(frame, context['map_frame'])):
                raise _GrblProbeAbort('$#', '', 'frame_changed')
            if reference is not None:
                if (reference.get('report_inches') != frame['report_inches'] or
                        reference['wcs'] != frame['wcs'] or any(abs(a - reference[k]) > frame['epsilon']
                        for a, k in zip(offset, ('offset_x', 'offset_y', 'offset_z')))):
                    raise _GrblProbeAbort('$#', '', 'frame_changed')
                if math.hypot(machine[0] - offset[0] - reference['x'],
                              machine[1] - offset[1] - reference['y']) > settings.xy_tolerance:
                    raise _GrblProbeAbort('?', '', 'plate_moved')
            start_work_z = machine[2] - offset[2]
            target = probe_target(start_work_z, settings)
            timeout = probe_timeout_s(start_work_z, target, settings.probe_feed)
            if not all(math.isfinite(value) for value in (start_work_z, target, timeout)):
                raise _GrblProbeAbort('', '', 'probe_invalid')
            probe_cmd = 'G38.2 Z{0:.4f} F{1:.4f}'.format(target, settings.probe_feed)
            if (float('%.4f' % target) >= start_work_z - frame['epsilon'] or
                    float('%.4f' % settings.probe_feed) <= 0.0):
                raise _GrblProbeAbort('', '', 'probe_invalid')
            motion_started = True
            text = step(probe_cmd, timeout=timeout)
            try:
                results = parse_grbl_probe_output(text)
            except ValueError:
                if ':0]' in (text or ''):
                    raise _GrblProbeAbort(probe_cmd, text, 'probe_failed')
                raise _GrblProbeAbort(probe_cmd, text, 'probe_invalid')
            if len(results) != 1:
                raise _GrblProbeAbort(probe_cmd, text, 'probe_invalid')

            contact_machine = tuple(float(value) * frame['scale'] for value in results[0])
            contact_work = (contact_machine[0] - offset[0], contact_machine[1] - offset[1],
                            contact_machine[2] - offset[2])
            start_xy = (machine[0] - offset[0], machine[1] - offset[1])
            ok, reason = check_contact(start_work_z, target, contact_work[2],
                                       (contact_work[0], contact_work[1]), start_xy, epsilon=frame['epsilon'])
            if not ok:
                raise _GrblProbeAbort(
                    probe_cmd, text, 'probe_failed' if reason == 'already_triggered' else 'probe_invalid')

            self._toolchange_frame(op, context, expected=frame, position=contact_machine)
            retract_z = min(start_work_z, contact_work[2] + settings.clearance)
            retract_z = math.floor(retract_z * 10000.0) / 10000.0
            # Never lift above the known starting height, even for an oversized clearance.
            step('G0 Z%.4f' % retract_z)
            step('G4 P0', timeout=10.0)
            self._toolchange_frame(op, context, expected=frame,
                                   position=(machine[0], machine[1], retract_z + offset[2]))
            return dict(evidence, ok=True, reason='ok',
                        contact_machine=contact_machine, contact_machine_z=contact_machine[2],
                        contact_xy=(contact_work[0], contact_work[1]),
                        offset_z=offset[2], offset_x=offset[0], offset_y=offset[1],
                        wcs=frame['wcs'], report_inches=frame['report_inches'])
        except _GrblProbeAbort as abort:
            # Recovery never retracts: failure means the frame/physical position may be unknown.
            self._grbl_handle_abort(op, abort, None, False)
            if motion_started or abort.status not in (
                    'not_idle', 'unknown_units', 'already_triggered', 'spindle_active',
                    'unsupported_offsets', 'modes_not_confirmed'):
                self._toolchange_recovery(op)
            return dict(evidence, reason=abort.status, map_may_be_stale=bool(motion_started))
        except (ValueError, TypeError, KeyError, OSError, serial.SerialException):
            self._toolchange_recovery(op)
            return dict(evidence, reason='probe_invalid', map_may_be_stale=bool(motion_started))

    def on_toolchange_apply(self):
        ready = self._toolchange_ready()
        if ready is None:
            return
        mode, settings = ready
        measurement = self.toolchange
        units = str(self.units).upper()
        session = self._toolchange_session_id
        ok, reason, expected = measurement.applied_offset(settings, session, units)
        if not ok:
            self._toolchange_refresh_ui(self._toolchange_reason_text(reason), error=True)
            return
        wcs = measurement.measurement['wcs']
        snapshot = dict(measurement.measurement)
        request = self.__dict__.get('_toolchange_request_id', 0) + 1
        self.__dict__['_toolchange_request_id'] = request
        self.__dict__['_toolchange_busy'] = True
        self.__dict__['_toolchange_action'] = 'apply'
        context = self._toolchange_context(mode, settings, units, session, request)
        ui = self._toolchange_ui()
        if ui is not None:
            ui.set_busy(True)
            ui.set_message(_("Writing and verifying the work offset..."))

        def task(op):
            payload = self._toolchange_apply_worker(op, wcs, expected, snapshot, context)
            payload.update({'kind': 'toolchange', 'action': 'apply', 'request': request,
                            'mode': mode, 'session': session, 'units': units,
                            'settings': context['settings'], 'generation': op.safety_generation,
                            'map_frame': context.get('map_frame')})
            self.toolchange_result_sig.emit(payload)
            return False

        if not self._grbl_start_op('tool change', task, motion=True):
            self.__dict__['_toolchange_busy'] = False
            self.__dict__['_toolchange_action'] = None
            if ui is not None:
                ui.set_busy(False)

    def _toolchange_apply_worker(self, op, wcs, expected_offset_z, snapshot, context):
        """Write the active WCS Z with G10 L2 and verify it against fresh reports. No motion."""
        step = lambda command, timeout=10.0: self._toolchange_step(op, context, command, timeout)
        write_attempted = False
        try:
            frame = self._toolchange_frame(op, context)
            current = frame['offset']
            if (context.get('map_frame') is not None and
                    not self._toolchange_map_matches_live(frame, context['map_frame'])):
                raise _GrblProbeAbort('$#', '', 'frame_changed')
            if (snapshot.get('report_inches') != frame['report_inches'] or wcs != frame['wcs'] or
                    any(abs(a - snapshot[k]) > frame['epsilon'] for a, k in
                        zip(current, ('offset_x', 'offset_y', 'offset_z')))):
                raise _GrblProbeAbort('$#', '', 'frame_changed')
            p_number = grbl_wcs_p_number(wcs)
            command ='G10 L2 P%d Z%.4f' % (p_number, float(expected_offset_z))
            write_attempted = True
            self._last_wco = None
            self._camera_context_changed('toolchange')
            step(command, timeout=10.0)
            expected = dict(frame, offset=(current[0], current[1], float(expected_offset_z)))
            after = self._toolchange_frame(op, context, expected=expected, position=frame['machine'])
            return {'ok': True, 'reason': 'ok', 'new_offset_z': after['offset'][2], 'wcs': wcs,
                    'old_offset_z': float(snapshot['offset_z'])}
        except _GrblProbeAbort as abort:
            if write_attempted:
                self._toolchange_recovery(op)
            return {'ok': False, 'reason': 'apply_not_verified' if write_attempted else abort.status,
                    'map_may_be_stale': bool(write_attempted)}
        except (ValueError, TypeError, KeyError, OSError, serial.SerialException):
            self._toolchange_recovery(op)
            return {'ok': False, 'reason': 'apply_not_verified', 'map_may_be_stale': bool(write_attempted)}

    def on_toolchange_acknowledge(self):
        self._toolchange_settings_changed()
        measurement = self.toolchange
        if measurement.state() != STATE_NEEDS_REMOVAL:
            return
        measurement.mark_plate_removed()
        self._toolchange_refresh_ui(_("The plate is removed. The board Z zero is ready for the job."))

    def on_toolchange_clear(self):
        if self.__dict__.get('_toolchange_busy', False):
            self._hold_now()
        self._toolchange_new_session()
        self._toolchange_refresh_ui(_("The tool-change reference was discarded."))

    def _on_toolchange_result(self, payload):
        if not payload:
            return
        measurement = self.__dict__.get('toolchange')
        if measurement is None:
            return
        if payload.get('request') != self.__dict__.get('_toolchange_request_id'):
            # a queued answer of an older request must not apply an offset or restore a state
            return
        self.__dict__['_toolchange_busy'] = False
        self.__dict__['_toolchange_action'] = None
        ui = self._toolchange_ui()
        if ui is not None:
            ui.set_busy(False)

        mode = self._toolchange_mode()
        settings = self._toolchange_settings()
        units = str(getattr(self, 'units', '')).upper()
        session = self.__dict__.get('_toolchange_session_id')
        if (payload.get('mode') != mode or payload.get('units') != units or
                payload.get('session') != session or settings is None or
                payload.get('settings') != settings.signature() or
                (payload.get('ok') and
                 payload.get('generation') != self.__dict__.get('_grbl_safety_generation', 0)) or
                str(self.app.app_units).upper() != units or
                self.__dict__.get('_grbl_connected') is False):
            measurement.forget()
            invalidated = (bool(payload.get('map_may_be_stale')) and
                           payload.get('map_frame') is not None and
                           self._toolchange_invalidate_map(payload.get('map_frame')))
            text = _("The answer is stale: the mode, the settings, the units or the session changed.")
            if invalidated:
                text += ' ' + _("The active height map was invalidated. Probe it again.")
            self._toolchange_refresh_ui(text, error=True)
            return
        if not payload.get('ok'):
            measurement.forget()
            invalidated = (bool(payload.get('map_may_be_stale')) and
                           payload.get('map_frame') is not None and
                           self._toolchange_invalidate_map(payload.get('map_frame')))
            text = self._toolchange_reason_text(payload.get('reason'))
            if invalidated:
                text += ' ' + _("The active height map was invalidated. Probe it again.")
            self._toolchange_refresh_ui(text, error=True)
            return

        action = payload.get('action')
        if action == 'reference':
            ok = measurement.record_reference(
                payload['contact_machine_z'], payload['contact_xy'], payload['offset_z'], payload['wcs'],
                units, session, settings, offset_xy=(payload['offset_x'], payload['offset_y']))
            if not ok:
                self._toolchange_refresh_ui(_("The plate reference position is invalid."), error=True)
                return
            measurement.reference['report_inches'] = payload['report_inches']
            self._toolchange_refresh_ui(
                _("Fixed plate reference recorded. Install the next tool, then measure it."))
            return
        if action == 'measure':
            ok = measurement.record_measurement(
                payload['contact_machine_z'], payload['contact_xy'], payload['offset_z'], payload['wcs'],
                units, session, offset_xy=(payload['offset_x'], payload['offset_y']))
            if not ok:
                self._toolchange_refresh_ui(_("The measured position is invalid."), error=True)
                return
            measurement.measurement['report_inches'] = payload['report_inches']
            self._toolchange_refresh_ui(_("Measurement ready. Apply it to set the work offset Z."))
            return
        if action == 'apply':
            new_offset_z = payload.get('new_offset_z')
            if new_offset_z is None or not math.isfinite(float(new_offset_z)):
                self._toolchange_refresh_ui(_("The new work offset Z is invalid."), error=True)
                return
            request = payload.get('request')
            if request is not None and self.__dict__.get('_toolchange_applied_request') == request:
                return  # a repeated delivery must not rebase the map twice
            self.__dict__['_toolchange_applied_request'] = request
            old_offset_z = payload.get('old_offset_z')
            if old_offset_z is None and measurement.measurement is not None:
                old_offset_z = measurement.measurement.get('offset_z')
            reconciled = False
            if old_offset_z is not None:
                reconciled = self._toolchange_reconcile_map(old_offset_z, new_offset_z)
            elif self.__dict__.get('_al_map_frame') is not None:
                self._toolchange_invalidate_map(self.__dict__.get('_al_map_frame'))
            measurement.mark_applied(new_offset_z)
            if mode == PLATE_FIXED:
                text = _("Work offset Z corrected to %.*f. The next tool is measured against the same plate.") % (
                    self.decimals, float(new_offset_z))
            else:
                text = _(
                    "Board Z zero set to the plate thickness. Remove the plate from the PCB, then confirm.")
            if reconciled:
                text += ' ' + _("The height map keeps its board heights in the corrected tool frame.")
            elif payload.get('map_frame') is not None:
                text += ' ' + _("The active height map was invalidated. Probe it again.")
            self._toolchange_refresh_ui(text)

    def on_wcs_selected(self, *args):
        """The WCS combo: send the selected WCS code, then read the active one back ($G)."""
        if not self._action_ok('wcs'):
            return
        wcs = self.ui.wcs_combo.currentText()

        def task(op):
            text, status = self._grbl_transact(op, wcs, timeout=5.0, echo=False)
            if status != 'ok':
                self.app.inform.emit('[ERROR_NOTCL] %s: %s -> %s' % (
                    _("WCS change failed"), wcs, grbl_reply_error(text) or status))
                return
            self._grbl_read_active_wcs(op)

        self._grbl_start_op('command', task)

    def _grbl_read_active_wcs(self, op):
        """
        Read $G into self._active_wcs. A worker thread under `op`; no widget (the next status report shows it).

        :return: the active WCS ('G54'...) or None if it could not be read.
        """
        text, status = self._grbl_transact(op, '$G', timeout=5.0, echo=False)
        try:
            if status != 'ok':
                raise ValueError(status)
            self._active_wcs = parse_grbl_active_wcs(text)
        except ValueError as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                _("Could not read the active work coordinate system"), str(err)))
            return None
        self._gc_inches = 'G20' in text.replace(']', ' ').split()
        return self._active_wcs

    def _show_active_wcs(self):
        """Show self._active_wcs on the combo and the status strip (GUI thread)."""
        wcs = self._active_wcs
        self.ui.status_strip.set_wcs(wcs)
        combo = self.ui.wcs_combo
        if wcs and combo.currentText() != wcs:
            combo.blockSignals(True)
            combo.setCurrentText(wcs)
            combo.blockSignals(False)

    def on_dro_clicked(self, axis):
        """A click on a DRO Work value: ask for the work position of that axis."""
        if not self._action_ok('zero'):
            return
        index = 'XYZA'.index(axis)
        work = self._last_work
        current = work[index] if work is not None and index < len(work) else 0.0
        value, ok = QtWidgets.QInputDialog.getDouble(
            self.app.ui, _("Set work position"), axis, current, -1e6, 1e6, self.app.decimals)
        if ok:
            self.set_work_position({axis: value})

    def on_grbl_zero(self, axis):
        axes = {'x': ('X',), 'y': ('Y',), 'z': ('Z',)}.get(axis, ('X', 'Y', 'Z'))
        self.set_work_position({letter: 0.0 for letter in axes})

    def set_work_position(self, values):
        """
        Make the current position the given work position in the active WCS (G10 L20).

        :param values: {axis letter: position in the display units of the Sender}
        """
        if not self._action_ok('zero'):
            return
        units, decimals = self.units, self.app.decimals

        def task(op):
            if self._grbl_read_active_wcs(op) is None:
                return
            # G10 L20 makes the current position the given one in the active work coordinate
            # system (G10 L2 would set the offset to machine 0)
            try:
                p_number = grbl_wcs_p_number(self._active_wcs)
            except ValueError as err:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Zero failed"), str(err)))
                return
            # the controller reads the words in its own modal units
            factor = {('MM', True): 1 / 25.4, ('IN', False): 25.4}.get((units, self._gc_inches), 1.0)
            axes = ' '.join('%s%s' % (letter, ('%.*f' % (decimals, value * factor)).rstrip('0').rstrip('.'))
                            for letter, value in values.items())
            cmd = 'G10 L20 P%d %s' % (p_number, axes)
            text, status = self._grbl_transact(op, cmd, timeout=5.0, echo=False)
            if status != 'ok':
                self.app.inform.emit('[ERROR_NOTCL] %s: %s -> %s' % (
                    _("Zero failed"), cmd, grbl_reply_error(text) or status))

        # the position is not trusted during recovery
        if self._grbl_start_op('zero', task, motion=True):
            self._invalidate_coordinates()
            # the work offset changes: the next status report that has no WCO must not use the old one
            self._last_wco = None
            self._camera_new_session('zero')
            self._toolchange_new_session('zero')

    def on_grbl_homing(self):
        if not self._action_ok('home'):
            return
        self._stop_continuous_jog()

        def task(op):
            with self._grbl_op_lock:
                pending = self._grbl_reset_pending
            if pending and not self._grbl_confirm_reset(op):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The controller reset was not confirmed. Press Reset, then Home."))
                return False
            # a home cycle can take a long time; GRBL answers 'ok' at the end
            text, status = self._grbl_transact(op, '$H', timeout=120.0)
            if status == 'ok' and self._grbl_wait_idle(op):
                with self._grbl_op_lock:
                    if not self._grbl_active_locked(op) or op.safety_generation != self._grbl_safety_generation:
                        return False
                    self._grbl_recovery = None
                    self._homed_this_session = True
                    self.app.inform.emit('[success] %s' % _("Home cycle finished."))
                return
            if 'alarm' in (text or '').lower() or status in ('timeout', 'port_error'):
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, status, text)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Home cycle failed"), text or status))

        # A pending-reset home reservation only reads confirmation before permitting $H.
        if self._grbl_start_op('home', task, motion=True, allow_in_recovery=True):
            self._invalidate_coordinates()
            self._camera_new_session('home')
            self._toolchange_new_session('home')
            self.app.inform.emit("%s" % _("The controller is doing a home cycle."))

    def _action_ok(self, action):
        """:return: True if the action is allowed now; else the reason is shown and False."""
        reason = self.action_allowed(action)
        if reason is not None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % reason)
        return reason is None

    def _machine_situation(self):
        """
        :return: the name of the first ACTION_RULES row that matches the machine now.

        A read without the operation lock: a status slot can run under the lock on the same thread, and the lock
        is not reentrant. Each value is one attribute read; a change after the read sends op_changed_sig or a
        status report, which applies the rules again. The port reservation still checks under the lock.
        """
        op = self._grbl_op
        reset_pending = self._grbl_reset_pending
        recovery = self._grbl_recovery
        phase = self._grbl_stop_phase
        if op is not None and op.name in MONITOR_OPS:
            op = None
        state = self._controller_state
        streaming = op is not None and op.name == 'stream'
        if not self._grbl_port_ready():
            return 'not_connected'
        if recovery == 'needs_reset' or phase or reset_pending:
            return 'needs_reset'
        if recovery == 'needs_home':
            return 'needs_home'
        if state == 'Alarm':
            return 'alarm'
        if op is not None and not streaming:
            return 'operation'
        if self._tool_pause is not None:
            return 'tool_pause'
        if streaming:
            if state == 'Check':
                return 'stream_check'
            if state in ('Hold', 'Door'):
                return 'stream_hold'
            if state == 'Tool':
                return 'stream_tool'
            if self._step_waiting and state == 'Idle':
                return 'stream_step'
            return 'stream'
        if state in ('Hold', 'Door'):
            return 'held'
        return 'idle'

    def action_allowed(self, action):
        """:return: None if the action is allowed in the present machine situation, else the reason text."""
        situation = self._machine_situation()
        allowed = dict(ACTION_RULES)[situation]
        if situation == 'not_connected' and self.__dict__.get('_simulation') is not None:
            # a host-side simulation stays stoppable
            allowed += ('stop', 'hold')
        if action in allowed:
            if (action == 'rapid_override' and situation.startswith('stream')
                    and callable(getattr(self.grbl_ser_port, 'prepare_program', None))):
                return _("Rapid override is not available during an E-Cut program.")
            return None
        reasons = {
            'not_connected': _("Not connected to a controller."),
            'needs_reset': _("The controller needs a reset first."),
            'needs_home': _("The controller needs a home cycle first."),
            'alarm': _("The controller is in alarm. Unlock or home it."),
            'operation': _("The controller is busy. Wait, or press Stop."),
            'tool_pause': _("The job waits for the tool change."),
            'stream_check': _("The controller is in check mode."),
            'stream_hold': _("The job is on hold."),
            'stream_tool': _("The controller waits for the tool change."),
            'stream_step': _("The job waits for the next block."),
            'stream': _("A job is running."),
            'held': _("The controller is on hold. Press Resume or Stop."),
            'idle': _("Not available now."),
        }
        return reasons[situation]

    def _apply_machine_state(self):
        """Enable each operator widget from action_allowed() and show the recovery state (GUI thread only)."""
        ui = self.ui
        situation = self._machine_situation()
        allowed = {action: self.action_allowed(action) is None for action in SENDER_ACTIONS}
        # the Spindle button also stops a running spindle where only spindle_off is allowed
        allowed['spindle'] = allowed['spindle'] or (allowed['spindle_off'] and ui.spindle_button.isChecked())
        # the Resume button stays off after a refused recheck until _on_status_report clears the refusal
        allowed['resume'] = allowed['resume'] and self._resume_refused_version is None
        for path, action in WIDGET_ACTIONS.items():
            widget = operator.attrgetter(path)(ui)
            widget.setEnabled(allowed[action])
        if self.macro_page is not None:
            try:
                self.macro_page.set_run_enabled(allowed['macro'])
            except RuntimeError:
                self.macro_page = None
        for button, byte in ui.ov_buttons.values():
            button.setEnabled(allowed['rapid_override' if byte in RAPID_OVERRIDE_BYTES else 'override'])
        # a jog arrow that the operator holds must stay enabled, or its release never arrives
        op = self._grbl_op
        ui.jog_wdg.setEnabled(allowed['jog'] or (op is not None and op.name == 'continuous jog'))
        if situation == 'needs_reset':
            ui.status_strip.set_state(_("Reset needed"))
        elif situation == 'needs_home':
            ui.status_strip.set_state(_("Home needed"))
        # the recovery banner is latched: only the end of the recovery hides it
        if self._grbl_recovery is None:
            ui.status_strip.set_recovery(None)
        elif self._grbl_recovery == 'needs_home':
            ui.status_strip.set_recovery(_("Reset done. Home the machine."), button=False)

    def on_grbl_override(self, byte):
        """A feed, rapid or spindle override button: send its real-time byte."""
        if self._action_ok('rapid_override' if byte in RAPID_OVERRIDE_BYTES else 'override'):
            self._grbl_realtime(byte)

    def on_grbl_stop(self):
        """A Stop (also of the registration and tool change dialogs): see _hold_now()."""
        if self._action_ok('stop'):
            self._hold_now()

    def _hold_now(self):
        """Stop the running operation at once: feed hold, and the machine needs recovery."""
        self._stop_continuous_jog()
        self._toolchange_new_session('stop')
        if self.__dict__.get('_simulation') is not None:
            self._simulation.stop()
            return
        with self._grbl_op_lock:
            self._start_seq += 1
            self._grbl_safety_generation += 1
            self._grbl_reset_pending = False
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
            written = self._grbl_write_realtime_locked(b'!')
            self._grbl_recovery = 'needs_reset'

        self._invalidate_coordinates()
        self._unlock_execution_view()
        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a controller."))
            return
        self.app.inform.emit('[WARNING_NOTCL] %s %s' % (
            _("Stopped. The controller is in feed hold."), self._grbl_recovery_text()))

    def on_grbl_reset(self, keep_position=None, wait_hold=False):
        """The Reset button and recovery button: see _reset_now()."""
        if self._action_ok('reset'):
            self._reset_now(keep_position, wait_hold)

    def _reset_now(self, keep_position=None, wait_hold=False):
        """
        Soft reset of the controller; a worker then waits for the startup banner.

        :param keep_position: True if no motion ran, so the position is still valid and the
            machine does not need Home. None (the Reset button): found from the current state.
        :param wait_hold: True after a feed hold (Stop): the reset byte waits until the hold is complete.
        """
        if self.__dict__.get('_simulation') is not None:
            return
        self._invalidate_coordinates()
        self._unlock_execution_view()
        self._invalidate_configuration()
        self._stop_continuous_jog()
        self._last_wco = None
        self._camera_new_session('reset')
        self._toolchange_new_session('reset')
        self.sender_io.clear_commanded()
        with self._grbl_op_lock:
            if keep_position is None:
                op = self._grbl_op
                keep_position = ((op is None or not op.motion) and not self._grbl_stream_dirty
                                 and self._grbl_recovery is None and self._reported_stationary())
            self._start_seq += 1
            self._grbl_safety_generation += 1
            self._grbl_brake_request += 1
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
            port = self.grbl_ser_port
            if wait_hold and port is not None:
                # no reset byte yet: status queries still work, and the hold wait runs in an operation
                self._grbl_stop_phase = 'braking'
                self._grbl_recovery = 'needs_reset'
                request = self._grbl_brake_request
                generation = self._grbl_safety_generation
            else:
                self._grbl_stop_phase = None
                written, receive_cleared = self._grbl_send_reset_locked(port)

        if wait_hold and port is not None:
            self._grbl_brake_then_reset_later(generation, keep_position, request, port)
            return
        self._grbl_reset_written(written, receive_cleared, keep_position)

    def _grbl_send_reset_locked(self, port):
        """
        The reset byte with its receive boundary. The caller holds _grbl_op_lock.

        Everything received before the reset byte belongs to the old controller epoch, so an old
        banner cannot confirm this reset.

        :return: (written, receive_cleared)
        """
        receive_cleared = self._grbl_begin_reset_epoch_locked(port) if port is not None else False
        written = self._grbl_write_realtime_locked(b'\x18')
        self._grbl_reset_pending = written and receive_cleared
        # The byte request is not proof of reset, even when the machine looked Idle.
        self._grbl_recovery = 'needs_reset'
        if not written:
            self._grbl_write_realtime_locked(b'!')
        return written, receive_cleared

    def _grbl_reset_written(self, written, receive_cleared, keep_position):
        """The messages after a reset byte (GUI thread), and the wait for its banner."""
        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a controller."))
            return
        if not receive_cleared:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Could not clear the GRBL receive buffer. Reset was sent, but Home is blocked. Press Reset again."))
            return
        self.app.inform.emit("%s" % _("The controller software reset was sent."))
        # a soft reset stops the spindle
        self._on_spindle_state(False)
        self._grbl_confirm_reset_later(self._grbl_safety_generation, keep_position)

    def _grbl_brake_then_reset_later(self, generation, keep_position, request, port, tries=40):
        """
        Stop: wait for the feed hold to complete, then write the reset byte (GUI thread; it tries
        again every 50 ms while an operation holds the port). If the port is still not free after
        the tries (2 s), the reset is written at once: a stuck operation must not delay it.
        """
        if (self._grbl_stop_phase != 'braking' or self._grbl_safety_generation != generation
                or self._grbl_brake_request != request or self.grbl_ser_port is not port):
            return

        def retry():
            QtCore.QTimer.singleShot(
                50, lambda: self._grbl_brake_then_reset_later(generation, keep_position, request, port, tries - 1))

        def write_now():
            with self._grbl_op_lock:
                self._grbl_stop_phase = None
                written, receive_cleared = self._grbl_send_reset_locked(port)
            self._grbl_reset_written(written, receive_cleared, keep_position)

        def task(op):
            try:
                # the hold wait: at most 5 s from the moment this task holds the port
                deadline = time.monotonic() + 5.0
                while True:
                    report = parse_grbl_status_report(self._grbl_query_status(op, timeout=0.5))
                    with self._grbl_op_lock:
                        ended = (op.failed or op.cancel.is_set()
                                 or op.safety_generation != self._grbl_safety_generation)
                    if ended:
                        break
                    # stopped: Idle, Alarm (GRBL stops motion there), Hold:0 or Door:0 (ready)
                    if report is not None and (report['state'] in ('Idle', 'Alarm') or (
                            report['state'] in ('Hold', 'Door') and report.get('substate') == '0')):
                        break
                    if time.monotonic() >= deadline:
                        break
                with self._grbl_op_lock:
                    if self._grbl_brake_request != request or self.grbl_ser_port is not port:
                        # a newer request or another connection owns the stop now
                        return
                    ended = (ended or op.failed or op.cancel.is_set()
                             or op.safety_generation != self._grbl_safety_generation)
                    if ended:
                        # a banner, a port error or a stop: nothing more is written here
                        self._grbl_stop_phase = None
                        result = (port, self._grbl_safety_generation, request)
                    else:
                        written, receive_cleared = self._grbl_send_reset_locked(port)
                        self._grbl_stop_phase = 'reset_sent' if written and receive_cleared else None
                        result = None
                if result is not None:
                    self.brake_ended_sig.emit(result)
                    return
                if not written or not receive_cleared:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not send the reset. Press Reset, then Home."))
                    return
                self.app.inform.emit("%s" % _("The controller software reset was sent."))
                self.spindle_state_sig.emit(False)
                self._grbl_reset_task(op, keep_position)
            finally:
                with self._grbl_op_lock:
                    if (self._grbl_brake_request == request and self.grbl_ser_port is port
                            and self._grbl_stop_phase is not None):
                        self._grbl_stop_phase = None

        if self._grbl_op is not None:
            if tries > 0:
                retry()
            else:
                write_now()
        elif not self._grbl_start_op('reset', task, allow_in_recovery=True):
            if tries > 0:
                retry()
            else:
                write_now()

    def _on_brake_ended(self, result):
        """
        The hold wait of a Stop ended without a reset byte (a banner, a port error). Start an
        ordinary reset, but only for the connection, the safety generation and the Stop request
        that this result belongs to.
        """
        port, generation, request = result
        if (self.grbl_ser_port is port and self._grbl_safety_generation == generation
                and self._grbl_brake_request == request and self._grbl_recovery == 'needs_reset'
                and self._grbl_stop_phase is None and not self._grbl_reset_pending):
            self._reset_now(keep_position=False)

    def _grbl_confirm_reset_later(self, generation, keep_position, tries=40):
        """
        Read the startup banner of a reset when the cancelled operation has freed the port
        (GUI thread; it tries again every 50 ms). Without a confirmation, Home still reads it.
        """
        if self._grbl_safety_generation != generation or not self._grbl_reset_pending:
            return
        if self._grbl_op is not None:
            if tries > 0:
                QtCore.QTimer.singleShot(50, lambda: self._grbl_confirm_reset_later(generation, keep_position, tries - 1))
            else:
                self.app.inform.emit('[WARNING_NOTCL] %s' % self._grbl_recovery_text())
            return

        self._grbl_start_op('reset', lambda op: self._grbl_reset_task(op, keep_position), allow_in_recovery=True)

    def _grbl_reset_task(self, op, keep_position):
        """Worker: wait for the banner of the reset that was written, then the state after it."""
        if not self._grbl_confirm_reset(op):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The controller reset was not confirmed. Press Reset, then Home."))
            return
        state = self._grbl_post_reset_state(op) if keep_position else None
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op) or op.safety_generation != self._grbl_safety_generation:
                return
            if state == 'Idle':
                # no motion ran, and a reset in Idle keeps the position
                self._grbl_recovery = None
                self.app.inform.emit('[success] %s' % _("Controller reset. The spindle and the outputs are off."))
                return
        self.app.inform.emit('[WARNING_NOTCL] %s' % self._grbl_recovery_text())

    def _on_resume_button(self):
        """Resume button guard: block cycle-start when recovery is needed or a reset is pending.

        After Stop or an error
        the stream is dirty and recovery is latched; sending ~ would restart abandoned motion.
        A healthy Hold->Resume is unaffected: _grbl_recovery is None in that path.

        All safety checks and the write happen in ONE critical section to prevent a worker
        from latching recovery between the check and the write.

        This is the one handler that resumes the stream (the Resume button):
        after a tool change it rechecks the rest of the program with the new TLO under an active machine profile.
        """
        if not self._action_ok('resume'):
            return False
        if not self._tool_change_recheck():
            return False
        with self._grbl_op_lock:
            fence_op = self.__dict__.get('_grbl_tool_fence_op')
            if fence_op is not None and fence_op is self._grbl_op:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wait for the tool change reply fence before resuming."))
                return False
            if self.__dict__.get('_grbl_mpg_owned') or self.__dict__.get('_grbl_sd_owned'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "External SD/MPG execution owns the controller. Resume it at its source."))
                return False
            if self.__dict__.get('_grbl_tool_command_busy'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Wait for the tool change command to finish before resuming."))
                return False
            # Recovery latched (needs_reset or needs_home): block resume
            if self._grbl_recovery is not None:
                self.app.inform.emit('[ERROR_NOTCL] %s' % self._grbl_recovery_text())
                return False
            # Reset in progress: block resume
            if self._grbl_reset_pending:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("A reset is pending. Wait for the banner."))
                return False
            # Stream dirty but no healthy active operation: abandoned job, block resume
            if self._grbl_stream_dirty and (self._grbl_op is None or self._grbl_op.cancel.is_set() or self._grbl_op.failed):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The previous job was abandoned. Press Reset, then Home."))
                return False
            written = self._grbl_write_realtime_locked(b'~')
        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a controller."))
            return False
        self.app.inform.emit("%s" % _("Controller resumed."))
        return True

    def _select_available_excellon(self, *args):
        if self.ui is None:
            return
        combo = self.ui.avoid_exc_combo
        root = self.app.collection.index(1, 0, QtCore.QModelIndex())
        if combo.rootModelIndex() != root:
            combo.setRootModelIndex(root)
        if combo.currentIndex() < 0 and combo.count():
            combo.setCurrentIndex(0)
        self._update_3d_excellon()

    def on_avoid_exc_holes(self, state):
        super().on_avoid_exc_holes(state)
        self.ui.avoid_exc_label.setVisible(bool(state))
        self.ui.avoid_exc_combo.setVisible(bool(state))
        self._update_3d_excellon()

    def _update_3d_excellon(self, *args):
        if self.view3d is None:
            return
        shapes = self.view3d.excellon_shapes
        shapes.clear()
        if self.ui.avoid_exc_holes_cb.get_value():
            try:
                objects = self._avoidance_excellon_objects()
            except ValueError:
                objects = []
            for obj in objects:
                for geo in flatten_shapely_geometry(obj.solid_geometry):
                    shapes.add(shape=geo, color=TOOL_COLOR, layer=0)
        shapes.redraw()

    def _avoidance_excellon_objects(self):
        name = self.ui.avoid_exc_combo.get_value()
        obj = self.app.collection.get_by_name(name) if name else None
        if obj is None or obj.kind != 'excellon':
            raise ValueError(_("Select an Excellon object to avoid its holes."))
        return [obj]

    def _al_read_probe_inputs(self):
        """
        Read and check the probing parameters (GUI thread).

        :return: (travel_z, depth, probe_fr), or None (the user was told why).
        """
        try:
            travel_z = float(self.ui.ptravelz_entry.get_value())
            probe_fr = float(self.ui.feedrate_probe_entry.get_value())
            depth = float(self.ui.pdepth_entry.get_value())
        except (TypeError, ValueError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probing parameters are not valid numbers."))
            return None
        if not all(math.isfinite(v) for v in (travel_z, probe_fr, depth)):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probing parameters are not valid numbers."))
            return None
        if depth >= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probe Z depth must be negative."))
            return None
        if probe_fr <= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probe feedrate must be more than zero."))
            return None
        if travel_z <= 0:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The probe travel Z must be more than zero."))
            return None
        return travel_z, depth, probe_fr

    def on_grbl_autolevel(self):
        if self._action_ok('probe'):
            self._queue_probe(False)

    def _check_probe_moves(self, op, profile, commanded, travel_z, depth, units, feed=None):
        """
        The check of the moves of a probe run, before any is sent: the work points that the controller is commanded
        to, from the live state of the controller (or the declared setup of the profile).

        :param op: the reserved probe operation (the live state is read through it).
        :return: True if the probe may go on (GUI thread; the user was told why when not).
        """
        setup = self.live_setup(op, profile, None)
        if setup is None:
            setup = setup_from_profile(profile, None, 'inch' if units == 'IN' else 'mm')
        result = check_probe_moves(profile, setup, commanded, travel_z, depth, units, feed)
        if has_reject(result):
            first = next(v for v in result.violations if v.severity == 'reject')
            message = _('Machine profile "%s": probe not started. Line %d: %s') % (
                profile.name, first.line, first.message)
            report_gate(self.app, FileGate(False, message, result=result))
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            return False
        return True

    def _queue_probe(self, start_after_probe):
        # read every UI value here, in the GUI thread; the worker must not touch widgets
        inputs = self._al_read_probe_inputs()
        if inputs is None:
            return
        travel_z, depth, probe_fr = inputs
        precision = self._al_read_precision_inputs(travel_z, depth, probe_fr)
        if precision is None:
            return
        if not self.al_voronoi_geo_storage:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no probe points. Add probe points first."))
            return

        # reserve first: a refused click must not retire the running probe or a valid map
        op = self._grbl_reserve('probe', motion=True)
        if op is None:
            return
        op.precision_inputs = precision

        points = {key: (value['point'].x, value['point'].y) for key, value in self.al_voronoi_geo_storage.items()}
        # with an active machine profile the controller is commanded to the work points W = C(D) of the grid points
        # (identity without a correction); the height is stored under the grid point. The moves are checked first:
        # a refused probe must not retire a valid map
        profile = active_profile_of(self.app)
        extra = {}
        if profile is not None:
            commanded = {key: map_point(profile, x, y) for key, (x, y) in points.items()}
            if not self._check_probe_moves(op, profile, list(commanded.values()), travel_z, depth,
                                           self.app.app_units.upper(), probe_fr):
                self._grbl_release(op)
                return
            if correction_matrix(profile) is not None:
                extra = {'commanded': commanded, 'calibration': calibration_hash(profile)}

        context = self._al_capture_context(replacement=True)
        if context is None:
            self._grbl_release(op)
            return
        if start_after_probe:
            self._probe_start_op = op
            self.ui.start_button.setDisabled(True)

        tol = context.tolerance
        units_cmd = 'G20' if context.units == 'IN' else 'G21'
        probe_timeout = probe_timeout_s(travel_z, depth, probe_fr)


        def worker_task():
            try:
                with self.app.proc_container.new('%s...' % _("Probing")):
                    self._grbl_probe_run(op, context, points, tol, travel_z, depth, probe_fr,
                                         units_cmd, probe_timeout, **extra)
            finally:
                self._grbl_release(op)
                if start_after_probe:
                    self.probe_finished_sig.emit(op, context)

        self.app.inform.emit('%s' % _("Sending probing GCode to the controller."))
        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def on_grbl_edge_probe(self):
        if self._action_ok('probe'):
            self._queue_edge_probe(center=False)

    def on_grbl_center_probe(self):
        if self._action_ok('probe'):
            self._queue_edge_probe(center=True)

    # the spelling used in the UI text
    on_grbl_centre_probe = on_grbl_center_probe

    def _edge_read_inputs(self):
        """
        Read and check the directional probing parameters (GUI thread).

        :return: (travel_z, distance, feed, pulloff), or None (the user was told why).
        """
        try:
            travel_z = float(self.ui.edge_probe_travelz_entry.get_value())
            distance = float(self.ui.edge_probe_distance_entry.get_value())
            feed = float(self.ui.edge_probe_feed_entry.get_value())
            pulloff = float(self.ui.edge_probe_pulloff_entry.get_value())
        except (TypeError, ValueError):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The edge probing parameters are not valid numbers."))
            return None
        values = (travel_z, distance, feed, pulloff)
        if not all(math.isfinite(value) and value > 0 for value in values):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The probe Z travel, distance, feedrate and pull-off must be positive finite numbers."))
            return None
        # The wire protocol uses four decimal places; never round a bounded move or a feed to zero.
        if not all(round(value, 4) > 0 for value in values):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The edge probing parameters must be representable to four decimal places."))
            return None
        return round(travel_z, 4), round(distance, 4), round(feed, 4), round(pulloff, 4)

    def _queue_edge_probe(self, center):
        units = self.app.app_units.upper()
        if units not in ('MM', 'IN'):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The project units must be MM or IN."))
            return
        if str(self.units).upper() != units:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The probe widget units do not match the project units. Refresh the Sender before probing."))
            return
        if self.ui.check_mode_cb.get_value() is True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Turn off check mode before probing."))
            return
        inputs = self._edge_read_inputs()
        if inputs is None:
            return
        travel_z, distance, feed, pulloff = inputs

        directions = ['xplus', 'xminus', 'yplus', 'yminus'] if center else [
            self.ui.edge_probe_axis_radio.get_value()]

        # reserve first: a refused click must not retire a running operation
        op = self._grbl_reserve('probe', motion=True)
        if op is None:
            return
        units_cmd = 'G20' if units == 'IN' else 'G21'
        probe_timeout = probe_timeout_s(distance, 0.0, feed)
        self._edge_probe_op = op
        self.ui.edge_probe_result_label.setText('')

        def worker_task():
            try:
                with self.app.proc_container.new('%s...' % _("Probing")):
                    self._grbl_edge_probe_run(op, units, directions, travel_z, distance, feed, pulloff,
                                              units_cmd, probe_timeout)
            finally:
                self._grbl_release(op)

        self.app.inform.emit('%s' % _("Sending probing GCode to the controller."))
        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def _on_edge_probe_result(self, result):
        """GUI thread: show a complete directional or centre result; never a partial one."""
        if not isinstance(result, dict):
            return
        if result.get('operation') is not getattr(self, '_edge_probe_op', None):
            return
        if result.get('safety_generation') != self._grbl_safety_generation:
            return
        if result.get('units') != self.app.app_units.upper():
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The project units changed while the probing ran. The result was discarded."))
            return
        if result.get('kind') == 'failed':
            return
        frame = result.get('frame')
        wcs = frame[0] if frame else ''
        if result.get('kind') == 'center':
            x, y = result['center']
            text = '%s X%.*f Y%.*f' % (_("Centre:"), self.decimals, x, self.decimals, y)
        elif result.get('kind') == 'edge':
            x, y, z = result['contact']
            text = '%s X%.*f Y%.*f Z%.*f' % (
                _("Contact:"), self.decimals, x, self.decimals, y, self.decimals, z)
        else:
            return
        if wcs:
            text += ' (%s, %s)' % (wcs, result.get('units'))
        self.ui.edge_probe_result_label.setText(text)

    def _on_probe_finished(self, op, context):
        if op is not getattr(self, '_probe_start_op', None):
            self._start_free_button()
            return
        self._probe_start_op = None
        if (op.cancel.is_set() or op.failed or op.safety_generation != self._grbl_safety_generation or
                not self._al_context_current(context) or not self.al_heights_valid or
                self.ui.autolevel_cb.get_value() is not True):
            self._start_free_button()
            return
        self._start_levelled(start_line=1)

    def _al_read_precision_inputs(self, travel_z, depth, probe_fr):
        """Capture optional fine probing parameters before reserving or moving GRBL."""
        if self.ui.precision_probe_cb.get_value() is not True:
            return ()
        try:
            feed = float(self.ui.precision_feed_entry.get_value())
            retract = float(self.ui.precision_retract_entry.get_value())
            valid = all(math.isfinite(v) for v in (feed, retract)) and \
                0.0001 <= feed <= min(probe_fr, 910000.0) and \
                0.0001 <= retract <= travel_z - depth
            # The wire protocol uses four decimal places; never round a limit
            # deeper, a retract above clearance, or a positive feed to zero.
            valid = valid and round(feed, 4) <= probe_fr and round(depth, 4) >= depth and \
                round(travel_z, 4) <= travel_z and round(probe_fr, 4) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Precision probing needs a positive fine feed no faster than the coarse feed, "
                "and a positive retract within the depth-to-clearance range. "
                "Use probing limits representable to four decimal places."))
            return None
        return round(feed, 4), round(retract, 4)

    def _restore_al_precision(self, target_obj):
        # Store the calibration's units with the job, not in mutable global options.
        # Legacy jobs start with precision disabled and physical defaults in mm.
        options = target_obj.obj_options
        units = self.app.app_units.upper()
        saved_units = options.get('tools_al_precision_units', 'MM')
        scale = 1.0 if saved_units == units else (25.4 if units == 'MM' else 1.0 / 25.4)
        fields = (
            (self.ui.precision_probe_cb, options.get('tools_al_precision_probe', False) is True),
            (self.ui.precision_feed_entry, options.get('tools_al_precision_feed', 10.0) * scale),
            (self.ui.precision_retract_entry, options.get('tools_al_precision_retract', 0.5) * scale),
        )
        blocked = [(widget, widget.blockSignals(True)) for widget, value in fields]
        try:
            for widget, value in fields:
                widget.set_value(value)
        finally:
            for widget, previous in blocked:
                widget.blockSignals(previous)
        self._update_precision_probe_ui()

    def _update_precision_probe_ui(self):
        enabled = self.ui.precision_probe_cb.get_value() is True
        self.ui.precision_feed_entry.setEnabled(enabled)
        self.ui.precision_retract_entry.setEnabled(enabled)

    def on_precision_probe_changed(self, *args):
        self._update_precision_probe_ui()
        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if self._is_supported_target(target_obj) and getattr(target_obj, 'kind', None) == 'cncjob':
            target_obj.obj_options.update({
                'tools_al_precision_probe': self.ui.precision_probe_cb.get_value() is True,
                'tools_al_precision_feed': self.ui.precision_feed_entry.get_value(),
                'tools_al_precision_retract': self.ui.precision_retract_entry.get_value(),
                'tools_al_precision_units': self.app.app_units.upper(),
            })

    def probing_gcode(self, storage):
        """The sender probes live, so it has no probing G-code file."""
        return ''

    def ui_connect(self):
        self.ui.al_add_button.clicked.connect(self.on_add_al_probepoints)
        self.ui.show_al_table.stateChanged.connect(self.on_show_al_table)

    def ui_disconnect(self):
        try:
            self.ui.al_add_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.show_al_table.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    def reset_fields(self):
        self.al_heights_valid = False
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(3, 0, QtCore.QModelIndex()))

    # ------------------------------------------------------------------
    # LevellingCoreMixin hooks
    # ------------------------------------------------------------------
    def _al_set_buttons_enabled(self, enabled):
        self.ui.grbl_get_heightmap_button.setDisabled(not enabled)
        self.ui.grbl_save_height_map_button.setDisabled(not enabled)

    def _al_result_lock(self):
        return self._grbl_op_lock

    def _al_result_retired(self, result):
        op = result.get('operation')
        return (op is not None and op.cancel.is_set()) or (
                'safety_generation' in result and
                result['safety_generation'] != self._grbl_safety_generation)

    def _al_on_publish_locked(self, result):
        # _on_al_result_ready() holds _grbl_op_lock: the heights and this increment are 1 step
        self._start_seq += 1

    def _invalidate_start_requests(self):
        """Make the pending levelled Start requests old. Never call it with _grbl_op_lock held."""
        with self._grbl_op_lock:
            self._start_seq += 1

    def _start_request_current(self, req):
        """:return: True if nothing changed since the request was made (GUI thread; takes no lock)."""
        return (req.seq == self._start_seq
                and self._al_context_current(req.al_ctx)
                and self._target() is req.target_obj
                and self._grbl_safety_generation == req.safety_generation
                and self._al_map_frame == req.map_frame
                and self._al_map_imported == req.map_imported)

    def _al_on_height_map_ready(self, target_obj, result=None):
        # _on_al_result_ready() calls this hook after it released _grbl_op_lock, so the helper is safe here
        # (the increment under the lock, before the publication, is _al_on_publish_locked)
        self._invalidate_start_requests()
        # the levelled job belongs to the old map; the next Start makes it again
        self.job_map_generation = None
        # the map belongs to the correction that its points were probed under (a probe log of an imported map
        # names it in its header); a map of no correction has None
        self._al_map_calibration = None if result is None else result.get('calibration')
        # bind the map to the GRBL frame of the probe; an imported map has no frame yet
        if result is not None and result.get('kind') == 'probe':
            self._al_map_frame = result.get('frame')
            self._al_map_imported = False
        else:
            self._al_map_frame = None
            self._al_map_imported = True
        self.app.inform.emit('[success] %s' % _("The height map is ready."))
        self._update_3d_height_map()

    @staticmethod
    def _tlo_line(lines):
        """:return: the index of the first line with G43, G43.1 or G49, or None."""
        for index, line in enumerate(lines):
            if any(letter == 'G' and _g_key(value) in ('43.0', '43.1', '49.0') for letter, value in _words(line)):
                return index
        return None

    def _al_arc_segment(self, target_obj):
        """The longest chord of 1 piece of a split arc: a preference of the sender."""
        return float(self.app.options.get("tools_grbl_arc_segment", 0.5))

    # ------------------------------------------------------------------
    # Run control. T16 replaces build_job() with the levelled job and the write callable
    # ------------------------------------------------------------------
    def _target(self):
        return self.app.collection.get_by_name(self.ui.object_combo.get_value())

    def _job_source(self):
        """:return: (the selected CNCJob, its G-code text), or (None, None) (the user was told why)."""
        target = self._target()
        if not self._is_supported_target(target):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Select a CNCJob object."))
            return None, None
        source = getattr(target, 'source_file', '') or ''
        if not source.strip():
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The CNCJob has no G-code. Use Update in the CNCJob UI."))
            return None, None
        return target, source

    def build_job(self, text=None, keep_markers=False):
        """
        Make self.job_lines from the selected CNCJob.

        :param text: the G-code text to make the lines from instead of the source of the CNCJob (the text that the
            machine profile corrected: the correction runs before the comments are removed).
        :param keep_markers: keep the '(MP:FIXED ...)' marker lines of a fixed sequence (the levelled job of an
            active profile: the correction after the levelling needs them).
        :return: True if the job has lines, and each line fits in the GRBL receive buffer.
        """
        target, source = self._job_source()
        if target is None:
            return False
        if text is None:
            text = source
        numbered = SenderGen.clean_lines_numbered(
            text, keep_markers, self.ui.optional_stop_cb.get_value() is True,
            self.ui.block_delete_cb.get_value() is True)
        lines = [line for _number, line in numbered]
        bad_index = validate_stream_lines(lines, *self._line_limits())
        if bad_index is not None:
            self.job_lines = []
            self.job_line_numbers = []
            self.app.inform.emit('[ERROR_NOTCL] %s' % (
                _("Line %d of the G-code is longer than GRBL accepts. The job was not made.") %
                numbered[bad_index][0]))
            return False
        self.job_lines = lines
        self.job_line_numbers = [number for number, _line in numbered]
        self.job_target_name = target.obj_options['name']
        self.job_map_generation = None
        return bool(self.job_lines)

    def build_levelled_job(self, op=None, apply_profile=True):
        """
        Make self.job_lines from the selected CNCJob, with the height map applied.
        GUI thread. Steps: clean -> refuse a tool length change -> split long G1
        (only if the job is not segmented) -> level (level_gcode_line also splits
        the arcs; the strict mode refuses the moves that it can not compensate) -> with an active machine
        profile: correct the levelled design XY and check the output (the marker lines of the fixed sequences
        stay through the levelling, so the stations keep their machine position).

        :param op: the reserved stream operation: the check reads the live state of the controller through it.
        :param apply_profile: False makes the design lines without the profile (the Save levelled G-code gate does it).

        The WCS of the levelling is the one of the bound frame (`_al_map_frame`). A map
        that is not bound yet (an imported map) has none: then the first WCS word of the
        program declares it.

        :return: True if the job has lines.
        """
        inputs = self._level_inputs(op, apply_profile)
        if inputs is None:
            return False
        prepared = SenderGen.prepare_levelled_job(inputs, self._profile_compute, self.autolevell_gcode, self._tlo_line,
                                                  self._line_limits())
        return self._apply_prepared(prepared)

    def _level_inputs(self, op, apply_profile=True, request=None):
        """
        Read the widgets, the height map and the options of a levelled job (GUI thread).

        :return: a SenderGen._StartInputs with `level` set, or None (the user was told why).
        """
        profile = active_profile_of(self.app) if apply_profile else None
        if apply_profile and self._al_map_calibration != calibration_hash(profile):
            # the heights belong to the points at C(p) of the correction that was in force at the probe
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The correction of the machine profile (the calibration) changed after the height map was made. "
                "Probe again."))
            return None
        inputs = self._start_inputs(op, apply_profile)
        if inputs is None:
            return None
        target = inputs.target
        ctx = self._al_capture_context()
        snapshot = self._al_capture_apply_input(target, units=ctx.units, require_segmented=False)
        if snapshot is None:
            return None
        home_wcs = None
        if self._al_map_frame is not None:
            # the frame holds the WCS as GRBL names it ('G54'); the levelling state wants the G number
            home_wcs = float(str(self._al_map_frame[0])[1:])
        # with a profile the marker lines of the fixed sequences go through the levelling (it keeps comments), so
        # that the correction after it leaves the stations alone
        inputs.level = SenderGen._LevelInput(
            method=self.ui.al_method_radio.get_value(), snapshot=snapshot,
            seg_len=float(self.app.options.get("tools_grbl_seg_len", 1.0)), home_wcs=home_wcs,
            keep_markers=profile is not None, generation=self._al_generation, request=request)
        return inputs

    def _profile_setup(self, op, profile, target):
        """
        The Setup of the check of a stream: the live state of the controller when the Sender is connected (`op` is
        the reserved operation), else the declared setup of the profile.

        It writes nothing of the Sender (a worker runs it): the GUI thread stores the setup and the key.

        :return: (Setup, the assumptions that this method adds to the result, the key of the live state or None)
        """
        units = 'inch' if str(target.units).upper() in ('IN', 'INCH') else 'mm'
        dialect = getattr(target, 'pp_geometry_name', None)
        live = self.live_setup(op, profile, dialect) if op is not None else None
        if live is None:
            return setup_from_profile(profile, dialect, units), [], None
        # the controller reports no modal state except the units: the program sets its own, these are the defaults
        setup = dataclasses.replace(
            live, units=live.units or units, distance_mode=live.distance_mode or 'G90',
            arc_mode=live.arc_mode or 'G91.1', plane=live.plane or 'G17', motion_mode=live.motion_mode or 'G0')
        return setup, ['modal state assumed: G90, G91.1, G17, G0'], self.live_setup_key

    def _confirm_profile_stream(self, note):
        """Ask the user if the stream goes on although the check has warnings (GUI thread). :return: True if yes."""
        return self._ask_profile_question(note, _("Stream anyway?"))

    def _confirm_profile_resume(self, note):
        """Ask the user if the Resume after a tool change goes on although the recheck has warnings (GUI thread)."""
        return self._ask_profile_question(note, _("Resume anyway?"))

    def _ask_profile_question(self, note, question):
        msgbox = FCMessageBox(parent=self.app.ui)
        title = _("Machine profile")
        msgbox.setWindowTitle(title)
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText('%s\n%s' % (note, question))
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        msgbox.setDefaultButton(bt_no)
        msgbox.exec()
        return msgbox.clickedButton() == bt_yes

    def _accept_check(self, profile, result):
        """
        Show the result of a check of the stream in the status bar dot and decide: a reject stops it (the violations
        dialog opens), warnings or a partial check ask for a confirmation.

        :return: True if the stream may go on.
        """
        if has_reject(result):
            first = next(v for v in result.violations if v.severity == 'reject')
            message = _('Machine profile "%s": stream not started. Line %d: %s') % (
                profile.name, first.line, first.message)
            report_gate(self.app, FileGate(False, message, result=result))
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            return False
        note = None
        if result.status is CheckStatus.VIOLATIONS:
            note = _('Machine profile: %d warnings') % sum(1 for v in result.violations
                                                           if v.kind is not ViolationKind.UNVERIFIED)
        elif result.status is CheckStatus.PARTIAL:
            note = _('Machine profile: partially checked')
        report_gate(self.app, FileGate(note=note, result=result))
        if note is None or self._confirm_profile_stream(note):
            return True
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        return False

    def _prepare_for_stream(self, text, target, profile, op, line_map=None):
        """
        The design text (or the levelled design text) of a stream, corrected by the profile when its calibration is
        on and checked as it will be streamed. Runs before the comments are removed (the markers of a fixed sequence
        are comments).

        :param line_map: optional out-parameter: receives the source line of each line of the corrected text.
        :return: the text to make the lines from, or None (the user was told why; nothing starts).
        """
        outcome = self._profile_compute(text, target, profile, op, line_map)
        return outcome.out if self._profile_present(profile, outcome) else None

    def _profile_compute(self, text, target, profile, op, line_map=None, correct=True):
        """
        The machine profile step of a stream with no GUI call and no write of the Sender (a worker can run it): the
        setup of the check, then the correction and the check of `text`. `correct=False` only checks the text.

        :return: SenderGen._ProfileOutcome
        """
        setup, assumed, setup_key = self._profile_setup(op, profile, target)
        outcome = SenderGen._ProfileOutcome(setup=setup, setup_key=setup_key)
        kwargs = {'line_map': line_map} if correct else {'correct': False}
        try:
            outcome.out, outcome.result = prepare_output(text, Purpose.STREAM, setup, profile, target.coords_decimals,
                                                         **kwargs)
        except CorrectionRefused as refused:
            outcome.refused = refused
            return outcome
        outcome.result.assumptions.extend(assumed)
        return outcome

    def _profile_present(self, profile, outcome):
        """
        Store the setup of a profile step and show its result (GUI thread): the refused correction, the status bar
        dot, the violations dialog, the confirmation.

        :return: True if the stream may go on.
        """
        self._stream_setup = outcome.setup
        self._stream_setup_key = outcome.setup_key
        refused = outcome.refused
        if refused is not None:
            message = _('Machine profile "%s": stream not started. Correction refused at line %d, %s: %s') % (
                profile.name, refused.line, refused.word, refused.reason)
            report_gate(self.app, FileGate(False, message))
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            return False
        return self._accept_check(profile, outcome.result)

    def build_stream_job(self, op=None, apply_profile=True):
        """
        The job for Start and Save: levelled if 'Level the G-code' is checked. With an active machine profile the
        design text is corrected and checked first (`op`: the reserved stream operation, for the live state).
        """
        if self.ui.autolevel_cb.get_value() is True:
            return self.build_levelled_job(op, apply_profile)
        inputs = self._start_inputs(op, apply_profile)
        if inputs is None:
            return False
        # the same steps as a Start, here in the calling thread
        return self._apply_prepared(SenderGen.prepare_job(inputs, self._profile_compute, self._line_limits()))

    def on_start(self):
        if not self._action_ok('start'):
            return
        # the WCO of an earlier session or job is not trusted
        self._last_wco = None
        if getattr(self, '_probe_start_op', None) is not None:
            return
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        if self.ui.autolevel_cb.get_value() is True:
            choice = 'new'
            if self.al_heights_valid and self._height_map_is_complete():
                context = self._al_capture_context()
                seq = getattr(self, '_start_seq', 0)
                safety_generation = self._grbl_safety_generation
                choice = self._choose_height_map()
                if (not self._al_context_current(context) or seq != getattr(self, '_start_seq', 0) or
                        safety_generation != self._grbl_safety_generation):
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                        "The height map, source object or machine state changed. Press Start again."))
                    return
            if choice == 'existing':
                self._start_levelled()
            elif choice == 'new':
                if self.ui.check_mode_cb.get_value() is True:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "Check mode needs an existing height map. Probe first or turn off check mode."))
                    return
                if not self._is_supported_target(self._target()):
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Select a CNCJob object."))
                    return
                if not self.build_job():
                    return
                self._console().set_program(self.job_lines)
                target = self._target()
                source = target.source_file
                context = self._al_capture_context()
                generation = self._grbl_safety_generation
                start_line = self._choose_start_line(len(self.job_lines), beginning_only=True)
                if start_line is None:
                    return
                if (not self._al_context_current(context) or generation != self._grbl_safety_generation or
                        self._target() is not target or target.source_file != source):
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                        "The program or machine state changed while the dialog was open. Press Start again."))
                    return
                if start_line != 1:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "Starting from a line with auto-levelling needs an existing height map. "
                        "Probe first, then use the existing map."))
                    return
                self._queue_probe(True)
            return
        # the check of the stream reads the live state of the controller: it needs the operation first
        op = self._grbl_reserve('stream', motion=True)
        if op is None:
            return
        inputs = self._start_inputs(op)
        if inputs is None:
            self._grbl_release(op)
            return

        limits = self._line_limits()

        def prepare_task():
            try:
                prepared = SenderGen.prepare_job(inputs, self._profile_compute, limits)
            except Exception as err:
                self.app.log.error("ToolGcodeSender.on_start() -> %s" % str(err))
                prepared = SenderGen._PreparedJob(inputs=inputs, error=_("The Start failed."))
            self.job_prepared_sig.emit(prepared)

        self.ui.start_button.setDisabled(True)
        self.app.inform.emit(_("Preparing the job"))
        self.app.worker_task.emit({'fcn': prepare_task, 'params': []})

    def _rapid_mm_min(self, profile):
        """The rapid rate (mm/min) for the time estimate (GUI thread)."""
        port = self.grbl_ser_port
        if port is not None and self._ecut_settings_capable(port):
            try:
                axes = port.get_motion_settings()['axes']
                return min(axes[0]['max_velocity_mm_s'], axes[1]['max_velocity_mm_s']) * 60.0
            except (serial.SerialException, OSError, KeyError, IndexError, TypeError):
                pass
        return SenderEstimate.rapid_rate(self._grbl_settings, None if profile is None else profile.rapid_max)

    def _start_inputs(self, op, apply_profile=True):
        """
        Read the widgets and the options for the preparation of a stream job (GUI thread).

        :return: a SenderGen._StartInputs, or None (the user was told why).
        """
        target, source = self._job_source()
        if target is None:
            return None
        profile = active_profile_of(self.app) if apply_profile else None
        self._stream_setup = None if profile is None else self._stream_setup
        with self._grbl_op_lock:
            seq = self._start_seq
            generation = self._grbl_safety_generation
        return SenderGen._StartInputs(
            op=op, target=target, source=source, check_mode=self.ui.check_mode_cb.get_value() is True,
            autolevel=self.ui.autolevel_cb.get_value() is True,
            optional_stop=self.ui.optional_stop_cb.get_value() is True,
            block_delete=self.ui.block_delete_cb.get_value() is True,
            arc_mode=self.app.options.get("tools_grbl_arc_mode", 'split'),
            arc_segment=float(self.app.options.get("tools_grbl_arc_segment", 0.5)), units=target.units,
            decimals=target.coords_decimals, profile=profile,
            poll_ms=int(self.app.options.get("tools_grbl_status_ms", 200)), seq=seq, generation=generation,
            rapid_mm_min=self._rapid_mm_min(active_profile_of(self.app)))

    def _apply_prepared(self, prepared):
        """
        Take a prepared job (GUI thread): show the check of the machine profile and the errors, then make
        the job lines.

        :return: True if the job has lines.
        """
        inputs = prepared.inputs
        self.job_lines = []
        self.job_line_numbers = []
        if prepared.profile is not None and not self._profile_present(inputs.profile, prepared.profile):
            return False
        if prepared.error is not None:
            if prepared.error:
                self.app.inform.emit('[ERROR_NOTCL] %s' % prepared.error)
            return False
        for message in prepared.diagnostics:
            self.app.inform.emit(message)
        self.job_lines = prepared.lines
        self.job_line_numbers = prepared.numbers
        self.job_cumulative = prepared.cumulative
        self.job_target_name = inputs.target.obj_options['name']
        # the levelled lines have no 1:1 map to the lines of the G-code text
        self.job_map_generation = None if inputs.level is None else inputs.level.generation
        if prepared.split_count:
            self.app.inform.emit('%s: %d' % (_("Arc moves split into line moves"), prepared.split_count))
        return bool(self.job_lines)

    def _on_job_prepared(self, prepared):
        """Slot of job_prepared_sig (GUI thread): the job is ready. Check it, then start the stream."""
        inputs = prepared.inputs
        op = inputs.op
        req = None if inputs.level is None else inputs.level.request
        if not self._prepared_current(inputs, req):
            self._start_abandoned(op)
            return
        if not self._apply_prepared(prepared):
            self._grbl_release(op)
            self._start_free_button()
            return
        if not self._prepared_current(inputs, req):
            self._start_abandoned(op)
            return
        self._start_stream_worker(op, self.job_lines, self.job_line_numbers, inputs.check_mode, inputs.poll_ms, req)

    def _prepared_current(self, inputs, req):
        """:return: True if the Start (and its levelled request, if any) of a prepared job is the current one."""
        return self._start_current(inputs.op, inputs.seq, inputs.generation, inputs.target, inputs.source) and \
            (req is None or self._start_request_current(req))

    def _choose_height_map(self):
        msgbox = FCMessageBox(parent=self.app.ui)
        msgbox.setWindowTitle(_("Height map"))
        msgbox.setText(_("A height map is available. Generate a new map or use the existing one?"))
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Question)
        new_button = msgbox.addButton(_('Generate New'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        existing_button = msgbox.addButton(_('Use Existing'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        cancel_button = msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msgbox.setDefaultButton(cancel_button)
        msgbox.exec()
        if msgbox.clickedButton() == new_button:
            return 'new'
        if msgbox.clickedButton() == existing_button:
            return 'existing'
        return 'cancel'

    def _start_free_button(self):
        """A levelled Start ended before the stream: the Start button works again (GUI thread)."""
        if self._grbl_op is None:
            self.ui.start_button.setDisabled(False)

    def _confirm_imported_frame(self):
        """
        Ask the user if the machine frame is the one of the imported height map (GUI thread).

        :return: True if the user answered Yes.
        """
        msgbox = FCMessageBox(parent=self.app.ui)
        title = _("Imported height map")
        msgbox.setWindowTitle(title)
        msgbox.setText('<b>%s</b>' % title)
        msgbox.setInformativeText(_(
            "This height map was imported. It has no machine frame. "
            "Is the machine in the same WCS and zero as when the map was made?"))
        msgbox.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        bt_yes = msgbox.addButton(_('Yes'), QtWidgets.QMessageBox.ButtonRole.YesRole)
        bt_no = msgbox.addButton(_('No'), QtWidgets.QMessageBox.ButtonRole.NoRole)
        msgbox.setDefaultButton(bt_no)
        msgbox.exec()
        return msgbox.clickedButton() == bt_yes

    @staticmethod
    def _frames_match(live, bound, tolerance=1e-4):
        """
        :return: True if the 2 frames (active_wcs, offset, report_inches) are the same: the same WCS and
            report units, and each axis of the offset within `tolerance` (machine units).
        """
        if live is None or bound is None:
            return False
        if live[0] != bound[0] or bool(live[2]) != bool(bound[2]) or len(live[1]) != len(bound[1]):
            return False
        return all(abs(a - b) <= tolerance for a, b in zip(live[1], bound[1]))

    def _start_levelled(self, start_line=None):
        """
        Phase 0 of a levelled Start (GUI thread): make the request, ask about an imported map, reserve
        the operation 'frame' and read the live frame in a worker (phase 1). _on_frame_ready() goes on.
        """
        # widget values are read here, before the request
        check_mode = self.ui.check_mode_cb.get_value() is True
        target = self._target()
        if not self._is_supported_target(target):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Select a CNCJob object."))
            return
        if not self._grbl_port_ready():
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a controller."))
            return
        ctx = self._al_capture_context()
        if self._al_capture_apply_input(target, units=ctx.units, require_segmented=False) is None:
            return
        units_cmd = 'G20' if ctx.units == 'IN' else 'G21'

        # each Start adds 1; the sequence number and the safety generation are read in 1 locked section
        with self._grbl_op_lock:
            self._start_seq += 1
            seq = self._start_seq
            safety_generation = self._grbl_safety_generation
        req = _StartRequest(seq, ctx, target, self._al_map_frame, self._al_map_imported,
                            safety_generation, check_mode)
        req.start_line = start_line

        if req.map_imported:
            if not self._confirm_imported_frame():
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
                return
            if not self._start_request_current(req):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The height map, the object or the machine state changed while the dialog was open. "
                    "Press Start again."))
                return

        op = self._grbl_reserve('frame', motion=False)
        if op is None:
            return

        def worker_task():
            result = {'error': _("The Start failed.")}
            try:
                result = self._grbl_frame_run(op, units_cmd)
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolGcodeSender._start_levelled() -> %s" % str(err))
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                result = {'error': '%s: %s' % (_("The serial port failed"), str(err))}
            except Exception as err:
                self.app.log.error("ToolGcodeSender._start_levelled() -> %s" % str(err))
            finally:
                # the GUI hears about it only after the port is free
                self._grbl_release(op)
                self.frame_ready_sig.emit(req, result)

        self.ui.start_button.setDisabled(True)
        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def _grbl_frame_run(self, op, units_cmd):
        """
        Phase 1 of a levelled Start (a worker thread, operation 'frame', no motion): GRBL must be Idle,
        then read its frame.

        :return: see _grbl_read_live_frame().
        """
        state = parse_grbl_status_state(self._grbl_query_status(op))
        if op.cancel.is_set():
            return {'error': _("The Start was stopped."), 'cancelled': True}
        if state == 'Check':
            return {'error': _("GRBL is in check mode. Send $C in the console, or press Reset.")}
        if state != 'Idle':
            return {'error': '%s: %s.' % (_("GRBL must be Idle to start the job. State"), state or _("no answer"))}
        return self._grbl_read_live_frame(op, units_cmd)

    def _on_frame_ready(self, req, result):
        """
        Slot of frame_ready_sig (GUI thread): phase 1 ended and the port is free. Bind or compare the frame,
        make the levelled job, reserve the operation 'stream' and start phase 2.
        """
        if not self._start_request_current(req):
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The Start request is old (the map, the object or the machine state changed). Press Start again."))
            self._start_free_button()
            return
        if 'error' in result:
            self.app.inform.emit('%s %s' % (
                '[WARNING_NOTCL]' if result.get('cancelled') else '[ERROR_NOTCL]', result['error']))
            self._start_free_button()
            return

        frame = result['frame']
        if self._al_map_imported and self._al_map_frame is None:
            # the first Start with this imported map binds it to the live frame
            self._al_map_frame = frame
            req.map_frame = frame
        elif not self._frames_match(frame, self._al_map_frame):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The machine frame (WCS or work offset) changed after the height map was made. Probe again."))
            self._start_free_button()
            return

        # the check of the stream reads the live state of the controller: it needs the operation first
        op = self._grbl_reserve('stream', motion=True)
        if op is None:
            self._start_free_button()
            return
        inputs = self._level_inputs(op, request=req)
        if inputs is None:
            self._grbl_release(op)
            self._start_free_button()
            return
        inputs.check_mode = req.check_mode
        self.app.inform.emit(_("Preparing the job"))

        limits = self._line_limits()

        def level_task():
            try:
                prepared = SenderGen.prepare_levelled_job(inputs, self._profile_compute, self.autolevell_gcode,
                                                          self._tlo_line, limits)
            except Exception as err:
                self.app.log.error("ToolGcodeSender._on_frame_ready() -> %s" % str(err))
                prepared = SenderGen._PreparedJob(inputs=inputs, error=str(err))
            self.job_prepared_sig.emit(prepared)

        self.app.worker_task.emit({'fcn': level_task, 'params': []})

    def _choose_start_line(self, line_count, beginning_only=False):
        dialog = GrblStartDialog(line_count, self.app.ui)
        cumulative = self.job_cumulative
        dialog.layout().insertWidget(0, QtWidgets.QLabel('%s ≈ %s' % (
            _("Estimated time"), SenderEstimate.clock_text(cumulative[-1] if cumulative else None))))
        if beginning_only:
            dialog.start_radio.setEnabled(False)
            reason = QtWidgets.QLabel(_("A new height map must start at the beginning.\n"
                                       "Probe first and use the existing map to start from a line."))
            reason.setWordWrap(True)
            dialog.layout().insertWidget(0, reason)
        elif self.ui.check_mode_cb.get_value() is True:
            dialog.layout().insertWidget(0, QtWidgets.QLabel(_("Check mode: validate without motion.")))
        try:
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return None
            return dialog.line_entry.get_value() if dialog.start_radio.get_value() == 'line' else 1
        finally:
            dialog.deleteLater()

    def _start_stream_worker(self, op, lines, numbers, check_mode, poll_ms, req=None):
        """Start the stream worker under the reserved operation `op` (GUI thread). `req`: a levelled Start."""
        self._console().set_program(lines)
        self.sender_io.on_job_start()
        target = self._target()
        source = target.source_file
        seq = getattr(self, '_start_seq', 0)
        generation = self._grbl_safety_generation
        self.ui.start_button.setDisabled(True)
        start_line = (req.start_line if req is not None and req.start_line is not None
                      else self._choose_start_line(len(lines)))
        with self._grbl_op_lock:
            current = (self._grbl_active_locked(op) and not op.cancel.is_set() and
                       generation == self._grbl_safety_generation and seq == getattr(self, '_start_seq', 0))
        if start_line is None or not current or self._target() is not target or target.source_file != source:
            self._grbl_release(op)
            self._start_free_button()
            if op.cancel.is_set():
                self._stream_result = {}
                self.stream_ended_sig.emit('stopped')
            if start_line is not None:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "The program or machine state changed while the dialog was open. Press Start again."))
            return
        profile = active_profile_of(self.app)
        if (profile is not None and self._stream_setup_key is not None and
                self.live_setup_changed(self._stream_setup_key, op, profile, getattr(target, 'pp_geometry_name', None))):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The offsets, the WCS, G92 or the tool length offset of the controller changed after the machine "
                "profile check. Press Start again."))
            self._grbl_release(op)
            self._start_free_button()
            return
        if start_line == 1:
            self._start_stream_launch(op, target, lines, numbers, check_mode, poll_ms, req, None)
            return
        # the preamble and its check run in a worker; _on_restart_prepared() goes on
        context = SimpleNamespace(op=op, target=target, source=source, seq=seq, generation=generation, lines=lines,
                                  numbers=numbers, check_mode=check_mode, poll_ms=poll_ms, req=req,
                                  start_line=start_line, profile=profile)
        travel_z = getattr(target, 'z_move', None)
        units, decimals = target.units, target.coords_decimals
        spindle_dwell = float(self.app.options.get("tools_grbl_spindle_dwell", 3.0))

        limits = self._line_limits()

        def restart_task():
            def compute(text):
                # the lines are the prepared (corrected) program: the preamble and the resumed part are in the
                # output frame already. Check them as one program from the live position; correct nothing again.
                return self._profile_compute(text, target, profile, op, correct=False)
            try:
                prepared = SenderGen.prepare_restart(lines, start_line, travel_z, units, decimals,
                                                     None if profile is None else compute, spindle_dwell, limits)
            except Exception as err:
                self.app.log.error("ToolGcodeSender._start_stream_worker() -> %s" % str(err))
                prepared = SenderGen._PreparedRestart(error=str(err))
            prepared.context = context
            self.restart_prepared_sig.emit(prepared)

        self.app.worker_task.emit({'fcn': restart_task, 'params': []})

    def _on_restart_prepared(self, prepared):
        """Slot of restart_prepared_sig (GUI thread): present the check of the restart, then start the stream."""
        c = prepared.context
        if not self._start_current(c.op, c.seq, c.generation, c.target, c.source):
            self._start_abandoned(c.op)
            return
        if prepared.error is not None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % prepared.error)
            self._grbl_release(c.op)
            self._start_free_button()
            return
        if prepared.outcome is not None and not self._profile_present(c.profile, prepared.outcome):
            self._grbl_release(c.op)
            self._start_free_button()
            return
        if not self._start_current(c.op, c.seq, c.generation, c.target, c.source):
            self._start_abandoned(c.op)
            return
        self.app.inform.emit('%s: %d' % (_("Starting from program line"), c.start_line))
        self._start_stream_launch(c.op, c.target, c.lines, c.numbers, c.check_mode, c.poll_ms, c.req,
                                  (prepared.preamble, c.start_line - 1, prepared.resumed))

    def _start_current(self, op, seq, generation, target, source):
        """:return: True if the operation, the start sequence, the safety generation and the program are the ones of
        the Start that asked (GUI thread)."""
        with self._grbl_op_lock:
            current = (self._grbl_active_locked(op) and not op.cancel.is_set() and
                       generation == self._grbl_safety_generation and seq == self._start_seq)
        return current and self._target() is target and target.source_file == source

    def _start_abandoned(self, op):
        """A Start is old (Stop, or the program or the machine state changed): free the port and the button."""
        self._grbl_release(op)
        self._start_free_button()
        if op.cancel.is_set():
            self._stream_result = {}
            self.stream_ended_sig.emit('stopped')
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The program or machine state changed while the job was prepared. Press Start again."))

    def _start_stream_launch(self, op, target, lines, numbers, check_mode, poll_ms, req, restart):
        """The stream starts (GUI thread): the lock of the view, the 3D preparation and the stream thread."""
        self._program_paused = False
        if not check_mode:
            self._lock_execution_view()
        self._preview_generation = self.__dict__.get('_preview_generation', 0) + 1
        if self.view3d is not None:
            # the 3D path is prepared in a worker: the stream does not wait for it
            generation = self._preview_generation
            view = self.view3d
            units, decimals = target.units, target.coords_decimals
            arc_segment = float(self.app.options.get("tools_grbl_arc_segment", 0.5))
            split_for_display = (self.ui.autolevel_cb.get_value() is not True and
                                 self.app.options.get("tools_grbl_arc_mode", 'split') != 'split')

            def prepare_task():
                result = {'stream': True, 'op': op, 'generation': generation, 'view': view, 'target': target}
                try:
                    display, indexes, chords = SenderGen.display_lines(
                        lines, units, arc_segment, decimals, split_for_display)
                    segments = parse_toolpath(display)
                    if indexes is not None:
                        segments = [(indexes[i], a, b, rapid) for i, a, b, rapid in segments]
                    result['prepared'] = SenderView3d.prepare_toolpath(segments, chords)
                except Exception as err:
                    self.app.log.error("ToolGcodeSender.on_start() -> %s" % str(err))
                    result['error'] = str(err)
                self.preview_ready_sig.emit(result)

            self.app.worker_task.emit({'fcn': prepare_task, 'params': []})

        def worker_task():
            how = 'error'
            try:
                how = self._grbl_stream_run(op, lines, numbers, check_mode, poll_ms / 1000.0, req,
                                            restart=restart)
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolGcodeSender.on_start() -> %s" % str(err))
                how = self._grbl_stream_crashed(op, 'port_error')
            except Exception as err:
                self.app.log.error("ToolGcodeSender.on_start() -> %s" % str(err))
                how = self._grbl_stream_crashed(op, 'error')
            finally:
                if how == 'finished':
                    # the program can change the WCS (G54..G59): read it before the release
                    try:
                        self._grbl_read_active_wcs(op)
                    except Exception as err:
                        self.app.log.error("ToolGcodeSender.on_start() -> %s" % str(err))
                # the operation is released after the status map; the GUI hears about it after the release
                self._grbl_release(op)
                self.stream_ended_sig.emit(how)

        self.ui.start_button.setDisabled(True)
        self.ui.progress_bar.setValue(0)
        self.app.inform.emit('%s: %d' % (_("Streaming lines"), len(lines)))
        self._console_pending.clear()
        self._progress_latest = None
        self._progress_shown = None
        self._stream_clock = QtCore.QElapsedTimer()
        self._stream_clock.start()
        self._show_job_time(0, len(lines))
        self._stream_ui_timer.start()
        try:
            self._start_stream_thread(worker_task)
        except Exception:
            self._stream_ui_timer.stop()
            self._unlock_execution_view()
            self._grbl_release(op)
            self._start_free_button()
            raise

    def _start_stream_thread(self, fn):
        """The stream runs for the whole job: its own thread, so it never blocks an app worker."""
        threading.Thread(target=fn, name='GrblSender-stream', daemon=True).start()

    def _grbl_stream_crashed(self, op, reason):
        """An exception ended the stream worker: stop the stream. Stop or Reset of the user keep their state."""
        with self._grbl_op_lock:
            if op.cancel.is_set():
                return 'stopped'
            self._grbl_abort_stream_locked(op, reason)
        return reason

    def _grbl_stream_io(self, op, req=None):
        """
        The 2 callables that the pure streamer uses. All port access of the stream goes through them.

        :param req: the _StartRequest of a levelled Start, else None. The write of a levelled Start checks
            the request before the first job line (A8.3 step 4.4): `write.first_job_line_written` is the
            list [bool] of that flag.
        :return: (write, readline)
        """
        first_job_line_written = [False]
        status_request = self._grbl_realtime_byte(b'?')

        def write(data):
            job_line = data not in (b'?', status_request)
            # the request check, the write and the flag are 1 locked section: an invalidation either gets
            # the lock first (no job line is written) or comes after the first job line (the job runs on)
            with self._grbl_op_lock:
                if op.cancel.is_set():
                    return 'cancelled'
                if not self._grbl_active_locked(op):
                    return 'port_error'
                if req is not None and job_line and not first_job_line_written[0] and req.seq != self._start_seq:
                    return 'stale'
                try:
                    count = op.port.write(data)
                except (serial.SerialException, OSError) as err:
                    self.app.log.error("ToolGcodeSender.write() -> %s" % str(err))
                    return 'port_error'
                # a short write is a transport failure, never 'stale'
                if count is not None and count != len(data):
                    return 'port_error'
                if job_line:
                    first_job_line_written[0] = True
                return 'ok'

        write.first_job_line_written = first_job_line_written

        def readline():
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op):
                    return b''
            return op.port.readline()

        return write, readline

    def _grbl_stream_control(self, op, data):
        """
        A real-time byte of the stream worker, under the operation lock: the tool change acknowledge (0xA3).

        :return: 'ok', 'cancelled' or 'port_error'.
        """
        with self._grbl_op_lock:
            if op.cancel.is_set():
                return 'cancelled'
            if not self._grbl_active_locked(op):
                return 'port_error'
            if not self._grbl_write_realtime_locked(data):
                return 'port_error'
            return 'ok'

    def _grbl_queue_tool_command(self, command):
        """Tool-state commands share the stream reader; never reserve a competing operation."""
        with self._grbl_op_lock:
            if not self.__dict__.get('_grbl_tool_change_pending'):
                return False
            if (callable(getattr(self.grbl_ser_port, 'prepare_program', None)) or
                    (self.__dict__.get('_grbl_caps') or {}).get('automatic_tool_change', False)):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "Operator commands are unavailable during an automatic or adapter-owned tool change."))
                return True
            op = self._grbl_op
            if (op is None or op.cancel.is_set() or op.failed or self._grbl_recovery is not None or
                    self._grbl_reset_pending or self.__dict__.get('_grbl_tool_command_busy')):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Tool change command is busy or recovery is required."))
                return True
            # Firmware settings and unrestricted motion aren't part of native touch-off.
            if ('\n' in command or '\r' in command or validate_stream_lines([command], *self._line_limits()) is not None or
                    not re.fullmatch(r'(?:\$(?:TPW|TLR|G|#)|\$J=[A-Z0-9+ .\-]+|'
                                     r'G43\.1\s+Z[+\-]?(?:\d+(?:\.\d*)?|\.\d+)|G49)',
                                     command, re.IGNORECASE)):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "During Tool state use axis jogs, $TPW, $TLR, $G, $#, G43.1 Z... or G49 only."))
                return True
            self._grbl_tool_commands.append(command)
            self._grbl_tool_command_busy = True
            probe = self.__dict__.get('_tool_tlo_probe')
            if probe is not None and command == '$#':
                probe['queued'] = True
            return True

    def _grbl_stream_run(self, op, lines, numbers, check_mode, poll_interval, req=None, restart=None):
        """
        The stream worker under 1 operation (a worker thread; it reads no widgets).

        :param numbers:       the line number in the G-code text of each line of `lines`.
        :param check_mode:    True if the user asked for check mode for this job.
        :param poll_interval: seconds between 2 status requests.
        :param req:           the _StartRequest of a levelled Start (phase 2 of A8.3), else None.
        :param restart:       (preamble, zero-based console index, resumed lines), or None for Beginning.
        :return: `how`: 'finished', 'check_passed', 'check_passed_needs_home', 'check_failed',
            'check_exit_failed', 'stopped', 'incomplete', 'rejected' (nothing was sent), 'stale',
            'stale_needs_home' (a stale request: GRBL got no job line), or the name of a failure
            ('port_error', 'timeout', 'error', 'alarm', 'line_too_long', 'unexpected_reset').
            'unexpected_reset' means a Grbl banner was seen mid-stream (controller reset).
            It is final: the success values are set only after the commit of A5.3.
        """
        self._stream_result = {}
        port = op.port
        with self._grbl_op_lock:
            if self._grbl_op is not op or op.cancel.is_set() or op.failed:
                return 'stopped'
            self._grbl_tool_fence_op = None
            self._grbl_tool_change_pending = False
            self._grbl_tool_commands = []
            self._grbl_tool_command_busy = False
            self._tool_resume = None
            self._tool_tlo_probe = None
        if (self.__dict__.get('_grbl_caps') or {}).get('probe') is False and any(
                re.search(r'G\s*38\.[2345](?!\d)', line, re.IGNORECASE) for line in lines):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The controller has no probe input. Probing is unavailable."))
            return 'rejected'
        # the final lines (after the corrections, the arc split and the restart preamble): a long line writes
        # nothing, so no $C, no dirty flag and no recovery latch
        preamble, start_index, resumed = restart if restart is not None else ([], 0, lines)
        bad_index = validate_stream_lines(preamble + resumed, *self._line_limits())
        if bad_index is not None:
            if bad_index < len(preamble):
                message = _("The restart preparation exceeds the GRBL line length limit.")
            else:
                position = start_index + bad_index - len(preamble)
                message = _("Line %d of the G-code is longer than GRBL accepts. The job was not started.") % (
                    numbers[position] if 0 <= position < len(numbers) else position + 1)
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            return 'rejected'

        # A8.3 step 4.1: the request must be current when the worker starts
        if req is not None:
            with self._grbl_op_lock:
                stale = op.safety_generation != req.safety_generation or req.seq != self._start_seq
            if stale:
                return self._grbl_stale_cleanup(op, False)

        # 1. GRBL must be Idle
        state = parse_grbl_status_state(self._grbl_query_status(op))
        if state != 'Idle':
            return self._grbl_start_refused(op, state)

        # 2. A5.4 step 2.2: a levelled job compares the live frame ($$, $G, $#) with the bound frame
        #    here. GRBL runs $# only in Idle, so this step comes before $C.
        if req is None:
            # a plain job reads $13 (the report units of the status reports) before $C
            status = self._grbl_read_report_units(op)
            if status == 'cancelled' or op.cancel.is_set():
                return 'stopped'
            if status in ('timeout', 'port_error'):
                with self._grbl_op_lock:
                    self._grbl_abort_stream_locked(op, status)
                return status
        else:
            result = self._grbl_read_live_frame(op, 'G20' if req.al_ctx.units == 'IN' else 'G21')
            if 'error' in result:
                return self._grbl_frame_refused(op, result['error'], result.get('cancelled', False))
            if not self._frames_match(result['frame'], req.map_frame):
                return self._grbl_frame_refused(op, _(
                    "The machine frame (WCS or work offset) changed after the height map was made. Probe again."))

        # The stream epoch is dirty from the first write: until a confirmed completion or the reset
        # banner, GRBL can hold lines (and their ok replies) of this job.
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op):
                return 'stopped' if op.cancel.is_set() else 'incomplete'
            # A8.3 step 4.3: after the last preflight query and before $C
            stale = req is not None and req.seq != self._start_seq
            if not stale:
                self._grbl_stream_dirty = True
        if stale:
            return self._grbl_stale_cleanup(op, False)

        # 3. check mode for this job
        if check_mode:
            how = self._grbl_enter_check_mode(op)
            if how is not None:
                return how

        # old data in the receive buffer is not a reply to the job
        with self._grbl_op_lock:
            text, status = self._grbl_prepare_locked(op, port)
            if status is not None:
                if status == 'cancelled' or op.cancel.is_set():
                    return 'stopped'
                self._grbl_abort_stream_locked(op, status, text)
                return status

        # 4. stream
        write, readline = self._grbl_stream_io(op, req)
        prefix_count = len(preamble)

        def program_done(done):
            # Preparation commands aren't lines of the displayed program.
            return start_index + done - prefix_count if done > prefix_count else 0

        program = preamble + resumed
        prepare_program = getattr(port, 'prepare_program', None)
        tool_change_ack = (not callable(prepare_program) and
                           not (self.__dict__.get('_grbl_caps') or {}).get('automatic_tool_change', False))
        if program and not check_mode and callable(prepare_program):
            try:
                prepare_program(program)
            except (serial.SerialException, OSError, ValueError) as error:
                self._stream_result = {'done': 0, 'last_text': str(error),
                                       'line': numbers[start_index] if start_index < len(numbers) else 1,
                                       'errors': [], 'error_indices': []}
                with self._grbl_op_lock:
                    self._grbl_abort_stream_locked(op, 'error', str(error))
                return 'error'

        # the recheck before Resume (machine profile): the progress, the position and the TLO of the open tool change
        stream_setup = self.__dict__.get('_stream_setup')
        acked = [0]
        last_mpos = [None]
        tlo = [None if stream_setup is None else stream_setup.tlo]
        last_command = [None]

        def program_line(index):
            index = start_index + max(0, index - prefix_count)
            return numbers[index] if 0 <= index < len(numbers) else index + 1

        def on_status(line):
            report = parse_grbl_status_report(line)
            with self._grbl_op_lock:
                if self._grbl_op is not op or op.cancel.is_set() or op.failed or self.grbl_ser_port is not port:
                    return
                if report is not None:
                    if 'mpos' in report:
                        last_mpos[0] = report['mpos']
                    elif 'wpos' in report and 'wco' in report:
                        last_mpos[0] = tuple(w + c for w, c in zip(report['wpos'], report['wco']))
                    if report['state'] == 'Tool' or report.get('tool_pending'):
                        if not self._grbl_tool_change_pending:
                            # This callback precedes A3; Resume must wait through the reply fence.
                            self._grbl_tool_fence_op = op
                    elif report['state'] in ('Run', 'Idle'):
                        self._grbl_tool_change_pending = False
            self._emit_sender_status(report, op)

        def on_tool_change():
            with self._grbl_op_lock:
                if (self._grbl_op is not op or op.cancel.is_set() or op.failed or
                        self.grbl_ser_port is not port or self.__dict__.get('_grbl_tool_fence_op') is not op):
                    return
                self._grbl_tool_change_pending = True
                self._grbl_tool_fence_op = None
                if stream_setup is not None:
                    # the M6 line is in flight: every line before it is acknowledged, the rest of the program follows
                    first = acked[0] + 1
                    self._tool_resume = {
                        'text': '\n'.join(program[first:]) + '\n', 'setup': stream_setup, 'tlo': tlo[0],
                        'mpos': last_mpos[0], 'version': 0,
                        'numbers': [program_line(first + i) for i in range(len(program) - first)]}
            if not tool_change_ack:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                    "Tool change is controlled by the controller or adapter. Operator commands are unavailable."))
                return
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "Tool change: install the tool, jog/touch off with $TPW or set G43.1 Z..., then press Cycle Start."))

        def tool_command():
            with self._grbl_op_lock:
                command = self._grbl_tool_commands.pop(0) if self._grbl_tool_commands else None
                last_command[0] = command
                return command

        def tool_command_done(text):
            with self._grbl_op_lock:
                if self._grbl_op is not op or op.cancel.is_set() or op.failed or self.grbl_ser_port is not port:
                    return
                self._grbl_tool_command_busy = False
                probe = self._tool_tlo_probe
                if probe is not None and last_command[0] == '$#':
                    probe['done'].set()
                if text.lower() == 'ok' and self._tool_resume is not None:
                    command = last_command[0] or ''
                    new_tlo = re.fullmatch(r'G43\.1\s+Z([+\-]?(?:\d+(?:\.\d*)?|\.\d+))', command, re.IGNORECASE)
                    if new_tlo is not None:
                        tlo[0] = float(new_tlo.group(1))
                    elif command.upper() == 'G49':
                        tlo[0] = 0.0
                    elif command.upper() in ('$TPW', '$TLR'):
                        tlo[0] = None       # the controller measured it: the Sender does not know the value
                    self._tool_resume['tlo'] = tlo[0]
                    self._tool_resume['version'] += 1
            if text.lower() != 'ok':
                self.app.inform.emit('[ERROR_NOTCL] %s' % text)

        def on_stream_line(text):
            # the reply lines of the `$#` that the recheck before Resume asked for
            probe = self._tool_tlo_probe
            if probe is not None and text.startswith('['):
                probe['lines'].append(text)
            self._console_pending.append(text)

        def on_progress(done, _total):
            acked[0] = done
            self._progress_latest = (program_done(done), len(lines))

        rx_size, max_line = self._line_limits()
        res = stream_lines(
            program, write, readline, rx_size=rx_size, max_line=max_line, check_mode=check_mode,
            on_progress=on_progress,
            on_line=on_stream_line,
            poll_status=True, poll_interval=poll_interval,
            on_status=on_status,
            status_request=self._grbl_realtime_byte(b'?'),
            write_control=lambda data: self._grbl_stream_control(op, data),
            # the E-Cut adapter has its own M6 protocol: prepare_program() gives it the whole job
            tool_change_ack=tool_change_ack,
            on_tool_change=on_tool_change, tool_command=tool_command,
            on_tool_command_done=tool_command_done)
        status = res['status']

        source_number = program_line

        failed_index = res['errors'][-1][0] if res['errors'] else res['done']
        self._stream_result = {
            'done': program_done(res['done']),
            'last_text': res['last_text'],
            'line': source_number(res.get('line_index', failed_index)),
            'errors': [source_number(index) for index, _text in res['errors']],
            'error_replies': [text for _index, text in res['errors']],
            'error_indices': [start_index + index - prefix_count for index in
                              ([index for index, _text in res['errors']] +
                               ([res['line_index']] if status == 'line_too_long' else []))
                              if index >= prefix_count],
        }
        if res['errors'] and res['errors'][-1][0] < prefix_count:
            self._stream_result['last_text'] = '%s: %s' % (_("Restart preparation"), res['last_text'])

        # the status map of A5.2 step 6
        if status == 'finished':
            return self._grbl_stream_finish(op, res, check_mode)
        if status == 'cancelled':
            return 'stopped'
        if status == 'stale':
            # the levelled Start request is old; GRBL got no job line (A8.3 step 4.5)
            return self._grbl_stale_cleanup(op, check_mode)
        with self._grbl_op_lock:
            if op.cancel.is_set():
                # Stop or Reset already owns the recovery state
                return 'stopped'
            self._grbl_abort_stream_locked(op, status, res['last_text'])
        return status

    def _grbl_frame_refused(self, op, text, cancelled=False):
        """
        The live frame can not start the levelled job. Nothing was written to GRBL, and the stream epoch is
        clean. The user is told why; a failure that the read latched shows its recovery text.

        :return: `how`.
        """
        if cancelled or op.cancel.is_set():
            return 'stopped'
        with self._grbl_op_lock:
            latched = self._grbl_recovery is not None
        if latched:
            text += ' ' + self._grbl_recovery_text()
        self.app.inform.emit('[ERROR_NOTCL] %s' % text)
        return 'rejected'

    def _grbl_stale_cleanup(self, op, check_entered):
        """
        The levelled Start request is old and GRBL got no job line (A8.3). This is not a cancellation and not
        a failure: no recovery state changes here. A `$C` that was sent still needs a confirmed outcome.

        The stream epoch is clean again only through _grbl_commit_success_locked(), after the cleanup, and
        not after a failed exit from check mode.

        :param check_entered: True if the job is in check mode (GRBL is in `Check`).
        :return: `how`: 'stale', 'stale_needs_home', 'check_exit_failed', 'stopped' or 'incomplete'.
        """
        exit_state = None
        if check_entered:
            with self._grbl_op_lock:
                if op.cancel.is_set():
                    return 'stopped'
                if op.failed:
                    return 'incomplete'
            exit_state = self._grbl_exit_check_mode(op)
            if exit_state == 'failed':
                with self._grbl_op_lock:
                    return 'stopped' if op.cancel.is_set() else 'check_exit_failed'
        with self._grbl_op_lock:
            if not self._grbl_commit_success_locked(op):
                # a Stop or a Reset during the cleanup keeps its recovery state
                return 'stopped'
        return 'stale_needs_home' if exit_state == 'alarm' else 'stale'

    def _grbl_start_refused(self, op, state):
        """GRBL is not Idle at the start of the stream worker. Nothing was written to GRBL."""
        if op.cancel.is_set():
            return 'stopped'
        if state is None:
            # the status query failed; it latched the failure where it applies
            with self._grbl_op_lock:
                self._grbl_abort_stream_locked(op, 'timeout')
            return 'timeout'
        if state == 'Check':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "GRBL is in check mode. Send $C in the console, or press Reset."))
            return 'rejected'
        text = '%s: %s.' % (_("GRBL must be Idle to start the job. State"), state)
        with self._grbl_op_lock:
            latched = self._grbl_recovery is not None
        if latched:
            text += ' ' + self._grbl_recovery_text()
        self.app.inform.emit('[ERROR_NOTCL] %s' % text)
        return 'rejected'

    def _grbl_enter_check_mode(self, op):
        """
        Send $C for this job. GRBL must answer [MSG:Enabled] and then report the state Check.

        :return: None if GRBL is in check mode, else the `how` that ends the job (the abort is done).
        """
        text, status = self._grbl_transact(op, '$C', timeout=5.0)
        if status == 'cancelled':
            return 'stopped'
        if status == 'ok' and '[msg:enabled]' in (text or '').lower():
            state = parse_grbl_status_state(self._grbl_query_status(op))
            if state == 'Check':
                return None
            how = 'check_exit_failed'       # GRBL said Enabled: it can be in check mode
        elif status == 'ok' or status == 'error':
            how = 'error'                   # GRBL refused $C, or answered something else
        else:
            how = 'check_exit_failed'       # no answer: GRBL can be in check mode
        with self._grbl_op_lock:
            if op.cancel.is_set():
                return 'stopped'
            self._grbl_abort_stream_locked(op, status if status in ('timeout', 'port_error') else 'error', text)
        return how

    def _grbl_stream_finish(self, op, res, check_mode):
        """
        The streamer acknowledged all lines. That does not mean that the job is done: wait for Idle
        (normal mode) or leave the check mode (A5.5). `how` is final only here.
        """
        if check_mode:
            # only a complete validation leaves the check mode by $C
            with self._grbl_op_lock:
                if op.cancel.is_set():
                    return 'stopped'
                if op.failed:
                    return 'incomplete'
            exit_state = self._grbl_exit_check_mode(op)
            with self._grbl_op_lock:
                if exit_state == 'failed':
                    return 'check_exit_failed'
                if not self._grbl_commit_success_locked(op):
                    return 'stopped'
                if res['errors']:
                    return 'check_failed'
                return 'check_passed' if exit_state == 'idle' else 'check_passed_needs_home'

        idle = self._grbl_wait_idle(op)
        with self._grbl_op_lock:
            if idle:
                return 'finished' if self._grbl_commit_success_locked(op) else 'stopped'
            if op.cancel.is_set():
                return 'stopped'
            self._grbl_abort_stream_locked(op, 'timeout')
        return 'incomplete'

    def on_emergency_stop(self):
        """The hardware stop remains available even while an offline simulation is open."""
        if not self._action_ok('estop'):
            return
        dialog = self.__dict__.get('_simulation')
        if dialog is not None:
            dialog.stop()
            self._simulation = None
        self._stop_now()
        if dialog is not None:
            dialog.close()

    def on_stream_stop(self):
        """The Stop button: see _stop_now()."""
        if self._action_ok('stop'):
            self._stop_now()

    def _stop_now(self):
        """
        Stop: feed hold now; the stream worker stops writing. Then a soft reset, which also stops
        the spindle. If no motion ran, the position is kept and the machine does not need Home.
        """
        if self.__dict__.get('_simulation') is not None:
            self._hold_now()
            return
        with self._grbl_op_lock:
            op = self._grbl_op
            idle = ((op is None or not op.motion) and not self._grbl_stream_dirty and self._grbl_recovery is None
                    and self._reported_stationary())
        self._hold_now()
        if not self._grbl_port_ready():
            return
        # a moving machine brakes in the feed hold; the reset byte waits until the hold is complete
        self._reset_now(keep_position=idle, wait_hold=not idle)

    def on_unlock(self):
        if not self._action_ok('unlock'):
            return

        def task(op):
            text, status = self._grbl_transact(op, '$X', timeout=5.0)
            if status != 'ok':
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                    _("Unlock failed"), code_text(grbl_reply_error(text), self._grbl_grblhal) or text or status))
            self.unlock_result_sig.emit(status == 'ok')
        self._grbl_start_op('unlock', task)

    def _on_unlock_result(self, ok):
        """Slot of unlock_result_sig (GUI thread): a good $X clears the alarm row."""
        if ok:
            self.ui.status_strip.set_alarm(None)

    def on_save_levelled_gcode(self):
        # always make the job again: the lines of an earlier Start can be old. The design (levelled) lines come
        # without the profile: the file gate below corrects and checks them like every file writer
        if not self.build_stream_job(apply_profile=False):
            return
        text = '\n'.join(self.job_lines) + '\n'
        if active_profile_of(self.app) is not None:
            target = self._target()
            if self.ui.autolevel_cb.get_value() is not True:
                # the source keeps the markers of the fixed sequences (the lines of the job have lost them)
                text = target.source_file
            gate = gate_file_text(
                self.app.options.get('global_machine_profile'), self.app.options.get('global_machine_profiles_folder'),
                self.app.data_path, text, getattr(target, 'pp_geometry_name', None), target.units, correct=True,
                uncorrected_source=True, decimals=target.coords_decimals)
            report_gate(self.app, gate)
            if not gate.ok:
                self.app.inform.emit('[ERROR_NOTCL] %s' % gate.error)
                return
            if gate.note:
                self.app.inform.emit('[WARNING_NOTCL] %s' % gate.note)
            if gate.text is not None:
                text = gate.text
        _filter_ = "G-Code Files (*.nc *.gcode *.cnc *.tap);;All Files (*.*)"
        try:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Save levelled G-code"),
                directory=self.app.get_last_save_folder() + '/' + str(self.job_target_name) + '_levelled',
                ext_filter=_filter_)
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(caption=_("Save levelled G-code"), ext_filter=_filter_)
        if not filename:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return
        try:
            with open(filename, 'w') as f:
                f.write(text)
        except (FileNotFoundError, PermissionError) as err:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not save the file"), str(err)))
            return
        self.app.inform.emit('[success] %s: %s' % (_("Saved"), filename))

    def _grbl_realtime(self, data):
        with self._grbl_op_lock:
            written = self._grbl_write_realtime_locked(data)
        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a controller."))
        return written

    def _on_hold_button(self):
        if self._action_ok('hold'):
            self._grbl_realtime(b'!')

    def on_program_line_selected(self, index):
        """Inspect selected moves while idle or held; running selection follows progress."""
        if self._grbl_op is not None and self.__dict__.get('_program_paused') is not True:
            console = self.app.ui.grbl_console_dock.widget()
            if isinstance(console, GrblConsole):
                console._follow_program(console.follow_cb.isChecked())
            return
        if self.view3d is not None:
            self.view3d.set_selected_line(index)

    def _flush_stream_ui(self, drain=False):
        """Show the progress and the console lines that the stream worker stored (GUI thread, 100 ms timer)."""
        latest = self._progress_latest
        if latest is not None and latest != self._progress_shown:
            self._progress_shown = latest
            self._on_stream_progress(*latest)
        pending = self._console_pending
        batch = []
        while pending and (drain or len(batch) < 500):
            batch.append(pending.popleft())
        if batch:
            self._console().append('\n'.join(batch))

    def _elapsed_text(self):
        clock = self._stream_clock
        return SenderEstimate.clock_text(0 if clock is None else clock.elapsed() / 1000.0)

    def _show_job_time(self, done, total):
        """The elapsed time and the estimated time that is left, under the progress bar."""
        cumulative = self.job_cumulative
        left = None if not cumulative else SenderEstimate.remaining(cumulative, done, self._feed_override_pct)
        self.ui.time_label.setText('%s  /  ≈ %s %s' % (self._elapsed_text(), SenderEstimate.clock_text(left),
                                                       _("left")))

    def _on_stream_progress(self, done, total):
        self.ui.progress_bar.setValue(int(done * 100 / total) if total else 0)
        self._show_job_time(done, total)
        console = self.app.ui.grbl_console_dock.widget()
        if isinstance(console, GrblConsole):
            console.update_progress(done)
        if self.view3d is not None:
            # `done` counts stream lines; the segments of the toolpath carry the index of their stream line
            self.view3d.set_accepted(done - 1)
            if self.__dict__.get('_program_paused') is not True and self.view3d.has_selection:
                self.view3d.set_selected_line(None)

    # the last live setup key and the $G / $# answers of this connection (see live_setup())
    live_setup_key = None
    _live_cache = None
    _stream_setup_key = None      # the live_setup_key of the state that the check of the stream read
    _stream_setup = None          # the Setup of that check (None: the stream runs without a machine profile)
    _tool_resume = None           # the state of the open tool change for the recheck before Resume (see below)
    _resume_refused_version = None    # the version of _tool_resume that the recheck refused
    _tool_tlo_probe = None        # the `$#` read of the recheck: {'lines': [...], 'done': Event}, else None

    def _read_tool_tlo(self):
        """
        The TLO of the controller after a probed tool change ($TPW / $TLR): `$#` goes through the tool command
        queue of the stream worker, the one reader of the port (GUI thread; it waits as long as live_setup() does).

        :return: the TLO in the units of the job, or None if the read failed or reports no TLO.
        """
        scale = self._report_scale()
        if scale is None:
            return None
        probe = {'lines': [], 'done': threading.Event()}
        with self._grbl_op_lock:
            self._tool_tlo_probe = probe
        try:
            self._grbl_queue_tool_command('$#')
            # a refused command (busy, recovery, adapter-owned tool change) gets no reply: do not wait for one
            if not probe.get('queued') or not probe['done'].wait(5.0):
                return None
        finally:
            with self._grbl_op_lock:
                self._tool_tlo_probe = None
        text = '\n'.join(probe['lines'])
        if '[TLO' not in text:
            return None
        try:
            raw = parse_grbl_offsets(text)['TLO']
        except (ValueError, KeyError):
            return None
        return (raw[2] if isinstance(raw, tuple) else raw) * scale

    def _tool_change_recheck(self):
        """
        The recheck before Resume (GUI thread; every route that resumes the stream calls it first). After a tool
        change, the REMAINING motion of the prepared program is checked again with the TLO that the tool change set,
        from the position where the machine waits. Nothing is corrected again.

        :return: False if the Resume is refused (the user was told; a reject disables the Resume button).
        """
        with self._grbl_op_lock:
            state = self._tool_resume
            if (state is None or not self.__dict__.get('_grbl_tool_change_pending') or
                    self.__dict__.get('_grbl_tool_command_busy') or self._grbl_recovery is not None):
                return True
            state = dict(state)
        profile = active_profile_of(self.app)
        if profile is None:
            return True
        scale = self._report_scale()
        mpos = state['mpos']
        start = None if mpos is None or scale is None else tuple(value * scale for value in mpos)
        if state['tlo'] is None:
            # $TPW / $TLR: the controller measured the tool length, so the controller tells it
            state['tlo'] = self._read_tool_tlo()
        setup = dataclasses.replace(state['setup'], start=start, tlo=state['tlo'])
        _out, result = prepare_output(state['text'], Purpose.STREAM, setup, profile, correct=False)
        numbers = state['numbers']

        def source_line(line):
            return numbers[line - 1] if 0 < line <= len(numbers) else line

        if has_reject(result):
            first = next(v for v in result.violations if v.severity == 'reject')
            message = _('Machine profile "%s": resume refused after the tool change. Line %d (%s): %s') % (
                profile.name, source_line(first.line), first.kind.value, first.message)
            report_gate(self.app, FileGate(False, message, result=result))
            self.app.inform.emit('[ERROR_NOTCL] %s' % message)
            self._resume_refused_version = state['version']
            self.ui.resume_button.setDisabled(True)
            return False
        note = None
        if result.status is CheckStatus.VIOLATIONS:
            note = _('Machine profile: %d warnings after the tool change') % sum(
                1 for v in result.violations if v.kind is not ViolationKind.UNVERIFIED)
        elif result.status is CheckStatus.PARTIAL:
            note = _('Machine profile: the rest of the program is partially checked after the tool change')
        report_gate(self.app, FileGate(note=note, result=result))
        if note is None or self._confirm_profile_resume(note):
            return True
        self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        return False

    def live_setup(self, op, profile, dialect=None, refresh=False):
        """
        The Setup of the Machine Profile check from the state that the controller reports: MPos of the status
        report, the stored offsets and G92 / TLO of `$#`, the active WCS and the units of `$G`. GRBL, grblHAL and
        the E-Cut port (it answers the same reports) are read in the same way. A worker thread that holds `op`.
        `$G` and `$#` are requested once per connection (`refresh` asks again); an answer that is missing or
        malformed leaves its items unknown (None).

        :return: the Setup, or None if the Sender is not connected. self.live_setup_key holds the key of the state.
        """
        port = self.grbl_ser_port
        if not self._grbl_connected or port is None:
            return None
        cache = self._live_cache
        if refresh or cache is None or cache[0] is not port:
            texts = []
            for command in ('$G', '$#'):
                text, status = self._grbl_transact(op, command, timeout=5.0, echo=False)
                texts.append(text if status == 'ok' else None)
            cache = self._live_cache = (port, texts[0], texts[1])
        gc_text, offsets_text = cache[1], cache[2]

        units = None
        if gc_text:
            for word, name in (('G20', 'inch'), ('G21', 'mm')):
                if re.search(r'(?<![A-Z0-9.])%s(?![0-9.])' % word, gc_text):
                    units = name
        active_wcs = None
        if gc_text:
            try:
                active_wcs = parse_grbl_active_wcs(gc_text)
            except ValueError:
                active_wcs = None

        # the positions are in the report units of GRBL ($13); the Setup holds the units of the program
        inches = self._grbl_report_inches
        factor = None
        if inches is not None and units is not None:
            factor = 1.0 if (units == 'inch') == bool(inches) else (25.4 if inches else 1.0 / 25.4)

        def scaled(value):
            return None if value is None or factor is None else tuple(v * factor for v in value)

        wcs_offsets = g92 = tlo = None
        if offsets_text:
            try:
                records = parse_grbl_offsets(offsets_text)
            except ValueError:
                records = {}
            found = {k: scaled(v) for k, v in records.items() if re.fullmatch(r'G5[4-9](\.[1-3])?', k)}
            wcs_offsets = found or None
            if wcs_offsets is not None and any(v is None for v in wcs_offsets.values()):
                wcs_offsets = None
            if '[G92' in offsets_text and 'G92' in records:
                g92 = scaled(records['G92'])
            if '[TLO' in offsets_text and 'TLO' in records and factor is not None:
                raw = records['TLO']
                tlo = (raw[2] if isinstance(raw, tuple) else raw) * factor

        machine_pos = None
        report = parse_grbl_status_report(self._grbl_query_status(op))
        if report is not None:
            if 'mpos' in report:
                machine_pos = scaled(report['mpos'])
            elif 'wpos' in report and 'wco' in report:
                machine_pos = scaled(tuple(w + c for w, c in zip(report['wpos'], report['wco'])))

        setup = setup_from_controller(profile, dialect, units, machine_pos, wcs_offsets, active_wcs, g92, tlo)
        self.live_setup_key = (setup.profile_hash,
                               None if wcs_offsets is None else tuple(sorted(setup.wcs_offsets.items())),
                               active_wcs, setup.g92, setup.tlo)
        return setup

    def live_setup_changed(self, old_key, op, profile, dialect=None):
        """
        Read the state of the controller again and say if it differs from `old_key` (the profile hash, the stored
        offsets, the active WCS, G92 and TLO of an earlier live_setup()). A live setup is valid only while this is False.
        """
        self.live_setup(op, profile, dialect, refresh=True)
        return self.live_setup_key != old_key

    def _report_scale(self):
        """
        :return: the factor that changes a position of a status report (the units of GRBL $13) into the
            units of the job, or None if $13 or the job units are not known.
        """
        inches = getattr(self, '_grbl_report_inches', None)
        if inches is None:
            return None
        try:
            units = getattr(self._target(), 'units', None)
        except Exception:
            units = None
        if str(units).upper() not in ('MM', 'IN'):
            units = getattr(self, 'units', None)
        if str(units).upper() not in ('MM', 'IN'):
            return None
        return grbl_report_scale(units, inches)

    def _simulation_available(self):
        return (self._grbl_op is None and self._probe_start_op is None and
                not self._grbl_close_pending and not self.__dict__.get('_program_paused', False) and
                self._controller_state not in ('Run', 'Hold', 'Door', 'Jog', 'Home') and
                not (self.grbl_ser_port is not None and not self._grbl_connected))

    def _update_sim_button(self):
        if self.ui is None:
            return
        self.ui.sim_button.setEnabled(self._simulation is None and self._simulation_available())

    def on_simulation(self):
        if self._simulation is not None or not self._simulation_available():
            return
        self._simulation = SimulationDialog(self)
        self._simulation.show()
        self._update_sim_button()

    def _start_simulation(self):
        dialog = self._simulation
        if dialog is None or dialog.running or not self._simulation_available():
            return
        autolevel = self.ui.autolevel_cb.get_value() is True
        if not (self.build_job() if autolevel else self.build_stream_job(apply_profile=False)):
            return
        lines = list(self.job_lines)
        if not lines:
            return
        probe_lines = []
        points = ()
        rx_size, max_line = self._line_limits()
        target = self._target()
        controller = VirtualGrbl()
        controller.scale = 25.4 if target.units.upper() == 'IN' else 1.0
        if autolevel:
            if not self.al_voronoi_geo_storage:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Add probe points before simulating auto-levelling."))
                return
            if self._al_data_units != target.units.upper():
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The units of the CNCJob object and of the height map do not match."))
                return
            inputs = self._al_read_probe_inputs()
            if inputs is None:
                return
            travel_z, depth, feed = inputs
            if travel_z * controller.scale <= 0.25:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "Simulation probe travel Z must be above the 0.25 mm simulated surface."))
                return
            preprocessors = [str(getattr(target, name, '') or '').lower()
                             for name in ('pp_geometry_name', 'pp_excellon_name')]
            if (self._tlo_line(lines) is not None or getattr(target, 'pp_solderpaste_name', None) is not None or
                    any(marker in name for name in preprocessors for marker in ('roland', 'hpgl', 'laser'))):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("This program can not be height-compensated."))
                return
            # ponytail: independent samples model warp; use a spatial surface if smooth warp is needed.
            points = tuple((v['point'].x, v['point'].y, random.uniform(0.0, 0.25) / controller.scale)
                           for v in self.al_voronoi_geo_storage.values())
            if any(not math.isfinite(value) for point in points for value in point):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The height map contains invalid values."))
                return
            if SenderGen.needs_segmentation(target):
                lines, _count = split_long_moves(
                    lines, float(self.app.options.get("tools_grbl_seg_len", 1.0)),
                    decimals=target.coords_decimals)
            # The leveller keeps interpolation caches on self. Keep simulated data private too.
            cache_names = ('_al_points', '_al_grid', '_al_decimals', '_al_offset_fn')
            # the lock keeps a Start worker out of the caches while they are swapped
            with self._al_lock:
                caches = {name: self.__dict__[name] for name in cache_names if name in self.__dict__}
                try:
                    result = self.autolevell_gcode(
                        SenderGen.job_proxy(target, lines), self.ui.al_method_radio.get_value(),
                        snapshot=(points, target.units.upper(), self._al_grid_tolerance()), strict=True)
                finally:
                    for name in cache_names:
                        self.__dict__.pop(name, None)
                    self.__dict__.update(caches)
            if result is None:
                return
            lines = [line for line in result['job'].splitlines() if line.strip()]
            if validate_stream_lines(lines, rx_size, max_line) is not None:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The levelled G-code has a line longer than GRBL accepts."))
                return
            probe_lines = ['G20 G90' if controller.scale == 25.4 else 'G21 G90', 'G0 Z%.6f' % travel_z]
            for x, y, height in points:
                controller.probe_heights.append(height * controller.scale)
                probe_lines.extend(['G0 X%.6f Y%.6f' % (x, y), 'G38.2 Z%.6f F%.6f' % (depth, feed),
                                    'G0 Z%.6f' % travel_z])
            probe_lines.append('G0 X0 Y0')
        if probe_lines:
            # Only the simulator knows the contact position. Keep real probe parsing conservative.
            display = list(probe_lines)
            display[0] = 'G90'
            display[1] = 'G0 X0 Y0 Z%.6f' % travel_z
            for i, (_x, _y, height) in enumerate(points):
                display[3 + 3 * i] = 'G1 Z%.6f' % height
            segments = parse_toolpath(display, start=(0, 0, 0))
            # Keep the cutting path visible without accepting it during the probing phase.
            segments.extend((len(probe_lines) + i, a, b, rapid)
                            for i, a, b, rapid in parse_toolpath(lines, start=(0, 0, 0)))
            chords = ()
        else:
            display, indexes, chords = self._display_lines()
            segments = parse_toolpath(display, start=(0, 0, 0))
            if indexes is not None:
                segments = [(indexes[i], a, b, rapid) for i, a, b, rapid in segments]
        dialog.preview = (segments, chords)
        dialog.height_map = None
        dialog.progress = 0
        controller.set_speed(dialog.slider.value())
        dialog.controller = controller
        dialog.units = self._target().units.upper()
        dialog.running = True
        dialog.start_button.setEnabled(False)
        dialog.stop_button.setEnabled(True)
        dialog.pause_button.setEnabled(True)
        dialog.pause_button.setText(_("Pause"))
        self._preview_generation += 1
        self._console().set_program(probe_lines or lines)
        self.ui.progress_bar.setValue(0)
        self.ui.start_button.setEnabled(False)
        self.ui.object_combo.setEnabled(False)
        if self.view3d is not None:
            if probe_lines:
                self.view3d.set_height_map(None)
            self.view3d.set_toolpath(*dialog.preview)
            if self.app.options.get("tools_gcode_fit_view", True):
                self.on_fit_view()

        pending = {}
        console_lines = []
        last_update = time.monotonic()

        def flush(force=False):
            nonlocal last_update
            now = time.monotonic()
            # Keep fast simulations from queuing a redraw and selection update for every line.
            if not force and now - last_update < 0.05:
                return
            if console_lines:
                self.simulation_event_sig.emit(dialog, 'console', '\n'.join(console_lines))
                console_lines.clear()
            for kind, value in pending.items():
                self.simulation_event_sig.emit(dialog, kind, value)
            pending.clear()
            last_update = now

        def event(kind, value):
            if kind == 'console':
                console_lines.append(value)
            else:
                pending[kind] = value
            flush()

        def worker_task():
            result = {'status': 'error', 'done': 0}
            try:
                if probe_lines:
                    result = stream_lines(
                        probe_lines, controller.write, controller.readline, rx_size=rx_size, max_line=max_line,
                        poll_status=True, poll_interval=0.05,
                        stall_timeout=float('inf'),
                        on_progress=lambda done, total: event('progress', (done, total)),
                        on_line=lambda line: event('console', line),
                        on_status=lambda line: event('status', parse_grbl_status_report(line)))
                    if result['status'] != 'finished' or controller.cancel.is_set():
                        if controller.cancel.is_set():
                            result['status'] = 'cancelled'
                        return
                    flush(force=True)
                    self.simulation_event_sig.emit(dialog, 'program', (lines, points))
                # The same character-counting streamer as the physical controller, with private I/O.
                result = stream_lines(
                    lines, controller.write, controller.readline, rx_size=rx_size, max_line=max_line,
                    poll_status=True, poll_interval=0.05,
                    stall_timeout=float('inf'),
                    on_progress=lambda done, total: event('progress', (done, total)),
                    on_line=lambda line: event('console', line),
                    on_status=lambda line: event('status', parse_grbl_status_report(line)))
            except Exception as err:
                self.app.log.error('G-code simulation -> %s' % str(err))
                result['last_text'] = str(err)
            finally:
                event('position', tuple(controller.position))
                flush(force=True)
                self.simulation_event_sig.emit(dialog, 'finished', result)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def _on_simulation_event(self, dialog, kind, value):
        if self._simulation is not dialog:
            return
        if kind == 'progress':
            dialog.progress = value[0]
            self._on_stream_progress(*value)
        elif kind == 'console':
            self._console().append(value)
        elif kind == 'program':
            lines, points = value
            dialog.preview = (parse_toolpath(lines, start=(0, 0, 0)), ())
            dialog.height_map = build_bilinear_grid(points, self._al_grid_tolerance())
            dialog.progress = 0
            self._console().set_program(lines)
            self.ui.progress_bar.setValue(0)
            if self.view3d is not None:
                self._update_3d_height_map()
                self.view3d.set_toolpath(*dialog.preview)
        elif kind == 'status' and value is not None:
            self._program_paused = dialog.controller.paused
            self._on_simulation_event(dialog, 'position', value['mpos'])
        elif kind == 'position':
            scale = 1 / 25.4 if dialog.units == 'IN' else 1.0
            pos = tuple(axis * scale for axis in value)
            state = _("Paused") if dialog.controller.paused else _("Running")
            self.ui.status_label.setText(_("Simulation") + ': ' + state +
                                         '   X %.3f  Y %.3f  Z %.3f' % pos)
            self.ui.status_strip.set_state('Hold' if dialog.controller.paused else 'Run')
            self.ui.status_strip.set_position(pos, dialog.units)
            if self.view3d is not None:
                self.view3d.set_tool_position(pos)
        elif kind == 'finished':
            dialog.running = False
            dialog.start_button.setEnabled(True)
            dialog.stop_button.setEnabled(False)
            dialog.pause_button.setEnabled(False)
            self._program_paused = False
            self.ui.start_button.setEnabled(self._grbl_port_ready())
            self.ui.object_combo.setEnabled(True)
            status = value['status']
            text = (_("Finished") if status == 'finished' else
                    _("Stopped") if status == 'cancelled' else
                    _("Error") + ': ' + value.get('last_text', status))
            self.ui.status_label.setText(_("Simulation") + ': ' + text)
            self.ui.status_strip.set_state('Idle')
            if dialog.closing:
                self._close_simulation()

    def _close_simulation(self):
        if self._simulation is None or self._simulation.running:
            return
        self._simulation.deleteLater()
        self._simulation = None
        self._update_3d_height_map()
        self._update_sim_button()

    def _on_status_report(self, report):
        if report is None:
            return
        if '_sender_port' in report and (
                report['_sender_port'] is not self.grbl_ser_port or
                report['_sender_generation'] != self._grbl_safety_generation or
                time.monotonic() - report.get('_sender_time', time.monotonic()) > 3.0):
            return
        self._controller_state = report['state'].split(':')[0]
        self._controller_state_time = time.monotonic()
        self.sender_io.on_status_report(report)
        if self._resume_refused_version is not None:
            # the recheck refused the Resume: the button works again after the next tool command or the end of the tool change
            state = self._tool_resume
            if state is None or state['version'] != self._resume_refused_version:
                self._resume_refused_version = None
                self.ui.resume_button.setDisabled(False)
        was_paused =self.__dict__.get('_program_paused') is True
        # a grblHAL manual M6 holds the job in the Tool state, like Hold and Door
        self._program_paused = report['state'].split(':')[0] in ('Hold', 'Door', 'Tool')
        if was_paused and not self._program_paused and report['state'] == 'Run':
            if self.view3d is not None:
                self.view3d.set_selected_line(None)
            console = self.app.ui.grbl_console_dock.widget()
            if isinstance(console, GrblConsole):
                console._follow_program(console.follow_cb.isChecked())
        if 'wco' in report:
            self._last_wco = report['wco']
        self._grbl_update_ownership(report)
        camera_scale = self._camera_report_scale()
        machine = self._report_machine_position(report, getattr(self, '_last_wco', None))
        if machine is not None and camera_scale is not None:
            camera_z = machine[2] * camera_scale
            if math.isfinite(camera_z):
                self._camera_last_z = camera_z
        pos = work_position(report, getattr(self, '_last_wco', None))
        text = report['state']
        strip_units = None
        if pos is not None:
            scale = self._report_scale()
            if scale is None:
                # a position in the wrong units is worse than none
                pos = None
                text += '   %s' % _("position unknown (GRBL $13 not read)")
            else:
                pos = tuple(value * scale for value in pos)
                try:
                    units = getattr(self._target(), 'units', None)
                except Exception:
                    units = None
                if str(units).upper() not in ('MM', 'IN'):
                    units = self.units
                strip_units = units
                text += '   %s (%s): X %.3f  Y %.3f  Z %.3f' % (
                    _("Work position"), units.lower(), *pos)
        if 'spindle_actual' in report:
            # the measured RPM that grblHAL adds to FS when the spindle has an encoder
            text += '   RPM %d' % round(report['spindle_actual'])
        text += self._ecut_queue_suffix()
        self.ui.status_label.setText(text)
        self.ui.status_strip.set_state(report['state'])
        self.ui.status_strip.set_position(pos, strip_units)
        self._last_work = pos
        self._show_active_wcs()
        if report['state'].split(':')[0] != 'Alarm':
            self.ui.status_strip.set_alarm(None)
        elif not self.ui.status_strip.alarm_label.text():
            self.ui.status_strip.set_alarm(
                code_text('ALARM:' + report['substate'], self._grbl_grblhal) if 'substate' in report else 'ALARM')
        self._apply_machine_state()
        if 'ov' in report:
            self._feed_override_pct = report['ov'][0]
            self.ui.ov_values_label.setText(_('F: %d%%  R: %d%%  S: %d%%') % report['ov'])
            # the accessory field comes with 'Ov'; without it, no accessory is on
            accessory = report.get('accessory', '')
            self._on_spindle_state('S' in accessory or 'C' in accessory)
        if self.view3d is not None and pos is not None:
            self.view3d.set_tool_position(pos)
        if self.view3d is not None:
            scale = self._report_scale()
            # Only this report's effective WCO is evidence for the overlay. Never reuse an
            # offset across a G92/tool/WCS change just because a previous report contained it.
            work = work_position(report)
            machine = self._report_machine_position(report)
            try:
                units = getattr(self._target(), 'units', None)
            except Exception:
                units = None
            if units not in ('MM', 'IN'):
                units = self.units
            if scale is None:
                work = machine = None
                units = None
            else:
                work = tuple(value * scale for value in work) if work is not None else None
                machine = tuple(value * scale for value in machine) if machine is not None else None
            # $22: bit 0 enables homing (grblHAL uses more bits of the same setting)
            not_homed = int((self._grbl_settings or {}).get(22) or 0) & 1 == 1 and not self._homed_this_session
            self.view3d.set_coordinates(work, machine, units, wcs=self._active_wcs, state=report['state'],
                                        not_homed=not_homed)
        if self.__dict__.get('camera_calibration') is not None and self.camera_calibration.has_calibration:
            self._camera_refresh_ui()

    def _reported_stationary(self):
        """
        Positive evidence that the machine stands still: a fresh Idle report of the controller.
        The local flags do not see motion that another source started (pendant, SD card), or a
        jog/command that the controller accepted and still executes.
        """
        return self._controller_state == 'Idle' and time.monotonic() - self._controller_state_time <= 3.0

    def _emit_sender_status(self, report, op):
        if report is None:
            return
        self.status_report_sig.emit(dict(report, _sender_port=op.port,
                                        _sender_generation=op.safety_generation, _sender_time=time.monotonic()))

    def _invalidate_coordinates(self):
        self._last_wco = None
        if self.ui is not None:
            self.ui.status_strip.set_position(None, None)
        if self.view3d is not None:
            try:
                self.view3d.set_coordinates(None, None, None)
            except RuntimeError:
                pass  # a destroyed canvas must never prevent the controller stop/reset

    def _lock_execution_view(self):
        if self.__dict__.get('_execution_tabs') is not None:
            return
        tabs = getattr(self.app.ui, 'plot_tab_area', None)
        if not isinstance(tabs, QtWidgets.QTabWidget):
            return
        self.on_show_3d()
        plot = getattr(self.app, 'area_3d_tab', None)
        if tabs.indexOf(plot) < 0:
            return
        self._execution_tabs = tabs
        self._execution_plot = plot
        self._execution_close_guard = getattr(plot, 'confirm_close', None)
        plot.confirm_close = lambda: False
        self._execution_tab_states = [(tabs.widget(i), tabs.isTabEnabled(i)) for i in range(tabs.count())]
        bar = tabs.tabBar
        self._execution_bar = bar() if callable(bar) else bar
        self._execution_buttons = []
        for i in range(tabs.count()):
            for side in (QtWidgets.QTabBar.ButtonPosition.LeftSide, QtWidgets.QTabBar.ButtonPosition.RightSide):
                button = self._execution_bar.tabButton(i, side)
                if button is not None:
                    self._execution_buttons.append((button, button.isEnabled()))
                    button.setEnabled(False)
        self._execution_bar.installEventFilter(self)
        tabs.currentChanged.connect(self._keep_execution_view)
        self._keep_execution_view()

    def _keep_execution_view(self, *_args):
        tabs = self.__dict__.get('_execution_tabs')
        if tabs is None:
            return
        plot = self._execution_plot
        for i in range(tabs.count()):
            widget = tabs.widget(i)
            if not any(saved is widget for saved, _enabled in self._execution_tab_states):
                self._execution_tab_states.append((widget, tabs.isTabEnabled(i)))
            tabs.setTabEnabled(i, widget is plot)
        if tabs.currentWidget() is not plot:
            tabs.setCurrentWidget(plot)

    def eventFilter(self, obj, event):
        if obj is self.__dict__.get('_execution_bar') and event.type() in (
                QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.MouseButtonRelease,
                QtCore.QEvent.Type.MouseButtonDblClick, QtCore.QEvent.Type.MouseMove):
            return True  # no close, detach or switch; the canvas and operator controls stay live
        return super().eventFilter(obj, event)

    def _unlock_execution_view(self):
        tabs = self.__dict__.get('_execution_tabs')
        if tabs is None:
            return
        self._execution_tabs = None
        try:
            if self._execution_close_guard is None:
                del self._execution_plot.confirm_close
            else:
                self._execution_plot.confirm_close = self._execution_close_guard
            tabs.currentChanged.disconnect(self._keep_execution_view)
            self._execution_bar.removeEventFilter(self)
            for button, enabled in self._execution_buttons:
                if not _qt_deleted(button):
                    button.setEnabled(enabled)
            for widget, enabled in self._execution_tab_states:
                index = tabs.indexOf(widget)
                if index >= 0:
                    tabs.setTabEnabled(index, enabled)
        except (RuntimeError, TypeError):
            pass  # the application may already have deleted the plot tabs
        self._execution_bar = None

    def show_probing_geo(self, state, reset=False):
        if self.view3d is not None and (reset or not state):
            self.view3d.probing_shapes.clear(update=True)
        super().show_probing_geo(state, reset=reset)

    def add_probing_shape(self, **kwargs):
        key = super().add_probing_shape(**kwargs)
        if self.view3d is not None:
            self.view3d.probing_shapes.add(tolerance=self.drawing_tolerance, layer=0, **kwargs)
        return key

    def plot_probing_geo(self, geometry, visibility, custom_color=None):
        super().plot_probing_geo(geometry, visibility, custom_color=custom_color)
        if self.view3d is not None:
            if visibility:
                self.view3d.probing_shapes.redraw()
            else:
                self.view3d.probing_shapes.clear(update=True)

    def _schedule_3d_preview(self):
        if self.__dict__.get('_camera_public_mode', False):
            return
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        generation = self._preview_generation

        def show_after_load():
            if generation != self._preview_generation:
                return
            self._cleanup_3d_if_inactive()
            if generation == self._preview_generation:
                self.on_show_3d()

        QtCore.QTimer.singleShot(0, show_after_load)

    def on_show_3d(self):
        open_3d_tab = getattr(self.app, 'on_3d_area', None)
        if callable(open_3d_tab):
            open_3d_tab()
        canvas = getattr(self.app, 'plotcanvas3d', None)
        if canvas is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The Plot 3D tab is not available."))
            return
        if self.view3d is None or self.view3d.canvas is not canvas:
            self.view3d = SenderView3d(canvas)
            if self.view3d.dro is not None:
                self.view3d.dro.dro_clicked.connect(self.on_dro_clicked)
            # the sender view starts in the isometric view; later the camera stays as the user turns it
            canvas.view.camera.set_view('iso')
        dialog = self.__dict__.get('_simulation')
        if dialog is not None and dialog.running:
            self.view3d.set_toolpath(*dialog.preview)
            self.view3d.set_accepted(dialog.progress - 1)
            self._update_3d_height_map()
            scale = 1 / 25.4 if dialog.units == 'IN' else 1.0
            self.view3d.set_tool_position(tuple(value * scale for value in dialog.controller.position))
            if self.app.options.get("tools_gcode_fit_view", True):
                self.on_fit_view()
            return
        self._update_3d_excellon()
        if self.ui.plot_probing_pts_cb.get_value() and self.al_voronoi_geo_storage:
            self.show_probing_geo(True, reset=True)
        else:
            self.view3d.probing_shapes.clear(update=True)
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        generation = self._preview_generation
        target = self._target()
        self._update_3d_height_map()
        if target is None:
            self.view3d.set_toolpath([])
            if self.app.options.get("tools_gcode_fit_view", True):
                canvas.fit_view()
            return
        # Freeze inputs before queuing work. Workers must never read widgets or mutate the stream job.
        source = getattr(target, 'source_file', '') or ''
        units, decimals = target.units, target.coords_decimals
        arc_mode = self.app.options.get("tools_grbl_arc_mode", 'split')
        arc_segment = float(self.app.options.get("tools_grbl_arc_segment", 0.5))
        seg_len = float(self.app.options.get("tools_grbl_seg_len", 1.0))
        segmented = not SenderGen.needs_segmentation(target)
        method = self.ui.al_method_radio.get_value()
        level = self.ui.autolevel_cb.get_value() is True and self.al_heights_valid
        snapshot = self._al_capture_apply_input(target, require_segmented=False) if level else None
        map_generation = self._al_generation
        home_wcs = float(str(self._al_map_frame[0])[1:]) if level and self._al_map_frame else None
        proxy = SenderGen.job_proxy(target, [])
        proxy.obj_options = dict(target.obj_options)
        view = self.view3d
        app = self.app

        def worker_task():
            result = {'generation': generation, 'target': target, 'source': source,
                      'map_generation': map_generation, 'view': view, 'levelled': snapshot is not None}
            try:
                numbered = SenderGen.clean_lines_numbered(source)
                job_lines = [line for _number, line in numbered]
                numbers = [number for number, _line in numbered]
                if snapshot is not None:
                    if self._tlo_line(job_lines) is not None:
                        raise ValueError(_("The program changes the tool length offset. It can not be levelled."))
                    if not segmented:
                        job_lines, _count = split_long_moves(job_lines, seg_len, decimals=decimals)
                    proxy.tools['job']['gcode'] = '\n'.join(job_lines) + '\n'
                    # Interpolation state is per instance; preview and Start must not share it.
                    leveler = LevellingCoreMixin()
                    leveler._al_lock = threading.RLock()
                    leveler.app = app
                    leveler._al_arc_segment = lambda obj: arc_segment
                    levelled = leveler.autolevell_gcode(proxy, method, snapshot=snapshot,
                                                       home_wcs=home_wcs, strict=True)
                    if levelled is None:
                        raise ValueError(_("Could not prepare the levelled preview."))
                    job_lines = [line for line in levelled['job'].splitlines() if line.strip()]
                    numbers = []
                elif arc_mode == 'split':
                    job_lines, _count, indexes, unsplit = SenderGen.split_arcs(
                        job_lines, units, arc_segment, decimals=decimals)
                    if unsplit:
                        raise ValueError(_("An arc can not be split for the preview."))
                    numbers = [numbers[i] for i in indexes]
                lines, indexes, chords = job_lines, None, ()
                if snapshot is None and arc_mode != 'split':
                    lines, _count, indexes, chords = SenderGen.split_arcs(
                        job_lines, units, arc_segment, decimals=decimals)
                segments = parse_toolpath(lines)
                if indexes is not None:
                    segments = [(indexes[i], a, b, rapid) for i, a, b, rapid in segments]
                result.update(prepared=SenderView3d.prepare_toolpath(segments, chords),
                              lines=job_lines, numbers=numbers)
            except Exception as err:
                app.log.error("ToolGcodeSender.on_show_3d() -> %s" % str(err))
                result['error'] = str(err)
            self.preview_ready_sig.emit(result)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def _on_preview_ready(self, result):
        if result.get('stream'):
            if (result['op'] is not self._grbl_op or result['generation'] != self._preview_generation or
                    result['view'] is not self.view3d or self._target() is not result['target']):
                return
            if 'error' in result:
                self.app.inform.emit('[ERROR_NOTCL] %s' % result['error'])
                return
            self.view3d.set_prepared_toolpath(result['prepared'])
            if self._progress_latest is not None:
                self.view3d.set_accepted(self._progress_latest[0] - 1)
            return
        if (self._grbl_op is not None or result['generation'] != self._preview_generation or
                result['view'] is not self.view3d or
                self.view3d is None or self.view3d.canvas is not getattr(self.app, 'plotcanvas3d', None) or
                result['map_generation'] != self._al_generation or self._target() is not result['target'] or
                (getattr(result['target'], 'source_file', '') or '') != result['source']):
            return
        if 'error' in result:
            self.view3d.set_toolpath([])
            self.app.inform.emit('[ERROR_NOTCL] %s' % result['error'])
            return
        self.view3d.set_prepared_toolpath(result['prepared'])
        if self._grbl_op is None and getattr(self, '_probe_start_op', None) is None:
            self.job_lines = result['lines']
            self.job_line_numbers = result['numbers']
            self.job_target_name = result['target'].obj_options['name']
            self.job_map_generation = result['map_generation'] if result['levelled'] else None
            self._console().set_program(self.job_lines)
        self._update_3d_height_map()
        if self.app.options.get("tools_gcode_fit_view", True):
            self.on_fit_view()

    def on_fit_view_toggled(self, checked):
        self.app.options["tools_gcode_fit_view"] = bool(checked)
        self.app.defaults["tools_gcode_fit_view"] = bool(checked)

    def _show_check_mode(self, checked=None):
        # the header shows that Start only validates
        checked = self.ui.check_mode_cb.isChecked()
        self.ui.check_badge.setVisible(checked)
        self.ui.start_button.setText(_("Start (check)") if checked else _("Start"))

    def on_fit_view(self):
        if self.view3d is None or not self.view3d._segments:
            return
        canvas = self.view3d.canvas
        canvas.view.camera.set_view('iso')
        canvas.fit_view(points=self.view3d.path.pos, margin=0.1)

    def _display_lines(self):
        """
        The lines for the 3D display. In the mode "send" for arcs, the stream holds the arcs: a copy is split
        for the display only, and each display line maps to its stream line.

        :return: (lines, stream_index, chord_lines). stream_index[i] is the stream line index of lines[i]
            (None: lines[i] is the stream line i). chord_lines: the stream line indexes of the arcs that
            could not be split: the display draws their chords in grey.
        """
        split_for_display = (self.ui.autolevel_cb.get_value() is not True and
                             self.app.options.get("tools_grbl_arc_mode", 'split') != 'split')
        if not split_for_display:
            return self.job_lines, None, ()
        target = self._target()
        return SenderGen.display_lines(
            self.job_lines, target.units, float(self.app.options.get("tools_grbl_arc_segment", 0.5)),
            target.coords_decimals, split_for_display)

    def _update_3d_height_map(self):
        if self.view3d is None:
            return
        if not self.al_voronoi_geo_storage:
            self.view3d.probing_shapes.clear(update=True)
        if not self.ui.plot_height_map_cb.get_value():
            self.view3d.set_height_map(None)
            return
        dialog = self.__dict__.get('_simulation')
        if dialog is not None:
            self.view3d.set_height_map(dialog.height_map)
            return
        if not self.al_heights_valid:
            self.view3d.set_height_map(None)
            return
        points = [(v['point'].x, v['point'].y, v['height']) for v in self.al_voronoi_geo_storage.values()]
        self.view3d.set_height_map(build_bilinear_grid(points, self._al_grid_tolerance()))

    def _on_stream_ended(self, how):
        """
        Slot of stream_ended_sig (GUI thread). Only 'finished' and 'check_passed' are a success.

        :param how: see _grbl_stream_run().
        """
        self._stream_ui_timer.stop()
        self._flush_stream_ui(drain=True)
        self._unlock_execution_view()
        self._grbl_tool_change_pending = False
        self.sender_io.refresh()
        if how not in ('finished', 'check_passed'):
            self._invalidate_coordinates()
        self._grbl_tool_commands = []
        self._grbl_tool_command_busy = False
        self._apply_machine_state()
        result = self._stream_result or {}
        console = self.app.ui.grbl_console_dock.widget()
        if isinstance(console, GrblConsole):
            console.update_progress(result.get('done', 0), result.get('error_indices', []))
        errors = result.get('errors', [])
        if errors and how in ('check_failed', 'check_exit_failed'):
            # the console queue drops old lines: the errors of a Check job come from the stream result
            summary = ['%s: %d' % (_("Check mode errors"), len(errors))]
            summary += ['%s %d: %s' % (_("Line"), number, code_text(reply, self._grbl_grblhal))
                        for number, reply in zip(errors, result.get('error_replies', []))]
            self._console().append('\n'.join(summary))
        recovery = (' ' + self._grbl_recovery_text()) if self._grbl_recovery is not None else ''
        last_text = code_text(result.get('last_text', ''), self._grbl_grblhal)
        reasons = {
            'alarm': _("The controller is in alarm."), 'error': _("GRBL stopped the job."),
            'timeout': _("The controller did not answer."), 'port_error': _("The serial port failed."),
            'unexpected_reset': _("The controller reset during the job.")}
        if how in reasons:
            # the feed hold stays: the banner stays until the recovery ends (_on_status_report)
            self.ui.status_strip.set_recovery(last_text or reasons[how])

        if how == 'finished':
            self.ui.progress_bar.setValue(100)
            self.app.inform.emit('[success] %s' % (_("Job done. Time elapsed: %s") % self._elapsed_text()))
        elif how == 'check_passed':
            self.ui.progress_bar.setValue(100)
            self.app.inform.emit('[success] %s' % _("Check mode: GRBL found no error."))
        elif how == 'check_passed_needs_home':
            self.ui.progress_bar.setValue(100)
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "Check mode: GRBL found no error. GRBL is in alarm after the reset. Press Home."))
        elif how == 'check_failed':
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (
                _("Check mode: GRBL found errors on the lines"), ', '.join(str(n) for n in errors[:20])))
        elif how == 'check_exit_failed':
            text = _("Could not leave check mode.") + recovery
            if errors:
                text += ' %s: %s' % (_("Errors on the lines"), ', '.join(str(n) for n in errors[:20]))
            self.app.inform.emit('[ERROR_NOTCL] %s' % text)
        elif how == 'rejected':
            # the worker told why; nothing was sent
            pass
        elif how == 'stale':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The Start request is old (the map, the object or the machine state changed). Press Start again."))
        elif how == 'stale_needs_home':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "The Start request is old. GRBL is in alarm after it left check mode. "
                "Press Home, then Start again."))
        elif how == 'stopped':
            self.app.inform.emit('[WARNING_NOTCL] %s%s' % (_("The job was stopped."), recovery))
        elif how in ('error', 'alarm'):
            if how == 'alarm':
                self.ui.status_strip.set_alarm(last_text or 'ALARM')
            self.app.inform.emit('[ERROR_NOTCL] %s %d: %s.%s' % (
                _("GRBL stopped the job at line"), result.get('line', 0), last_text, recovery))
        else:
            self.app.inform.emit('[ERROR_NOTCL] %s%s' % (_("The job did not finish."), recovery))

    def _sync_console_filter(self):
        """The plugin became active or inactive: the console of the dock (if one exists, none is made) turns its
        application event filter on or off."""
        try:
            console = self.app.ui.grbl_console_dock.widget()
        except (AttributeError, RuntimeError):
            return
        if isinstance(console, GrblConsole) and console.tool is self:
            console.sync_event_filter()

    def _console(self):
        """:return: the GrblConsole in the dock (made at the first call)."""
        dock = self.app.ui.grbl_console_dock
        console = dock.widget()
        if not isinstance(console, GrblConsole):
            console = GrblConsole(self)
            dock.setWidget(console)
        elif console.tool is not self:
            # a new project made a new plugin: the console of the dock must not keep the deleted one
            console.set_tool(self)
        # the Sender UI is rebuilt on reset; the dock keeps one set of jog controls
        jog_page = getattr(getattr(self, 'ui', None), 'jog_page', None)
        if isinstance(jog_page, QtWidgets.QWidget):
            console.set_jog_block(jog_page)
        header = getattr(getattr(self, 'ui', None), 'operate_header', None)
        if isinstance(header, QtWidgets.QWidget):
            console.set_header(header)
        camera_page = getattr(getattr(self, 'ui', None), 'camera_page', None)
        if isinstance(camera_page, QtWidgets.QWidget):
            console.set_page('camera', _("Camera"), camera_page)
        overrides = getattr(getattr(self, 'ui', None), 'overrides_page', None)
        if isinstance(overrides, QtWidgets.QWidget):
            console.set_page('overrides', _("Overrides"), overrides)
        if console.page('io') is None:
            console.set_page('io', _("IO"), self.sender_io.new_page())
            self.sender_io.evaluate()
        macros = console.page('macros')
        if macros is None or macros.tool is not self:
            macros = SenderMacros.MacroPage(self)
            console.set_page('macros', _("Buttons"), macros)
            self.macro_page = macros
        command_row = getattr(getattr(self, 'ui', None), 'command_row', None)
        if isinstance(command_row, QtWidgets.QWidget):
            console.set_command_row(command_row)
        if not self.__dict__.get('_console_dock_hooked', False):
            dock.visibilityChanged.connect(self._on_console_dock_visibility)
            console.page_changed.connect(self._on_console_page_changed)
            self._console_dock_hooked = True
        return console

    def _reload_console_program(self, target_obj):
        if self.__dict__.get('_camera_public_mode', False):
            return
        # A running stream keeps its own program and acknowledgement indexes.
        if self.__dict__.get('_simulation') is not None and self._simulation.running:
            return
        if self._grbl_op is not None and self._grbl_op.name == 'stream':
            return
        text = (getattr(target_obj, 'source_file', '') or '') if self._is_supported_target(target_obj) else ''
        self._console().set_program([line for _number, line in SenderGen.clean_lines_numbered(text)])

    def on_console_toggled(self, checked):
        self.app.options["tools_grbl_console_visible"] = bool(checked)
        if checked:
            self.show_console_if_wanted()
        else:
            self.hide_console()

    def show_console_if_wanted(self):
        if self.app.options.get("tools_grbl_console_visible", True):
            console = self._console()
            camera_mode = self.__dict__.get('_camera_public_mode', False)
            console.show_page('camera' if camera_mode else 'program')
            self.app.ui.grbl_console_dock.show()
            self.app.ui.grbl_console_dock.raise_()
            if camera_mode:
                console.expand_jog()
            else:
                console.program_view.setFocus()

    def hide_console(self):
        self._stop_continuous_jog()
        self.app.ui.grbl_console_dock.hide()

    def on_plugin_cleanup(self):
        """The plugin closes: stop and disconnect the controller, release the Plot 3D tab, hide the console."""
        self._unlock_execution_view()
        self._invalidate_coordinates()
        self._invalidate_configuration()
        button = getattr(getattr(self, 'ui', None), 'estop_button', None)
        if button is not None:
            button.hide()
        self._stop_continuous_jog()
        self._toolchange_new_session('close')
        if self.__dict__.get('_simulation') is not None:
            self._simulation.close()
        registration_ui = getattr(getattr(self, 'ui', None), 'registration_widget', None)
        if registration_ui is not None:
            try:
                registration_ui.shutdown()
            except (AttributeError, RuntimeError):
                pass
        self._camera_stop_preview()
        # a connected machine and its job go on: only an explicit "Stop and disconnect" (apply_machine_decision()),
        # the plugin uninstall or the application quit stop it, never a panel hide without an answer of the user
        if not self.machine_connected():
            self._grbl_shutdown()
        self._plugin_active = False
        # every close path ends here (the run() toggle, the notebook tab close, the panel hide); run() sets it again
        self._camera_public_mode = False
        self._sync_console_filter()
        self._preview_generation = getattr(self, '_preview_generation', 0) + 1
        self._probe_start_op = None
        self._clear_probe_data()
        self.hide_console()
        view = self.view3d
        self.view3d = None
        if view is None or view.canvas is not getattr(self.app, 'plotcanvas3d', None):
            return
        view.clear()
        tabs = self.app.ui.plot_tab_area
        index = tabs.indexOf(self.app.area_3d_tab)
        if index >= 0:
            tabs.closeTab(index)

    def _cleanup_3d_if_inactive(self):
        if self.__dict__.get('_kept_across_reset', False):
            return      # the panel is closed by a new project only: the connection and the job go on
        ui = self.app.ui
        if ui.plugin_scroll_area.widget() is not self or ui.notebook.indexOf(ui.plugin_tab) < 0:
            self.on_plugin_cleanup()

    def event(self, event):
        if event.type() in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.Close,
                            QtCore.QEvent.Type.ApplicationDeactivate, QtCore.QEvent.Type.WindowDeactivate):
            self._stop_continuous_jog()
        result = super().event(event)
        if event.type() in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.ParentChange) and \
                getattr(self, '_plugin_active', False):
            # Wait for takeWidget()/removeTab() to finish; hiding a notebook page isn't a close.
            QtCore.QTimer.singleShot(0, self._cleanup_3d_if_inactive)
        return result
