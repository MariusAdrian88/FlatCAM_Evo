"""Host G-code grammar and machine-coordinate translation using the simulated board."""
import importlib
import pytest
from test_ecut_spindle import pwm_api

def engine_api():
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    api = pwm_api()
    return module.EcutGcodeEngine(api), api


def test_modal_units_distance_and_feed_survive_separate_blocks():
    engine, api = engine_api()
    assert engine.execute_line('G20 G91 G1 X.01 F1') == ['ok']
    engine.execute_line('X.01')
    assert api.position_mm[0] == pytest.approx(.508, abs=.005)

def test_work_offsets_do_not_reset_raw_counts():
    engine, api = engine_api()
    engine.execute_line('G0 X1')
    original = list(api._usb.positions)
    engine.execute_line('G92 X0')
    assert api._usb.positions == original
    engine.execute_line('G0 X.1')
    assert api.position_mm[0] == pytest.approx(1.1, abs=.005)
    engine.execute_line('G53 G0 X0')
    assert api.position_mm[0] == pytest.approx(0)

@pytest.mark.parametrize('line', [
    'M3 S6000 G1 X1', 'M3 S6000 G2 X1 R.1 F100',
    'M3 S6000 G0 X1 X2', 'M3 S6000 G0 G1 X1 F100',
    'M3 S6000 G38.2 Z-1 F10', 'G0 X1 P4',
    'M3 S6000 G0 X1 Q4', 'M3 S6000 G90.1',
])
def test_invalid_complete_block_has_no_effects(line):
    engine, api = engine_api()
    with pytest.raises(Exception):
        engine.execute_line(line)
    assert api._usb.events == []
    assert api.position_mm[0] == 0

@pytest.mark.parametrize('plane,offset', [('G17', 'J'), ('G18', 'I'), ('G19', 'K')])
def test_full_circle_in_all_planes(plane, offset):
    engine, api = engine_api()
    engine.execute_line(f'{plane} G2 {offset}.05 F60')
    assert api.position_mm[:3] == pytest.approx([0, 0, 0], abs=.005)
    assert any(event[0] == 'motion' for event in api._usb.events)

def test_wcs_g10_tlo_and_stored_position():
    engine, api = engine_api()
    engine.execute_line('G10 L2 P2 X1 Z2')
    engine.execute_line('G55 G0 X0')
    assert api.position_mm[0] == pytest.approx(1, abs=.005)
    engine.execute_line('G28.1')
    engine.execute_line('G43.1 Z.5')
    engine.execute_line('G0 Z0')
    assert api.position_mm[2] == pytest.approx(2.5, abs=.005)
    engine.execute_line('G49')
    engine.execute_line('G28')
    assert api.position_mm[:3] == pytest.approx([1, 0, 0], abs=.005)

def test_check_mode_has_no_usb_effects_and_restores_live_parser():
    engine, api = engine_api()
    engine.execute_line('$C')
    engine.execute_line('G91 G1 X1 F100 M3 S6000')
    engine.execute_line('X1')
    engine.execute_line('G10 L2 P1 X4')
    assert api._usb.events == []
    engine.execute_line('$C')
    engine.execute_line('G0 X.1')
    assert api.position_mm[0] == pytest.approx(.1, abs=.005)

def test_jog_does_not_change_program_modes():
    engine, api = engine_api()
    engine.execute_line('G20 G91')
    engine.execute_line('$J=G21 G90 X.1 F60')
    engine.execute_line('G0 X.01')
    assert api.position_mm[0] == pytest.approx(.354, abs=.005)

def test_status_uses_measured_counts_not_commanded_accumulator():
    engine, api = engine_api()
    api._acc.steps[0] = 999999
    assert '|MPos:0.000,0.000,0.000|' in engine.status_report()

@pytest.mark.parametrize('line', ['$H', '$SLP', '$32=1', '$100=80.5'])
def test_unverified_effects_are_refused_without_usb(line):
    engine, api = engine_api()
    with pytest.raises(Exception):
        engine.execute_line(line)
    assert api._usb.events == []

def test_inverse_time_requires_each_feed():
    engine, api = engine_api()
    engine.execute_line('G93 G1 X.1 F600')
    with pytest.raises(Exception):
        engine.execute_line('X.2')
    assert api.position_mm[0] == pytest.approx(.1, abs=.005)

def test_host_settings_and_offsets_persist(tmp_path):
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    api = pwm_api()
    path = tmp_path / 'host.json'
    engine = module.EcutGcodeEngine(api, path)
    engine.execute_line('$12=.001')
    engine.execute_line('G10 L2 P1 X2')
    restored = module.EcutGcodeEngine(api, path)
    assert restored.settings[12] == .001
    assert any('[G54:2.000,' in line for line in restored.execute_line('$#'))

