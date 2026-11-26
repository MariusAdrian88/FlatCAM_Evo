# ##########################################################
# FlatCAM Evo: E-Cut USB Motion Controller API             #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/19/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Public API for E-Cut USB motion controller.

Usage:
    from appPlugins.Common.ecut import EcutAPI

    api = EcutAPI()
    api.load_config('my_machine.json')
    api.connect()
    api.push_config()  # initialize RAM and preserve measured position
    api.run_gcode(['G0 X10 Y10', 'G1 X20 F500'])
    api.disconnect()

ponytail: no Mach3 dependency. FlatCAM's GCode is the planner.
"""
from __future__ import annotations

import logging
import math
import re
import struct
import sys
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Sequence

from .ecut_protocol import (
    EcutUsb, EcutUsbError, EcutError, Cmd, encode_motion_record, EcutRxFrame,
    MAX_MOTION_RECORDS_PER_PACKET, REPORT_SIZE,
    build_tx_packet, build_axis_config_packet, build_pin_config_packet, build_input_config_packet,
    build_motion_limits_packet, build_target_packet, build_soft_limits_packet, build_spindle_pwm_packet,
)
from .ecut_config import (
    EcutBoardConfig, AxisDirection, SignalPolarity, SpindleControl, InputSignal, ProbeMode,
)

from .ecut_planner import (BacklashState, LinearMove, apply_backlash, backlash_active, backlash_axis_active,
                           backlash_reversal, plan_path_segments, iter_jog_segments)

log = logging.getLogger(__name__)

INT16_MAX = 32767


class State(Enum):
    DISCONNECTED = 0
    IDLE = 1
    RUNNING = 2
    PAUSED = 3
    ESTOP = 4
    ERROR = 5


_HOMING_PROBING_MSG = 'homing/probing completion is not verified'
_UNVERIFIED_NAMES = {'homing': 'Sensor homing', 'probing': 'Probing',
                     'backlash': 'Backlash compensation', 'mpg': 'MPG handwheel'}


@dataclass(frozen=True)
class _Modal:
    """G-code modal state: units (G20/G21), distance mode (G90/G91), motion mode (G0/G1), feed in mm/min."""
    units_mm: bool = True
    absolute: bool = True
    motion: Optional[str] = None
    feed_mm_min: Optional[float] = None
    spindle_rpm: float = 0.0
    selected_tool: Optional[int] = None


@dataclass(frozen=True)
class _SpindleAction:
    command: Optional[int]
    rpm: float


@dataclass(frozen=True)
class _DwellAction:
    seconds: float


@dataclass(frozen=True)
class _ToolSelectAction:
    tool: int


@dataclass(frozen=True)
class _ManualPauseAction:
    tool: Optional[int] = None


@dataclass(frozen=True)
class _MoveAction:
    rapid: bool
    absolute: bool
    words: dict          # axis index -> value in mm
    feed_mm_min: Optional[float]
    corner_deviation_mm: Optional[float] = None   # limit for the corner at the END of the move (LinearMove)


@dataclass(frozen=True)
class _PlannedPathAction:
    start_steps: tuple[int, ...]
    target_steps: tuple[int, ...]
    segments: Sequence[tuple[int, ...]]
    feed_mm_min: Optional[float]
    # backlash (directions, offsets) before and after the path; a run checks the start state
    backlash_start: tuple = ((0,) * 6, (0,) * 6)
    backlash_end: tuple = ((0,) * 6, (0,) * 6)
    # axes whose offset changes anywhere in the path (a reversal and its way back cancel at the end)
    backlash_changed: frozenset = frozenset()

@dataclass(frozen=True)
class _ProbeAction(_MoveAction):
    pass


class MotionLine(str):
    """G-code text of one move plus the limit for the corner at its end (``LinearMove.corner_deviation_mm``).

    ``run_gcode`` takes plain text lines; the host G-code engine sends this subclass for the moves of a
    G61/G64 program so the path control mode reaches the planner. Plain strings keep the junction setting.
    """
    corner_deviation_mm: Optional[float]

    def __new__(cls, text: str, corner_deviation_mm: Optional[float] = None):
        line = super().__new__(cls, text)
        line.corner_deviation_mm = corner_deviation_mm
        return line


_WORD_RE = re.compile(r'\s*([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))')
_AXIS_LETTERS = 'XYZABC'


def _strip_comments(line: str) -> str:
    line = line.split(';', 1)[0]
    return re.sub(r'\([^)]*\)', ' ', line).strip()


def _tokenize(line: str, lineno: int) -> list[tuple[str, str, float]]:
    """(letter, raw number text, value) for each word. Anything that is not a word is an error."""
    text = _strip_comments(line).upper()
    words = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == '':
            break
        m = _WORD_RE.match(text, pos)
        if not m:
            raise EcutError(f'line {lineno}: cannot parse {text[pos:].strip()!r}')
        value = float(m.group(2))
        if not math.isfinite(value):
            raise EcutError(f'line {lineno}: invalid number in {m.group(0).strip()!r}')
        words.append((m.group(1), m.group(2), value))
        pos = m.end()
    return words


def _compile_line(line: str, lineno: int, modal: _Modal) -> tuple[_Modal, list]:
    """Validate one line against the modal state. Returns the new modal state and the actions to execute.

    Pure: sends nothing. Raises EcutError naming the line number and the offending word.
    """
    words = _tokenize(line, lineno)
    if not words:
        return modal, []

    def bad(letter, raw, why='unsupported'):
        return EcutError(f'line {lineno}: {why} word {letter}{raw}')

    units = dist = motion = None
    dwell = False
    feed_mode = False
    for (letter, raw, v) in words:
        if letter != 'G':
            continue
        code = f'{v:g}'
        if code in ('20', '21'):
            if units is not None:
                raise bad('G', raw, 'conflicting')
            units = (code == '21')
        elif code in ('90', '91'):
            if dist is not None:
                raise bad('G', raw, 'conflicting')
            dist = (code == '90')
        elif code in ('0', '1', '31', '38.2'):
            if motion is not None:
                raise bad('G', raw, 'conflicting')
            motion = 'G' + code
        elif code == '94':
            if feed_mode:
                raise bad('G', raw, 'duplicate')
            feed_mode = True
        elif code == '4':
            if dwell:
                raise bad('G', raw, 'duplicate')
            dwell = True
        elif code == '28':
            raise EcutError(f'line {lineno}: word G{raw}: {_HOMING_PROBING_MSG}')
        else:
            raise bad('G', raw)

    new = modal
    if units is not None:
        new = replace(new, units_mm=units)
    if dist is not None:
        new = replace(new, absolute=dist)
    if motion is not None:
        new = replace(new, motion=motion)

    scale = 1.0 if new.units_mm else 25.4
    axis_words: dict[int, float] = {}
    stop = False
    spindle = None
    speed = None
    duration = tool = manual = None
    for (letter, raw, v) in words:
        if letter == 'G':
            continue
        if letter in _AXIS_LETTERS:
            idx = _AXIS_LETTERS.index(letter)
            if idx in axis_words:
                raise bad(letter, raw, 'duplicate')
            axis_words[idx] = v * (scale if idx < 3 else 1)    # A B C are degrees: no inch scale
        elif letter == 'F':
            if v <= 0:
                raise bad(letter, raw, 'invalid')
            new = replace(new, feed_mm_min=v * scale)
        elif letter == 'P':
            if not dwell or duration is not None or v < 0:
                raise bad(letter, raw, 'invalid')
            duration = v
        elif letter == 'T':
            if tool is not None or not v.is_integer() or not 0 <= v <= 0x7fffffff:
                raise bad(letter, raw, 'invalid')
            tool = int(v)
            new = replace(new, selected_tool=tool)
        elif letter == 'M' and f'{v:g}' in ('0', '6'):
            if manual is not None:
                raise bad(letter, raw, 'conflicting')
            manual = int(v)
        elif letter == 'S':
            if speed is not None:
                raise bad(letter, raw, 'duplicate')
            if v < 0:
                raise bad(letter, raw, 'invalid')
            speed = v
            new = replace(new, spindle_rpm=v)
        elif letter == 'M' and f'{v:g}' in ('3', '4', '5'):
            if spindle is not None:
                raise bad(letter, raw, 'conflicting')
            spindle = int(v)
        elif letter == 'M' and f'{v:g}' in ('2', '30'):
            stop = True
        else:
            raise bad(letter, raw)

    if dwell and (duration is None or axis_words or motion is not None):
        raise EcutError(f'line {lineno}: G4 requires nonnegative P seconds and no axis/motion words')
    if manual is not None and (stop or dwell or spindle in (3, 4)):
        raise EcutError(f'line {lineno}: conflicting manual pause commands')
    if manual == 6 and (new.selected_tool is None or axis_words):
        raise EcutError(f'line {lineno}: M6 requires a selected T tool and no axis words')
    actions: list = []
    if tool is not None:
        actions.append(_ToolSelectAction(tool))
    if spindle is not None or speed is not None:
        actions.append(_SpindleAction(spindle, new.spindle_rpm))
    if axis_words:
        if new.motion is None:
            raise EcutError(f'line {lineno}: axis word without a motion mode (G0/G1)')
        if new.motion in ('G1', 'G31', 'G38.2') and not new.feed_mm_min:
            raise EcutError(f'line {lineno}: {new.motion} without a feed rate (F)')
        if new.motion in ('G31', 'G38.2'):
            if len(axis_words) != 1 or next(iter(axis_words)) >= 3:
                raise EcutError(f'line {lineno}: {new.motion} requires one XYZ axis')
            actions.append(_ProbeAction(False, new.absolute, axis_words, new.feed_mm_min))
        else:
            actions.append(_MoveAction(new.motion == 'G0', new.absolute, axis_words, new.feed_mm_min))
    if dwell:
        actions.append(_DwellAction(duration))
    if manual is not None:
        actions.append(_ManualPauseAction(new.selected_tool if manual == 6 else None))
    if stop:
        actions.append('stop')
    return new, actions


def compile_program(lines: list[str], modal: Optional[_Modal] = None, *, checkpoint: Optional[Callable[[], None]] = None) -> list:
    """Validate a whole program; returns the flat list of (line number, action). Sends nothing."""
    modal = modal or _Modal()
    actions: list = []
    for n, line in enumerate(lines, 1):
        if checkpoint is not None:
            checkpoint()
        modal, acts = _compile_line(line, n, modal)
        if isinstance(line, MotionLine) and line.corner_deviation_mm is not None:
            acts = [replace(a, corner_deviation_mm=line.corner_deviation_mm) if type(a) is _MoveAction else a
                    for a in acts]
        actions.extend((n, a) for a in acts)
    return actions


class _StepAccumulator:
    def __init__(self, config: EcutBoardConfig):
        self.config = config
        self.steps = [0] * 6

    def to_steps(self, pos_mm: list[Optional[float]]) -> list[int]:
        """Absolute target in steps. A None entry keeps the current step position of that axis."""
        result = []
        for i in range(6):
            ax = self.config.axes[i]
            if not ax.enabled or pos_mm[i] is None:
                result.append(self.steps[i])
                continue
            raw = pos_mm[i] * ax.steps_per_mm
            result.append(int(round(raw * self.config.interpolation_factor * int(ax.direction))))
        return result

    def total_deltas(self, target_mm: list[Optional[float]]) -> list[int]:
        """Step distance to the target; not limited to int16."""
        target_steps = self.to_steps(target_mm)
        if any(not (-0x80000000 <= value <= 0x7fffffff) for value in target_steps):
            raise EcutError('Target exceeds signed 32-bit board position range')
        return [target_steps[i] - self.steps[i] for i in range(6)]

    @staticmethod
    def split(total: list[int], fractions: list[float]) -> list[list[int]]:
        """Split a move into segments with the cumulative fractions of the distance (last one must be 1.0).

        Every axis gets the same fraction, the cumulative step count is rounded, so the segment deltas add up to
        exactly the total and a multi-axis move keeps its direction. Never clips.
        """
        segments = []
        prev = [0] * 6
        last = len(fractions) - 1
        for k, f in enumerate(fractions):
            cum = list(total) if k == last else [int(round(t * f)) for t in total]
            segments.append([cum[i] - prev[i] for i in range(6)])
            prev = cum
        return segments

    @classmethod
    def split_uniform(cls, total: list[int]) -> list[list[int]]:
        """Fewest equal segments so that every delta fits int16."""
        n = max(1, math.ceil(max(abs(t) for t in total) / INT16_MAX))
        while True:
            segments = cls.split(total, [(k + 1) / n for k in range(n)])
            if all(abs(v) <= INT16_MAX for seg in segments for v in seg):
                return segments
            n += 1

    def deltas(self, target_mm: list[Optional[float]]) -> list[int]:
        d = self.total_deltas(target_mm)
        if any(abs(v) > INT16_MAX for v in d):
            raise EcutError('Move does not fit one motion record, use split_uniform()')
        return d

    def advance(self, d: list[int]) -> None:
        for i in range(6):
            self.steps[i] += d[i]

    def reset(self) -> None:
        self.steps = [0] * 6

    def position_mm(self, offsets=None) -> list[float]:
        """Axis positions; ``offsets`` (backlash counts, board minus logical) gives the logical position."""
        offsets = offsets or (0,) * 6
        return [
            (self.steps[i] - offsets[i]) / (self.config.axes[i].steps_per_mm * self.config.interpolation_factor
                                            * int(self.config.axes[i].direction))
            if self.config.axes[i].enabled else 0.0
            for i in range(6)
        ]


# Native FUN_100129a0: input slots 0..31, independent of our UI/name ordering.
_INPUT_WIRE_NAMES = tuple(
    name for axis in 'XYZABC' for name in (axis + '_LIMIT_PLUS', axis + '_LIMIT_MINUS', axis + '_HOME')
) + ('INPUT_1', 'INPUT_2', 'INPUT_3', 'INPUT_4', 'PROBE', 'SPINDLE_INDEX',
     'LIMIT_OVERRIDE', 'ESTOP', 'THC_ON', 'THC_UP', 'THC_DOWN', 'DOOR', 'FEED_HOLD', 'CYCLE_START')


class _HoldableRecords:
    """1 ms records of a planned path. A hold slows the time along the same path.

    While held, the actual speed falls by the axis deceleration limit every record until it is 0.
    After release it rises by the acceleration limit until the planned speed. The emitted counts
    are rounded positions on the planned path, so their sum is exactly the path total.
    A feed override (rate_limit, 0.1 to 1.0) moves the goal speed to that share of the planned
    speed, inside the same deceleration (down) and acceleration (up) limits.
    Without a hold or override the planned records pass unchanged.
    """

    def __init__(self, segments, config: EcutBoardConfig, held: Callable[[], bool],
                 rate_limit: Callable[[], float] = lambda: 1.0):
        self._records = iter(segments)
        self._count = len(segments)
        self._held = held
        self._rate_limit = rate_limit
        self._scales = [abs(axis.steps_per_mm * config.interpolation_factor) for axis in config.axes]
        self._acceleration = [axis.acceleration_mm_s2 for axis in config.axes]
        self._deceleration = [axis.effective_deceleration_mm_s2 for axis in config.axes]
        self._index = 0  # path record that holds self._s
        self._origin = [0] * 6  # path counts at the start of that record
        self._record = self._read()
        self._s = 0  # position on the path, in records; int until the first hold
        self._emitted = [0] * 6
        self._speed = None  # actual mm/s while the time is scaled
        self.stopped = False

    def _read(self):
        record = next(self._records, None) if self._index < self._count else None
        return tuple(record) if record is not None else (0,) * 6

    def _seek(self, s):
        while self._index < self._count and s >= self._index + 1:
            self._origin = [a + b for a, b in zip(self._origin, self._record)]
            self._index += 1
            self._record = self._read()

    def _nominal(self, limits):
        """Planned speed (mm/s) of the current record and the limit along its direction (mm/s2)."""
        distances = [count / scale for count, scale in zip(self._record, self._scales)]
        length = math.hypot(*distances)
        if length == 0:
            return 0.0, 0.0
        return length * 1000, min(limit * length / abs(d) for limit, d in zip(limits, distances) if d)

    @property
    def done(self) -> bool:
        return self._s >= self._count

    @property
    def remaining(self) -> int:
        """Planned path records not yet taken (a hold stretches the time, not the path)."""
        return max(0, math.ceil(self._count - self._s))

    def take(self, count: int) -> list[tuple[int, ...]]:
        out = []
        while len(out) < count and not self.done:
            held = self._held()
            limit_rate = self._rate_limit()
            if not held and limit_rate == 1.0 and self._speed is None and type(self._s) is int:
                # no hold yet: the planned record, unchanged
                out.append(self._record)
                self._emitted = [a + b for a, b in zip(self._emitted, self._record)]
                self._s += 1
                self._seek(self._s)
                continue
            nominal, decel = self._nominal(self._deceleration)
            accel = self._nominal(self._acceleration)[1]
            if self._speed is None:
                self._speed = nominal
            goal = 0.0 if held else nominal * limit_rate
            if self._speed > goal:
                target = max(goal, self._speed - decel * 0.001)
            else:
                target = min(goal, self._speed + accel * 0.001)
            if held:
                if nominal > 0 and target <= 0 or nominal == 0:
                    self._speed, self.stopped = 0.0, True
                    break
            else:
                self.stopped = False
            rate = 1.0 if nominal == 0 else min(1.0, target / nominal)
            s = min(float(self._count), self._s + rate)
            self._seek(s)
            fraction = s - self._index
            position = [round(o + r * fraction) for o, r in zip(self._origin, self._record)]
            out.append(tuple(p - e for p, e in zip(position, self._emitted)))
            self._emitted, self._s = position, s
            self._speed = rate * nominal
            if not held and limit_rate == 1.0 and rate == 1.0 and s == int(s):
                self._s, self._speed = int(s), None  # back at the planned speed on a whole record
        return out


def encode_config(config: EcutBoardConfig) -> bytes:
    """Seven initialization reports, encoded from native FUN_100129a0 and its helpers."""
    config.validate()
    if config.mpg.enabled and not config.unverified.mpg:
        raise EcutError('MPG motion dispatch is not yet verified')
    if config.spindle.control == SpindleControl.STEP_DIR:
        raise EcutError('Step/direction spindle drive is not yet verified')
    signals = []
    for name in _INPUT_WIRE_NAMES:
        sig = config.inputs.get(name, InputSignal())
        if name == 'ESTOP' and config.safety.estop_input_enabled:
            if sig.enabled and (sig.pin != config.safety.estop_input_pin
                                or sig.polarity != config.safety.estop_polarity):
                raise EcutError('Conflicting emergency-stop input assignments')
            sig = InputSignal(True, config.safety.estop_input_pin, config.safety.estop_polarity, 0)
        signals.append((sig.enabled and sig.port == 1, sig.pin,
                        sig.polarity == SignalPolarity.ACTIVE_LOW))
    mpg = bytearray(63)
    # 9B: step/direction inversion flags at wire33 and selected MPG counter at35.
    flags = sum((int(ax.dir_active_low) << i) | (int(ax.step_active_low) << (8 + i))
                for i, ax in enumerate(config.axes))
    struct.pack_into('>H', mpg, 32, flags)
    struct.pack_into('>I', mpg, 34, config.mpg.port_select)
    for i, homing in enumerate(config.homing):
        value = int(homing.home_offset_mm * config.axes[i].steps_per_mm)
        struct.pack_into('>i', mpg, 38 + 4 * i, value)
    io = [(ax.step_pin, ax.dir_pin, ax.step_port, ax.dir_port,
           ax.step_active_low, ax.dir_active_low, ax.enabled) for ax in config.axes]
    io.append(tuple(config.spindle_axis_io))
    # Preserve the plugin's X-direction replication quirk in A2 until firmware
    # semantics for this word are independently established.
    axis_flags = 0x3f if config.axes[0].direction == AxisDirection.REVERSE else 0
    return b''.join((
        build_tx_packet(Cmd.CONFIG, bytes(32) + b'\x00\x02'),
        build_tx_packet(Cmd.SET_POSITION, bytes(32) + b'\x00\x3f'),
        build_tx_packet(Cmd.MPG_CONFIG, bytes(mpg)),
        build_axis_config_packet(tuple(int(ax.steps_per_mm) for ax in config.axes),
                                 config.spindle.delay_between_pulse_and_dir_us,
                                 config.interpolation_factor,
                                 tuple(int(ax.work_offset_mm * ax.steps_per_mm) for ax in config.axes)),
        build_input_config_packet(signals, axis_flags),
        build_pin_config_packet(config.step_functions, config.dir_functions, io),
        build_tx_packet(Cmd.MODE, bytes(32) + b'\x00\x04'),
    ))


def effective_inputs(config: EcutBoardConfig) -> list:
    """Inputs as the board reads them, with the same rules as encode_config.

    Each entry: name, pin, inverted (active low), readable, reason. The safety E-stop override applies.
    """
    names = list(config.inputs)
    if config.safety.estop_input_enabled and 'ESTOP' not in names:
        names.append('ESTOP')
    out = []
    for name in names:
        sig = config.inputs.get(name, InputSignal())
        if name == 'ESTOP' and config.safety.estop_input_enabled:
            sig = InputSignal(True, config.safety.estop_input_pin, config.safety.estop_polarity, 0)
        readable = bool(sig.enabled and sig.port == 1 and 0 <= sig.pin <= 15)
        out.append({'name': name, 'pin': sig.pin, 'inverted': sig.polarity == SignalPolarity.ACTIVE_LOW,
                    'readable': readable, 'reason': '' if readable else 'not readable (port/pin)'})
    return out


def effective_outputs(config: EcutBoardConfig) -> list:
    """Digital outputs the board can switch: 1 entry per pin (name, pin, inverted), names merged."""
    by_pin = {}
    for name, sig in config.outputs.items():
        if not sig.enabled or not 0 <= sig.pin <= 7 or name.startswith(('STEP_', 'DIR_', 'SPINDLE_PWM')):
            continue
        entry = by_pin.get(sig.pin)
        if entry is None:
            by_pin[sig.pin] = {'names': [name], 'pin': sig.pin, 'inverted': bool(sig.inverted)}
        else:
            entry['names'].append(name)
    return list(by_pin.values())


def decode_ecut_io(io_state: dict, inputs: list, outputs: list) -> dict:
    """name -> (True | False | None, reason) from EcutSenderPort.get_io_state()."""
    stale = not (io_state.get('fresh') and io_state.get('configured'))
    result = {}
    for entry in inputs:
        if not entry['readable']:
            result[entry['name']] = (None, entry['reason'])
        elif stale:
            result[entry['name']] = (None, 'no fresh state')
        else:
            high = bool(int(io_state['inputs']) & (1 << entry['pin']))
            result[entry['name']] = (high != entry['inverted'], '')
    for entry in outputs:
        for name in entry['names']:
            if stale:
                result[name] = (None, 'no fresh state')
            else:
                high = bool(int(io_state['outputs']) & (1 << entry['pin']))
                result[name] = (high != entry['inverted'], '')
    return result


class EcutAPI:
    """Public API for E-Cut USB motion controller.

    Lifecycle:
        1. Create:          api = EcutAPI()
        2. Configure:       api.load_config('machine.json')  or  api.configure(...)
        3. Connect:         api.connect()
        4. Operate:         api.push_config()  # initialize RAM and preserve measured position, api.run_gcode(...), api.jog(...), etc.
        5. Disconnect:      api.disconnect()
    """

    def __init__(self):
        self._config = EcutBoardConfig()
        self._usb = EcutUsb(self._config.vid, self._config.pid)
        self._state = State.DISCONNECTED
        self._acc = _StepAccumulator(self._config)
        self._traj_id = 0
        self._initialized = False
        self._initialized_config = None
        self._expected_outputs = 0
        self._logical_outputs = {}
        self._spindle_direction = None
        self._spindle_rpm = 0.0
        self._spindle_pwm_owned = False
        self._spindle_period_counts = 1000
        self._selected_tool = None
        self._current_tool = None
        self._pending_tool_change = None
        self._pause_reason = None
        self._pause_restore = None
        self._pause_started = None
        self._paused_seconds = 0.0
        self._stop_generation = 0
        self._input_values = {}
        self._input_pending = {}
        self._expansion_inputs = None
        self._telemetry = {}
        self._homing_axis = None
        self._native_motion_active = False
        self._jog_active = False
        self._jog_cancel = threading.Event()
        self._homed_axes = set()
        # host backlash compensation: last direction per axis (1, -1, 0 unknown) and the offset in counts
        # (board minus logical); the offset lives and dies with the reference of its axis
        self._backlash_dir = [0] * 6
        self._backlash_offset = [0] * 6
        self._takeup_held = None   # continuation check of the backlash take-up of a continuous jog
        self._takeup_cut = False
        self._backlash_pending = set()   # axes whose offset changed in the path that is running
        self._last_probe_result = None
        self._fault_reason = None
        self._fault_alarm = 1
        self._last_rx: Optional[EcutRxFrame] = None
        self._running = False
        self._feed_override = 1.0
        self._rate_limit = 1.0  # live share of the planned speed (0.1 to 1.0) for the streamed records
        self._modal = _Modal(units_mm=self._config.units_mm)
        self._feed_mm_min: Optional[float] = None
        self._on_state_change: Optional[Callable[[State], None]] = None
        self._on_rx: Optional[Callable[[EcutRxFrame], None]] = None
        self._planning_checkpoint: Optional[Callable[[], None]] = None
        # decelerated stop (request_stop): set by the worker only
        self._streaming = False
        self._stop_requested = False
        self._stop_deadline = 0.0
        self.stop_completed = False
        # host-side queue of the running program (see queue_state); written by the worker only
        self._queue_lock = threading.Lock()
        self._queue = self._empty_queue()

    # ------------------------------------------------------------------
    # Configuration API
    # ------------------------------------------------------------------

    @property
    def config(self) -> EcutBoardConfig:
        return self._config

    def load_config(self, path: str | Path) -> None:
        if self.connected:
            raise EcutError('disconnect() before loading another configuration')
        self._config = EcutBoardConfig.load(path)
        self._usb = EcutUsb(self._config.vid, self._config.pid)
        self._acc = _StepAccumulator(self._config)
        self._modal = _Modal(units_mm=self._config.units_mm)
        log.info('Config loaded from %s', path)

    def save_config(self, path: str | Path) -> None:
        self._config.save(path)

    def configure_axis(self, axis: int, *, steps_per_mm: Optional[float] = None,
                       direction: Optional[AxisDirection] = None,
                       enabled: Optional[bool] = None,
                       max_velocity: Optional[float] = None,
                       acceleration: Optional[float] = None,
                       deceleration: Optional[float] = None,
                       soft_limit_pos: Optional[float] = None,
                       soft_limit_neg: Optional[float] = None,
                       backlash: Optional[float] = None) -> None:
        if not (0 <= axis < 6):
            raise ValueError(f'Axis must be 0-5, got {axis}')
        # Zero clears an explicit braking override and follows acceleration.
        if deceleration is not None and (isinstance(deceleration, bool)
                or not isinstance(deceleration, (int, float))
                or not 0 <= deceleration <= sys.float_info.max):
            raise ValueError('Deceleration must be finite and nonnegative')
        ax = self._config.axes[axis]
        if steps_per_mm is not None:
            ax.steps_per_mm = steps_per_mm
        if direction is not None:
            ax.direction = direction
        if enabled is not None:
            ax.enabled = enabled
        if max_velocity is not None:
            ax.max_velocity_mm_s = max_velocity
        if acceleration is not None:
            ax.acceleration_mm_s2 = acceleration
        if deceleration is not None:
            ax.deceleration_mm_s2 = None if deceleration == 0 else deceleration
        if soft_limit_pos is not None:
            ax.soft_limit_positive_mm = soft_limit_pos
        if soft_limit_neg is not None:
            ax.soft_limit_negative_mm = soft_limit_neg
        if backlash is not None:
            ax.backlash_mm = backlash

    def configure_input(self, signal_name: str, *, enabled: bool = True,
                        pin: int = 0, polarity: SignalPolarity = SignalPolarity.ACTIVE_LOW,
                        debounce_ms: int = 20) -> None:
        if type(pin) is not int or not 0 <= pin < 32 or type(debounce_ms) is not int or debounce_ms < 0:
            raise ValueError('Input pin must be 0..31 and debounce must be nonnegative')
        if signal_name not in self._config.inputs:
            raise ValueError(f'Unknown input signal: {signal_name}')
        sig = self._config.inputs[signal_name]
        sig.enabled = enabled
        sig.pin = pin
        sig.polarity = polarity
        sig.debounce_ms = debounce_ms

    def configure_output(self, signal_name: str, *, enabled: bool = True,
                         pin: int = 0, inverted: bool = False) -> None:
        if type(pin) is not int or not 0 <= pin < 16:
            raise ValueError('Output pin must be 0..15')
        if signal_name not in self._config.outputs:
            raise ValueError(f'Unknown output signal: {signal_name}')
        out = self._config.outputs[signal_name]
        out.enabled = enabled
        out.pin = pin
        out.inverted = inverted

    def configure_homing(self, axis: int, *, enabled: bool = True,
                         direction: AxisDirection = AxisDirection.FORWARD,
                         fast_vel: float = 10.0, slow_vel: float = 2.0,
                         offset: float = 0.0) -> None:
        if not (0 <= axis < 6):
            raise ValueError(f'Axis must be 0-5, got {axis}')
        h = self._config.homing[axis]
        h.enabled = enabled
        h.direction = direction
        h.fast_velocity_mm_s = fast_vel
        h.slow_velocity_mm_s = slow_vel
        h.home_offset_mm = offset

    def configure_probe(self, *, fast_vel: float = 5.0, slow_vel: float = 0.5,
                        max_travel: float = 50.0, retract: float = 1.0) -> None:
        p = self._config.probe
        p.fast_velocity_mm_s = fast_vel
        p.slow_velocity_mm_s = slow_vel
        p.max_travel_mm = max_travel
        p.retract_mm = retract

    def configure_spindle(self, control: SpindleControl, *,
                          rpm_min: float = 0.0, rpm_max: float = 24000.0,
                          pulses_per_rev: int = 0) -> None:
        s = self._config.spindle
        s.control = control
        s.speed_rpm_min = rpm_min
        s.speed_rpm_max = rpm_max
        s.pulses_per_revolution = pulses_per_rev

    def set_interpolation(self, index: int) -> None:
        if not (0 <= index <= 9):
            raise ValueError(f'Interpolation index must be 0-9, got {index}')
        self._config.interpolation_index = index

    # ------------------------------------------------------------------
    # Connection API
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if self.connected:
            raise EcutError('Already connected')
        try:
            self._usb.vid = self._config.vid
            self._usb.pid = self._config.pid
            self._usb.open()
            self._input_values.clear()
            self._input_pending.clear()
            self._expansion_inputs = None
            self._homed_axes.clear()
            self._clear_backlash()
            self._last_probe_result = None
            self._native_motion_active = False
            self._initialized = False
            self._set_state(State.IDLE)
            return True
        except EcutUsbError as e:
            log.error('Connect failed: %s', e)
            self._set_state(State.ERROR)
            return False

    def disconnect(self) -> None:
        try:
            # also after an error: stop and outputs off are always safe to send
            if self.connected:
                self._shutdown(Cmd.STOP)
        finally:
            self._input_values.clear()
            self._input_pending.clear()
            self._expansion_inputs = None
            self._homed_axes.clear()
            self._clear_backlash()
            self._last_probe_result = None
            self._native_motion_active = False
            self._initialized = False
            self._running = False
            self._spindle_direction = None
            self._spindle_rpm = 0.0
            self._spindle_pwm_owned = False
            self._initialized_config = None
            self._usb.close()
            self._set_state(State.DISCONNECTED)

    def restart(self) -> None:
        self.disconnect()
        if not self.connect():
            raise EcutUsbError('Reconnect failed')
        self.push_config()

    def encode_config(self) -> bytes:
        """Encode the selected RAM initialization configuration without opening USB."""
        return encode_config(self._config)

    def effective_inputs(self, config: Optional[EcutBoardConfig] = None) -> list:
        return effective_inputs(self._config if config is None else config)

    def effective_outputs(self, config: Optional[EcutBoardConfig] = None) -> list:
        return effective_outputs(self._config if config is None else config)

    def push_config(self) -> None:
        """Initialize RAM, preserve measured counts and deactivate configured outputs."""
        self._require_connected()
        payload = self.encode_config()  # validate everything before sending anything
        self._input_values.clear()
        self._input_pending.clear()
        self._expansion_inputs = None
        # the counts are kept (checked below): a reference stays valid while its axis scale is the same
        # a reset during motion stops without deceleration: nothing is kept
        self._forget_reference_if_moving()
        # an offset that is not 0 is part of the reference: a changed backlash setting removes both
        kept = {axis for axis in self._homed_axes if self._same_axis_scale(axis)
                and (self._same_axis_backlash(axis) or not self._backlash_offset[axis])}
        for axis in range(6):
            if axis not in kept:
                self._clear_backlash([axis])
            elif not self._same_axis_backlash(axis):
                self._backlash_dir[axis] = 0   # the last direction is not known for the new setting
        self._homed_axes.clear()
        self._last_probe_result = None
        self._native_motion_active = False
        self._initialized = False
        self._running = False
        self._logical_outputs.clear()
        self._expected_outputs = self._inactive_outputs()
        try:
            self._shutdown(Cmd.STOP)
            inactive = self._inactive_outputs()
            if inactive != self._expected_outputs:
                self._expected_outputs = inactive
                self._usb.cmd(Cmd.SET_OUTPUTS, struct.pack('>III', 0, 0xffff, inactive))
            if self._config.spindle.control == SpindleControl.PWM:
                self._spindle_period_counts = self._config.spindle.pwm_period_counts
                self._send_spindle_pwm(0)
            frame = self._wait_idle()
            positions = frame.axis_positions
            for start in range(0, len(payload), REPORT_SIZE):
                packet = bytearray(payload[start:start + REPORT_SIZE])
                if packet[1] == Cmd.SET_POSITION:
                    struct.pack_into('>6i', packet, 6, *positions)
                self._usb.cmd(packet[1], bytes(packet[2:]))
            self._usb.cmd(Cmd.SET_OUTPUTS, struct.pack('>III', 0, 0xffff, self._expected_outputs))
            frame = self._wait_idle()
            if not frame.configured or frame.axis_positions != positions:
                raise EcutError('Initialization readback did not preserve configured positions')
            self._acc.steps = list(frame.axis_positions)
            self._set_state(State.IDLE)
            self._check_interlocks(frame)
            if self._state == State.ESTOP:
                raise EcutError('Initialization refused: an input interlock is active')
            self._initialized_config = self._config.to_dict()
            self._initialized = True
            self._homed_axes.update(kept)
            self._fault_reason = None
            self._fault_alarm = 1
            self._set_state(State.IDLE)
        except Exception:
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            raise

    def _same_axis_scale(self, axis: int) -> bool:
        previous = self._initialized_config
        if previous is None or previous.get('interpolation_index') != self._config.interpolation_index:
            return False
        before, now = previous['axes'][axis], self._config.axes[axis]
        return before['steps_per_mm'] == now.steps_per_mm and before['direction'] == int(now.direction)

    def _same_axis_backlash(self, axis: int) -> bool:
        previous = self._initialized_config
        if previous is None:
            return False
        before, now = previous['axes'][axis], self._config.axes[axis]
        return (before['backlash_mm'] == now.backlash_mm
                and before['backlash_compensation_enabled'] == now.backlash_compensation_enabled)

    def _clear_backlash(self, axes=range(6)) -> None:
        for axis in axes:
            self._backlash_dir[axis] = self._backlash_offset[axis] = 0
        self._backlash_pending.difference_update(axes)

    def _lose_backlash(self, axes) -> None:
        """The offset of these axes is not known: they lose their reference and their offset."""
        axes = [axis for axis in axes if backlash_axis_active(self._config.axes[axis])]
        for axis in axes:
            self._homed_axes.discard(axis)
        self._clear_backlash(axes)

    def _backlash_state(self, steps=None) -> BacklashState:
        return BacklashState(tuple(self._acc.steps if steps is None else steps),
                             tuple(self._backlash_dir), tuple(self._backlash_offset))

    def logical_position_mm(self, frame=None) -> list[float]:
        """Axis positions in mm without the backlash offset; of a board frame, else of the host counts."""
        if frame is None:
            return self._acc.position_mm(self._backlash_offset)
        return [(frame.axis_positions[i] - self._backlash_offset[i])
                / (ax.steps_per_mm * self._config.interpolation_factor * int(ax.direction))
                for i, ax in enumerate(self._config.axes)]

    def reference_axes(self, axes) -> None:
        """Manual homing, without a home switch: the current position becomes each axis home position.

        The home position is the homing ``home_offset_mm`` of the axis (0 = Mach3 auto zero). The board
        counts change with a0 (the captured all-axis layout) and must read back, else the axes stay
        unreferenced.
        """
        axes = sorted(set(axes))
        if not axes or any(type(axis) is not int or not 0 <= axis < 6 for axis in axes):
            raise ValueError('Reference axes must be in 0..5')
        if any(not self._config.axes[axis].enabled for axis in axes):
            raise EcutError('Cannot reference a disabled axis')
        self._require_state(State.IDLE)
        self._require_motion_allowed()
        if not self._initialized or self._jog_active:
            raise EcutError('Reference requires an initialized, stopped board')
        frame = self._wait_idle()
        target = list(frame.axis_positions)
        for axis in axes:
            value = self._config.homing[axis].home_offset_mm * self._axis_scale(axis)
            if not math.isfinite(value) or not -0x80000000 <= value <= 0x7fffffff:
                raise EcutError('Home position exceeds the signed 32-bit board position range')
            target[axis] = round(value)
        for axis in axes:
            self._homed_axes.discard(axis)
        self._clear_backlash(axes)   # the board counts are set new
        packet =bytearray(build_tx_packet(Cmd.SET_POSITION, bytes(32) + b'\x00\x3f'))
        struct.pack_into('>6i', packet, 6, *target)
        self._usb.cmd(packet[1], bytes(packet[2:]))
        # the board applies the counts about 10 ms after a0: idle frames before that still show the old counts
        end = time.monotonic() + 1.0
        while True:
            frame = self._wait_idle()
            if frame.axis_positions == tuple(target):
                break
            if time.monotonic() >= end:
                raise EcutError('The board did not confirm the new home counts; axes stay unreferenced')
        self._acc.steps = list(target)
        self._homed_axes.update(axes)

    def save_config_to_board(self) -> None:
        raise EcutError('Persistent configuration writes are not verified')

    @property
    def state(self) -> State:
        return self._state

    @property
    def connected(self) -> bool:
        return self._usb.is_open

    def on_state_change(self, callback: Callable[[State], None]) -> None:
        self._on_state_change = callback

    def on_rx_frame(self, callback: Callable[[EcutRxFrame], None]) -> None:
        self._on_rx = callback

    # ------------------------------------------------------------------
    # Motion control API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._require_connected()
        self._forget_reference_if_moving()
        self._native_motion_active = False
        self._running = False
        try:
            self._shutdown(Cmd.STOP)
            frame = self._wait_idle()
            self._acc.steps = list(frame.axis_positions)
            if self._state not in (State.ESTOP, State.ERROR):
                self._set_state(State.IDLE)
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            raise

    @property
    def stop_pending(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        """Stop a streamed program along its path: brake at the deceleration limit, then stop.

        The worker finishes the stop (`_finish_requested_stop`) when the board is stationary, so the machine
        reference stays. At `_stop_deadline` it stops abruptly. Anywhere else this is stop().
        """
        self._require_connected()
        if not (self._streaming and self._running and self._pause_reason is None
                and self._state in (State.RUNNING, State.PAUSED)):
            self.stop()
            return
        if not self._stop_requested:
            self.stop_completed = False
            self._stop_requested = True
            self._stop_deadline = time.monotonic() + 5.0
            self._set_state(State.PAUSED)  # _HoldableRecords brakes while PAUSED

    def is_stationary(self, frame: Optional[EcutRxFrame]) -> bool:
        """Hold complete: the board is configured, idle, its FIFO is empty and it is at the target counts."""
        return (frame is not None and frame.configured and not frame.motion_busy
                and frame.fifo_free == 12000 and self._at_target_counts(frame))

    def _at_target_counts(self, frame: EcutRxFrame) -> bool:
        """The measured counts of the enabled axes are the target; a disabled axis keeps any retained count."""
        return all(measured == target for measured, target, axis
                   in zip(frame.axis_positions, self._acc.steps, self._config.axes) if axis.enabled)

    def _finish_requested_stop(self) -> None:
        # nothing moves: _shutdown() does not forget the machine reference (_forget_reference_if_moving)
        self._stop_requested = False
        self._running = False
        try:
            self._shutdown(Cmd.STOP)
            frame = self._wait_idle()
            self._acc.steps = list(frame.axis_positions)
            # a path with a reversal stopped half way: the offset of its axes is not known
            self._lose_backlash(self._backlash_pending)
            if self._state not in (State.ESTOP, State.ERROR):
                self._set_state(State.IDLE)
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            raise
        self._end_requested_stop()

    def _abort_requested_stop(self) -> None:
        # the board did not stop in time: abrupt stop, the reference is forgotten
        self._stop_requested = False
        self.stop()
        self._end_requested_stop()

    def _end_requested_stop(self) -> None:
        self.stop_completed = True
        if self._planning_checkpoint is not None:
            self._planning_checkpoint()  # the sender port ends the program here

    def estop(self) -> None:
        self._require_connected()
        # state first: nothing may be submitted after this point, even if the USB write fails
        self._forget_reference_if_moving()
        self._running = False
        self._set_state(State.ESTOP)
        self._shutdown(Cmd.ESTOP)

    def reset(self) -> None:
        """Software stop and full RAM reinitialization, preserving measured positions."""
        self.push_config()

    def pause(self) -> None:
        if self._state == State.RUNNING:
            if self._native_motion_active:
                self._fault_reason = 'Sensor cycle aborted by feed hold; start a fresh cycle after reset'
                self._sensor_error()
            else:
                self._set_state(State.PAUSED)

    @property
    def selected_tool(self) -> Optional[int]:
        return self._selected_tool

    @property
    def current_tool(self) -> Optional[int]:
        return self._current_tool

    @property
    def pending_tool_change(self) -> Optional[int]:
        return self._pending_tool_change

    @property
    def pause_reason(self) -> Optional[str]:
        return self._pause_reason

    def _check_resume_inputs(self) -> None:
        if self._config.safety.feed_hold_on_door_open and self._input_values.get('DOOR', False):
            raise EcutError('Cannot resume while the door input is active')
        if self._input_values.get('FEED_HOLD', False):
            raise EcutError('Cannot resume while the feed hold input is active')

    def _check_manual_continue(self) -> None:
        self._require_state(State.PAUSED)
        self._require_motion_allowed()
        self._check_resume_inputs()
        if self._wait_for_rx(timeout=0.5) is None:
            raise EcutError('No status before manual continue')
        self._require_state(State.PAUSED)
        self._check_resume_inputs()

    def confirm_tool_change(self, tool: int) -> None:
        """Operator confirmation only; no automatic motion, offsets or spindle restart."""
        if (type(tool) is not int or self._pause_reason != 'M6'
                or self._pending_tool_change != tool):
            raise EcutError('Confirmation must match the pending M6 tool')
        self._check_manual_continue()
        self._current_tool = tool
        self._pending_tool_change = None
        self._pause_reason = None
        self._set_state(State.RUNNING)

    def resume(self) -> None:
        if self._pause_reason == 'M6':
            raise EcutError('Use confirm_tool_change() for the pending tool change')
        self._check_resume_inputs()
        if self._state != State.PAUSED or self._stop_requested:
            return
        if self._pause_reason == 'M0':
            self._check_manual_continue()
            restore = self._pause_restore
            self._pause_restore = None
            self._pause_reason = None
            generation = self._stop_generation
            self._set_state(State.RUNNING)
            if not self._running or self._state != State.RUNNING or generation != self._stop_generation:
                return
            if restore is not None:
                direction, rpm, coolant = restore
                try:
                    if direction is not None:
                        self._apply_spindle(rpm, direction)
                    for name, active in coolant.items():
                        if not self._running or self._state != State.RUNNING or generation != self._stop_generation:
                            return
                        if active:
                            self.set_output(name, True)
                except Exception:
                    if self._state != State.ESTOP:
                        self._set_state(State.ERROR)
                    self._initialized = False
                    self._shutdown(Cmd.STOP)
                    raise
        else:
            self._set_state(State.RUNNING)

    def _wait_while_paused(self) -> None:
        """Poll until explicit continue, stop or fault; manual waits require live status."""
        while self._state == State.PAUSED and self._running:
            if self._stop_requested:
                # braking is over: finish the stop when the board is stationary, else at the deadline
                if self.is_stationary(self.poll()):
                    self._finish_requested_stop()
                    return
                if time.monotonic() >= self._stop_deadline:
                    self._abort_requested_stop()
                    return
            elif self._pause_reason is not None:
                if self._wait_for_rx(timeout=0.5) is None:
                    raise EcutError('No status during manual pause')
            else:
                self.poll()
            time.sleep(0.01)

    def _manual_pause(self, action: _ManualPauseAction) -> None:
        self._require_motion_allowed()
        self._wait_for_motion(timeout=2.0)
        protected = {self._config.outputs[name].pin
                     for name in ('SPINDLE_CW', 'SPINDLE_CCW', 'FLOOD', 'MIST')
                     if self._config.outputs[name].enabled}
        aliases = [name for name, sig in self._config.outputs.items()
                   if sig.enabled and sig.pin in protected and not name.startswith(('STEP_', 'DIR_'))]
        restore = None
        if action.tool is None and self._config.safety.restore_outputs_after_m0:
            restore = (self._spindle_direction, self._spindle_rpm,
                       {name: self._logical_outputs.get(name, False) for name in aliases
                        if name not in ('SPINDLE_CW', 'SPINDLE_CCW')})
        if self._spindle_pwm_owned:
            self._send_spindle_pwm(0)
        for name in aliases:
            self.set_output(name, False)
        if self._state in (State.ESTOP, State.ERROR) or not self._running:
            raise EcutError('Manual pause interrupted before operator confirmation')
        self._spindle_direction = None
        self._spindle_rpm = 0.0
        self._pause_restore = restore
        self._pending_tool_change = action.tool
        self._pause_reason = 'M6' if action.tool is not None else 'M0'
        if action.tool is not None:
            self._current_tool = None
        # Fields and outputs are ready before notifying the existing state callback.
        self._set_state(State.PAUSED)
        self._wait_while_paused()

    def _dwell(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        paused_before = self._paused_seconds
        while self._running and not self._stop_requested:
            self._wait_while_paused()
            if not self._running:
                return
            if self._state in (State.ESTOP, State.ERROR):
                raise EcutError('Dwell interrupted by stop/fault')
            # time spent paused does not count into the dwell
            remaining = deadline + (self._paused_seconds - paused_before) - time.monotonic()
            if remaining <= 0:
                return
            if self._wait_for_rx(timeout=0.5) is None:
                raise EcutError('No status during dwell')
            if self._state in (State.ESTOP, State.ERROR):
                raise EcutError('Dwell interrupted by stop/fault')
            time.sleep(min(.001, remaining))

    # ------------------------------------------------------------------
    # Homing API
    # ------------------------------------------------------------------

    def home_all(self) -> None:
        axes = [i for i, h in enumerate(self._config.homing) if h.enabled]
        self.home_axes(axes)

    def home_axis(self, axis: int) -> None:
        self.home_axes([axis])

    def home_axes(self, axes: list[int]) -> None:
        """Home X/Y/Z/A using input edges, bounded firmware moves and explicit backoff."""
        if not axes:
            raise EcutError('No homing axes configured')
        if any(not 0 <= axis < 6 for axis in axes):
            raise ValueError('Invalid homing axis')
        self._require_sensor_cycle_capture('homing')
        self._require_state(State.IDLE)
        self._require_motion_allowed()
        for axis in axes:
            if not 0 <= axis < 6:
                raise ValueError(f'Invalid axis {axis}')
            if axis >= 4:
                raise EcutError('B/C homing completion is not verified in this plugin')
            h = self._config.homing[axis]
            sig = self._config.inputs['XYZABC'[axis] + '_HOME']
            if not h.enabled or not self._config.axes[axis].enabled:
                raise EcutError('Homing requires an enabled axis and homing configuration')
            if self._config.homing_mode != 0 and (not sig.enabled or sig.port != 1):
                raise EcutError('Homing requires a configured physical home input')
            if h.use_index_pulse or self._config.homing_mode == 4:
                raise EcutError('Index homing completion is not verified')
            if self._config.homing_mode not in (0, 1, 2, 3):
                raise EcutError('Invalid homing mode')
            if any(not math.isfinite(v) or v <= 0 for v in
                   (h.max_travel_mm, h.retract_mm, h.fast_velocity_mm_s, h.slow_velocity_mm_s)):
                raise EcutError('Homing requires positive finite distances and speeds')
        self._running = True
        self._set_state(State.RUNNING)
        try:
            for axis in sorted(set(axes), key=lambda i: int(self._config.homing[i].sequence)):
                self._homing_axis = axis
                self._homed_axes.discard(axis)
                self._clear_backlash([axis])   # the board counts are set new
                h = self._config.homing[axis]
                name = 'XYZABC'[axis] + '_HOME'
                passes = self._config.homing_count if self._config.homing_mode == 3 else 1
                if type(passes) is not int or not 1 <= passes <= 100:
                    raise EcutError('Homing count must be 1..100')
                for attempt in range(passes if self._config.homing_mode else 0):
                    speed = h.fast_velocity_mm_s
                    if self._config.homing_mode == 3:
                        ratio = self._config.homing_ratio_pct / 100.0
                        if not 0 < ratio <= 1:
                            raise EcutError('Repeated homing ratio must be 1..100 percent')
                        speed = max(h.slow_velocity_mm_s, speed * ratio ** attempt)
                    start = self._measured_counts()
                    target = list(start)
                    scale = self._axis_scale(axis)
                    target[axis] += round(h.max_travel_mm * int(h.direction) * scale)
                    self._native_move(Cmd.HOME, target, axis, speed, 0x5555)
                    self._wait_switch(name, True, target, self._sensor_timeout(axis, h.max_travel_mm, speed))
                    self._stop_native_axis(axis)
                    if self._config.homing_mode in (2, 3):
                        target = self._measured_counts()
                        target[axis] -= round(h.retract_mm * int(h.direction) * scale)
                        self._native_move(Cmd.JOG_ON, target, axis, speed, 0x5555)
                        self._wait_switch(name, False, target, self._sensor_timeout(axis, h.retract_mm, speed))
                        self._stop_native_axis(axis)
                target = self._measured_counts()
                target[axis] = round(h.home_offset_mm * self._axis_scale(axis))
                self._set_board_counts(target, 1 << axis)
                self._clear_backlash([axis])
                self._homed_axes.add(axis)
        except Exception as error:
            self._sensor_error()
            if self._state != State.ESTOP:
                # a limit or E-stop input keeps its own alarm code; a failed cycle is ALARM:9
                self._fault_alarm = 9
                self._fault_reason = 'Homing failed: %s' % error
            raise
        finally:
            self._homing_axis = None
            self._running = False
            if self._state == State.RUNNING:
                self._set_state(State.IDLE)

    @property
    def homed_axes(self) -> tuple[int, ...]:
        return tuple(sorted(self._homed_axes))

    # ------------------------------------------------------------------
    # Probing API
    # ------------------------------------------------------------------

    def probe(self, axis: int = 2, distance: Optional[float] = None) -> Optional[tuple[float, ...]]:
        """Two-pass probe using AE/retract/AC; results are stationary controller reports.

        No separate firmware hit latch has been established. last_probe_result
        labels the position source so a caller cannot mistake it for such a latch.
        """
        return self._probe_sensor(axis, distance, None)

    def _probe_sensor(self, axis: int, distance: Optional[float], speed: Optional[float]):
        self._last_probe_result = None
        if not 0 <= axis < 6:
            raise ValueError(f'Invalid axis {axis}')
        if axis >= 3:
            raise EcutError('Probing outside XYZ is not verified')
        self._require_sensor_cycle_capture('probing')
        program_running = self._running and self._state == State.RUNNING
        if not program_running:
            self._require_state(State.IDLE)
        self._require_motion_allowed()
        cfg = self._config.probe
        sig = self._config.inputs['PROBE']
        if not sig.enabled or sig.port != 1:
            raise EcutError('Probing requires a configured physical probe input')
        if cfg.mode != ProbeMode.TOWARD_WORKPIECE:
            raise EcutError('Probe-away mode is not verified')
        distance = -cfg.max_travel_mm if distance is None else distance
        if not math.isfinite(distance) or not 0 < abs(distance) <= cfg.max_travel_mm:
            raise EcutError('Probe distance must be nonzero and within maximum travel')
        if any(not math.isfinite(v) or v <= 0 for v in
               (cfg.fast_velocity_mm_s, cfg.slow_velocity_mm_s, cfg.retract_mm, cfg.retract_velocity_mm_s)):
            raise EcutError('Probe speeds and retract distance must be positive and finite')
        self._require_probe_inactive()
        self._last_probe_result = None
        completed = False
        origin = self._measured_counts()
        target = list(origin)
        delta = round(distance * self._axis_scale(axis))
        if delta == 0:
            raise EcutError('Probe distance rounds to zero controller counts')
        target[axis] += delta
        self._require_motion_allowed()
        self._running = True
        self._set_state(State.RUNNING)
        try:
            hit = None
            seek_speed = cfg.fast_velocity_mm_s if speed is None else speed
            sign = 1 if delta > 0 else -1
            for speed in (seek_speed, min(seek_speed, cfg.slow_velocity_mm_s)):
                # a native move reverses the axis like a planned one, but takes up the lash by itself
                self._travel(axis, sign)
                hit_offset = self._backlash_offset[axis]
                self._native_move(Cmd.PROBE, target, axis, speed, 0)
                self._wait_switch('PROBE', True, target, self._sensor_timeout(axis, cfg.max_travel_mm, speed))
                self._stop_native_axis(axis)
                hit = tuple(self._acc.steps)
                retract = list(hit)
                retract[axis] -= sign * round(cfg.retract_mm * abs(self._axis_scale(axis)))
                self._travel(axis, -sign)
                self._native_move(Cmd.PROBE, retract, axis, cfg.retract_velocity_mm_s, 0)
                self._wait_switch('PROBE', False, retract, self._sensor_timeout(axis, cfg.retract_mm, cfg.retract_velocity_mm_s))
                self._usb.cmd(Cmd.PROBE_FINISH)
                self._wait_native_target(retract, self._sensor_timeout(axis, cfg.retract_mm, cfg.retract_velocity_mm_s))
            offsets = list(self._backlash_offset)
            offsets[axis] = hit_offset
            point = tuple((hit[i] - offsets[i]) / self._axis_scale(i) for i in range(6))
            self._last_probe_result = {'position_mm': point, 'raw_counts': hit,
                                       'position_source': 'stationary_controller_report',
                                       'firmware_latched': False}
            completed = True
            return point
        except Exception as error:
            self._last_probe_result = None
            self._lose_backlash([axis])
            self._sensor_error()
            if self._state != State.ESTOP:
                # a limit or E-stop input keeps its own alarm code; a failed probe is ALARM:5
                self._fault_alarm = 5
                self._fault_reason = 'Probe failed: %s' % error
            raise
        finally:
            self._running = program_running and completed
            if self._state == State.RUNNING and not self._running:
                self._set_state(State.IDLE)

    @property
    def last_probe_result(self) -> Optional[dict]:
        return dict(self._last_probe_result) if self._last_probe_result is not None else None

    def probe_grid(self, points: list[tuple[float, float]], safe_z_mm: float) -> list[tuple[float, float, float]]:
        """Collect XYZ samples for a height map; abort immediately if any probe fails.

        Samples have the same stationary-report accuracy limitation as probe().
        This method does not change FlatCAM's UI or its height-map format.
        """
        if not math.isfinite(safe_z_mm) or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
            raise EcutError('Invalid probe grid coordinates')
        if not points:
            return []
        self._require_sensor_cycle_capture('probing')
        self._require_state(State.IDLE)
        self._require_motion_allowed()
        sig = self._config.inputs['PROBE']
        if not sig.enabled or sig.port != 1 or self._config.probe.mode != ProbeMode.TOWARD_WORKPIECE:
            raise EcutError('Probe grid requires a configured physical toward-workpiece probe')
        self._require_probe_inactive()
        samples = []
        for x, y in points:
            self.run_gcode([f'G21 G90 G0 Z{safe_z_mm:.12g}', f'G0 X{x:.12g} Y{y:.12g}'])
            point = self.probe()
            samples.append((x, y, point[2]))
        self.run_gcode([f'G21 G90 G0 Z{safe_z_mm:.12g}'])
        return samples

    def _raw_input_active(self, name: str, frame: Optional[EcutRxFrame] = None) -> Optional[bool]:
        sig = self._config.inputs[name]
        frame = self._last_rx if frame is None else frame
        if not sig.enabled or sig.port != 1 or frame is None:
            return None
        bank = frame.inputs_0_15 if sig.pin < 16 else self._expansion_inputs
        if bank is None:
            return None
        return bool(bank & (1 << (sig.pin % 16))) == (sig.polarity == SignalPolarity.ACTIVE_HIGH)

    def _require_probe_inactive(self) -> None:
        inputs = self.read_inputs()
        if inputs.get('PROBE', True) or self._raw_input_active('PROBE') is not False:
            raise EcutError('Probe input is already active or unavailable')

    def _travel(self, axis: int, sign: int) -> None:
        """Note a move of ``axis`` in direction ``sign`` (+1/-1) that is not planned (probe)."""
        state = backlash_reversal(self._config, self._backlash_state(), axis, sign)
        self._backlash_dir[axis] = state.dirs[axis]
        self._backlash_offset[axis] = state.offsets[axis]

    def _axis_scale(self, axis: int) -> float:
        ax = self._config.axes[axis]
        return ax.steps_per_mm * self._config.interpolation_factor * int(ax.direction)

    def _measured_counts(self) -> list[int]:
        frame = self._wait_for_rx(0.5)
        if frame is None:
            raise EcutError('No measured position status')
        return list(frame.axis_positions)

    def _sensor_timeout(self, axis: int, distance: float, speed: float) -> float:
        return distance / min(speed, self._config.axes[axis].max_velocity_mm_s) + 2.0

    def _native_move(self, opcode: int, targets: list[int], axis: int, speed: float, flags: int) -> None:
        self._require_motion_allowed()
        self._wait_while_paused()
        if not self._running or self._state != State.RUNNING:
            raise EcutError('Sensor operation stopped before native move')
        if not self._config.axes[axis].enabled:
            raise EcutError('Cannot move a disabled axis')
        ax = self._config.axes[axis]
        logical = targets[axis] / self._axis_scale(axis)
        # Homing is a hardware cycle: it finds the switches and never uses the soft limits.
        enforce_soft = self._homing_axis is None
        if enforce_soft and ax.soft_limits_enabled and not ax.soft_limit_negative_mm <= logical <= ax.soft_limit_positive_mm:
            raise EcutError('Soft limit exceeded by sensor move')
        if any(not -0x80000000 <= value <= 0x7fffffff for value in targets):
            raise EcutError('Sensor move exceeds signed position range')
        speeds = [min(speed, ax.max_velocity_mm_s) if i == axis else 0.0
                  for i, ax in enumerate(self._config.axes)]
        # Native moves need B5 even when every soft limit is disabled.
        maximum, minimum = [], []
        homing_limits = self._homing_axis is None
        for i, setting in enumerate(self._config.axes):
            if homing_limits and setting.soft_limits_enabled:
                factor = setting.steps_per_mm * int(setting.direction)
                bounds = sorted((int(setting.soft_limit_negative_mm * factor),
                                 int(setting.soft_limit_positive_mm * factor)))
                minimum.append(bounds[0])
                maximum.append(bounds[1])
            else:
                minimum.append(-0x80000000)
                maximum.append(0x7fffffff)
        packet = build_soft_limits_packet(maximum, minimum)
        self._usb.cmd(packet[1], packet[2:])
        accel = [int(ax.acceleration_mm_s2 * abs(self._axis_scale(i)) * 65536 / 1000000)
                 for i, ax in enumerate(self._config.axes)]
        velocity = [int(v * abs(self._axis_scale(i)) * 65536 / 1000) for i, v in enumerate(speeds)]
        packet = build_motion_limits_packet(accel, velocity)
        self._usb.cmd(packet[1], packet[2:])
        self._drain_rx()
        self._require_motion_allowed()
        if not self._running or self._state != State.RUNNING:
            raise EcutError('Sensor operation stopped before native target')
        packet = build_target_packet(opcode, tuple(value * 65536 for value in targets),
                                     7 if opcode == Cmd.PROBE else axis, flags)
        self._native_motion_active = True
        self._usb.cmd(packet[1], packet[2:])

    def _wait_switch(self, name: str, active: bool, target: list[int], timeout: float) -> EcutRxFrame:
        end = time.monotonic() + timeout + self._config.inputs[name].debounce_ms / 1000
        while time.monotonic() < end:
            self._wait_while_paused()
            frame = self.poll()
            if self._state in (State.ESTOP, State.ERROR) or not self._running:
                raise EcutError('Sensor operation stopped')
            if frame and self._input_values.get(name) is active:
                return frame
            if (frame and list(frame.axis_positions) == target
                    and self._raw_input_active(name, frame) is not active):
                raise EcutError(f'No contact/release: {name} input state {active} not reached')
            time.sleep(0.001)
        raise EcutError(f'timeout waiting for {name} input state {active}')

    def _wait_native_target(self, target: list[int], timeout: float) -> None:
        self._acc.steps = list(target)
        self._wait_for_motion(timeout)
        if not self._running or self._state != State.RUNNING:
            raise EcutError('Sensor operation stopped before target completion')
        self._native_motion_active = False

    def _stop_native_axis(self, axis: int) -> None:
        self._usb.cmd(Cmd.JOG_OFF, bytes(32) + struct.pack('>H', axis))
        frame = self._wait_idle()
        self._native_motion_active = False
        self._acc.steps = list(frame.axis_positions)

    def _set_board_counts(self, target: list[int], mask: int) -> None:
        self._require_motion_allowed()
        if not self._running or self._state != State.RUNNING:
            raise EcutError('Homing stopped before coordinate assignment')
        params = struct.pack('>I6iIH', 0, *target, 0, mask)
        self._usb.cmd(Cmd.SET_POSITION, params)
        self._drain_rx()
        self._acc.steps = list(target)
        self._wait_for_motion(1.0)
        if not self._running or self._state != State.RUNNING:
            raise EcutError('Sensor operation stopped before coordinate confirmation')

    def _sensor_error(self) -> None:
        self._last_probe_result = None
        self._native_motion_active = False
        self._running = False
        self._initialized = False
        if self._state != State.ESTOP:
            self._set_state(State.ERROR)
        try:
            self.stop()
        except EcutError:
            log.exception('Failed to stop sensor operation')

    # ------------------------------------------------------------------
    # Jogging API
    # ------------------------------------------------------------------

    # Native bounded target and stop layouts are known; the continuous jog lifecycle is not implemented.
    # jog_step() uses the verified motion records instead.

    @property
    def jogging(self) -> bool:
        return self._jog_active

    def jog_start(self, axis: int, direction: int = 0, feed_mm_min: Optional[float] = None,
                  *, should_continue: Optional[Callable[[], bool]] = None) -> None:
        """Stream on the owning worker; release requests a planned braking tail.

        Direction 0 is positive, 1 negative. The caller must service release or
        provide a live dead-man callback. USB access stays on this calling thread.
        """
        self._require_state(State.IDLE)
        self._require_motion_allowed()
        if self._jog_active:
            raise EcutError('A continuous jog is already active')
        self._jog_cancel.clear()

        def held():
            keep = should_continue is None or should_continue()
            return keep and not self._jog_cancel.is_set() and self._state == State.RUNNING

        # the take-up of a reversal is part of the jog: it ends at the release too
        self._takeup_held = lambda: (should_continue is None or should_continue()) and not self._jog_cancel.is_set()
        self._takeup_cut = False
        try:
            self._compensate_jog(axis, direction)
        finally:
            self._takeup_held = None
        if self._takeup_cut:
            self._lose_backlash([axis])   # the take-up did not reach its end: the offset is not known
            return
        # the soft bound of the jog runs on the logical position
        start = tuple(steps - offset for steps, offset in zip(self._acc.steps, self._backlash_offset))
        segments = iter_jog_segments(self._config, start, axis, direction, feed_mm_min, held)
        # Validation and first release check happen before changing state or USB.
        self._set_state(State.RUNNING)
        try:
            first = next(segments, None)
        except Exception:
            if self._state in (State.RUNNING, State.PAUSED):
                self._set_state(State.IDLE)
            raise
        if first is None:
            if self._state in (State.RUNNING, State.PAUSED):
                self._set_state(State.IDLE)
            return
        self._travel(axis, (1 if direction == 0 else -1) * (1 if self._axis_scale(axis) > 0 else -1))
        self._running = self._jog_active = True
        self._traj_id = (self._traj_id + 1) & 0xffffff
        submitted = 0
        started = last_send = last_status = time.monotonic()
        try:
            self._wait_for_motion(timeout=.5)
            self._require_motion_allowed()
            if not self._running or not held():
                return
            started = last_send = last_status = time.monotonic()
            progress_at = None
            measured = tuple(self._acc.steps)

            def check_status(frame):
                nonlocal last_status, progress_at, measured
                now = time.monotonic()
                if frame is not None:
                    last_status = now
                    if not frame.configured or not 7000 < frame.fifo_free <= 12000:
                        raise EcutError('Invalid or backed-up FIFO status during continuous jog')
                    if frame.axis_positions == tuple(self._acc.steps):
                        progress_at = None
                    elif frame.axis_positions != measured or progress_at is None:
                        progress_at = now
                    # Allow captured firmware prefetch plus the bounded RX interval.
                    elif now - progress_at > .25:
                        raise EcutError('No measured position progress during continuous jog')
                    measured = frame.axis_positions
                if now - last_status > .1:
                    raise EcutError('No fresh status during continuous jog')

            pending = first
            while pending is not None and self._running:
                # At most 24 ms submitted ahead of the host clock; never fill the FIFO.
                while submitted * .001 > time.monotonic() - started + .020:
                    if self._planning_checkpoint is not None:
                        self._planning_checkpoint()
                    check_status(self.poll())
                    self._require_motion_allowed()
                    if not self._running:
                        return
                    time.sleep(.001)
                if self._planning_checkpoint is not None:
                    self._planning_checkpoint()
                self._require_motion_allowed()
                if not self._running:
                    return
                if submitted and time.monotonic() - last_send > .024:
                    raise EcutError('Continuous jog stream underrun; reset required')
                chunk = [pending]
                for _ in range(MAX_MOTION_RECORDS_PER_PACKET - 1):
                    value = next(segments, None)
                    if value is None:
                        break
                    chunk.append(value)
                self._require_motion_allowed()
                if not self._running:
                    return
                last_send = time.monotonic()
                self._usb.send_motion([encode_motion_record(self._traj_id, value) for value in chunk])
                for value in chunk:
                    self._acc.advance(value)
                submitted += len(chunk)
                if time.monotonic() - last_send > .024:
                    raise EcutError('Continuous jog stream underrun; reset required')
                check_status(self.poll())
                self._require_motion_allowed()
                if not self._running:
                    return
                pending = next(segments, None)
            if self._running:
                self._usb.cmd(Cmd.END_STREAM)
                self._wait_for_motion(timeout=2.0)
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            try:
                self._shutdown(Cmd.STOP)
            except EcutError:
                log.exception('Failed to stop after continuous jog error')
            raise
        finally:
            self._running = self._jog_active = False
            if self._state in (State.RUNNING, State.PAUSED):
                self._set_state(State.IDLE)

    def _compensate_jog(self, axis: int, direction: int) -> None:
        """Before a continuous jog that reverses the axis: the compensation move, then the direction is noted."""
        if type(axis) is not int or not 0 <= axis < 6 or direction not in (0, 1):
            return   # iter_jog_segments refuses it
        scale = self._axis_scale(axis)
        sign = (1 if direction == 0 else -1) * (1 if scale > 0 else -1)
        before = (tuple(self._backlash_dir), tuple(self._backlash_offset))
        after = backlash_reversal(self._config, self._backlash_state(), axis, sign)
        if after.offsets == before[1]:
            return   # no reversal: jog_start notes the direction when the jog moves
        steps = tuple(self._acc.steps)
        target = [None] * 6
        target[axis] = (steps[axis] - before[1][axis]) / scale
        try:
            segments = plan_path_segments(self._config, steps, [LinearMove(tuple(target), True, None, None, after.offsets)],
                                          feed_override=self._feed_override, checkpoint=self._planning_checkpoint,
                                          junction_deviation_mm=self._config.junction_deviation_mm,
                                          start_offsets=before[1])
            end = tuple(steps[i] + after.offsets[i] - before[1][i] for i in range(6))
            self._run_action(_PlannedPathAction(steps, end, segments, self._feed_mm_min, before,
                                                (after.dirs, after.offsets)))
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            try:
                self._shutdown(Cmd.STOP)
            except EcutError:
                log.exception('Failed to stop after the backlash move')
            raise

    def jog_stop(self) -> None:
        """Thread-safe release request; the owning worker sends the braking tail."""
        self._jog_cancel.set()

    def jog_step(self, axis: int, distance_mm: float) -> None:
        self._require_connected()
        if not (0 <= axis < 6):
            raise ValueError(f'Invalid axis {axis}')
        target: list[Optional[float]] = [None] * 6
        target[axis] = self.logical_position_mm()[axis] + distance_mm
        self._require_motion_allowed()
        try:
            self._move_linear(_MoveAction(True, True, {axis: target[axis]}, None))
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            try:
                self._shutdown(Cmd.STOP)
            except EcutError:
                log.exception('Failed to stop after jog error')
            raise

    # ------------------------------------------------------------------
    # GCode execution API
    # ------------------------------------------------------------------

    def run_gcode(self, lines: list[str]) -> None:
        """Validate the whole program first (nothing is sent if any line is unsupported), then execute it.

        Modal state starts as: units from config.units_mm (G21 by default), G90, no motion mode, no feed.
        """
        self._require_state(State.IDLE)
        modal = _Modal(units_mm=self._config.units_mm)
        actions = compile_program(lines, modal, checkpoint=self._planning_checkpoint)
        if any(isinstance(action, _ProbeAction) for _, action in actions):
            self._require_sensor_cycle_capture('probing')
            sig = self._config.inputs['PROBE']
            if not sig.enabled or sig.port != 1 or self._config.probe.mode != ProbeMode.TOWARD_WORKPIECE:
                raise EcutError('Probing program requires a configured physical toward-workpiece probe')
        direction = self._spindle_direction
        for lineno, action in actions:
            if self._planning_checkpoint is not None:
                self._planning_checkpoint()
            if isinstance(action, _ManualPauseAction) and action.tool is not None:
                direction = None
            if isinstance(action, _SpindleAction):
                if action.command in (3, 4):
                    direction = 'SPINDLE_CW' if action.command == 3 else 'SPINDLE_CCW'
                elif action.command == 5:
                    direction = None
                try:
                    self._validate_spindle(action.rpm, direction)
                except EcutError as error:
                    raise EcutError(f'line {lineno}: {error}') from error
        actions = self._plan_program_actions(actions)
        self._modal = modal
        self._queue_plan(actions)
        self._set_state(State.RUNNING)
        self._running = True
        try:
            for _lineno, action in actions:
                if self._stop_requested:
                    self._finish_requested_stop()
                self._wait_while_paused()
                if not self._running:
                    break
                self._queue_start(action)
                try:
                    self._run_action(action)
                finally:
                    self._queue_finish()
                self._poll()
        except Exception:
            self._running = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            self._initialized = False
            try:
                self._shutdown(Cmd.STOP)
            except EcutError:
                log.exception('Failed to stop after motion error')
            raise
        finally:
            # an E-stop (or error) that happened during the run is kept
            if self._state in (State.RUNNING, State.PAUSED):
                self._set_state(State.IDLE)
            self._running = False
            if self._stop_requested:  # the run ended before the stop could finish
                self._stop_requested = False
                self.stop_completed = True
            self._queue_clear()

    @staticmethod
    def _empty_queue() -> dict:
        return {'waiting': 0, 'waiting_records': 0, 'unplanned': 0, 'active': 0, 'active_records': 0}

    def queue_state(self) -> dict:
        """Host-side queue of the running run_gcode call, safe to read from any thread.

        blocks_waiting: motion blocks planned but not started. blocks_active: the block being streamed.
        records_unsent: planned 1 ms records of those blocks not yet written to the board; None while a
        block's records are not planned yet (probe-dependent moves). Records already written to the board
        are not counted: the board reports free FIFO units, not a record count.
        """
        with self._queue_lock:
            queue = dict(self._queue)
        if self._state not in (State.RUNNING, State.PAUSED):
            queue = self._empty_queue()
        known = not queue['unplanned'] and queue['active_records'] is not None
        return {'blocks_waiting': queue['waiting'], 'blocks_active': queue['active'],
                'records_unsent': queue['waiting_records'] + queue['active_records'] if known else None}

    @staticmethod
    def _is_block(action) -> bool:
        return isinstance(action, (_PlannedPathAction, _MoveAction))

    def _queue_plan(self, actions) -> None:
        queue = self._empty_queue()
        for _lineno, action in actions:
            if isinstance(action, _PlannedPathAction):
                queue['waiting'] += 1
                queue['waiting_records'] += len(action.segments)
            elif isinstance(action, _MoveAction):
                queue['waiting'] += 1
                queue['unplanned'] += 1
        with self._queue_lock:
            self._queue = queue

    def _queue_start(self, action) -> None:
        if not self._is_block(action):
            return
        planned = isinstance(action, _PlannedPathAction)
        with self._queue_lock:
            queue = self._queue
            queue['waiting'] = max(0, queue['waiting'] - 1)
            if planned:
                queue['waiting_records'] = max(0, queue['waiting_records'] - len(action.segments))
            else:
                queue['unplanned'] = max(0, queue['unplanned'] - 1)
            queue['active'] = 1
            queue['active_records'] = len(action.segments) if planned else None

    def _queue_progress(self, remaining: int) -> None:
        with self._queue_lock:
            if self._queue['active']:
                self._queue['active_records'] = remaining

    def _queue_finish(self) -> None:
        with self._queue_lock:
            self._queue['active'] = 0
            self._queue['active_records'] = 0

    def _queue_clear(self) -> None:
        with self._queue_lock:
            self._queue = self._empty_queue()

    def run_gcode_file(self, path: str | Path) -> None:
        path = Path(path)
        with open(path) as f:
            self.run_gcode(f.readlines())

    # ------------------------------------------------------------------
    # Spindle API
    # ------------------------------------------------------------------

    def _refuse_unverified(self, what: str) -> None:
        raise EcutError(f'{what} not verified: the command codes are not proven by the DLL evidence')

    def _validate_spindle(self, rpm: float, direction: Optional[str]) -> None:
        self._require_connected()
        self._config.validate()
        spindle = self._config.spindle
        if spindle.control != SpindleControl.PWM:
            raise EcutError(f'S{rpm:g}/M3/M4/M5 require verified PWM/DAC spindle mode')
        if not self._initialized or self._config.to_dict() != self._initialized_config:
            raise EcutError('Spindle requires initialization with the current configuration')
        if (not isinstance(rpm, (int, float)) or isinstance(rpm, bool) or not math.isfinite(rpm)
                or rpm < 0 or rpm > spindle.speed_rpm_max
                or (rpm > 0 and rpm < spindle.speed_rpm_min)):
            raise EcutError('Spindle RPM is outside the configured range')
        if direction is not None:
            signal = self._config.outputs.get(direction)
            if signal is None or not signal.enabled:
                raise EcutError(f'Spindle run output {direction} is not configured')

    def _send_spindle_pwm(self, duty: int) -> None:
        packet = build_spindle_pwm_packet(self._spindle_period_counts, duty)
        self._spindle_pwm_owned = True
        self._usb.cmd(packet[1], packet[2:])

    def _forget_reference_if_moving(self) -> None:
        # a stop without deceleration can lose steps: home the axes again
        if self._running or self._jog_active or (self._last_rx is not None and self._last_rx.motion_busy):
            self._homed_axes.clear()
            self._clear_backlash()

    def _shutdown(self, command: Cmd) -> None:
        """Attempt every independent stop write, retaining the initialized pin polarity."""
        self._forget_reference_if_moving()
        if self._stop_requested:  # an abrupt stop ends a pending decelerated stop
            self._stop_requested = False
            self.stop_completed = True
        self._stop_generation += 1
        self._native_motion_active = False
        self._running = False
        self._pending_tool_change = None
        self._pause_reason = None
        self._pause_restore = None
        self._spindle_direction = None
        self._spindle_rpm = 0.0
        self._logical_outputs.clear()
        if self._initialized_config is not None:
            outputs = self._initialized_config['outputs']
            self._expected_outputs = sum(1 << pin for pin in {
                sig['pin'] for name, sig in outputs.items()
                if sig['enabled'] and sig['inverted'] and not name.startswith(('STEP_', 'DIR_'))})
        else:
            self._expected_outputs = self._inactive_outputs()
        commands = [(command, b'')]
        if self._spindle_pwm_owned:
            packet = build_spindle_pwm_packet(self._spindle_period_counts, 0)
            commands.append((packet[1], packet[2:]))
        commands.append((Cmd.SET_OUTPUTS, struct.pack('>III', 0, 0xffff, self._expected_outputs)))
        failure = None
        for opcode, params in commands:
            try:
                self._usb.cmd(opcode, params)
            except Exception as error:
                if failure is None:
                    failure = error
        if failure is not None:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            raise failure

    def _check_spindle_interlocks(self) -> None:
        if self._state in (State.ESTOP, State.ERROR, State.PAUSED):
            raise EcutError('Spindle command interrupted; reset/resume first')
        if (self._input_values.get('FEED_HOLD', False)
                or (self._config.safety.feed_hold_on_door_open and self._input_values.get('DOOR', False))):
            raise EcutError('Spindle activation refused by feed hold/door input')

    def _spindle_dwell(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._wait_for_rx(timeout=0.5) is None:
                raise EcutError('No status during spindle dwell')
            self._check_spindle_interlocks()
            time.sleep(0.001)

    def _apply_spindle(self, rpm: float, direction: Optional[str]) -> None:
        self._validate_spindle(rpm, direction)
        if self._state in (State.ESTOP, State.ERROR, State.PAUSED):
            raise EcutError('Spindle activation refused until reset/resume')
        generation = self._stop_generation

        def check_active_command():
            self._check_spindle_interlocks()
            if generation != self._stop_generation:
                raise EcutError('Spindle command cancelled by stop/reset')

        previous = self._spindle_direction
        was_running = self._spindle_rpm > 0 and previous is not None
        try:
            frame = self._wait_for_rx(timeout=0.5)
            if frame is None or self._state in (State.ESTOP, State.ERROR, State.PAUSED):
                raise EcutError('No safe status before spindle command')
            check_active_command()
            if not rpm or direction is None or previous != direction:
                self._send_spindle_pwm(0)
                for name in ('SPINDLE_CW', 'SPINDLE_CCW'):
                    if self._config.outputs[name].enabled:
                        self.set_output(name, False)
                if was_running:
                    self._spindle_dwell(self._config.spindle.dwell_off_seconds)
            if rpm and direction is not None:
                check_active_command()
                duty = round(self._spindle_period_counts * rpm / self._config.spindle.speed_rpm_max)
                self._send_spindle_pwm(duty)
                self.set_output(direction, True)
                check_active_command()
                if not was_running or previous != direction:
                    self._spindle_dwell(self._config.spindle.dwell_on_seconds)
            check_active_command()
            self._spindle_direction = direction
            self._spindle_rpm = rpm
        except Exception:
            self._initialized = False
            if self._state != State.ESTOP:
                self._set_state(State.ERROR)
            try:
                self._shutdown(Cmd.STOP)
            except EcutError:
                log.exception('Failed to clear spindle after command error')
            raise

    def spindle_on_cw(self, rpm: float = 0.0) -> None:
        self._apply_spindle(rpm, 'SPINDLE_CW')

    def spindle_on_ccw(self, rpm: float = 0.0) -> None:
        self._apply_spindle(rpm, 'SPINDLE_CCW')

    def spindle_off(self) -> None:
        self._apply_spindle(0.0, None)

    def spindle_speed(self, rpm: float) -> None:
        self._validate_spindle(rpm, self._spindle_direction)
        if self._spindle_direction is None:
            self._spindle_rpm = rpm
        else:
            self._apply_spindle(rpm, self._spindle_direction)

    # ------------------------------------------------------------------
    # Coolant API
    # ------------------------------------------------------------------

    def flood_on(self) -> None:
        self.set_output('FLOOD', True)

    def flood_off(self) -> None:
        self.set_output('FLOOD', False)

    def mist_on(self) -> None:
        self.set_output('MIST', True)

    def mist_off(self) -> None:
        self.set_output('MIST', False)

    # ------------------------------------------------------------------
    # DRO / Position API
    # ------------------------------------------------------------------

    @property
    def position_mm(self) -> list[float]:
        """The logical position (the tool): the board counts without the backlash offset."""
        return self.logical_position_mm()

    @property
    def position_steps(self) -> list[int]:
        return list(self._acc.steps)

    def set_position(self, axis: int, value_mm: float) -> None:
        if not (0 <= axis < 6):
            raise ValueError(f'Invalid axis {axis}')
        ax = self._config.axes[axis]
        if self.connected:
            raise EcutError('Local coordinate changes while connected are refused; preserve measured counts')
        self._acc.steps[axis] = int(round(value_mm * ax.steps_per_mm * self._config.interpolation_factor
                                         * int(ax.direction)))

    def zero_all(self) -> None:
        if self.connected:
            raise EcutError('Local coordinate changes while connected are refused; preserve measured counts')
        self._acc.reset()

    # The GET_DRO/SET_DRO parameter layout and the reply field are UNVERIFIED guesses: both refuse.

    def get_dro(self, index: int) -> float:
        self._refuse_unverified('DRO access')

    def set_dro(self, index: int, value: float) -> None:
        self._refuse_unverified('DRO access')

    # ------------------------------------------------------------------
    # I/O API
    # ------------------------------------------------------------------

    def read_inputs(self) -> dict[str, bool]:
        self._require_connected()
        end = time.monotonic() + 0.5
        expected = {name for name, sig in self._config.inputs.items() if sig.enabled and sig.port == 1}
        while time.monotonic() < end:
            self.poll()
            if expected <= self._input_values.keys():
                return dict(self._input_values)
            time.sleep(0.001)
        return dict(self._input_values)

    def _inactive_outputs(self) -> int:
        return sum(1 << pin for pin in {sig.pin for name, sig in self._config.outputs.items()
                   if sig.enabled and sig.inverted and not name.startswith(('STEP_', 'DIR_'))})

    def set_output(self, name: str, on: bool) -> None:
        self._require_connected()
        sig = self._config.outputs.get(name)
        if sig is None or not sig.enabled or name.startswith(('STEP_', 'DIR_')):
            raise EcutError(f'Output {name!r} is not configured for digital control')
        self._config.validate()
        if not self._initialized or self._config.to_dict() != self._initialized_config:
            raise EcutError('Output requires initialization with the current configuration')
        if on and self._state in (State.ESTOP, State.ERROR):
            raise EcutError('Output activation refused until reset')
        self._logical_outputs[name] = bool(on)
        active = any(self._logical_outputs.get(other, False) for other, alias in self._config.outputs.items()
                     if alias.enabled and alias.pin == sig.pin and not other.startswith(('STEP_', 'DIR_')))
        mask = 1 << sig.pin
        raw = bool(active) != bool(sig.inverted)
        expected = (self._expected_outputs & ~mask) | (mask if raw else 0)
        # This firmware replaces the entire bitmap even with a partial write mask.
        self._usb.cmd(Cmd.SET_OUTPUTS, struct.pack('>III', 0, 0xffff, expected))
        self._expected_outputs = expected
        self._drain_rx()
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            frame = self.poll()
            if on and self._state in (State.ESTOP, State.ERROR):
                raise EcutError('Output activation interrupted by interlock')
            if frame and frame.outputs == expected:
                return
            time.sleep(0.001)
        if self._state != State.ESTOP:
            self._set_state(State.ERROR)
        self._initialized = False
        try:
            self.stop()
        except EcutError:
            log.exception('Failed to clear outputs after readback error')
        raise EcutError('timeout verifying digital output bitmap')

    def read_output(self, name: str) -> bool:
        sig = self._config.outputs.get(name)
        if sig is None or not sig.enabled or name.startswith(('STEP_', 'DIR_')):
            raise EcutError(f'Output {name!r} is not configured for digital control')
        self._require_connected()
        frame = self._wait_for_rx(timeout=0.5)
        if frame is None:
            raise EcutError('No output status received')
        return bool(frame.outputs & (1 << sig.pin)) != bool(sig.inverted)

    def _update_inputs(self, frame: EcutRxFrame) -> None:
        if frame.subtype == 4:
            self._expansion_inputs = frame.subtype_values[0]
        if frame.subtype == 1:
            self._telemetry['counters'] = tuple(frame.subtype_values)
        elif frame.subtype == 3:
            self._telemetry['spindle_period'] = frame.subtype_values[1]
        elif frame.subtype == 5:
            self._telemetry['identity'] = tuple(frame.subtype_values)
        now = time.monotonic()
        for name, sig in self._config.inputs.items():
            if not sig.enabled or sig.port != 1:
                continue
            if not 0 <= sig.pin < 32:
                raise EcutError('Input pin outside 0..31')
            bank = frame.inputs_0_15 if sig.pin < 16 else self._expansion_inputs
            if bank is None:
                continue
            active = bool(bank & (1 << (sig.pin % 16))) == (sig.polarity == SignalPolarity.ACTIVE_HIGH)
            pending, since = self._input_pending.get(name, (active, now))
            if active != pending:
                pending, since = active, now
            self._input_pending[name] = (pending, since)
            # Fault activation is immediate; release and ordinary changes debounce.
            immediate_fault = active and (name == 'ESTOP' or '_LIMIT_' in name)
            if sig.debounce_ms == 0 or immediate_fault or now - since >= sig.debounce_ms / 1000:
                self._input_values[name] = active

    @property
    def telemetry(self) -> dict:
        return dict(self._telemetry)

    # ------------------------------------------------------------------
    # Feed override API
    # ------------------------------------------------------------------

    @property
    def feed_override(self) -> float:
        return self._feed_override

    @feed_override.setter
    def feed_override(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError('Feed override must be finite')
        safety = self._config.safety
        self._feed_override = max(safety.min_feedrate_override_pct / 100.0,
                                  min(safety.max_feedrate_override_pct / 100.0, value))

    @property
    def rate_limit(self) -> float:
        return self._rate_limit

    @rate_limit.setter
    def rate_limit(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError('Rate limit must be finite')
        self._rate_limit = max(0.1, min(1.0, value))

    # ------------------------------------------------------------------
    # Status / diagnostics API
    # ------------------------------------------------------------------

    @property
    def last_rx_frame(self) -> Optional[EcutRxFrame]:
        return self._last_rx

    @property
    def estop_active(self) -> bool:
        return self._state == State.ESTOP or (
            self._last_rx is not None and self._last_rx.estop_active
        )

    @property
    def limits_triggered(self) -> list[str]:
        return [name for name, active in self._input_values.items() if active and '_LIMIT_' in name]

    def poll(self) -> Optional[EcutRxFrame]:
        if not self._usb.is_open:
            return None
        try:
            frame = self._usb.poll()
            if frame:
                if not frame.crc_valid:
                    raise EcutUsbError('Invalid RX CRC')
                if not frame.configured and self._initialized:
                    self._homed_axes.clear()
                    self._clear_backlash()
                self._last_rx = frame
                self._update_inputs(frame)
                self._check_interlocks(frame)
                if frame.estop_active and self._state != State.ESTOP:
                    self.estop()
                if self._on_rx:
                    self._on_rx(frame)
            return frame
        except Exception:
            if self._usb.is_open:
                self._initialized = False
                if self._state != State.ESTOP:
                    self._set_state(State.ERROR)
                try:
                    self._shutdown(Cmd.STOP)
                except EcutError:
                    log.exception('Failed to clear spindle after status error')
            raise

    def _check_interlocks(self, frame: EcutRxFrame) -> None:
        if self._state in (State.DISCONNECTED, State.ESTOP, State.ERROR):
            return
        safety = self._config.safety
        emergency = self._input_values.get('ESTOP', False)
        if safety.estop_input_enabled:
            pin = safety.estop_input_pin
            bank = frame.inputs_0_15 if pin < 16 else self._expansion_inputs
            if bank is not None:
                emergency |= bool(bank & (1 << (pin % 16))) == (safety.estop_polarity == SignalPolarity.ACTIVE_HIGH)
        limits = self.limits_triggered
        if self._homing_axis is not None:
            homing = self._config.homing[self._homing_axis]
            home = self._config.inputs['XYZABC'[self._homing_axis] + '_HOME']
            if not homing.hw_limit_when_homing:
                limits = [name for name in limits if self._config.inputs[name].pin != home.pin]
        if emergency or limits:
            self._fault_reason = 'Emergency input' if emergency else 'Limit input: ' + ', '.join(limits)
            self._fault_alarm = 10 if emergency else 1
            self.estop()
            return
        if safety.feed_hold_on_door_open and self._input_values.get('DOOR', False):
            self.pause()
        if self._input_values.get('FEED_HOLD', False):
            self.pause()

    @property
    def fault_reason(self) -> Optional[str]:
        return self._fault_reason

    @property
    def fault_alarm(self) -> int:
        """Alarm code of the last fault: 10 = E-stop input, 1 = limit input or a cause that is not tracked."""
        return self._fault_alarm

    @property
    def active_pins(self) -> str:
        """Letters of the active inputs for the status report: X Y Z A limits, P probe, E E-stop."""
        pins = {name[0] for name in self.limits_triggered if name[0] in 'XYZA'}
        if self._input_values.get('PROBE', False):
            pins.add('P')
        if self._input_values.get('ESTOP', False) or (self._state == State.ESTOP and self._fault_alarm == 10):
            pins.add('E')
        return ''.join(sorted(pins))

    def config_summary(self) -> str:
        return self._config.summary()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_state(self, s: State) -> None:
        if s != self._state:
            old = self._state
            if old == State.PAUSED and self._pause_started is not None:
                self._paused_seconds += time.monotonic() - self._pause_started
                self._pause_started = None
            if s == State.PAUSED:
                self._pause_started = time.monotonic()
            self._state = s
            log.info('State: %s -> %s', old.name, s.name)
            if self._on_state_change:
                self._on_state_change(s)

    def _require_connected(self) -> None:
        if not self._usb.is_open:
            raise EcutError('Not connected')

    def _require_state(self, expected: State) -> None:
        if self._state != expected:
            raise EcutError(f'Expected state {expected.name}, got {self._state.name}')

    def _require_sensor_cycle_capture(self, feature: str) -> None:
        # The cycles are not verified on hardware: the owner turns each one on in E-Cut hardware > Not verified.
        if not self._config.unverified.to_dict()[feature]:
            raise EcutError(f'{_UNVERIFIED_NAMES[feature]} is not verified on hardware. '
                            'Turn it on in E-Cut hardware > Not verified.')

    def _require_motion_allowed(self) -> None:
        if self._state in (State.ESTOP, State.ERROR):
            raise EcutError(f'Motion refused in state {self._state.name}, reset() first')
        self._require_connected()
        if self._config.motion_period_s != 0.001:
            raise EcutError('motion timing is not configured/verified: require 0.001 s records')
        if not self._initialized:
            raise EcutError('Motion requires initialization via push_config()')
        if self._initialized_config is not None and self._config.to_dict() != self._initialized_config:
            raise EcutError('Configuration changed; initialize again before motion')
        self.encode_config()

    def _submit_segments(self, segments: list[list[int]]) -> None:
        """Send segment deltas as motion records, at most MAX_MOTION_RECORDS_PER_PACKET per packet.

        A feed hold brakes along the same path at the configured deceleration and then waits;
        resume accelerates again. The records already in the board FIFO run before the braking starts.
        """
        per_packet = max(1, MAX_MOTION_RECORDS_PER_PACKET)
        in_run = self._running
        self._traj_id = (self._traj_id + 1) & 0xFFFFFF
        records = _HoldableRecords(segments, self._config, lambda: self._state == State.PAUSED,
                                    lambda: self._rate_limit)
        packet = 0
        sent = 0
        first_record_time = None
        paused_seconds = 0.0
        self._streaming = in_run  # request_stop() brakes only while records are streamed
        try:
            while True:
                # Native FUN_100082f0 gates up to two four-record reports on >7000 free.
                if packet % 2 == 0 and not self._wait_for_stream_space(in_run, records):
                    return
                if records.stopped:
                    paused_from = time.monotonic()
                    self._wait_while_paused()
                    paused_seconds += time.monotonic() - paused_from
                self._require_motion_allowed()
                if in_run and not self._running:
                    return  # stop() during the run: do not send the rest of this move
                if self._takeup_held is not None and not self._takeup_held():
                    self._takeup_cut = True   # jog release during the backlash take-up
                    break
                chunk = records.take(per_packet)
                if not chunk:
                    if records.done:
                        break
                    continue  # held: nothing to send until resume
                if first_record_time is None:
                    first_record_time = time.monotonic()
                self._usb.send_motion([encode_motion_record(self._traj_id, tuple(deltas)) for deltas in chunk])
                sent += len(chunk)
                for deltas in chunk:
                    self._acc.advance(deltas)
                self._queue_progress(records.remaining)
                packet += 1
                self.poll()
            self._require_motion_allowed()
            if in_run and not self._running:
                return
            self._usb.cmd(Cmd.END_STREAM)
            # the board runs 1 record per ms; the records of this stream may still be in its FIFO
            pending_seconds = 0.0
            if first_record_time is not None:
                pending_seconds = max(0.0, sent * 0.001 - (time.monotonic() - first_record_time) + paused_seconds)
            self._wait_for_motion(timeout=2.0 + pending_seconds)
        finally:
            self._streaming = False

    def _poll_latest(self) -> Optional[EcutRxFrame]:
        """Read all queued status reports; the newest one tells the real FIFO space."""
        latest = self.poll()
        if latest is None or not isinstance(self._usb, EcutUsb):
            return latest
        for _ in range(64):
            frame = self.poll()
            if frame is None:
                break
            latest = frame
        return latest

    def _wait_for_stream_space(self, in_run: bool, records=None) -> bool:
        start = time.monotonic()
        paused = self._paused_seconds
        while time.monotonic() - start - (self._paused_seconds - paused) < 0.5:
            if records is None or records.stopped:
                self._wait_while_paused()
            self._require_motion_allowed()
            if in_run and not self._running:
                return False
            frame = self._poll_latest()
            if in_run and not self._running:
                return False
            if frame is not None:
                if not frame.configured or not 0 <= frame.fifo_free <= 12000:
                    raise EcutError('Invalid FIFO status while streaming motion')
                if frame.fifo_free > 7000:
                    return True
            time.sleep(0.001)
        raise EcutError('Timeout waiting for fresh FIFO status while streaming motion')

    def _exec_line(self, line: str) -> None:
        """Validate and execute one line against the current modal state (comments and blank lines are skipped)."""
        modal, actions = _compile_line(line, 1, self._modal)
        self._modal = modal
        for action in actions:
            self._run_action(action)

    def _run_action(self, action) -> None:
        if action == 'stop':
            self.stop()
        elif isinstance(action, _ToolSelectAction):
            self._selected_tool = action.tool
        elif isinstance(action, _ManualPauseAction):
            self._manual_pause(action)
        elif isinstance(action, _DwellAction):
            self._dwell(action.seconds)
        elif isinstance(action, _SpindleAction):
            if action.command == 3:
                self.spindle_on_cw(action.rpm)
            elif action.command == 4:
                self.spindle_on_ccw(action.rpm)
            elif action.command == 5:
                self.spindle_off()
            else:
                self.spindle_speed(action.rpm)
        elif isinstance(action, _PlannedPathAction):
            self._require_motion_allowed()
            if (tuple(self._acc.steps) != action.start_steps
                    or (tuple(self._backlash_dir), tuple(self._backlash_offset)) != action.backlash_start):
                raise EcutError('Position changed since path preflight')
            self._feed_mm_min = action.feed_mm_min
            changed = set(action.backlash_changed) | {
                i for i in range(6) if action.backlash_end[1][i] != action.backlash_start[1][i]}
            self._backlash_dir[:], self._backlash_offset[:] = action.backlash_end
            if len(action.segments):
                was_running = self._running
                try:
                    self._backlash_pending |= changed
                    self._submit_segments(action.segments)
                except BaseException:
                    self._lose_backlash(changed)
                    raise
                if not self._stop_requested and (self._running or not was_running):
                    self._backlash_pending -= changed
        elif isinstance(action, _ProbeAction):
            axis, value = next(iter(action.words.items()))
            distance = value - self.logical_position_mm()[axis] if action.absolute else value
            self._probe_sensor(axis, distance, action.feed_mm_min / 60.0)
        else:
            self._move_linear(action)

    def _plan_linear_actions(self, actions, start_steps, backlash=None) -> _PlannedPathAction:
        """``backlash``: (directions, offsets) at the start; default the present ones.

        The moves are planned on the logical position (``virtual``); ``apply_backlash`` turns them into board
        moves with the compensation of each reversal.
        """
        self._require_motion_allowed()
        dirs, offsets = backlash or (tuple(self._backlash_dir), tuple(self._backlash_offset))
        virtual = _StepAccumulator(self._config)
        virtual.steps = [start_steps[i] - offsets[i] for i in range(6)]
        moves = []
        for lineno, action in actions:
            if self._planning_checkpoint is not None:
                self._planning_checkpoint()
            current = virtual.position_mm()
            target = [None] * 6
            for axis, value in action.words.items():
                target[axis] = value if action.absolute else current[axis] + value
            try:
                total = virtual.total_deltas(target)
            except (OverflowError, ValueError) as error:
                raise EcutError(f'line {lineno}: target exceeds the board position range') from error
            moves.append(LinearMove(tuple(target), action.rapid, action.feed_mm_min, action.corner_deviation_mm))
            virtual.advance(total)
        moves, end = apply_backlash(moves, BacklashState(tuple(start_steps), tuple(dirs), tuple(offsets)), self._config)
        segments = plan_path_segments(self._config, tuple(start_steps), moves, feed_override=self._feed_override,
                                      junction_deviation_mm=self._config.junction_deviation_mm,
                                      checkpoint=self._planning_checkpoint, start_offsets=tuple(offsets))
        target_steps = tuple(virtual.steps[i] + end.offsets[i] for i in range(6))
        changed = set()
        previous = tuple(offsets)
        for move in moves:
            if move.offset_counts is not None:
                changed.update(i for i in range(6) if move.offset_counts[i] != previous[i])
                previous = move.offset_counts
        return _PlannedPathAction(tuple(start_steps), target_steps, segments, actions[-1][1].feed_mm_min,
                                  (tuple(dirs), tuple(offsets)), (end.dirs, end.offsets), frozenset(changed))

    def _plan_program_actions(self, actions):
        """Preflight known paths; probe-dependent moves use confirmed runtime counts."""
        planned = []
        pending = []
        position = tuple(self._acc.steps)
        backlash = (tuple(self._backlash_dir), tuple(self._backlash_offset))
        position_unknown = False

        def flush():
            nonlocal position, backlash
            if pending:
                path = self._plan_linear_actions(pending, position, backlash)
                planned.append((pending[0][0], path))
                position, backlash = path.target_steps, path.backlash_end
                pending.clear()

        for lineno, action in actions:
            if self._planning_checkpoint is not None:
                self._planning_checkpoint()
            if type(action) is _MoveAction:
                if position_unknown:
                    # Probe hit and retract counts are only known during execution.
                    planned.append((lineno, action))
                    continue
                if all(axis < 3 for axis in action.words):
                    pending.append((lineno, action))
                else:
                    flush()
                    path = self._plan_linear_actions([(lineno, action)], position, backlash)
                    planned.append((lineno, path))
                    position, backlash = path.target_steps, path.backlash_end
            else:
                flush()
                planned.append((lineno, action))
                if isinstance(action, _ProbeAction):
                    position_unknown = True
        flush()
        return planned

    def _move_linear(self, action: _MoveAction) -> None:
        path = self._plan_linear_actions([(0, action)], tuple(self._acc.steps))
        self._run_action(path)

    def _plan_timed(self, total: list[int], rapid: bool, period: float) -> list[list[int]]:
        """Segment a move so each record covers at most (effective feed x period), with a trapezoidal profile."""
        cfg = self._config
        dist = [abs(total[i]) / (cfg.axes[i].steps_per_mm * cfg.interpolation_factor) for i in range(6)]
        length = math.sqrt(sum(d * d for d in dist))
        v_limit = math.inf
        a_limit = math.inf
        for i in range(6):
            if dist[i] <= 0:
                continue
            u = dist[i] / length
            ax = cfg.axes[i]
            if ax.max_velocity_mm_s > 0:
                v_limit = min(v_limit, ax.max_velocity_mm_s / u)
            if ax.acceleration_mm_s2 > 0:
                a_limit = min(a_limit, ax.acceleration_mm_s2 / u)
        v = v_limit
        if not rapid:
            v = min(v, self._feed_mm_min * self._feed_override / 60.0)
        if not math.isfinite(v) or v <= 0:
            raise EcutError('Effective feed is zero or no axis velocity limit is configured')

        # trapezoid (triangle when the move is too short to reach v)
        if math.isfinite(a_limit):
            if v * v / a_limit >= length:
                v = math.sqrt(a_limit * length)
                t_acc, t_cruise = v / a_limit, 0.0
            else:
                t_acc = v / a_limit
                t_cruise = (length - v * v / a_limit) / v
        else:
            t_acc, t_cruise = 0.0, length / v
        t_total = 2 * t_acc + t_cruise

        def travelled(t: float) -> float:
            if t_acc > 0 and t <= t_acc:
                return 0.5 * a_limit * t * t
            if t <= t_acc + t_cruise:
                return (0.5 * a_limit * t_acc * t_acc if t_acc > 0 else 0.0) + v * (t - t_acc)
            td = max(0.0, t_total - t)
            return length - (0.5 * a_limit * td * td if t_acc > 0 else 0.0)

        n = max(1, math.ceil(t_total / period))
        while True:
            tau = t_total / n
            fractions = [min(1.0, max(0.0, travelled(k * tau) / length)) for k in range(1, n + 1)]
            fractions[-1] = 1.0
            segments = self._acc.split(total, fractions)
            worst = max(abs(x) for seg in segments for x in seg)
            if worst <= INT16_MAX:
                return segments
            n = max(n + 1, math.ceil(n * worst / INT16_MAX))

    def _poll(self) -> None:
        self.poll()

    def _wait_for_rx(self, timeout: float = 1.0) -> Optional[EcutRxFrame]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = self.poll()
            if frame:
                return frame
            time.sleep(0.001)
        return None

    def _drain_rx(self) -> None:
        if isinstance(self._usb, EcutUsb):
            for _ in range(512):
                if self.poll() is None:
                    return
            raise EcutError('Cannot drain queued HID status')

    def _wait_idle(self) -> EcutRxFrame:
        end = time.monotonic() + 2.0
        self._drain_rx()
        while time.monotonic() < end:
            frame = self.poll()
            if (frame and not frame.estop_active and not frame.motion_busy
                    and frame.fifo_free == 12000 and frame.outputs == self._expected_outputs):
                return frame
            time.sleep(0.001)
        raise EcutError('timeout waiting for stopped board with inactive outputs')

    def _wait_for_motion(self, timeout: float = 30.0) -> None:
        """Require measured target counts, configured idle state and drained FIFO."""
        end = time.monotonic() + timeout
        # a requested stop has its own deadline: it never ends in the timeout error below
        while time.monotonic() < (max(end, self._stop_deadline) if self._stop_requested else end):
            if self._state in (State.ESTOP, State.ERROR):
                raise EcutError('E-stop/error while waiting')
            frame = self.poll()
            if self._state in (State.ESTOP, State.ERROR):
                raise EcutError('E-stop/error while waiting')
            if frame and frame.estop_active:
                raise EcutError('E-stop while waiting')
            if self._stop_requested:
                if self.is_stationary(frame):
                    self._finish_requested_stop()
                    return
            elif (frame and frame.configured and not frame.motion_busy
                    and frame.outputs == self._expected_outputs and frame.fifo_free == 12000
                    and self._at_target_counts(frame)):
                return
            time.sleep(0.001)
        if self._stop_requested:
            self._abort_requested_stop()
            return
        raise EcutError(f'timeout after {timeout} s waiting for measured motion target')
