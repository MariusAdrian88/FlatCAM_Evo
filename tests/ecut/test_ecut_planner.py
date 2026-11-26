"""Offline lookahead planning; no transport or hardware is involved."""

import importlib
import math
from dataclasses import FrozenInstanceError

import pytest

from appPlugins.Common.ecut.ecut_config import EcutBoardConfig, AxisDirection
from appPlugins.Common.ecut.ecut_protocol import EcutError


@pytest.fixture
def planner():
    return importlib.import_module('appPlugins.Common.ecut.ecut_planner')


def config():
    cfg = EcutBoardConfig(interpolation_index=0)
    for axis in cfg.axes:
        axis.steps_per_mm = 100
        axis.max_velocity_mm_s = 100
        axis.acceleration_mm_s2 = 100
    return cfg


def move(planner, x=None, y=None, z=None, feed=6000, rapid=False):
    return planner.LinearMove((x, y, z, None, None, None), rapid, feed)


def totals(block):
    return tuple(sum(segment[i] for segment in block.segments) for i in range(6))


def test_collinear_blocks_keep_speed_and_avoid_per_move_stops(planner):
    cfg = config()
    moves = [move(planner, x=x) for x in (10, 20, 30)]
    blocks = planner.plan_linear_path(cfg, (0,) * 6, moves)
    stopped = [planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=10)])[0]] * 3
    assert len(blocks) == 3
    assert blocks[0].entry_speed_mm_s == 0
    assert blocks[-1].exit_speed_mm_s == 0
    assert blocks[0].exit_speed_mm_s == pytest.approx(math.sqrt(2000))
    assert blocks[0].exit_speed_mm_s == blocks[1].entry_speed_mm_s
    assert blocks[1].exit_speed_mm_s == blocks[2].entry_speed_mm_s
    assert sum(block.duration_s for block in blocks) < sum(block.duration_s for block in stopped) * 0.7


def test_right_angle_obeys_junction_deviation(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=10), move(planner, x=10, y=10)])
    # Turning acceleration is projected onto normalized (-1, 1): 100 * sqrt(2).
    expected = math.sqrt(100 * math.sqrt(2) * 0.01 * math.sqrt(0.5) / (1 - math.sqrt(0.5)))
    assert blocks[0].exit_speed_mm_s == pytest.approx(expected)
    assert blocks[1].entry_speed_mm_s == pytest.approx(expected)


def test_reversal_and_zero_junction_deviation_stop(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=10), move(planner, x=0)])
    assert blocks[0].exit_speed_mm_s == blocks[1].entry_speed_mm_s == 0
    corner = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=10), move(planner, y=10)], junction_deviation_mm=0)
    assert corner[0].exit_speed_mm_s == 0


def test_short_final_segment_brakes_previous_blocks(planner):
    cfg = config()
    blocks = planner.plan_linear_path(cfg, (0,) * 6,
                                      [move(planner, x=10), move(planner, x=10.1), move(planner, x=10.11)])
    assert blocks[-1].entry_speed_mm_s == pytest.approx(math.sqrt(2))
    assert blocks[-2].entry_speed_mm_s == pytest.approx(math.sqrt(22))
    for block in blocks:
        length = abs(block.target_steps[0] - block.start_steps[0]) / 200
        assert block.exit_speed_mm_s ** 2 <= block.entry_speed_mm_s ** 2 + 2 * block.acceleration_mm_s2 * length + 1e-8
        assert block.entry_speed_mm_s ** 2 <= block.exit_speed_mm_s ** 2 + 2 * block.acceleration_mm_s2 * length + 1e-8


def test_projected_axis_limits_and_feed_override(planner):
    cfg = config()
    cfg.axes[1].max_velocity_mm_s = 5
    cfg.axes[1].acceleration_mm_s2 = 10
    block = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=10, y=10)])[0]
    assert block.nominal_speed_mm_s == pytest.approx(5 * math.sqrt(2))
    assert block.acceleration_mm_s2 == pytest.approx(10 * math.sqrt(2))
    normal = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=100, feed=600)])[0]
    slow = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=100, feed=600)], feed_override=0.5)[0]
    rapid = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=100, rapid=True)], feed_override=0.5)[0]
    assert normal.nominal_speed_mm_s == 10
    assert slow.nominal_speed_mm_s == 5
    assert rapid.nominal_speed_mm_s == 100
    assert slow.duration_s > normal.duration_s