@pytest.mark.parametrize('setting,value,field,expected', [
    (100, 120, 'steps_per_mm', 120),
    (110, 120, 'max_velocity_mm_s', 2),
    (120, 5, 'acceleration_mm_s2', 5),
])
def test_verified_settings_map_exactly(setting, value, field, expected):
    engine, api = engine_api()
    engine.execute_line(f'${setting}={value}')
    assert getattr(api.config.axes[0], field) == expected
    assert engine.settings[setting] == value


def test_native_setting_updates_accumulator_scale():
    engine, api = engine_api()
    engine.execute_line('$100=120')
    engine.execute_line('G0 X1')
    assert api._usb.positions[0] == round(120 * api.config.interpolation_factor)
    assert api.position_mm[0] == pytest.approx(1, abs=.005)

def test_native_settings_persist_before_reconnect(tmp_path):
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    path = tmp_path / 'native.json'
    engine = module.EcutGcodeEngine(pwm_api(), path)
    engine.execute_line('$100=120')
    api = pwm_api()
    restored = module.EcutGcodeEngine(api, path)
    assert api.config.axes[0].steps_per_mm == 120
    assert restored.settings[100] == 120

def test_disabled_stationary_axis_does_not_prevent_enabled_axis_move():
    engine, api = engine_api()
    api.config.axes[2].enabled = False
    api.push_config()
    engine.execute_line('G0 X.1')
    assert api.position_mm[0] == pytest.approx(.1, abs=.005)

def test_check_mode_native_and_host_configuration_is_read_only():
    engine, api = engine_api()
    engine.execute_line('$C')
    for line in ('$100=120', '$12=.1', '$N0=G0X1', '$RST=*'):
        with pytest.raises(Exception):
            engine.execute_line(line)
    assert api._usb.events == []


def test_disabled_axis_request_is_refused_before_spindle_or_coolant():
    engine, api = engine_api()
    api.config.axes[2].enabled = False
    api.push_config()
    api._usb.events.clear()
    with pytest.raises(Exception):
        engine.execute_line('M3 S6000 G0 Z1')
    assert api._usb.events == []

@pytest.mark.parametrize('line', [
    'G2 X.1 Y.1 I0 J0 F60', 'G18 G2 X.1 I.1 J.2 F60',
    'G1 X.1 F0', 'G10 L2 P7 X1', 'G10 L3 P1 X1',
    'G43.1 X1', 'G80 X1', 'G49 G0 X1', 'G93 G1 X1',
    'N10000001', 'T256', 'T1.5', 'G0 X1 S-1', 'M6',
])
def test_rejected_blocks_leave_parser_and_usb_unchanged(line):
    engine, api = engine_api()
    modal = engine.execute_line('$G')
    with pytest.raises(Exception):
        engine.execute_line(line)
    assert api._usb.events == []
    assert engine.execute_line('$G') == modal

def test_program_end_resets_only_gcode_specified_modal_groups():
    engine, api = engine_api()
    engine.execute_line('G92 X1')
    engine.execute_line('G43.1 Z.5')
    engine.execute_line('G20 G91 F2 S6000 T2')
    engine.execute_line('M30')
    modal = engine.execute_line('$G')[0]
    assert 'G1 G54 G17 G20 G90 G94' in modal
    assert 'T2 F50.8 S6000' in modal
    assert '[G92:-1.000,0.000,0.000]' in engine.execute_line('$#')
    assert '[TLO:0.500]' in engine.execute_line('$#')

def test_dwell_is_seconds_and_does_not_change_modal_motion():
    engine, api = engine_api()
    engine.execute_line('G91 G0 X.01')
    engine.execute_line('G4 P0')
    engine.execute_line('X.01')
    assert api.position_mm[0] == pytest.approx(.02, abs=.005)

def test_helical_radius_arc_and_offset_arc_end_at_requested_target():
    engine, api = engine_api()
    engine.execute_line('G3 X.1 Y.1 Z.05 R.1 F60')
    assert api.position_mm[:3] == pytest.approx([.1, .1, .05], abs=.005)
    engine.execute_line('G2 X.2 Y0 I0 J-.1 F60')
    assert api.position_mm[:3] == pytest.approx([.2, 0, .05], abs=.005)

def test_g10_l20_sets_work_coordinate_at_current_machine_position():
    engine, api = engine_api()
    engine.execute_line('G0 X1')
    engine.execute_line('G10 L20 P1 X.2')
    engine.execute_line('G0 X.3')
    assert api.position_mm[0] == pytest.approx(1.1, abs=.005)


