# ##########################################################
# FlatCAM Evo: E-Cut Cartesian motion planner              #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     10/20/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""Pure Cartesian lookahead and lazy 1 ms motion records.

The junction-deviation geometry and reverse/forward speed constraints follow the
published junction-deviation planner model. This is an independent Python implementation; it
contains no third-party firmware code and performs no device operations. All six axes
retain the existing controller's linear millimetre convention. Callers must
isolate rotary or otherwise incompatible units before planning a path.
"""

from __future__ import annotations

from bisect import bisect_right
import math
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Optional

from .ecut_config import EcutBoardConfig
from .ecut_protocol import EcutError


@dataclass(frozen=True)
class LinearMove:
    target_mm: tuple[Optional[float], ...]
    rapid: bool = False
    feed_mm_min: Optional[float] = None
    # Corner at the END of this move. None follows the path setting; a positive value can only
    # tighten it (the smaller one wins); 0 is exact path: stop where the direction changes, pass
    # straight continuations. A path setting of 0 stops at every block whatever this says.
    corner_deviation_mm: Optional[float] = None
    # Backlash offsets (counts per axis, board minus logical) that hold after this move; see apply_backlash.
    offset_counts: Optional[tuple[int, ...]] = None


@dataclass(frozen=True)
class BacklashState:
    steps: tuple[int, ...]     # board counts
    dirs: tuple[int, ...]      # last direction of each axis: 1, -1, or 0 when unknown
    offsets: tuple[int, ...]   # board minus logical counts


def _offsets(values):
    if values is None:
        return (0,) * 6
    if not isinstance(values, Sequence) or len(values) != 6 or any(type(v) is not int for v in values):
        raise EcutError('Backlash offsets require six integer counts')
    return tuple(values)


def backlash_axis_active(axis) -> bool:
    return bool(axis.enabled and axis.backlash_compensation_enabled and axis.backlash_mm > 0)


def backlash_active(config: EcutBoardConfig) -> bool:
    return any(backlash_axis_active(axis) for axis in config.axes)


def backlash_reversal(config: EcutBoardConfig, state: BacklashState, axis: int, sign: int) -> BacklashState:
    """State after axis ``axis`` travels in direction ``sign`` (+1/-1): a reversal adds the backlash counts."""
    ax = config.axes[axis]
    if not backlash_axis_active(ax):
        return state
    dirs, offsets = list(state.dirs), list(state.offsets)
    if dirs[axis] and dirs[axis] != sign:
        offsets[axis] += sign * round(ax.backlash_mm * abs(ax.steps_per_mm * config.interpolation_factor))
    dirs[axis] = sign
    return BacklashState(state.steps, tuple(dirs), tuple(offsets))


def apply_backlash(moves, state: BacklashState, config: EcutBoardConfig):
    """Add a short compensation move before each move that reverses an axis.

    ``moves`` are logical (the tool position). Each returned move carries the offsets that hold after it
    (``offset_counts``), so ``plan_linear_path(..., start_offsets=state.offsets)`` gives board targets
    (logical plus offset) and checks the soft limits on the logical position. The compensation is a
    rapid move of ``backlash_mm`` on the reversing axes only (limited by the axis velocity).
    Returns the new moves and the state after them. The first move of an axis only sets its direction.
    """
    if not backlash_active(config):
        return list(moves), state
    scales = tuple(axis.steps_per_mm * config.interpolation_factor * int(axis.direction) for axis in config.axes)
    logical = [state.steps[i] - state.offsets[i] for i in range(6)]
    now = state
    out = []
    for move in moves:
        if (not isinstance(move, LinearMove) or not isinstance(move.target_mm, Sequence)
                or len(move.target_mm) != 6):
            out.append(move)   # the planner refuses it
            continue
        reversed_axes = {}
        for i, position in enumerate(move.target_mm):
            if position is None or not _finite(position) or not _finite(position * scales[i]):
                continue
            target = round(position * scales[i])
            sign = (target > logical[i]) - (target < logical[i])
            before, logical[i] = logical[i], target
            if not sign:
                continue
            after = backlash_reversal(config, now, i, sign)
            if after.offsets[i] != now.offsets[i]:
                reversed_axes[i] = before
            now = after
        offsets = now.offsets
        if reversed_axes:
            target = [None] * 6
            for i, before in reversed_axes.items():
                target[i] = before / scales[i]
            out.append(LinearMove(tuple(target), True, None, None, offsets))
        out.append(replace(move, offset_counts=offsets))
    return out, BacklashState(tuple(logical[i] + now.offsets[i] for i in range(6)), now.dirs, now.offsets)


@dataclass(frozen=True)
class _Profile:
    length: float
    entry: float
    exit: float
    peak: float
    acceleration: float
    deceleration: float
    t_acc: float
    t_cruise: float
    duration: float

    def fraction(self, t: float) -> float:
        if t <= 0:
            return 0.0
        if t >= self.duration:
            return 1.0
        if t < self.t_acc:
            distance = self.entry * t + 0.5 * self.acceleration * t * t
        elif t < self.t_acc + self.t_cruise:
            distance = ((self.entry + self.peak) * self.t_acc * 0.5
                        + self.peak * (t - self.t_acc))
        else:
            remaining = self.duration - t
            distance = self.length - self.exit * remaining - 0.5 * self.deceleration * remaining * remaining
        return min(1.0, max(0.0, distance / self.length))


@dataclass(frozen=True)
class _Segments(Sequence):
    total: tuple[int, ...]
    profile: _Profile
    count: int = field()
    scales: tuple[float, ...]

    def __len__(self):
        return self.count

    def _cumulative(self, index):
        if index <= 0:
            return (0,) * 6
        if index >= self.count:
            return self.total
        fraction = self.profile.fraction(self.profile.duration * (index / self.count))
        return tuple(round(value * fraction) for value in self.total)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self.count)))
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        before = self._cumulative(index)
        after = self._cumulative(index + 1)
        return tuple(b - a for a, b in zip(before, after))

    def __iter__(self):
        previous = (0,) * 6
        for index in range(1, self.count + 1):
            current = self._cumulative(index)
            yield tuple(b - a for a, b in zip(previous, current))
            previous = current


@dataclass(frozen=True)
class PlannedBlock:
    start_steps: tuple[int, ...]
    target_steps: tuple[int, ...]
    entry_speed_mm_s: float
    exit_speed_mm_s: float
    nominal_speed_mm_s: float
    acceleration_mm_s2: float
    duration_s: float
    segments: Sequence[tuple[int, ...]]
    # Effective corner deviation at this block's end (see LinearMove); None = the path setting.
    corner_deviation_mm: Optional[float] = None

    def iter_segments(self) -> Iterator[tuple[int, ...]]:
        return iter(self.segments)


def _with_checkpoint(items, checkpoint):
    for item in items:
        if checkpoint is not None:
            checkpoint()
        yield item

def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and -sys.float_info.max <= value <= sys.float_info.max


def _record_count(duration, max_step_rate):
    # One count of headroom accommodates the difference between two rounded positions.
    required = max(duration / 0.001, duration * max_step_rate / 32766)
    if not math.isfinite(required):
        raise EcutError('Motion duration exceeds the host record-count range')
    count = max(1, math.ceil(required))
    if count > sys.maxsize:
        raise EcutError('Motion duration exceeds the host record-count range')
    return count


def _profile(length, entry, exit, nominal, acceleration, deceleration=None):
    deceleration = acceleration if deceleration is None else deceleration
    if acceleration == deceleration:
        peak = min(nominal, math.hypot(math.sqrt(acceleration) * math.sqrt(length), entry * math.sqrt(0.5), exit * math.sqrt(0.5)))
    else:
        ratio = min(acceleration, deceleration) / max(acceleration, deceleration)
        entry_weight = 1 / (1 + ratio) if acceleration < deceleration else ratio / (1 + ratio)
        exit_weight = ratio / (1 + ratio) if acceleration < deceleration else 1 / (1 + ratio)
        peak = min(nominal, math.hypot(math.sqrt(min(acceleration, deceleration)) * math.sqrt(2 * length / (1 + ratio)),
                                       entry * math.sqrt(entry_weight), exit * math.sqrt(exit_weight)))
    t_acc = max(0.0, (peak - entry) / acceleration)
    t_dec = max(0.0, (peak - exit) / deceleration)
    cruise_distance = max(0.0, length - (entry + peak) * t_acc * 0.5 - (exit + peak) * t_dec * 0.5)
    t_cruise = cruise_distance / peak
    duration = t_acc + t_cruise + t_dec
    if not all(math.isfinite(value) for value in (peak, t_acc, t_dec, t_cruise, duration)) or duration <= 0:
        raise EcutError('Motion profile is outside the finite host planning range')
    return _Profile(length, entry, exit, peak, acceleration, deceleration, t_acc, t_cruise, duration)


def soft_limit_violation(axis, position, tolerance):
    """Distance (mm) outside the soft limit window widened by the tolerance; 0 inside or without limits."""
    if not (axis.enabled and axis.soft_limits_enabled):
        return 0.0
    return max(0.0, axis.soft_limit_negative_mm - tolerance - position, position - axis.soft_limit_positive_mm - tolerance)


def check_soft_limit_move(axis, start, target, tolerance, name='axis'):
    """A move may end outside the window only when it ends closer to the window than it starts.

    So an axis left outside (a stop, a changed limit) can always move back in.
    """
    after = soft_limit_violation(axis, target, tolerance)
    if after > 0 and after > soft_limit_violation(axis, start, tolerance) + 1e-9:
        raise EcutError(f'Target exceeds the {name} soft limit')


def plan_linear_path(config: EcutBoardConfig, start_steps, moves, *, feed_override=1.0,
                     junction_deviation_mm=0.01, period_s=0.001, checkpoint=None,
                     corner_speed_caps=None, start_offsets=None) -> list[PlannedBlock]:
    """Validate the whole path, then maximize feasible speeds at all junctions.

    ``start_offsets`` (backlash, counts per axis: board minus logical) lets the soft limits run on the
    logical position; a move with ``offset_counts`` sets the offsets of its explicit axes after it.

    First entry and final exit are stopped. ``None`` preserves the actual count
    of that axis. Profiles and targets are immutable; records are generated
    lazily to keep storage proportional to the number of source moves.
    Zero junction deviation selects an exact stop at every block, including straight lines.

    Each block's diagnostic records stretch its duration to whole milliseconds.
    Use ``iter_path_segments`` when streaming adjacent blocks: it shares one
    timebase so short blocks do not independently slow their junctions.
    An optional checkpoint runs throughout preparation; cancellation exceptions propagate.
    """
    if checkpoint is not None:
        checkpoint()
    config.validate()
    tolerance = config.soft_limit_tolerance_mm
    if period_s != 0.001:
        raise EcutError('Motion records require 0.001 s')
    if (not _finite(feed_override) or feed_override <= 0
            or not config.safety.min_feedrate_override_pct <= feed_override * 100 <= config.safety.max_feedrate_override_pct):
        raise EcutError('Feed override is outside the configured safety range')
    if not _finite(junction_deviation_mm) or junction_deviation_mm < 0:
        raise EcutError('Junction deviation must be finite and nonnegative')
    if not isinstance(start_steps, Sequence) or len(start_steps) != 6 or any(type(value) is not int or not -0x80000000 <= value <= 0x7fffffff for value in start_steps):
        raise EcutError('Start position requires six signed 32-bit counts')
    offsets = _offsets(start_offsets)
    scales = tuple(axis.steps_per_mm * config.interpolation_factor * int(axis.direction) for axis in config.axes)
    current = tuple(start_steps)
    geometry = []
    for move in _with_checkpoint(moves, checkpoint):
        if (not isinstance(move, LinearMove) or not isinstance(move.target_mm, Sequence)
                or len(move.target_mm) != 6 or type(move.rapid) is not bool):
            raise EcutError('Linear move requires six targets and a rapid flag')
        move_offsets = offsets if move.offset_counts is None else _offsets(move.offset_counts)
        offsets = list(offsets)
        if ((move.feed_mm_min is not None and (not _finite(move.feed_mm_min) or move.feed_mm_min <= 0))
                or (not move.rapid and move.feed_mm_min is None)):
            raise EcutError('Feed must be finite and positive')
        corner = move.corner_deviation_mm
        if corner is not None and (not _finite(corner) or corner < 0):
            raise EcutError('Corner deviation must be finite and nonnegative')
        target = list(current)
        for i, position in enumerate(move.target_mm):
            if position is None:
                continue
            axis = config.axes[i]
            if not axis.enabled:
                raise EcutError('Explicit target on a disabled axis')
            if not _finite(position):
                raise EcutError('Target must be finite')
            raw = position * scales[i]
            if not math.isfinite(raw) or not -0x80000000 <= raw + move_offsets[i] <= 0x7fffffff:
                raise EcutError('Target exceeds signed 32-bit board position range')
            target[i] = round(raw) + move_offsets[i]
            # the soft limits use the logical position: the board counts without the backlash offset
            check_soft_limit_move(axis, (current[i] - offsets[i]) / scales[i], (target[i] - move_offsets[i]) / scales[i],
                                  tolerance, 'XYZABC'[i])
            offsets[i] = move_offsets[i]
        offsets = tuple(offsets)
        target = tuple(target)
        delta = tuple(target[i] - current[i] for i in range(6))
        if not any(delta):
            continue
        distances = tuple(delta[i] / scales[i] for i in range(6))
        length = math.hypot(*distances)
        unit = tuple(distance / length for distance in distances)
        velocity = min(config.axes[i].max_velocity_mm_s / abs(unit[i]) for i in range(6) if unit[i])
        acceleration = min(config.axes[i].acceleration_mm_s2 / abs(unit[i]) for i in range(6) if unit[i])
        deceleration = min(config.axes[i].effective_deceleration_mm_s2 / abs(unit[i]) for i in range(6) if unit[i])
        if not move.rapid:
            velocity = min(velocity, move.feed_mm_min * (feed_override / 60.0))
        if not math.isfinite(velocity) or velocity <= 0 or not math.isfinite(acceleration) or not math.isfinite(deceleration):
            raise EcutError('Effective feed or acceleration is outside the finite planning range')
        geometry.append((current, target, delta, length, unit, velocity, acceleration, deceleration,
                         None if corner is None else min(corner, junction_deviation_mm)))
        current = target
    if not geometry:
        return []

    # Junction speeds are constrained by geometry and by both neighbouring feeds.
    speeds = [0.0] * (len(geometry) + 1)
    for i in _with_checkpoint(range(1, len(geometry)), checkpoint):
        previous, following = geometry[i - 1], geometry[i]
        dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(previous[4], following[4]))))
        cap = min(previous[5], following[5])
        deviation = junction_deviation_mm if previous[8] is None else previous[8]
        if junction_deviation_mm == 0 or dot <= -0.999999 or (deviation == 0 and dot < 0.999999):
            cap = 0.0
        elif dot < 0.999999:
            turn = tuple(b - a for a, b in zip(previous[4], following[4]))
            turn_length = math.hypot(*turn)
            turn_acceleration = min(min(config.axes[j].acceleration_mm_s2, config.axes[j].effective_deceleration_mm_s2) * turn_length / abs(turn[j])
                                    for j in range(6) if turn[j])
            half_angle = math.sqrt(0.5 * (1 + dot))
            corner_speed = math.sqrt(turn_acceleration * deviation * half_angle / (1 - half_angle))
            cap = min(cap, corner_speed)
        speeds[i] = cap
    for i, cap in (corner_speed_caps or {}).items():   # {block index: max exit speed mm/s}
        if 0 <= i < len(geometry) - 1:
            speeds[i + 1] = min(speeds[i + 1], cap)
    for i in _with_checkpoint(range(len(geometry) - 1, 0, -1), checkpoint):
        length, deceleration = geometry[i][3], geometry[i][7]
        speeds[i] = min(speeds[i], math.hypot(speeds[i + 1], math.sqrt(deceleration) * math.sqrt(2 * length)))
    for i, item in _with_checkpoint(enumerate(geometry), checkpoint):
        speeds[i + 1] = min(speeds[i + 1], math.hypot(speeds[i], math.sqrt(item[6]) * math.sqrt(2 * item[3])))

    blocks = []
    for i, (start, target, delta, length, unit, nominal, acceleration, deceleration, corner) in _with_checkpoint(enumerate(geometry), checkpoint):
        profile = _profile(length, speeds[i], speeds[i + 1], nominal, acceleration, deceleration)
        max_step_rate = (max(abs(value) for value in delta) / length) * profile.peak
        count = _record_count(profile.duration, max_step_rate)
        blocks.append(PlannedBlock(start, target, speeds[i], speeds[i + 1], nominal, acceleration,
                                   count * period_s, _Segments(delta, profile, count, scales), corner))
    return blocks


@dataclass(frozen=True)
class _PathSegments(Sequence):
    blocks: tuple[PlannedBlock, ...]
    ends: tuple[float, ...]
    count: int = field()
    scales: tuple[float, ...]
    limits: tuple[float, ...] = ()  # chord error allowed at each block end; 0 = exact path (no bound)

    def __len__(self):
        return self.count

    def _position(self, index):
        if index <= 0:
            return self.blocks[0].start_steps
        if index >= self.count:
            return self.blocks[-1].target_steps
        t = self.ends[-1] * (index / self.count)
        block_index = min(bisect_right(self.ends, t), len(self.blocks) - 1)
        block = self.blocks[block_index]
        start_time = self.ends[block_index - 1] if block_index else 0.0
        fraction = block.segments.profile.fraction(t - start_time)
        return tuple(round(start + delta * fraction)
                     for start, delta in zip(block.start_steps, block.segments.total))

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self.count)))
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        return tuple(b - a for a, b in zip(self._position(index), self._position(index + 1)))

    def __iter__(self):
        if not self.blocks:
            return
        previous = self.blocks[0].start_steps
        for index in range(1, self.count + 1):
            current = self._position(index)
            yield tuple(b - a for a, b in zip(previous, current))
            previous = current

    def _corner_error(self, checkpoint=None):
        """Worst corner chord error as a multiple of that corner's allowed error."""
        return max(self._corner_errors(checkpoint), default=0.0)

    def _corner_errors(self, checkpoint=None):
        """Chord error of each corner (after block i) as a multiple of its allowed error."""
        ratios = []
        for i in _with_checkpoint(range(len(self.blocks) - 1), checkpoint):
            block, boundary = self.blocks[i], self.ends[i]
            if not self.limits[i]:
                ratios.append(0.0)
                continue
            index = min(self.count - 1, int(boundary / self.ends[-1] * self.count))
            before = self._position(index)
            after = self._position(index + 1)
            chord = tuple((b - a) / scale for a, b, scale in zip(before, after, self.scales))
            offset = tuple((b - a) / scale for a, b, scale in zip(before, block.target_steps, self.scales))
            chord_squared = sum(value * value for value in chord)
            fraction = min(1.0, max(0.0, sum(a * b for a, b in zip(chord, offset)) / chord_squared)) if chord_squared else 0.0
            error = math.hypot(*(point - fraction * direction for point, direction in zip(offset, chord)))
            ratios.append(error / self.limits[i])
        return ratios


