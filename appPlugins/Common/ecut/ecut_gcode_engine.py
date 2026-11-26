# ##########################################################
# FlatCAM Evo: E-Cut host G-code interpreter               #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/20/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Independent G-code command interpreter over the verified E-Cut API.

Coordinates and settings are stored on the PC. Prepared motion batches use
host lookahead; synchronized laser control remains unverified.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ecut_controller import EcutError, MotionLine, State, _strip_comments, compile_program, encode_config
from .ecut_config import EcutBoardConfig, OutputSignal
from .ecut_planner import LinearMove, check_soft_limit_move, plan_linear_path


class GcodeError(EcutError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class _Parser:
    motion: int = 0
    plane: int = 17
    units: int = 21
    distance: int = 90
    feed_mode: int = 94
    wcs: int = 0
    feed: float = 0.0
    speed: float = 0.0
    spindle: int = 5
    coolant: set = field(default_factory=set)
    tool: int = 0
    tool_selected: bool = False
    tlo: float = 0.0
    path_mode: int = 64  # modal group 13: 61 exact path, 64 blending
    path_tolerance: float | None = None  # G64 P in mm; None = blend at the junction deviation setting
    g92: list = field(default_factory=lambda: [0.0] * 3)
    position: list = field(default_factory=lambda: [0.0] * 3)


_GROUPS = {
    0: 'motion', 1: 'motion', 2: 'motion', 3: 'motion', 80: 'motion',
    38.2: 'motion', 38.3: 'motion', 38.4: 'motion', 38.5: 'motion',
    17: 'plane', 18: 'plane', 19: 'plane',
    20: 'units', 21: 'units', 90: 'distance', 91: 'distance',
    91.1: 'arc_distance', 93: 'feed_mode', 94: 'feed_mode',
    40: 'cutter', 43.1: 'tlo', 49: 'tlo', 61: 'path', 64: 'path',
    54: 'wcs', 55: 'wcs', 56: 'wcs', 57: 'wcs', 58: 'wcs', 59: 'wcs',
    4: 'nonmodal', 10: 'nonmodal', 28: 'nonmodal', 30: 'nonmodal',
    28.1: 'nonmodal', 30.1: 'nonmodal', 53: 'nonmodal',
    92: 'nonmodal', 92.1: 'nonmodal',
}
# Machine profile fields added after the first release; older PC profiles lack them.
_ADDED_HARDWARE_FIELDS = ('soft_limit_tolerance_mm', 'input_labels', 'output_labels')
# $12 above this turns small circles into polygons
ARC_TOLERANCE_MAX_MM = 0.1
_WORD = re.compile(r'([A-Z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))')


@dataclass(frozen=True)
class _ProgramBatch:
    lines: tuple
    coolant: tuple
    stage: str
    final: str
    ended: bool


@dataclass(frozen=True)
class _PreparedProgram:
    start: tuple
    batches: tuple


class EcutGcodeEngine:
    """Persistent modal parser. A whole block is planned before any USB effect."""

    def __init__(self, api, settings_path=None):
        self.api = api
        self.settings_path = Path(settings_path) if settings_path else None
        self._host = {10: 1, 12: .002, 13: 0, 32: 0}
        self._wcs = [[0.0] * 3 for _ in range(6)]
        self._stored = [[0.0] * 3 for _ in range(2)]
        self._startup = ['', '']
        self._native = {}
        self._motor_setup = [dict(steps_per_revolution=200, microsteps=1, gear_ratio=1.0,
                                  travel_per_revolution_mm=1.0) for _ in range(4)]
        self._rapid_override = 100
        self._spindle_override = 100
        self.check_mode = False
        self._homing = False
        self._check_snapshot = None
        self._parser = _Parser(position=self._measured_position())
        self._active_parser = None
        self._program_running = False
        self._program_epoch = 0
        self._program_owner = object()
        if self.settings_path and self.settings_path.exists():
            self._load()

    @property
    def settings(self):
        values = dict(self._host)
        values[11] = self.api.config.junction_deviation_mm
        for i, axis in enumerate(self.api.config.axes[:4]):
            values[100 + i] = axis.steps_per_mm
            values[110 + i] = axis.max_velocity_mm_s * 60
            values[120 + i] = axis.acceleration_mm_s2
            values[140 + i] = axis.deceleration_mm_s2 or 0
        values[30] = self.api.config.spindle.speed_rpm_max
        values[31] = self.api.config.spindle.speed_rpm_min
        return values

    @property
    def pending_tool_change(self):
        return self.api.pending_tool_change

    @property
    def pause_reason(self):
        return self.api.pause_reason

    def confirm_tool_change(self, tool):
        self.api.confirm_tool_change(tool)

    def _measured_position(self):
        frame = self.api.last_rx_frame
        if frame is None:
            return [0.0] * 3
        return [frame.axis_positions[i] / (ax.steps_per_mm *
                self.api.config.interpolation_factor * int(ax.direction))
                for i, ax in enumerate(self.api.config.axes[:3])]

    def _offset(self, parser=None, wcs=None):
        parser = parser or self._parser
        wcs = self._wcs if wcs is None else wcs
        return [wcs[parser.wcs][i] + parser.g92[i] +
                (parser.tlo if i == 2 else 0) for i in range(3)]

    def _save(self):
        if self.settings_path and not self.check_mode:
            data = {'settings': self._host, 'wcs': self._wcs,
                    'stored': self._stored, 'startup': self._startup, 'native': self._native,
                    'motor_setup': self._motor_setup, 'machine': self.api.config.to_dict()}
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + '.tmp')
            temporary.write_text(json.dumps(data, allow_nan=False), encoding='utf-8')
            temporary.replace(self.settings_path)

    def _load(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('settings'), dict):
                raise ValueError('settings must be a mapping')
            if not isinstance(data.get('native', {}), dict):
                raise ValueError('native settings must be a mapping')
            host = {int(k): v for k, v in data['settings'].items()}
            if set(host) != set(self._host) or host[10] not in (0, 1) or host[13] not in (0, 1) or host[32] != 0:
                raise ValueError('unsupported settings')
            if not math.isfinite(host[12]) or not 0 < host[12] <= ARC_TOLERANCE_MAX_MM:
                raise ValueError('invalid tolerance')
            for key, count in [('wcs', 6), ('stored', 2)]:
                rows = data[key]
                if len(rows) != count or any(len(row) != 3 or any(
                        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                        for v in row) for row in rows):
                    raise ValueError('invalid coordinates')
            if len(data['startup']) != 2 or any(not isinstance(v, str) for v in data['startup']):
                raise ValueError('invalid startup blocks')
            for line in data['startup']:
                if any(letter == 'M' and value in (0, 6) for letter, value in self._words(line)):
                    raise ValueError('startup cannot wait for M0/M6 confirmation')
            native = {int(k): v for k, v in data.get('native', {}).items()}
            if 'machine' in data:
                data['machine'] = self._validated_hardware(data['machine'], self.api.config.to_dict())
            candidate = (EcutBoardConfig.from_dict(data['machine']) if 'machine' in data
                         else copy.deepcopy(self.api.config))
            motors = self._validated_motors(data.get('motor_setup', self._motor_setup))
            for key, value in native.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('invalid native setting')
                self._candidate_setting(candidate, key, float(value))
            encode_config(candidate)
            self.api._config = candidate
            self.api._acc.config = candidate
            self._native = native
            self._motor_setup = motors
            self._host, self._wcs, self._stored, self._startup = host, data['wcs'], data['stored'], data['startup']
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise GcodeError(7, f'Invalid E-Cut host settings: {error}') from error

    @staticmethod
    def _validated_motors(motors):
        keys = {'steps_per_revolution', 'microsteps', 'gear_ratio', 'travel_per_revolution_mm'}
        if not isinstance(motors, list) or len(motors) != 4:
            raise GcodeError(3, 'Motor setup requires four axes')
        for motor in motors:
            if not isinstance(motor, dict) or set(motor) != keys:
                raise GcodeError(3, 'Invalid motor setup fields')
            for key, value in motor.items():
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value <= 0):
                    raise GcodeError(3, 'Motor setup values must be finite and positive')
                if key in ('steps_per_revolution', 'microsteps') and (int(value) != value or value > 65536):
                    raise GcodeError(3, 'Motor steps and microsteps require integers up to 65536')
        return copy.deepcopy(motors)

    def get_motion_settings(self):
        hardware = self.api.config.to_dict()
        revision = hashlib.sha256(json.dumps([hardware, self._native, self._motor_setup],
                                  sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()
        keys = ('steps_per_mm', 'max_velocity_mm_s', 'acceleration_mm_s2', 'deceleration_mm_s2',
                'soft_limits_enabled', 'soft_limit_negative_mm', 'soft_limit_positive_mm')
        return {'revision': revision, 'interpolation_factor': self.api.config.interpolation_factor,
                'axes': [{key: int(axis.steps_per_mm) if key == 'steps_per_mm' else getattr(axis, key)
                          for key in keys} for axis in self.api.config.axes[:4]],
                'motors': copy.deepcopy(self._motor_setup), 'hardware': hardware}

    def _require_tuning_idle(self, revision):
        if not isinstance(revision, str) or revision != self.get_motion_settings()['revision']:
            raise GcodeError(3, 'Machine settings changed; reload before applying')
        if self.check_mode or self.api.state != State.IDLE or self.pending_tool_change is not None:
            raise GcodeError(8, 'Motion settings require idle with check mode disabled')

    def _require_outputs_off(self):
        frame = self.api._wait_for_rx(.5)
        if frame is None or not frame.configured or frame.motion_busy or self.api.state != State.IDLE:
            raise GcodeError(8, 'Fresh configured idle board status required')
        if (getattr(self.api, '_spindle_direction', None) is not None
                or getattr(self.api, '_spindle_rpm', 0)
                or any(getattr(self.api, '_logical_outputs', {}).values())
                or frame.outputs & 0xffff != self.api._inactive_outputs() & 0xffff):
            raise GcodeError(8, 'Switch the spindle and all outputs off before tuning')
        return frame

    @staticmethod
    def _validated_hardware(values, template):
        values, template = copy.deepcopy(values), copy.deepcopy(template)
        if isinstance(values, dict):
            # fields added after a profile was saved take their defaults
            for name in _ADDED_HARDWARE_FIELDS:
                if name in template and name not in values:
                    values[name] = copy.deepcopy(template[name])
        if isinstance(values, dict) and isinstance(values.get('outputs'), dict):
            for name in set(values['outputs']) - set(template['outputs']):
                if name in {f'ENABLE{i}' for i in range(1, 7)}:
                    defaults = OutputSignal().to_dict()
                    template['outputs'][name] = defaults
                    if isinstance(values['outputs'][name], dict):
                        values['outputs'][name] = {**defaults, **values['outputs'][name]}
        EcutGcodeEngine._validate_hardware_types(values, template)
        return values

    @staticmethod
    def _validate_hardware_types(values, template):
        if isinstance(template, dict):
            if not isinstance(values, dict) or set(values) != set(template):
                raise GcodeError(3, 'Hardware configuration fields do not match the machine profile')
            for key in template:
                EcutGcodeEngine._validate_hardware_types(values[key], template[key])
        elif isinstance(template, list):
            if not isinstance(values, list) or len(values) != len(template):
                raise GcodeError(3, 'Hardware configuration array has an invalid length')
            for value, item in zip(values, template):
                EcutGcodeEngine._validate_hardware_types(value, item)
        elif isinstance(template, bool) and type(values) is not bool:
            raise GcodeError(3, 'Hardware switches require boolean values')
        elif not isinstance(template, bool) and isinstance(template, (int, float)) and isinstance(values, bool):
            raise GcodeError(3, 'Hardware numeric fields cannot be boolean values')

    def save_motion_settings(self, payload):
        if not isinstance(payload, dict):
            raise GcodeError(3, 'Motion settings must be a mapping')
        self._require_tuning_idle(payload.get('revision'))
        snapshot = self.get_motion_settings()
        if payload.get('interpolation_factor', snapshot['interpolation_factor']) != snapshot['interpolation_factor']:
            raise GcodeError(3, 'Interpolation factor is read only')
        hardware = self._validated_hardware(payload.get('hardware', snapshot['hardware']), snapshot['hardware'])
        for key, value in hardware.items():
            if key not in ('axes', 'inputs', 'outputs', 'homing', 'probe', 'spindle', 'safety',
                           *_ADDED_HARDWARE_FIELDS) and value != snapshot['hardware'][key]:
                raise GcodeError(3, f'Hardware field {key} is read only')
        axis_fields = {'enabled', 'direction', 'step_pin', 'dir_pin', 'step_port', 'dir_port',
                       'step_active_low', 'dir_active_low'}
        for i, values in enumerate(hardware['axes']):
            for key, value in values.items():
                if (i >= 4 or key not in axis_fields) and value != snapshot['hardware']['axes'][i][key]:
                    raise GcodeError(3, f'Hardware axis field {key} is read only; use the motion settings')
        for values in hardware['inputs'].values():
            if values['enabled'] and (values['port'] != 1 or type(values['pin']) is not int or not 0 <= values['pin'] < 16):
                raise GcodeError(3, 'Only the sixteen native input pins are supported here')
        candidate = EcutBoardConfig.from_dict(hardware)
        axes = payload.get('axes')
        if not isinstance(axes, list) or len(axes) != 4:
            raise GcodeError(3, 'Motion settings require four axes')
        native = dict(self._native)
        for i, values in enumerate(axes):
            if not isinstance(values, dict) or set(values) != set(snapshot['axes'][i]):
                raise GcodeError(3, 'Invalid axis settings fields')
            for key, value in values.items():
                if key == 'soft_limits_enabled':
                    if type(value) is not bool:
                        raise GcodeError(3, 'Soft limit switch requires a boolean')
                elif value is None and key == 'deceleration_mm_s2':
                    continue
                elif (isinstance(value, bool) or not isinstance(value, (int, float))
                      or not math.isfinite(value)):
                    raise GcodeError(3, 'Axis settings must be finite numbers')
            for base, name, factor in ((100, 'steps_per_mm', 1), (110, 'max_velocity_mm_s', 60),
                                      (120, 'acceleration_mm_s2', 1), (140, 'deceleration_mm_s2', 1),
                                      (150, 'soft_limit_negative_mm', 1), (160, 'soft_limit_positive_mm', 1),
                                      (170, 'soft_limits_enabled', 1)):
                value = (values[name] or 0) * factor
                self._candidate_setting(candidate, base + i, float(value))
                native[base + i] = value
        motors = self._validated_motors(payload.get('motors'))
        for key, value in ((11, candidate.junction_deviation_mm), (30, candidate.spindle.speed_rpm_max),
                           (31, candidate.spindle.speed_rpm_min)):
            if key in native:
                native[key] = value
        encode_config(candidate)
        frame = self._require_outputs_off()
        plan_linear_path(candidate, frame.axis_positions, [], checkpoint=self.api._planning_checkpoint)
        previous = self.api._config
        self.api._config = candidate
        self.api._acc.config = candidate
        try:
            self.api.push_config()
        except BaseException:
            self.api._config = previous
            self.api._acc.config = previous
            raise
        self._native, self._motor_setup = native, motors
        self._parser.spindle, self._parser.coolant = 5, set()
        self._parser.position = self._measured_position()
        try:
            self._save()
        except OSError as error:
            raise GcodeError(7, f'Machine settings applied to RAM; PC storage failed: {error}') from error
        return self.get_motion_settings()

    def reference_axes(self, axes):
        """Manual machine home, without switches: the current position becomes each axis home position."""
        if self.check_mode or self.api.state != State.IDLE or self.pending_tool_change is not None:
            raise GcodeError(8, 'Machine home requires idle with check mode disabled')
        if not isinstance(axes, (list, tuple)) or any(type(axis) is not int or not 0 <= axis < 4 for axis in axes):
            raise GcodeError(3, 'Machine home axes must be X, Y, Z or A')
        self.api.reference_axes(list(axes))
        self._parser.position = self._measured_position()
        return {'homed_axes': list(self.api.homed_axes)}

    def calibration_move(self, axis, distance_mm, feed_mm_min, revision):
        self._require_tuning_idle(revision)
        if type(axis) is not int or not 0 <= axis < 4:
            raise GcodeError(3, 'Calibration axis must be X, Y, Z or A')
        if (isinstance(distance_mm, bool) or not isinstance(distance_mm, (int, float))
                or not math.isfinite(distance_mm) or not 0 < abs(distance_mm) <= 10):
            raise GcodeError(3, 'Calibration distance must be nonzero and at most 10 mm')
        if (isinstance(feed_mm_min, bool) or not isinstance(feed_mm_min, (int, float))
                or not math.isfinite(feed_mm_min) or not 0 < feed_mm_min <= self.api.config.axes[axis].max_velocity_mm_s * 60):
            raise GcodeError(3, 'Calibration feed must be positive and within the configured axis rate')
        frame = self._require_outputs_off()
        target = [None] * 6
        ax = self.api.config.axes[axis]
        target[axis] = frame.axis_positions[axis] / (ax.steps_per_mm * self.api.config.interpolation_factor * int(ax.direction)) + distance_mm
        blocks = plan_linear_path(self.api.config, frame.axis_positions,
                                  [LinearMove(tuple(target), False, feed_mm_min)],
                                  feed_override=self.api.feed_override, checkpoint=self.api._planning_checkpoint)
        if not blocks or sum(block.duration_s for block in blocks) > 30:
            raise GcodeError(3, 'Calibration must produce a measurable move lasting at most 30 seconds')
        modal, epoch = copy.deepcopy(self.api._modal), self._program_epoch
        try:
            self.api.run_gcode([f'G21 G91 G94 G1 {"XYZA"[axis]}{distance_mm:.12g} F{feed_mm_min:.12g}'])
        finally:
            if epoch == self._program_epoch:
                self.api._modal = modal
                self._parser.position = self._measured_position()
        if self.api.state != State.IDLE:
            raise GcodeError(8, 'Calibration did not complete; inspect board status')
        return {'revision': revision, 'position_mm': list(self.api.position_mm), 'distance_mm': distance_mm}

    def execute_line(self, line: str) -> list[str]:
        text = line.strip()
        if text.startswith('$'):
            return self._system(text.upper())
        parser, wcs, stored, lines, coolant, ended = self._validated_plan(text)
        if not self.check_mode:
            self._active_parser = parser
            try:
                self._execute(lines, coolant)
            finally:
                self._active_parser = None
        self._parser, self._wcs, self._stored = parser, wcs, stored
        if ended:
            parser.motion, parser.plane, parser.distance, parser.feed_mode = 1, 17, 90, 94
            parser.wcs, parser.spindle, parser.coolant = 0, 5, set()
            if not self.check_mode:
                self._rapid_override = self._spindle_override = 100
                self.api.feed_override = 1
        self._save()
        return ['ok']

    def _program_state(self, parser=None, wcs=None, stored=None):
        values = dict((parser or self._parser).__dict__)
        values['coolant'] = sorted(values['coolant'])
        return json.dumps({'parser': values, 'wcs': self._wcs if wcs is None else wcs,
                           'stored': self._stored if stored is None else stored},
                          sort_keys=True, allow_nan=False)

    def _restore_program_state(self, state):
        values = json.loads(state)
        values['parser']['coolant'] = set(values['parser']['coolant'])
        self._parser = _Parser(**values['parser'])
        self._wcs, self._stored = values['wcs'], values['stored']

    def _program_start(self):
        frame = self.api.last_rx_frame
        return (self._program_epoch, tuple(frame.axis_positions) if frame else (),
                json.dumps(self.api.config.to_dict(), sort_keys=True, allow_nan=False),
                self._program_state(), json.dumps(self._host, sort_keys=True),
                self.api.feed_override, self._rapid_override, self._spindle_override,
                self.check_mode, self._program_owner, tuple(self.api._acc.steps))

    def _planning_checkpoint(self):
        checkpoint = self.api._planning_checkpoint
        if checkpoint is not None:
            checkpoint()

    def prepare_program(self, lines):
        """Preflight a complete file without touching USB, persistence or live state."""
        if self._program_running or self.api.state != State.IDLE:
            raise GcodeError(8, 'Program preparation requires an idle controller')
        start = self._program_start()
        if not start[1]:
            raise GcodeError(8, 'Program preparation requires measured starting counts')
        virtual = copy.copy(self)
        virtual._parser = copy.deepcopy(self._parser)
        virtual._parser.position = (list(self._parser.position) if self.check_mode
                                    else self._measured_position())
        virtual._wcs, virtual._stored = copy.deepcopy((self._wcs, self._stored))
        virtual._host = copy.deepcopy(self._host)
        virtual._native = copy.deepcopy(self._native)
        virtual.settings_path = None
        virtual.check_mode = True
        batches, normalized, source_indices = [], [], []
        pending, pending_stage, pending_final, pending_signature = [], None, None, None

        def flush():
            nonlocal pending, pending_stage, pending_final, pending_signature
            if pending:
                batches.append(_ProgramBatch(tuple(pending), (), pending_stage, pending_final, False))
            pending, pending_stage, pending_final, pending_signature = [], None, None, None

        for index, line in enumerate(lines):
            self._planning_checkpoint()
            try:
                # comments can hold any text (object names); the code itself must be ASCII
                if (not isinstance(line, str) or not _strip_comments(line).isascii() or
                        any(ord(char) < 32 or char in '?~!$' for char in line)):
                    raise GcodeError(20, 'Prepared files require ASCII G-code without system/realtime commands')
                parser, wcs, stored, block, coolant, ended = virtual._validated_plan(line)
                stage = virtual._program_state(parser, wcs, stored)
                virtual.execute_line(line)
                final = virtual._program_state()
                words = virtual._words(line)
                semantic = bool(coolant or ended or any(
                    letter in 'MST' or (letter == 'G' and value in
                    (4, 10, 28, 30, 28.1, 30.1, 43.1, 49, 54, 55, 56, 57, 58, 59, 92, 92.1))
                    for letter, value in words))
                mode = json.loads(stage)
                mode['parser'].pop('position')
                signature = json.dumps(mode, sort_keys=True)
                normalized.extend(block)
                source_indices.extend([index] * len(block))
                if semantic or not block:
                    flush()
                    batches.append(_ProgramBatch(tuple(block), tuple(coolant), stage, final, ended))
                else:
                    if pending and signature != pending_signature:
                        flush()
                    if not pending:
                        pending_stage, pending_signature = stage, signature
                    pending.extend(block)
                    pending_final = final
            except GcodeError as error:
                error.source_index = index
                raise
        flush()
        try:
            self.api._plan_program_actions(compile_program(normalized, checkpoint=self.api._planning_checkpoint))
        except (EcutError, ValueError, OverflowError) as error:
            failure = GcodeError(20, str(error))
            match = re.search(r'line (\d+)', str(error))
            if match and source_indices:
                failure.source_index = source_indices[min(int(match[1]) - 1, len(source_indices) - 1)]
            raise failure from error
        if start != self._program_start():
            raise GcodeError(8, 'Controller state changed during program preparation')
        return _PreparedProgram(start, tuple(batches))

    def execute_program(self, prepared):
        """Execute preflighted motion groups, committing only reached host changes."""
        if (not isinstance(prepared, _PreparedProgram) or self._program_running or
                self.api.state != State.IDLE or prepared.start != self._program_start()):
            raise GcodeError(8, 'Prepared program is stale; prepare it again')
        self._program_epoch += 1
        epoch = self._program_epoch
        self._program_running = True
        try:
            for batch in prepared.batches:
                if self._program_epoch != epoch:
                    raise GcodeError(8, 'Prepared program was interrupted')
                self._restore_program_state(batch.stage)
                self._save()
                if not self.check_mode:
                    self._active_parser = self._parser
                    self._execute(list(batch.lines), batch.coolant)
                if self._program_epoch != epoch:
                    raise GcodeError(8, 'Prepared program was interrupted')
                self._restore_program_state(batch.final)
                if batch.ended and not self.check_mode:
                    self._rapid_override = self._spindle_override = 100
                    self.api.feed_override = 1
                self._save()
        finally:
            self._active_parser = None
            self._program_running = False
            if not self.check_mode:
                self._parser.position = self._measured_position()

    def _words(self, text):
        text = text.split(';', 1)[0].upper()
        text = re.sub(r'\([^)]*\)', '', text)
        text = ''.join(text.split())
        words, pos = [], 0
        while pos < len(text):
            match = _WORD.match(text, pos)
            if not match:
                raise GcodeError(2, 'Expected a letter followed by a finite number')
            value = float(match[2])
            if not math.isfinite(value):
                raise GcodeError(2, 'Non-finite number')
            words.append((match[1], value))
            pos = match.end()
        return words

    def _validated_plan(self, text, jog=False):
        try:
            return self._plan(text, jog)
        except GcodeError:
            raise
        except (EcutError, ValueError, OverflowError) as error:
            raise GcodeError(20, str(error)) from error

    def _plan(self, text, jog=False):
        parser = copy.deepcopy(self._parser)
        wcs, stored = copy.deepcopy(self._wcs), copy.deepcopy(self._stored)
        if not self.check_mode:
            parser.position = self._measured_position()
        origin = list(parser.position)
        if jog:
            parser.motion, parser.feed_mode = 1, 94
        values, groups, gs, ms = {}, set(), [], []
        for letter, value in self._words(text):
            if letter == 'G':
                if value not in _GROUPS:
                    raise GcodeError(20, f'Unsupported G{value:g}')
                group = _GROUPS[value]
                if group in groups:
                    raise GcodeError(21, 'More than one command in a modal group')
                groups.add(group)
                gs.append(value)
            elif letter == 'M':
                if value not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 30):
                    raise GcodeError(20, f'Unsupported M{value:g}')
                group = 'spindle' if value in (3, 4, 5) else 'coolant' if value in (7, 8, 9) else 'flow'
                if group in groups:
                    raise GcodeError(21, 'More than one command in a modal group')
                groups.add(group)
                ms.append(int(value))
            else:
                if letter not in 'FIJKLNPRSTXYZ':
                    raise GcodeError(20, f'Unsupported {letter} word')
                if letter in values:
                    raise GcodeError(25, f'Repeated {letter} word')
                if letter in 'FNPTS' and value < 0:
                    raise GcodeError(4, f'Negative {letter} value')
                values[letter] = value
        if any(g in gs for g in (38.2, 38.3, 38.4, 38.5)):
            raise GcodeError(20, 'Probing refused: physical input cycle capture is not verified')
        if jog and (ms or any(g not in (20, 21, 90, 91, 53) for g in gs)
                    or any(k not in 'FXYZ' for k in values) or 'F' not in values
                    or not any(k in values for k in 'XYZ')):
            raise GcodeError(16, 'Jog requires axes, F and only G20/G21 G90/G91 G53')
        for letter, maximum in [('N', 10000000), ('T', 255)]:
            if letter in values and (not values[letter].is_integer() or values[letter] > maximum):
                raise GcodeError(23, f'Invalid {letter} integer')
        for g in gs:
            group = _GROUPS[g]
            if group in ('motion', 'plane', 'units', 'distance', 'feed_mode'):
                if group == 'feed_mode' and g != parser.feed_mode:
                    parser.feed = 0
                setattr(parser, group, int(g))
            elif group == 'wcs':
                parser.wcs = int(g - 54)
            elif group == 'path':
                parser.path_mode, parser.path_tolerance = int(g), None
        scale = 25.4 if parser.units == 20 else 1
        if 'F' in values:
            parser.feed = values['F'] * (scale if parser.feed_mode == 94 else 1)
        if 'S' in values:
            parser.speed = values['S']
        if 'T' in values:
            parser.tool, parser.tool_selected = int(values['T']), True
        axes = {i: values[a] * scale for i, a in enumerate('XYZ') if a in values}
        consumed = set('FNST')
        non = next((g for g in gs if _GROUPS[g] == 'nonmodal'), None)
        explicit_motion = 'motion' in groups
        axis_non = non in (10, 28, 30, 92)
        if (axis_non or 'tlo' in groups) and explicit_motion:
            raise GcodeError(24, 'Conflicting axis commands')
        lines, coolant = [], []
        if 43.1 in gs:
            if set(axes) != {2}:
                raise GcodeError(37, 'G43.1 requires only Z')
            parser.tlo = axes[2]
        if 49 in gs:
            if axes:
                raise GcodeError(24, 'G49 does not accept axes')
            parser.tlo = 0
        offset = self._offset(parser, wcs)
        target = list(parser.position)
        for i, value in axes.items():
            target[i] = value + offset[i] if parser.distance == 90 else target[i] + value
        moving = bool(axes) and not axis_non and 'tlo' not in groups
        if non == 53:
            if parser.motion not in (0, 1):
                raise GcodeError(30, 'G53 requires G0 or G1')
            for i, value in axes.items():
                target[i] = value
        elif non == 4:
            if 'P' not in values:
                raise GcodeError(28, 'G4 requires P seconds')
            consumed.add('P')
        elif non == 10:
            if values.get('L') not in (2, 20) or 'P' not in values or not axes:
                raise GcodeError(28, 'G10 requires L2/L20 P0..6 and axes')
            p = values['P']
            if not p.is_integer() or not 0 <= p <= 6 or 'R' in values:
                raise GcodeError(20, 'Invalid G10 coordinate system')
            index = parser.wcs if p == 0 else int(p) - 1
            for i, value in axes.items():
                wcs[index][i] = value if values['L'] == 2 else (
                    parser.position[i] - parser.g92[i] - (parser.tlo if i == 2 else 0) - value)
            consumed.update('LP')
        elif non == 92:
            if not axes:
                raise GcodeError(26, 'G92 requires an axis')
            for i, value in axes.items():
                parser.g92[i] = parser.position[i] - wcs[parser.wcs][i] - (
                    parser.tlo if i == 2 else 0) - value
        elif non == 92.1:
            parser.g92 = [0.0] * 3
        elif non in (28.1, 30.1):
            if axes:
                raise GcodeError(31, 'Stored-position command does not accept axes')
            stored[0 if non == 28.1 else 1] = list(parser.position)
        elif non in (28, 30):
            if axes:
                lines.append(self._linear(0, target, 0))
            destination = list(parser.position)
            for i in (axes if axes else range(3)):
                destination[i] = stored[0 if non == 28 else 1][i]
            lines.append(self._linear(0, destination, 0, target if axes else parser.position))
            parser.position = destination
        if axes and non in (28.1, 30.1, 92.1):
            raise GcodeError(31, 'Unused axis words')
        arc = parser.motion in (2, 3) and not axis_non and 'tlo' not in groups and (
            axes or explicit_motion or any(k in values for k in 'IJKR'))
        if 64 in gs and 'P' in values:
            # G64 P<tolerance>: the P word is the tolerance, so it cannot also be a dwell, G10 or arc P.
            if arc or non in (4, 10):
                raise GcodeError(24, 'G64 P conflicts with another P word in the block')
            if not values['P'] > 0:
                raise GcodeError(4, 'G64 P must be a positive tolerance')
            parser.path_tolerance = values['P'] * scale
            consumed.add('P')
        points = []
        if moving or arc:
            if parser.motion == 80:
                raise GcodeError(31, 'Axis words with G80')
            if parser.motion != 0 and (parser.feed <= 0 or (
                    parser.feed_mode == 93 and 'F' not in values)):
                raise GcodeError(22, 'A positive F is required for this motion')
            if arc:
                points, length = self._arc(parser, target, values, scale)
                consumed.update('IJKRP')
            else:
                points = [target]
                length = math.dist(parser.position, target)
            feed = parser.feed if parser.feed_mode == 94 else length * parser.feed
            # Path control: only the end of the programmed move gets the mode's corner limit. G61's 0 would
            # stop at every chord vertex of an arc, so interior vertices keep the junction setting, or the
            # G64 P tolerance, which bounds how far the chords may leave the programmed arc.
            end = None if jog else self.path_deviation_mm(parser)
            interior = None if parser.path_mode == 61 else end
            for n, point in enumerate(points, 1):
                line = self._linear(0 if parser.motion == 0 else 1, point, feed)
                corner = end if n == len(points) else interior
                lines.append(line if corner is None else MotionLine(line, corner))
            parser.position = target
        if any(k not in consumed and k not in 'XYZ' for k in values):
            raise GcodeError(36, 'Unused value words')
        # Validate all targets before a spindle/coolant action can run.
        tolerance = self.api.config.soft_limit_tolerance_mm
        previous = list(origin)
        for line in lines:
            self._planning_checkpoint()
            wordmap = dict(self._words(line))
            for i, name in enumerate('XYZ'):
                axis = self.api.config.axes[i]
                value = wordmap.get(name, parser.position[i])
                if not axis.enabled and abs(value - self._parser.position[i]) > 1e-9:
                    raise GcodeError(20, f'Axis {name} is disabled')
                if name in wordmap and axis.enabled and axis.soft_limits_enabled:
                    try:
                        check_soft_limit_move(axis, previous[i], value, tolerance, name)
                    except EcutError as error:
                        raise GcodeError(15, f'Soft limit on {name}') from error
                if name in wordmap:
                    previous[i] = value
                raw = value * axis.steps_per_mm * self.api.config.interpolation_factor * int(axis.direction)
                if not math.isfinite(raw) or not -2147483648 <= raw <= 2147483647:
                    raise GcodeError(15, 'Target outside signed 32-bit counts')
        before = []
        if 'T' in values:
            before.append(f'T{parser.tool}')
        spindle = next((m for m in ms if m in (3, 4, 5)), None)
        if spindle is not None:
            parser.spindle = spindle
        if spindle is not None or 'S' in values:
            direction = {3: 'SPINDLE_CW', 4: 'SPINDLE_CCW', 5: None}[parser.spindle]
            effective_speed = self._effective_speed(parser.speed)
            self.api._validate_spindle(effective_speed, direction)
            before.append((f'M{spindle} ' if spindle is not None else '') + f'S{effective_speed:g}')
        for m in ms:
            if m in (7, 8, 9):
                names = ('MIST',) if m == 7 else ('FLOOD',) if m == 8 else ('MIST', 'FLOOD')
                for name in names:
                    sig = self.api.config.outputs[name]
                    if m != 9 and not sig.enabled:
                        raise GcodeError(20, f'{name} output is not configured')
                    if sig.enabled:
                        coolant.append((name, m != 9))
                if m == 9:
                    parser.coolant.clear()
                else:
                    parser.coolant.add(m)
        if non == 4:
            before.append(f'G4 P{values["P"]:.6f}')
        if 6 in ms:
            if not parser.tool_selected or axes or 0 in ms:
                raise GcodeError(20, 'M6 requires selected T and no axes')
            lines.append(f'T{parser.tool} M6')
            parser.spindle, parser.coolant = 5, set()
        if 0 in ms:
            lines.append('M0')
        ended = any(m in ms for m in (2, 30))
        if ended:
            lines.append('M30')
        lines = before + lines
        compile_program(lines, checkpoint=self.api._planning_checkpoint)
        return parser, wcs, stored, lines, coolant, ended

    def _linear(self, motion, target, feed, start=None):
        if motion == 0 and self._rapid_override != 100:
            start = self._parser.position if start is None else start
            distance = [abs(target[i] - start[i]) for i in range(3)]
            length = math.sqrt(sum(v*v for v in distance))
            limits = [self.api.config.axes[i].max_velocity_mm_s * length / distance[i]
                      for i in range(3) if distance[i] > 0]
            feed = (min(limits) * 60 * self._rapid_override / 100 if limits else 1)
            feed /= max(.1, self.api.feed_override)
            motion = 1
        coordinates = ' '.join(f'{name}{target[i]:.9f}' for i, name in enumerate('XYZ')
                               if self.api.config.axes[i].enabled or abs(target[i] - self._parser.position[i]) > 1e-9)
        return f'G21 G90 G{motion} {coordinates}' + (f' F{feed:.9f}' if motion else '')

    def _arc(self, parser, target, values, scale):
        a, b, linear = {17: (0, 1, 2), 18: (2, 0, 1), 19: (1, 2, 0)}[parser.plane]
        start = parser.position
        clockwise = parser.motion == 2
        dx, dy = target[a] - start[a], target[b] - start[b]
        has_offsets = any(k in values for k in 'IJK')
        if 'R' in values:
            if has_offsets:
                raise GcodeError(36, 'R and IJK cannot be combined')
            radius = abs(values['R'] * scale)
            chord = math.hypot(dx, dy)
            if chord == 0 or radius == 0 or chord > 2 * radius:
                raise GcodeError(34, 'Invalid radius arc')
            h = math.sqrt(max(0, radius * radius - chord * chord / 4))
            sign = -1 if clockwise else 1
            if values['R'] < 0:
                sign = -sign
            center = (start[a] + dx / 2 - sign * dy * h / chord,
                      start[b] + dy / 2 + sign * dx * h / chord)
        else:
            names = 'IJK'
            if names[linear] in values or not any(names[i] in values for i in (a, b)):
                raise GcodeError(35, 'Arc requires offsets in the selected plane')
            center = (start[a] + values.get(names[a], 0) * scale,
                      start[b] + values.get(names[b], 0) * scale)
            radius = math.hypot(start[a] - center[0], start[b] - center[1])
            end_radius = math.hypot(target[a] - center[0], target[b] - center[1])
            if radius <= 0 or abs(radius - end_radius) > max(.005, radius * .001):
                raise GcodeError(33, 'Arc endpoint radius mismatch')
        begin = math.atan2(start[b] - center[1], start[a] - center[0])
        end = math.atan2(target[b] - center[1], target[a] - center[0])
        # The live start comes from rounded counts: an end within one count of the start is a full circle.
        resolution = max(1 / abs(self.api.config.axes[i].steps_per_mm * self.api.config.interpolation_factor)
                         for i in (a, b))
        if 'R' not in values and math.hypot(dx, dy) <= resolution:
            sweep = -2 * math.pi if clockwise else 2 * math.pi
        else:
            sweep = (end - begin) % (2 * math.pi)
            if clockwise:
                sweep -= 2 * math.pi
        # P = number of turns (LinuxCNC): P1 is the plain arc, each count above 1 adds one full circle.
        turns = values.get('P', 1)
        if not turns.is_integer() or turns < 1:
            raise GcodeError(23, 'Arc P must be a positive integer number of turns')
        if turns > 1:
            if turns > 100000:
                raise GcodeError(14, 'Arc exceeds host segment limit')
            sweep += (turns - 1) * (-2 * math.pi if clockwise else 2 * math.pi)
        tolerance = self._host[12]
        # At least 8 segments per full circle, whatever the tolerance.
        step = min(math.pi / 4, 2 * math.acos(max(-1, min(1, 1 - tolerance / radius))))
        count = max(1, math.ceil(abs(sweep) / max(step, 1e-9)))
        if count > 100000:
            raise GcodeError(14, 'Arc exceeds host segment limit')
        points = []
        for n in range(1, count + 1):
            self._planning_checkpoint()
            fraction = n / count
            point = list(start)
            angle = begin + sweep * fraction
            point[a], point[b] = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
            point[linear] += (target[linear] - start[linear]) * fraction
            points.append(target if n == count else point)
        return points, math.hypot(abs(sweep) * radius, target[linear] - start[linear])

    def path_deviation_mm(self, parser=None):
        """Corner deviation for `LinearMove.corner_deviation_mm` under the active path control mode.

        None: G64 without P, the junction deviation setting applies. 0.0: G61 exact path.
        Otherwise the G64 P tolerance in mm, never above the junction deviation setting.
        """
        parser = parser or self._parser
        if parser.path_mode == 61:
            return 0.0
        if parser.path_tolerance is None:
            return None
        return min(parser.path_tolerance, self.api.config.junction_deviation_mm)

    def _effective_speed(self, programmed):
        if programmed == 0:
            return 0
        spindle = self.api.config.spindle
        return max(spindle.speed_rpm_min, min(spindle.speed_rpm_max,
                   programmed * self._spindle_override / 100))

    def run_startup(self):
        responses = []
        for line in self._startup:
            if line:
                self.execute_line(line)
                responses.append(f'>{line}:ok')
        return responses

    def _execute(self, lines, coolant):
        if lines or coolant:
            self.api._require_motion_allowed()
        for name, on in coolant:
            self.api.set_output(name, on)
        if lines:
            self.api.run_gcode(lines)

    def _system(self, text):
        if text == '$':
            return ['[HLP:$$ $# $G $I $N $C $X $J= $RST=$/#/*]',
                    '[MSG:E-Cut host adapter; sensor cycles, laser and driver sleep unverified]',
                    '[MSG:E-Cut axis settings X/Y/Z/A: $110..113 velocity mm/min; $120..123 acceleration mm/s2]',
                    '[MSG:E-Cut host extension $140..143 deceleration mm/s2; 0 follows acceleration]', 'ok']
        if text == '$$':
            return [f'${key}={value:g}' for key, value in sorted(self.settings.items())] + ['ok']
        if text == '$I':
            return ['[VER:1.1h:E-Cut host compatibility]',
                    '[OPT:V,1,128]',
                    '[MSG:Host lookahead for prepared motion batches; prepared-job overrides unavailable]', 'ok']
        if text == '$G':
            p = self._parser
            coolant = ' '.join(f'M{m}' for m in sorted(p.coolant)) or 'M9'
            return [f'[GC:G{p.motion} G{54+p.wcs} G{p.plane} G{p.units} G{p.distance} '
                    f'G{p.feed_mode} M{p.spindle} {coolant} T{p.tool} F{p.feed:g} S{p.speed:g}]', 'ok']
        if text == '$#':
            rows = [f'[G{54+i}:{self._coordinates(v)}]' for i, v in enumerate(self._wcs)]
            rows += [f'[G{28+i*2}:{self._coordinates(v)}]' for i, v in enumerate(self._stored)]
            return rows + [f'[G92:{self._coordinates(self._parser.g92)}]',
                           f'[TLO:{self._report_value(self._parser.tlo):.3f}]',
                           '[MSG:Probe result unavailable; physical cycle not verified]', 'ok']
        if text == '$C':
            if not self.check_mode:
                self._check_snapshot = copy.deepcopy((self._parser, self._wcs, self._stored))
                self.check_mode = True
                return ['[MSG:Enabled]', 'ok']
            self._parser, self._wcs, self._stored = self._check_snapshot
            self._check_snapshot = None
            self.check_mode = False
            return ['[MSG:Disabled]', 'ok']
        if text == '$X':
            if self.check_mode:
                raise GcodeError(8, 'Unlock unavailable in check mode')
            frame = self.api._wait_for_rx(.5)
            if frame is None or self.api.estop_active or self.api.limits_triggered or self.api.state in (State.ERROR, State.ESTOP):
                raise GcodeError(9, 'Unlock refused: reset and fresh safe inputs required')
            self.api._check_resume_inputs()
            return ['[MSG:Caution: Unlocked]', 'ok']
        if text in ('$H', '$HX', '$HY', '$HZ'):
            return self._home_cycle(text[2:])
        if text.startswith('$H'):
            raise GcodeError(3, 'Unsupported system command')
        if text == '$SLP':
            raise GcodeError(20, 'Driver disable/sleep command is not verified')
        if text.startswith('$J='):
            if self.check_mode:
                raise GcodeError(8, 'Jog unavailable in check mode')
            parser, _, _, lines, coolant, _ = self._validated_plan(text[3:], jog=True)
            self._execute(lines, coolant)
            self._parser.position = parser.position
            return ['ok']
        if text == '$N':
            return [f'$N{i}={line}' for i, line in enumerate(self._startup)] + ['ok']
        match = re.fullmatch(r'\$N([01])=(.*)', text)
        if match:
            if self.check_mode:
                raise GcodeError(8, 'Startup storage unavailable in check mode')
            if any(letter == 'M' and value in (0, 6) for letter, value in self._words(match[2])):
                raise GcodeError(20, 'Startup blocks cannot wait for M0/M6 confirmation')
            self._validated_plan(match[2])
            self._startup[int(match[1])] = match[2]
            self._save()
            return ['ok']
        if text.startswith('$RST='):
            if self.check_mode or self.api.state != State.IDLE:
                raise GcodeError(8, 'Host restore requires idle and check mode disabled')
            kind = text[5:]
            if kind not in ('$', '#', '*'):
                raise GcodeError(3, 'Unsupported restore selector')
            if kind in ('$', '*') and self._native:
                raise GcodeError(20, 'Native setting restore requires the machine profile; automatic reset refused')
            if kind in ('$', '*'):
                self._host = {10: 1, 12: .002, 13: 0, 32: 0}
            if kind in ('#', '*'):
                self._wcs, self._stored = [[0.] * 3 for _ in range(6)], [[0.] * 3 for _ in range(2)]
            if kind == '*':
                self._startup = ['', '']
            self._save()
            return ['[MSG:E-Cut PC settings restored; board EEPROM unchanged]', 'ok']
        match = re.fullmatch(r'\$(\d+)=([-+]?(?:\d+(?:\.\d*)?|\.\d+))', text)
        if match:
            self._set_setting(int(match[1]), float(match[2]))
            return ['ok']
        raise GcodeError(3, 'Unsupported system command')

    def _home_cycle(self, letter):
        """$H / $HX / $HY / $HZ: the sensor homing cycle, Z first, then X and Y (configured axes only)."""
        if not self.api.config.unverified.homing:
            raise GcodeError(20, 'Homing refused: physical input cycle capture is not verified')
        if self.check_mode or self.pending_tool_change is not None:
            raise GcodeError(8, 'Homing requires check mode disabled and no tool change pending')
        config = self.api.config
        if letter:
            axes = ['XYZ'.index(letter)]
        else:
            axes = [axis for axis in (2, 0, 1) if config.homing[axis].enabled and config.axes[axis].enabled]
        if not axes:
            self.api.home_axes([])
        self._homing = True
        try:
            for axis in axes:
                self.api.home_axes([axis])
        finally:
            self._homing = False
            self._parser.position = self._measured_position()
        return ['ok']

    def _set_setting(self, key, value):
        if self.check_mode or self.api.state != State.IDLE or self.pending_tool_change is not None:
            raise GcodeError(8, 'Settings require idle and check mode disabled')
        if not math.isfinite(value):
            raise GcodeError(2, 'Non-finite setting')
        if key in (10, 13, 32, 12):
            if key == 12 and 0 < value <= ARC_TOLERANCE_MAX_MM:
                self._host[key] = value
            elif key == 10 and value in (0, 1):
                self._host[key] = int(value)
            elif key == 13 and value in (0, 1):
                self._host[key] = int(value)
            elif key == 32 and value == 0:
                self._host[key] = 0
            else:
                raise GcodeError(20, 'Unsupported reporting mask or synchronized laser mode')
            self._save()
            return
        candidate = copy.deepcopy(self.api.config)
        self._candidate_setting(candidate, key, value)
        try:
            encode_config(candidate)
        except (EcutError, ValueError, OverflowError) as error:
            raise GcodeError(3, str(error)) from error
        previous = self.api._config
        self.api._config = candidate
        self.api._acc.config = candidate
        if key == 11 or 140 <= key <= 143:
            if self.api._initialized_config is not None:
                snapshot = copy.deepcopy(self.api._initialized_config)
                if key == 11:
                    snapshot['junction_deviation_mm'] = value
                else:
                    snapshot['axes'][key - 140]['deceleration_mm_s2'] = value or None
                self.api._initialized_config = snapshot
            self._native[key] = value
            self._save()
            return
        try:
            self.api.push_config()
        except Exception:
            self.api._config = previous
            self.api._acc.config = previous
            raise
        self._native[key] = value
        self._parser.spindle, self._parser.coolant = 5, set()
        self._parser.position = self._measured_position()
        self._save()

    @staticmethod
    def _candidate_setting(candidate, key, value):
        if key == 11:
            if value < 0:
                raise GcodeError(3, 'Junction deviation must be nonnegative')
            candidate.junction_deviation_mm = value
        elif 100 <= key <= 103:
            if not value.is_integer() or value <= 0:
                raise GcodeError(3, 'Native steps/mm requires a positive integer')
            candidate.axes[key - 100].steps_per_mm = int(value)
        elif 110 <= key <= 113 or 120 <= key <= 123:
            if value <= 0:
                raise GcodeError(3, 'Rate/acceleration must be positive')
            axis = key % 10
            if key < 120:
                candidate.axes[axis].max_velocity_mm_s = value / 60
            else:
                candidate.axes[axis].acceleration_mm_s2 = value
        elif 140 <= key <= 143:
            if value < 0:
                raise GcodeError(3, 'Deceleration must be nonnegative; 0 follows acceleration')
            candidate.axes[key - 140].deceleration_mm_s2 = value or None
        elif 150 <= key <= 153:
            candidate.axes[key - 150].soft_limit_negative_mm = value
        elif 160 <= key <= 163:
            candidate.axes[key - 160].soft_limit_positive_mm = value
        elif 170 <= key <= 173:
            if value not in (0, 1):
                raise GcodeError(3, 'Soft limit switch must be 0 or 1')
            candidate.axes[key - 170].soft_limits_enabled = bool(value)
        elif key in (30, 31):
            setattr(candidate.spindle, 'speed_rpm_max' if key == 30 else 'speed_rpm_min', value)
        else:
            raise GcodeError(20, f'Setting ${key} has no verified equivalent on E-Cut')

    def _report_value(self, value):
        return value / 25.4 if self._host[13] else value

    def _coordinates(self, values):
        return ','.join(f'{self._report_value(v):.3f}' for v in values)

    def status_report(self, state=None):
        if state is None:
            state = {State.IDLE: 'Idle', State.RUNNING: 'Run', State.PAUSED: 'Hold:0',
                     State.ESTOP: 'Alarm', State.ERROR: 'Alarm', State.DISCONNECTED: 'Alarm'}[self.api.state]
        if self._homing and state == 'Run':
            state = 'Home'
        if self.check_mode:
            state = 'Check'
        position, offset = self._measured_position(), self._offset()
        kind = 'MPos' if self._host[10] else 'WPos'
        if kind == 'WPos':
            position = [p - o for p, o in zip(position, offset)]
        override = round(self.api.feed_override * 100)
        speed = getattr(self.api, '_spindle_rpm', 0)
        # the accessory field of the sender protocol: S = spindle CW, C = spindle CCW
        accessory = {'SPINDLE_CW': '|A:S', 'SPINDLE_CCW': '|A:C'}.get(getattr(self.api, '_spindle_direction', None), '')
        pins = self.api.active_pins
        pins = f'|Pn:{pins}' if pins else ''
        # E-Cut status does not provide measured velocity or spindle RPM.
        return (f'<{state}|{kind}:{self._coordinates(position)}|FS:0,{speed:g}{pins}'
                f'|WCO:{self._coordinates(offset)}|Ov:{override},{self._rapid_override},{self._spindle_override}'
                f'{accessory}>')

    def reset(self):
        self._program_epoch += 1
        self.api.reset()
        if self._check_snapshot is not None:
            _, self._wcs, self._stored = self._check_snapshot
        self.check_mode = False
        self._check_snapshot = None
        self._rapid_override = self._spindle_override = 100
        self.api.feed_override = 1
        self._parser = _Parser(position=self._measured_position())
        # The G-code Sender detects the adapter by this banner (its banner check).
        return ["E-Cut host adapter 1.0 ['$' for help]"]

    def realtime(self, code):
        if code == ord('?'):
            return [self.status_report()]
        if code == 0x18:
            return self.reset()
        if self._program_running and (0x90 <= code <= 0x9e or code in (0xa0, 0xa1)):
            raise GcodeError(20, 'Overrides are unavailable during a prepared program')
        if self.check_mode:
            raise GcodeError(8, 'Physical realtime action unavailable in check mode')
        if code == ord('!'):
            self.api.pause()
        elif code == ord('~'):
            if self.pause_reason == 'M6':
                raise GcodeError(20, 'Use confirm_tool_change() for the pending M6 tool')
            self.api.resume()
        elif code == 0x85:
            self._program_epoch += 1
            self.api.request_stop()
        elif code in (0x90, 0x91, 0x92, 0x93, 0x94):
            increments = {0x91: .1, 0x92: -.1, 0x93: .01, 0x94: -.01}
            self.api.feed_override = 1 if code == 0x90 else max(.1, min(2,
                self.api.feed_override + increments[code]))
        elif code in (0x95, 0x96, 0x97):
            self._rapid_override = {0x95: 100, 0x96: 50, 0x97: 25}[code]
        elif code in (0x99, 0x9a, 0x9b, 0x9c, 0x9d):
            if self.pause_reason is not None or self.api.state == State.PAUSED:
                raise GcodeError(20, 'Spindle override refused during operator confirmation/hold')
            previous = self._spindle_override
            changes = {0x9a: 10, 0x9b: -10, 0x9c: 1, 0x9d: -1}
            self._spindle_override = 100 if code == 0x99 else max(10, min(200,
                previous + changes[code]))
            try:
                self.api.spindle_speed(self._effective_speed((self._active_parser or self._parser).speed))
            except Exception:
                self._spindle_override = previous
                raise
        elif code in (0xa0, 0xa1):
            if self.pause_reason is not None or self.api.state == State.PAUSED:
                raise GcodeError(20, 'Coolant toggle refused during operator confirmation/hold')
            name, modal = ('FLOOD', 8) if code == 0xa0 else ('MIST', 7)
            if not self.api.config.outputs[name].enabled:
                raise GcodeError(20, f'{name} output is not configured')
            active = not self.api.read_output(name)
            self.api.set_output(name, active)
            if active:
                (self._active_parser or self._parser).coolant.add(modal)
            else:
                (self._active_parser or self._parser).coolant.discard(modal)
        elif code == 0x84:
            self.api.pause()
            raise GcodeError(20, 'Safety-door parking is not verified; feed held')
        else:
            raise GcodeError(20, f'Realtime 0x{code:02x} hardware effect is not verified')
        return []