def test_arc_through_disabled_axis_is_refused_before_any_motion():
    engine, api = engine_api()
    api.config.axes[2].enabled = False
    api.push_config()
    api._usb.events.clear()
    with pytest.raises(Exception):
        engine.execute_line('G18 G2 I.05 F60')
    assert api._usb.events == []


@pytest.mark.parametrize('code,expected', [(0x95, 100), (0x96, 50), (0x97, 25)])
def test_rapid_override_reports_and_applies_to_following_move(code, expected):
    engine, api = engine_api()
    engine.realtime(code)
    assert f',%d,100>' % expected in engine.status_report()
    lines = []
    original = api.run_gcode
    api.run_gcode = lambda block: (lines.extend(block), original(block))[-1]
    engine.execute_line('G0 X.1')
    if expected != 100:
        assert any('G1 ' in line and ' F' in line for line in lines)
    assert api.position_mm[0] == pytest.approx(.1, abs=.005)

def test_spindle_override_changes_dac_but_keeps_programmed_speed():
    engine, api = engine_api()
    engine.execute_line('M3 S6000')
    engine.realtime(0x9A)
    assert api._spindle_rpm == pytest.approx(6600)
    assert 'S6000]' in engine.execute_line('$G')[0]
    assert ',100,110|A:S>' in engine.status_report()
    engine.execute_line('S12000')
    assert api._spindle_rpm == pytest.approx(13200)

def test_override_and_coolant_cannot_energize_at_manual_confirmation():
    engine, api = engine_api()
    api._pause_reason, api._pending_tool_change = 'M6', 2
    for code in (0x9A, 0xA0, 0xA1):
        with pytest.raises(Exception):
            engine.realtime(code)
    assert api._usb.events == []

def test_coolant_realtime_toggle_uses_configured_output():
    engine, api = engine_api()
    api.config.outputs['FLOOD'].enabled = True
    api.config.outputs['FLOOD'].pin = 2
    api.push_config()
    engine.realtime(0xA0)
    assert api.read_output('FLOOD')
    engine.realtime(0xA0)
    assert not api.read_output('FLOOD')
    with pytest.raises(Exception):
        engine.realtime(0xA1)

def test_startup_blocks_execute_and_retain_modal_state():
    engine, api = engine_api()
    engine.execute_line('$N0=G21G91G0X.01')
    engine.execute_line('$N1=G0X.01')
    assert engine.run_startup() == ['>G21G91G0X.01:ok', '>G0X.01:ok']
    assert api.position_mm[0] == pytest.approx(.02, abs=.005)
    assert 'G91' in engine.execute_line('$G')[0]

@pytest.mark.parametrize('line', ['$N0=M0', '$N1=T1M6'])
def test_startup_cannot_wait_for_unavailable_operator_confirmation(line):
    engine, api = engine_api()
    with pytest.raises(Exception):
        engine.execute_line(line)
    assert api._usb.events == []



def test_reset_discards_virtual_check_coordinate_updates():
    engine, api = engine_api()
    engine.execute_line('G10 L2 P1 X2')
    engine.execute_line('$C')
    engine.execute_line('G10 L2 P1 X4')
    engine.reset()
    assert '[G54:2.000,0.000,0.000]' in engine.execute_line('$#')
    assert not engine.check_mode

def test_native_reinitialization_reports_outputs_stopped():
    engine, api = engine_api()
    engine.execute_line('M3 S6000')
    engine.execute_line('$100=120')
    assert 'M5 M9' in engine.execute_line('$G')[0]
    assert api._spindle_direction is None

def test_invalid_config_and_spindle_raise_parser_error_before_usb():
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    engine, api = engine_api()
    for line in ('$30=-1', '$31=25000', 'M3 S6000 G2 X1 R.1 F100'):
        with pytest.raises(module.GcodeError):
            engine.execute_line(line)
    assert api._usb.events == []


@pytest.mark.parametrize('tool', [None, 2])
def test_manual_pause_waits_for_the_required_confirmation(tool):
    import threading
    import time
    engine, api = engine_api()
    errors = []
    def run():
        try:
            engine.execute_line('M0' if tool is None else 'T2 M6')
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        # the controller sets the reason first and the PAUSED state after it: wait for both
        deadline = time.monotonic() + 2
        while (api.pause_reason is None or api.state.name != 'PAUSED') and time.monotonic() < deadline:
            time.sleep(.001)
        assert api.pause_reason == ('M0' if tool is None else 'M6')
        assert worker.is_alive()
        if tool is None:
            engine.realtime(ord('~'))
        else:
            module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
            with pytest.raises(module.GcodeError):
                engine.realtime(ord('~'))
            engine.confirm_tool_change(tool)
        worker.join(2)
        assert not worker.is_alive()
        assert not errors
        if tool is not None:
            assert api.current_tool == tool
    finally:
        if worker.is_alive():
            api.stop()
            worker.join(2)

