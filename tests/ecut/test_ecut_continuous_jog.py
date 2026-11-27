"""Continuous streamed jogging: kinematics, release, bounds and fault handling."""
import struct
from collections import deque
import math

import pytest

from appPlugins.Common.ecut import State
from appPlugins.Common.ecut.ecut_protocol import EcutError, Cmd
from appPlugins.Common.ecut import ecut_controller
from test_ecut_controller import make_api, sent_deltas


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def jog_api(monkeypatch, reverse=False):
    api = make_api(steps_per_mm=10000, interpolation=0)
    axis = api.config.axes[0]
    axis.max_velocity_mm_s = 2
    axis.acceleration_mm_s2 = 4
    axis.soft_limits_enabled = True
    axis.soft_limit_negative_mm = 0
    axis.soft_limit_positive_mm = 2
    if reverse:
        api._acc.steps[0] = api._usb.positions[0] = round(2 * api._axis_scale(0))
    clock = Clock()
    monkeypatch.setattr(ecut_controller.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(ecut_controller.time, 'sleep', clock.sleep)
    return api, clock


@pytest.mark.parametrize('reverse', [False, True])
def test_continuous_jog_accelerates_cruises_and_brakes_at_soft_limit(monkeypatch, reverse):
    api, clock = jog_api(monkeypatch, reverse)
    api.jog_start(0, int(reverse), 120)
    scale = api._axis_scale(0)
    raw = round(2 * scale) if reverse else 0
    values = sent_deltas(api)
    speeds = [abs(v[0]) for v in values]
    for delta in values:
        raw += delta[0]
        assert 0 <= raw <= round(2 * scale)
        assert not any(delta[1:])
    assert raw == (0 if reverse else round(2 * scale))
    assert sum(speeds[:50]) < sum(speeds[500:550]) / 2
    assert sum(speeds[-50:]) < sum(speeds[500:550]) / 2
    assert api.state == State.IDLE and not api.jogging
    assert clock.now >= len(values) * .001 - .025
    assert [int(op) for op, _ in api._usb.cmds] == [int(Cmd.END_STREAM)]


def test_release_during_the_backlash_take_up_stops_the_records(monkeypatch):
    api, clock = jog_api(monkeypatch, reverse=True)
    api.config.axes[0].backlash_mm = 1.0
    api.config.axes[0].backlash_compensation_enabled = True
    api.config.unverified.backlash = True
    api.push_config()
    api.jog_start(0, 1, 120)            # to the limit at 0: the last direction is negative
    sent = api._usb.send_motion
    packets = []

    def counting(records):
        sent(records)
        packets.append(len(records))
        if len(packets) == 1:
            api.jog_stop()              # release in the take-up of the reversal
    api._usb.send_motion = counting
    api.jog_start(0, 0, 120)
    assert len(packets) < 25            # it brakes within a few records, the take-up is 1000
    assert api.state == State.IDLE and not api.jogging


@pytest.mark.parametrize('release_at', [.15, .65])
def test_release_brakes_without_sending_native_jog_or_hard_stop(monkeypatch, release_at):
    api, clock = jog_api(monkeypatch)
    def held():
        if clock.now >= release_at:
            api.jog_stop()
        return True
    api.jog_start(0, 0, 120, should_continue=held)
    values = sent_deltas(api)
    speeds = [abs(v[0]) for v in values]
    assert 0 < api.position_mm[0] < 2
    assert sum(speeds[-30:]) / 30 < max(speeds) / 2
    assert api.state == State.IDLE and not api.jogging
    assert [int(op) for op, _ in api._usb.cmds] == [int(Cmd.END_STREAM)]


def test_release_before_first_record_sends_no_motion(monkeypatch):
    api, _ = jog_api(monkeypatch)
    api.jog_start(0, 0, 120, should_continue=lambda: False)
    assert sent_deltas(api) == []
    assert api.state == State.IDLE


@pytest.mark.parametrize('axis,direction,feed', [(6, 0, 10), (0, 2, 10), (0, 0, float('nan')), (0, 0, 0)])
def test_invalid_continuous_jog_is_refused_before_motion(monkeypatch, axis, direction, feed):
    api, _ = jog_api(monkeypatch)
    with pytest.raises((EcutError, ValueError)):
        api.jog_start(axis, direction, feed)
    assert sent_deltas(api) == []


def test_persistent_missing_status_stops_and_does_not_resume(monkeypatch):
    api, clock = jog_api(monkeypatch)
    original = api._usb.poll
    api._usb.poll = lambda: original() if clock.now < .1 else None
    with pytest.raises(EcutError, match='status'):
        api.jog_start(0, 0, 120)
    assert api.state == State.ERROR and not api.jogging
    assert any(int(op) == int(Cmd.STOP) for op, _ in api._usb.cmds)


def test_reset_callback_cannot_submit_more_jog_records(monkeypatch):
    api, _ = jog_api(monkeypatch)
    def stop_after_packet(number):
        api.stop()
    api._usb.on_motion = stop_after_packet
    api.jog_start(0, 0, 120)
    assert len(api._usb.motion_packets) == 1
    assert api.state == State.IDLE


@pytest.mark.parametrize('reverse', [False, True])
def test_reversed_axis_scale_and_fractional_bound(monkeypatch, reverse):
    from appPlugins.Common.ecut.ecut_config import AxisDirection
    api, _ = jog_api(monkeypatch)
    api.config.axes[0].direction = AxisDirection.REVERSE
    api.config.axes[0].soft_limit_positive_mm = .1234567
    if reverse:
        api._acc.steps[0] = api._usb.positions[0] = -1234
    api.jog_start(0, int(reverse), 120)
    assert 0 <= api.position_mm[0] <= .1234567
    assert api.position_steps[0] == (0 if reverse else math.ceil(.1234567 * api._axis_scale(0)))


def test_real_time_queue_is_bounded_and_idle_requires_actual_execution(monkeypatch):
    from test_ecut_verified import report
    from appPlugins.Common.ecut.ecut_protocol import decode_rx_report
    api, clock = jog_api(monkeypatch)
    queue = deque()
    maximum = 0
    due = 0
    def send(records):
        nonlocal due, maximum
        poll()
        api._usb.motion_packets.append(list(records))
        due = max(due, clock.now)
        for record in records:
            due += .001
            queue.append((due, struct.unpack('>6h', record[4:16])))
        maximum = max(maximum, len(queue))
    def poll():
        while queue and queue[0][0] <= clock.now + 1e-10:
            _, delta = queue.popleft()
            api._usb.positions = [p + d for p, d in zip(api._usb.positions, delta)]
        return decode_rx_report(report(tuple(api._usb.positions), status=129 if queue else 1,
                                       fifo=12000 - 16 * len(queue)))
    api._usb.send_motion = send
    api._usb.poll = poll
    api.jog_start(0, 0, 120, should_continue=lambda: clock.now < .65)
    assert not queue
    assert 0 < maximum <= 24
    assert api.position_steps == api._usb.positions
    assert api.state == State.IDLE


def test_starved_stream_fails_closed_without_catching_up(monkeypatch):
    api, clock = jog_api(monkeypatch)
    original = api._usb.send_motion
    def slow_send(records):
        original(records)
        clock.now += .05
    api._usb.send_motion = slow_send
    with pytest.raises(EcutError, match='underrun'):
        api.jog_start(0, 0, 120)
    assert len(api._usb.motion_packets) == 1
    assert api.state == State.ERROR


def test_hold_brakes_and_cannot_resume_the_old_jog(monkeypatch):
    api, clock = jog_api(monkeypatch)
    api._planning_checkpoint = lambda: api.pause() if clock.now > .2 else None
    api.jog_start(0, 0, 120)
    position = api.position_steps
    assert api.state == State.IDLE
    api.resume()
    assert api.state == State.IDLE and api.position_steps == position


def test_stop_from_deadman_callback_cannot_send_another_packet(monkeypatch):
    api, _ = jog_api(monkeypatch)
    calls = 0
    at_stop = None
    def held():
        nonlocal calls, at_stop
        calls += 1
        if calls == 20:
            api.stop()
            at_stop = len(api._usb.motion_packets)
        return True
    api.jog_start(0, 0, 120, should_continue=held)
    assert at_stop is not None
    assert len(api._usb.motion_packets) == at_stop


def test_unconfigured_report_aborts_before_another_packet(monkeypatch):
    from dataclasses import replace
    api, _ = jog_api(monkeypatch)
    original = api._usb.poll
    def poll():
        frame = original()
        return replace(frame, status=0) if api._usb.motion_packets else frame
    api._usb.poll = poll
    with pytest.raises(EcutError, match='status'):
        api.jog_start(0, 0, 120)
    assert len(api._usb.motion_packets) == 1


def test_release_during_initial_idle_confirmation_sends_no_motion(monkeypatch):
    api, _ = jog_api(monkeypatch)
    original = api._usb.poll
    def poll():
        api.jog_stop()
        return original()
    api._usb.poll = poll
    api.jog_start(0, 0, 120)
    assert sent_deltas(api) == []


def test_emergency_during_first_deadman_check_is_not_cleared(monkeypatch):
    api, _ = jog_api(monkeypatch)
    def held():
        api.estop()
        return True
    api.jog_start(0, 0, 120, should_continue=held)
    assert not sent_deltas(api)
    assert api.state == State.ESTOP


def test_frozen_position_reports_cannot_keep_a_jog_streaming(monkeypatch):
    from test_ecut_verified import report
    from appPlugins.Common.ecut.ecut_protocol import decode_rx_report
    api, clock = jog_api(monkeypatch)
    frozen = decode_rx_report(report())
    api._usb.poll = lambda: frozen
    with pytest.raises(EcutError, match='progress'):
        api.jog_start(0, 0, 120)
    assert clock.now < .4
    assert api.state == State.ERROR


def test_substep_feed_does_not_trip_the_progress_watchdog(monkeypatch):
    api, clock = jog_api(monkeypatch)
    api.config.axes[0].steps_per_mm = 1
    api.jog_start(0, 0, .06, should_continue=lambda: clock.now < .3)
    assert api.state == State.IDLE
    assert api.position_steps[0] == 0
    assert clock.now >= .3


def test_feed_caps_the_continuous_jog_speed(monkeypatch):
    api, clock = jog_api(monkeypatch)
    api.jog_start(0, 0, 30)
    assert max(abs(v[0]) for v in sent_deltas(api)) <= round(.5 * api._axis_scale(0) * .001)
    assert clock.now >= 4


def test_delayed_reports_and_prefetch_do_not_false_trip_on_coarse_steps(monkeypatch):
    from dataclasses import replace
    api, clock = jog_api(monkeypatch)
    axis = api.config.axes[0]
    axis.steps_per_mm = 3
    axis.acceleration_mm_s2 = 25 / 6
    original = api._usb.poll
    queue = deque()
    due = .080
    next_report = .090
    def send(records):
        nonlocal due
        api._usb.motion_packets.append(list(records))
        for record in records:
            due = max(due, clock.now) + .001
            queue.append((due, struct.unpack('>6h', record[4:16])))
    def poll():
        nonlocal next_report
        while queue and queue[0][0] <= clock.now + 1e-10:
            _, delta = queue.popleft()
            api._usb.positions = [p + d for p, d in zip(api._usb.positions, delta)]
        if not api._usb.motion_packets:
            return original()
        if clock.now + 1e-10 < next_report:
            return None
        next_report += .090
        return replace(original(), status=129 if queue else 1, field_790=12000 - 16 * len(queue))
    api._usb.send_motion = send
    api._usb.poll = poll
    api.jog_start(0, 0, 120)
    assert not queue
    assert api.position_steps[0] == 12
    assert api.state == State.IDLE


def backlash_jog_api(monkeypatch):
    api, clock = jog_api(monkeypatch, reverse=True)
    api.config.axes[0].backlash_mm = 1.0
    api.config.axes[0].backlash_compensation_enabled = True
    api.config.unverified.backlash = True
    api.push_config()
    api.jog_start(0, 1, 120)            # to the limit at 0: the last direction is negative
    del api._usb.motion_packets[:]
    return api, clock


def test_feed_hold_during_the_backlash_take_up_brakes_it(monkeypatch):
    api, clock = backlash_jog_api(monkeypatch)
    sent = api._usb.send_motion
    packets = []

    def counting(records):
        sent(records)
        packets.append(len(records))
        if len(packets) == 1:
            api.pause()                 # the physical FEED_HOLD after the first packet of the take-up
    api._usb.send_motion = counting
    api.jog_start(0, 0, 120)
    moved = abs(sum(delta[0] for delta in sent_deltas(api))) / api._axis_scale(0)
    assert moved < .05                  # the whole take-up is 1 mm
    assert len(sent_deltas(api)) < 100
    assert api.state == State.IDLE and not api.jogging


@pytest.mark.parametrize('hold_at_end, known', [(True, True), (False, False)])
def test_a_hold_after_the_last_takeup_record_keeps_the_offset(monkeypatch, hold_at_end, known):
    api, clock = backlash_jog_api(monkeypatch)
    api.config.axes[0].backlash_mm = 1.0
    takeup = round(abs(api._axis_scale(0)))      # 1 mm of counts
    sent, total, fired = api._usb.send_motion, [0], []

    def counting(records):
        sent(records)
        total[0] += sum(abs(struct.unpack('>6h', rec[4:16])[0]) for rec in records)
        if not fired and (total[0] >= takeup if hold_at_end else True):
            fired.append(1)
            api.pause()                 # the FEED_HOLD: after the last record of the take-up, or in its middle
    api._usb.send_motion = counting
    api.jog_start(0, 0, 120)
    assert fired and total[0] <= takeup + 16 * 4     # no jog record after the hold
    assert (api._backlash_offset[0] != 0) == known
    assert api.state == State.IDLE


def test_nan_feed_writes_no_motion_before_the_refusal(monkeypatch):
    api, _ = backlash_jog_api(monkeypatch)
    with pytest.raises((EcutError, ValueError)):
        api.jog_start(0, 0, float('nan'))
    assert sent_deltas(api) == []
