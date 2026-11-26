"""Pin names, soft limit tolerance, bed size helper and manual home buttons (snapshots only)."""
from copy import deepcopy
import importlib

from PyQt6 import QtCore, QtWidgets
import pytest

from appPlugins.Common.ecut.ecut_config import EcutBoardConfig


def fresh(**kwargs):
    state = dict(connected=True, age_s=0.01, inputs=0, outputs=0, busy=False, homed_axes=[])
    state.update(kwargs)
    return state


@pytest.fixture
def snapshot():
    return {'revision': 'saved-1', 'interpolation_factor': 64,
            'axes': [dict(steps_per_mm=400, max_velocity_mm_s=5, acceleration_mm_s2=40,
                          deceleration_mm_s2=None, soft_limits_enabled=False,
                          soft_limit_negative_mm=-100, soft_limit_positive_mm=100) for _ in range(4)],
            'motors': [dict(steps_per_revolution=200, microsteps=8, gear_ratio=1,
                            travel_per_revolution_mm=4) for _ in range(4)],
            'hardware': EcutBoardConfig().to_dict()}


@pytest.fixture
def make(snapshot):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    module = importlib.import_module('appPlugins.ToolGcodeSender.SenderMotion')
    created = []

    def factory(settings=None):
        widget = module.EcutMotionDialog(settings if settings is not None else snapshot)
        created.append(widget)
        return widget
    yield factory
    for widget in created:
        widget.set_busy(False)
        widget.close()
        widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def yes(monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes)


def test_labels_roundtrip_into_apply_payload_and_live_io(make):
    dialog = make()
    tabs = dialog.hardware_tabs
    tabs.input_label_fields[3].setText('Probe')
    tabs.output_label_fields[0].setText('Vacuum')
    assert dialog.is_dirty()
    sent = []
    dialog.apply_requested.connect(sent.append)
    dialog.apply_button.click()
    hardware = sent[0]['hardware']
    assert hardware['input_labels'][3] == 'Probe' and len(hardware['input_labels']) == 16
    assert hardware['output_labels'][0] == 'Vacuum' and len(hardware['output_labels']) == 8
    EcutBoardConfig.from_dict(hardware).validate()
    saved = deepcopy(dialog._snapshot)
    saved['hardware'] = hardware
    dialog.load_settings(saved)
    assert tabs.input_label_fields[3].text() == 'Probe'
    dialog.update_io(fresh(inputs=8, outputs=1))
    assert tabs.input_leds[3].text() == 'I3 Probe — 1 / closed'
    assert tabs.input_leds[4].text() == 'I4 — 0 / open'
    assert tabs.output_buttons[0].text() == 'O0 Vacuum — On'
    assert tabs.output_buttons[1].text() == 'O1 — Off'


def test_label_keeps_role_text_and_is_limited_and_printable(make, snapshot):
    snapshot['hardware']['outputs']['SPINDLE_CW'].update(enabled=True, pin=0)
    dialog = make(snapshot)
    tabs = dialog.hardware_tabs
    tabs.output_label_fields[0].setText('Vac')
    dialog.update_io(fresh(outputs=1))
    assert tabs.output_buttons[0].text() == 'O0 Vac — On · SPINDLE_CW'
    tabs.input_label_fields[0].setText('x' * 80)
    assert len(tabs.values()['input_labels'][0]) == 32
    tabs.input_label_fields[1].setText('  a\tb ')
    assert tabs.values()['input_labels'][1] == 'ab'


def test_no_hardcoded_output_names_without_labels_or_roles(make):
    dialog = make()
    dialog.update_io(fresh(outputs=255))
    assert [b.text() for b in dialog.hardware_tabs.output_buttons] == ['O%d — On' % p for p in range(8)]


def test_old_snapshot_without_label_keys_opens(make, snapshot):
    for key in ('input_labels', 'output_labels', 'soft_limit_tolerance_mm'):
        del snapshot['hardware'][key]
    dialog = make(snapshot)
    values = dialog.values()['hardware']
    assert values['input_labels'] == [''] * 16 and values['output_labels'] == [''] * 8
    assert values['soft_limit_tolerance_mm'] == 0.05
    assert not dialog.is_dirty()