def iter_path_segments(blocks, *, junction_deviation_mm=0.01, checkpoint=None, scale_timebase=True):
    """Return a lazy sequence sharing one 1 ms timebase across the path.

    Block profiles are sampled before their individual millisecond rounding.
    Only total path duration is rounded up; every junction therefore shares the
    same time scaling. A record crossing a corner follows the chord between its
    endpoints. Every crossed waypoint's distance from that rounded chord is
    checked before returning; the shared timebase is slowed if needed to keep
    that error within junction deviation. Total record count is available with
    ``len(result)``. This bounds host interpolation, not the motors' response.

    A block's ``corner_deviation_mm`` (from ``LinearMove``) tightens the bound at its own end
    corner only; 0 leaves that corner unbounded because the plan already stops there.

    Zero deviation uses each exact-stop block's individual timebase so every
    corner target is visited exactly. This requires a zero-speed corner plan.
    The optional checkpoint runs during construction, never while emitting records.

    With ``scale_timebase=False`` the timebase is never slowed: the result is
    ``(segments, ratios)``, the chord error of each corner as a multiple of its allowed
    error (``plan_path_segments`` uses it to slow only the corners that exceed it).
    """
    if not _finite(junction_deviation_mm) or junction_deviation_mm < 0:
        raise EcutError('Junction deviation must be finite and nonnegative')
    blocks = tuple(_with_checkpoint(blocks, checkpoint))
    if not blocks:
        empty = _PathSegments((), (), 0, ())
        return empty if scale_timebase else (empty, [])
    if any(not isinstance(block.segments, _Segments) for block in _with_checkpoint(blocks, checkpoint)):
        raise EcutError('Path requires planner-generated blocks')
    scales = blocks[0].segments.scales
    if any(block.segments.scales != scales for block in _with_checkpoint(blocks, checkpoint)):
        raise EcutError('Path blocks require matching axis calibration')
    if any(previous.target_steps != following.start_steps for previous, following in _with_checkpoint(zip(blocks, blocks[1:]), checkpoint)):
        raise EcutError('Path blocks must have contiguous positions')
    if junction_deviation_mm == 0:
        if any(block.exit_speed_mm_s for block in _with_checkpoint(blocks, checkpoint)):
            raise EcutError('Zero deviation sampling requires exact-stop blocks')
        # A zero-width corner cannot be crossed by a chord on a fixed timebase.
        # Quantize stopped runs independently and retain all exact waypoints.
        stopped = _StoppedPathSegments(blocks)
        return stopped if scale_timebase else (stopped, [])
    ends = []
    duration = 0.0
    correction = 0.0
    max_step_rate = 0.0
    for block in _with_checkpoint(blocks, checkpoint):
        profile = block.segments.profile
        increment = profile.duration - correction
        updated = duration + increment
        correction = (updated - duration) - increment
        duration = updated
        if not math.isfinite(duration):
            raise EcutError('Path duration is outside the finite planning range')
        ends.append(duration)
        max_step_rate = max(max_step_rate, (max(abs(value) for value in block.segments.total) / profile.length) * profile.peak)
    count = _record_count(duration, max_step_rate)
    limits = tuple(junction_deviation_mm if block.corner_deviation_mm is None
                   else min(block.corner_deviation_mm, junction_deviation_mm) for block in blocks[:-1])
    while True:
        if checkpoint is not None:
            checkpoint()
        result = _PathSegments(blocks, tuple(ends), count, scales, limits)
        ratios = result._corner_errors(checkpoint=checkpoint)
        if not scale_timebase:
            return result, ratios
        ratio = max(ratios, default=0.0)
        if ratio <= 1:
            return result
        count *= max(2, math.ceil(ratio))
        if count > sys.maxsize:
            raise EcutError('Corner tolerance exceeds the host record-count range')