def test_exact_signed_targets_and_preserved_axes(planner):
    cfg = config()
    cfg.axes[1].direction = AxisDirection.REVERSE
    start = (200, -400, 600, 0, 0, 0)
    blocks = planner.plan_linear_path(cfg, start,
                                      [move(planner, x=-0.13, y=1.37), move(planner, x=-0.23, z=-0.51)])
    assert blocks[0].start_steps == start
    assert blocks[0].target_steps == (-26, -274, 600, 0, 0, 0)
    assert blocks[1].target_steps == (-46, -274, -102, 0, 0, 0)
    for block in blocks:
        assert totals(block) == tuple(b - a for a, b in zip(block.start_steps, block.target_steps))
        assert block.duration_s == pytest.approx(len(block.segments) * 0.001)
        assert all(type(value) is int and -32767 <= value <= 32767 for seg in block.segments for value in seg)
    with pytest.raises(FrozenInstanceError):
        blocks[0].target_steps = (0,) * 6


def test_quantized_time_never_exceeds_feed_and_bounds_large_deltas(planner):
    cfg = config()
    cfg.axes[0].max_velocity_mm_s = 1e8
    cfg.axes[0].acceleration_mm_s2 = 1e12
    block = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=10000, rapid=True)])[0]
    assert totals(block) == (2000000, 0, 0, 0, 0, 0)
    assert all(abs(value) <= 32767 for seg in block.segments for value in seg)
    assert len(block.segments) > 61
    small = planner.plan_linear_path(config(), (0,) * 6, [move(planner, x=0.1, feed=60)])[0]
    assert small.duration_s >= 0.11 - 1e-10  # 0.01 s acceleration + 0.09 cruise + 0.01 braking.


def test_segments_are_lazy_for_long_slow_jobs(planner):
    block = planner.plan_linear_path(config(), (0,) * 6, [move(planner, x=100, feed=0.001)])[0]
    assert len(block.segments) >= 6000000000
    assert not isinstance(block.segments, (list, tuple))
    assert block.segments[-1] == (0,) * 6
    assert next(block.iter_segments()) == block.segments[0]


def test_zero_moves_are_removed_without_losing_following_target(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=0), move(planner, x=0.001), move(planner, x=1)])
    assert len(blocks) == 1
    assert totals(blocks[0]) == (200, 0, 0, 0, 0, 0)
    assert planner.plan_linear_path(config(), (0,) * 6, []) == []


@pytest.mark.parametrize('kwargs', [
    {'feed_override': 0}, {'feed_override': float('nan')}, {'feed_override': 3},
    {'junction_deviation_mm': -1}, {'junction_deviation_mm': float('inf')},
    {'period_s': 0.002},
])
def test_rejects_invalid_planning_settings(planner, kwargs):
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), (0,) * 6, [move(planner, x=1)], **kwargs)


@pytest.mark.parametrize('target,feed', [(float('nan'), 1), (1, 0), (1, -1), (1, float('nan')), (1e20, 1)])
def test_rejects_nonfinite_out_of_range_targets_and_bad_feeds(planner, target, feed):
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), (0,) * 6, [move(planner, x=target, feed=feed)])


def test_disabled_axis_explicit_target_is_refused(planner):
    cfg = config()
    cfg.axes[1].enabled = False
    with pytest.raises(EcutError, match='disabled'):
        planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=1, y=0)])


def test_soft_limit_failure_refuses_entire_path(planner):
    cfg = config()
    cfg.axes[0].soft_limits_enabled = True
    cfg.axes[0].soft_limit_negative_mm = -1
    cfg.axes[0].soft_limit_positive_mm = 2
    with pytest.raises(EcutError, match='soft limit'):
        planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=1), move(planner, x=3)])


