# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

"""The IO tab of the Sender dock: the state of the inputs and outputs of the connected controller."""

import re
import time

from PyQt6 import QtCore, sip

from appPlugins.Common.grbl.grbl_protocol import (
    grbl_capabilities, grbl_io_signals, decode_grbl_pins, decode_grbl_accessory)

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

# no status report for this long: the inputs and the accessory outputs are unknown
IO_STALE_S = 2.5
# a request or the activation sync that no report confirmed after this long is "not yet confirmed"
IO_SYNC_S = 12.0
# the e-Cut state is read, and the timers are checked, at this period while the IO tab is active
IO_TICK_MS = 200
# the next try to read the revision of the e-Cut settings
IO_REVISION_RETRY_S = 2.0

SPINDLE_CW, SPINDLE_CCW, FLOOD, MIST = 'Spindle CW', 'Spindle CCW', 'Flood', 'Mist'
ACCESSORY = (SPINDLE_CW, SPINDLE_CCW, FLOOD, MIST)
SPINDLES = (SPINDLE_CW, SPINDLE_CCW)
COOLANT_BYTE = {FLOOD: b'\xa0', MIST: b'\xa1'}
ECUT_DISCONNECTED = {'connected': False, 'fresh': False, 'configured': False, 'inputs': 0, 'outputs': 0,
                     'busy': True}


def _input_text(name):
    """:return: (label, full name) of a GRBL input signal."""
    table = {'X Limit': ('X', _('X Limit')), 'Y Limit': ('Y', _('Y Limit')), 'Z Limit': ('Z', _('Z Limit')),
             'Probe': ('P', _('Probe')), 'Door': ('D', _('Door')), 'Hold': ('H', _('Hold')),
             'Reset': ('R', _('Reset')), 'Cycle Start': ('CS', _('Cycle Start')), 'E-stop': ('ES', _('E-stop'))}
    return table.get(name, (name[:2], name))


def _output_text(name):
    table = {SPINDLE_CW: ('CW', _('Spindle CW')), SPINDLE_CCW: ('CCW', _('Spindle CCW')),
             FLOOD: ('F', _('Flood')), MIST: ('M', _('Mist'))}
    return table.get(name, (name, name))


def _deleted(widget):
    try:
        return widget is None or sip.isdeleted(widget)
    except TypeError:
        return False


