"""Motion tuning uses validated PC settings and the existing simulated board."""
import copy
import importlib
import json
import threading
import time

import pytest

from test_ecut_spindle import pwm_api


def motion_engine(tmp_path):
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    api = pwm_api()
    return module.EcutGcodeEngine(api, tmp_path / 'machine.json'), api


def test_a_file_of_a_released_version_loads_with_axis_a_off(tmp_path):
    engine, api = motion_engine(tmp_path)
    engine.save_motion_settings(engine.get_motion_settings())
    data = json.loads(engine.settings_path.read_text())
    data['machine'].pop('config_version', None)    # saved when the default had all six axes on
    data['machine']['axes'][3]['enabled'] = True
    engine.settings_path.write_text(json.dumps(data))
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    restored = module.EcutGcodeEngine(pwm_api(), engine.settings_path)
    assert not restored.api.config.axes[3].enabled
    assert restored.get_motion_settings()['migrated_a_off']
    with pytest.raises(Exception):
        restored.execute_line('G1 A6 F600')        # refused like a disabled A


def test_axis_a_chosen_by_the_user_survives_a_save_and_a_reload(tmp_path):
    engine, api = motion_engine(tmp_path)
    payload = engine.get_motion_settings()
    payload['hardware']['axes'][3]['enabled'] = True
    engine.save_motion_settings(payload)
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    restored = module.EcutGcodeEngine(pwm_api(), engine.settings_path)
    assert restored.api.config.axes[3].enabled
    assert not restored.get_motion_settings()['migrated_a_off']


@pytest.mark.parametrize('name', ['ENABLE1', 'ENABLE6'])
def test_enable_output_alias_save_materializes_defaults_and_survives_reload(tmp_path, name):
    from appPlugins.Common.ecut.ecut_controller import encode_config
    engine, api = motion_engine(tmp_path)
    original_native_packets = encode_config(api.config)
    payload = engine.get_motion_settings()
    payload['hardware']['outputs']['SPINDLE_CW']['enabled'] = False
    payload['hardware']['outputs'][name] = {'enabled': True}
    saved = engine.save_motion_settings(payload)
    assert saved['hardware']['outputs'][name] == {'enabled': True, 'pin': 0, 'inverted': False}
    # ENABLE is a logical AF output alias; configuring it adds no driver-enable command.
    assert encode_config(api.config) == original_native_packets
    restored = type(engine)(pwm_api(), engine.settings_path)
    assert restored.get_motion_settings()['hardware']['outputs'][name] == saved['hardware']['outputs'][name]


@pytest.mark.parametrize('name,values', [
    ('ENABLE0', {'enabled': True}), ('ENABLE7', {'enabled': True}),
    ('UNKNOWN_OUTPUT', {'enabled': True}),
    ('ENABLE1', {'enabled': True, 'unexpected': 1}),
    ('ENABLE1', {'enabled': 1}), ('ENABLE1', {'enabled': True, 'pin': 16}),
])
def test_new_output_alias_validation_rejects_unknown_or_invalid_fields_before_usb(tmp_path, name, values):
    engine, api = motion_engine(tmp_path)
    original = api.config.to_dict()
    payload = engine.get_motion_settings()
    payload['hardware']['outputs'][name] = values
    with pytest.raises(Exception):
        engine.save_motion_settings(payload)
    assert api.config.to_dict() == original
    assert api._usb.events == []
    assert not engine.settings_path.exists()