@pytest.mark.parametrize('start', [(0,) * 5, (0,) * 5 + (2 ** 31,), (0,) * 5 + (0.5,)])
def test_start_position_requires_six_int32_counts(planner, start):
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), start, [move(planner, x=1)])


def test_shared_timebase_matches_single_collinear_move(planner):
    cfg = config()
    moves = [move(planner, x=i * 0.005, feed=78) for i in range(1, 201)]
    blocks = planner.plan_linear_path(cfg, (0,) * 6, moves)
    continuous = planner.iter_path_segments(blocks)
    single = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=1, feed=78)])[0]
    assert len(continuous) == len(single.segments)
    assert tuple(sum(segment[i] for segment in continuous) for i in range(6)) == (200, 0, 0, 0, 0, 0)
    assert len(continuous) < sum(len(block.segments) for block in blocks)
    assert all(abs(a[0] - b[0]) <= 1 for a, b in zip(continuous, single.segments))


def test_shared_timebase_keeps_rounded_corner_chord_within_deviation(planner):
    cfg = config()
    cfg.axes[0].acceleration_mm_s2 = cfg.axes[1].acceleration_mm_s2 = 1e7
    cfg.axes[0].max_velocity_mm_s = cfg.axes[1].max_velocity_mm_s = 1e3
    blocks = planner.plan_linear_path(cfg, (0,) * 6,
                                      [move(planner, x=1.005, rapid=True), move(planner, y=1.005, rapid=True)])
    segments = planner.iter_path_segments(blocks)
    positions = [(0, 0)]
    for segment in segments:
        positions.append((positions[-1][0] + segment[0], positions[-1][1] + segment[1]))
    waypoint = (201, 0)
    assert waypoint not in positions            # the corner falls between two records
    worst = 0
    for start, end in zip(positions, positions[1:]):
        if start[0] < 201 and end[1] > 0:
            dx, dy = end[0] - start[0], end[1] - start[1]
            error = abs(dx * (start[1] - waypoint[1]) - (start[0] - waypoint[0]) * dy) / math.hypot(dx, dy) / 200
            worst = max(worst, error)
    assert 0.005 < worst <= 0.01 + 1e-12
    assert positions[-1] == (201, 201)
    assert all(abs(value) <= 32767 for segment in segments for value in segment)


def test_shared_timebase_reversal_preserves_turnaround(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=1), move(planner, x=-1)])
    current, highest = 0, 0
    for segment in planner.iter_path_segments(blocks):
        current += segment[0]
        highest = max(highest, current)
    assert current == -200
    assert highest == 200


def test_shared_timebase_empty_and_zero_deviation(planner):
    assert list(planner.iter_path_segments([])) == []
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=1), move(planner, y=1)], junction_deviation_mm=0)
    assert list(planner.iter_path_segments(blocks, junction_deviation_mm=0)) == [seg for block in blocks for seg in block.segments]

@pytest.mark.parametrize('target', [10 ** 1000, None])
def test_malformed_target_shape_and_oversized_numbers_raise_ecut_error(planner, target):
    requested = planner.LinearMove(target if target is None else (target, None, None, None, None, None), False, 60)
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), (0,) * 6, [requested])


def test_zero_deviation_sampler_refuses_nonzero_corner_speed(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=1), move(planner, y=1)])
    with pytest.raises(EcutError):
        planner.iter_path_segments(blocks, junction_deviation_mm=0)


def test_unrepresentable_record_count_is_refused(planner):
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), (0,) * 6, [move(planner, x=1, feed=1e-300)])

def test_finite_extreme_calibration_does_not_overflow_profile_math(planner):
    cfg = config()
    cfg.axes[0].max_velocity_mm_s = 1e300
    cfg.axes[0].acceleration_mm_s2 = 1e308
    block = planner.plan_linear_path(cfg, (0,) * 6, [move(planner, x=1000000, rapid=True)])[0]
    assert 6104 < len(block.segments) < 13000
    assert totals(block) == (200000000, 0, 0, 0, 0, 0)