class SenderIOController:
    """
    The state and the requests of the IO tab. Owned by the Sender (no widgets of its own except the page that
    the dock mounts). GUI thread, except the worker tasks that it starts.

    A state is green or red only from evidence: a fresh status report or a readback. The clock is injectable.
    """

    def __init__(self, tool, clock=time.monotonic):
        self.tool = tool
        self.clock = clock
        self.page = None
        # changes with every rebuild of the signals: a request or a result of an older one is dropped
        self.generation = 0
        self.port = None
        self.kind = None                # None (not discovered), 'grbl' or 'ecut'
        # True or False once $$ was read (the controller reports the pins or not); None = not read
        self.pin_report = None
        self.active = False
        self._timer = None
        self._reset_state()

    def _reset_state(self):
        self.input_names = []
        self.input_states = {}
        self.input_reason = None
        self.report_time = None
        self.acc_states = {name: None for name in ACCESSORY}
        self.unconfirmed = set()
        self.aux = {}
        self.pending = None
        self.sync_open = False
        self.sync_deadline = None
        self.ecut_inputs = []
        self.ecut_outputs = []
        self.ecut_state = dict(ECUT_DISCONNECTED)
        self.revision = None
        self.revision_loading = False
        self.revision_try = None
        self.inputs_spec = []
        self.outputs_spec = []

    # -------------------------------------------------------------------------------------------- page

    def new_page(self):
        """:return: a new IOPage for the dock. It shows the signals of the current discovery."""
        from appPlugins.ToolGcodeSender.SenderIOUI import IOPage
        page = IOPage()
        page.output_requested.connect(self.on_output_requested)
        self._drop_requests()
        self.page = page
        self._build_page()
        return page

    def _drop_requests(self):
        """A request of an older page, queued or running, fails the generation check: nothing is written."""
        self.generation += 1
        self._clear_pending()

    def detach(self):
        """The dock removed its pages (a new project): the page is gone."""
        self._drop_requests()
        self.page = None
        self.evaluate()

    def _live_page(self):
        if _deleted(self.page):
            self.page = None
        return self.page

    def _build_page(self):
        page = self._live_page()
        if page is None:
            return
        if self.kind is None:
            page.set_signals([], [])
            page.set_message(_("Not connected") if not self.tool._grbl_port_ready() else _("Reading the controller"))
        else:
            page.set_signals(self.inputs_spec, self.outputs_spec)
            page.set_message('')
        self.refresh()

    # ------------------------------------------------------------------------------------- discovery

    def discover(self, port):
        """
        The connect read finished (slot of io_discovery_sig). A port that is not the current one is dropped.
        Builds the signal list of the controller.
        """
        tool = self.tool
        if port is None or port is not tool.grbl_ser_port or not tool._grbl_port_ready():
            return
        self.generation += 1
        self.port = port
        self._reset_state()
        self.report_time = None
        if tool._ecut_settings_capable(port):
            self._discover_ecut(port)
        else:
            self._discover_grbl()
        self._build_page()
        self.evaluate()

    def _discover_grbl(self):
        tool = self.tool
        self.kind = 'grbl'
        caps = tool.__dict__.get('_grbl_caps') or grbl_capabilities(None)
        grblhal = tool.__dict__.get('_grbl_grblhal', False)
        self.input_names = grbl_io_signals(caps)
        self.inputs_spec = []
        for name in self.input_names:
            label, full = _input_text(name)
            tip = full
            if name == 'Probe' and caps.get('probe') is None:
                tip += '\n' + _("capability not reported")
            self.inputs_spec.append((name, label, tip))
        names = [SPINDLE_CW]
        if caps.get('spindle_dir') is not False:
            names.append(SPINDLE_CCW)
        names.append(FLOOD)
        if caps.get('mist') is not False:
            names.append(MIST)
        self.outputs_spec = []
        for name in names:
            label, full = _output_text(name)
            unknown = (name == MIST and caps.get('mist') is None) or (
                name == SPINDLE_CCW and caps.get('spindle_dir') is None)
            self.outputs_spec.append((name, label, full + ('\n' + _("capability not reported") if unknown else '')))
        if grblhal:
            for number in range(caps.get('aux_out') or 0):
                name = 'Aux %d' % number
                self.outputs_spec.append((name, 'A%d' % number, _("Digital output %d") % number))

    def on_ecut_config_saved(self, port, revision):
        """The E-Cut settings were saved: the signal maps and the revision of the new config replace the old pair."""
        if self.kind != 'ecut' or port is not self.port:
            return
        self._drop_requests()
        self._read_ecut_maps(port)
        self.revision = revision
        self._build_page()

    def _read_ecut_maps(self, port):
        self.ecut_inputs = port.effective_inputs()
        self.ecut_outputs = port.effective_outputs()
        self.inputs_spec = [(entry['name'], entry['name'][:3], entry['name']) for entry in self.ecut_inputs]
        self.outputs_spec = [(entry['names'][0], 'O%d' % entry['pin'], ' / '.join(entry['names']))
                             for entry in self.ecut_outputs]

    def _discover_ecut(self, port):
        self.kind = 'ecut'
        self._read_ecut_maps(port)
        self.request_revision()

    def on_disconnect(self):
        """The port closed: an empty page and no monitor."""
        self.generation += 1
        self.port = None
        self.kind = None
        self._reset_state()
        self._build_page()
        self.evaluate()

    # ------------------------------------------------------------------------- active / monitor / sync

    def _page_shown(self):
        """True if the IO tab is the current tab of a visible dock."""
        page = self._live_page()
        if page is None:
            return False
        try:
            dock = self.tool.app.ui.grbl_console_dock
            console = dock.widget()
            return bool(dock.isVisible() and console.is_page_current(page))
        except (AttributeError, RuntimeError):
            return False

    def evaluate(self):
        """io_active = the IO tab is current, the dock is visible and a controller is connected."""
        tool = self.tool
        active = bool(self._page_shown() and tool._grbl_port_ready() and self.kind is not None)
        if active == self.active:
            self.refresh()
            return
        self.active = active
        tool.set_io_monitor(active and self.kind == 'grbl')
        if active:
            self._activate()
        else:
            if self._timer is not None:
                self._timer.stop()
            self.sync_open = False
            self.sync_deadline = None
        self.refresh()

    def _activate(self):
        """All unknown, then the sync: a full report (grblHAL 0x87), or the next report with Ov (GRBL)."""
        tool = self.tool
        self.report_time = None
        self.input_states = {}
        self.acc_states = {name: None for name in ACCESSORY}
        self.unconfirmed.clear()
        self.sync_open = True
        self.sync_deadline = self.clock() + IO_SYNC_S
        if self._timer is None:
            self._timer = QtCore.QTimer()
            self._timer.setInterval(IO_TICK_MS)
            self._timer.timeout.connect(self.tick)
        self._timer.start()
        if self.kind == 'grbl':
            self.request_full_report()
        else:
            self.request_revision()

    def request_full_report(self):
        """grblHAL: the real-time 0x87 asks for a full report (accessories included)."""
        tool = self.tool
        if tool.__dict__.get('_grbl_grblhal', False):
            with tool._grbl_op_lock:
                tool._grbl_write_realtime_locked(b'\x87')

    # --------------------------------------------------------------------------------------- reports

    def on_status_report(self, report):
        """A status report of the current port (GUI thread)."""
        tool = self.tool
        if self.kind != 'grbl' or self.port is not tool.grbl_ser_port:
            self.refresh()
            return
        self.report_time = self.clock()
        self.input_states, self.input_reason = decode_grbl_pins(report, self.input_names, self.pin_report)
        grblhal = tool.__dict__.get('_grbl_grblhal', False)
        if 'accessory' in report or (not grblhal and 'ov' in report):
            self.acc_states = decode_grbl_accessory(report, grblhal, self.acc_states)
            self.unconfirmed.clear()
            self.sync_open = False
            self.sync_deadline = None
            pending = self.pending
            if pending is not None and pending['accepted'] and self.acc_states[pending['name']] == pending['wanted']:
                self._clear_pending()       # an opposite readback keeps it: the "not confirmed" timeout still fires
        if report['state'].split(':')[0] == 'Alarm' or tool.__dict__.get('_grbl_mpg_owned') \
                or tool.__dict__.get('_grbl_sd_owned'):
            self.clear_commanded()
        self.refresh()

    def clear_commanded(self):
        """The aux outputs are "commanded" only: a job, a macro, a reset, an Alarm or an owner makes them unknown."""
        if self.aux:
            self.aux.clear()
            self.refresh()

    def on_job_start(self):
        self.clear_commanded()
        self.refresh()

    # --------------------------------------------------------------------------------------- display

    def block_reason(self, for_write=False):
        """:return: why the outputs are disabled (str), or None if a request is allowed (Part F)."""
        tool = self.tool
        if not tool._grbl_port_ready():
            return _("Not connected")
        if self.kind is None or self.port is not tool.grbl_ser_port:
            return _("The controller discovery is not finished")
        op = tool._grbl_op
        if (op is not None and op.name == 'stream') or tool.__dict__.get('_program_paused') \
                or tool.__dict__.get('_simulation') is not None:
            return _("A job is running")
        if tool._controller_state.split(':')[0] == 'Alarm':
            return _("The controller is in Alarm")
        if tool._grbl_recovery is not None or tool._grbl_reset_pending:
            return _("The controller needs recovery")
        if tool.__dict__.get('_grbl_mpg_owned') or tool.__dict__.get('_grbl_sd_owned'):
            return _("An external source controls the controller")
        if self.kind == 'ecut':
            if self.ecut_state.get('busy'):
                return _("The E-Cut board is busy")
            if self.revision is None:
                return _("The E-Cut settings are not read yet")
        if not for_write and self.pending is not None:
            return _("Waiting for the previous request")
        return None

    def _display(self):
        """:return: {name: (state, reason, source)} of every signal."""
        states = {}
        now = self.clock()
        if self.kind == 'grbl':
            fresh = self.report_time is not None and now - self.report_time <= IO_STALE_S
            for name in self.input_names:
                if not fresh:
                    states[name] = (None, _("no fresh status"), '')
                else:
                    state = self.input_states.get(name)
                    states[name] = (state, '' if state is not None else (self.input_reason or ''),
                                    _("readback") if state is not None else '')
            for name, _label, _tip in self.outputs_spec:
                if name in ACCESSORY:
                    state = self.acc_states[name]
                    if not fresh:
                        states[name] = (None, _("no fresh status"), '')
                    elif name in self.unconfirmed or (state is None and not self.sync_open):
                        states[name] = (None, _("not yet confirmed"), '')
                    elif state is None:
                        states[name] = (None, _("waiting for the controller status"), '')
                    else:
                        states[name] = (state, '', _("readback"))
                else:
                    value = self.aux.get(name)
                    states[name] = (value, '' if value is not None else _("not read back"),
                                    _("commanded") if value is not None else '')
        elif self.kind == 'ecut':
            from appPlugins.Common.ecut.ecut_controller import decode_ecut_io
            decoded = decode_ecut_io(self.ecut_state, self.ecut_inputs, self.ecut_outputs)
            for name, (state, reason) in decoded.items():
                states[name] = (state, reason, _("readback") if state is not None else '')
        return states

    def refresh(self):
        """Show the states and the enable state of the outputs."""
        page = self._live_page()
        if page is None:
            return
        states = self._display()
        page.update(states)
        reason = self.block_reason()
        page.set_outputs_enabled(reason is None, reason or '')
        for name in ACCESSORY:
            if name in page.outputs:
                page.set_output_blocked(name, _("The state is not known yet") if states[name][0] is None else '')
        for name in page.outputs:
            page.set_pending(name, self.pending is not None and self.pending['name'] == name)

    # ---------------------------------------------------------------------------------------- timer

    def tick(self):
        """The 200 ms timer while the IO tab is active: freshness, deadlines and the e-Cut read."""
        now = self.clock()
        if self.kind == 'ecut' and self.port is self.tool.grbl_ser_port:
            try:
                self.ecut_state = self.port.get_io_state()
            except (OSError, ValueError, RuntimeError):
                self.ecut_state = dict(ECUT_DISCONNECTED)
            if self.revision is None:
                self.request_revision()
        if self.sync_open and self.sync_deadline is not None and now >= self.sync_deadline:
            self.sync_open = False
            self.sync_deadline = None
        pending = self.pending
        if pending is not None and pending['accepted'] and pending['deadline'] is not None \
                and now >= pending['deadline']:
            names = SPINDLES if pending['kind'] == 'spindle' else (pending['name'],)
            self.unconfirmed.update(names)
            self._clear_pending()
            self._message(_("Coolant change not confirmed") if pending['kind'] == 'coolant'
                          else _("Spindle change not confirmed"), warning=True)
        self.refresh()

    # --------------------------------------------------------------------------------------- requests

    def _message(self, text, warning=False):
        page = self._live_page()
        if page is not None:
            page.set_message(text)
        if warning and text:
            self.tool.app.inform.emit('[WARNING_NOTCL] %s' % text)

    def _clear_pending(self):
        self.pending = None

    def _new_pending(self, name, wanted, kind):
        self.pending = {'name': name, 'wanted': wanted, 'kind': kind, 'port': self.tool.grbl_ser_port,
                        'generation': self.generation, 'accepted': False, 'deadline': None}
        return self.pending

    def _current(self, pending):
        return (pending is self.pending and pending['port'] is self.tool.grbl_ser_port
                and pending['generation'] == self.generation)

    def on_output_requested(self, name, wanted):
        """A click on an output (GUI thread). 1 request at a time."""
        self._message('')
        reason = self.block_reason()
        if reason is not None:
            self._message(reason)
            return
        if self.kind == 'ecut':
            self._ecut_write(name, wanted)
        elif name in COOLANT_BYTE:
            if self.kind == 'grbl' and (self.report_time is None or self.clock() - self.report_time > IO_STALE_S):
                # the freshness is checked at the click: the toggle must not follow a cached state
                self._message(_("no fresh status"))
                self.refresh()
                return
            self._coolant_write(name, wanted)
        else:
            self._command_write(name, wanted)
        self.refresh()

    def _command_write(self, name, wanted):
        """Spindle (M3/M4/M5) or a grblHAL aux output (M64/M65): a transaction under an operation."""
        tool = self.tool
        if name in SPINDLES:
            if self.acc_states[name] is None:
                self._message(_("The state is not known yet"))
                return
            if wanted:
                command = '%s S%d' % ('M3' if name == SPINDLE_CW else 'M4', int(tool.ui.spindle_rpm_entry.get_value()))
            else:
                command = 'M5'
            pending = self._new_pending(name, wanted, 'spindle')
        else:
            command = '%s P%d' % ('M64' if wanted else 'M65', int(name.split()[-1]))
            pending = self._new_pending(name, wanted, 'aux')

        def guard():
            if not self._current(pending):
                return _("The request is old")
            return self.block_reason(for_write=True)

        def task(op):
            text, status = tool._grbl_transact(op, command, timeout=10.0, guard=guard)
            tool.io_result_sig.emit((pending, status, text))
            return False        # a command that does not move: no wait for Idle

        if not tool._grbl_start_op('IO output', task, motion=True):
            self._clear_pending()

    def _coolant_write(self, name, wanted):
        """Flood/Mist: the real-time toggle, only if the shown state is known and differs from the wanted one."""
        tool = self.tool
        state = self.acc_states[name]
        if state is None or state == wanted:
            self._message(_("The state is not known yet") if state is None else '')
            return
        pending = self._new_pending(name, wanted, 'coolant')
        sent = False
        with tool._grbl_op_lock:
            reason = self.block_reason(for_write=True)
            if reason is None and tool._controller_state.split(':')[0] not in ('Idle', 'Run', 'Hold'):
                reason = _("The controller is not ready")
            if reason is None:
                sent = tool._grbl_write_realtime_locked(COOLANT_BYTE[name])
                if sent and tool.__dict__.get('_grbl_grblhal', False):
                    tool._grbl_write_realtime_locked(b'\x87')
        if not sent:
            self._clear_pending()
            self._message(reason or _("The command was not sent"))
            return
        pending['accepted'] = True
        pending['deadline'] = self.clock() + IO_SYNC_S

    def on_result(self, result):
        """Slot of io_result_sig: the answer of a GRBL command."""
        pending, status, text = result
        if not self._current(pending):
            return
        if status == 'ok':
            if pending['kind'] == 'aux':
                self.aux[pending['name']] = pending['wanted']
                self._clear_pending()
            else:
                pending['accepted'] = True
                pending['deadline'] = self.clock() + IO_SYNC_S
                self.request_full_report()
        else:
            self._clear_pending()
            match = re.search(r'error:\s*(\d+)', text or '')
            if match and match.group(1) == '20':
                self._live_page().set_output_supported(pending['name'], False)
                self._message(_("The controller does not support this output"))
            else:
                self._message(text or _("The request failed"), warning=True)
        self.refresh()

    # ------------------------------------------------------------------------------------------ e-Cut

    def request_revision(self):
        """Read the revision of the E-Cut settings in a worker (the output write needs it)."""
        tool = self.tool
        port = self.port
        now = self.clock()
        if (self.kind != 'ecut' or self.revision_loading
                or (self.revision_try is not None and now - self.revision_try < IO_REVISION_RETRY_S)):
            return
        self.revision_loading = True
        self.revision_try = now

        def read():
            result = error = None
            try:
                result = port.get_motion_settings()['revision']
            except Exception as failure:
                error = str(failure)
            finally:
                tool.ecut_motion_result_sig.emit((port, None, 'io_revision', result, error))

        tool.app.worker_task.emit({'fcn': read, 'params': []})

    def on_revision(self, port, revision, error):
        if port is not self.port:
            return
        self.revision_loading = False
        if error is None:
            self.revision = revision
        self.refresh()

    def _ecut_write(self, name, wanted):
        tool = self.tool
        entry = next((e for e in self.ecut_outputs if e['names'][0] == name), None)
        if entry is None:
            return
        pending = self._new_pending(name, wanted, 'ecut')
        op = tool._grbl_reserve('E-Cut settings')
        if op is None:
            self._clear_pending()
            return
        pending['accepted'] = True
        tool._ecut_settings_task(op, None, 'output', (entry['pin'], wanted != entry['inverted'], self.revision))

    def on_ecut_output_result(self, port, result, error):
        """The result of the output write (dialog None, action 'output')."""
        if port is not self.port or self.pending is None or self.pending['kind'] != 'ecut':
            return
        self._clear_pending()
        if error is None:
            if isinstance(result, dict) and 'inputs' in result:
                self.ecut_state = result
        else:
            self._message(error, warning=True)
        self.refresh()