@pytest.mark.parametrize('data', [
    {'settings': [], 'wcs': [], 'stored': [], 'startup': []},
    {'settings': {'10': 1, '12': .002, '13': 0, '32': 0}, 'wcs': [[0,0,0]]*6,
     'stored': [[0,0,0]]*2, 'startup': ['', ''], 'native': []},
    {'settings': {'10': 1, '12': .002, '13': 0, '32': 0}, 'wcs': [[0,0,0]]*6,
     'stored': [[0,0,0]]*2, 'startup': ['M0', '']},
])
def test_corrupt_host_settings_fail_as_clear_parser_error(tmp_path, data):
    import json
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    path = tmp_path / 'corrupt.json'
    path.write_text(json.dumps(data))
    api = pwm_api()
    with pytest.raises(module.GcodeError):
        module.EcutGcodeEngine(api, path)
    assert api._usb.events == []


@pytest.mark.parametrize('selector', ['$', '*'])
def test_restore_refuses_to_silently_keep_native_overrides(selector):
    engine, api = engine_api()
    engine.execute_line('$100=120')
    api._usb.events.clear()
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    with pytest.raises(module.GcodeError):
        engine.execute_line('$RST=' + selector)
    assert engine.settings[100] == 120
    assert api._usb.events == []


def test_spindle_override_during_block_uses_that_blocks_programmed_speed():
    engine, api = engine_api()
    original = api._usb.send_motion
    applied = []
    def send(records):
        original(records)
        if not applied:
            applied.append(True)
            engine.realtime(0x9A)
    api._usb.send_motion = send
    engine.execute_line('M3 S6000 G1 X.01 F60')
    assert api._spindle_rpm == pytest.approx(6600)


def test_prepare_program_is_pure_and_batches_adjacent_linear_moves(tmp_path):
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    api = pwm_api()
    path = tmp_path / 'not-created.json'
    engine = module.EcutGcodeEngine(api, path)
    before = engine.execute_line('$G')
    calls = []
    original = api.run_gcode
    api.run_gcode = lambda lines: (calls.append(list(lines)), original(lines))[-1]
    job = engine.prepare_program(['G21 G90', 'G1 X.01 F60', 'X.02', 'X.03'])
    assert api._usb.events == []
    assert engine.execute_line('$G') == before
    assert not path.exists()
    assert engine.execute_program(job) is None
    assert len(calls) == 1
    assert api.position_mm[0] == pytest.approx(.03, abs=.005)

def test_prepare_program_advances_virtual_incremental_coordinates():
    engine, api = engine_api()
    job = engine.prepare_program(['G91 G1 X.01 F60', 'X.01', 'X.01'])
    engine.execute_program(job)
    assert api.position_mm[0] == pytest.approx(.03, abs=.005)
    assert 'G91' in engine.execute_line('$G')[0]

@pytest.mark.parametrize('lines', [
    ['M3 S6000', 'G2 X1 R.1 F100'],
    ['G0 X.1', '$H'], ['G0 X.1', 'G1X1?F100'],
    ['G0 X.1', 'G0X1é'],
])
def test_whole_program_rejection_has_no_effects_and_source_index(lines):
    engine, api = engine_api()
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    with pytest.raises(module.GcodeError) as caught:
        engine.prepare_program(lines)
    assert caught.value.source_index == 1
    assert api._usb.events == []
    assert api.position_mm[0] == 0

def test_prepared_job_refuses_changed_machine_or_parser():
    engine, api = engine_api()
    job = engine.prepare_program(['G0 X.1'])
    engine.execute_line('G91')
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    with pytest.raises(module.GcodeError):
        engine.execute_program(job)
    assert api._usb.events == []

def test_prepared_job_has_coordinate_barriers_and_retains_only_reached_changes():
    engine, api = engine_api()
    job = engine.prepare_program(['G0 X.01', 'G10 L2 P1 X1', 'G0 X0',
                                  'G10 L2 P1 X2', 'G0 X0'])
    original = api.run_gcode
    calls = []
    def run(lines):
        calls.append(list(lines))
        if len(calls) == 2:
            raise RuntimeError('injected batch failure')
        original(lines)
    api.run_gcode = run
    with pytest.raises(RuntimeError):
        engine.execute_program(job)
    assert '[G54:1.000,0.000,0.000]' in engine.execute_line('$#')