def test_motion_snapshot_and_bulk_save_roundtrip_preserve_other_settings(tmp_path):
    engine, api = motion_engine(tmp_path)
    engine.execute_line('G10 L2 P2 X12')
    before = engine.get_motion_settings()
    assert type(before['axes'][0]['steps_per_mm']) is int
    payload = copy.deepcopy(before)
    payload['axes'][0].update(steps_per_mm=400, max_velocity_mm_s=7,
                              acceleration_mm_s2=123, deceleration_mm_s2=77,
                              soft_limits_enabled=True, soft_limit_negative_mm=-12,
                              soft_limit_positive_mm=23)
    payload['motors'][0]['microsteps'] = 8
    saved = engine.save_motion_settings(payload)
    assert saved['revision'] != before['revision']
    assert saved['axes'][0] == payload['axes'][0]
    assert saved['motors'] == payload['motors']
    assert engine._wcs[1][0] == 12
    stored = json.loads(engine.settings_path.read_text())
    assert stored['native']['100'] == 400
    assert stored['native']['110'] == 420
    assert stored['native']['150'] == -12
    restored = type(engine)(pwm_api(), engine.settings_path)
    assert restored.get_motion_settings()['axes'] == saved['axes']
    assert restored.get_motion_settings()['motors'] == saved['motors']
    engine.execute_line('$120=321')
    assert engine.get_motion_settings()['axes'][0]['soft_limit_positive_mm'] == 23


@pytest.mark.parametrize('mutation', ['invalid', 'stale', 'limits', 'output'])
def test_save_rejects_whole_candidate_before_usb_or_file(tmp_path, mutation):
    engine, api = motion_engine(tmp_path)
    payload = engine.get_motion_settings()
    if mutation == 'invalid':
        payload['axes'][3]['steps_per_mm'] = 2.5
    elif mutation == 'stale':
        payload['revision'] = 'old'
    elif mutation == 'limits':
        payload['axes'][0].update(soft_limits_enabled=True,
                                  soft_limit_negative_mm=2, soft_limit_positive_mm=1)
    else:
        api.spindle_on_cw(1000)
    previous = api.config.to_dict()
    api._usb.events.clear()
    with pytest.raises(Exception):
        engine.save_motion_settings(payload)
    assert api.config.to_dict() == previous
    assert api._usb.events == []
    assert not engine.settings_path.exists()


def test_calibration_preserves_parser_and_offsets_uses_applied_profile(tmp_path):
    engine, api = motion_engine(tmp_path)
    engine.execute_line('G20 G91 G93')
    engine.execute_line('G10 L2 P2 X4')
    parser = copy.deepcopy(engine._parser)
    result = engine.calibration_move(0, 1, 60, engine.get_motion_settings()['revision'])
    assert result['distance_mm'] == 1
    assert len(result['position_mm']) == 6
    assert result['position_mm'][0] == pytest.approx(1, abs=.005)
    parser.position = engine._parser.position
    assert engine._parser == parser
    assert engine._wcs[1][0] == 101.6
    assert any(event[0] == 'motion' for event in api._usb.events)


@pytest.mark.parametrize('axis,distance,feed', [(0, 11, 60), (0, 1, .01),
                                               (0, 1, float('inf')), (4, 1, 60),
                                               (True, 1, 60), (0, 1, 999999)])
def test_calibration_bounds_fail_before_motion(tmp_path, axis, distance, feed):
    engine, api = motion_engine(tmp_path)
    revision = engine.get_motion_settings()['revision']
    with pytest.raises(Exception):
        engine.calibration_move(axis, distance, feed, revision)
    assert api._usb.events == []