def plan_path_segments(config: EcutBoardConfig, start_steps, moves, *, feed_override=1.0,
                       junction_deviation_mm=0.01, checkpoint=None, max_rounds=8, start_offsets=None):
    """Plan ``moves`` and return the path records, slowing only the corners that need it.

    A corner whose record chord exceeds its tolerance gets a lower exit speed (the chord error is
    linear in the speed for a fixed 1 ms record) and the path is planned again. If a corner is still
    above tolerance after ``max_rounds``, the whole timebase is slowed instead, so the tolerance holds.
    """
    kwargs = dict(feed_override=feed_override, junction_deviation_mm=junction_deviation_mm, checkpoint=checkpoint,
                  start_offsets=start_offsets)
    caps = {}
    for _ in range(max_rounds):
        blocks = plan_linear_path(config, start_steps, moves, corner_speed_caps=caps, **kwargs)
        segments, ratios = iter_path_segments(blocks, junction_deviation_mm=junction_deviation_mm,
                                              checkpoint=checkpoint, scale_timebase=False)
        over = [(i, ratio) for i, ratio in enumerate(ratios) if ratio > 1]
        if not over:
            return segments
        for i, ratio in over:
            # The error is not exactly linear when the speed changes within a record: at least halve a repeated corner.
            caps[i] = blocks[i].exit_speed_mm_s / (ratio if i not in caps else max(ratio, 2.0))
    return iter_path_segments(blocks, junction_deviation_mm=junction_deviation_mm, checkpoint=checkpoint)