def test_zero_deviation_stops_collinear_blocks_and_samples_exact_targets(planner):
    blocks = planner.plan_linear_path(config(), (0,) * 6,
                                      [move(planner, x=1), move(planner, x=2)], junction_deviation_mm=0)
    segments = planner.iter_path_segments(blocks, junction_deviation_mm=0)
    assert blocks[0].exit_speed_mm_s == blocks[1].entry_speed_mm_s == 0
    assert list(segments) == [segment for block in blocks for segment in block.segments]
    assert tuple(sum(segment[i] for segment in segments) for i in range(6)) == (400, 0, 0, 0, 0, 0)

class PlanningCancelled(BaseException):
    pass


def test_path_checkpoint_cancels_before_consuming_large_source(planner):
    visited = []

    def moves():
        for index in range(1, 10001):
            visited.append(index)
            yield move(planner, x=index / 100)

    def checkpoint():
        if len(visited) >= 10:
            raise PlanningCancelled()

    with pytest.raises(PlanningCancelled):
        planner.plan_linear_path(config(), (0,) * 6, moves(), checkpoint=checkpoint)
    assert len(visited) == 10


def test_path_checkpoint_remains_active_after_source_geometry(planner):
    exhausted = []

    def moves():
        for index in range(1, 101):
            yield move(planner, x=index / 100)
        exhausted.append(True)

    def checkpoint():
        if exhausted:
            raise PlanningCancelled()

    with pytest.raises(PlanningCancelled):
        planner.plan_linear_path(config(), (0,) * 6, moves(), checkpoint=checkpoint)


def _corner_chord_error(planner, blocks, **kwargs):
    """Largest distance (mm) of the programmed corner from the 1 ms record chord crossing it."""
    positions = [(0, 0)]
    for segment in planner.iter_path_segments(blocks, **kwargs):
        positions.append((positions[-1][0] + segment[0], positions[-1][1] + segment[1]))
    corner = blocks[0].target_steps
    worst = 0.0
    for start, end in zip(positions, positions[1:]):
        if start[0] < corner[0] and end[1] > 0:
            dx, dy = end[0] - start[0], end[1] - start[1]
            worst = max(worst, abs(dx * (start[1] - corner[1]) - (start[0] - corner[0]) * dy) / math.hypot(dx, dy) / 200)
    return worst


def test_per_move_corner_deviation_sets_corner_speed_and_exact_path(planner):
    def exit_speed(deviation, second=None, **kw):
        first = planner.LinearMove((10, None, None, None, None, None), False, 6000, deviation)
        return planner.plan_linear_path(config(), (0,) * 6, [first, second or move(planner, x=10, y=10)], **kw)[0].exit_speed_mm_s
    default = exit_speed(None)
    assert exit_speed(0.0025) == pytest.approx(default / 2)             # speed ~ sqrt(deviation)
    assert exit_speed(0) == 0                                          # exact path: stop at the corner
    assert exit_speed(5.0) == pytest.approx(default)                   # never looser than the setting
    assert exit_speed(0.0025, junction_deviation_mm=0) == 0            # setting 0 stops everywhere
    collinear = exit_speed(0, move(planner, x=20))                     # exact path does not stop on a straight line
    assert collinear == pytest.approx(math.sqrt(2000))


@pytest.mark.parametrize('bad', [-0.1, math.nan, math.inf, True])
def test_invalid_per_move_corner_deviation_is_refused(planner, bad):
    first = planner.LinearMove((10, None, None, None, None, None), False, 6000, bad)
    with pytest.raises(EcutError):
        planner.plan_linear_path(config(), (0,) * 6, [first, move(planner, x=10, y=10)])