def test_storage_failure_reports_ram_applied(tmp_path, monkeypatch):
    engine, api = motion_engine(tmp_path)
    payload = engine.get_motion_settings()
    payload['axes'][0]['steps_per_mm'] = 400
    monkeypatch.setattr(engine, '_save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(Exception, match='applied.*storage'):
        engine.save_motion_settings(payload)
    assert api.config.axes[0].steps_per_mm == 400


def test_transport_requests_execute_only_on_existing_worker_without_serial_replies(tmp_path):
    from test_ecut_sender_port import FakeAPI, FakeEngine, wait_for
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    engine = FakeEngine(api)
    engine.get_motion_settings = lambda: (api.record('motion_settings') or {'revision': 'current'})
    engine.save_motion_settings = lambda payload: (api.record('motion_save') or payload)
    engine.calibration_move = lambda *args: (api.record('calibration') or {'distance_mm': args[1]})
    port = EcutSenderPort(api=api, engine=engine, timeout=.01)
    try:
        port.readline()
        wait_for(lambda: not port._active and not port._startup_pending)
        assert port.get_motion_settings() == {'revision': 'current'}
        assert port.save_motion_settings({'revision': 'current'}) == {'revision': 'current'}
        assert port.calibration_move(0, 1, 60, 'current') == {'distance_mm': 1}
        assert port.readline() == b''
        assert {ident for _, ident in api.calls} == {port._worker.ident}
        assert port._worker.ident != threading.get_ident()
    finally:
        port.close()


def read_reply(port, limit=2.0):
    """The next reply line; the port read time is short, so a busy machine may need more than 1 read."""
    deadline = time.monotonic() + limit
    line = port.readline()
    while not line and time.monotonic() < deadline:
        line = port.readline()
    return line


def real_port(tmp_path):
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from test_ecut_sender_port import wait_for
    engine, api = motion_engine(tmp_path)
    api.connect = lambda: True
    port = EcutSenderPort(api=api, engine=engine, timeout=.01)
    port.readline()
    wait_for(lambda: not port._active and not port._startup_pending)
    return port, engine, api


def test_io_monitor_is_cached_and_manual_output_preserves_other_bits(tmp_path, monkeypatch):
    port, engine, api = real_port(tmp_path)
    try:
        original_poll = api.poll
        caller = threading.get_ident()
        def worker_poll():
            assert threading.get_ident() != caller
            return original_poll()
        monkeypatch.setattr(api, 'poll', worker_poll)
        assert port.get_io_state()['fresh']
        revision = port.get_motion_settings()['revision']
        for pin in range(8):
            port.set_manual_output(pin, True, revision)
        assert port.get_io_state()['outputs'] == 255
        assert api._expected_outputs == 255
        for pin in range(8):
            port.set_manual_output(pin, False, revision)
            assert port.get_io_state()['outputs'] == 255 & ~((1 << (pin + 1)) - 1)
        assert port.get_motion_settings()['revision'] == revision
        assert not any(event[0] == 'motion' for event in api._usb.events)
    finally:
        port.close()


def test_hardware_save_persists_and_rejects_readonly_board_changes(tmp_path):
    engine, api = motion_engine(tmp_path)
    payload = engine.get_motion_settings()
    payload['hardware']['inputs']['INPUT_1'].update(enabled=True, pin=7, polarity=1)
    payload['hardware']['outputs']['OUTPUT_7'].update(enabled=True, pin=7)
    payload['hardware']['axes'][0]['dir_active_low'] = True
    saved = engine.save_motion_settings(payload)
    restored = type(engine)(pwm_api(), engine.settings_path)
    assert restored.get_motion_settings()['hardware'] == saved['hardware']
    payload = saved
    payload['hardware']['interpolation_index'] = 0
    api._usb.events.clear()
    with pytest.raises(Exception, match='read.only|unsupported'):
        engine.save_motion_settings(payload)
    assert api._usb.events == []


def test_calibration_watchdog_cancels_without_worker_or_request_leak(tmp_path):
    from test_ecut_sender_port import wait_for
    port, engine, api = real_port(tmp_path)
    port.CALIBRATION_WATCHDOG = .03
    entered = threading.Event()
    def stuck(*args):
        entered.set()
        while True:
            api.poll()
            time.sleep(.002)
    engine.calibration_move = stuck
    try:
        with pytest.raises(Exception, match='watchdog'):
            port.calibration_move(0, 1, 60, engine.get_motion_settings()['revision'])
        assert entered.is_set()
        wait_for(lambda: port._motion_request is None and not port._active)
        assert port._worker.is_alive()
        assert port.get_motion_settings()['revision']
    finally:
        port.close()


def test_manual_output_readback_failure_is_reported_and_connection_fails_closed(tmp_path):
    from appPlugins.Common.ecut.ecut_protocol import Cmd
    port, engine, api = real_port(tmp_path)
    original = api._usb.cmd
    api._usb.cmd = lambda opcode, params=b'': None if opcode == Cmd.SET_OUTPUTS else original(opcode, params)
    try:
        with pytest.raises(Exception, match='verifying|readback'):
            port.set_manual_output(7, True, engine.get_motion_settings()['revision'])
    finally:
        port.close()


def test_hardware_spindle_change_cannot_be_overwritten_by_older_native_settings(tmp_path):
    engine, api = motion_engine(tmp_path)
    engine.execute_line('$30=20000')
    payload = engine.get_motion_settings()
    payload['hardware']['spindle']['speed_rpm_max'] = 18000
    engine.save_motion_settings(payload)
    restored = type(engine)(pwm_api(), engine.settings_path)
    assert restored.api.config.spindle.speed_rpm_max == 18000


def test_manual_generic_output_preserves_spindle_cache_and_manual_spindle_off_requires_m5(tmp_path):
    from test_ecut_sender_port import wait_for
    port, engine, api = real_port(tmp_path)
    try:
        port.write(b'M3 S1000\n')
        assert read_reply(port) == b'ok\r\n'
        wait_for(lambda: not port._active)
        revision = port.get_motion_settings()['revision']
        port.set_manual_output(7, True, revision)
        assert api._spindle_rpm == 1000
        assert api._spindle_direction == 'SPINDLE_CW'
        port.set_manual_output(7, False, revision)
        port.set_manual_output(0, False, revision)
        with pytest.raises(Exception, match='spindle.*off'):
            port.calibration_move(0, 1, 60, revision)
        port.write(b'M5\n')
        assert read_reply(port) == b'ok\r\n'
        wait_for(lambda: not port._active)
        assert api._spindle_rpm == 0
    finally:
        port.close()


@pytest.mark.parametrize('operation', ['save', 'calibration', 'output'])
def test_transport_tuning_is_rejected_while_commands_are_running(tmp_path, operation):
    from test_ecut_sender_port import FakeAPI, FakeEngine
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    engine = FakeEngine(api)
    port = EcutSenderPort(api=api, engine=engine, timeout=.01)
    try:
        port.write(b'WAIT\n')
        assert engine.entered.wait(1)
        with pytest.raises(Exception, match='busy'):
            if operation == 'save':
                port.save_motion_settings({})
            elif operation == 'calibration':
                port.calibration_move(0, 1, 60, 'stale')
            else:
                port.set_manual_output(0, True, 'stale')
    finally:
        engine.release.set()
        port.close()


@pytest.mark.parametrize('control', [b'\x18', b'!'])
def test_realtime_cancels_calibration_and_releases_waiter(tmp_path, control):
    from test_ecut_sender_port import wait_for
    port, engine, api = real_port(tmp_path)
    entered = threading.Event()
    errors = []
    def stuck(*args):
        entered.set()
        while True:
            api.poll()
            time.sleep(.002)
    def call():
        try:
            port.calibration_move(0, 1, 60, engine.get_motion_settings()['revision'])
        except Exception as error:
            errors.append(error)
    engine.calibration_move = stuck
    thread = threading.Thread(target=call)
    try:
        thread.start()
        assert entered.wait(1)
        assert port.get_motion_settings()['revision']
        with pytest.raises(Exception):
            port.write(b'G0 X1\n')
        port.write(control)
        thread.join(1)
        assert not thread.is_alive()
        assert errors
        wait_for(lambda: port._motion_request is None and not port._active)
        assert port._worker.is_alive()
    finally:
        port.close()
        thread.join(1)


def test_settings_read_is_cached_available_during_jobs_and_does_not_expose_mutations():
    from test_ecut_sender_port import FakeAPI, FakeEngine
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    engine = FakeEngine(api)
    engine.get_motion_settings = lambda: {'revision': 'current', 'axes': [{'steps_per_mm': 400}]}
    port = EcutSenderPort(api=api, engine=engine, timeout=.01)
    try:
        port.write(b'WAIT\n')
        assert engine.entered.wait(1)
        snapshot = port.get_motion_settings()
        snapshot['axes'][0]['steps_per_mm'] = 1
        assert port.get_motion_settings()['axes'][0]['steps_per_mm'] == 400
    finally:
        engine.release.set()
        port.close()
