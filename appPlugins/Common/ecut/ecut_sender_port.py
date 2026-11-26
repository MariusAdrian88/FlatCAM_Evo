# ##########################################################
# FlatCAM Evo: E-Cut host sender connection                #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/20/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""In-process serial facade; one worker owns every E-Cut board operation."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import copy
import math
import struct
import threading
import time

import serial

from .ecut_config import SpindleControl
from .ecut_controller import EcutAPI, State
from .ecut_protocol import Cmd, EcutError, EcutUsbError


class _Interrupted(BaseException):
    """Unwind a synchronous planner after close, stop or a session reset."""


@dataclass
class _ProgramRequest:
    epoch: int
    lines: tuple
    event: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)


@dataclass
class _JogRequest:
    epoch: int
    axis: int
    direction: int
    feed_mm_min: float
    deadline: float
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class _MotionRequest:
    epoch: int
    operation: str
    arguments: tuple
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None
    result: dict | None = None


class EcutSenderPort:
    # The G-code Sender detects the adapter by this banner (its banner check).
    BANNER = "E-Cut host adapter 1.0 ['$' for help]"
    MAX_PENDING_BYTES = 65536
    MAX_LINE_BYTES = 4096
    MAX_CONTROLS = 256
    MAX_PROGRAM_BYTES = 32 * 1024 * 1024
    MAX_PROGRAM_LINES = 1000000
    PREPARE_TIMEOUT = 30.0
    HOLD_STATUS_MAX_AGE = 0.5
    JOIN_TIMEOUT = 5.0
    JOG_LEASE_SECONDS = 0.25
    MOTION_REQUEST_TIMEOUT = 40.0
    CALIBRATION_WATCHDOG = 35.0
    REALTIME = frozenset((ord('?'), ord('!'), ord('~'), 0x18, 0x84, 0x85,
                          *range(0x90, 0x9f), 0xa0, 0xa1))

    def __init__(self, api=None, settings_path=None, timeout=0.1, write_timeout=1.0, *, engine=None):
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.dtr = True
        self.tool_change_callback = None
        self._api = api
        self._engine = engine
        self._settings_path = settings_path
        self._condition = threading.Condition()
        self._rx = bytearray()
        self._partial = bytearray()
        self._lines = deque()
        self._controls = deque()
        self._queued_bytes = 0
        self._epoch = 0
        self._closing = False
        self._open = True
        self._active = False
        self._held = False
        self._stop_wait = False
        self._servicing = False
        self._pending_tool_change = None
        self._pause_reason = None
        self._announced_tool = None
        self._tool_change_token = None
        self._wake_banner = True
        self._utf8_remaining = 0
        self._startup_pending = True
        self._prepare_request = None
        self._preparing = None  # the request that the worker plans now
        self._jog_request = None
        self._motion_request = None
        self._motion_settings_snapshot = None
        self._prepared_job = None
        self._prepared_lines = ()
        self._prepared_index = 0
        self._prepared_ready = False
        self._program_failed = False
        self._alarm_announced = False
        self._status_frame = None
        self._status_frame_at = 0.0
        self._startup_error = None
        self._close_error = None
        self._ready = threading.Event()
        self._worker = threading.Thread(target=self._run, name='EcutSenderPort', daemon=True)
        self._worker.start()
        if not self._ready.wait(self.JOIN_TIMEOUT):
            self.close()
            raise serial.SerialException('E-Cut initialization timed out')
        if self._startup_error is not None:
            self._worker.join(self.JOIN_TIMEOUT)
            raise serial.SerialException(str(self._startup_error)) from self._startup_error

    @property
    def is_open(self):
        with self._condition:
            return self._open and not self._closing

    @property
    def in_waiting(self):
        with self._condition:
            return len(self._rx)

    @property
    def pending_tool_change(self):
        with self._condition:
            return self._pending_tool_change

    @property
    def pause_reason(self):
        with self._condition:
            return self._pause_reason

    @property
    def tool_change_token(self):
        with self._condition:
            return self._tool_change_token

    @property
    def jogging(self):
        with self._condition:
            return self._jog_request is not None

    def start_jog(self, axis, direction=0, feed_mm_min=None):
        """Lease a jog to the board worker; callers must refresh while held."""
        if type(axis) is not int or not 0 <= axis < 6:
            raise ValueError('Jog axis must be an integer from 0 to 5')
        if type(direction) is not int or direction not in (0, 1):
            raise ValueError('Jog direction must be 0 or 1')
        if (not isinstance(feed_mm_min, (int, float)) or isinstance(feed_mm_min, bool)
                or not math.isfinite(feed_mm_min) or feed_mm_min <= 0):
            raise ValueError('Jog feed must be finite and positive')
        with self._condition:
            self._require_open()
            if (self._active or self._held or self._startup_pending or self._lines or self._partial
                    or self._controls or self._pending_tool_change is not None or self._program_failed
                    or self._prepare_request is not None or self._prepared_job is not None
                    or self._jog_request is not None or self._motion_request is not None or self._engine.check_mode
                    or self._api.state != State.IDLE):
                raise serial.SerialException('E-Cut is busy or in check mode; cannot jog')
            self._jog_request = _JogRequest(self._epoch, axis, direction, feed_mm_min,
                                            time.monotonic() + self.JOG_LEASE_SECONDS)
            self._condition.notify_all()

    def get_motion_settings(self):
        with self._condition:
            if self._motion_settings_snapshot is None:
                raise serial.SerialException('E-Cut machine settings are not ready')
            return copy.deepcopy(self._motion_settings_snapshot)

    def _cache_motion_settings(self):
        getter = getattr(self._engine, 'get_motion_settings', None)
        if getter is not None:
            snapshot = getter()
            with self._condition:
                self._motion_settings_snapshot = snapshot

    def save_motion_settings(self, payload):
        return self._request_motion('save_motion_settings', copy.deepcopy(payload))

    def calibration_move(self, axis, distance_mm, feed_mm_min, revision):
        return self._request_motion('calibration_move', axis, distance_mm, feed_mm_min, revision)

    def reference_axes(self, axes):
        """Manual machine home of the axes (0..3) at the current position."""
        return self._request_motion('reference_axes', list(axes))

    def set_manual_output(self, pin, active, revision):
        if type(pin) is not int or not 0 <= pin < 8 or type(active) is not bool:
            raise ValueError('Manual output requires pin 0..7 and a boolean physical level')
        return self._request_motion('set_manual_output', pin, active, revision)

    def effective_inputs(self):
        """The input map of the present config (no USB; safe from the GUI thread)."""
        return self._api.effective_inputs()

    def effective_outputs(self):
        """The output map of the present config (no USB; safe from the GUI thread)."""
        return self._api.effective_outputs()

    def get_io_state(self):
        """Read the last worker status without accessing USB on the caller thread."""
        with self._condition:
            frame = self._status_frame
            age = max(0.0, time.monotonic() - self._status_frame_at) if frame is not None else None
            connected = self._open and not self._closing
            busy = (self._active or self._held or self._startup_pending or bool(self._lines)
                    or bool(self._partial) or self._pending_tool_change is not None
                    or self._prepare_request is not None or self._prepared_job is not None
                    or self._jog_request is not None or self._motion_request is not None
                    or self._program_failed or self._engine.check_mode or self._api.state != State.IDLE)
            return {'connected': connected, 'fresh': bool(connected and age is not None and age <= .5),
                    'age_s': age, 'configured': bool(frame is not None and frame.configured),
                    'inputs': int(frame.inputs_0_15) & 0xffff if frame is not None else 0,
                    'outputs': int(frame.outputs) & 0xff if frame is not None else 0, 'busy': bool(busy),
                    'homed_axes': list(self._api.homed_axes)}

    def get_queue_state(self):
        """Host-side queue for the Sender, readable from the GUI thread; O(1), no USB access.

        lines_waiting: complete lines accepted from the Sender, not yet started by the worker.
        blocks_waiting / blocks_active / records_unsent: motion of the running G-code call (a prepared
        program runs in batches, so later batches are not counted; 'program' says one is running).
        board_queued: True/False from a fresh board report (FIFO not empty or motion busy), else None.
        The board reports free FIFO units, not records, so no count of records on the board exists.
        """
        with self._condition:
            lines = len(self._lines)
            busy = bool(self._active or self._held or lines or self._pending_tool_change is not None
                        or self._prepare_request is not None or self._prepared_job is not None
                        or self._jog_request is not None or self._motion_request is not None
                        or self._api.state in (State.RUNNING, State.PAUSED))
            program = self._prepared_job is not None
            frame = self._status_frame
            fresh = (self._open and not self._closing and frame is not None and frame.configured
                     and time.monotonic() - self._status_frame_at <= self.HOLD_STATUS_MAX_AGE)
            board = bool(frame.motion_busy or frame.fifo_free != 12000) if fresh else None
            motion = self._api.queue_state()
        return {'idle': not busy and board is not True, 'lines_waiting': lines, 'program': program,
                'blocks_waiting': motion['blocks_waiting'], 'blocks_active': motion['blocks_active'],
                'records_unsent': motion['records_unsent'], 'board_queued': board}

    def _request_motion(self, operation, *arguments):
        if threading.current_thread() is self._worker:
            raise serial.SerialException('Motion settings cannot block the E-Cut worker')
        with self._condition:
            self._require_open()
            if (self._active or self._held or self._startup_pending or self._lines or self._partial
                    or self._controls or self._pending_tool_change is not None or self._program_failed
                    or self._prepare_request is not None or self._prepared_job is not None
                    or self._jog_request is not None or self._motion_request is not None
                    or self._engine.check_mode or self._api.state != State.IDLE):
                raise serial.SerialException('E-Cut is busy or in check mode; cannot tune motion or outputs')
            request = _MotionRequest(self._epoch, operation, arguments,
                                      time.monotonic() + self.MOTION_REQUEST_TIMEOUT)
            self._motion_request = request
            self._condition.notify_all()
        if not request.event.wait(self.MOTION_REQUEST_TIMEOUT):
            with self._condition:
                if not request.event.is_set():
                    request.error = serial.SerialTimeoutException('E-Cut motion request timed out; inspect machine state')
                    request.event.set()
                    self._condition.notify_all()
        if request.error is not None:
            raise serial.SerialException(str(request.error)) from request.error
        return request.result

    def _manual_output_on_worker(self, pin, active, revision):
        self._engine._require_tuning_idle(revision)
        frame = self._api._wait_for_rx(.5)
        if frame is None or not frame.configured or frame.motion_busy or self._api.state != State.IDLE:
            raise ValueError('Fresh configured idle board status required for manual output')
        if not self._api._initialized or self._api.config.to_dict() != self._api._initialized_config:
            raise ValueError('Manual output requires initialization with the current configuration')
        if active:
            self._api._check_resume_inputs()
        mask = frame.outputs | (1 << pin) if active else frame.outputs & ~(1 << pin)
        self._api._usb.cmd(Cmd.SET_OUTPUTS, struct.pack('>III', 0, 0xffff, mask & 0xffff))
        self._api._expected_outputs = mask & 0xffff
        self._api._logical_outputs = {name: bool(mask & (1 << sig.pin)) != bool(sig.inverted)
                                     for name, sig in self._api.config.outputs.items()
                                     if sig.enabled and not name.startswith(('STEP_', 'DIR_'))}
        # Keep a nonzero DAC speed guarded until an explicit M5 clears it.
        if any(sig.enabled and sig.pin == pin for name, sig in self._api.config.outputs.items()
               if name in ('SPINDLE_CW', 'SPINDLE_CCW', 'SPINDLE_PWM')):
            self._api._spindle_direction = None
            self._engine._parser.spindle = 5
        self._engine._parser.coolant = {code for name, code in (('MIST', 7), ('FLOOD', 8))
                                       if self._api._logical_outputs.get(name, False)}
        self._api._drain_rx()
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            frame = self._api.poll()
            if active and self._api.state in (State.ESTOP, State.ERROR, State.PAUSED):
                raise serial.SerialException('Manual output activation interrupted by interlock')
            if frame is not None and frame.outputs == mask & 0xffff:
                return self.get_io_state()
            time.sleep(.001)
        self._api._initialized = False
        self._api._set_state(State.ERROR)
        raise serial.SerialException('Timeout verifying manual output readback')

    def _motion_on_worker(self, request):
        try:
            self._service_controls()
            if request.epoch != self._epoch or request.event.is_set():
                raise _Interrupted()
            if request.operation == 'calibration_move':
                request.deadline = time.monotonic() + self.CALIBRATION_WATCHDOG
            if request.operation == 'set_manual_output':
                request.result = self._manual_output_on_worker(*request.arguments)
            else:
                request.result = getattr(self._engine, request.operation)(*request.arguments)
        except _Interrupted:
            if request.error is None:
                request.error = serial.SerialException('E-Cut motion request cancelled; inspect machine state')
            raise
        except Exception as error:
            request.error = error
            if not hasattr(error, 'code') and not isinstance(error, ValueError):
                raise
        finally:
            self._cache_motion_settings()
            with self._condition:
                if self._motion_request is request:
                    self._motion_request = None
                self._active = False
                request.event.set()
                self._condition.notify_all()

    def refresh_jog(self):
        with self._condition:
            request = self._jog_request
            if request is None or not self._jog_continue(request):
                return False
            request.deadline = time.monotonic() + self.JOG_LEASE_SECONDS
            return True

    def stop_jog(self):
        with self._condition:
            if self._jog_request is not None:
                self._jog_request.stop.set()
            self._condition.notify_all()

    def _jog_continue(self, request):
        with self._condition:
            live = (self._jog_request is request and request.epoch == self._epoch
                    and not self._closing and not request.stop.is_set()
                    and time.monotonic() < request.deadline)
            if not live:
                request.stop.set()
            return live

    def _jog_on_worker(self, request):
        try:
            if self._jog_continue(request):
                if self._api.state != State.IDLE or self._engine.check_mode:
                    self._emit(['error:3', '[MSG:E-Cut is not idle; cannot jog]'])
                    return
                self._api.jog_start(request.axis, request.direction, request.feed_mm_min,
                                    should_continue=lambda: self._jog_continue(request))
        except ValueError as error:
            self._emit(['error:3', '[MSG:%s]' % error])
        finally:
            with self._condition:
                if self._jog_request is request:
                    self._jog_request = None
                self._condition.notify_all()

    def _require_open(self):
        if not self._open or self._closing:
            raise serial.SerialException('E-Cut connection is closed')

    def _clear_commands(self):
        self._lines.clear()
        self._partial.clear()
        self._utf8_remaining = 0
        self._queued_bytes = 0
        self._controls.clear()
        if self._jog_request is not None:
            self._jog_request.stop.set()
            self._jog_request = None
        self._tool_change_token = None
        if self._prepare_request is not None:
            self._prepare_request.error = serial.SerialException('E-Cut program preparation cancelled')
            self._prepare_request.event.set()
            self._prepare_request = None
        if self._motion_request is not None:
            self._motion_request.error = serial.SerialException('E-Cut motion request cancelled; inspect machine state')
            self._motion_request.event.set()
            self._motion_request = None
        self._prepared_job = None
        self._prepared_lines = ()
        self._prepared_index = 0
        self._prepared_ready = False
        self._program_failed = False
        self._epoch += 1
        self._condition.notify_all()

    def prepare_program(self, lines):
        """Preflight on the board worker, then accept an exact source upload."""
        if threading.current_thread() is self._worker:
            raise serial.SerialException('Program preparation cannot block the E-Cut worker')
        expected = []
        total = 0
        for line in lines:
            if not isinstance(line, str) or not line or '\r' in line or '\n' in line:
                raise ValueError('Program requires nonempty source lines without line endings')
            try:
                raw = line.encode('utf-8')
            except UnicodeEncodeError as error:
                raise ValueError('E-Cut program source must be UTF-8 text') from error
            # In encoded text every byte >= 0x80 is part of a character, so only ASCII bytes can be commands.
            if len(raw) > self.MAX_LINE_BYTES or any(value in self.REALTIME for value in raw if value < 0x80):
                raise ValueError('Program source contains a real-time byte or an oversized line')
            total += len(raw) + 1
            if total > self.MAX_PROGRAM_BYTES or len(expected) >= self.MAX_PROGRAM_LINES:
                raise ValueError('E-Cut program exceeds the bounded host program capacity')
            expected.append(raw)
        if not expected:
            raise ValueError('E-Cut program is empty')
        with self._condition:
            self._require_open()
            if (self._active or self._held or self._startup_pending or self._lines or self._partial
                    or self._controls or self._pending_tool_change is not None or self._program_failed
                    or self._prepare_request is not None or self._prepared_job is not None
                    or self._jog_request is not None or self._motion_request is not None):
                raise serial.SerialException('E-Cut is busy; cannot prepare a program')
            request = _ProgramRequest(self._epoch, tuple(expected))
            self._prepare_request = request
            self._condition.notify_all()
        if not request.event.wait(max(self.PREPARE_TIMEOUT, len(expected) / 5000.0)):
            with self._condition:
                if not request.event.is_set():
                    request.cancelled.set()  # the planning checkpoint ends the planner
                    request.error = serial.SerialTimeoutException('E-Cut program preparation timed out')
                    if self._prepare_request is request:
                        self._prepare_request = None
                    request.event.set()
        if request.error is not None:
            raise serial.SerialException(str(request.error)) from request.error

    def _prepare_on_worker(self, request):
        self._preparing = request
        try:
            if self._api.state != State.IDLE:
                raise serial.SerialException('E-Cut board is not idle; cannot prepare a program')
            if self._engine.check_mode:
                raise serial.SerialException('Whole-program upload is unavailable in check mode')
            job = self._engine.prepare_program([raw.decode('utf-8') for raw in request.lines])
            if job is None:
                raise serial.SerialException('E-Cut program preparation returned no job')
            with self._condition:
                if self._prepare_request is request and request.epoch == self._epoch and not self._closing:
                    self._prepared_job = job
                    self._prepared_lines = request.lines
                    self._prepared_index = 0
                    self._prepared_ready = False
        except _Interrupted:
            if request.error is None:
                request.error = serial.SerialException('E-Cut program preparation interrupted')
            raise
        except Exception as error:
            request.error = error
        finally:
            self._preparing = None
            with self._condition:
                if self._prepare_request is request:
                    self._prepare_request = None
                request.event.set()

    def _accept_prepared_line(self, raw):
        with self._condition:
            if self._prepared_job is None:
                return False
            if (self._prepared_index >= len(self._prepared_lines)
                    or raw != self._prepared_lines[self._prepared_index]):
                self._clear_commands()
                self._program_failed = True
                self._emit(['error:3', '[MSG:E-Cut program upload mismatch; reset required]'])
                return True
            self._prepared_index += 1
            self._prepared_ready = self._prepared_index == len(self._prepared_lines)
            self._emit(['ok'])
            return True

    def _error_replies(self, error):
        """Sender protocol replies for a refused line. A USB failure or a program defect still closes the adapter."""
        if hasattr(error, 'code'):
            # the message tells the operator why, e.g. a spindle output without a role
            return ['[MSG:E-Cut: %s]' % error, 'error:%d' % error.code]
        if isinstance(error, EcutUsbError) or not isinstance(error, EcutError):
            raise error
        # error:9 = locked out during an alarm; the operator clears it with reset, as in the sender protocol
        code = 9 if self._api.state in (State.ESTOP, State.ERROR) else 20
        return ['[MSG:E-Cut: %s]' % error, 'error:%d' % code]

    def _classify(self, data):
        """Mark the real-time bytes. A byte inside a UTF-8 character (a comment) is text, not a command."""
        flags, remaining = [], self._utf8_remaining
        for value in data:
            if remaining and 0x80 <= value <= 0xbf:
                remaining -= 1
                flags.append(False)
                continue
            remaining = 3 if 0xf0 <= value <= 0xf4 else 2 if 0xe0 <= value <= 0xef else 1 if 0xc2 <= value <= 0xdf else 0
            flags.append(value in self.REALTIME)
        return flags, remaining

    def write(self, data):
        data = bytes(data)
        size = len(data)
        flags, utf8_remaining = self._classify(data)
        # A soft reset is stronger than a jog cancel in the same write.
        cancel = max((i for i, value in enumerate(data) if flags[i] and value == 0x18), default=-1)
        if cancel < 0:
            cancel = max((i for i, value in enumerate(data) if flags[i] and value == 0x85), default=-1)
        emergency = data[cancel] if cancel >= 0 else None
        if emergency is not None:
            data, flags = data[cancel + 1:], flags[cancel + 1:]
        normal = bytes(value for i, value in enumerate(data) if not flags[i])
        controls = [value for i, value in enumerate(data) if flags[i]]
        deadline = None if self.write_timeout is None else time.monotonic() + self.write_timeout
        with self._condition:
            self._require_open()
            if emergency is not None:
                # Reset/stop must remain available even when ordinary queues are full.
                if emergency == 0x85 and self._jog_request is not None:
                    self.stop_jog()
                else:
                    self._clear_commands()
                    self._rx.clear()
                self._controls.append(('realtime', emergency))
            if normal and (self._program_failed or self._prepare_request is not None
                           or self._prepared_ready or self._jog_request is not None or self._motion_request is not None):
                raise serial.SerialException('E-Cut program is preparing, executing or requires reset')
            if len(normal) > self.MAX_PENDING_BYTES:
                raise serial.SerialTimeoutException('E-Cut host queue capacity exceeded')
            while self._queued_bytes + len(normal) > self.MAX_PENDING_BYTES:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise serial.SerialTimeoutException('E-Cut host queue is full')
                self._condition.wait(remaining)
                self._require_open()
            candidate = self._partial + normal
            if any(len(line) > self.MAX_LINE_BYTES for line in candidate.replace(b'\r', b'\n').split(b'\n')):
                raise serial.SerialException('E-Cut command line is too long')
            if len(self._controls) + len(controls) > self.MAX_CONTROLS:
                raise serial.SerialTimeoutException('E-Cut real-time queue is full')
            for i, value in enumerate(data):
                if flags[i]:
                    if value in (0x18, 0x85):
                        # a queued soft reset must survive a later jog cancel
                        resets = [item for item in self._controls if item == ('realtime', 0x18)]
                        self._clear_commands()
                        self._controls.extend(resets)
                        self._rx.clear()
                    self._controls.append(('realtime', value))
                elif value in (10, 13):
                    if self._partial:
                        self._lines.append((self._epoch, bytes(self._partial)))
                        self._partial.clear()
                        self._wake_banner = False
                    # Empty CR/LF wakes the host adapter after the sender clears its banner.
                    elif self._wake_banner:
                        self._controls.append(('wake', None))
                        self._wake_banner = False
                else:
                    self._partial.append(value)
                    self._queued_bytes += 1
            # after the loop: a reset inside the loop clears the state
            self._utf8_remaining = utf8_remaining
            self._condition.notify_all()
        return size

    def read(self, size=1):
        return self._read(size, False)

    def readline(self, size=-1):
        return self._read(size, True)

    def _read(self, size, line):
        if size == 0:
            return b''
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        result = bytearray()
        with self._condition:
            while size < 0 or len(result) < size:
                available = len(self._rx)
                if available:
                    count = available if size < 0 else min(available, size - len(result))
                    if line:
                        newline = self._rx.find(b'\n', 0, count)
                        if newline >= 0:
                            count = newline + 1
                    result.extend(self._rx[:count])
                    del self._rx[:count]
                    if line and result.endswith(b'\n'):
                        break
                    if size < 0 and not line:
                        break
                if not self._open or self._closing:
                    break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(remaining)
        return bytes(result)

    def readlines(self):
        result = []
        while True:
            line = self.readline()
            if not line:
                return result
            result.append(line)

    def reset_input_buffer(self):
        with self._condition:
            self._rx.clear()

    def confirm_tool_change(self, tool, token=None):
        if type(tool) is not int or tool < 0:
            raise ValueError('Tool number must be a nonnegative integer')
        with self._condition:
            self._require_open()
            if len(self._controls) >= self.MAX_CONTROLS:
                raise serial.SerialTimeoutException('E-Cut real-time queue is full')
            self._controls.append(('tool', (tool, self._tool_change_token if token is None else token)))
            self._condition.notify_all()

    def close(self):
        with self._condition:
            was_closing = self._closing
            self._closing = True
            self._clear_commands()
            self._condition.notify_all()
        if threading.current_thread() is self._worker:
            return
        # a repeated close must not block the GUI thread again for the full timeout
        self._worker.join(0.01 if was_closing else self.JOIN_TIMEOUT)
        if self._worker.is_alive():
            raise serial.SerialException('E-Cut worker did not stop; reconnection is unsafe')
        error, self._close_error = self._close_error, None  # reported once
        if error is not None:
            raise serial.SerialException('E-Cut shutdown failed: %s' % error) from error

    def _emit(self, lines):
        with self._condition:
            if self._closing:
                return
            for line in lines:
                self._rx.extend((line.rstrip('\r\n') + '\r\n').encode('ascii', errors='replace'))
            self._condition.notify_all()

    def _snapshot(self):
        tool = self._engine.pending_tool_change
        with self._condition:
            self._pending_tool_change = tool
            self._pause_reason = self._engine.pause_reason
            if tool != self._announced_tool:
                self._tool_change_token = object() if tool is not None else None
        if tool != self._announced_tool:
            self._announced_tool = tool
            if tool is not None:
                self._emit(['[ECUT:TOOL:%d]' % tool])
                callback = self.tool_change_callback
                if callback is not None:
                    callback(tool)

    def _announce_alarm(self):
        """One ALARM line per change to ESTOP/ERROR. Only the limit and E-stop inputs have their own code;
        a soft limit, homing or probe fault reports ALARM:1 because the controller does not track that cause."""
        if self._api.state not in (State.ESTOP, State.ERROR):
            self._alarm_announced = False
        elif not self._alarm_announced:
            with self._condition:
                self._emit(self._alarm_lines())

    def _alarm_lines(self, *extra):
        """The ALARM line of a fault, once per fault: after it, _announce_alarm stays quiet."""
        if self._api.state not in (State.ESTOP, State.ERROR):
            return ['ALARM:1', *extra]
        self._alarm_announced = True
        lines = ['ALARM:%d' % self._api.fault_alarm]
        if self._api.fault_reason:
            lines.append('[MSG:E-Cut: %s]' % self._api.fault_reason)
        return [*lines, *extra]

    def _status(self):
        with self._condition:
            busy = (self._active or bool(self._lines) or bool(self._partial)
                    or self._prepare_request is not None or self._prepared_job is not None
                    or self._motion_request is not None)
        state = None
        if self.jogging:
            state = 'Jog'
        elif self._held or self._api.state == State.PAUSED:
            frame = self._status_frame
            stationary = (time.monotonic() - self._status_frame_at <= self.HOLD_STATUS_MAX_AGE
                          and self._api.is_stationary(frame))
            state = 'Hold:0' if stationary else 'Hold:1'
        elif busy and self._api.state in (State.IDLE, State.RUNNING):
            state = 'Run'
        self._emit([self._engine.status_report(state=state)])

    def _on_rx(self, frame):
        if frame is not None:
            with self._condition:
                self._status_frame = frame
                self._status_frame_at = time.monotonic()
        if self._servicing:
            return
        self._snapshot()
        self._service_controls()

    def _service_controls(self):
        if self._servicing:
            return
        self._servicing = True
        try:
            while True:
                if self._stop_wait and self._api.stop_completed:
                    # the decelerated stop is over (0x85): end the program
                    self._stop_wait = False
                    self._held = False
                    raise _Interrupted()
                if self._preparing is not None and self._preparing.cancelled.is_set():
                    raise _Interrupted()
                with self._condition:
                    if self._closing:
                        raise _Interrupted()
                    request = self._motion_request
                    if request is not None and (request.event.is_set() or time.monotonic() >= request.deadline):
                        request.error = serial.SerialTimeoutException('E-Cut motion request watchdog expired; machine stopped')
                        self._api.stop()
                        if not self._active:
                            self._motion_request = None
                            request.event.set()
                        raise _Interrupted()
                    if not self._controls:
                        return
                    kind, value = self._controls.popleft()
                try:
                    if kind == 'wake':
                        self._emit([self.BANNER])
                    elif kind == 'tool':
                        tool, token = value
                        with self._condition:
                            valid = (token is not None and token is self._tool_change_token
                                     and tool == self._pending_tool_change)
                        if not valid:
                            self._emit(['error:3'])
                            continue
                        self._engine.confirm_tool_change(tool)
                    elif value == ord('?'):
                        self._status()
                    elif self._motion_request is not None and value != 0x18:
                        if value in (ord('!'), 0x84, 0x85):
                            self._api.stop()
                            raise _Interrupted()
                        self._emit(['[MSG:E-Cut real-time command refused (error 20)]'])
                    elif self.jogging and value in (ord('!'), ord('~'), 0x85):
                        if value != ord('~'):
                            self.stop_jog()
                            self._api.jog_stop()
                        # A held jog always brakes; resume never restarts it.
                    elif self.jogging and (0x90 <= value <= 0x9e or value in (0xa0, 0xa1)):
                        self._emit(['[MSG:E-Cut real-time command refused (error 20)]'])
                    elif value == 0x18:
                        self._engine.reset()
                        self._held = False
                        self._stop_wait = False
                        self._startup_pending = True
                        self._snapshot()
                        self._emit([self.BANNER])
                        raise _Interrupted()
                    else:
                        with self._condition:
                            prepared = self._prepare_request is not None or self._prepared_job is not None
                            ready = self._prepared_ready
                        # the feed override (0x90-0x94) reaches the engine once the prepared program runs
                        if prepared and (0x90 <= value <= 0x9e or value in (0xa0, 0xa1)) and not (
                                ready and 0x90 <= value <= 0x94):
                            self._emit(['[MSG:E-Cut real-time command refused (error 20)]'])
                            continue
                        replies = self._engine.realtime(value)
                        if value == 0x85 and self._api.stop_pending:
                            # the streaming loop brakes; the entry check above ends the program
                            self._stop_wait = True
                        elif value == ord('!'):
                            self._held = True
                        elif value in (ord('~'), 0x85):
                            self._held = False
                        self._emit(replies)
                        if value == 0x85 and not self._stop_wait:
                            raise _Interrupted()
                except Exception as error:
                    # Rejected controls are visible but must not discard a pending M6.
                    if not hasattr(error, 'code'):
                        raise
                    self._emit(['[MSG:E-Cut real-time command refused (error %d): %s]' % (error.code, error)])
                self._snapshot()
        finally:
            self._servicing = False

    def _initialize(self):
        if self._api is None:
            self._api = EcutAPI()
            for axis in range(6):
                self._api.configure_axis(axis, enabled=axis < 3)
            self._api.configure_spindle(SpindleControl.PWM, rpm_max=24000.0)
            self._api.config.spindle.pwm_period_counts = 1000
            # No output has a role by default: the operator assigns SPINDLE_CW/CCW to O0..O7.
            self._api.configure_output('SPINDLE_CW', enabled=False)
            self._api.configure_output('SPINDLE_CCW', enabled=False)
        if self._engine is None:
            from .ecut_gcode_engine import EcutGcodeEngine
            self._engine = EcutGcodeEngine(self._api, settings_path=self._settings_path)
        self._api.on_rx_frame(self._on_rx)
        self._api._planning_checkpoint = self._service_controls
        if not self._api.connect():
            raise serial.SerialException('Could not connect to E-Cut USB board')
        self._api.push_config()
        self._api.poll()
        self._cache_motion_settings()
        self._emit([self.BANNER])

    def _run(self):
        initialized = False
        try:
            self._initialize()
            initialized = True
            self._ready.set()
            while not self._closing:
                try:
                    self._service_controls()
                    self._api.poll()
                    self._announce_alarm()
                    with self._condition:
                        if self._closing:
                            break
                        if self._prepared_ready and self._partial:
                            self._accept_prepared_line(bytes(self._partial))
                            continue
                        if self._held or not (self._startup_pending or self._lines
                                              or self._prepare_request is not None or self._prepared_ready
                                              or self._jog_request is not None or self._motion_request is not None):
                            self._condition.wait(0.01)
                            continue
                        jog = None
                        motion = None
                        if self._startup_pending:
                            self._startup_pending = False
                            startup = True
                            request = job = None
                        elif self._jog_request is not None:
                            jog = self._jog_request
                            startup = False
                            request = job = None
                        elif self._motion_request is not None:
                            motion = self._motion_request
                            startup = False
                            request = job = None
                        elif self._prepare_request is not None:
                            request = self._prepare_request
                            startup = False
                            job = None
                        elif self._prepared_ready and not (self._lines or self._partial):
                            job = self._prepared_job
                            startup = False
                            request = None
                        else:
                            startup = False
                            request = job = None
                            epoch, raw = self._lines.popleft()
                            self._queued_bytes -= len(raw)
                            self._condition.notify_all()
                        epoch = self._epoch
                        self._active = True
                    if startup:
                        try:
                            replies = self._engine.run_startup()
                        except Exception as error:
                            # The sender protocol reports a bad startup block and stays connected
                            replies = self._error_replies(error)
                        self._cache_motion_settings()
                        with self._condition:
                            if epoch == self._epoch and not self._closing:
                                self._emit(replies)
                        continue
                    if jog is not None:
                        self._jog_on_worker(jog)
                        continue
                    if motion is not None:
                        self._motion_on_worker(motion)
                        continue
                    if request is not None:
                        self._prepare_on_worker(request)
                        continue
                    if job is not None:
                        failure = None
                        try:
                            self._engine.execute_program(job)
                        except Exception as error:
                            self._error_replies(error)  # raises for a USB failure or a program defect
                            failure = ['[MSG:E-Cut program stopped: %s]' % error]
                        with self._condition:
                            if epoch == self._epoch:
                                self._prepared_job = None
                                self._prepared_lines = ()
                                self._prepared_ready = False
                                if failure is not None:
                                    # the upload lines are already acknowledged: report an alarm, require reset
                                    self._program_failed = True
                                    self._emit(self._alarm_lines(*failure))
                        self._snapshot()
                        continue
                    if self._accept_prepared_line(raw):
                        continue
                    try:
                        line = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        self._emit(['error:1'])
                        continue
                    try:
                        replies = self._engine.execute_line(line)
                    except Exception as error:
                        replies = self._error_replies(error)
                    self._cache_motion_settings()
                    with self._condition:
                        if epoch == self._epoch and not self._closing:
                            self._emit(replies)
                    self._snapshot()
                except _Interrupted:
                    continue
                finally:
                    self._active = False
        except _Interrupted:
            if not initialized:
                self._startup_error = serial.SerialException('E-Cut startup cancelled')
        except Exception as error:
            if not initialized:
                self._startup_error = error
            else:
                with self._condition:
                    self._clear_commands()
                self._emit(self._alarm_lines('[MSG:E-Cut error: %s]' % error))
        finally:
            self._servicing = True
            if self._api is not None:
                try:
                    if self._api.connected:
                        self._api.stop()
                except Exception as error:
                    self._close_error = error
                try:
                    self._api.disconnect()
                except Exception as error:
                    self._close_error = error
            with self._condition:
                self._open = False
                self._clear_commands()
                self._pending_tool_change = None
                self._pause_reason = None
                self._condition.notify_all()
            self._ready.set()