def test_one_corner_does_not_slow_the_path(planner):
    cfg = config()
    cfg.axes[0].acceleration_mm_s2 = cfg.axes[1].acceleration_mm_s2 = 1e5
    cfg.axes[0].max_velocity_mm_s = cfg.axes[1].max_velocity_mm_s = 1e3
    cfg.axes[0].steps_per_mm = cfg.axes[1].steps_per_mm = 1000    # a step is well below the deviation
    step = 10.013                                          # not a whole number of records per move
    straight = [move(planner, x=step * (i + 1), feed=3000) for i in range(100)]
    corner = [move(planner, x=step * (i + 1), feed=3000) for i in range(50)]
    corner += [planner.LinearMove((step * 50, step * (i + 1), None, None, None, None), False, 3000)
               for i in range(50)]
    base = planner.plan_path_segments(cfg, (0,) * 6, straight)
    segments = planner.plan_path_segments(cfg, (0,) * 6, corner)
    assert len(segments) < 1.1 * len(base)
    positions = [(0, 0)]
    for segment in segments:
        positions.append((positions[-1][0] + segment[0], positions[-1][1] + segment[1]))
    waypoint = (round(step * 50 * 1000), 0)
    worst = 0
    for start, end in zip(positions, positions[1:]):
        if start[0] < waypoint[0] and end[1] > 0:
            dx, dy = end[0] - start[0], end[1] - start[1]
            worst = max(worst, abs(dx * (start[1] - waypoint[1]) - (start[0] - waypoint[0]) * dy) / math.hypot(dx, dy) / 1000)
    assert worst <= 0.01 + 1e-12


def test_per_move_corner_deviation_bounds_record_chord_error(planner):
    cfg = config()
    cfg.axes[0].acceleration_mm_s2 = cfg.axes[1].acceleration_mm_s2 = 1e4
    cfg.axes[0].max_velocity_mm_s = cfg.axes[1].max_velocity_mm_s = 1e5
    def run(deviation):
        first = planner.LinearMove((10.0037, None, None, None, None, None), True, None, deviation)
        blocks = planner.plan_linear_path(cfg, (0,) * 6, [first, move(planner, x=10.0037, y=7.3, rapid=True)])
        return _corner_chord_error(planner, blocks), blocks
    loose, _ = run(None)
    assert 0.002 < loose <= 0.01 + 1e-12
    limit = loose / 3
    tight, blocks = run(limit)
    assert tight <= limit + 1e-12
    assert blocks[0].exit_speed_mm_s < run(None)[1][0].exit_speed_mm_s


def backlash_config(enabled=True):
    cfg = config()
    cfg.axes[0].steps_per_mm = 25600 // cfg.interpolation_factor   # 25600 counts per mm
    cfg.axes[0].backlash_mm = 0.05
    cfg.axes[0].backlash_compensation_enabled = True
    cfg.unverified.backlash = enabled
    return cfg


def test_backlash_adds_counts_on_reversal_only(planner):
    cfg = backlash_config()
    state = planner.BacklashState((0,) * 6, (0,) * 6, (0,) * 6)
    moves = [move(planner, x=10), move(planner, x=20), move(planner, x=10)]
    out, end = planner.apply_backlash(moves, state, cfg)
    assert len(out) == 4 and out[2].rapid and out[2].target_mm[0] == 20   # the compensation, before move 3
    assert [m.offset_counts[0] for m in out] == [0, 0, -1280, -1280]
    assert end.offsets[0] == -1280 and end.dirs[0] == -1
    blocks = planner.plan_linear_path(cfg, (0,) * 6, out, start_offsets=state.offsets)
    assert blocks[-1].target_steps[0] == 10 * 25600 - 1280   # board = logical + offset
    assert blocks[2].target_steps[0] - blocks[1].target_steps[0] == -1280


def test_backlash_soft_limit_uses_the_logical_position(planner):
    cfg = backlash_config()
    cfg.axes[0].soft_limits_enabled = True
    cfg.axes[0].soft_limit_negative_mm, cfg.axes[0].soft_limit_positive_mm = 0, 20
    cfg.soft_limit_tolerance_mm = 0
    state = planner.BacklashState((0,) * 6, (0,) * 6, (0,) * 6)
    out, _ = planner.apply_backlash([move(planner, x=20), move(planner, x=0)], state, cfg)
    planner.plan_linear_path(cfg, (0,) * 6, out)   # the board runs to -0.05 mm: inside the logical window


def test_backlash_with_the_switch_off_is_refused_by_validate():
    with pytest.raises(Exception, match='not yet verified'):
        backlash_config(enabled=False).validate()