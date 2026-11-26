"""Regressions derived from USB captures, without opening hardware."""
import struct

import pytest

from appPlugins.Common.ecut import EcutAPI, EcutUsb, EcutUsbError, EcutError, State, decode_rx_report, ecut_crc60


def report(positions=(0,) * 6, status=1, outputs=0, fifo=12000):
    data = bytearray(65)
    struct.pack_into('>6i', data, 1, *positions)
    struct.pack_into('>H', data, 29, outputs)
    struct.pack_into('>H', data, 33, status)
    struct.pack_into('>H', data, 39, fifo)
    struct.pack_into('>I', data, 61, ecut_crc60(data[1:61]))
    return bytes(data)


def test_negative_position_and_hidapi_report_without_id():
    data = report((930112, -1889280, 2200768, 0, 0, 0))
    assert decode_rx_report(data).axis_positions[1] == -1889280
    usb = EcutUsb()
    class Device:
        def read(self, *args):
            return list(data[1:])
    usb._dev = Device()
    assert usb.recv().axis_positions[1] == -1889280


def test_crc_damage_never_reaches_controller():
    data = bytearray(report())
    data[10] ^= 1
    usb = EcutUsb()
    class Device:
        def read(self, *args):
            return list(data)
    usb._dev = Device()
    with pytest.raises(EcutUsbError, match='CRC'):
        usb.recv()


class Board:
    def __init__(self):
        self.is_open = True
        self.positions = [930112, -1889280, 2200768, 0, 0, 0]
        self.commands = []
    def poll(self):
        return decode_rx_report(report(tuple(self.positions)))
    def cmd(self, opcode, params=b''):
        self.commands.append((int(opcode), bytes(params)))
        if int(opcode) == 0xa0:
            self.positions = list(struct.unpack_from('>6i', params, 4))
    def send_motion(self, records):
        for record in records:
            for i, delta in enumerate(struct.unpack_from('>6h', record, 4)):
                self.positions[i] += delta


def test_initialization_and_reset_preserve_counts_and_zero_outputs():
    api = EcutAPI()
    api._usb = Board()
    before = list(api._usb.positions)
    api.push_config()
    assert api.position_steps == before
    assert [op for op, _ in api._usb.commands] == [0xc8, 0xaf, 0xca, 0xa0, 0x9b, 0x9f, 0xa2, 0xcd, 0x1a, 0xaf]
    params = next(p for op, p in api._usb.commands if op == 0xaf)
    assert params[:12] == bytes.fromhex('000000000000ffff00000000')
    api.reset()
    assert api.position_steps == before
    assert api.state == State.IDLE
    assert all(p[-1:] == b'\0' for _, p in api._usb.commands if p)


def test_completion_requires_target_and_idle_instead_of_any_frame():
    api = EcutAPI()
    api._usb = Board()
    api._acc.steps = [10, 0, 0, 0, 0, 0]
    api._usb.poll = lambda: decode_rx_report(report())
    with pytest.raises(EcutError, match='timeout'):
        api._wait_for_motion(timeout=0.01)


def test_uninitialized_motion_is_refused_without_writes():
    api = EcutAPI()
    api._usb = Board()
    api._state = State.IDLE
    with pytest.raises(EcutError, match='initializ'):
        api.jog_step(0, 0.01)
    assert api._usb.commands == []


def test_short_move_and_return_end_with_empty_e5():
    api = EcutAPI()
    api._usb = Board()
    api.push_config()
    before = api.position_steps
    api.run_gcode(['G91', 'G1 X0.01 Y0.01 F60', 'G1 X-0.01 Y-0.01'])
    assert api.position_steps == before == api._usb.positions
    markers = [p for op, p in api._usb.commands if op == 0xe5]
    assert len(markers) == 1 and all(not any(p) for p in markers)


def test_failed_stop_latches_error_and_prevents_more_motion():
    api = EcutAPI()
    api._usb = Board()
    api.push_config()
    def timeout():
        raise EcutError('timeout')
    api._wait_idle = timeout
    with pytest.raises(EcutError, match='timeout'):
        api.stop()
    assert api.state == State.ERROR
    with pytest.raises(EcutError):
        api.jog_step(0, 0.01)


def test_busy_target_is_not_completed():
    api = EcutAPI()
    api._usb = Board()
    api._usb.poll = lambda: decode_rx_report(report(status=0x81))
    with pytest.raises(EcutError, match='timeout'):
        api._wait_for_motion(timeout=0.01)


def test_poll_does_not_wait_for_a_future_status_report():
    usb = EcutUsb()
    class Device:
        def read(self, size, timeout):
            assert timeout == 0
            return []
    usb._dev = Device()
    assert usb.poll() is None


def test_switch_enables_sensor_cycles():
    from appPlugins.Common.ecut import Cmd
    from test_ecut_sensors import sensor_api
    api = sensor_api(unverified=False)
    api._usb.commands.clear()
    refused = [(lambda: api.home_axis(2), 'Sensor homing'), (lambda: api.probe(2, -.005), 'Probing'),
               (lambda: api.probe_grid([(.001, .002)], .001), 'Probing'),
               (lambda: api.run_gcode(['G91 G1 X.001 F6', 'G38.2 Z-.005 F6']), 'Probing')]
    for call, name in refused:
        with pytest.raises(EcutError, match=f'{name} is not verified on hardware.*Not verified'):
            call()
    assert api._usb.commands == []
    api = sensor_api()
    api._usb.commands.clear()
    api.home_axis(2)
    assert any(int(op) == Cmd.HOME for op, _ in api._usb.commands)


def test_unverified_switches_round_trip_and_default_off():
    from appPlugins.Common.ecut import EcutBoardConfig
    cfg = EcutBoardConfig()
    assert not any(cfg.unverified.to_dict().values())
    cfg.unverified.mpg = cfg.unverified.backlash = True
    again = EcutBoardConfig.from_dict(cfg.to_dict())
    assert again.unverified.to_dict() == dict(homing=False, probing=False, backlash=True, mpg=True)
    old = cfg.to_dict()
    del old['unverified']
    assert not any(EcutBoardConfig.from_dict(old).unverified.to_dict().values())
