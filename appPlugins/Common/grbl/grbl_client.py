# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/16/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The GRBL serial client of the GRBL Sender Plugin: 1 operation owns the port at
a time, every reply is classified, and an uncertain motion latches recovery.

The class that uses GrblClientMixin must have self.app and must call
self._grbl_init_state() in __init__. It must also have the hook
_grbl_set_ui_connected(connected) and the signal grbl_port_closed_sig.
"""

import math
import re
import time
import threading

import serial
import serial.tools.list_ports

import gettext
import appTranslation as fcTranslate
import builtins

from appPlugins.Common.grbl.grbl_protocol import (
    parse_grbl_probe_output,
    parse_grbl_active_wcs,
    parse_grbl_work_offset,
    grbl_reply_error,
    probe_timeout_s,
    grbl_report_scale,
    parse_grbl_setting,
    parse_grbl_status_state,
    parse_grbl_status_report,
    grbl_modes_confirmed,
    parse_grbl_identity,
    grbl_capabilities,
)
from appPlugins.Common.levelling_interp import match_nearest_point

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


# the size of the GRBL serial receive buffer (GRBL 1.1 on an ATmega328P)
GRBL_RX_BUFFER = 127
# LINE_BUFFER_SIZE of GRBL 1.1: the longest line GRBL accepts, with the line end
GRBL_MAX_LINE = 80

# the startup banner of the E-Cut USB adapter (appPlugins/Common/ecut); it is not a GRBL banner
ECUT_BANNER = "E-Cut host adapter 1.0 ['$' for help]"

# grblHAL NEWOPT RT-: the printable real-time commands are off, only the top-bit-set forms are read
GRBL_TOP_BIT_REALTIME = {b'?': b'\x80', b'~': b'\x81', b'!': b'\x82'}


def is_controller_banner(line):
    """True for a controller startup banner: a GRBL or grblHAL banner, or the E-Cut adapter banner."""
    text = (line or '').strip().lower()
    return text.startswith('grbl ') or text.startswith('grblhal ') or text.startswith('e-cut host adapter ')


def is_grblhal_banner(line):
    """True for the grblHAL startup banner. A grblHAL build at a higher compatibility level prints 'Grbl '."""
    return (line or '').strip().lower().startswith('grblhal ')


def is_native_jog_banner(line):
    """True for the banner of a controller with the $J jog command: GRBL 1.1 and grblHAL."""
    text = (line or '').strip()
    return is_grblhal_banner(text) or (text.startswith('Grbl 1.1') and len(text) > 8 and text[8].isalpha())


_GCODE_WORD_RE = re.compile(r'([A-Za-z])\s*([+-]?(?:\d+\.?\d*|\.\d+))')
_GCODE_COMMENT_RE = re.compile(r'\([^)]*\)')


# the console shows a controller reply on its own line: 'Answer:' tells it from the command
CONSOLE_ANSWER = 'Answer:             '

# the name of the idle status query operation (Sender._poll_idle_status); it is short, so a new
# operation waits for its end instead of a "busy" message
STATUS_POLL = 'status poll'


def spindle_state_of_line(line):
    """
    :param line: 1 G-code line that the controller accepted.
    :return: True if it starts the spindle (M3, M4), False if it stops it (M5, M2, M30),
        None if it does not change the spindle.
    """
    text = _GCODE_COMMENT_RE.sub('', (line or '').split(';', 1)[0])
    if text.lstrip().startswith('$'):
        return None
    state = None
    for letter, value in _GCODE_WORD_RE.findall(text):
        if letter.upper() != 'M':
            continue
        try:
            code = float(value)
        except ValueError:
            continue
        if code in (3, 4):
            state = True
        elif code in (5, 2, 30):
            state = False
    return state


def is_tool_change_line(line):
    """
    :param line: 1 line of G-Code.
    :return: True for M6, including a block that also contains motion.
    """
    text = _GCODE_COMMENT_RE.sub('', (line or '').split(';', 1)[0])
    if text.lstrip().startswith('$'):
        return False
    words = _GCODE_WORD_RE.findall(text)
    return any(letter.upper() == 'M' and float(value) == 6.0 for letter, value in words)


def validate_stream_lines(lines, rx_size=GRBL_RX_BUFFER):
    """
    :param lines:   list of str, no line ends.
    :param rx_size: the receive buffer size of GRBL.
    :return: the index of the first line that is longer than min(rx_size, GRBL_MAX_LINE)
        bytes (encoded, with the line end), or None if all lines fit.
    """
    limit = min(rx_size, GRBL_MAX_LINE)
    for index, line in enumerate(lines):
        if len((line + '\n').encode('utf-8')) > limit:
            return index
    return None


def dwell_seconds(line):
    """
    :param line: 1 line of G-Code.
    :return: the P value of a G4 line (GRBL reads P as seconds), else 0.0.
    """
    text = _GCODE_COMMENT_RE.sub('', line or '').split(';')[0]
    words = _GCODE_WORD_RE.findall(text)
    if not any(letter.upper() == 'G' and float(value) == 4.0 for letter, value in words):
        return 0.0
    for letter, value in words:
        if letter.upper() == 'P':
            seconds = float(value)
            return seconds if math.isfinite(seconds) and seconds > 0.0 else 0.0
    return 0.0


def stream_lines(lines, write, readline, rx_size=GRBL_RX_BUFFER, check_mode=False,
                 on_progress=None, on_line=None, poll_status=False, poll_interval=0.2,
                 on_status=None, clock=time.monotonic, stall_timeout=30.0,
                 status_request=b'?', write_control=None, tool_change_ack=False,
                 on_tool_change=None, tool_command=None, on_tool_command_done=None):
    """
    Send `lines` to GRBL with the character-counting protocol: a new line
    is written only while the bytes of the lines in flight (each line + '\\n')
    fit in the GRBL receive buffer. Each 'ok' or 'error:N' frees the oldest
    line. Runs in a worker thread; it reads no widgets, and it never touches
    a port: all I/O goes through `write` and `readline`.

    Without polling, the streamer does not see motion, so a long move can
    time out. The sender always passes poll_status=True. Also, the streamer
    sees a stop request only in `write`, so it sees a stop while it polls.

    :param lines:        list of str, no line ends, no empty lines.
    :param write:        callable(data: bytes) -> 'ok', 'cancelled', 'port_error' or 'stale'.
        The streamer calls it for each job line and for each status request. On any status
        but 'ok', the streamer returns that status at once and writes nothing more.
    :param readline:     callable() -> bytes, 1 line (or a part of it, or b'' on a read timeout).
        An exception (serial.SerialException, OSError) gives 'port_error'.
    :param rx_size:      the receive buffer size of GRBL.
    :param check_mode:   True in $C mode: an error does not stop the stream, it is counted.
    :param on_progress:  callable(done, total), after each acknowledged line.
    :param on_line:      callable(text), for each job line written and each reply line read
        (not for the status request and not for status reports).
    :param poll_status:  True to send a status request every poll_interval s.
    :param on_status:    callable(text), for each status report line.
    :param clock:        callable() -> seconds (monotonic).
    :param stall_timeout: seconds without progress while lines are in flight -> 'timeout'.
        Only these events are progress: an 'ok' or 'error:N'; a status report with the
        state Hold, Door, Jog, Home or Tool; a Run report whose position is not the same as in
        the last report. An Idle report or an unchanged Run report is not progress.
        A G4 line gets dwell_seconds(line) more seconds, 1 time for that line: with
        poll_status=True when a status report shows Idle while the G4 is the oldest line
        in flight, else when the G4 becomes the oldest line in flight.
    :param status_request: the byte written to request a status report ('?', or 0x80 on a grblHAL
        that reports RT-).
    :param write_control: callable(data: bytes) -> status, for the real-time bytes; by default `write`.
    :param tool_change_ack: True to acknowledge the grblHAL Tool state (a manual M6) with 0xA3.
        No job line is written while the state is Tool: the controller accepts no motion until
        the tool change is completed by a Cycle Start.
    :param on_tool_change: callable(), once after 0xA3 and the temporary stream's $G reply fence.
    :param tool_command: callable() -> a queued touch-off/jog command or None, read after the fence in Tool.
        Temporary commands have separate acknowledgements; finish them before issuing Cycle Start.
    :param on_tool_command_done: callable(text), only after a successful touch-off/jog acknowledgement.
        A temporary command/fence error returns 'error' with 'tool_command' and 'last_text', without
        unblocking the caller's busy state before it latches recovery.
    :return: dict: 'status' ('finished', 'error', 'alarm', 'timeout', 'port_error',
        'cancelled', 'stale', 'line_too_long' or 'unexpected_reset'), 'done' (acknowledged lines),
        'errors' (list of (line index, reply text)), 'last_text' (the last reply line),
        'in_flight' (lines written but not acknowledged) and, for 'line_too_long',
        'line_index'. 'unexpected_reset' means a Grbl banner was seen mid-stream (controller reset).
    """
    total = len(lines)
    in_flight = []          # (line index, byte count) of the lines written but not acknowledged
    used = 0
    sent = 0
    done = 0
    errors = []
    buf = b''
    last_text = ''
    last_progress = clock()
    last_position = None    # the position of the last status report
    dwell_index = None      # the line that got its dwell extension
    dwell_until = None
    next_poll = clock()
    tool_wait = False       # the controller waits in the Tool state (a manual M6)
    tool_index = None       # M6 barrier: acknowledgement plus completion status, never a timed bypass
    tool_acked = False
    tool_ready = False      # the $G marker and its ok fence off any early M6 reply
    tool_fence_marker = False
    tool_fence_deadline = None
    tool_status_requested = False
    poll_pending = False
    poll_sent_at = None     # a lost status reply must not stop polling forever
    manual_command = None
    manual_deadline = None
    control = write if write_control is None else write_control

    def result(status, **extra):
        res = {'status': status, 'done': done, 'errors': errors, 'last_text': last_text,
               'in_flight': len(in_flight)}
        res.update(extra)
        return res

    def arm_dwell():
        # the extension of a G4 line: 1 time for that line
        nonlocal dwell_index, dwell_until
        if in_flight and in_flight[0][0] != dwell_index:
            seconds = dwell_seconds(lines[in_flight[0][0]])
            if seconds > 0.0:
                dwell_index = in_flight[0][0]
                dwell_until = clock() + seconds + stall_timeout

    bad_index = validate_stream_lines(lines, rx_size)
    if bad_index is not None:
        return result('line_too_long', line_index=bad_index)

    while done < total or tool_index is not None or tool_wait or manual_command is not None:
        # write while the next line fits; the Tool state of a manual M6 takes no motion
        while not tool_wait and tool_index is None and manual_command is None and sent < total:
            changing_tool = tool_change_ack and not check_mode and is_tool_change_line(lines[sent])
            if changing_tool and (in_flight or poll_pending or buf):
                break  # retire every earlier acknowledgement before sending M6
            data = (lines[sent] + '\n').encode('utf-8')
            if used + len(data) > rx_size:
                break
            try:
                status = write(data)
            except (serial.SerialException, OSError):
                return result('port_error')
            if status != 'ok':
                return result(status)
            in_flight.append((sent, len(data)))
            used += len(data)
            if on_line is not None:
                on_line(lines[sent])
            sent += 1
            if changing_tool:
                # grblHAL flushes its input buffer when the tool change is acknowledged: a line written
                # after the M6 would be dropped, so none is written until the tool change is done
                tool_index = sent - 1
                tool_acked = tool_status_requested = False
                tool_ready = tool_fence_marker = False
                tool_fence_deadline = None
                break
        if not poll_status:
            arm_dwell()

        if (tool_wait and tool_ready and not buf and
                manual_command is None and tool_command is not None):
            manual_command = tool_command()
            if manual_command is not None:
                if validate_stream_lines([manual_command], rx_size) is not None:
                    return result('error', last_text='Tool change command is too long.')
                try:
                    status = write((manual_command + '\n').encode('utf-8'))
                except (serial.SerialException, OSError):
                    return result('port_error')
                if status != 'ok':
                    return result(status)
                manual_deadline = clock() + stall_timeout
                if on_line is not None:
                    on_line(manual_command)

        if (poll_status and poll_pending and poll_sent_at is not None and
                clock() - poll_sent_at > max(poll_interval * 5.0, 1.0)):
            # the status reply is lost: drop the request so polling and a queued M6 can resume
            poll_pending = False
            poll_sent_at = None
        # Don't solicit asynchronous parser-state reports while the $G fence is pending.
        if (poll_status and (not tool_change_ack or not poll_pending) and clock() >= next_poll and
                tool_fence_deadline is None):
            try:
                status = write(status_request)
            except (serial.SerialException, OSError):
                return result('port_error')
            if status != 'ok':
                return result(status)
            next_poll = clock() + poll_interval
            poll_pending = True
            poll_sent_at = clock()
            if tool_acked or tool_ready:
                tool_status_requested = True

        try:
            chunk = readline()
        except (serial.SerialException, OSError):
            return result('port_error')
        if chunk:
            buf += chunk
        while b'\n' in buf:
            raw, buf = buf.split(b'\n', 1)
            text = raw.decode('utf-8', errors='replace').strip()
            if not text:
                continue
            if text.startswith('<'):
                poll_pending = False
                poll_sent_at = None
                if on_status is not None:
                    on_status(text)
                report = parse_grbl_status_report(text)
                if report is None:
                    continue
                position = report.get('mpos', report.get('wpos'))
                state = report['state']
                if state == 'Alarm':
                    return result('alarm')
                if report.get('mpg') or ',' in report.get('sd', '') or report.get('sd') == 'Pending':
                    return result('error', last_text='External SD/MPG execution owns the controller.')
                if state in ('Hold', 'Door', 'Jog', 'Home', 'Tool'):
                    last_progress = clock()
                elif state == 'Run' and position is not None and position != last_position:
                    last_progress = clock()
                elif state == 'Idle' and poll_status:
                    arm_dwell()
                if position is not None:
                    last_position = position
                if state == 'Tool':
                    # a manual M6 of grblHAL: no job line until the tool change is done, after the acknowledge
                    if not tool_wait:
                        tool_wait = True
                        if tool_change_ack and not tool_ready and tool_fence_deadline is None:
                            try:
                                status = control(b'\xa3')
                            except (serial.SerialException, OSError):
                                return result('port_error')
                            if status != 'ok':
                                return result(status)
                            # $G is permitted in Tool. Its synchronous [GC:...] then ok establishes
                            # reply ownership without assuming that an empty serial read drained M6.
                            try:
                                status = write(b'$G\n')
                            except (serial.SerialException, OSError):
                                return result('port_error', tool_command='$G')
                            if status != 'ok':
                                return result(status, tool_command='$G')
                            tool_fence_deadline = clock() + stall_timeout
                            if on_line is not None:
                                on_line('$G')
                        elif not tool_change_ack and on_tool_change is not None:
                            on_tool_change()
                elif (state == 'Idle' and not report.get('tool_pending') and
                      (tool_status_requested or not tool_change_ack)):
                    # a backend without a manual acknowledgement (ATC / E-Cut) clears on Idle alone
                    if tool_fence_deadline is not None:
                        return result('error', tool_command='$G',
                                      last_text='Tool change completed before the reply fence.')
                    tool_wait = False
                    if tool_acked:
                        tool_index = None
                        tool_ready = False
                continue
            last_text = text
            if on_line is not None:
                on_line(text)
            lowered = text.lower()
            if lowered.startswith('alarm:'):
                return result('alarm')
            if is_controller_banner(text):
                # An unexpected controller reset mid-stream: the banner discards all pending
                # replies and leaves Check mode. Stop writing job lines; the caller must recover.
                return result('unexpected_reset')
            if text.startswith('[GC:') and tool_fence_deadline is not None:
                if tool_fence_marker or re.fullmatch(r'\[GC:[^\[\]]+\]', text) is None:
                    return result('error', tool_command='$G', last_text='Ambiguous tool change reply fence.')
                tool_fence_marker = True
                continue
            if lowered == 'ok' or lowered.startswith('error:'):
                if tool_fence_deadline is not None:
                    if lowered.startswith('error:'):
                        return result('error', tool_command='$G')
                    if tool_fence_marker:
                        tool_fence_deadline = None
                        tool_ready = True
                        last_progress = clock()
                        if on_tool_change is not None:
                            on_tool_change()
                        continue
                    if tool_acked or not in_flight or in_flight[0][0] != tool_index:
                        return result('error', tool_command='$G', last_text='Missing tool change reply fence marker.')
                    # Before [GC:...] only the original program M6 may retire.
                if manual_command is not None:
                    if lowered.startswith('error:'):
                        return result('error', tool_command=manual_command)
                    manual_command = None
                    manual_deadline = None
                    last_progress = clock()
                    if on_tool_command_done is not None:
                        on_tool_command_done(text)
                    continue
                if not in_flight:
                    continue
                index, size = in_flight.pop(0)
                used -= size
                done += 1
                if index == tool_index:
                    tool_acked = True
                    if not poll_status:
                        return result('error', last_text='M6 requires status polling.')
                last_progress = clock()
                if dwell_index == index:
                    dwell_index = None
                    dwell_until = None
                if lowered.startswith('error:'):
                    errors.append((index, text))
                    if not check_mode:
                        return result('error')
                if on_progress is not None:
                    on_progress(done, total)

        if tool_fence_deadline is not None and clock() > tool_fence_deadline:
            return result('timeout', tool_command='$G', last_text='Tool change reply fence did not acknowledge.')
        if in_flight or tool_index is not None or tool_wait or sent < total:
            deadline = last_progress + stall_timeout
            if dwell_until is not None and dwell_until > deadline:
                deadline = dwell_until
            if clock() > deadline:
                return result('timeout')
        if manual_deadline is not None and clock() > manual_deadline:
            return result('timeout', last_text='Tool change command did not acknowledge.')
    return result('finished')


class _GrblProbeAbort(Exception):
    """
    Stops a GRBL probing run.

    status: 'error', 'timeout', 'cancelled', 'port_error', 'not_idle',
    'unknown_units', 'modes_not_confirmed', 'probe_failed' or 'probe_invalid'.
    """

    def __init__(self, command, text, status):
        super().__init__(status)
        self.command = command
        self.text = text
        self.status = status


class _GrblOperation:
    """
    1 operation that sends commands to GRBL and reads the replies. While it
    exists, it owns the serial port: no other operation can start.

    :param name:   short name for messages, for example 'probe' or 'jog'.
    :param motion: True if the operation can move the machine; it then needs
                   a machine that is not in recovery.
    """

    def __init__(self, name, motion=False):
        self.name = name
        self.motion = motion
        self.cancel = threading.Event()
        self.failed = False
        self.safety_generation = 0
        self.port = None


class GrblClientMixin:
    # Counts the events that make a pending levelled Start request old (A8.3). It changes only
    # under _grbl_op_lock. The class value is the default for the first increment.
    _start_seq = 0
    # $13 of GRBL: True (the status reports are in inches), False (mm) or None (not known)
    _grbl_report_inches = None

    def _grbl_init_state(self):
        self._start_seq = 0
        self._grbl_report_inches = None
        self.grbl_probe_result = ''
        self.grbl_work_offset = (0.0, 0.0, 0.0)

        # serial session: 1 operation that reads GRBL replies at a time
        self._grbl_op_lock = threading.Lock()
        self._grbl_op = None
        # None, 'needs_reset' or 'needs_home'; motion is blocked until it is None again
        self._grbl_recovery = None
        # True if a disconnect waits for the running operation to stop
        self._grbl_close_pending = False
        self._grbl_connected = False
        self._grbl_connect_attempt = None
        self._grbl_safety_generation = 0
        self._grbl_rx_port = None
        self._grbl_rx_buffer = b''
        self._grbl_reply_pending = False
        # True after the user was told that an earlier command never got its reply
        self._grbl_stale_notified = False
        self._grbl_reset_pending = False
        # True from the first write of a stream until a confirmed completion or the reset banner:
        # GRBL can still hold lines (and their ok replies) of the stopped job
        self._grbl_stream_dirty = False

        # grblHAL: its own banner was seen, and the answer of $I+ and what it allows
        self._grbl_grblhal = False
        self._grbl_identity = None
        self._grbl_caps = None
        # grblHAL NEWOPT RT-: write the top-bit-set real-time commands
        self._grbl_top_bit_realtime = False
        # text that blocks motion (an unsupported grblHAL configuration), or None
        self._grbl_motion_blocked = None
        # True while the controller waits in the Tool state for a manual M6
        self._grbl_tool_change_pending = False
        self._grbl_identity_pending = False
        self._grbl_tool_commands = []
        self._grbl_tool_command_busy = False
        # True while an MPG pendant has taken the input stream (grblHAL |MPG:1)
        self._grbl_mpg_owned = False
        self._grbl_sd_owned = False

        self.grbl_ser_port = None

    @staticmethod
    def on_grbl_list_serial_ports():
        """
        List the serial ports of the system. The ports are not opened, so this
        is fast and does not reset a board through DTR.

        :returns: a list of port names
        """
        return [port_info.device for port_info in serial.tools.list_ports.comports()]

    def _grbl_close_port(self, port=None, attempt=None):
        """Close the serial port now. Safe in a worker thread (the UI changes through a signal)."""
        with self._grbl_op_lock:
            if attempt is not None and (self._grbl_connect_attempt is not attempt or (
                    port is not None and self.grbl_ser_port is not port)):
                return False
            # a pending levelled Start request is old (we hold the lock: no helper call)
            self._start_seq += 1
            port = self.grbl_ser_port
            self.grbl_ser_port = None
            self._grbl_connected = False
            # the WCO and the report units belong to the session that ended
            self._last_wco = None
            self._grbl_report_inches = None
            self._grbl_connect_attempt = None
            self._grbl_close_pending = False
            if port is not None:
                self._grbl_rx_port = None
                self._grbl_rx_buffer = b''
                self._grbl_reply_pending = False
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError) as err:
                self.app.log.debug("ToolLevelling._grbl_close_port() -> %s" % str(err))
        try:
            self.grbl_port_closed_sig.emit()
        except RuntimeError:
            pass  # a new project deleted the plugin: the port is closed and no UI is left to update
        return True

    def _grbl_disconnect(self):
        """
        Close the port. During an operation: stop the operation (feed hold if it
        can move the machine) and close the port when the operation releases it.
        """
        with self._grbl_op_lock:
            self._start_seq += 1
            self._grbl_safety_generation += 1
            self._grbl_reset_pending = False
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
                if op.motion:
                    self._grbl_write_realtime_locked(b'!')
                    self._grbl_recovery = 'needs_reset'
                self._grbl_close_pending = True

        if op is not None:
            if op.name != STATUS_POLL:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The port closes when the running operation stops."))
            return

        self._grbl_close_port()
        self.app.inform.emit("%s" % _("Port disconnected."))

    def _grbl_force_close(self):
        """Close the port now, also during an operation (the application ends)."""
        with self._grbl_op_lock:
            self._start_seq += 1
            self._grbl_safety_generation += 1
            self._grbl_reset_pending = False
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
                if op.motion:
                    self._grbl_write_realtime_locked(b'!')
                    self._grbl_recovery = 'needs_reset'
        self._grbl_close_port()

    def _grbl_shutdown(self):
        """
        The plugin closes: feed hold and soft reset (motion, spindle and outputs stop), then close the
        port. The next session starts clean; only a motion that the reset cut leaves 'needs_home'.
        """
        dialog = self.__dict__.get('_ecut_motion_dialog')
        if dialog is not None:
            self._ecut_motion_dialog = None
            try:
                dialog.close()
            except RuntimeError:
                pass  # the dialog was already deleted
        with self._grbl_op_lock:
            op = self._grbl_op
            moving = (op is not None and op.motion) or self._grbl_stream_dirty
            if self._grbl_port_ready():
                self._grbl_write_realtime_locked(b'!')
                self._grbl_write_realtime_locked(b'\x18')
        self._grbl_force_close()
        with self._grbl_op_lock:
            self._grbl_recovery = 'needs_home' if moving else None
            self._grbl_stream_dirty = False
            self._grbl_reset_pending = False
            self._grbl_reply_pending = False

    def _grbl_port_ready(self):
        """:return: True only after an open port has completed the GRBL handshake."""
        port = getattr(self, 'grbl_ser_port', None)
        return self._grbl_connected and port is not None and bool(getattr(port, 'is_open', False))

    def _grbl_recovery_text(self):
        if self._grbl_recovery == 'needs_reset':
            return _("GRBL needs recovery. Press Reset, then Home.")
        if callable(getattr(getattr(self, 'grbl_ser_port', None), 'reference_axes', None)):
            return _("The controller needs recovery. Press Home, or set the machine home of X, Y and Z "
                     "in the E-Cut settings.")
        return _("GRBL needs recovery. Press Home.")

    def _grbl_reserve(self, name, motion=False, allow_in_recovery=False):
        """
        Reserve the serial port for 1 operation. Call it in the GUI thread,
        before the worker task is scheduled.

        :param name:              short name of the operation.
        :param motion:            True if the operation can move the machine.
        :param allow_in_recovery: True only for the home cycle, which ends the recovery.
        :return: a _GrblOperation, or None (the user was told why).
        """
        if self.__dict__.get('_simulation') is not None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Close the simulation first."))
            return None
        if not self._grbl_port_ready() or self._grbl_close_pending:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a GRBL controller."))
            return None

        # the idle status query takes milliseconds (its answer time limit is 0.5 s)
        with self._grbl_op_lock:
            busy = self._grbl_op
        if name != STATUS_POLL and busy is not None and busy.name == STATUS_POLL:
            deadline = time.monotonic() + 0.7
            while self._grbl_op is busy and time.monotonic() < deadline:
                time.sleep(0.005)

        with self._grbl_op_lock:
            if self._grbl_op is not None:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s. %s' % (
                    _("GRBL is busy"), self._grbl_op.name, _("Wait, or press Stop.")))
                return None
            if self._grbl_stream_dirty and not (name in ('home', 'reset') and allow_in_recovery):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "GRBL can still have lines of the stopped job. Press Reset, then Home."))
                return None
            if motion and self._grbl_recovery is not None and not (
                    allow_in_recovery and (self._grbl_recovery == 'needs_home' or (
                        name == 'home' and self._grbl_recovery == 'needs_reset' and self._grbl_reset_pending))):
                self.app.inform.emit('[ERROR_NOTCL] %s' % self._grbl_recovery_text())
                return None
            # an unsupported controller configuration (for example more than the X, Y and Z axes) takes no motion
            blocked = self.__dict__.get('_grbl_motion_blocked')
            if motion and self.__dict__.get('_grbl_identity_pending'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "Controller discovery is incomplete. Wait, or reconnect after the external job finishes."))
                return None
            if motion and blocked:
                self.app.inform.emit('[ERROR_NOTCL] %s' % blocked)
                return None
            # a pendant that has taken the input stream would run our motion under its own control
            if motion and self.__dict__.get('_grbl_mpg_owned'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The MPG pendant has control of the input stream. Release it in the pendant."))
                return None
            if motion and self.__dict__.get('_grbl_sd_owned'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "An SD job owns the controller. Finish or stop it at its source before sending motion."))
                return None
            if name == 'probe' and (self.__dict__.get('_grbl_caps') or {}).get('probe') is False:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The controller has no probe input. Probing is unavailable."))
                return None
            op = _GrblOperation(name, motion)
            op.safety_generation = self._grbl_safety_generation
            op.port = self.grbl_ser_port
            self._grbl_op = op
            return op

    def _grbl_release(self, op):
        """Free the serial port after the operation `op` ended; close it if a disconnect waits."""
        close_now = False
        with self._grbl_op_lock:
            if self._grbl_op is op:
                self._grbl_op = None
                close_now = self._grbl_close_pending
        if close_now:
            self._grbl_close_port()

    def _grbl_start_op(self, name, fn, motion=False, allow_in_recovery=False, own_thread=False):
        """
        Reserve the port now (GUI thread) and run fn(op) in a worker thread.
        The port is released when fn ends, also after an exception.

        :param own_thread: True: a new thread, not the app worker pool. A short operation then never
            waits in the queue behind a long job while it holds the port.
        :return: True if the worker task was scheduled.
        """
        op = self._grbl_reserve(name, motion=motion, allow_in_recovery=allow_in_recovery)
        if op is None:
            return False

        def worker_task():
            try:
                result = fn(op)
                if motion and not allow_in_recovery and result is not False:
                    self._grbl_wait_idle(op)
            except (serial.SerialException, OSError) as err:
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                self.app.log.error("ToolLevelling._grbl_start_op(%s) -> %s" % (name, str(err)))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The serial port failed"), str(err)))
            finally:
                self._grbl_release(op)

        if own_thread:
            threading.Thread(target=worker_task, name='GrblSender-' + name, daemon=True).start()
        else:
            self.app.worker_task.emit({'fcn': worker_task, 'params': []})
        return True

    def _grbl_active_locked(self, op, port=None):
        """The caller holds the operation lock; a cancelled reply is never success."""
        if self._grbl_op is op and not op.cancel.is_set() and (
                not self._grbl_port_ready() or self.grbl_ser_port is not op.port):
            self._grbl_failure_locked(op, 'port_error')
        return (self._grbl_op is op and not op.cancel.is_set() and not op.failed
                and self._grbl_port_ready() and (port is None or self.grbl_ser_port is port))

    def _grbl_failure_locked(self, op, status, text=''):
        """Latch alarm/uncertain motion before attempting hold. Caller holds the lock."""
        alarm = any(line.lower().startswith('alarm:') or parse_grbl_status_state(line) == 'Alarm'
                    for line in (text or '').splitlines())
        unexpected_reset = status != 'handshake' and any(
            is_controller_banner(line) for line in (text or '').splitlines())
        uncertain = status in ('timeout', 'port_error', 'probe_invalid')
        if op is not None and (self._grbl_op is not op or op.cancel.is_set() or op.failed):
            return
        if alarm or unexpected_reset or status == 'probe_failed' or (uncertain and (op is None or op.motion)):
            self._grbl_recovery = 'needs_reset'
            self._grbl_reset_pending = False
            self._grbl_safety_generation += 1
            if op is not None:
                op.failed = True
            if (uncertain or unexpected_reset) and not alarm:
                if op is None or self.grbl_ser_port is op.port:
                    self._grbl_write_realtime_locked(b'!')
                else:
                    # Never send the old operation's hold to a replacement controller.
                    try:
                        op.port.write(self._grbl_realtime_byte(b'!'))
                    except (serial.SerialException, OSError) as err:
                        self.app.log.error("ToolLevelling._grbl_failure_locked() -> %s" % str(err))

    def _grbl_read_more_locked(self, op, port):
        """
        Buffer 1 more readline() of `port`; the operation lock is free during the read.
        The caller holds the lock and must treat everything it checked before as stale.

        :return: None if the data was buffered, else 'port_error' or 'cancelled'.
        """
        error = None
        chunk = b''
        self._grbl_op_lock.release()
        try:
            try:
                chunk = port.readline()
            except (serial.SerialException, OSError) as err:
                error = err
            if not chunk:
                time.sleep(0.01)
        finally:
            self._grbl_op_lock.acquire()
        if error is not None:
            self.app.log.error("ToolLevelling._grbl_read_more_locked() -> %s" % str(error))
            self._grbl_failure_locked(op, 'port_error')
            return 'port_error'
        if not self._grbl_active_locked(op, port):
            return 'cancelled'
        self._grbl_rx_buffer += chunk or b''
        return None

    def _grbl_prepare_locked(self, op, port):
        """
        Classify buffered async data before writing; old ok is not a new acknowledgement.

        A partial line is read to its end (up to 0.5 s) before it is classified. A reply that is
        still pending from an earlier command gets 1 s to arrive; it is consumed, never reused.
        """
        if self._grbl_rx_port is not port:
            self._grbl_rx_port = port
            self._grbl_rx_buffer = b''
            self._grbl_reply_pending = False
        try:
            waiting = port.in_waiting
            if waiting:
                self._grbl_rx_buffer += port.read(waiting)
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolLevelling._grbl_prepare_locked() -> %s" % str(err))
            self._grbl_failure_locked(op, 'port_error')
            return '', 'port_error'
        partial_until = pending_until = None
        while True:
            lines = self._grbl_rx_buffer.split(b'\n')
            self._grbl_rx_buffer = lines.pop()
            text = '\n'.join(line.decode('utf-8', errors='replace').strip() for line in lines)
            self._grbl_failure_locked(op, 'error', text)
            if op.failed:
                return text, 'error'
            if any(line.strip().lower() == 'ok' or line.strip().lower().startswith('error:')
                   for line in text.splitlines()):
                self._grbl_reply_pending = False
            if op.motion and any(line.strip().startswith('[PRB:') for line in text.splitlines()):
                self._grbl_failure_locked(op, 'probe_invalid')
                return text, 'error'
            if self._grbl_rx_buffer:
                # A line is still arriving: wait for its end before it is classified.
                if partial_until is None:
                    partial_until = time.monotonic() + 0.5
                elif time.monotonic() >= partial_until:
                    self._grbl_failure_locked(op, 'probe_invalid')
                    return text, 'error'
            else:
                partial_until = None
                if not self._grbl_reply_pending:
                    return '', None
                if getattr(self, '_grbl_stale_notified', False):
                    self._grbl_failure_locked(op, 'timeout')
                    return text, 'timeout'
                if pending_until is None:
                    pending_until = time.monotonic() + 1.0
                elif time.monotonic() >= pending_until:
                    self._grbl_stale_notified = True
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "GRBL did not answer an earlier command. Press Reset, then Home."))
                    self._grbl_failure_locked(op, 'timeout')
                    return text, 'timeout'
            status = self._grbl_read_more_locked(op, port)
            if status is not None:
                return text, status

    def _grbl_wait_idle(self, op):
        """
        Keep motion ownership while the controller reports a busy state; acceptance is not completion.
        There is no overall deadline: a long move, a feed hold or an open door ends with Stop, Idle
        or a failure. An unanswered status query already latches a timeout.
        """
        while True:
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op):
                    return False
            state = parse_grbl_status_state(self._grbl_query_status(op))
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op):
                    return False
                if state == 'Idle':
                    return True
                if state not in ('Run', 'Jog', 'Hold', 'Door', 'Home'):
                    self._grbl_failure_locked(op, 'probe_invalid')
                    return False
            time.sleep(0.05)

    def _grbl_realtime_byte(self, data):
        """
        The real-time command byte to write: the printable form, or its top-bit-set form on a grblHAL
        that reported RT- (its printable real-time commands are off).

        :param data: b'?', b'!' or b'~' (another byte is returned unchanged).
        :return: bytes
        """
        if self.__dict__.get('_grbl_top_bit_realtime', False):
            return GRBL_TOP_BIT_REALTIME.get(data, data)
        return data

    def _grbl_write_realtime_locked(self, data):
        """
        Write a GRBL real-time command (1 byte, no newline, no reply read).
        The caller must hold self._grbl_op_lock.

        :return: True if the byte was written.
        """
        if self.__dict__.get('_simulation') is not None or not self._grbl_port_ready():
            return False
        try:
            self.grbl_ser_port.write(self._grbl_realtime_byte(data))
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolLevelling._grbl_write_realtime_locked() -> %s" % str(err))
            return False
        return True

    def _grbl_transact(self, op, command, timeout=10.0, echo=True, guard=None):
        """
        Send 1 line to GRBL for the operation `op`, and read the reply until
        'ok', 'error:N' or 'ALARM:N', the time limit, a stop of `op`, or a port error.

        Cancellation and operation identity are checked before writes and after
        reads under the Stop lock. Buffered asynchronous alarms are classified,
        and residual acknowledgements are consumed before the next write.
        The read uses the port that was written to, never a replacement port.

        :return: (text, status). text: the decoded reply lines joined by '\\n'.
            status: 'ok', 'error', 'timeout', 'cancelled' or 'port_error'.
        """
        cmd = command.strip()
        if (self.__dict__.get('_grbl_caps') or {}).get('probe') is False and re.search(
                r'G\s*38\.[2345](?!\d)', cmd, re.IGNORECASE):
            return _("The controller has no probe input. Probing is unavailable."), 'error'
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op) or self._grbl_reset_pending:
                return '', 'cancelled'
            port = self.grbl_ser_port
            text, status = self._grbl_prepare_locked(op, port)
            if status is not None:
                return text, status
            # Tool measurements must also retire changed settings/requests at the write boundary.
            if guard is not None:
                guard()
            try:
                self._grbl_reply_pending = True
                self._grbl_stale_notified = False
                port.write((cmd + '\n').encode('utf-8'))
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_transact() -> %s" % str(err))
                self._grbl_failure_locked(op, 'port_error')
                return '', 'port_error'

        if echo:
            self.console_line_sig.emit(cmd)

        decoded_lines = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return '\n'.join(decoded_lines), 'cancelled'
            try:
                chunk = b'' if b'\n' in self._grbl_rx_buffer else port.readline()
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_transact() -> %s" % str(err))
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                return '\n'.join(decoded_lines), 'port_error'
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return '\n'.join(decoded_lines), 'cancelled'
                self._grbl_rx_buffer += chunk
                complete = self._grbl_rx_buffer.rsplit(b'\n', 1)[0] if b'\n' in self._grbl_rx_buffer else b''
                self._grbl_failure_locked(op, 'error', complete.decode('utf-8', errors='replace'))
                if op.failed:
                    self._grbl_rx_buffer = self._grbl_rx_buffer.rsplit(b'\n', 1)[-1]
                    return '\n'.join(decoded_lines + [complete.decode('utf-8', errors='replace').strip()]), 'error'
                while b'\n' in self._grbl_rx_buffer:
                    raw_line, self._grbl_rx_buffer = self._grbl_rx_buffer.split(b'\n', 1)
                    decoded_line = raw_line.decode('utf-8', errors='replace').strip()
                    if not decoded_line:
                        continue
                    decoded_lines.append(decoded_line)
                    if echo:
                        self.console_line_sig.emit(CONSOLE_ANSWER + decoded_line.upper())
                    lowered = decoded_line.lower()
                    if lowered == 'ok':
                        self._grbl_reply_pending = False
                        spindle = spindle_state_of_line(cmd)
                        if spindle is not None:
                            self.spindle_state_sig.emit(spindle)
                        text, status = self._grbl_prepare_locked(op, port)
                        if status is not None:
                            return '\n'.join(decoded_lines + [text]), status
                        return '\n'.join(decoded_lines), 'ok'
                    if lowered.startswith('error:'):
                        self._grbl_reply_pending = False
                        return '\n'.join(decoded_lines), 'error'

        with self._grbl_op_lock:
            if not self._grbl_active_locked(op, port):
                return '\n'.join(decoded_lines), 'cancelled'
            self._grbl_failure_locked(op, 'timeout')
        return '\n'.join(decoded_lines), 'timeout'

    def _grbl_update_ownership(self, report):
        """Regular status reports identify an external input owner, not just its transport."""
        if 'mpg' in report:
            self._grbl_mpg_owned = report['mpg']
        sd = report.get('sd', '')
        self._grbl_sd_owned = sd == 'Pending' or ',' in sd

    def _grbl_query_status(self, op, timeout=2.0):
        """Request a fresh status report, preserving async alarm and cancellation evidence."""
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op) or self._grbl_reset_pending:
                return None
            port = self.grbl_ser_port
            text, status = self._grbl_prepare_locked(op, port)
            if status is not None:
                return None
            try:
                port.write(self._grbl_realtime_byte(b'?'))
                if self.__dict__.get('_grbl_identity_pending'):
                    port.write(b'\x80')
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_query_status() -> %s" % str(err))
                self._grbl_failure_locked(op, 'port_error')
                return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return None
            try:
                chunk = b'' if b'\n' in self._grbl_rx_buffer else port.readline()
            except (serial.SerialException, OSError):
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                return None
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return None
                self._grbl_rx_buffer += chunk or b''
                complete = self._grbl_rx_buffer.rsplit(b'\n', 1)[0] if b'\n' in self._grbl_rx_buffer else b''
                text = complete.decode('utf-8', errors='replace')
                self._grbl_failure_locked(op, 'error', text)
                while b'\n' in self._grbl_rx_buffer:
                    raw_line, self._grbl_rx_buffer = self._grbl_rx_buffer.split(b'\n', 1)
                    line = raw_line.decode('utf-8', errors='replace').strip()
                    if line.startswith('<'):
                        report = parse_grbl_status_report(line)
                        if report is None and op.motion:
                            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                                "Invalid controller position report. Only finite XYZ reports are supported; "
                                "check the controller axis configuration."))
                            return None
                        if report is not None:
                            self._grbl_update_ownership(report)
                            if op.motion and (self.__dict__.get('_grbl_mpg_owned') or self._grbl_sd_owned):
                                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                                    "External SD/MPG execution owns the controller. Release it at its source first."))
                                return None
                        state = parse_grbl_status_state(line)
                        if state is None:
                            self._grbl_failure_locked(op, 'probe_invalid')
                            return None
                        text, status = self._grbl_prepare_locked(op, port)
                        if status is not None and state != 'Alarm':
                            return None
                        return line
                if op.failed:
                    return None
        with self._grbl_op_lock:
            if self._grbl_active_locked(op, port):
                self._grbl_failure_locked(op, 'timeout')
        return None

    def _grbl_confirm_reset(self, op, timeout=10.0):
        """The home worker ignores old replies until this reset epoch's startup banner."""
        port = op.port
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op, port) or op.safety_generation != self._grbl_safety_generation:
                return False
            if self._grbl_rx_port is not port:
                self._grbl_rx_port = port
                self._grbl_rx_buffer = b''

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._grbl_op_lock:
                if (not self._grbl_active_locked(op, port) or not self._grbl_reset_pending
                        or op.safety_generation != self._grbl_safety_generation):
                    return False
            try:
                chunk = b'' if b'\n' in self._grbl_rx_buffer else port.readline()
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_confirm_reset() -> %s" % str(err))
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                return False
            with self._grbl_op_lock:
                if (not self._grbl_active_locked(op, port) or not self._grbl_reset_pending
                        or op.safety_generation != self._grbl_safety_generation):
                    return False
                self._grbl_rx_buffer += chunk or b''
                while b'\n' in self._grbl_rx_buffer:
                    raw_line, self._grbl_rx_buffer = self._grbl_rx_buffer.split(b'\n', 1)
                    line = raw_line.decode('utf-8', errors='replace').strip()
                    if is_controller_banner(line):
                        # Only the same uncancelled reset epoch can retire old reply evidence.
                        self._grbl_reply_pending = False
                        self._grbl_reset_pending = False
                        self._grbl_stream_dirty = False
                        self._grbl_recovery = 'needs_home'
                        text, status = self._grbl_prepare_locked(op, port)
                        return status is None
                    # Pre-banner ok/error/ALARM/MSG lines belong to the previous controller epoch
                    # (GRBL 1.1 prints ALARM:3 before the banner when the reset hits a moving machine).

        with self._grbl_op_lock:
            if self._grbl_active_locked(op, port) and op.safety_generation == self._grbl_safety_generation:
                self._grbl_failure_locked(op, 'timeout')
        return False

    def _grbl_abort_stream_locked(self, op, reason, text=''):
        """
        A stream of `op` failed with lines in GRBL: hold, latch recovery. Caller holds the lock.
        It does nothing if `op` is cancelled, failed or replaced, or if a newer safety epoch
        exists (a Stop or a Reset of the user owns the recovery state then).

        It never changes _grbl_stream_dirty: only a confirmed completion or the reset banner do.

        :param reason: 'port_error', 'timeout', 'error', 'alarm' or 'line_too_long'.
        :param text:   the last reply line of GRBL.
        :return: True if this call (or the failure latch inside it) latched the recovery.
        """
        if (self._grbl_op is not op or op.cancel.is_set() or op.failed
                or op.safety_generation != self._grbl_safety_generation):
            return False
        self._grbl_failure_locked(op, reason, text)
        if op.failed:
            return True
        try:
            op.port.write(self._grbl_realtime_byte(b'!'))
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolGcodeSender._grbl_abort_stream_locked() -> %s" % str(err))
        self._grbl_recovery = 'needs_reset'
        self._grbl_reset_pending = False
        self._grbl_safety_generation += 1
        op.failed = True
        return True

    def _grbl_commit_success_locked(self, op):
        """
        Make a success final. Caller holds the lock.

        :return: True if `op` still owns the port, is not cancelled or failed, and no newer
            safety epoch exists. Then the stream epoch is clean.
        """
        if (self._grbl_op is not op or op.cancel.is_set() or op.failed
                or op.safety_generation != self._grbl_safety_generation):
            return False
        self._grbl_stream_dirty = False
        return True

    def _grbl_begin_reset_epoch_locked(self, port):
        """
        The receive boundary of a reset: everything received before it belongs to the old
        controller epoch. Caller holds the lock.

        :return: True if the receive buffer of the port was cleared.
        """
        self._grbl_rx_buffer = b''
        self._grbl_rx_port = port
        try:
            port.reset_input_buffer()
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolGcodeSender._grbl_begin_reset_epoch_locked() -> %s" % str(err))
            return False
        return True

    def _grbl_post_reset_state(self, op, timeout=2.0):
        """
        Read the machine state after a confirmed reset. Not _grbl_query_status: that one sends the
        report text to _grbl_failure_locked, which takes '<Alarm|...>' as a new alarm. After a reset
        with the homing lock, Alarm is the expected state.

        :return: the state word of the first status report, or None (an ALARM:N line, a timeout,
            a port error or a stop; the failure is latched where it applies).
        """
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op):
                return None
            port = self.grbl_ser_port
            try:
                port.write(self._grbl_realtime_byte(b'?'))
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolGcodeSender._grbl_post_reset_state() -> %s" % str(err))
                self._grbl_failure_locked(op, 'port_error')
                return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return None
            try:
                chunk = b'' if b'\n' in self._grbl_rx_buffer else port.readline()
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolGcodeSender._grbl_post_reset_state() -> %s" % str(err))
                with self._grbl_op_lock:
                    self._grbl_failure_locked(op, 'port_error')
                return None
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op, port):
                    return None
                self._grbl_rx_buffer += chunk or b''
                while b'\n' in self._grbl_rx_buffer:
                    raw_line, self._grbl_rx_buffer = self._grbl_rx_buffer.split(b'\n', 1)
                    line = raw_line.decode('utf-8', errors='replace').strip()
                    if line.lower().startswith('alarm:'):
                        self._grbl_failure_locked(op, 'error', line)
                        return None
                    if line.startswith('<'):
                        return parse_grbl_status_state(line)
                    # '[MSG:...]' and 'ok' lines are ignored

        with self._grbl_op_lock:
            if self._grbl_active_locked(op, port):
                self._grbl_failure_locked(op, 'timeout')
        return None

    def _grbl_exit_check_mode(self, op):
        """
        Leave the check mode of GRBL with $C. GRBL resets when it leaves the check mode, so this is
        a reset epoch: the banner confirms it. Call it only after a complete validation.

        :return: 'idle' (recovery is clear), 'alarm' (GRBL is in alarm after the reset: it needs
            Home; the recovery stays 'needs_home') or 'failed' (the caller must not commit).
        """
        with self._grbl_op_lock:
            if not self._grbl_active_locked(op):
                return 'failed'
            port = op.port
            if not self._grbl_begin_reset_epoch_locked(port):
                self._grbl_failure_locked(op, 'port_error')
                return 'failed'
            self._grbl_safety_generation += 1
            op.safety_generation = self._grbl_safety_generation
            self._grbl_reset_pending = True
            self._grbl_recovery = 'needs_reset'
            self._grbl_reply_pending = False
            try:
                port.write(b'$C\n')
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolGcodeSender._grbl_exit_check_mode() -> %s" % str(err))
                self._grbl_failure_locked(op, 'port_error')
                return 'failed'

        # [MSG:Disabled] and ok come before the banner: _grbl_confirm_reset ignores them
        if not self._grbl_confirm_reset(op):
            return 'failed'
        state = self._grbl_post_reset_state(op)
        with self._grbl_op_lock:
            if (self._grbl_op is not op or op.cancel.is_set() or op.failed
                    or op.safety_generation != self._grbl_safety_generation):
                return 'failed'
            if state == 'Idle':
                # check mode made no motion, and GRBL keeps its position at a reset in Idle
                self._grbl_recovery = None
                return 'idle'
            if state == 'Alarm':
                return 'alarm'
            self._grbl_failure_locked(op, 'probe_invalid')
            return 'failed'

    def _grbl_probe_run(self, op, context, points, tol, travel_z, depth, probe_fr, units_cmd, probe_timeout):
        """
        Probe every point. Runs in a worker thread and reads no widgets.

        Preconditions before any motion: GRBL is Idle, setting $13 is known,
        the units, G90 and G94 are acknowledged and confirmed by $G.
        Each probe must give 1 valid record for its point before the next move.
        A complete result goes to the GUI thread through al_result_ready_sig.
        """
        modes_ok = False
        offset = None
        staged = {}
        evidence = {'context': context, 'operation': op, 'safety_generation': op.safety_generation}
        precision = getattr(op, 'precision_inputs', ())

        def step(command, timeout=10.0):
            text, status = self._grbl_transact(op, command, timeout=timeout)
            if status != 'ok':
                raise _GrblProbeAbort(command, text, status)
            return text

        def check_frame():
            # A changed/unknown coordinate frame must not produce a guessed lift.
            try:
                text = step('$G')
                if not grbl_modes_confirmed(text, units_cmd) or parse_grbl_active_wcs(text) != active_wcs:
                    raise ValueError('distance mode, units or active WCS changed')
                if parse_grbl_work_offset(step('$#'), active_wcs) != offset:
                    raise ValueError('work offset changed')
                if parse_grbl_status_state(self._grbl_query_status(op)) != 'Idle':
                    raise ValueError('GRBL is not Idle')
            except ValueError as err:
                raise _GrblProbeAbort('$G / $# / ?', str(err), 'probe_invalid')
            except _GrblProbeAbort as abort:
                if abort.status == 'error':
                    raise _GrblProbeAbort(abort.command, abort.text, 'probe_invalid')
                raise

        try:
            state = parse_grbl_status_state(self._grbl_query_status(op))
            if state != 'Idle':
                raise _GrblProbeAbort('?', state or _("no answer"), 'not_idle')

            report_setting = parse_grbl_setting(step('$$'), 13)
            if report_setting is None:
                raise _GrblProbeAbort('$$', '', 'unknown_units')
            if precision and report_setting not in (0.0, 1.0):
                raise _GrblProbeAbort('$$', _('GRBL $13 must report 0 or 1 before precision probing.'), 'error')
            scale = grbl_report_scale(context.units, report_setting == 1.0)
            if precision and precision[1] <= 0.0001 * scale:
                raise _GrblProbeAbort('precision retract', _(
                    'Increase the precision retract above the GRBL position report resolution.'), 'error')

            step(units_cmd)
            step('G90')
            step('G94')
            gc_text = step('$G')
            if not grbl_modes_confirmed(gc_text, units_cmd):
                raise _GrblProbeAbort('$G', gc_text, 'modes_not_confirmed')
            modes_ok = True

            active_wcs = parse_grbl_active_wcs(gc_text)
            offset = parse_grbl_work_offset(step('$#'), active_wcs)
            self.grbl_work_offset = offset

            probe_cmd = 'G38.2 Z{0:.4f} F{1:.4f}'.format(depth, probe_fr)
            for key, (x, y) in points.items():
                step('G0 Z{0:.4f}'.format(travel_z))
                step('G0 X{0:.4f} Y{1:.4f}'.format(x, y))
                # GRBL answers 'ok' to G4 P0 only when all queued moves are done,
                # so the probe time limit starts when the probe move starts
                step('G4 P0', timeout=120.0)
                text = step(probe_cmd, timeout=probe_timeout)
                height = self._al_check_probe_reply(probe_cmd, text, key, points, tol, offset, scale)
                if precision:
                    fine_feed, retract = precision
                    retract_z = round(height + retract, 4)
                    if not depth <= height < retract_z <= travel_z:
                        raise _GrblProbeAbort(probe_cmd, _(
                            'Contact or precision retract is outside the depth/clearance limits.'), 'probe_invalid')
                    check_frame()
                    # PRB is machine position in report units. height is already
                    # converted to the active work frame and job units. Stay G90
                    # so every normal/error recovery lift uses the same frame.
                    step('G0 Z{0:.4f}'.format(retract_z))
                    step('G4 P0', timeout=120.0)
                    check_frame()
                    step('G4 P0', timeout=120.0)
                    fine_cmd = 'G38.2 Z{0:.4f} F{1:.4f}'.format(depth, fine_feed)
                    text = step(fine_cmd, timeout=probe_timeout_s(retract_z, depth, fine_feed))
                    height = self._al_check_probe_reply(fine_cmd, text, key, points, tol, offset, scale)
                    if not depth <= height < retract_z:
                        raise _GrblProbeAbort(fine_cmd, text, 'probe_invalid')
                    check_frame()
                    step('G0 Z{0:.4f}'.format(travel_z))
                    step('G4 P0', timeout=120.0)
                staged[key] = height

            step('G0 Z{0:.4f}'.format(travel_z))
            step('G4 P0', timeout=120.0)
            with self._grbl_op_lock:
                if not self._grbl_active_locked(op) or self._grbl_safety_generation != op.safety_generation:
                    raise _GrblProbeAbort('G4 P0', '', 'cancelled')
        except _GrblProbeAbort as abort:
            self._grbl_handle_abort(op, abort, travel_z, modes_ok)
            self.al_result_ready_sig.emit(dict(evidence, kind='failed'))
            return False
        except ValueError as err:
            # the $G or $# reply could not be read; the modes are confirmed, so lift Z
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not read the GRBL work offset"), str(err)))
            if precision:
                self._grbl_hold_for_recovery()
                self.app.inform.emit('[ERROR_NOTCL] %s' % self._grbl_recovery_text())
            elif offset is not None:
                self._grbl_retract(op, travel_z)
            self.al_result_ready_sig.emit(dict(evidence, kind='failed'))
            return False

        self.app.inform.emit('%s' % _("Finished probing. Doing the autolevelling."))
        # the frame of the map: the WCS, the work offset and the report units of GRBL ($13)
        frame = (active_wcs, offset, report_setting == 1.0)
        self.al_result_ready_sig.emit(dict(evidence, kind='probe', heights=staged, frame=frame))
        return True

    def _grbl_read_live_frame(self, op, units_cmd):
        """
        Read the frame of GRBL as _grbl_probe_run() does, with no motion and no mode command:
        $$ ($13), $G (units, G90, G94, active WCS) and $# (work offset), then a status report
        that must be Idle. Call it only when the last status was Idle. A worker thread; no widgets.

        :param units_cmd: 'G20' or 'G21': the units of the height map.
        :return: {'frame': (active_wcs, offset, report_inches)}, or {'error': text} (the text is for the
            user), with 'cancelled': True if the operation was stopped.
        """
        def step(command):
            text, status = self._grbl_transact(op, command, timeout=5.0, echo=False)
            if status != 'ok':
                raise _GrblProbeAbort(command, text, status)
            return text

        try:
            report_setting = parse_grbl_setting(step('$$'), 13)
            self._grbl_report_inches = None if report_setting is None else report_setting == 1.0
            if report_setting is None:
                return {'error': _("GRBL did not report the setting $13. The levelled job was not started.")}
            gc_text = step('$G')
            if not grbl_modes_confirmed(gc_text, units_cmd):
                return {'error': _(
                    "GRBL is not in the units of the height map, or not in G90/G94. "
                    "The levelled job was not started.")}
            active_wcs = parse_grbl_active_wcs(gc_text)
            offset = parse_grbl_work_offset(step('$#'), active_wcs)
            state = parse_grbl_status_state(self._grbl_query_status(op))
        except _GrblProbeAbort as abort:
            if abort.status == 'cancelled' or op.cancel.is_set():
                return {'error': _("The Start was stopped."), 'cancelled': True}
            return {'error': '%s: %s %s' % (
                _("Could not read the machine frame of GRBL"), abort.command, abort.text or abort.status)}
        except ValueError as err:
            return {'error': '%s: %s' % (_("Could not read the GRBL work offset"), str(err))}
        if op.cancel.is_set():
            return {'error': _("The Start was stopped."), 'cancelled': True}
        if state != 'Idle':
            return {'error': '%s: %s.' % (_("GRBL must be Idle to start the job. State"), state or _("no answer"))}
        return {'frame': (active_wcs, offset, report_setting == 1.0)}

    def _al_check_probe_reply(self, command, text, expected_key, points, tol, offset, scale):
        """
        Check the reply of 1 G38.2 move.

        :return: the height (app units) for expected_key.
        :raises _GrblProbeAbort: 'probe_failed' if a finite record proves no contact;
            'probe_invalid' if the record is malformed/nonfinite or not unique,
            or if the record is not at the commanded point.
        """
        try:
            results = parse_grbl_probe_output(text)
        except ValueError:
            # Validate the numeric record separately from its contact flag. A bad
            # number is uncertainty, not evidence that GRBL entered Alarm.
            records = [line.strip() for line in text.splitlines() if line.strip().startswith('[PRB:')]
            if len(records) == 1 and records[0].endswith(':0]'):
                try:
                    valid = parse_grbl_probe_output(records[0][:-3] + ':1]')
                except ValueError:
                    valid = []
                if len(valid) == 1:
                    raise _GrblProbeAbort(command, text, 'probe_failed')
            raise _GrblProbeAbort(command, text, 'probe_invalid')
        if len(results) != 1:
            raise _GrblProbeAbort(command, text, 'probe_invalid')

        mx, my, mz = results[0]
        ox, oy, oz = offset
        x = (mx - ox) * scale
        y = (my - oy) * scale
        z = (mz - oz) * scale
        if match_nearest_point(points, x, y, tol) != expected_key:
            raise _GrblProbeAbort(command, text, 'probe_invalid')
        return z

    def _grbl_hold_for_recovery(self):
        """Feed hold now; the machine needs a reset and a home cycle."""
        with self._grbl_op_lock:
            self._grbl_failure_locked(self._grbl_op, 'probe_invalid')

    def _grbl_retract(self, op, travel_z):
        """
        Lift Z to travel_z. Call it only after the units and G90 are confirmed.

        :return: True only after the accepted move physically completed.
        """
        text, status = self._grbl_transact(op, 'G0 Z{0:.4f}'.format(travel_z), timeout=30.0)
        if status == 'ok':
            text, status = self._grbl_transact(op, 'G4 P0', timeout=120.0)
            if status == 'ok':
                return True
        if status == 'cancelled':
            return False
        self._grbl_hold_for_recovery()
        self.app.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (
            _("The Z retract failed"), grbl_reply_error(text) or status, self._grbl_recovery_text()))
        return False

    def _grbl_handle_abort(self, op, abort, travel_z, modes_ok):
        """Tell the user why the probing run stopped, and make the machine safe."""
        status = abort.status
        if status == 'cancelled':
            # Stop, Reset or a disconnect already sent the hold or the reset
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Probing was stopped."))
            return

        if status == 'not_idle':
            self.app.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (
                _("GRBL state"), abort.text, _("GRBL must be Idle before probing. Reset or home GRBL.")))
            return

        if status == 'unknown_units':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "GRBL did not report setting $13 (report units). Probing was not started."))
            return

        if status in ('timeout', 'probe_invalid', 'port_error'):
            # the machine state is not known: stop the motion now
            self._grbl_hold_for_recovery()
            self.app.inform.emit('[ERROR_NOTCL] %s: %s (%s). %s' % (
                _("Probing stopped"), abort.command, status, self._grbl_recovery_text()))
            if status == 'probe_invalid' and abort.text:
                self.app.inform.emit('[ERROR_NOTCL] %s' % abort.text)
            return

        if status == 'probe_failed' or 'alarm' in (abort.text or '').lower():
            # GRBL is in alarm: it accepts no motion until a reset
            with self._grbl_op_lock:
                self._grbl_failure_locked(op, status, abort.text)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s. %s' % (
                _("The probe did not touch the surface or GRBL is in alarm"), abort.command,
                self._grbl_recovery_text()))
            return

        # 'error' or 'modes_not_confirmed': GRBL refused a command
        self.app.inform.emit('[ERROR_NOTCL] %s: %s -> %s' % (
            _("GRBL refused a command"), abort.command, grbl_reply_error(abort.text) or abort.text))
        if modes_ok:
            self._grbl_retract(op, travel_z)
        else:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _(
                "No Z retract was sent, because the units and the distance mode were not confirmed."))