def test_prepared_job_refuses_realtime_overrides_but_allows_status():
    engine, api = engine_api()
    original = api._usb.send_motion
    seen = []
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    def send(records):
        original(records)
        if not seen:
            seen.append(engine.realtime(ord('?')))
            for code in (0x91, 0x96, 0x9A, 0xA0):
                with pytest.raises(module.GcodeError):
                    engine.realtime(code)
    api._usb.send_motion = send
    engine.execute_program(engine.prepare_program(['G1 X.01 F60', 'X.02']))
    assert seen

def test_prepared_job_reset_invalidates_job_before_writes():
    engine, api = engine_api()
    job = engine.prepare_program(['G0 X.1'])
    engine.reset()
    api._usb.events.clear()
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    with pytest.raises(module.GcodeError):
        engine.execute_program(job)
    assert api._usb.events == []


def test_prepared_program_stop_does_not_continue_later_batches():
    engine, api = engine_api()
    job = engine.prepare_program(['G1 X.01 F60', 'G10 L2 P1 X2', 'G0 X0'])
    original = api._usb.send_motion
    stopped = []
    def send(records):
        original(records)
        if not stopped:
            stopped.append(True)
            engine.realtime(0x85)
    api._usb.send_motion = send
    with pytest.raises(Exception):
        engine.execute_program(job)
    assert '[G54:0.000,0.000,0.000]' in engine.execute_line('$#')

@pytest.mark.parametrize('tool', [None, 2])
def test_prepared_program_manual_pause_and_tool_change(tool):
    import threading
    import time
    engine, api = engine_api()
    job = engine.prepare_program(['G1 X.01 F60', 'M0' if tool is None else 'T2M6', 'X.02'])
    errors = []
    def run():
        try:
            engine.execute_program(job)
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        # the controller sets the reason first and the PAUSED state after it: wait for both
        deadline = time.monotonic() + 2
        while (api.pause_reason is None or api.state.name != 'PAUSED') and time.monotonic() < deadline:
            time.sleep(.001)
        assert api.pause_reason == ('M0' if tool is None else 'M6')
        if tool is None:
            engine.realtime(ord('~'))
        else:
            engine.confirm_tool_change(tool)
        worker.join(2)
        assert not worker.is_alive()
        assert not errors
        assert api.position_mm[0] == pytest.approx(.02, abs=.005)
    finally:
        if worker.is_alive():
            api.stop()
            worker.join(2)

def test_junction_setting_is_host_only_and_persists(tmp_path):
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    api = pwm_api()
    path = tmp_path / 'junction.json'
    engine = module.EcutGcodeEngine(api, path)
    engine.execute_line('$11=.025')
    assert engine.settings[11] == .025
    assert api.config.junction_deviation_mm == .025
    assert api._usb.events == []
    engine.execute_line('G1 X.01 F60')
    restored = module.EcutGcodeEngine(pwm_api(), path)
    assert restored.settings[11] == .025

def test_negative_junction_setting_is_refused_before_effects():
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    engine, api = engine_api()
    with pytest.raises(module.GcodeError):
        engine.execute_line('$11=-.1')
    assert api._usb.events == []


def test_prepared_job_is_bound_to_its_engine():
    first, api = engine_api()
    second, _ = engine_api()
    job = first.prepare_program(['G0 X.01'])
    module = importlib.import_module('appPlugins.Common.ecut.ecut_gcode_engine')
    with pytest.raises(module.GcodeError):
        second.execute_program(job)

def test_prepared_check_program_stays_isolated_from_usb():
    engine, api = engine_api()
    engine.execute_line('$C')
    engine.execute_program(engine.prepare_program(['G91 G1 X1 F60', 'X1',
                                                    'G10 L2 P1 X4']))
    assert api._usb.events == []
    engine.execute_line('$C')
    assert '[G54:0.000,0.000,0.000]' in engine.execute_line('$#')
    engine.execute_line('G0 X.01')
    assert api.position_mm[0] == pytest.approx(.01, abs=.005)


def test_arc_point_checkpoint_propagates_baseexception_without_live_effects():
    engine, api = engine_api()
    before_state = engine._program_state()
    class PlanningStopped(BaseException):
        pass
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 3:
            raise PlanningStopped()
    api._planning_checkpoint = checkpoint
    with pytest.raises(PlanningStopped):
        engine.prepare_program(['G2 I.5 F60'])
    assert engine._program_state() == before_state
    assert api._usb.events == []