def test_tolerance_roundtrip(make, snapshot):
    snapshot['hardware']['soft_limit_tolerance_mm'] = 0.2
    dialog = make(snapshot)
    assert dialog.soft_limit_tolerance.value() == 0.2
    assert dialog.soft_limit_tolerance.maximum() == 1 and dialog.soft_limit_tolerance.decimals() == 3
    dialog.soft_limit_tolerance.setValue(0.125)
    assert dialog.is_dirty()
    assert dialog.values()['hardware']['soft_limit_tolerance_mm'] == 0.125


def test_bed_size_fills_limits_with_negative_z_and_checks_them(make):
    dialog = make()
    for key, value in (('x', 300), ('y', 200), ('z', 50)):
        dialog.bed_size[key].setValue(value)
    dialog.bed_size_button.click()
    axes = dialog.values()['axes']
    assert [(a['soft_limit_negative_mm'], a['soft_limit_positive_mm'], a['soft_limits_enabled'])
            for a in axes[:3]] == [(0, 300, True), (0, 200, True), (-50, 0, True)]
    assert axes[3]['soft_limit_negative_mm'] == -100 and not axes[3]['soft_limits_enabled']
    assert dialog.is_dirty()


def test_bed_size_zero_axis_is_skipped(make):
    dialog = make()
    dialog.bed_size['x'].setValue(100)
    dialog.bed_size_button.click()
    axes = dialog.values()['axes']
    assert axes[0]['soft_limits_enabled'] and not axes[1]['soft_limits_enabled']
    assert 'Y' in dialog.status_label.text()


def test_home_buttons_emit_axis_lists_after_confirmation(make, snapshot, yes):
    snapshot['hardware']['axes'][3]['enabled'] = True
    dialog = make()
    sent = []
    dialog.reference_requested.connect(sent.append)
    expected = [[0], [1], [2], [3], [0, 1, 2, 3]]
    for button, axes in zip(dialog.home_buttons, expected):
        dialog.update_io(fresh())
        assert button.isEnabled()
        button.click()
        assert sent[-1] == axes
        dialog.set_busy(False)
    assert len(sent) == 5


def test_home_all_uses_enabled_axes_only(make, snapshot, yes):
    snapshot['hardware']['axes'][3]['enabled'] = False
    dialog = make(snapshot)
    sent = []
    dialog.reference_requested.connect(sent.append)
    dialog.update_io(fresh())
    dialog.home_buttons[4].click()
    assert sent == [[0, 1, 2]]


def test_declined_confirmation_emits_nothing(make, monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.No)
    dialog = make()
    sent = []
    dialog.reference_requested.connect(sent.append)
    dialog.update_io(fresh())
    dialog.home_buttons[0].click()
    assert sent == [] and not dialog._busy


def test_home_buttons_disabled_when_busy_stale_or_disconnected(make):
    dialog = make()
    assert not any(b.isEnabled() for b in dialog.home_buttons)
    dialog.update_io(fresh())
    assert all(b.isEnabled() for b in dialog.home_buttons)
    dialog.update_io(fresh(age_s=5))
    assert not any(b.isEnabled() for b in dialog.home_buttons)
    dialog.update_io(fresh(connected=False))
    assert not any(b.isEnabled() for b in dialog.home_buttons)
    dialog.update_io(fresh(busy=True))
    assert not any(b.isEnabled() for b in dialog.home_buttons)
    dialog.update_io(fresh())
    dialog.set_busy(True)
    assert not any(b.isEnabled() for b in dialog.home_buttons)
    dialog.set_busy(False)
    dialog.update_io(fresh())
    assert all(b.isEnabled() for b in dialog.home_buttons)


def test_homed_label_follows_io_state(make):
    dialog = make()
    assert dialog.home_label.text() == 'Machine home: X — Y — Z — A —'
    dialog.update_io(fresh(homed_axes=[0, 1]))
    assert dialog.home_label.text() == 'Machine home: X ✓ Y ✓ Z — A —'
    dialog.update_io(fresh(homed_axes=[2, 9]))
    assert dialog.home_label.text() == 'Machine home: X — Y — Z ✓ A —'
    dialog.update_io({'connected': False})
    assert dialog.home_label.text() == 'Machine home: X — Y — Z — A —'