@dataclass(frozen=True)
class _StoppedPathSegments(Sequence):
    blocks: tuple[PlannedBlock, ...]

    def __len__(self):
        return sum(len(block.segments) for block in self.blocks)

    def __getitem__(self, index):
        count = len(self)
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(count)))
        if index < 0:
            index += count
        if not 0 <= index < count:
            raise IndexError(index)
        for block in self.blocks:
            if index < len(block.segments):
                return block.segments[index]
            index -= len(block.segments)
        raise IndexError(index)

    def __iter__(self):
        for block in self.blocks:
            yield from block.segments


def iter_jog_segments(config, start_steps, axis, direction, feed_mm_min, should_continue):
    """Single-axis 1 ms jog, with a stopped endpoint and a releasable braking tail."""
    if type(axis) is not int or not 0 <= axis < 6:
        raise ValueError('Jog axis must be in 0..5')
    if type(direction) is not int or direction not in (0, 1):
        raise ValueError('Jog direction must be 0 (positive) or 1 (negative)')
    config.validate()
    ax = config.axes[axis]
    if not ax.enabled:
        raise EcutError('Cannot jog a disabled axis')
    if feed_mm_min is not None and (not _finite(feed_mm_min) or feed_mm_min <= 0):
        raise EcutError('Jog feed must be finite and positive')
    scale = ax.steps_per_mm * config.interpolation_factor * int(ax.direction)
    sign = 1 if direction == 0 else -1
    raw_sign = sign * (1 if scale > 0 else -1)
    # Choose an inward-rounded machine bound; work offsets never move this fence.
    bound = 0x7fffffff if raw_sign > 0 else -0x80000000
    if ax.soft_limits_enabled:
        edge = ax.soft_limit_positive_mm if sign > 0 else ax.soft_limit_negative_mm
        bound = min(bound, math.floor(edge * scale)) if raw_sign > 0 else max(bound, math.ceil(edge * scale))
    if (bound - start_steps[axis]) * raw_sign <= 0:
        return  # already at or beyond the limit in this direction: the jog does not move
    target = [None] * 6
    target[axis] = bound / scale
    # Reuse the ordinary planner's start/count/configuration validation.
    blocks = plan_linear_path(config, start_steps, [LinearMove(tuple(target), True)])
    if not blocks:
        return
    length = abs((bound - start_steps[axis]) / scale)
    velocity = min(ax.max_velocity_mm_s, 32766 / (abs(scale) * .001))
    if feed_mm_min is not None:
        velocity = min(velocity, feed_mm_min / 60)
    acceleration = ax.acceleration_mm_s2
    deceleration = ax.effective_deceleration_mm_s2
    profile = _profile(length, 0, 0, velocity, acceleration, deceleration)
    elapsed = offset = 0.0
    previous = start_steps[axis]
    braking = False
    while elapsed < profile.duration:
        if not braking and not should_continue():
            travelled = profile.length * profile.fraction(elapsed)
            if elapsed < profile.t_acc:
                speed = profile.entry + acceleration * elapsed
            elif elapsed < profile.t_acc + profile.t_cruise:
                speed = profile.peak
            else:
                speed = max(0.0, profile.exit + deceleration * (profile.duration - elapsed))
            if speed == 0:
                return
            offset += travelled
            profile = _profile(speed * speed / (2 * deceleration), speed, 0, speed, acceleration, deceleration)
            elapsed = 0.0
            braking = True
        elapsed = min(profile.duration, elapsed + .001)
        travelled = min(length, offset + profile.length * profile.fraction(elapsed))
        raw = start_steps[axis] + round(sign * scale * travelled)
        raw = min(raw, bound) if raw_sign > 0 else max(raw, bound)
        delta = [0] * 6
        delta[axis] = raw - previous
        if not -32767 <= delta[axis] <= 32767:
            raise EcutError('Jog record exceeds the verified signed 16-bit range')
        yield tuple(delta)
        previous = raw
