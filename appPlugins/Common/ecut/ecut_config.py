# ##########################################################
# FlatCAM Evo: E-Cut Board Configuration                   #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/19/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Configuration for E-Cut USB motion controller.

Covers axis setup, I/O mapping, homing, probing, limits, spindle, and safety.
Serializable to/from JSON for persistence.

Recovered from e-Cut.dll disassembly + Mach3 SDK struct layouts.
See docs/mach3/Ecut_Recovered_Protocol/FINDINGS.md.
"""
from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from pathlib import Path
from typing import Optional

from .ecut_protocol import AXIS_NAMES, INTERPOLATION_FACTORS, EcutError

log = logging.getLogger(__name__)

NATIVE_INPUT_COUNT = 16
NATIVE_OUTPUT_COUNT = 8
LABEL_MAX_LENGTH = 32
SOFT_LIMIT_TOLERANCE_MAX_MM = 1.0
BACKLASH_MAX_MM = 1.0


class EcutConfigError(EcutError, ValueError):
    """Invalid configuration value."""


def check_interpolation_index(value) -> int:
    """interpolation_index must be an int 0..9 (index into INTERPOLATION_FACTORS)."""
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value < len(INTERPOLATION_FACTORS)):
        raise EcutConfigError(
            f'interpolation_index must be an integer 0-{len(INTERPOLATION_FACTORS) - 1}, got {value!r}')
    return value


class SignalPolarity(IntEnum):
    ACTIVE_LOW = 0
    ACTIVE_HIGH = 1


class AxisDirection(IntEnum):
    FORWARD = 1
    REVERSE = -1


class HomeSequence(IntEnum):
    FIRST = 0
    SECOND = 1
    THIRD = 2


class ProbeMode(IntEnum):
    TOWARD_WORKPIECE = 0
    AWAY_FROM_WORKPIECE = 1


class SpindleControl(IntEnum):
    NONE = 0
    PWM = 1
    STEP_DIR = 2
    ANALOG = 3


@dataclass
class InputSignal:
    """One physical input signal configuration.

    Maps a logical signal (limit, home, probe, etc.) to a physical input pin.
    The protocol exposes 16 native inputs and an optional 16-bit expansion bank.
    """
    enabled: bool = False
    pin: int = 0  # 0-15 native, 16-31 expansion
    polarity: SignalPolarity = SignalPolarity.ACTIVE_LOW
    debounce_ms: int = 20
    port: int = 1

    def to_dict(self) -> dict:
        return {'enabled': self.enabled, 'pin': self.pin,
                'polarity': int(self.polarity), 'debounce_ms': self.debounce_ms, 'port': self.port}

    @classmethod
    def from_dict(cls, d: dict) -> InputSignal:
        return cls(enabled=d.get('enabled', False), pin=d.get('pin', 0),
                   polarity=SignalPolarity(d.get('polarity', 0)),
                   debounce_ms=d.get('debounce_ms', 20), port=d.get('port', 1))


@dataclass
class OutputSignal:
    """One physical output signal configuration."""
    enabled: bool = False
    pin: int = 0
    inverted: bool = False

    def to_dict(self) -> dict:
        return {'enabled': self.enabled, 'pin': self.pin, 'inverted': self.inverted}

    @classmethod
    def from_dict(cls, d: dict) -> OutputSignal:
        return cls(enabled=d.get('enabled', False), pin=d.get('pin', 0),
                   inverted=d.get('inverted', False))


@dataclass
class AxisConfig:
    """Per-axis configuration."""
    enabled: bool = True
    steps_per_mm: float = 800.0
    direction: AxisDirection = AxisDirection.FORWARD
    max_velocity_mm_s: float = 50.0
    acceleration_mm_s2: float = 200.0
    soft_limit_positive_mm: float = 300.0
    soft_limit_negative_mm: float = 0.0
    soft_limits_enabled: bool = False
    backlash_mm: float = 0.0
    backlash_compensation_enabled: bool = False
    step_pin: int = 0
    dir_pin: int = 0
    step_port: int = 1
    dir_port: int = 1
    step_active_low: bool = False
    dir_active_low: bool = False
    work_offset_mm: float = 0.0
    deceleration_mm_s2: Optional[float] = None

    @property
    def effective_deceleration_mm_s2(self) -> float:
        return self.acceleration_mm_s2 if self.deceleration_mm_s2 is None else self.deceleration_mm_s2

    def to_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'steps_per_mm': self.steps_per_mm,
            'direction': int(self.direction),
            'max_velocity_mm_s': self.max_velocity_mm_s,
            'acceleration_mm_s2': self.acceleration_mm_s2,
            'deceleration_mm_s2': self.deceleration_mm_s2,
            'soft_limit_positive_mm': self.soft_limit_positive_mm,
            'soft_limit_negative_mm': self.soft_limit_negative_mm,
            'soft_limits_enabled': self.soft_limits_enabled,
            'backlash_mm': self.backlash_mm,
            'backlash_compensation_enabled': self.backlash_compensation_enabled,
            **{key: getattr(self, key) for key in ('step_pin', 'dir_pin', 'step_port', 'dir_port',
                                                 'step_active_low', 'dir_active_low', 'work_offset_mm')},
        }

    @classmethod
    def from_dict(cls, d: dict) -> AxisConfig:
        kwargs = {}
        for k in ('enabled', 'steps_per_mm', 'max_velocity_mm_s',
                  'acceleration_mm_s2', 'deceleration_mm_s2', 'soft_limit_positive_mm',
                  'soft_limit_negative_mm', 'soft_limits_enabled',
                  'backlash_mm', 'backlash_compensation_enabled', 'step_pin', 'dir_pin', 'step_port',
                  'dir_port', 'step_active_low', 'dir_active_low', 'work_offset_mm'):
            if k in d:
                kwargs[k] = d[k]
        if 'direction' in d:
            kwargs['direction'] = AxisDirection(d['direction'])
        return cls(**kwargs)


@dataclass
class HomingConfig:
    """Per-axis homing configuration."""
    enabled: bool = False
    sequence: HomeSequence = HomeSequence.FIRST
    direction: AxisDirection = AxisDirection.FORWARD
    fast_velocity_mm_s: float = 10.0
    slow_velocity_mm_s: float = 2.0
    home_offset_mm: float = 0.0
    use_index_pulse: bool = False
    hw_limit_when_homing: bool = True
    sw_limit_when_homing: bool = False
    max_travel_mm: float = 300.0
    retract_mm: float = 2.0

    def to_dict(self) -> dict:
        return {
            'enabled': self.enabled,
            'sequence': int(self.sequence),
            'direction': int(self.direction),
            'fast_velocity_mm_s': self.fast_velocity_mm_s,
            'slow_velocity_mm_s': self.slow_velocity_mm_s,
            'home_offset_mm': self.home_offset_mm,
            'use_index_pulse': self.use_index_pulse,
            'hw_limit_when_homing': self.hw_limit_when_homing,
            'sw_limit_when_homing': self.sw_limit_when_homing,
            'max_travel_mm': self.max_travel_mm,
            'retract_mm': self.retract_mm,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HomingConfig:
        return cls(
            enabled=d.get('enabled', False),
            sequence=HomeSequence(d.get('sequence', 0)),
            direction=AxisDirection(d.get('direction', 1)),
            fast_velocity_mm_s=d.get('fast_velocity_mm_s', 10.0),
            slow_velocity_mm_s=d.get('slow_velocity_mm_s', 2.0),
            home_offset_mm=d.get('home_offset_mm', 0.0),
            use_index_pulse=d.get('use_index_pulse', False),
            hw_limit_when_homing=d.get('hw_limit_when_homing', True),
            sw_limit_when_homing=d.get('sw_limit_when_homing', False),
            max_travel_mm=d.get('max_travel_mm', 300.0),
            retract_mm=d.get('retract_mm', 2.0),
        )


@dataclass
class ProbeConfig:
    """Probe cycle configuration."""
    mode: ProbeMode = ProbeMode.TOWARD_WORKPIECE
    fast_velocity_mm_s: float = 5.0
    slow_velocity_mm_s: float = 0.5
    max_travel_mm: float = 50.0
    retract_mm: float = 1.0
    retract_velocity_mm_s: float = 10.0
    retracted_error_check: bool = True

    def to_dict(self) -> dict:
        return {
            'mode': int(self.mode),
            'fast_velocity_mm_s': self.fast_velocity_mm_s,
            'slow_velocity_mm_s': self.slow_velocity_mm_s,
            'max_travel_mm': self.max_travel_mm,
            'retract_mm': self.retract_mm,
            'retract_velocity_mm_s': self.retract_velocity_mm_s,
            'retracted_error_check': self.retracted_error_check,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProbeConfig:
        return cls(
            mode=ProbeMode(d.get('mode', 0)),
            fast_velocity_mm_s=d.get('fast_velocity_mm_s', 5.0),
            slow_velocity_mm_s=d.get('slow_velocity_mm_s', 0.5),
            max_travel_mm=d.get('max_travel_mm', 50.0),
            retract_mm=d.get('retract_mm', 1.0),
            retract_velocity_mm_s=d.get('retract_velocity_mm_s', 10.0),
            retracted_error_check=d.get('retracted_error_check', True),
        )


@dataclass
class SpindleConfig:
    """Spindle control configuration."""
    control: SpindleControl = SpindleControl.NONE
    pwm_frequency_hz: int = 1000
    pwm_period_counts: int = 1000
    pulses_per_revolution: int = 0
    pulse_width_us: int = 10
    delay_between_pulse_and_dir_us: int = 100
    speed_rpm_min: float = 0.0
    speed_rpm_max: float = 24000.0
    dwell_on_seconds: float = 0.0
    dwell_off_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            'control': int(self.control),
            'pwm_frequency_hz': self.pwm_frequency_hz,
            'pwm_period_counts': self.pwm_period_counts,
            'pulses_per_revolution': self.pulses_per_revolution,
            'pulse_width_us': self.pulse_width_us,
            'delay_between_pulse_and_dir_us': self.delay_between_pulse_and_dir_us,
            'speed_rpm_min': self.speed_rpm_min,
            'speed_rpm_max': self.speed_rpm_max,
            'dwell_on_seconds': self.dwell_on_seconds,
            'dwell_off_seconds': self.dwell_off_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SpindleConfig:
        return cls(
            control=SpindleControl(d.get('control', 0)),
            pwm_frequency_hz=d.get('pwm_frequency_hz', 1000),
            pwm_period_counts=d.get('pwm_period_counts', 1000),
            pulses_per_revolution=d.get('pulses_per_revolution', 0),
            pulse_width_us=d.get('pulse_width_us', 10),
            delay_between_pulse_and_dir_us=d.get('delay_between_pulse_and_dir_us', 100),
            speed_rpm_min=d.get('speed_rpm_min', 0.0),
            speed_rpm_max=d.get('speed_rpm_max', 24000.0),
            dwell_on_seconds=d.get('dwell_on_seconds', 0.0),
            dwell_off_seconds=d.get('dwell_off_seconds', 0.0),
        )


@dataclass
class MPGConfig:
    """Manual Pulse Generator (handwheel) configuration."""
    enabled: bool = False
    method: int = 0
    reverse: bool = False
    port_select: int = 7
    x1_pin: int = 0
    x10_pin: int = 0
    x100_pin: int = 0
    lock_pin: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> MPGConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class UnverifiedConfig:
    """Switches for features that are not verified on hardware (all off by default)."""
    homing: bool = False
    probing: bool = False
    backlash: bool = False
    mpg: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> UnverifiedConfig:
        return cls(**{k: bool(v) for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class SafetyConfig:
    """Safety and interlock configuration."""
    estop_input_enabled: bool = False
    estop_input_pin: int = 0
    estop_polarity: SignalPolarity = SignalPolarity.ACTIVE_LOW
    limits_re_engage_after_estop: bool = True
    feed_hold_on_door_open: bool = False
    restore_outputs_after_m0: bool = True
    max_feedrate_override_pct: int = 200
    min_feedrate_override_pct: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d['estop_polarity'] = int(self.estop_polarity)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SafetyConfig:
        return cls(
            estop_input_enabled=d.get('estop_input_enabled', False),
            estop_input_pin=d.get('estop_input_pin', 0),
            estop_polarity=SignalPolarity(d.get('estop_polarity', 0)),
            limits_re_engage_after_estop=d.get('limits_re_engage_after_estop', True),
            feed_hold_on_door_open=d.get('feed_hold_on_door_open', False),
            restore_outputs_after_m0=d.get('restore_outputs_after_m0', True),
            max_feedrate_override_pct=d.get('max_feedrate_override_pct', 200),
            min_feedrate_override_pct=d.get('min_feedrate_override_pct', 0),
        )


INPUT_SIGNAL_NAMES = (
    'X_LIMIT_PLUS', 'X_LIMIT_MINUS', 'X_HOME',
    'Y_LIMIT_PLUS', 'Y_LIMIT_MINUS', 'Y_HOME',
    'Z_LIMIT_PLUS', 'Z_LIMIT_MINUS', 'Z_HOME',
    'A_LIMIT_PLUS', 'A_LIMIT_MINUS', 'A_HOME',
    'B_LIMIT_PLUS', 'B_LIMIT_MINUS', 'B_HOME',
    'C_LIMIT_PLUS', 'C_LIMIT_MINUS', 'C_HOME',
    'PROBE', 'ESTOP', 'DOOR',
    'FEED_HOLD', 'CYCLE_START', 'SINGLE_STEP',
    'INPUT_1', 'INPUT_2', 'INPUT_3', 'INPUT_4', 'SPINDLE_INDEX', 'LIMIT_OVERRIDE',
    'THC_ON', 'THC_UP', 'THC_DOWN',
)

OUTPUT_SIGNAL_NAMES = (
    'STEP_X', 'DIR_X', 'STEP_Y', 'DIR_Y', 'STEP_Z', 'DIR_Z',
    'STEP_A', 'DIR_A', 'STEP_B', 'DIR_B', 'STEP_C', 'DIR_C',
    'SPINDLE_CW', 'SPINDLE_CCW', 'SPINDLE_PWM',
    'FLOOD', 'MIST',
    'OUTPUT_0', 'OUTPUT_1', 'OUTPUT_2', 'OUTPUT_3',
    'OUTPUT_4', 'OUTPUT_5', 'OUTPUT_6', 'OUTPUT_7',
)


@dataclass
class EcutBoardConfig:
    """Host and board configuration recovered from the plugin serializers.

    These fields retain the existing config format. Unverified hardware options are refused:
    - 6 axes (X, Y, Z, A, B, C) with steps/mm, direction, limits
    - Input signals (limits, home, probe, estop, etc.)
    - Output signals (step/dir, spindle, coolant, generic)
    - Homing per axis
    - Probe cycle
    - Spindle control
    - MPG handwheel
    - Safety interlocks
    - Board-level settings (VID/PID, interpolation, USB)
    """
    vid: int = 0x0483
    pid: int = 0x5750
    interpolation_index: int = 5
    units_mm: bool = True

    step_functions: list[int] = field(default_factory=lambda: list(range(8)))
    dir_functions: list[int] = field(default_factory=lambda: list(range(8, 16)))
    # Seventh CD entry is the spindle channel, in the same seven-field order as AxisConfig IO.
    homing_mode: int = 2
    homing_count: int = 2
    homing_ratio_pct: int = 50
    spindle_axis_io: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0, 0, 1])

    axes: list[AxisConfig] = field(default_factory=lambda: [AxisConfig(enabled=i != 3) for i in range(6)])  # A is off by default
    homing: list[HomingConfig] = field(default_factory=lambda: [HomingConfig() for _ in range(6)])

    inputs: dict[str, InputSignal] = field(default_factory=lambda: {
        name: InputSignal() for name in INPUT_SIGNAL_NAMES
    })
    outputs: dict[str, OutputSignal] = field(default_factory=lambda: {
        name: OutputSignal() for name in OUTPUT_SIGNAL_NAMES
    })

    probe: ProbeConfig = field(default_factory=ProbeConfig)
    spindle: SpindleConfig = field(default_factory=SpindleConfig)
    mpg: MPGConfig = field(default_factory=MPGConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    unverified: UnverifiedConfig = field(default_factory=UnverifiedConfig)


    # Captures and isolated motion tests establish 1 ms per record on firmware 2.4.8.288.
    # Other periods are refused before motion submission.
    motion_period_s: Optional[float] = 0.001
    junction_deviation_mm: float = 0.01
    # The board moves in whole counts: a target can land this far outside a soft limit (mm).
    soft_limit_tolerance_mm: float = 0.05
    # Operator names of the 16 native inputs I0..I15 and the 8 outputs O0..O7; empty = no name.
    input_labels: list[str] = field(default_factory=lambda: [''] * NATIVE_INPUT_COUNT)
    output_labels: list[str] = field(default_factory=lambda: [''] * NATIVE_OUTPUT_COUNT)

    def __post_init__(self):
        check_interpolation_index(self.interpolation_index)

    def to_dict(self) -> dict:
        return {
            'vid': self.vid,
            'pid': self.pid,
            'interpolation_index': self.interpolation_index,
            'units_mm': self.units_mm,
            'step_functions': list(self.step_functions),
            'dir_functions': list(self.dir_functions),
            'spindle_axis_io': list(self.spindle_axis_io),
            'homing_mode': self.homing_mode,
            'homing_count': self.homing_count,
            'homing_ratio_pct': self.homing_ratio_pct,
            'axes': [a.to_dict() for a in self.axes],
            'homing': [h.to_dict() for h in self.homing],
            'inputs': {k: v.to_dict() for k, v in self.inputs.items()},
            'outputs': {k: v.to_dict() for k, v in self.outputs.items()},
            'probe': self.probe.to_dict(),
            'spindle': self.spindle.to_dict(),
            'mpg': self.mpg.to_dict(),
            'safety': self.safety.to_dict(),
            'unverified': self.unverified.to_dict(),
            'motion_period_s': self.motion_period_s,
            'junction_deviation_mm': self.junction_deviation_mm,
            'soft_limit_tolerance_mm': self.soft_limit_tolerance_mm,
            'input_labels': list(self.input_labels),
            'output_labels': list(self.output_labels),
        }

    @classmethod
    def from_dict(cls, d: dict) -> EcutBoardConfig:
        cfg = cls()
        cfg.vid = d.get('vid', cfg.vid)
        cfg.pid = d.get('pid', cfg.pid)
        cfg.interpolation_index = check_interpolation_index(d.get('interpolation_index', cfg.interpolation_index))
        cfg.units_mm = d.get('units_mm', cfg.units_mm)
        for key in ('homing_mode', 'homing_count', 'homing_ratio_pct'):
            if key in d:
                setattr(cfg, key, d[key])
        for key in ('step_functions', 'dir_functions', 'spindle_axis_io'):
            if key in d:
                setattr(cfg, key, list(d[key]))
        cfg.motion_period_s = d.get('motion_period_s', cfg.motion_period_s)
        cfg.junction_deviation_mm = d.get('junction_deviation_mm', cfg.junction_deviation_mm)
        cfg.soft_limit_tolerance_mm = d.get('soft_limit_tolerance_mm', cfg.soft_limit_tolerance_mm)
        for key in ('input_labels', 'output_labels'):
            if key in d:
                setattr(cfg, key, list(d[key]))

        if 'axes' in d:
            cfg.axes = [AxisConfig.from_dict(a) for a in d['axes']]
        if 'homing' in d:
            cfg.homing = [HomingConfig.from_dict(h) for h in d['homing']]
        if 'inputs' in d:
            cfg.inputs = {k: InputSignal.from_dict(v) for k, v in d['inputs'].items()}
        if 'outputs' in d:
            cfg.outputs = {k: OutputSignal.from_dict(v) for k, v in d['outputs'].items()}
        if 'probe' in d:
            cfg.probe = ProbeConfig.from_dict(d['probe'])
        if 'spindle' in d:
            cfg.spindle = SpindleConfig.from_dict(d['spindle'])
        if 'mpg' in d:
            cfg.mpg = MPGConfig.from_dict(d['mpg'])
        if 'safety' in d:
            cfg.safety = SafetyConfig.from_dict(d['safety'])
        if 'unverified' in d:
            cfg.unverified = UnverifiedConfig.from_dict(d['unverified'])

        return cfg

    def validate(self) -> None:
        if len(self.axes) != 6 or len(self.homing) != 6:
            raise EcutConfigError('Configuration requires six axis and homing entries')
        check_interpolation_index(self.interpolation_index)
        if self.motion_period_s != 0.001:
            raise EcutConfigError('Motion records require 0.001 s')
        if (not isinstance(self.junction_deviation_mm, (int, float))
                or isinstance(self.junction_deviation_mm, bool)
                or not math.isfinite(self.junction_deviation_mm) or self.junction_deviation_mm < 0):
            raise EcutConfigError('Junction deviation must be finite and nonnegative')
        if (not isinstance(self.soft_limit_tolerance_mm, (int, float)) or isinstance(self.soft_limit_tolerance_mm, bool)
                or not 0 <= self.soft_limit_tolerance_mm <= SOFT_LIMIT_TOLERANCE_MAX_MM):
            raise EcutConfigError(f'Soft limit tolerance must be 0..{SOFT_LIMIT_TOLERANCE_MAX_MM} mm')
        for key, count in (('input_labels', NATIVE_INPUT_COUNT), ('output_labels', NATIVE_OUTPUT_COUNT)):
            labels = getattr(self, key)
            if (not isinstance(labels, list) or len(labels) != count
                    or any(not isinstance(label, str) or len(label) > LABEL_MAX_LENGTH
                           or not label.isprintable() for label in labels)):
                raise EcutConfigError(f'{key} requires {count} printable names of at most {LABEL_MAX_LENGTH} characters')
        for ax in self.axes:
            if ax.deceleration_mm_s2 is not None and (
                    not isinstance(ax.deceleration_mm_s2, (int, float)) or isinstance(ax.deceleration_mm_s2, bool)
                    or not 0 < ax.deceleration_mm_s2 <= sys.float_info.max):
                raise EcutConfigError('Axis deceleration must be finite and positive')
            if (not math.isfinite(ax.steps_per_mm) or not 1 <= ax.steps_per_mm <= 0x7fffffff
                    or int(ax.steps_per_mm) != ax.steps_per_mm
                    or ax.direction not in (AxisDirection.FORWARD, AxisDirection.REVERSE)
                    or not math.isfinite(ax.max_velocity_mm_s) or ax.max_velocity_mm_s <= 0
                    or not math.isfinite(ax.acceleration_mm_s2) or ax.acceleration_mm_s2 <= 0
                    or not math.isfinite(ax.work_offset_mm)):
                raise EcutConfigError('Invalid axis calibration, direction, velocity, acceleration or offset')
            if ax.soft_limits_enabled and (not math.isfinite(ax.soft_limit_negative_mm)
                    or not math.isfinite(ax.soft_limit_positive_mm)
                    or ax.soft_limit_negative_mm >= ax.soft_limit_positive_mm):
                raise EcutConfigError('Invalid soft limit interval')
            if ax.backlash_compensation_enabled or ax.backlash_mm:
                if not self.unverified.backlash:
                    raise EcutConfigError('Backlash compensation is not yet verified')
                if (type(ax.backlash_compensation_enabled) is not bool or isinstance(ax.backlash_mm, bool)
                        or not isinstance(ax.backlash_mm, (int, float))
                        or not math.isfinite(ax.backlash_mm) or not 0 <= ax.backlash_mm <= BACKLASH_MAX_MM):
                    raise EcutConfigError(f'Backlash must be 0..{BACKLASH_MAX_MM} mm')
        for sig in self.inputs.values():
            if (type(sig.pin) is not int or not 0 <= sig.pin < 32 or sig.port not in (0, 1)
                    or sig.polarity not in (SignalPolarity.ACTIVE_LOW, SignalPolarity.ACTIVE_HIGH)
                    or type(sig.debounce_ms) is not int or not 0 <= sig.debounce_ms <= 10000):
                raise EcutConfigError('Invalid input pin, port, polarity or debounce')
        for sig in self.outputs.values():
            if type(sig.pin) is not int or not 0 <= sig.pin < 16:
                raise EcutConfigError('Output pin must be 0..15')
        polarities = {}
        for name, sig in self.outputs.items():
            if sig.enabled and not name.startswith(('STEP_', 'DIR_')):
                if sig.pin in polarities and polarities[sig.pin] != sig.inverted:
                    raise EcutConfigError('Shared output pins require the same polarity')
                polarities[sig.pin] = sig.inverted
        spindle = self.spindle
        values = (spindle.speed_rpm_min, spindle.speed_rpm_max,
                  spindle.dwell_on_seconds, spindle.dwell_off_seconds)
        if (spindle.control not in tuple(SpindleControl)
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) for value in values)
                or not 0 <= spindle.speed_rpm_min < spindle.speed_rpm_max
                or spindle.dwell_on_seconds < 0 or spindle.dwell_off_seconds < 0
                or type(spindle.pwm_period_counts) is not int
                or not 1 <= spindle.pwm_period_counts <= 0xffff
                or type(spindle.pulses_per_revolution) is not int
                or not 0 <= spindle.pulses_per_revolution <= 0x7fffffff):
            raise EcutConfigError('Invalid spindle RPM range, dwell, period or feedback calibration')
        if spindle.control == SpindleControl.PWM and spindle.pwm_frequency_hz != 1000:
            raise EcutConfigError('PWM frequency conversion is not verified; use raw pwm_period_counts')
        if spindle.control == SpindleControl.PWM:
            for name in ('SPINDLE_CW', 'SPINDLE_CCW'):
                sig = self.outputs.get(name)
                if sig is not None and sig.enabled and any(
                        other != name and alias.enabled and alias.pin == sig.pin
                        and not other.startswith(('STEP_', 'DIR_'))
                        for other, alias in self.outputs.items()):
                    raise EcutConfigError('Spindle run outputs require exclusive pins')
        safety = self.safety
        if (type(safety.restore_outputs_after_m0) is not bool
                or type(safety.estop_input_pin) is not int or not 0 <= safety.estop_input_pin < 32
                or safety.estop_polarity not in (SignalPolarity.ACTIVE_LOW, SignalPolarity.ACTIVE_HIGH)
                or not 0 <= safety.min_feedrate_override_pct <= safety.max_feedrate_override_pct <= 250):
            raise EcutConfigError('Invalid safety input or override range')

    def save(self, path: str | Path) -> None:
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # write a temporary file, then replace: a crash cannot leave half a profile
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
        log.info('Config saved to %s', path)

    @classmethod
    def load(cls, path: str | Path) -> EcutBoardConfig:
        path = Path(path)
        with open(path, encoding='utf-8') as f:
            cfg = cls.from_dict(json.load(f))
        cfg.validate()
        return cfg

    @property
    def interpolation_factor(self) -> int:
        return INTERPOLATION_FACTORS[check_interpolation_index(self.interpolation_index)]

    def summary(self) -> str:
        lines = [
            "E-Cut Board Configuration",
            f"  VID/PID: {self.vid:04X}/{self.pid:04X}",
            f"  Interpolation: {self.interpolation_factor}x (index {self.interpolation_index})",
            f"  Units: {'mm' if self.units_mm else 'inch'}",
            "",
            "Axes:",
        ]
        for i, (name, axis) in enumerate(zip(AXIS_NAMES, self.axes)):
            if axis.enabled:
                lines.append(
                    f"  {name}: {axis.steps_per_mm:.1f} steps/mm, "
                    f"dir={'fwd' if axis.direction == 1 else 'rev'}, "
                    f"max={axis.max_velocity_mm_s:.1f} mm/s, "
                    f"acc={axis.acceleration_mm_s2:.1f} mm/s²"
                )
        enabled_inputs = [k for k, v in self.inputs.items() if v.enabled]
        lines.append(f"\nInputs ({len(enabled_inputs)} enabled): {', '.join(enabled_inputs)}")
        enabled_outputs = [k for k, v in self.outputs.items() if v.enabled]
        lines.append(f"Outputs ({len(enabled_outputs)} enabled): {', '.join(enabled_outputs)}")
        if self.probe.mode is not None:
            lines.append(f"Probe: mode={self.probe.mode.name}, fast={self.probe.fast_velocity_mm_s} mm/s")
        if self.spindle.control != SpindleControl.NONE:
            lines.append(f"Spindle: {self.spindle.control.name}, {self.spindle.speed_rpm_min}-{self.spindle.speed_rpm_max} RPM")
        return '\n'.join(lines)
